"""
RAAH External Ingestion & Event API Router
==========================================

Exposes authenticated, normalized REST endpoints for external CAD call intake,
ambulance AVL/GPS telemetry, hospital status feeds, and traffic advisories.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Header

from api.dependencies import manager
from api.auth import (
    AuthenticatedUser,
    Permission,
    require_permission,
)
from api.adapters import (
    NormalizedEvent,
    IngestionResponse,
    CADIncidentInput,
    AmbulanceGPSInput,
    HospitalStatusInput,
    TrafficUpdateInput,
    EventType,
    ingestion_service,
    adapter_registry,
)

router = APIRouter(prefix="/ingestion", tags=["External Ingestion & CAD Adapters"])


# ======================================================================
# 1. CAD INCIDENT INGESTION
# ======================================================================

@router.post(
    "/cad/incident",
    response_model=IngestionResponse,
    summary="Ingest CAD emergency incident",
    description="Ingest, validate, deduplicate, and authoritatively dispatch an emergency call from external CAD.",
)
def ingest_cad_incident(
    req: CADIncidentInput,
    x_correlation_id: Optional[str] = Header(default=None),
    user: AuthenticatedUser = Depends(require_permission(Permission.INGEST_EMERGENCY)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.INCIDENT_CALL.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or payload.get("correlation_id") or None,
        payload=payload,
        metadata={"operator": user.username, "role": user.role.value},
    )

    resp = ingestion_service.ingest_event(normalized, operator=user.username)
    return resp


# ======================================================================
# 2. AMBULANCE GPS / AVL TELEMETRY INGESTION
# ======================================================================

@router.post(
    "/gps/location",
    response_model=IngestionResponse,
    summary="Ingest ambulance GPS telemetry",
    description="Ingest AVL GPS fix, update vehicle kinematics, and recalculate route ETAs.",
)
def ingest_ambulance_gps(
    req: AmbulanceGPSInput,
    x_correlation_id: Optional[str] = Header(default=None),
    user: AuthenticatedUser = Depends(require_permission(Permission.STANDARD_DISPATCH)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.AMBULANCE_GPS.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={"operator": user.username},
    )

    resp = ingestion_service.ingest_event(normalized, operator=user.username)
    return resp


# ======================================================================
# 3. HOSPITAL STATUS TELEMETRY INGESTION
# ======================================================================

@router.post(
    "/hospital/status",
    response_model=IngestionResponse,
    summary="Ingest hospital capacity telemetry",
    description="Ingest bed, ICU, and load telemetry from regional hospital feeds.",
)
def ingest_hospital_status(
    req: HospitalStatusInput,
    x_correlation_id: Optional[str] = Header(default=None),
    user: AuthenticatedUser = Depends(require_permission(Permission.APPROVE_HOSPITAL_DIVERSION)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.HOSPITAL_STATUS.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={"operator": user.username},
    )

    resp = ingestion_service.ingest_event(normalized, operator=user.username)
    return resp


# ======================================================================
# 4. TRAFFIC ADVISORY INGESTION
# ======================================================================

@router.post(
    "/traffic/update",
    response_model=IngestionResponse,
    summary="Ingest real-time traffic conditions",
    description="Ingest congestion or road condition updates affecting transit routes.",
)
def ingest_traffic_update(
    req: TrafficUpdateInput,
    x_correlation_id: Optional[str] = Header(default=None),
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.TRAFFIC_UPDATE.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={"operator": user.username},
    )

    resp = ingestion_service.ingest_event(normalized, operator=user.username)
    return resp


# ======================================================================
# 5. GENERIC NORMALIZED EVENT INGESTION
# ======================================================================

@router.post(
    "/event",
    response_model=IngestionResponse,
    summary="Ingest raw normalized event",
    description="Generic ingestion endpoint accepting fully-formed NormalizedEvent payloads.",
)
def ingest_generic_event(
    event: NormalizedEvent,
    user: AuthenticatedUser = Depends(require_permission(Permission.INGEST_EMERGENCY)),
):
    resp = ingestion_service.ingest_event(event, operator=user.username)
    return resp


# ======================================================================
# 6. IDEMPOTENCY RECORD RETRIEVAL
# ======================================================================

@router.get(
    "/idempotency/{source}/{source_event_id}",
    summary="Get event idempotency record",
    description="Retrieve durable deduplication record for an external event.",
)
def get_idempotency_record(
    source: str,
    source_event_id: str,
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
):
    rec = manager.persistence_store.get_idempotency_record(source, source_event_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Idempotency record not found for {source}:{source_event_id}")
    return rec.to_dict()


# ======================================================================
# 7. INGESTION STATUS & PROVIDER TELEMETRY
# ======================================================================

@router.get(
    "/status",
    summary="Get ingestion metrics and provider health",
    description="Returns live throughput, deduplication metrics, and adapter health checks.",
)
def get_ingestion_status(
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": ingestion_service.get_metrics(),
        "adapters": adapter_registry.health_check_all(),
    }
