"""
RAAH Authoritative Ingestion Service
====================================

Orchestrates validation, idempotency checks, event ordering, and authoritative
Simulator/DispatchState mutations for all external telemetry and CAD events.
"""

import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

from api.settings import settings
from api.dependencies import manager
from api.persistence import IdempotencyRecord
from api.adapters.models import (
    NormalizedEvent,
    IngestionResponse,
    EventStatus,
    EventType,
)

logger = logging.getLogger("raah.adapters.ingestion")


class IngestionService:
    """
    Authoritative ingestion and normalization boundary.
    Guarantees strict idempotency, event ordering, and simulator isolation.
    """

    def __init__(self):
        self._ingestion_lock = threading.Lock()
        self._last_known_entity_time: Dict[str, datetime] = {}
        self._entity_time_lock = threading.Lock()

        # Telemetry & Metrics
        self.total_ingested = 0
        self.total_accepted = 0
        self.total_duplicates = 0
        self.total_rejected = 0
        self.total_stale = 0
        self.total_latency_ms = 0.0
        self.by_event_type: Dict[str, Dict[str, int]] = {
            EventType.INCIDENT_CALL.value: {"ingested": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "stale": 0},
            EventType.AMBULANCE_GPS.value: {"ingested": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "stale": 0},
            EventType.HOSPITAL_STATUS.value: {"ingested": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "stale": 0},
            EventType.TRAFFIC_UPDATE.value: {"ingested": 0, "accepted": 0, "duplicate": 0, "rejected": 0, "stale": 0},
        }

    # ==================================================================
    # PRIMARY INGESTION PIPELINE
    # ==================================================================

    def ingest_event(
        self,
        event: NormalizedEvent,
        operator: Optional[str] = None,
    ) -> IngestionResponse:
        """
        Execute the complete ingestion pipeline:
          1. Schema and structure validation
          2. Event age and ordering verification
          3. Durable idempotency deduplication check
          4. Authoritative Simulator mutation (under lock)
          5. Idempotency record persistence
          6. Structured audit logging
        """
        start_t = time.perf_counter()
        self.total_ingested += 1

        logger.info(
            "External event received: type=%s source=%s source_id=%s cid=%s",
            event.event_type,
            event.source,
            event.source_event_id,
            event.correlation_id,
            extra={
                "event_type": event.event_type,
                "source": event.source,
                "source_event_id": event.source_event_id,
                "correlation_id": event.correlation_id,
            },
        )

        # --------------------------------------------------------------
        # 1. SCHEMA & TYPE VALIDATION
        # --------------------------------------------------------------
        if event.schema_version != 1:
            self.total_rejected += 1
            err_msg = f"Unsupported schema version '{event.schema_version}'. Supported: 1."
            logger.warning("External event rejected: %s", err_msg)
            return self._build_response(
                event=event,
                status=EventStatus.REJECTED,
                message=err_msg,
                start_t=start_t,
            )

        supported_types = {e.value for e in EventType}
        if event.event_type not in supported_types:
            self.total_rejected += 1
            err_msg = f"Unknown event type '{event.event_type}'. Supported: {sorted(supported_types)}."
            logger.warning("External event rejected: %s", err_msg)
            return self._build_response(
                event=event,
                status=EventStatus.REJECTED,
                message=err_msg,
                start_t=start_t,
            )

        # --------------------------------------------------------------
        # 2. STALENESS & ORDERING VALIDATION
        # --------------------------------------------------------------
        is_stale, stale_reason = self._check_event_ordering(event)
        if is_stale:
            self.total_stale += 1
            logger.warning(
                "External event classified as STALE: source=%s source_id=%s reason=%s",
                event.source,
                event.source_event_id,
                stale_reason,
            )
            return self._build_response(
                event=event,
                status=EventStatus.STALE,
                message=stale_reason,
                start_t=start_t,
            )

        # --------------------------------------------------------------
        # 3. DURABLE IDEMPOTENCY & MUTATION (SYNCHRONIZED)
        # --------------------------------------------------------------
        with self._ingestion_lock:
            # Check existing idempotency record in durable store
            existing_record = None
            try:
                existing_record = manager.persistence_store.get_idempotency_record(
                    source=event.source,
                    source_event_id=event.source_event_id,
                )
            except Exception as store_err:
                logger.error("Persistence store check failed during idempotency check: %s", store_err)
                self.total_rejected += 1
                return self._build_response(
                    event=event,
                    status=EventStatus.REJECTED,
                    message=f"Persistence error: {store_err}",
                    start_t=start_t,
                )

            if existing_record is not None:
                # DUPLICATE DETECTED: Do NOT mutate simulator; return cached deterministic response
                self.total_duplicates += 1
                try:
                    updated_rec = manager.persistence_store.increment_idempotency_seen(
                        source=event.source,
                        source_event_id=event.source_event_id,
                    )
                    seen_count = updated_rec.seen_count if updated_rec else existing_record.seen_count + 1
                except Exception:
                    seen_count = existing_record.seen_count + 1

                logger.info(
                    "Duplicate external event recognized: source=%s source_id=%s seen_count=%d",
                    event.source,
                    event.source_event_id,
                    seen_count,
                    extra={
                        "source": event.source,
                        "source_event_id": event.source_event_id,
                        "seen_count": seen_count,
                    },
                )
                return self._build_response(
                    event=event,
                    status=EventStatus.DUPLICATE,
                    message=f"Duplicate event recognized. Returning cached execution outcome (seen {seen_count} times).",
                    result=existing_record.response_payload,
                    duplicate_of=existing_record.idempotency_key,
                    seen_count=seen_count,
                    start_t=start_t,
                )

            # FIRST TIME SEEN: Apply Authoritative Mutation to Simulator
            try:
                mutation_result = self._apply_authoritative_mutation(event, operator)
            except Exception as mut_err:
                self.total_rejected += 1
                logger.error(
                    "Authoritative simulator rejection for event %s:%s: %s",
                    event.source,
                    event.source_event_id,
                    mut_err,
                )
                return self._build_response(
                    event=event,
                    status=EventStatus.REJECTED,
                    message=f"Simulator rejection: {mut_err}",
                    start_t=start_t,
                )

            # Record Idempotency in Durable Store
            now_iso = datetime.now(timezone.utc).isoformat()
            idem_rec = IdempotencyRecord(
                idempotency_key=f"{event.source}:{event.source_event_id}",
                source=event.source,
                source_event_id=event.source_event_id,
                event_type=event.event_type,
                status="ACCEPTED",
                response_payload=mutation_result,
                first_seen_at=now_iso,
                last_seen_at=now_iso,
                seen_count=1,
                correlation_id=event.correlation_id,
            )

            try:
                manager.persistence_store.save_idempotency_record(idem_rec)
            except Exception as save_err:
                logger.warning("Failed to save idempotency record (non-fatal): %s", save_err)

            # Update entity ordering watermark
            self._update_entity_watermark(event)

            self.total_accepted += 1
            logger.info(
                "External event successfully ingested: type=%s source=%s source_id=%s",
                event.event_type,
                event.source,
                event.source_event_id,
                extra={
                    "event_type": event.event_type,
                    "source": event.source,
                    "source_event_id": event.source_event_id,
                },
            )

            # Broadcast corresponding realtime event (M13 Phase 1)
            try:
                from api.realtime.broadcaster import broadcaster
                from api.realtime.models import EventType as RealtimeEventType
                sim_time = manager.simulator.state.current_time if manager.simulator else 0
                evt_map = {
                    "CAD_INCIDENT": RealtimeEventType.INCIDENT_DISPATCHED,
                    "GPS_LOCATION": RealtimeEventType.AMBULANCE_UPDATE,
                    "HOSPITAL_STATUS": RealtimeEventType.HOSPITAL_UPDATE,
                    "AMBULANCE_STATUS": RealtimeEventType.AMBULANCE_UPDATE,
                }
                rt_type = evt_map.get(event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type))
                if rt_type and mutation_result:
                    broadcaster.broadcast(rt_type, mutation_result, sim_time)
            except Exception as bcast_err:
                logger.debug("Ingestion realtime broadcast notice: %s", bcast_err)

            return self._build_response(
                event=event,
                status=EventStatus.ACCEPTED,
                message="External event accepted and executed authoritatively by Simulator.",
                result=mutation_result,
                start_t=start_t,
            )

    # ==================================================================
    # ORDERING & STALENESS
    # ==================================================================

    def _check_event_ordering(self, event: NormalizedEvent) -> Tuple[bool, Optional[str]]:
        """Verify event timestamp against max age and per-entity watermarks."""
        now = datetime.now(timezone.utc)
        try:
            occurred_dt = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
        except Exception:
            return True, f"Malformed ISO 8601 occurred_at timestamp: '{event.occurred_at}'."

        # Reject events from far future (> 5 minutes clock drift)
        if occurred_dt > now + timedelta(minutes=5):
            return True, "Event occurred_at timestamp is in the future."

        # Reject events older than max_event_age_seconds
        max_age = timedelta(seconds=settings.max_event_age_seconds)
        if now - occurred_dt > max_age:
            return True, f"Event is older than maximum permitted age ({settings.max_event_age_seconds}s)."

        # Entity-level out-of-order check (GPS or Hospital capacity)
        entity_key = self._extract_entity_key(event)
        if entity_key:
            with self._entity_time_lock:
                last_time = self._last_known_entity_time.get(entity_key)
                if last_time and occurred_dt < last_time:
                    return True, f"Out-of-order event: entity '{entity_key}' already has newer state from {last_time.isoformat()}."

        return False, None

    def _extract_entity_key(self, event: NormalizedEvent) -> Optional[str]:
        """Extract unique entity identifier for ordering watermarks."""
        if event.event_type == EventType.AMBULANCE_GPS.value:
            amb_id = event.payload.get("ambulance_id")
            return f"amb:{amb_id}" if amb_id else None
        elif event.event_type == EventType.HOSPITAL_STATUS.value:
            hosp_id = event.payload.get("hospital_id")
            return f"hosp:{hosp_id}" if hosp_id else None
        return None

    def _update_entity_watermark(self, event: NormalizedEvent):
        """Record the latest accepted event timestamp for this entity."""
        entity_key = self._extract_entity_key(event)
        if entity_key:
            try:
                occurred_dt = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
                with self._entity_time_lock:
                    prev = self._last_known_entity_time.get(entity_key)
                    if not prev or occurred_dt > prev:
                        self._last_known_entity_time[entity_key] = occurred_dt
            except Exception:
                pass

    # ==================================================================
    # AUTHORITATIVE SIMULATOR MUTATION
    # ==================================================================

    def _apply_authoritative_mutation(
        self,
        event: NormalizedEvent,
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute state mutation exclusively through existing Simulator methods.
        Never bypasses dispatch engine, redirection engine, or DispatchState.
        """
        sim = manager.simulator
        with manager.lock:
            # 1. CAD INCIDENT INTAKE
            if event.event_type == EventType.INCIDENT_CALL.value:
                payload = dict(event.payload)
                if "incident_id" in payload and len(payload) == 1:
                    # Integer index dispatch
                    return sim.create_incident(int(payload["incident_id"]))
                # Dynamic intake matching 24-feature ML contract
                return sim.create_custom_incident(payload)

            # 2. AMBULANCE GPS / AVL TELEMETRY
            elif event.event_type == EventType.AMBULANCE_GPS.value:
                amb_id = str(event.payload.get("ambulance_id", "")).strip()
                if not amb_id or amb_id not in sim.state.ambulances:
                    raise ValueError(f"Ambulance unit '{amb_id}' not found in live DispatchState.")

                amb = sim.state.ambulances[amb_id]
                amb.latitude = float(event.payload["latitude"])
                amb.longitude = float(event.payload["longitude"])

                if "status" in event.payload and event.payload["status"]:
                    amb.status = str(event.payload["status"]).upper()
                if "traffic_level" in event.payload and event.payload["traffic_level"]:
                    amb.traffic_level = str(event.payload["traffic_level"]).upper()
                if "road_condition" in event.payload and event.payload["road_condition"]:
                    amb.road_condition = str(event.payload["road_condition"]).upper()

                amb.recalculate_eta()
                return {
                    "ambulance_id": amb.ambulance_id,
                    "status": amb.status,
                    "latitude": amb.latitude,
                    "longitude": amb.longitude,
                    "eta_minutes": amb.eta_minutes,
                    "sim_time": sim.state.current_time,
                }

            # 3. HOSPITAL CAPACITY & STATUS FEED
            elif event.event_type == EventType.HOSPITAL_STATUS.value:
                hosp_id = str(event.payload.get("hospital_id", "")).strip()
                if not hosp_id or hosp_id not in sim.state.hospitals:
                    raise ValueError(f"Hospital facility '{hosp_id}' not found in live DispatchState.")

                hosp = sim.state.hospitals[hosp_id]
                if "capacity" in event.payload and event.payload["capacity"] is not None:
                    hosp.capacity = max(0, int(event.payload["capacity"]))
                if "current_load" in event.payload and event.payload["current_load"] is not None:
                    hosp.current_load = max(0, int(event.payload["current_load"]))
                if "icu_capacity" in event.payload and event.payload["icu_capacity"] is not None:
                    hosp.icu_capacity = max(0, int(event.payload["icu_capacity"]))
                if "current_icu_load" in event.payload and event.payload["current_icu_load"] is not None:
                    hosp.current_icu_load = max(0, int(event.payload["current_icu_load"]))

                return {
                    "hospital_id": hosp.hospital_id,
                    "capacity": hosp.capacity,
                    "current_load": hosp.current_load,
                    "available_beds": hosp.available_beds,
                    "icu_capacity": hosp.icu_capacity,
                    "current_icu_load": hosp.current_icu_load,
                    "available_icu": hosp.available_icu,
                    "sim_time": sim.state.current_time,
                }

            # 4. TRAFFIC FEED ADVISORY
            elif event.event_type == EventType.TRAFFIC_UPDATE.value:
                t_level = str(event.payload.get("traffic_level", "NORMAL")).upper()
                r_cond = str(event.payload.get("road_condition", "GOOD")).upper()
                amb_id = event.payload.get("ambulance_id")

                affected_count = 0
                if amb_id and str(amb_id) in sim.state.ambulances:
                    amb = sim.state.ambulances[str(amb_id)]
                    amb.traffic_level = t_level
                    amb.road_condition = r_cond
                    amb.recalculate_eta()
                    affected_count = 1
                else:
                    # Fleet-wide advisory
                    for amb in sim.state.ambulances.values():
                        amb.traffic_level = t_level
                        amb.road_condition = r_cond
                        amb.recalculate_eta()
                        affected_count += 1

                return {
                    "traffic_level": t_level,
                    "road_condition": r_cond,
                    "units_updated": affected_count,
                    "sim_time": sim.state.current_time,
                }

            else:
                raise ValueError(f"Unsupported event type: {event.event_type}")

    def _build_response(
        self,
        event: NormalizedEvent,
        status: EventStatus,
        message: str,
        start_t: float,
        result: Optional[Dict[str, Any]] = None,
        duplicate_of: Optional[str] = None,
        seen_count: int = 1,
    ) -> IngestionResponse:
        """Construct standardized IngestionResponse and track latency."""
        latency_ms = (time.perf_counter() - start_t) * 1000.0
        self.total_latency_ms += latency_ms

        # Record into central Observability MetricsCollector (M13.5 Phase 1)
        try:
            from api.observability.metrics import metrics_collector
            metrics_collector.record_ingestion(status=status.value, duration_ms=latency_ms)
        except Exception:
            pass

        # Track per-event-type breakdown
        etype = getattr(event.event_type, "value", str(event.event_type))
        if etype in self.by_event_type:
            self.by_event_type[etype]["ingested"] += 1
            st_key = status.value.lower()
            if st_key in self.by_event_type[etype]:
                self.by_event_type[etype][st_key] += 1

        return IngestionResponse(
            status=status,
            event_id=event.event_id,
            source=event.source,
            source_event_id=event.source_event_id,
            event_type=event.event_type,
            received_at=event.received_at,
            message=message,
            result=result,
            correlation_id=event.correlation_id,
            duplicate_of=duplicate_of,
            seen_count=seen_count,
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Return live ingestion performance metrics."""
        mean_lat = (self.total_latency_ms / self.total_ingested) if self.total_ingested > 0 else 0.0
        return {
            "total_ingested": self.total_ingested,
            "total_accepted": self.total_accepted,
            "total_duplicates": self.total_duplicates,
            "total_rejected": self.total_rejected,
            "total_stale": self.total_stale,
            "mean_latency_ms": round(mean_lat, 2),
            "by_event_type": {k: dict(v) for k, v in self.by_event_type.items()},
        }


# Global singleton instance
ingestion_service = IngestionService()
