"""
RAAH Optimization API Router (M11 Phase 2)
==========================================

Operator Copilot & Interactive Decision Execution endpoints.
Produces operational snapshots, ranked recommendations, isolated what-if simulations,
operator approval/rejection workflows, authoritative executions, and audit records.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends

from api.dependencies import manager
from api.auth import (
    AuthenticatedUser,
    Role,
    Permission,
    get_current_user,
    require_permission,
    require_any_role,
)
from api.schemas.optimization import (
    OperationalSnapshotResponse,
    OptimizationRecommendationResponse,
    SimulationImpactSchema,
    SimulateRecommendationRequest,
    OptimizationHealthResponse,
    ApproveRecommendationRequest,
    RejectRecommendationRequest,
    ExecutionResultResponse,
    ExecutionAuditRecordResponse,
    CopilotSummaryResponse,
)
from Dispatch.optimization.decision_engine import DecisionEngine
from api.decision_evidence import evidence_store

router = APIRouter(
    prefix="/optimization",
    tags=["Optimization"],
)

# Persistent optimization engine instance
decision_engine = DecisionEngine()


@router.get(
    "/snapshot",
    response_model=OperationalSnapshotResponse,
    summary="Get operational state snapshot",
)
def get_snapshot():
    """
    Capture an observational snapshot of the live simulator state including
    fleet distribution, incident queues, hospital projected capacities, and MCIs.
    """
    with manager.lock:
        sim = manager.simulator
        snapshot = decision_engine.get_snapshot(sim)
        return snapshot.to_dict()


@router.get(
    "/recommendations",
    response_model=List[OptimizationRecommendationResponse],
    summary="Get ranked optimization recommendations",
)
def get_recommendations():
    """
    Evaluate the current simulator state and return a ranked list of
    explainable candidate actions (repositioning and hospital diversion).
    """
    with manager.lock:
        sim = manager.simulator
        recs = decision_engine.evaluate_state(sim)
        return [r.to_dict() for r in recs]


@router.get(
    "/recommendations/{recommendation_id}",
    response_model=OptimizationRecommendationResponse,
    summary="Get details of a specific recommendation",
)
def get_recommendation(recommendation_id: str):
    """
    Retrieve full details, score breakdown, and constraints of a generated recommendation.
    """
    rec = decision_engine.get_recommendation(recommendation_id)
    if not rec:
        # Check if active by evaluating state
        with manager.lock:
            sim = manager.simulator
            decision_engine.evaluate_state(sim)
            rec = decision_engine.get_recommendation(recommendation_id)

    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"Optimization recommendation '{recommendation_id}' not found.",
        )
    return rec.to_dict()


@router.post(
    "/simulate",
    response_model=SimulationImpactSchema,
    summary="Simulate what-if outcome of a recommendation",
)
def simulate_recommendation(body: SimulateRecommendationRequest):
    """
    Execute an isolated what-if simulation for a proposed recommendation.
    Does NOT execute the decision on live state.
    """
    with manager.lock:
        sim = manager.simulator
        impact = decision_engine.simulate_recommendation(body.recommendation_id, sim)

    if not impact:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot simulate unknown recommendation '{body.recommendation_id}'.",
        )
    return impact.to_dict()


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=ExecutionResultResponse,
    summary="Approve and authoritatively execute an optimization recommendation",
)
def approve_recommendation(
    recommendation_id: str,
    body: Optional[ApproveRecommendationRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Operator approval endpoint. Revalidates current state, rechecks hard safety
    constraints, and authoritatively executes the action through Simulator coordination.
    """
    req = body or ApproveRecommendationRequest()
    rec = decision_engine.get_recommendation(recommendation_id)
    if not rec:
        with manager.lock:
            sim = manager.simulator
            decision_engine.evaluate_state(sim)
            rec = decision_engine.get_recommendation(recommendation_id)

    if rec:
        if rec.decision_type == "FLEET_REPOSITION":
            if not user.has_permission(Permission.APPROVE_FLEET_REPOSITION):
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: FLEET_REPOSITION requires APPROVE_FLEET_REPOSITION permission ({user.role.value} not authorized).",
                )
        elif rec.decision_type == "HOSPITAL_DIVERSION":
            if not user.has_permission(Permission.APPROVE_HOSPITAL_DIVERSION):
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: HOSPITAL_DIVERSION requires APPROVE_HOSPITAL_DIVERSION permission ({user.role.value} not authorized).",
                )
    else:
        if not (user.has_permission(Permission.APPROVE_FLEET_REPOSITION) or user.has_permission(Permission.APPROVE_HOSPITAL_DIVERSION)):
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Insufficient privileges to approve recommendations ({user.role.value} not authorized).",
            )

    op_id = req.operator_id or f"{user.username} ({user.role.value})"

    with manager.lock:
        sim = manager.simulator
        res = decision_engine.approve_recommendation(
            rec_id=recommendation_id,
            sim_instance=sim,
            operator_id=op_id,
            operator_note=req.operator_note,
        )

    if res.status == "FAILED" and "not found" in (res.error_message or "").lower():
        raise HTTPException(
            status_code=404,
            detail=res.error_message,
        )

    try:
        mode = getattr(getattr(decision_engine, "policy_engine", None), "config", None)
        mode_val = mode.mode if mode else None
        evidence_store.record_optimization(
            recommendation=rec,
            execution_result=res,
            sim_time=sim.state.current_time,
            policy_mode=mode_val,
        )
    except Exception:
        pass

    return res.to_dict()


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=OptimizationRecommendationResponse,
    summary="Reject an advisory optimization recommendation",
)
def reject_recommendation(
    recommendation_id: str,
    body: Optional[RejectRecommendationRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Explicitly dismiss an advisory recommendation with operator audit notes.
    """
    req = body or RejectRecommendationRequest()
    op_id = req.operator_id or f"{user.username} ({user.role.value})"

    with manager.lock:
        rec = decision_engine.reject_recommendation(
            rec_id=recommendation_id,
            operator_id=op_id,
            reason=req.reason,
        )

    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"Optimization recommendation '{recommendation_id}' not found.",
        )
    return rec.to_dict()


@router.get(
    "/executions",
    response_model=List[ExecutionAuditRecordResponse],
    summary="List recent execution audit records",
)
def get_executions(limit: int = 50):
    """
    Retrieve persistent execution audit records in reverse chronological order.
    """
    records = decision_engine.audit_store.get_executions(limit=limit)
    return records


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionAuditRecordResponse,
    summary="Get execution audit record by ID",
)
def get_execution(execution_id: str):
    """
    Retrieve details of an authoritative execution audit record.
    """
    record = decision_engine.audit_store.get_execution(execution_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"Execution audit record '{execution_id}' not found.",
        )
    return record


@router.get(
    "/copilot/summary",
    response_model=CopilotSummaryResponse,
    summary="Get real-time Operator Copilot summary and alerts",
)
def get_copilot_summary():
    """
    Retrieve real-time copilot overview, top actionable recommendation,
    pending/stale counters, and latest execution result.
    """
    with manager.lock:
        sim = manager.simulator
        summary = decision_engine.get_copilot_summary(sim)
        return summary


@router.get(
    "/health",
    response_model=OptimizationHealthResponse,
    summary="Check optimization layer operational health",
)
def get_health():
    """
    Report the operational status and safety mode of the optimization intelligence engine.
    """
    last_sim = decision_engine._last_snapshot.sim_time if decision_engine._last_snapshot else 0
    active_count = len(decision_engine._recommendations_index)
    mode = decision_engine.policy_engine.config.mode
    is_auto = (mode in ("GUARDED", "FULL") and not decision_engine.policy_engine.config.kill_switch_active)

    return {
        "status": "OPERATIONAL",
        "mode": "READ_ONLY_RECOMMENDATION_ONLY",
        "active_recommendations_count": active_count,
        "last_evaluated_sim_time": last_sim,
        "autonomous_execution_enabled": False,
    }


# ----------------------------------------------------------------------
# M11 PHASE 3: ADAPTIVE POLICY & BOUNDED AUTONOMY ROUTES
# ----------------------------------------------------------------------

from api.schemas.optimization import (
    PolicyConfigResponse,
    ChangePolicyModeRequest,
    ChangePolicyModeResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    PolicyEvaluationResponse,
    EvaluatePolicyRequest,
    RollbackRequest,
    PolicyPerformanceResponse,
)


@router.get(
    "/policy",
    summary="Get overview of the Adaptive Policy Engine",
)
def get_policy_overview():
    """
    Retrieve current policy engine state, active mode, kill switch, and metrics.
    """
    cfg = decision_engine.policy_engine.config
    perf = decision_engine.policy_engine.get_performance()
    return {
        "mode": cfg.mode,
        "kill_switch_active": cfg.kill_switch_active,
        "version": cfg.version,
        "performance": perf.to_dict(),
        "config": cfg.to_dict(),
    }


@router.get(
    "/policy/config",
    response_model=PolicyConfigResponse,
    summary="Get Adaptive Policy Configuration",
)
def get_policy_config():
    """
    Retrieve configurable guardrails, thresholds, cooldowns, and safety floors.
    """
    return decision_engine.policy_engine.config.to_dict()


@router.post(
    "/policy/mode",
    response_model=ChangePolicyModeResponse,
    summary="Change policy autonomy mode",
)
def set_policy_mode(
    body: ChangePolicyModeRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.CHANGE_POLICY_MODE)),
):
    """
    Operator-initiated transition between OFF, ADVISORY, and GUARDED modes.
    Restricted to Supervisors and Administrators.
    """
    op_id = body.operator_id or f"{user.username} ({user.role.value})"
    try:
        res = decision_engine.policy_engine.set_mode(
            new_mode=body.mode,
            operator_id=op_id,
            reason=body.reason,
        )
        return res
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post(
    "/policy/kill-switch",
    response_model=KillSwitchResponse,
    summary="Engage or release emergency kill-switch",
)
def toggle_kill_switch(
    body: KillSwitchRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.KILL_SWITCH)),
):
    """
    Emergency kill-switch that halts all autonomous executions immediately.
    Restricted to Supervisors, Medical Controllers, and Administrators.
    """
    op_id = body.operator_id or f"{user.username} ({user.role.value})"
    if body.action.upper() == "ENGAGE":
        return decision_engine.policy_engine.activate_kill_switch(
            operator_id=op_id,
            reason=body.reason,
        )
    else:
        return decision_engine.policy_engine.deactivate_kill_switch(
            operator_id=op_id,
            reason=body.reason,
        )


@router.get(
    "/policy/performance",
    response_model=PolicyPerformanceResponse,
    summary="Get policy performance and telemetry",
)
def get_policy_performance():
    """
    Retrieve counts of attempted, executed, blocked actions, operator approvals,
    and predicted vs actual benefits.
    """
    return decision_engine.policy_engine.get_performance().to_dict()


@router.get(
    "/policy/decisions",
    response_model=List[PolicyEvaluationResponse],
    summary="Get recent policy evaluations",
)
def get_policy_decisions(limit: int = 50):
    """
    Retrieve history of policy evaluations and guardrail checks.
    """
    return decision_engine.policy_engine.get_decisions(limit=limit)


@router.post(
    "/policy/evaluate",
    response_model=PolicyEvaluationResponse,
    summary="Evaluate recommendation against policy",
)
def evaluate_recommendation_policy(body: EvaluatePolicyRequest):
    """
    Evaluate a specific recommendation against current policy rules and guardrails.
    """
    with manager.lock:
        sim = manager.simulator
        res = decision_engine.evaluate_policy(body.recommendation_id, sim)
    if not res:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation '{body.recommendation_id}' not found.",
        )
    return res.to_dict()


@router.post(
    "/policy/rollback/{execution_id}",
    response_model=ExecutionResultResponse,
    summary="Roll back an executed fleet repositioning",
)
def rollback_execution(execution_id: str, body: RollbackRequest):
    """
    Safely reverse a recently executed fleet repositioning action.
    """
    with manager.lock:
        sim = manager.simulator
        res = decision_engine.rollback_execution(
            execution_id=execution_id,
            sim_instance=sim,
            operator_id=body.operator_id,
            reason=body.reason,
        )
    if res.status != "SUCCESS":
        raise HTTPException(status_code=400, detail=res.error_message)
    return res.to_dict()


# ----------------------------------------------------------------------
# M11 PHASE 4: OPERATIONAL LEARNING, CALIBRATION & ADAPTATION ROUTES
# ----------------------------------------------------------------------

from api.schemas.optimization import (
    LearningReportResponse,
    ConfidenceCalibrationResponse,
    OperationalDriftResponse,
    PolicyPerformanceTrendResponse,
    LearningRecommendationResponse,
    ApproveLearningRecRequest,
    ApproveLearningRecResponse,
    RejectLearningRecRequest,
    ComparePoliciesRequest,
    ComparePoliciesResponse,
    PolicyVersionSummaryResponse,
    RollbackPolicyVersionRequest,
)


@router.get(
    "/learning",
    response_model=LearningReportResponse,
    summary="Get comprehensive Operational Learning report",
)
def get_learning_report():
    """
    Synthesize end-to-end LearningReport with safety score, confidence calibration,
    operational drift telemetry, and adaptive policy recommendations.
    """
    with manager.lock:
        sim = manager.simulator
        report = decision_engine.get_learning_report(sim)
        return report.to_dict()


@router.get(
    "/learning/performance",
    response_model=PolicyPerformanceTrendResponse,
    summary="Get longitudinal policy performance trends",
)
def get_learning_performance(
    min_sim_time: int = 0,
    max_sim_time: Optional[int] = None,
):
    """
    Retrieve historical policy execution trends and metrics across a time window.
    """
    trend = decision_engine.get_performance_trend()
    return trend.to_dict()


@router.get(
    "/learning/calibration",
    response_model=ConfidenceCalibrationResponse,
    summary="Get confidence calibration buckets and error stats",
)
def get_learning_calibration():
    """
    Analyze historical outcomes across standard confidence buckets and compute
    empirical vs. predicted calibration error.
    """
    calib = decision_engine.get_calibration()
    return calib.to_dict()


@router.get(
    "/learning/drift",
    response_model=OperationalDriftResponse,
    summary="Get operational drift indicators and severity classification",
)
def get_learning_drift():
    """
    Evaluate system-level operational drift in ETA, coverage, hospital saturation,
    and action success rates.
    """
    with manager.lock:
        sim = manager.simulator
        drift = decision_engine.get_drift(sim)
        return drift.to_dict()


@router.get(
    "/learning/recommendations",
    response_model=List[LearningRecommendationResponse],
    summary="Get pending adaptive policy recommendations",
)
def get_learning_recommendations():
    """
    Retrieve evidence-based proposals to adjust policy parameters within safe bounds.
    """
    with manager.lock:
        sim = manager.simulator
        recs = decision_engine.get_learning_recommendations(sim)
        return [r.to_dict() for r in recs]


@router.get(
    "/learning/recommendations/{recommendation_id}",
    response_model=LearningRecommendationResponse,
    summary="Get details of a specific adaptive recommendation",
)
def get_learning_recommendation(recommendation_id: str):
    """
    Retrieve specific adaptive policy recommendation by ID.
    """
    rec = decision_engine.get_learning_recommendation(recommendation_id)
    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"Adaptive recommendation '{recommendation_id}' not found.",
        )
    return rec.to_dict()


@router.post(
    "/learning/compare",
    response_model=ComparePoliciesResponse,
    summary="Perform isolated offline A/B policy comparison",
)
def compare_policies(body: ComparePoliciesRequest):
    """
    Compare Policy A vs. Policy B offline over historical outcomes or scenarios
    without mutating the live simulator.
    """
    from Dispatch.optimization.policy import PolicyConfig
    cfg_a = None
    cfg_b = None
    if body.policy_a:
        cfg_a = PolicyConfig(**body.policy_a)
    if body.policy_b:
        cfg_b = PolicyConfig(**body.policy_b)

    return decision_engine.compare_policies(cfg_a, cfg_b)


@router.post(
    "/learning/recommendations/{recommendation_id}/approve",
    response_model=ApproveLearningRecResponse,
    summary="Operator approves adaptive policy recommendation",
)
def approve_learning_recommendation(
    recommendation_id: str,
    body: ApproveLearningRecRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.APPROVE_POLICY_CHANGE)),
):
    """
    Validate safety bounds, update policy parameter, create a new immutable
    policy version, and mark recommendation APPROVED.
    Restricted to Supervisors and Administrators.
    """
    op_id = body.operator_id or f"{user.username} ({user.role.value})"
    try:
        new_cfg, rec = decision_engine.approve_learning_recommendation(
            recommendation_id=recommendation_id,
            operator_id=op_id,
        )
        return {
            "recommendation": rec.to_dict(),
            "new_policy": new_cfg.to_dict(),
        }
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.post(
    "/learning/recommendations/{recommendation_id}/reject",
    response_model=LearningRecommendationResponse,
    summary="Operator rejects adaptive policy recommendation",
)
def reject_learning_recommendation(
    recommendation_id: str,
    body: RejectLearningRecRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.APPROVE_POLICY_CHANGE)),
):
    """
    Dismiss an adaptive policy recommendation with an operator rationale.
    Restricted to Supervisors and Administrators.
    """
    op_id = body.operator_id or f"{user.username} ({user.role.value})"
    try:
        rec = decision_engine.reject_learning_recommendation(
            recommendation_id=recommendation_id,
            operator_id=op_id,
            reason=body.reason,
        )
        return rec.to_dict()
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


@router.get(
    "/policy/history",
    response_model=List[PolicyVersionSummaryResponse],
    summary="Get immutable policy version history",
)
def get_policy_history():
    """
    Retrieve chronological list of immutable policy versions with parent links.
    """
    return decision_engine.get_policy_history()


@router.post(
    "/learning/rollback/{policy_version}",
    response_model=PolicyConfigResponse,
    summary="Roll back active policy to previous version",
)
def rollback_policy_version(
    policy_version: str,
    body: RollbackPolicyVersionRequest,
    user: AuthenticatedUser = Depends(require_permission(Permission.ROLLBACK_POLICY)),
):
    """
    Roll back to a previous policy configuration by generating a NEW immutable
    version that restores the target parameters.
    Restricted to Supervisors and Administrators.
    """
    op_id = body.operator_id or f"{user.username} ({user.role.value})"
    try:
        new_cfg = decision_engine.rollback_policy(
            target_version=policy_version,
            operator_id=op_id,
            reason=body.reason,
        )
        return new_cfg.to_dict()
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


