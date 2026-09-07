from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

from api.dependencies import manager
from redirection_engine import check_live_redirection
from api.auth import AuthenticatedUser, Permission, require_permission
from api.realtime.broadcaster import broadcaster
from api.realtime.models import EventType
from api.decision_evidence import evidence_store
from api.schemas.redirection import (
    RedirectionResult,
    DecisionRecord,
    ManualRedirectionRequest,
)


# ==============================================================
# ROUTER
# ==============================================================

router = APIRouter()


# ==============================================================
# POST /redirect/check/{incident_id}
# ==============================================================

@router.post(
    "/check/{incident_id}",
    response_model=RedirectionResult,
    summary="Check redirection for an incident",
    description=(
        "Evaluate whether a dispatched incident "
        "should be redirected to a different hospital. "
        "This is a read-only evaluation — it does NOT "
        "apply the redirection."
    ),
)
def check_redirection(incident_id: int):

    sim = manager.simulator
    lock = manager.lock

    with lock:

        incident = sim.state.incidents.get(
            incident_id
        )

        if incident is None:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Incident {incident_id} "
                    f"not found in state."
                ),
            )

        try:

            result = check_live_redirection(
                sim.state,
                incident_id,
            )

        except ValueError as error:

            raise HTTPException(
                status_code=404,
                detail=str(error),
            )

        except Exception as error:

            raise HTTPException(
                status_code=500,
                detail=(
                    f"Redirection engine error: "
                    f"{error}"
                ),
            )

    return result


# ==============================================================
# POST /redirect/apply/{incident_id}
# ==============================================================

@router.post(
    "/apply/{incident_id}",
    response_model=DecisionRecord,
    summary="Manually apply a hospital redirection",
    description=(
        "Manually redirect an en-route incident's ambulance to a new hospital. "
        "Mutates live state, updates the vehicle destination, adjusts hospital loads, "
        "and logs an [OPERATOR] tagged decision record."
    ),
)
def apply_redirection(
    incident_id: int,
    request: Optional[ManualRedirectionRequest] = None,
    user: AuthenticatedUser = Depends(require_permission(Permission.MANUAL_REROUTE)),
):
    sim = manager.simulator
    lock = manager.lock

    target_hosp = request.target_hospital_id if request else None
    reason = request.reason if request and request.reason else f"Operator manual override by {user.username} ({user.role.value})"

    with lock:
        try:
            decision = sim.apply_manual_redirection(
                incident_id=incident_id,
                target_hospital_id=target_hosp,
                reason=reason,
            )
        except ValueError as error:
            err_msg = str(error)
            if "not found in state" in err_msg:
                raise HTTPException(status_code=404, detail=err_msg)
            elif (
                "must be EN_ROUTE" in err_msg
                or "already en route" in err_msg
                or "has no assigned ambulance" in err_msg
            ):
                raise HTTPException(status_code=400, detail=err_msg)
            elif "full" in err_msg or "no available" in err_msg or "identical" in err_msg:
                raise HTTPException(status_code=409, detail=err_msg)
            else:
                raise HTTPException(status_code=400, detail=err_msg)
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Manual redirection failed: {error}",
            )

        payload = asdict(decision)

    try:
        broadcaster.broadcast(
            EventType.REDIRECTION_EXECUTED,
            payload,
            sim.state.current_time,
        )
    except Exception:
        pass

    try:
        evidence_store.record_redirection(
            decision_payload=payload,
            sim_time=sim.state.current_time,
        )
    except Exception:
        pass

    return DecisionRecord(**payload)


# ==============================================================
# GET /redirect/decisions
# ==============================================================

@router.get(
    "/decisions",
    response_model=list[DecisionRecord],
    summary="All redirection decisions",
    description=(
        "Returns every redirection decision logged "
        "during this simulation session."
    ),
)
def get_decisions():

    sim = manager.simulator
    lock = manager.lock

    with lock:

        decisions = sim.logger.get_decisions()

        results = [
            DecisionRecord(**asdict(decision))
            for decision in decisions
        ]

    return results


# ==============================================================
# GET /redirect/decisions/{incident_id}
# ==============================================================

@router.get(
    "/decisions/{incident_id}",
    response_model=list[DecisionRecord],
    summary="Decisions for a specific incident",
    description=(
        "Returns all redirection decisions logged "
        "for a specific incident."
    ),
)
def get_incident_decisions(incident_id: int):

    sim = manager.simulator
    lock = manager.lock

    with lock:

        decisions = sim.logger.get_incident_history(
            incident_id
        )

        results = [
            DecisionRecord(**asdict(decision))
            for decision in decisions
        ]

    return results
