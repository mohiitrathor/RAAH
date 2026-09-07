from fastapi import APIRouter, HTTPException, Depends

from api.dependencies import manager
from api.schemas.dispatch import DispatchResult, CustomIncidentRequest
from api.auth import AuthenticatedUser, Permission, require_permission
from api.realtime.broadcaster import broadcaster
from api.realtime.models import EventType
from api.decision_evidence import evidence_store


# ==============================================================
# ROUTER
# ==============================================================

router = APIRouter()


# ==============================================================
# POST /dispatch/live
# ==============================================================

@router.post(
    "/live",
    response_model=DispatchResult,
    summary="Dispatch a live emergency call",
    description=(
        "Triage and dispatch a dynamic emergency call intake. "
        "Validates against the full 24-feature ML contract, "
        "selects available ambulance and suitable hospital using "
        "live authoritative state, and mutates DispatchState."
    ),
)
def dispatch_live_emergency(
    request: CustomIncidentRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.INGEST_EMERGENCY)),
):

    sim = manager.simulator
    lock = manager.lock

    with lock:

        try:

            result = sim.create_custom_incident(
                request.model_dump()
            )

        except Exception as error:

            raise HTTPException(
                status_code=500,
                detail=f"Live dispatch error: {error}",
            )

    try:
        broadcaster.broadcast(
            EventType.INCIDENT_DISPATCHED,
            result,
            sim.state.current_time,
        )
    except Exception:
        pass

    try:
        evidence_store.record_dispatch(
            dispatch_result=result,
            sim_time=sim.state.current_time,
        )
    except Exception:
        pass

    return result


# ==============================================================
# POST /dispatch/{incident_id}
# ==============================================================

@router.post(
    "/{incident_id}",
    response_model=DispatchResult,
    summary="Dispatch an incident",
    description=(
        "Run the full dispatch pipeline for an incident: "
        "ML severity prediction, ambulance selection, "
        "hospital selection, and state mutation."
    ),
)
def dispatch_incident(
    incident_id: int,
    user: AuthenticatedUser = Depends(require_permission(Permission.STANDARD_DISPATCH)),
):

    sim = manager.simulator
    lock = manager.lock

    with lock:

        try:

            result = sim.create_incident(
                incident_id
            )

        except ValueError as error:

            raise HTTPException(
                status_code=404,
                detail=str(error),
            )

        except Exception as error:

            raise HTTPException(
                status_code=500,
                detail=f"Dispatch engine error: {error}",
            )

    try:
        broadcaster.broadcast(
            EventType.INCIDENT_DISPATCHED,
            result,
            sim.state.current_time,
        )
    except Exception:
        pass

    try:
        evidence_store.record_dispatch(
            dispatch_result=result,
            sim_time=sim.state.current_time,
        )
    except Exception:
        pass

    return result
