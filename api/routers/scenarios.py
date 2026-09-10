"""
RAAH Scenario & Replay API Router (M10 Phase 1)
==============================================

Provides endpoints for scenario registration, deterministic execution,
and standalone operational replay querying and playback inspection.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional, Dict, Any
import uuid

from Dispatch.scenarios import (
    ScenarioDefinition,
    ScenarioConfig,
    ScheduledIncident,
    ScheduledMCI,
    ScheduledReposition,
    ScheduledRedirection,
    ScheduledHospitalEvent,
    ScenarioRunner,
    ReplayEngine,
    ScenarioStore,
    ReplayStore,
)
from api.auth import AuthenticatedUser, Permission, require_permission
from api.schemas.scenarios import (
    ScenarioCreateRequest,
    ScenarioResponse,
    ScenarioRunRequest,
    RunMetadataResponse,
    ReplayStateResponse,
)
from api.persistence.replay_compiler import (
    resolve_replay_artifact,
    list_operational_runs_metadata,
)

scenario_store = ScenarioStore()
replay_store = ReplayStore()


# In-memory cache of active ReplayEngine instances (replay_id -> ReplayEngine)
_replay_engines: Dict[str, ReplayEngine] = {}

router = APIRouter(tags=["Scenarios & Replay"])


# ======================================================================
# SCENARIOS API
# ======================================================================

@router.get(
    "/scenarios",
    response_model=List[ScenarioResponse],
    summary="List all available scenario definitions",
)
def list_scenarios():
    scenarios = scenario_store.list()
    out = []
    for s in scenarios:
        out.append(
            ScenarioResponse(
                scenario_id=s.scenario_id,
                name=s.name,
                description=s.description,
                config=s.config.to_dict(),
                scheduled_incidents_count=len(s.scheduled_incidents),
                scheduled_mcis_count=len(s.scheduled_mcis),
                scheduled_repositions_count=len(s.scheduled_repositions),
                scheduled_redirections_count=len(s.scheduled_redirections),
                created_at=s.created_at,
            )
        )
    return out


@router.post(
    "/scenarios",
    response_model=ScenarioResponse,
    summary="Create or update a deterministic scenario definition",
)
def create_scenario(
    req: ScenarioCreateRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.RUN_DRILLS)),
):
    sid = req.scenario_id or f"SCEN_{uuid.uuid4().hex[:6]}"
    cfg_data = req.config.model_dump() if req.config else {}
    config = ScenarioConfig.from_dict(cfg_data)

    incidents = [ScheduledIncident.from_dict(i.model_dump()) for i in (req.scheduled_incidents or [])]
    mcis = [ScheduledMCI.from_dict(m.model_dump()) for m in (req.scheduled_mcis or [])]
    repos = [ScheduledReposition.from_dict(r.model_dump()) for r in (req.scheduled_repositions or [])]
    redirs = [ScheduledRedirection.from_dict(d.model_dump()) for d in (req.scheduled_redirections or [])]
    h_events = [ScheduledHospitalEvent.from_dict(h.model_dump()) for h in (req.scheduled_hospital_events or [])]

    scenario = ScenarioDefinition(
        scenario_id=sid,
        name=req.name,
        description=req.description or "",
        config=config,
        scheduled_incidents=incidents,
        scheduled_mcis=mcis,
        scheduled_repositions=repos,
        scheduled_redirections=redirs,
        scheduled_hospital_events=h_events,
        metadata=req.metadata or {},
    )

    scenario_store.save(scenario)

    return ScenarioResponse(
        scenario_id=scenario.scenario_id,
        name=scenario.name,
        description=scenario.description,
        config=scenario.config.to_dict(),
        scheduled_incidents_count=len(scenario.scheduled_incidents),
        scheduled_mcis_count=len(scenario.scheduled_mcis),
        scheduled_repositions_count=len(scenario.scheduled_repositions),
        scheduled_redirections_count=len(scenario.scheduled_redirections),
        created_at=scenario.created_at,
    )


@router.get(
    "/scenarios/{scenario_id}",
    summary="Get complete details of a scenario definition",
)
def get_scenario(scenario_id: str):
    scen = scenario_store.get(scenario_id)
    if not scen:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")
    return scen.to_dict()


@router.post(
    "/scenarios/{scenario_id}/run",
    response_model=RunMetadataResponse,
    summary="Execute a deterministic scenario and persist replay archive",
)
def run_scenario(
    scenario_id: str,
    req: Optional[ScenarioRunRequest] = None,
    user: AuthenticatedUser = Depends(require_permission(Permission.RUN_DRILLS)),
):
    scen = scenario_store.get(scenario_id)
    if not scen:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    # Apply execution overrides if provided
    if req:
        if req.override_seed is not None:
            scen.config.deterministic_seed = req.override_seed
        if req.duration_minutes is not None:
            scen.config.duration_minutes = req.duration_minutes

    runner = ScenarioRunner()
    run_id = req.run_id if req and req.run_id else None

    replay_artifact = runner.run(scen, run_id=run_id)
    replay_store.save(replay_artifact)

    # Initialize ReplayEngine in memory cache
    _replay_engines[replay_artifact.run_metadata.run_id] = ReplayEngine(replay_artifact)

    return RunMetadataResponse(**replay_artifact.run_metadata.to_dict())


# ======================================================================
# REPLAYS API
# ======================================================================

@router.get(
    "/replays",
    response_model=List[RunMetadataResponse],
    summary="List all recorded replay runs",
)
def list_replays():
    scenario_metas = replay_store.list_metadata()
    operational_metas = list_operational_runs_metadata()
    all_metas = list(scenario_metas) + list(operational_metas)
    return [RunMetadataResponse(**m.to_dict()) for m in all_metas]


@router.get(
    "/replays/{replay_id}",
    summary="Get metadata and final summary for a replay archive",
)
def get_replay_summary(replay_id: str):
    rep = resolve_replay_artifact(replay_id, not_found_msg=f"Replay '{replay_id}' not found.")
    return {
        "replay_format_version": rep.replay_format_version,
        "run_metadata": rep.run_metadata.to_dict(),
        "final_summary": rep.final_summary,
        "total_events": len(rep.events),
        "total_snapshots": len(rep.snapshots),
    }


def _get_or_load_replay_engine(replay_id: str) -> ReplayEngine:
    if replay_id in _replay_engines:
        return _replay_engines[replay_id]
    rep = resolve_replay_artifact(replay_id, not_found_msg=f"Replay '{replay_id}' not found.")
    engine = ReplayEngine(rep)
    _replay_engines[replay_id] = engine
    return engine



@router.get(
    "/replays/{replay_id}/state",
    response_model=ReplayStateResponse,
    summary="Get reconstructed operational state at current or sought simulation time",
)
def get_replay_state(
    replay_id: str,
    sim_time: Optional[int] = Query(None, description="Optional simulation minute to seek to"),
):
    engine = _get_or_load_replay_engine(replay_id)
    if sim_time is not None:
        engine.seek(sim_time)
    return ReplayStateResponse(**engine.get_state())


@router.post(
    "/replays/{replay_id}/step",
    response_model=ReplayStateResponse,
    summary="Advance replay by one event",
)
def step_replay(replay_id: str):
    engine = _get_or_load_replay_engine(replay_id)
    engine.step()
    return ReplayStateResponse(**engine.get_state())


@router.get(
    "/replays/{replay_id}/events",
    summary="Get ordered event stream for a replay",
)
def get_replay_events(
    replay_id: str,
    processed_only: bool = Query(False, description="Return only processed events so far"),
):
    engine = _get_or_load_replay_engine(replay_id)
    return engine.get_events(processed_only=processed_only)
