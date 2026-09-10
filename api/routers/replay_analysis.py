"""
RAAH Replay Analysis & Operational Workstation API Router (M10 Phase 3)
========================================================================

Endpoints for timeline browsing, event inspection, scenario comparisons,
before/after telemetry analysis, and report generation.
Completely observational; operates exclusively on recorded archives and
never touches the live Simulator.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, Any, List

from Dispatch.scenarios.store import ReplayStore
from Dispatch.scenarios.analysis import (
    ReplayAnalyzer,
    ReplaySessionManager,
)
from Dispatch.scenarios.models import ReplayArtifact
from api.persistence.replay_compiler import resolve_replay_artifact
from api.schemas.replay_analysis import (
    ReplayTimelineResponse,
    ReplayEventSummaryResponse,
    ReplayAnalysisResponse,
    ScenarioComparisonRequest,
    ScenarioComparisonResponse,
    BeforeAfterRequest,
    BeforeAfterResponse,
    DrillReportRequest,
    DrillReportResponse,
    ReplayModeRequest,
)
from api.schemas.scenarios import ReplayStateResponse

router = APIRouter(prefix="/replays", tags=["Operational Replay & Scenario Analysis"])

replay_store = ReplayStore()
session_manager = ReplaySessionManager()

# Lightweight in-memory session mode registry
_session_modes: Dict[str, str] = {}


def _get_artifact(run_id: str) -> ReplayArtifact:
    return resolve_replay_artifact(run_id, not_found_msg=f"Replay archive '{run_id}' not found.")



@router.get(
    "/{run_id}/timeline",
    response_model=ReplayTimelineResponse,
    summary="Get chronologically sorted timeline with optional event type or entity filtering",
)
def get_timeline(
    run_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event type (e.g. DISPATCH, MCI_DECLARED)"),
    entity_id: Optional[str] = Query(None, description="Filter by entity ID (ambulance, incident, hospital, MCI)"),
):
    artifact = _get_artifact(run_id)
    timeline = ReplayAnalyzer.build_timeline(artifact, event_type=event_type, entity_id=entity_id)
    return ReplayTimelineResponse(**timeline.to_dict())


@router.get(
    "/{run_id}/events/{event_index}",
    response_model=ReplayEventSummaryResponse,
    summary="Get deep inspector details for an operational event by index",
)
def get_event_detail(run_id: str, event_index: int):
    artifact = _get_artifact(run_id)
    detail = ReplayAnalyzer.get_event_detail(artifact, event_index)
    if not detail:
        raise HTTPException(
            status_code=404,
            detail=f"Event index {event_index} not found in replay '{run_id}'.",
        )
    return ReplayEventSummaryResponse(**detail)


@router.get(
    "/{run_id}/analysis",
    response_model=ReplayAnalysisResponse,
    summary="Get comprehensive operational analysis and resilience scorecard for a replay",
)
def get_replay_analysis(run_id: str):
    artifact = _get_artifact(run_id)
    analysis = ReplayAnalyzer.analyze(artifact)
    return ReplayAnalysisResponse(**analysis.to_dict())


@router.get(
    "/{run_id}/state/{sim_time}",
    response_model=ReplayStateResponse,
    summary="Seek to simulation minute and get reconstructed operational state",
)
def seek_replay_state(
    run_id: str,
    sim_time: int,
    session_id: Optional[str] = Query("default", description="Independent replay session ID"),
):
    artifact = _get_artifact(run_id)
    engine = session_manager.get_or_create(f"{session_id}_{run_id}", artifact)
    engine.seek(sim_time)
    return ReplayStateResponse(**engine.get_state())


@router.post(
    "/{run_id}/before-after",
    response_model=BeforeAfterResponse,
    summary="Compare reconstructed operational state between two simulation timestamps",
)
def compare_before_after(run_id: str, req: BeforeAfterRequest):
    artifact = _get_artifact(run_id)
    result = ReplayAnalyzer.compare_snapshots_before_after(
        artifact, time_a=req.time_a, time_b=req.time_b
    )
    return BeforeAfterResponse(**result)


@router.post(
    "/{run_id}/report",
    response_model=DrillReportResponse,
    summary="Generate a structured drill report (JSON or Markdown)",
)
def generate_drill_report(run_id: str, req: Optional[DrillReportRequest] = None):
    artifact = _get_artifact(run_id)
    fmt = req.format if req and req.format else "json"
    report = ReplayAnalyzer.generate_report(artifact, format=fmt)
    return DrillReportResponse(**report)


@router.post(
    "/compare",
    response_model=ScenarioComparisonResponse,
    summary="Compare two recorded scenario runs (Scenario A vs Scenario B)",
)
def compare_scenarios(req: ScenarioComparisonRequest):
    artifact_a = _get_artifact(req.run_id_a)
    artifact_b = _get_artifact(req.run_id_b)
    comp = ReplayAnalyzer.compare_scenarios(artifact_a, artifact_b)
    return ScenarioComparisonResponse(**comp)


@router.post(
    "/{run_id}/mode",
    summary="Toggle replay session mode (affects frontend/replay session state only)",
)
def set_replay_mode(run_id: str, req: ReplayModeRequest):
    _session_modes[req.session_id or "default"] = req.mode
    return {
        "status": "ok",
        "run_id": run_id,
        "session_id": req.session_id or "default",
        "mode": req.mode,
    }
