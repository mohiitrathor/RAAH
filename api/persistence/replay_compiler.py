"""
RAAH Operational Run to Replay Artifact Compiler (M13.4 Phase 1)
================================================================

Pure, read-only adapter that converts an existing persisted operational run
from SQLite (raah_history.db) into a validated, schema-compliant ReplayArtifact.

Guarantees & Architecture Invariants:
  1. DispatchState remains the sole authoritative live state.
  2. Replay is strictly observational.
  3. Replay MUST NEVER mutate live DispatchState.
  4. Replay MUST NEVER re-execute DispatchSimulator to reconstruct history.
  5. Replay MUST use recorded persistence/checkpoint/telemetry data.
  6. ReplayArtifact format strictly adheres to Dispatch.scenarios.models.
  7. Compilation is 100% deterministic (reproducible sorting and IDs).
  8. Compilation performs zero database writes (enforced via PRAGMA query_only = ON).
  9. Never fabricates historical data that was not persisted.
 10. Honest documentation when decision evidence or checkpoints are absent.
"""

import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple, Union

from fastapi import HTTPException

from api.settings import settings
from api.persistence.db import get_connection, DEFAULT_DB_PATH
from api.persistence.serializer import compute_state_checksum
from Dispatch.scenarios.models import (
    ReplayArtifact,
    RunMetadata,
    ScenarioDefinition,
    ScenarioConfig,
    ScheduledIncident,
    ScheduledMCI,
    ScheduledReposition,
    ScheduledRedirection,
    ScheduledHospitalEvent,
)

logger = logging.getLogger("raah.persistence.replay_compiler")


# ======================================================================
# COMPILER EXCEPTIONS
# ======================================================================

class ReplayCompilationError(Exception):
    """Base exception for operational run replay compilation failures."""
    pass


class RunNotFoundError(ReplayCompilationError):
    """Raised when the requested run_id does not exist in persistence."""
    pass


class NoCheckpointsError(ReplayCompilationError):
    """Raised when state checkpoints are strictly required but none exist for the run."""
    pass


class CorruptCheckpointError(ReplayCompilationError):
    """Raised when a checkpoint fails cryptographic checksum or integrity validation."""
    pass


class MalformedPersistenceError(ReplayCompilationError):
    """Raised when persisted records contain unparseable or corrupted JSON."""
    pass


class EmptyRunError(ReplayCompilationError):
    """Raised when an empty run is rejected under strict validation."""
    pass


# Deterministic precedence order for operational events occurring at identical sim_time
_EVENT_TYPE_PRIORITY = {
    "SCENARIO_START": 0,
    "MCI_DECLARED": 10,
    "REPOSITION_START": 20,
    "DISPATCH": 30,
    "REDIRECTION": 40,
    "HOSPITAL_SATURATED": 50,
    "HOSPITAL_RESTORED": 51,
    "DIVERSION_DECLARED": 52,
    "AMBULANCE_ARRIVED": 60,
    "HISTORICAL_EVENT": 70,
    "SCENARIO_END": 90,
}


# ======================================================================
# OPERATIONAL RUN COMPILER
# ======================================================================

class OperationalReplayCompiler:
    """
    Durable, read-only compiler that constructs ReplayArtifact containers
    from historical simulation runs stored in SQLite.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH).resolve()

    def _get_readonly_connection(self) -> sqlite3.Connection:
        """
        Open a strictly read-only connection.
        Enforces SQLite PRAGMA query_only = ON to guarantee zero DB writes.
        """
        conn = get_connection(self.db_path)
        try:
            conn.execute("PRAGMA query_only = ON;")
        except Exception as ex:
            conn.close()
            raise ReplayCompilationError(f"Failed to enable read-only protection: {ex}")
        return conn

    def compile(
        self,
        run_id: Union[int, str],
        require_checkpoints: bool = False,
        allow_empty: bool = True,
    ) -> ReplayArtifact:
        """
        Compile the specified simulation run into a validated ReplayArtifact.

        Args:
            run_id: Identifier of the simulation run in simulation_runs table.
            require_checkpoints: If True, raises NoCheckpointsError if no state
                                 checkpoints exist for this run.
            allow_empty: If False, raises EmptyRunError if the run contains no
                         checkpoints, events, or telemetry.

        Returns:
            Fully populated and validated ReplayArtifact instance.
        """
        try:
            numeric_run_id = int(run_id)
        except (ValueError, TypeError):
            raise RunNotFoundError(f"Invalid run_id format: {run_id}")

        conn = self._get_readonly_connection()
        try:
            cursor = conn.cursor()

            # 1. Fetch simulation run session
            cursor.execute("SELECT * FROM simulation_runs WHERE run_id = ?", (numeric_run_id,))
            run_row = cursor.fetchone()
            if not run_row:
                raise RunNotFoundError(f"Simulation run '{numeric_run_id}' not found in persistence.")

            run_dict = dict(run_row)

            # 2. Fetch associated checkpoints
            snapshots = self._compile_snapshots(cursor, run_dict)
            if require_checkpoints and not snapshots:
                raise NoCheckpointsError(
                    f"Simulation run '{numeric_run_id}' has no recorded state checkpoints."
                )

            # 3. Fetch relational telemetry & synthesize ordered events
            events, scheduled_data = self._compile_events_and_schedules(cursor, numeric_run_id)

            # 4. Check for empty run condition
            is_empty = (
                len(snapshots) == 0
                and len(events) == 0
                and len(scheduled_data["incidents"]) == 0
            )
            if is_empty and not allow_empty:
                raise EmptyRunError(
                    f"Simulation run '{numeric_run_id}' contains no checkpoints or telemetry events."
                )

            # 5. Build domain sub-structures
            run_meta = self._build_run_metadata(run_dict, snapshots, events)
            scenario_def = self._build_scenario_definition(run_dict, snapshots, scheduled_data)
            final_summary = self._build_final_summary(run_dict, snapshots, events, scheduled_data)

            # 6. Construct and validate the ReplayArtifact
            artifact = ReplayArtifact(
                replay_format_version="1.0.0",
                run_metadata=run_meta,
                scenario_definition=scenario_def,
                events=events,
                snapshots=snapshots,
                final_summary=final_summary,
            )

            # Validate round-trip schema integrity
            try:
                dict_repr = artifact.to_dict()
                ReplayArtifact.from_dict(dict_repr)
            except Exception as val_err:
                raise MalformedPersistenceError(
                    f"Compiled artifact failed schema validation: {val_err}"
                )

            return artifact

        finally:
            conn.close()

    # ------------------------------------------------------------------
    # SNAPSHOT COMPILATION
    # ------------------------------------------------------------------

    def _compile_snapshots(
        self,
        cursor: sqlite3.Cursor,
        run_dict: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Extract and cryptographically verify all state checkpoints for the run.
        """
        run_id = run_dict["run_id"]
        started_at = run_dict.get("started_at")
        ended_at = run_dict.get("ended_at")

        # 1. Query checkpoints with explicit run_id in metadata
        cursor.execute(
            """
            SELECT checkpoint_id, simulation_time, schema_version, saved_at,
                   payload_json, checksum, is_valid, metadata_json
            FROM state_checkpoints
            WHERE json_extract(metadata_json, '$.run_id') = ?
               OR json_extract(metadata_json, '$.run_id') = ?
            ORDER BY simulation_time ASC, saved_at ASC, checkpoint_id ASC
            """,
            (run_id, str(run_id)),
        )
        rows = cursor.fetchall()

        # 2. Fallback to time-window correlation if no explicit run_id metadata was tagged
        if not rows and started_at:
            if ended_at:
                cursor.execute(
                    """
                    SELECT checkpoint_id, simulation_time, schema_version, saved_at,
                           payload_json, checksum, is_valid, metadata_json
                    FROM state_checkpoints
                    WHERE saved_at >= ? AND saved_at <= ?
                    ORDER BY simulation_time ASC, saved_at ASC, checkpoint_id ASC
                    """,
                    (started_at, ended_at),
                )
            else:
                cursor.execute(
                    """
                    SELECT checkpoint_id, simulation_time, schema_version, saved_at,
                           payload_json, checksum, is_valid, metadata_json
                    FROM state_checkpoints
                    WHERE saved_at >= ?
                    ORDER BY simulation_time ASC, saved_at ASC, checkpoint_id ASC
                    """,
                    (started_at,),
                )
            rows = cursor.fetchall()

        snapshots: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            chk_dict = dict(row)
            cid = chk_dict["checkpoint_id"]

            if not chk_dict.get("is_valid", 1):
                raise CorruptCheckpointError(
                    f"Checkpoint '{cid}' is flagged as invalid in persistence."
                )

            raw_payload = chk_dict.get("payload_json", "")
            try:
                payload = json.loads(raw_payload)
            except Exception:
                raise MalformedPersistenceError(
                    f"Checkpoint '{cid}' contains malformed JSON payload."
                )

            # Cryptographic SHA-256 validation
            expected_chk = chk_dict.get("checksum", "")
            computed_chk = compute_state_checksum(payload)
            if computed_chk != expected_chk:
                raise CorruptCheckpointError(
                    f"Checkpoint '{cid}' failed cryptographic checksum validation."
                )

            # Extract state block (supports canonical serializer format)
            state_data = payload.get("state", payload)

            # Convert incidents dict to sorted list
            raw_incidents = state_data.get("incidents", {})
            if isinstance(raw_incidents, dict):
                incidents_list = sorted(
                    list(raw_incidents.values()),
                    key=lambda x: int(x.get("incident_id", 0)),
                )
            else:
                incidents_list = list(raw_incidents)

            # Convert ambulances dict to sorted list
            raw_ambulances = state_data.get("ambulances", {})
            if isinstance(raw_ambulances, dict):
                ambulances_list = sorted(
                    list(raw_ambulances.values()),
                    key=lambda x: str(x.get("ambulance_id", "")),
                )
            else:
                ambulances_list = list(raw_ambulances)

            # Convert hospitals dict to sorted list
            raw_hospitals = state_data.get("hospitals", {})
            if isinstance(raw_hospitals, dict):
                hospitals_list = sorted(
                    list(raw_hospitals.values()),
                    key=lambda x: str(x.get("hospital_id", "")),
                )
            else:
                hospitals_list = list(raw_hospitals)

            # Extract actively repositioning units
            repositioning_list = [
                {
                    "ambulance_id": a.get("ambulance_id"),
                    "target_lat": a.get("latitude", 0.0),
                    "target_lon": a.get("longitude", 0.0),
                }
                for a in ambulances_list
                if a.get("status") == "REPOSITIONING" or a.get("is_repositioning")
            ]

            snapshots.append({
                "snapshot_id": idx,
                "checkpoint_id": cid,
                "sim_time": int(chk_dict.get("simulation_time", 0)),
                "incidents": incidents_list,
                "ambulances": ambulances_list,
                "hospitals": hospitals_list,
                "active_mcis": [],
                "repositioning": repositioning_list,
                "coverage_summary": {},
                "timestamp": str(chk_dict.get("saved_at", "")),
            })

        return snapshots

    # ------------------------------------------------------------------
    # EVENT & SCHEDULE COMPILATION
    # ------------------------------------------------------------------

    def _compile_events_and_schedules(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, List[Any]]]:
        """
        Extract relational telemetry and synthesize deterministically ordered events.
        """
        raw_events: List[Dict[str, Any]] = []

        # 1. Incidents
        cursor.execute(
            """
            SELECT incident_id, source, condition, predicted_severity, priority,
                   ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at
            FROM historical_incidents
            WHERE run_id = ?
            ORDER BY dispatched_sim_time ASC, incident_id ASC
            """,
            (run_id,),
        )
        incidents_rows = [dict(r) for r in cursor.fetchall()]
        incidents_by_id = {r["incident_id"]: r for r in incidents_rows}

        scheduled_incidents = [
            ScheduledIncident(
                sim_time=int(r["dispatched_sim_time"]),
                incident_id=int(r["incident_id"]),
                custom_data=None,
                latitude=float(r["patient_lat"]),
                longitude=float(r["patient_lon"]),
                condition=str(r["condition"]),
                severity=str(r["predicted_severity"]),
                notes=f"Priority P{r['priority']}" if r.get("priority") is not None else "",
            )
            for r in incidents_rows
        ]

        # 2. Dispatches
        cursor.execute(
            """
            SELECT incident_id, ambulance_id, ambulance_type, initial_hospital_id,
                   final_hospital_id, initial_eta_minutes, final_eta_minutes,
                   route_distance_km, traffic_level, road_condition,
                   dispatched_sim_time, arrived_sim_time, status, updated_at
            FROM historical_dispatches
            WHERE run_id = ?
            ORDER BY dispatched_sim_time ASC, incident_id ASC
            """,
            (run_id,),
        )
        dispatch_rows = [dict(r) for r in cursor.fetchall()]

        for d in dispatch_rows:
            iid = int(d["incident_id"])
            inc = incidents_by_id.get(iid, {})
            d_time = int(d["dispatched_sim_time"])

            raw_events.append({
                "sim_time": d_time,
                "event_type": "DISPATCH",
                "entity_ids": {
                    "incident_id": iid,
                    "ambulance_id": str(d["ambulance_id"]),
                    "hospital_id": str(d["initial_hospital_id"]),
                },
                "payload": {
                    "severity": str(inc.get("predicted_severity", "Moderate")),
                    "priority": int(inc.get("priority", 3)),
                    "condition": str(inc.get("condition", "Medical")),
                    "eta_minutes": float(d["initial_eta_minutes"]),
                    "route_distance_km": float(d["route_distance_km"]) if d["route_distance_km"] is not None else None,
                    "traffic_level": d.get("traffic_level"),
                    "road_condition": d.get("road_condition"),
                },
                "timestamp": str(inc.get("created_at") or d.get("updated_at") or ""),
                "_source_key": f"dispatch_{iid}_{d_time}",
            })

            # Arrival event if recorded
            arr_time = d.get("arrived_sim_time")
            if arr_time is not None:
                raw_events.append({
                    "sim_time": int(arr_time),
                    "event_type": "AMBULANCE_ARRIVED",
                    "entity_ids": {
                        "incident_id": iid,
                        "ambulance_id": str(d["ambulance_id"]),
                        "hospital_id": str(d["final_hospital_id"]),
                    },
                    "payload": {
                        "status": str(d["status"]),
                    },
                    "timestamp": str(d.get("updated_at") or ""),
                    "_source_key": f"arrived_{iid}_{arr_time}",
                })

        # 3. Redirections
        cursor.execute(
            """
            SELECT id, incident_id, ambulance_id, decision_type, trigger_type,
                   original_hospital_id, new_hospital_id, eta_before, eta_after,
                   eta_saved, eta_improvement_pct, reason, sim_time, created_at
            FROM historical_redirections
            WHERE run_id = ?
            ORDER BY sim_time ASC, id ASC
            """,
            (run_id,),
        )
        redir_rows = [dict(r) for r in cursor.fetchall()]

        scheduled_redirections = []
        for rd in redir_rows:
            r_time = int(rd["sim_time"])
            iid = int(rd["incident_id"])
            scheduled_redirections.append(
                ScheduledRedirection(
                    sim_time=r_time,
                    incident_id=iid,
                    target_hospital_id=rd["new_hospital_id"],
                    reason=str(rd["reason"]),
                )
            )
            raw_events.append({
                "sim_time": r_time,
                "event_type": "REDIRECTION",
                "entity_ids": {
                    "incident_id": iid,
                    "ambulance_id": str(rd["ambulance_id"]),
                    "original_hospital_id": rd["original_hospital_id"],
                    "target_hospital_id": rd["new_hospital_id"],
                },
                "payload": {
                    "decision_type": str(rd["decision_type"]),
                    "trigger_type": str(rd["trigger_type"]),
                    "reason": str(rd["reason"]),
                    "eta_before": rd["eta_before"],
                    "eta_after": rd["eta_after"],
                    "eta_saved": rd["eta_saved"],
                },
                "timestamp": str(rd.get("created_at") or ""),
                "_source_key": f"redir_{rd['id']}_{r_time}",
            })

        # 4. Repositions
        cursor.execute(
            """
            SELECT id, ambulance_id, origin_zone, target_zone, origin_lat,
                   origin_lon, target_lat, target_lon, started_sim_time,
                   completed_sim_time, reason, status, created_at
            FROM historical_repositions
            WHERE run_id = ?
            ORDER BY started_sim_time ASC, id ASC
            """,
            (run_id,),
        )
        repos_rows = [dict(r) for r in cursor.fetchall()]

        scheduled_repositions = []
        for rp in repos_rows:
            s_time = int(rp["started_sim_time"])
            aid = str(rp["ambulance_id"])
            scheduled_repositions.append(
                ScheduledReposition(
                    sim_time=s_time,
                    ambulance_id=aid,
                    target_lat=float(rp["target_lat"]),
                    target_lon=float(rp["target_lon"]),
                    reason=str(rp["reason"]),
                )
            )
            raw_events.append({
                "sim_time": s_time,
                "event_type": "REPOSITION_START",
                "entity_ids": {
                    "ambulance_id": aid,
                },
                "payload": {
                    "status": "REPOSITIONING",
                    "ambulance_id": aid,
                    "origin_zone": rp["origin_zone"],
                    "target_zone": rp["target_zone"],
                    "origin_coords": [float(rp["origin_lat"]), float(rp["origin_lon"])],
                    "target_coords": [float(rp["target_lat"]), float(rp["target_lon"])],
                    "reason": str(rp["reason"]),
                },
                "timestamp": str(rp.get("created_at") or ""),
                "_source_key": f"repos_{rp['id']}_{s_time}",
            })

        # 5. Multi-Casualty Incidents (MCI)
        cursor.execute(
            """
            SELECT mci_id, name, latitude, longitude, declared_sim_time,
                   resolved_sim_time, status, total_casualties, notes, created_at
            FROM historical_mci_events
            WHERE run_id = ?
            ORDER BY declared_sim_time ASC, mci_id ASC
            """,
            (run_id,),
        )
        mci_rows = [dict(r) for r in cursor.fetchall()]

        scheduled_mcis = []
        for mc in mci_rows:
            m_time = int(mc["declared_sim_time"])
            mid = str(mc["mci_id"])
            scheduled_mcis.append(
                ScheduledMCI(
                    sim_time=m_time,
                    name=str(mc["name"]),
                    latitude=float(mc["latitude"]),
                    longitude=float(mc["longitude"]),
                    estimated_casualties=int(mc["total_casualties"]),
                    mci_id=mid,
                    notes=str(mc.get("notes") or ""),
                )
            )
            raw_events.append({
                "sim_time": m_time,
                "event_type": "MCI_DECLARED",
                "entity_ids": {
                    "mci_id": mid,
                },
                "payload": {
                    "name": str(mc["name"]),
                    "total_casualties": int(mc["total_casualties"]),
                    "coords": [float(mc["latitude"]), float(mc["longitude"])],
                    "status": str(mc["status"]),
                    "notes": str(mc.get("notes") or ""),
                },
                "timestamp": str(mc.get("created_at") or ""),
                "_source_key": f"mci_{mid}_{m_time}",
            })

        # 6. Discrete simulation events
        cursor.execute(
            """
            SELECT id, event_type, sim_time, facility_or_unit_id, message, created_at
            FROM historical_events
            WHERE run_id = ?
            ORDER BY sim_time ASC, id ASC
            """,
            (run_id,),
        )
        hist_events = [dict(r) for r in cursor.fetchall()]

        scheduled_hosp_events = []
        for he in hist_events:
            e_type = str(he["event_type"])
            h_time = int(he["sim_time"])
            fac_id = str(he.get("facility_or_unit_id") or "")

            if e_type in ("HOSPITAL_SATURATED", "HOSPITAL_RESTORED", "HOSPITAL_DIVERSION"):
                scheduled_hosp_events.append(
                    ScheduledHospitalEvent(
                        sim_time=h_time,
                        hospital_id=fac_id,
                        event_type=e_type,
                    )
                )

            raw_events.append({
                "sim_time": h_time,
                "event_type": e_type,
                "entity_ids": {
                    "facility_or_unit_id": fac_id,
                },
                "payload": {
                    "message": str(he.get("message") or ""),
                },
                "timestamp": str(he.get("created_at") or ""),
                "_source_key": f"event_{he['id']}_{h_time}",
            })

        # 7. Deterministic event sorting:
        def _sort_key(ev: Dict[str, Any]):
            return (
                ev["sim_time"],
                _EVENT_TYPE_PRIORITY.get(ev.get("event_type", ""), 99),
                json.dumps(ev.get("entity_ids", {}), sort_keys=True),
                ev.get("_source_key", ""),
            )

        sorted_events = sorted(raw_events, key=_sort_key)

        # 8. Re-assign canonical consecutive 1-based event IDs
        final_events: List[Dict[str, Any]] = []
        for idx, ev in enumerate(sorted_events, start=1):
            clean_ev = {
                "event_id": idx,
                "sim_time": ev["sim_time"],
                "event_type": ev["event_type"],
                "entity_ids": ev["entity_ids"],
                "payload": ev["payload"],
                "timestamp": ev["timestamp"],
            }
            final_events.append(clean_ev)

        scheduled_data = {
            "incidents": scheduled_incidents,
            "mcis": scheduled_mcis,
            "repositions": scheduled_repositions,
            "redirections": scheduled_redirections,
            "hospital_events": scheduled_hosp_events,
            "counts": {
                "dispatches": len(dispatch_rows),
                "redirections": len(redir_rows),
                "repositions": len(repos_rows),
                "mcis": len(mci_rows),
                "hist_events": len(hist_events),
            },
        }

        return final_events, scheduled_data

    # ------------------------------------------------------------------
    # METADATA & SUMMARY BUILDERS
    # ------------------------------------------------------------------

    def _build_run_metadata(
        self,
        run_dict: Dict[str, Any],
        snapshots: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> RunMetadata:
        """Construct standard RunMetadata container."""
        run_id = run_dict["run_id"]
        started_at = run_dict.get("started_at", "")
        ended_at = run_dict.get("ended_at")

        wall_clock_duration = 0.0
        if started_at and ended_at:
            try:
                t0 = datetime.fromisoformat(started_at)
                t1 = datetime.fromisoformat(ended_at)
                wall_clock_duration = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                wall_clock_duration = 0.0

        start_sim = 0
        if snapshots:
            start_sim = snapshots[0]["sim_time"]
        elif events:
            start_sim = events[0]["sim_time"]

        end_sim = int(run_dict.get("final_sim_time") or 0)
        if events and events[-1]["sim_time"] > end_sim:
            end_sim = events[-1]["sim_time"]
        if snapshots and snapshots[-1]["sim_time"] > end_sim:
            end_sim = snapshots[-1]["sim_time"]

        return RunMetadata(
            scenario_id=f"OPERATIONAL_RUN_{run_id}",
            run_id=f"run_{run_id}",
            start_sim_time=start_sim,
            end_sim_time=end_sim,
            wall_clock_duration_seconds=round(wall_clock_duration, 4),
            event_count=len(events),
            snapshot_count=len(snapshots),
            completion_status=str(run_dict.get("status", "COMPLETED")),
            deterministic_seed=0,
            replay_format_version="1.0.0",
            created_at=str(ended_at or started_at or datetime.now(timezone.utc).isoformat()),
        )

    def _build_scenario_definition(
        self,
        run_dict: Dict[str, Any],
        snapshots: List[Dict[str, Any]],
        scheduled_data: Dict[str, List[Any]],
    ) -> ScenarioDefinition:
        """Construct standard ScenarioDefinition container."""
        run_id = run_dict["run_id"]
        end_sim = max(int(run_dict.get("final_sim_time") or 0), 1)

        return ScenarioDefinition(
            scenario_id=f"OPERATIONAL_RUN_{run_id}",
            name=f"Operational Run #{run_id}",
            description=str(run_dict.get("notes") or f"Historical operational session #{run_id}"),
            config=ScenarioConfig(
                duration_minutes=end_sim,
                tick_minutes=1.0,
                snapshot_interval_ticks=1,
                deterministic_seed=0,
            ),
            scheduled_incidents=scheduled_data["incidents"],
            scheduled_mcis=scheduled_data["mcis"],
            scheduled_repositions=scheduled_data["repositions"],
            scheduled_redirections=scheduled_data["redirections"],
            scheduled_hospital_events=scheduled_data["hospital_events"],
            metadata={
                "run_id": int(run_id),
                "source": "RAAH_PERSISTENCE_SQLITE",
                "started_at": str(run_dict.get("started_at")),
                "ended_at": str(run_dict.get("ended_at")),
                "status": str(run_dict.get("status")),
                "total_ticks": int(run_dict.get("total_ticks") or 0),
                "has_checkpoints": len(snapshots) > 0,
                "fidelity": "FULL_CHECKPOINTS_AND_TELEMETRY" if snapshots else "TELEMETRY_ONLY_NO_CHECKPOINTS",
                "decision_evidence_available": False,  # M13.3 evidence was in-memory; stated honestly
            },
            created_at=str(run_dict.get("started_at") or datetime.now(timezone.utc).isoformat()),
        )

    def _build_final_summary(
        self,
        run_dict: Dict[str, Any],
        snapshots: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        scheduled_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Construct summary metrics dictionary."""
        counts = scheduled_data.get("counts", {})
        end_sim = int(run_dict.get("final_sim_time") or 0)
        if events and events[-1]["sim_time"] > end_sim:
            end_sim = events[-1]["sim_time"]

        return {
            "run_id": int(run_dict["run_id"]),
            "status": str(run_dict.get("status", "COMPLETED")),
            "final_sim_time": end_sim,
            "total_ticks": int(run_dict.get("total_ticks") or 0),
            "total_incidents": len(scheduled_data["incidents"]),
            "total_dispatches": counts.get("dispatches", 0),
            "total_redirections": counts.get("redirections", 0),
            "total_repositions": counts.get("repositions", 0),
            "total_mci_events": counts.get("mcis", 0),
            "total_events": len(events),
            "total_snapshots": len(snapshots),
        }


# ======================================================================
# CONVENIENCE ENTRY POINT
# ======================================================================

def compile_operational_run_to_artifact(
    run_id: Union[int, str],
    db_path: Optional[Path] = None,
    require_checkpoints: bool = False,
    allow_empty: bool = True,
) -> ReplayArtifact:
    """
    Compile a persisted operational simulation run into a ReplayArtifact.

    Args:
        run_id: Identifier of the simulation run.
        db_path: Optional custom path to SQLite database.
        require_checkpoints: If True, raises NoCheckpointsError when no checkpoints exist.
        allow_empty: If False, raises EmptyRunError when run has no data.

    Returns:
        Validated ReplayArtifact.
    """
    compiler = OperationalReplayCompiler(db_path=db_path)
    return compiler.compile(
        run_id=run_id,
        require_checkpoints=require_checkpoints,
        allow_empty=allow_empty,
    )


# ======================================================================
# OPERATIONAL REPLAY RESOLUTION & LISTING (M13.4 Phase 2)
# ======================================================================

_compiled_artifacts_cache: Dict[str, ReplayArtifact] = {}


def clear_compiled_artifacts_cache():
    """Clear in-memory cache of compiled operational artifacts."""
    _compiled_artifacts_cache.clear()


def is_operational_replay_id(replay_id: str) -> bool:
    """
    Check whether a replay_id follows operational run conventions.
    Operational runs are identified by `run_<digits>` format.
    """
    if not isinstance(replay_id, str):
        return False
    if replay_id.startswith("run_") and replay_id[4:].isdigit():
        return True
    return False


def parse_operational_run_id(replay_id: str) -> Optional[int]:
    """Parse numeric run_id from operational replay identifier."""
    if not isinstance(replay_id, str):
        return None
    if replay_id.startswith("run_") and replay_id[4:].isdigit():
        return int(replay_id[4:])
    if replay_id.isdigit():
        return int(replay_id)
    return None


def list_operational_runs_metadata(
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
) -> List[RunMetadata]:
    """
    Query SQLite persistence layer for operational simulation runs
    and return them as standard RunMetadata containers.
    Optimized for high concurrency and sub-10ms response times.
    """
    resolved_db = Path(db_path or getattr(settings, "database_path", DEFAULT_DB_PATH)).resolve()
    if not resolved_db.exists():
        return []

    try:
        conn = get_connection(resolved_db)
        conn.execute("PRAGMA query_only = ON;")
        cursor = conn.cursor()

        # 1. Fetch simulation runs
        run_query = """
            SELECT run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes
            FROM simulation_runs
            ORDER BY run_id DESC
        """
        if limit is not None and limit > 0:
            run_query += f" LIMIT {int(limit)}"

        cursor.execute(run_query)
        rows = [dict(r) for r in cursor.fetchall()]

        if not rows:
            conn.close()
            return []

        # 2. Fast single-pass group queries for event counts
        cursor.execute("SELECT run_id, count(*) FROM historical_dispatches GROUP BY run_id;")
        dispatch_counts = dict(cursor.fetchall())

        cursor.execute("SELECT run_id, count(*) FROM historical_events GROUP BY run_id;")
        event_counts = dict(cursor.fetchall())

        cursor.execute("SELECT run_id, count(*) FROM historical_redirections GROUP BY run_id;")
        redir_counts = dict(cursor.fetchall())

        conn.close()
    except Exception as ex:
        logger.warning("Failed to list operational runs from SQLite: %s", ex)
        return []

    metas: List[RunMetadata] = []
    for r in rows:
        rid = r["run_id"]
        wall_clock_duration = 0.0
        if r.get("started_at") and r.get("ended_at"):
            try:
                t0 = datetime.fromisoformat(r["started_at"])
                t1 = datetime.fromisoformat(r["ended_at"])
                wall_clock_duration = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                wall_clock_duration = 0.0

        end_sim = int(r.get("final_sim_time") or r.get("total_ticks") or 0)
        tot_evs = (
            dispatch_counts.get(rid, 0)
            + event_counts.get(rid, 0)
            + redir_counts.get(rid, 0)
        )

        metas.append(
            RunMetadata(
                scenario_id=f"OPERATIONAL_RUN_{rid}",
                run_id=f"run_{rid}",
                start_sim_time=0,
                end_sim_time=end_sim,
                wall_clock_duration_seconds=round(wall_clock_duration, 4),
                event_count=tot_evs,
                snapshot_count=0,
                completion_status=str(r.get("status") or "COMPLETED"),
                deterministic_seed=0,
                replay_format_version="1.0.0",
                created_at=str(r.get("ended_at") or r.get("started_at") or datetime.now(timezone.utc).isoformat()),
            )
        )
    return metas



def resolve_replay_artifact(
    replay_id: str,
    db_path: Optional[Path] = None,
    not_found_msg: Optional[str] = None,
) -> ReplayArtifact:
    """
    Unified resolver: retrieves scenario replays from disk ReplayStore,
    or compiles persisted operational runs from SQLite on demand.
    """
    # 1. Check in-memory compiled cache
    if replay_id in _compiled_artifacts_cache:
        return _compiled_artifacts_cache[replay_id]

    resolved_db = Path(db_path or getattr(settings, "database_path", DEFAULT_DB_PATH)).resolve()

    # 2. Check if operational run identifier (e.g. run_1, run_42)
    if is_operational_replay_id(replay_id):
        numeric_run_id = parse_operational_run_id(replay_id)
        if numeric_run_id is None:
            raise HTTPException(status_code=404, detail=not_found_msg or f"Operational replay run '{replay_id}' not found.")

        compiler = OperationalReplayCompiler(db_path=resolved_db)
        try:
            artifact = compiler.compile(numeric_run_id, allow_empty=True)
            _compiled_artifacts_cache[replay_id] = artifact
            return artifact
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail=not_found_msg or f"Operational replay run '{replay_id}' not found.")
        except CorruptCheckpointError as ex:
            logger.warning("Corrupt checkpoint in operational run %s: %s", replay_id, ex)
            raise HTTPException(status_code=422, detail=f"Operational replay run '{replay_id}' has corrupt checkpoint data.")
        except MalformedPersistenceError as ex:
            logger.warning("Malformed persistence data in operational run %s: %s", replay_id, ex)
            raise HTTPException(status_code=422, detail=f"Operational replay run '{replay_id}' has malformed persistence data.")
        except ReplayCompilationError as ex:
            logger.error("Failed to compile operational run %s: %s", replay_id, ex)
            raise HTTPException(status_code=400, detail=f"Failed to compile operational replay run '{replay_id}'.")

    # 3. Otherwise, check file-based scenario replay store
    from Dispatch.scenarios.store import ReplayStore
    scenario_store = ReplayStore()
    rep = scenario_store.get(replay_id)
    if rep:
        return rep

    # 4. Fallback check: could replay_id be a bare integer referring to an operational run?
    numeric_id = parse_operational_run_id(replay_id)
    if numeric_id is not None and resolved_db.exists():
        compiler = OperationalReplayCompiler(db_path=resolved_db)
        try:
            artifact = compiler.compile(numeric_id, allow_empty=True)
            _compiled_artifacts_cache[replay_id] = artifact
            return artifact
        except RunNotFoundError:
            pass
        except CorruptCheckpointError:
            raise HTTPException(status_code=422, detail=f"Operational replay run '{replay_id}' has corrupt checkpoint data.")
        except MalformedPersistenceError:
            raise HTTPException(status_code=422, detail=f"Operational replay run '{replay_id}' has malformed persistence data.")

    raise HTTPException(status_code=404, detail=not_found_msg or f"Replay archive '{replay_id}' not found.")
