"""
RAAH External Ingestion & Event API Router
==========================================

Exposes authenticated, normalized REST endpoints for external CAD call intake,
ambulance AVL/GPS telemetry, hospital status feeds, and traffic advisories.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Header, status

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
    require_ingestion_auth,
    IngestionIdentity,
    m2m_store,
    M2MCredentialSummary,
    CADIntakePayload,
    CADTriageMapper,
    CADNormalizationError,
)
from api.observability.metrics import metrics_collector

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
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.INCIDENT_CALL, Permission.INGEST_EMERGENCY)),
):
    payload = req.model_dump()
    role_val = auth.operator_user.role.value if auth.operator_user else None
    normalized = NormalizedEvent(
        event_type=EventType.INCIDENT_CALL.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or payload.get("correlation_id") or None,
        payload=payload,
        metadata={
            "operator": auth.attribution_name,
            "auth_type": auth.auth_type,
            "role": role_val,
            "key_id": auth.identifier if auth.is_m2m else None,
        },
    )

    resp = ingestion_service.ingest_event(normalized, operator=auth.attribution_name)
    return resp


# ======================================================================
# 1B. REALISTIC EXTERNAL CAD INTAKE & TRIAGE NORMALIZATION
# ======================================================================

@router.post(
    "/cad/intake",
    response_model=IngestionResponse,
    summary="Ingest realistic external CAD incident",
    description="Accept realistic CAD emergency incident payload, normalize and triage into RAAH clinical ML contract, and execute authoritative dispatch.",
)
def ingest_realistic_cad(
    req: CADIntakePayload,
    x_correlation_id: Optional[str] = Header(default=None),
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.INCIDENT_CALL, Permission.INGEST_EMERGENCY)),
):
    try:
        mapped_internal = CADTriageMapper.normalize(req)
    except CADNormalizationError as norm_err:
        metrics_collector.record_cad_intake_normalization_failure()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"CAD normalization failed: {norm_err}",
        )

    role_val = auth.operator_user.role.value if auth.operator_user else None
    normalized = NormalizedEvent(
        event_type=EventType.INCIDENT_CALL.value,
        source=req.source,
        source_event_id=req.external_incident_id,
        occurred_at=req.occurred_at,
        correlation_id=x_correlation_id or None,
        payload=mapped_internal,
        metadata={
            "operator": auth.attribution_name,
            "auth_type": auth.auth_type,
            "role": role_val,
            "key_id": auth.identifier if auth.is_m2m else None,
            "external_incident_id": req.external_incident_id,
            "call_type": req.call_type,
            "intake_mode": "realistic_cad_v1",
        },
    )

    resp = ingestion_service.ingest_event(normalized, operator=auth.attribution_name)
    if resp.status.value in ("ACCEPTED", "DUPLICATE"):
        metrics_collector.record_cad_intake_accepted()
    else:
        metrics_collector.record_cad_intake_rejected()

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
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.AMBULANCE_GPS, Permission.STANDARD_DISPATCH)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.AMBULANCE_GPS.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={
            "operator": auth.attribution_name,
            "auth_type": auth.auth_type,
            "key_id": auth.identifier if auth.is_m2m else None,
        },
    )

    resp = ingestion_service.ingest_event(normalized, operator=auth.attribution_name)
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
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.HOSPITAL_STATUS, Permission.APPROVE_HOSPITAL_DIVERSION)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.HOSPITAL_STATUS.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={
            "operator": auth.attribution_name,
            "auth_type": auth.auth_type,
            "key_id": auth.identifier if auth.is_m2m else None,
        },
    )

    resp = ingestion_service.ingest_event(normalized, operator=auth.attribution_name)
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
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.TRAFFIC_UPDATE, Permission.VIEW_LIVE)),
):
    payload = req.model_dump()
    normalized = NormalizedEvent(
        event_type=EventType.TRAFFIC_UPDATE.value,
        source=req.source,
        source_event_id=req.source_event_id,
        occurred_at=req.occurred_at or payload.pop("occurred_at", None),
        correlation_id=x_correlation_id or None,
        payload=payload,
        metadata={
            "operator": auth.attribution_name,
            "auth_type": auth.auth_type,
            "key_id": auth.identifier if auth.is_m2m else None,
        },
    )

    resp = ingestion_service.ingest_event(normalized, operator=auth.attribution_name)
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
    auth: IngestionIdentity = Depends(require_ingestion_auth(EventType.INCIDENT_CALL, Permission.INGEST_EMERGENCY)),
):
    if auth.is_m2m and not auth.allows_event_type(event.event_type):
        metrics_collector.record_m2m_scope_mismatch(auth.provider_id or auth.identifier)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: M2M credential '{auth.identifier}' is not authorized for event type '{event.event_type}'.",
        )

    resp = ingestion_service.ingest_event(event, operator=auth.attribution_name)
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
    metrics_snap = metrics_collector.get_snapshot()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": ingestion_service.get_metrics(),
        "adapters": adapter_registry.health_check_all(),
        "security": metrics_snap.get("security", {}),
        "cad_intake": metrics_snap.get("cad_intake", {}),
    }


# ======================================================================
# 8. M2M CREDENTIAL MANAGEMENT (ADMINISTRATIVE)
# ======================================================================

@router.get(
    "/m2m/credentials",
    response_model=List[M2MCredentialSummary],
    summary="List registered M2M credentials",
    description="Lists safe public metadata of configured external provider credentials (secrets and hashes excluded).",
)
def list_m2m_credentials(
    user: AuthenticatedUser = Depends(require_permission(Permission.USER_ADMINISTRATION)),
):
    return m2m_store.list_credentials()
