from fastapi import APIRouter, HTTPException, Query, Depends

from api.dependencies import manager
from simulation_output import SimulationOutput
from api.schemas.state import DashboardResponse
from api.schemas.simulation import (
    RealtimeStartRequest,
    RealtimeStatusResponse,
    RealtimeControlResponse,
)
from api.auth import (
    AuthenticatedUser,
    Role,
    Permission,
    require_permission,
    require_any_role,
)
from api.realtime.broadcaster import broadcaster
from api.realtime.models import EventType


# ==============================================================
# ROUTER
# ==============================================================

router = APIRouter()


# ==============================================================
# POST /simulation/tick
# ==============================================================

@router.post(
    "/tick",
    response_model=DashboardResponse,
    summary="Advance simulation time manually",
    description=(
        "Advance the simulation clock by N minutes (default 1). "
        "Only available when real-time simulation mode is STOPPED. "
        "Returns 409 Conflict if real-time mode is currently running."
    ),
)
def simulation_tick(
    minutes: int = Query(
        default=1,
        ge=1,
        le=60,
        description="Number of minutes to advance.",
    ),
    user: AuthenticatedUser = Depends(require_any_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
):

    if manager.is_realtime_running:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot manually tick while real-time simulation "
                "is running. Stop real-time mode first."
            ),
        )

    sim = manager.simulator
    lock = manager.lock

    with lock:

        sim.advance_time(minutes)
        sim.process_events()
        sim.check_redirections()

        data = SimulationOutput.dashboard_snapshot(
            sim.state
        )
        cur_time = int(sim.state.current_time)
        fleet_counts = data.get("fleet", {})
        active_inc_count = len(data.get("active_incidents", []))
        moving_ambs = [
            {
                "ambulance_id": str(a.ambulance_id),
                "latitude": round(float(a.latitude), 6),
                "longitude": round(float(a.longitude), 6),
                "status": str(a.status),
                "eta_minutes": round(float(a.eta_minutes), 2) if a.eta_minutes is not None else None,
                "hospital_id": getattr(a, "hospital_id", None),
                "route_waypoints": [list(wp) for wp in (getattr(a, "route_waypoints", None) or [])],
            }
            for a in sim.state.ambulances.values()
            if a.status in ("EN_ROUTE", "ARRIVED") or getattr(a, "is_repositioning", False)
        ]
        active_incidents_data = [
            {
                "incident_id": int(inc.incident_id),
                "priority": int(inc.priority),
                "severity": str(inc.severity),
                "status": str(inc.status),
                "ambulance_id": inc.ambulance_id,
                "hospital_id": inc.hospital_id,
                "eta_minutes": (
                    round(float(sim.state.ambulances[inc.ambulance_id].eta_minutes), 2)
                    if (inc.ambulance_id in sim.state.ambulances and sim.state.ambulances[inc.ambulance_id].eta_minutes is not None)
                    else None
                ),
            }
            for inc in sim.state.get_active_incidents()
        ]
        tick_payload = {
            "current_time": cur_time,
            "status": "STOPPED",
            "speed_multiplier": 60.0,
            "ticks_processed": 1,
            "fleet": fleet_counts,
            "active_incidents_count": active_inc_count,
            "active_incidents": active_incidents_data,
            "moving_ambulances": moving_ambs,
        }

    try:
        broadcaster.broadcast(EventType.TICK, tick_payload, cur_time)
    except Exception:
        pass

    return data


# ==============================================================
# POST /simulation/reset
# ==============================================================

@router.post(
    "/reset",
    summary="Reset simulation",
    description=(
        "Safely stops any active real-time simulation thread, "
        "destroys the current Simulator, and creates a fresh "
        "instance with clean world state at time = 0. "
        "Strictly restricted to Administrators."
    ),
)
def simulation_reset(
    user: AuthenticatedUser = Depends(require_permission(Permission.RESET_SIMULATION)),
):

    manager.reset()

    return {
        "status": "reset",
        "time": 0,
    }


# ==============================================================
# POST /simulation/realtime/start
# ==============================================================

@router.post(
    "/realtime/start",
    response_model=RealtimeControlResponse,
    summary="Start real-time background simulation",
    description=(
        "Starts the background wall-clock simulation loop. "
        "Advances the simulation by minutes_per_tick every "
        "tick_interval_seconds. Returns 409 Conflict if already running."
    ),
)
def simulation_realtime_start(
    request: RealtimeStartRequest = RealtimeStartRequest(),
    user: AuthenticatedUser = Depends(require_any_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
):

    try:

        result = manager.start_realtime(
            tick_interval_seconds=request.tick_interval_seconds,
            minutes_per_tick=request.minutes_per_tick,
        )

        return result

    except RuntimeError as error:

        raise HTTPException(
            status_code=409,
            detail=str(error),
        )


# ==============================================================
# POST /simulation/realtime/stop
# ==============================================================

@router.post(
    "/realtime/stop",
    response_model=RealtimeControlResponse,
    summary="Stop real-time background simulation",
    description=(
        "Stops the background real-time simulation loop cleanly. "
        "Waits for the background thread to finish its active tick. "
        "Idempotent: safe to call if already stopped."
    ),
)
def simulation_realtime_stop(
    user: AuthenticatedUser = Depends(require_any_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
):

    return manager.stop_realtime()


# ==============================================================
# GET /simulation/realtime/status
# ==============================================================

@router.get(
    "/realtime/status",
    response_model=RealtimeStatusResponse,
    summary="Get real-time simulation status",
    description=(
        "Returns telemetry for the background real-time simulation "
        "including running status, tick rate, speed multiplier, "
        "processed ticks, and error state."
    ),
)
def simulation_realtime_status(
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
):
    return manager.get_realtime_status()


# ==============================================================
# POST /simulation/realtime/restart
# ==============================================================

@router.post(
    "/realtime/restart",
    response_model=RealtimeControlResponse,
    summary="Restart real-time simulation after failure",
    description=(
        "Safely recovers and restarts the real-time simulation worker "
        "if it crashed or entered an ERRORED state."
    ),
)
def simulation_realtime_restart(
    user: AuthenticatedUser = Depends(require_any_role(Role.SUPERVISOR, Role.ADMINISTRATOR)),
):
    try:
        return manager.restart_realtime()
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))
