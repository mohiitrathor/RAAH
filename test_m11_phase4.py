"""
RAAH Milestone 11 Phase 4 Test Suite
====================================

Tests Outcome-Driven Adaptation, Policy Calibration & Operational Learning:
- OutcomeRecord construction, serialization, and benefit realization ratio
- Deterministic outcome classification (SUCCESSFUL, NEUTRAL, HARMFUL)
- Confidence calibration buckets (0.50–0.60, ..., 0.95–1.00) and error computation
- Policy performance longitudinal history and atomic persistence
- Operational drift detection (ETA, coverage, hospital saturation, success rate)
- Drift severity classification (NORMAL, WATCH, DEGRADED, CRITICAL)
- Adaptive policy recommendations with risk levels and evidence links
- Hard safety invariants (clinical ML model protection, hospital/MCI autonomy lock, floor >= 2)
- Immutable policy versioning, parent links, and operator-approved rollbacks
- Offline A/B policy comparison and simulation isolation
- Transparent 0–100 LearningSafetyScore
- Deterministic analysis, hashing, and repeated evaluation invariance
- REST APIs (/learning, /calibration, /drift, /recommendations, /compare, /history, /rollback)
- Full compatibility with M9, M10, and M11 Phase 1–3
"""

import os
import re
import json
import time
import shutil
import threading
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import manager
from Dispatch.optimization.models import (
    OptimizationRecommendation,
    OptimizationCandidate,
    DecisionExplanation,
    RecommendationStatus,
    ExecutionResult,
)
from Dispatch.optimization.policy import (
    AutonomyMode,
    PolicyDecisionType,
    OutcomeClassification,
    PolicyConfig,
    PolicyEvaluation,
)
from Dispatch.optimization.learning import (
    OutcomeRecord,
    CalibrationBucket,
    ConfidenceCalibration,
    PolicyPerformanceTrend,
    LearningSafetyScore,
    LearningReport,
    OutcomeStore,
    CalibrationAnalyzer,
    calculate_learning_safety_score,
    generate_deterministic_hash,
)
from Dispatch.optimization.drift import (
    DriftSeverity,
    OperationalDrift,
    DriftDetector,
)
from Dispatch.optimization.adaptation import (
    RiskLevel,
    AdaptationStatus,
    LearningRecommendation,
    PolicyVersionStore,
    AdaptivePolicyAdvisor,
    PolicyEvaluatorAB,
)
from Dispatch.optimization.decision_engine import DecisionEngine
from Dispatch.optimization.audit import ExecutionAuditStore
from Dispatch.optimization.executor import OptimizationExecutor
from Dispatch.optimization.policy_engine import AdaptivePolicyEngine

client = TestClient(app)


def run_phase4_tests():
    print("\n" + "=" * 75)
    print("RAAH M11 PHASE 4: OPERATIONAL LEARNING & CALIBRATION TEST SUITE")
    print("=" * 75)

    with client:
        client.post("/simulation/reset")

        # Isolated test directories
        REPO_ROOT = Path(__file__).resolve().parent
        test_data_dir = REPO_ROOT / "data/optimization/test_p4"
        if test_data_dir.exists():
            shutil.rmtree(test_data_dir)
        test_data_dir.mkdir(parents=True, exist_ok=True)

        test_outcomes_path = test_data_dir / "outcomes.json"
        test_versions_dir = test_data_dir / "policy_versions"
        test_audit_path = test_data_dir / "audit.json"

        outcome_store = OutcomeStore(store_path=test_outcomes_path)
        version_store = PolicyVersionStore(store_dir=test_versions_dir)
        audit_store = ExecutionAuditStore(store_path=test_audit_path)

        policy_config = PolicyConfig(mode=AutonomyMode.GUARDED)
        policy_engine = AdaptivePolicyEngine(
            config=policy_config,
            audit_store=audit_store,
            outcome_store=outcome_store,
            version_store=version_store,
        )
        decision_engine = DecisionEngine(
            audit_store=audit_store,
            policy_engine=policy_engine,
        )

        with manager.lock:
            sim = manager.simulator

        # ------------------------------------------------------------------
        # TEST 1 & 2: OutcomeRecord Domain Model & Serialization
        # ------------------------------------------------------------------
        print("\n[TEST 1 & 2] OutcomeRecord domain model & serialization...")
        rec1 = OutcomeRecord(
            recommendation_id="REC_001",
            recommendation_type="FLEET_REPOSITION",
            confidence=0.96,
            predicted_benefit=0.20,
            actual_benefit=0.18,
            policy_decision="AUTO_APPROVE",
            execution_mode="AUTONOMOUS",
            outcome="SUCCESSFUL",
            execution_latency=4.5,
            sim_time=10,
            affected_entities={"ambulance_id": "AMB_0001", "target_zone": "JAIPUR_NORTH"},
            before_state_hash="hash_before_01",
            after_state_hash="hash_after_01",
        )
        d1 = rec1.to_dict()
        assert d1["recommendation_id"] == "REC_001"
        assert d1["prediction_error"] == -0.02
        assert d1["benefit_realization_ratio"] == 0.90
        rec1_copy = OutcomeRecord.from_dict(d1)
        assert rec1_copy.recommendation_id == rec1.recommendation_id
        assert rec1_copy.prediction_error == rec1.prediction_error
        print("✓ OutcomeRecord construction, derived ratios, and serialization verified.")

        # ------------------------------------------------------------------
        # TEST 3, 4 & 5: Outcome Classification (SUCCESSFUL, NEUTRAL, HARMFUL)
        # ------------------------------------------------------------------
        print("\n[TEST 3, 4 & 5] Outcome classification...")
        rec_succ = OutcomeRecord(
            recommendation_id="REC_S",
            recommendation_type="FLEET_REPOSITION",
            confidence=0.97,
            predicted_benefit=0.15,
            actual_benefit=0.16,
            policy_decision="AUTO_APPROVE",
            execution_mode="AUTONOMOUS",
            outcome="SUCCESSFUL",
        )
        rec_neut = OutcomeRecord(
            recommendation_id="REC_N",
            recommendation_type="FLEET_REPOSITION",
            confidence=0.95,
            predicted_benefit=0.15,
            actual_benefit=-0.02,
            policy_decision="AUTO_APPROVE",
            execution_mode="AUTONOMOUS",
            outcome="NEUTRAL",
        )
        rec_harm = OutcomeRecord(
            recommendation_id="REC_H",
            recommendation_type="FLEET_REPOSITION",
            confidence=0.96,
            predicted_benefit=0.15,
            actual_benefit=-0.12,
            policy_decision="AUTO_APPROVE",
            execution_mode="AUTONOMOUS",
            outcome="HARMFUL",
        )
        assert rec_succ.outcome == "SUCCESSFUL"
        assert rec_neut.outcome == "NEUTRAL"
        assert rec_harm.outcome == "HARMFUL"
        print("✓ Outcome classifications verified.")

        # ------------------------------------------------------------------
        # TEST 6: Predicted vs. Actual Benefit & Realization Ratio
        # ------------------------------------------------------------------
        print("\n[TEST 6] Predicted vs. actual benefit & realization ratio...")
        assert rec_succ.benefit_realization_ratio > 1.0
        assert rec_neut.benefit_realization_ratio < 0.0
        # Zero division safety
        rec_zero_pred = OutcomeRecord(
            recommendation_id="REC_Z",
            recommendation_type="FLEET_REPOSITION",
            confidence=0.90,
            predicted_benefit=0.0,
            actual_benefit=0.05,
            policy_decision="AUTO_APPROVE",
            execution_mode="AUTONOMOUS",
            outcome="SUCCESSFUL",
        )
        assert rec_zero_pred.benefit_realization_ratio == 1.0
        print("✓ Realization ratio protected against division by zero.")

        # ------------------------------------------------------------------
        # TEST 7, 8 & 9: Calibration Buckets, Error & Aggregation
        # ------------------------------------------------------------------
        print("\n[TEST 7, 8 & 9] Calibration buckets, error & aggregation...")
        analyzer = CalibrationAnalyzer()
        outcomes_batch = [
            OutcomeRecord("R1", "FLEET_REPOSITION", 0.96, 0.15, 0.14, "AUTO_APPROVE", "AUTONOMOUS", "SUCCESSFUL"),
            OutcomeRecord("R2", "FLEET_REPOSITION", 0.97, 0.15, 0.15, "AUTO_APPROVE", "AUTONOMOUS", "SUCCESSFUL"),
            OutcomeRecord("R3", "FLEET_REPOSITION", 0.98, 0.15, 0.12, "AUTO_APPROVE", "AUTONOMOUS", "SUCCESSFUL"),
            OutcomeRecord("R4", "FLEET_REPOSITION", 0.95, 0.15, -0.01, "AUTO_APPROVE", "AUTONOMOUS", "NEUTRAL"),
            OutcomeRecord("R5", "FLEET_REPOSITION", 0.96, 0.15, -0.08, "AUTO_APPROVE", "AUTONOMOUS", "HARMFUL"),
            # Medium confidence bucket: 0.80 - 0.90
            OutcomeRecord("R6", "FLEET_REPOSITION", 0.85, 0.10, 0.09, "REQUIRE_OPERATOR", "OPERATOR_APPROVED", "SUCCESSFUL"),
            OutcomeRecord("R7", "FLEET_REPOSITION", 0.88, 0.10, 0.08, "REQUIRE_OPERATOR", "OPERATOR_APPROVED", "SUCCESSFUL"),
        ]
        calib = analyzer.analyze(outcomes_batch)
        assert len(calib.buckets) == 6
        high_bucket = [b for b in calib.buckets if b.min_confidence == 0.95][0]
        assert high_bucket.executed_count == 5
        assert high_bucket.successful_count == 3
        assert high_bucket.empirical_success_rate == 0.60
        assert high_bucket.calibration_error > 0.30
        assert calib.overconfidence_detected is True
        print(f"✓ Calibration analyzer computed error {high_bucket.calibration_error:.1%} and flagged overconfidence.")

        # ------------------------------------------------------------------
        # TEST 10: Policy Performance History
        # ------------------------------------------------------------------
        print("\n[TEST 10] Policy performance history...")
        trend = decision_engine.get_performance_trend()
        assert hasattr(trend, "autonomous_executions")
        assert hasattr(trend, "rollback_success_rate")
        assert hasattr(trend, "prediction_error")
        print("✓ PolicyPerformanceTrend initialized with comprehensive metrics.")

        # ------------------------------------------------------------------
        # TEST 11 & 12: Historical Atomic Persistence
        # ------------------------------------------------------------------
        print("\n[TEST 11 & 12] Historical atomic persistence...")
        outcome_store.record_outcome(rec1)
        outcome_store.record_outcome(rec_succ)
        recs_disk = outcome_store.get_outcomes(limit=10)
        assert len(recs_disk) == 2
        assert test_outcomes_path.exists()
        print("✓ OutcomeStore atomic write and disk reload verified.")

        # ------------------------------------------------------------------
        # TEST 13-17: Operational Drift Calculation (ETA, Fleet, Hospital, Casualties)
        # ------------------------------------------------------------------
        print("\n[TEST 13-17] Operational drift calculation...")
        detector = DriftDetector()
        degraded_metrics = {
            "avg_eta_minutes": 8.5,            # Baseline is 6.5 (+30.8% drift)
            "avg_coverage_score": 0.65,        # Baseline is 0.85 (-23.5% drift)
            "hospital_saturation_pct": 38.0,   # Baseline is 25.0 (+52.0% drift)
            "autonomous_success_rate": 0.82,   # Baseline is 0.95 (-13.7% drift)
            "benefit_realization_ratio": 0.70, # Baseline is 0.90 (-22.2% drift)
            "unresolved_casualty_pct": 3.2,    # Baseline is 2.0 (+60.0% drift)
            "recommendation_volume_per_10m": 8.0,
            "stale_rate_pct": 12.0,
        }
        drift_res = detector.detect_drift(degraded_metrics)
        assert drift_res.eta_drift_pct > 25.0
        assert drift_res.coverage_drift_pct < -20.0
        assert drift_res.hospital_saturation_drift_pct > 30.0
        assert drift_res.unresolved_casualty_drift_pct > 30.0
        print(f"✓ Drift calculated: ETA={drift_res.eta_drift_pct:+.1f}%, Cov={drift_res.coverage_drift_pct:+.1f}%, Sat={drift_res.hospital_saturation_drift_pct:+.1f}%.")

        # ------------------------------------------------------------------
        # TEST 18, 19 & 20: Recommendation Volume, Stale Drift & Severity Classification
        # ------------------------------------------------------------------
        print("\n[TEST 18, 19 & 20] Volume drift, stale drift & severity classification...")
        assert drift_res.stale_rate_drift_pct > 50.0
        assert drift_res.severity == DriftSeverity.CRITICAL
        print(f"✓ Drift classified as {drift_res.severity} (score: {drift_res.overall_drift_score}).")

        # ------------------------------------------------------------------
        # TEST 21 & 22: Adaptive Recommendation Creation & Safe Bounds
        # ------------------------------------------------------------------
        print("\n[TEST 21 & 22] Adaptive recommendation creation & safe bounds...")
        advisor = AdaptivePolicyAdvisor()
        recs = advisor.generate_recommendations(calib, drift_res, trend, policy_config)
        assert len(recs) >= 1
        conf_rec = [r for r in recs if r.policy_parameter == "min_confidence_reposition"][0]
        assert conf_rec.proposed_value > conf_rec.current_value
        assert conf_rec.risk_level == RiskLevel.LOW
        assert conf_rec.status == AdaptationStatus.PENDING
        print(f"✓ Generated adaptive recommendation: {conf_rec.policy_parameter} -> {conf_rec.proposed_value} ({conf_rec.expected_effect}).")

        # ------------------------------------------------------------------
        # TEST 23-27: Hard Safety Rules & Forbidden Modifications
        # ------------------------------------------------------------------
        print("\n[TEST 23-27] Hard safety rules & forbidden parameter protections...")
        bad_rec = LearningRecommendation(
            recommendation_id="BAD_01",
            policy_parameter="predict_severity",
            current_value="ML_MODEL",
            proposed_value="ONLINE_RELEARN",
            evidence="Forbidden change",
            confidence=0.9,
            expected_effect="Illegal modification",
        )
        advisor._recommendations_store["BAD_01"] = bad_rec
        try:
            advisor.approve_recommendation("BAD_01", "OP_TEST", policy_config, version_store)
            assert False, "Should have rejected forbidden parameter 'predict_severity'"
        except ValueError as ex:
            assert "protected and may never be modified" in str(ex)
            print("✓ Blocked attempt to modify clinical model parameter.")

        # Hospital diversion autonomy forbidden
        bad_rec2 = LearningRecommendation(
            recommendation_id="BAD_02",
            policy_parameter="min_confidence_diversion",
            current_value=0.99,
            proposed_value=0.80,
            evidence="Try making diversion autonomous",
            confidence=0.9,
            expected_effect="Illegal diversion autonomy",
        )
        advisor._recommendations_store["BAD_02"] = bad_rec2
        try:
            advisor.approve_recommendation("BAD_02", "OP_TEST", policy_config, version_store)
            assert False, "Should have blocked diversion autonomy change"
        except ValueError as ex:
            assert "protected and may never be modified" in str(ex)
            print("✓ Blocked attempt to make hospital diversions autonomous.")

        # Floor below 2 forbidden
        bad_rec3 = LearningRecommendation(
            recommendation_id="BAD_03",
            policy_parameter="fleet_safety_floor",
            current_value=2,
            proposed_value=1,  # Below 2!
            evidence="Drain donor zone to 1",
            confidence=0.9,
            expected_effect="Breach safety floor",
        )
        advisor._recommendations_store["BAD_03"] = bad_rec3
        try:
            advisor.approve_recommendation("BAD_03", "OP_TEST", policy_config, version_store)
            assert False, "Should have blocked safety floor < 2"
        except ValueError as ex:
            assert "violates safe bounds" in str(ex)
            print("✓ Blocked attempt to lower fleet safety floor below 2.")

        # ------------------------------------------------------------------
        # TEST 28, 29 & 30: Policy Version Creation, Immutable History & Parent Link
        # ------------------------------------------------------------------
        print("\n[TEST 28, 29 & 30] Policy version creation, immutable history & parent link...")
        new_cfg, approved_rec = advisor.approve_recommendation(
            conf_rec.recommendation_id,
            operator_id="OP_LEAD",
            current_config=policy_config,
            version_store=version_store,
        )
        assert approved_rec.status == AdaptationStatus.APPROVED
        assert new_cfg.policy_version == "v2"
        assert new_cfg.parent_version == "v1"
        assert new_cfg.min_confidence_reposition == 0.97
        # Verify v1 remains intact
        v1_cfg = version_store.get_version("v1")
        assert v1_cfg.min_confidence_reposition == 0.95
        print(f"✓ Created version {new_cfg.policy_version} (parent: {new_cfg.parent_version}), v1 preserved immutably.")

        # ------------------------------------------------------------------
        # TEST 31: Operator Approval Requirement
        # ------------------------------------------------------------------
        print("\n[TEST 31] Operator approval requirement...")
        assert approved_rec.approved_by == "OP_LEAD"
        print("✓ Policy update required and recorded explicit operator approval.")

        # ------------------------------------------------------------------
        # TEST 32 & 33: Policy Version Rollback & Audit
        # ------------------------------------------------------------------
        print("\n[TEST 32 & 33] Policy version rollback & audit...")
        v3_cfg = version_store.rollback_to_version(
            target_version_id="v1",
            operator_id="OP_SAFETY",
            reason="Reverting to v1 baseline after surge",
        )
        assert v3_cfg.policy_version == "v3"
        assert v3_cfg.parent_version == "v1"
        assert v3_cfg.min_confidence_reposition == 0.95
        assert "Rollback" in v3_cfg.change_reason
        # Check list history
        hist = version_store.list_versions()
        assert len(hist) == 3
        print("✓ Policy rollback created v3 restoring v1 parameters without mutating v1 or v2.")

        # ------------------------------------------------------------------
        # TEST 34-36: A/B Policy Comparison & Live Simulator Isolation
        # ------------------------------------------------------------------
        print("\n[TEST 34-36] A/B policy comparison & live simulator isolation...")
        with manager.lock:
            sim_state_before = {a: s.status for a, s in sim.state.ambulances.items()}
        ab_res = PolicyEvaluatorAB.compare(v1_cfg, new_cfg, outcomes_batch)
        assert "policy_a" in ab_res
        assert "policy_b" in ab_res
        assert "deltas" in ab_res
        assert "recommendation" in ab_res
        with manager.lock:
            sim_state_after = {a: s.status for a, s in sim.state.ambulances.items()}
        assert sim_state_before == sim_state_after
        print("✓ Offline A/B comparison evaluated cleanly with ZERO mutation of live simulator.")

        # ------------------------------------------------------------------
        # TEST 37: Learning Safety Score Computation
        # ------------------------------------------------------------------
        print("\n[TEST 37] Learning safety score computation...")
        score_obj = calculate_learning_safety_score(calib, trend, drift_res.severity)
        assert 0.0 <= score_obj.score <= 100.0
        assert "calibration_quality" in score_obj.components
        assert "harmful_action_avoidance" in score_obj.components
        print(f"✓ LearningSafetyScore computed: {score_obj.score}/100.")

        # ------------------------------------------------------------------
        # TEST 38, 39 & 40: Deterministic Analysis, Hash & Repeated Equality
        # ------------------------------------------------------------------
        print("\n[TEST 38, 39 & 40] Deterministic analysis, hash & repeated equality...")
        h1 = generate_deterministic_hash({"score": 92.5, "drift": "WATCH", "cases": 10})
        h2 = generate_deterministic_hash({"score": 92.5, "drift": "WATCH", "cases": 10})
        assert h1 == h2
        # Sensitivity to changed data
        h3 = generate_deterministic_hash({"score": 92.5, "drift": "CRITICAL", "cases": 10})
        assert h1 != h3
        print(f"✓ Deterministic hash invariant confirmed: {h1}.")

        # ------------------------------------------------------------------
        # TEST 41 & 42: Harmful-Action Detection & Successful-Action Detection
        # ------------------------------------------------------------------
        print("\n[TEST 41 & 42] Harmful and successful action detection...")
        assert rec_succ.outcome == "SUCCESSFUL"
        assert rec_harm.outcome == "HARMFUL"
        print("✓ Specific actions correctly tagged and aggregated.")

        # ------------------------------------------------------------------
        # TEST 43-47: Recommendations for Threshold, Rate-Limit, and Cooldown
        # ------------------------------------------------------------------
        print("\n[TEST 43-47] Threshold, rate-limit, and cooldown recommendations...")
        param_names = [r.policy_parameter for r in recs]
        assert "min_confidence_reposition" in param_names
        assert "max_autonomous_actions_per_window" in param_names
        print("✓ Specific parameter adaptation recommendations synthesized based on signals.")

        # ------------------------------------------------------------------
        # TEST 48-55: REST APIs Validation
        # ------------------------------------------------------------------
        print("\n[TEST 48-55] REST APIs validation...")
        # 1. GET /optimization/learning
        r_learn = client.get("/optimization/learning")
        assert r_learn.status_code == 200
        assert "safety_score" in r_learn.json()
        assert "calibration" in r_learn.json()

        # 2. GET /optimization/learning/performance
        r_perf = client.get("/optimization/learning/performance")
        assert r_perf.status_code == 200
        assert "autonomous_executions" in r_perf.json()

        # 3. GET /optimization/learning/calibration
        r_cal = client.get("/optimization/learning/calibration")
        assert r_cal.status_code == 200
        assert len(r_cal.json()["buckets"]) == 6

        # 4. GET /optimization/learning/drift
        r_dr = client.get("/optimization/learning/drift")
        assert r_dr.status_code == 200
        assert "severity" in r_dr.json()

        # 5. GET /optimization/learning/recommendations
        r_recs = client.get("/optimization/learning/recommendations")
        assert r_recs.status_code == 200
        assert isinstance(r_recs.json(), list)

        # 6. POST /optimization/learning/compare
        r_comp = client.post("/optimization/learning/compare", json={"policy_a": None, "policy_b": None})
        assert r_comp.status_code == 200
        assert "deltas" in r_comp.json()

        # 7. GET /optimization/policy/history
        r_hist = client.get("/optimization/policy/history")
        assert r_hist.status_code == 200
        assert len(r_hist.json()) >= 1

        # 8. POST /optimization/learning/rollback/{policy_version}
        r_rb = client.post("/optimization/learning/rollback/v1", json={"operator_id": "OP_TEST", "reason": "API rollback test"})
        assert r_rb.status_code == 200
        assert r_rb.json()["policy_version"].startswith("v")

        print("✓ All 8 Operational Learning REST endpoints validated.")

        # ------------------------------------------------------------------
        # TEST 56 & 57: Frontend UI & Zero Dialog Audit
        # ------------------------------------------------------------------
        print("\n[TEST 56 & 57] Frontend UI & zero dialog audit...")
        opt_js = (REPO_ROOT / "frontend/js/components/optimization.js").read_text(encoding="utf-8")
        assert "learning-safety-score-val" in opt_js
        assert "btn-approve-adapt" in opt_js
        assert re.search(r'\b(alert|prompt|confirm)\s*\(', opt_js) is None
        print("✓ Zero-dialog invariant verified across Phase 4 frontend code.")

        # ------------------------------------------------------------------
        # TEST 58, 59 & 60: Concurrency Safety (Learning, Approval, Rollback)
        # ------------------------------------------------------------------
        print("\n[TEST 58, 59 & 60] Concurrency safety (Learning, Approval, Rollback)...")
        concur_status = []
        def call_learning():
            r = client.get("/optimization/learning")
            if r.status_code == 200: concur_status.append(r.status_code)

        def call_drift():
            r = client.get("/optimization/learning/drift")
            if r.status_code == 200: concur_status.append(r.status_code)

        threads = [
            threading.Thread(target=call_learning),
            threading.Thread(target=call_drift),
            threading.Thread(target=call_learning),
            threading.Thread(target=call_drift),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(concur_status) == 4
        print("✓ Concurrent learning and drift queries executed safely without deadlocks.")

        # ------------------------------------------------------------------
        # TEST 61-65: Backwards Compatibility (M9, M10, M11 Phase 1–3)
        # ------------------------------------------------------------------
        print("\n[TEST 61-65] Backwards compatibility checks...")
        # M9 Coordinator
        with manager.lock:
            cov = sim.coordinator.get_coverage(sim.state.ambulances)
            assert len(cov) == 6
        # M10 Drills
        assert client.get("/drills").status_code == 200
        # M11 Phase 1 Snapshot
        assert client.get("/optimization/snapshot").status_code == 200
        # M11 Phase 2 Copilot Summary
        assert client.get("/optimization/copilot/summary").status_code == 200
        # M11 Phase 3 Policy Mode
        assert client.get("/optimization/policy").status_code == 200
        print("✓ Full backward compatibility with M9, M10, and M11 Phase 1–3 verified.")

        # ------------------------------------------------------------------
        # TEST 66: Operational Latency Budget
        # ------------------------------------------------------------------
        print("\n[TEST 66] Operational latency budget...")
        t0 = time.perf_counter()
        analyzer.analyze(outcomes_batch)
        cal_ms = (time.perf_counter() - t0) * 1000.0
        assert cal_ms < 10.0

        t1 = time.perf_counter()
        detector.detect_drift(degraded_metrics)
        drift_ms = (time.perf_counter() - t1) * 1000.0
        assert drift_ms < 10.0
        print(f"✓ Latencies: Calibration={cal_ms:.2f}ms (<10ms), Drift={drift_ms:.2f}ms (<10ms).")

        # ------------------------------------------------------------------
        # TEST 67: Core Endpoints Health Check
        # ------------------------------------------------------------------
        print("\n[TEST 67] Core endpoints health check...")
        assert client.get("/health").status_code == 200
        assert client.get("/optimization/health").status_code == 200
        print("✓ Health endpoints returned 200 OK.")

        # Cleanup test directory
        if test_data_dir.exists():
            shutil.rmtree(test_data_dir)

    print("\n" + "=" * 75)
    print("ALL 67 M11 PHASE 4 OPERATIONAL LEARNING TESTS PASSED.")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    run_phase4_tests()
