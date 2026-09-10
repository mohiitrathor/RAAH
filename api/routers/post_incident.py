"""
RAAH Post-Incident Review & Continuous Regression API Endpoints (M10 Phase 4)
=============================================================================
"""

from typing import Dict, List, Any
from fastapi import APIRouter, HTTPException, Depends

from Dispatch.scenarios.store import ReplayStore
from Dispatch.scenarios.models import ReplayArtifact
from Dispatch.scenarios.post_incident import PostIncidentReviewEngine
from api.persistence.replay_compiler import resolve_replay_artifact
from Dispatch.scenarios.regression import (
    RegressionSuite,
    RegressionStore,
    RegressionTolerances,
)
from api.schemas.post_incident import (
    PostIncidentReviewResponse,
    PIRReportRequest,
    PIRReportResponse,
    PIRCompareRequest,
    PIRCompareResponse,
    CreateBaselineRequest,
    RegressionRunRequest,
    RegressionReportResponse,
)

router = APIRouter(tags=["Post-Incident Review & Regression"])
replay_store = ReplayStore()
regression_store = RegressionStore()
regression_suite = RegressionSuite(store=regression_store)


def _get_artifact(run_id: str) -> ReplayArtifact:
    """Resolve replay artifact for either scenario runs or operational runs."""
    return resolve_replay_artifact(run_id, not_found_msg=f"Replay run '{run_id}' not found.")


# --------------------------------------------------------------------------
# Post-Incident Review (PIR) Endpoints
# --------------------------------------------------------------------------

@router.get("/replays/{run_id}/pir", response_model=PostIncidentReviewResponse)
def get_post_incident_review(run_id: str):
    """Retrieve full Post-Incident Review for a replay run."""
    artifact = _get_artifact(run_id)
    pir = PostIncidentReviewEngine.generate_review(artifact)
    return pir.to_dict()


@router.get("/replays/{run_id}/findings")
def get_pir_findings(run_id: str):
    """Retrieve operational findings identified for a replay run."""
    artifact = _get_artifact(run_id)
    pir = PostIncidentReviewEngine.generate_review(artifact)
    return {
        "run_id": run_id,
        "scenario_id": artifact.run_metadata.scenario_id,
        "total_findings": len(pir.findings),
        "findings": [f.to_dict() for f in pir.findings],
    }


@router.get("/replays/{run_id}/root-causes")
def get_pir_root_causes(run_id: str):
    """Retrieve causal graph and cascading failure chains for a replay run."""
    artifact = _get_artifact(run_id)
    pir = PostIncidentReviewEngine.generate_review(artifact)
    return {
        "run_id": run_id,
        "scenario_id": artifact.run_metadata.scenario_id,
        "root_cause_graph": pir.root_cause_graph.to_dict(),
        "cascading_failures": pir.cascading_failures,
    }


@router.post("/replays/{run_id}/pir/report", response_model=PIRReportResponse)
def export_pir_report(run_id: str, req: PIRReportRequest):
    """Export PIR report in JSON, Markdown, or HTML format."""
    artifact = _get_artifact(run_id)
    pir = PostIncidentReviewEngine.generate_review(artifact)
    report = PostIncidentReviewEngine.export_report(pir, format=req.format)
    return PIRReportResponse(
        run_id=run_id,
        scenario_id=artifact.run_metadata.scenario_id,
        format=report["format"],
        content=report["content"],
    )


@router.post("/replays/pir/compare", response_model=PIRCompareResponse)
def compare_post_incident_reviews(req: PIRCompareRequest):
    """Compare two PIR evaluations to evaluate performance regressions or improvements."""
    art_a = _get_artifact(req.run_id_a)
    art_b = _get_artifact(req.run_id_b)

    pir_a = PostIncidentReviewEngine.generate_review(art_a)
    pir_b = PostIncidentReviewEngine.generate_review(art_b)

    comp = PostIncidentReviewEngine.compare_pir(pir_a, pir_b)
    return comp


# --------------------------------------------------------------------------
# Continuous Regression & Baseline Endpoints
# --------------------------------------------------------------------------

@router.get("/regression/baseline")
def get_regression_baseline():
    """Retrieve the official established regression baseline."""
    baseline = regression_store.get_baseline()
    if not baseline:
        raise HTTPException(status_code=404, detail="No official regression baseline established yet.")
    return baseline


from api.auth import AuthenticatedUser, Permission, require_permission


@router.post("/regression/baseline/create")
def create_regression_baseline(
    req: CreateBaselineRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.RUN_DRILLS)),
):
    """Explicitly establish or update the official regression baseline."""
    baseline = regression_suite.create_baseline(description=req.description)
    return {
        "status": "BASELINE_CREATED",
        "version": baseline.get("version"),
        "created_at": baseline.get("created_at"),
        "case_count": len(baseline.get("cases", {})),
        "baseline": baseline,
    }


@router.post("/regression/run", response_model=RegressionReportResponse)
def run_regression_suite(
    req: RegressionRunRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.RUN_DRILLS)),
):
    """Run all standard regression drill cases sequentially against established baseline."""
    report = regression_suite.run_suite(run_id=req.run_id)
    return report.to_dict()


@router.get("/regression/results")
def list_regression_results():
    """List historical regression test runs."""
    return regression_store.list_runs()


@router.get("/regression/results/{run_id}", response_model=RegressionReportResponse)
def get_regression_result(run_id: str):
    """Retrieve a specific historical regression run report."""
    run = regression_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Regression run '{run_id}' not found.")
    return run
