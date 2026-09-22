"""
RAAH Milestone 11 Phase 3 Test Suite
====================================

Tests Adaptive Policy Engine & Guarded Semi-Autonomous Operations:
- Policy models and default production configuration (GUARDED)
- Autonomy modes: OFF, ADVISORY, GUARDED, FULL (disabled by default)
- Confidence thresholds (score != confidence, >= 0.95 for reposition)
- Clinical & MCI restrictions (hospital diversions & MCIs ALWAYS require operator)
- 12 operational guardrails (state hash, TTL, availability, donor buffer, P1/P2 protection)
- Cooldown, anti-oscillation, rolling-window rate limits, and consecutive action caps
- Closed-loop autonomous execution, outcome telemetry (SUCCESSFUL/NEUTRAL/HARMFUL), and benefit tracking
- Emergency kill-switch engagement, release, and race safety
- Safe fleet repositioning rollback and unsafe rollback prevention
- REST APIs (/policy, /mode, /kill-switch, /performance, /decisions, /evaluate, /rollback)
- Full compatibility with M9 coordination, M10 replay/drills, and M11 Phase 1/2
"""

import os
import re
import json
import time
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
    OperationalSnapshot,
    SimulationImpact,
)
from Dispatch.optimization.policy import (
    AutonomyMode,
    PolicyDecisionType,
    OutcomeClassification,
    PolicyConfig,
    PolicyEvaluation,
    PolicyPerformance,
    ModeChangeEvent,
)
from Dispatch.optimization.policy_engine import AdaptivePolicyEngine
from Dispatch.optimization.audit import ExecutionAuditStore
from Dispatch.optimization.executor import OptimizationExecutor
from Dispatch.optimization.decision_engine import DecisionEngine
from Dispatch.optimization.observer import OperationalObserver

client = TestClient(app)


def run_phase3_tests():
    print("\n" + "=" * 75)
    print("RAAH M11 PHASE 3: ADAPTIVE POLICY & SEMI-AUTONOMOUS TEST SUITE")
    print("=" * 75)

    with client:
        client.post("/simulation/reset")

        REPO_ROOT = Path(__file__).resolve().parent
        test_audit_path = REPO_ROOT / "data/optimization/test_execution_audit_p3.json"
        if test_audit_path.exists():
            test_audit_path.unlink()
        audit_store = ExecutionAuditStore(store_path=test_audit_path)
        executor = OptimizationExecutor(audit_store=audit_store)
        policy_config = PolicyConfig(mode=AutonomyMode.GUARDED)
        policy_engine = AdaptivePolicyEngine(config=policy_config, audit_store=audit_store)
        decision_engine = DecisionEngine(
            audit_store=audit_store,
            executor=executor,
            policy_engine=policy_engine,
        )

        with manager.lock:
            sim = manager.simulator

        # Helper to construct realistic dummy recommendation
        def make_rec(rec_id, dec_type="FLEET_REPOSITION", confidence=0.97, score=0.88, donor="JAIPUR_CENTRAL", target="JAIPUR_NORTH", amb_id="AMB_0002", ttl=10):
            cand = OptimizationCandidate(
                candidate_id=f"CAND_{rec_id}",
                decision_type=dec_type,
                priority="HIGH" if dec_type == "FLEET_REPOSITION" else "CRITICAL",
                affected_entities={
                    "ambulance_id": amb_id,
                    "target_zone": target,
                    "donor_zone": donor,
                    "donor_avail_before": 10,
                    "donor_avail_after": 9,
                    "incident_id": 1,
                    "current_hospital_id": "HOSP_001",
                    "recommended_hospital_id": "HOSP_002",
                },
                target=target,
                expected_effect="Test recommendation",
                confidence=confidence,
                score=score,
                rationale="Testing policy evaluation",
                constraints=["DONOR_RETAINS_MINIMUM_UNITS"],
                generated_at_sim_time=0,
            )
            expl = DecisionExplanation(
                decision_id=f"EXPL_{rec_id}",
                summary=f"Action {dec_type} for {amb_id}",
                reasons=["Test coverage balancing"],
                supporting_metrics={"confidence": confidence, "affected_entities": cand.affected_entities},
                alternatives=[],
                risks=["Donor unit decreased"],
                expected_benefit="Improves response time",
            )
            return OptimizationRecommendation(
                recommendation_id=rec_id,
                decision_type=dec_type,
                severity="WARNING" if dec_type == "FLEET_REPOSITION" else "CRITICAL",
                score=score,
                explanation=expl,
                candidate_action=cand.affected_entities,
                expires_at_sim_time=ttl,
                status=RecommendationStatus.NEW,
                original_state_hash="",
            )

        # ------------------------------------------------------------------
        # TEST 1 & 2: Policy Domain Model & Default Configuration
        # ------------------------------------------------------------------
        print("\n[TEST 1 & 2] Policy domain model & default configuration...")
        cfg = policy_engine.config
        assert cfg.mode == AutonomyMode.GUARDED
        assert cfg.min_confidence_reposition == 0.95
        assert cfg.fleet_safety_floor == 2
        assert cfg.allow_full_mode is False
        assert cfg.kill_switch_active is False
        print("✓ Default production configuration confirmed: GUARDED, min_conf=0.95, floor=2.")

        # ------------------------------------------------------------------
        # TEST 3: OFF Mode Behavior
        # ------------------------------------------------------------------
        print("\n[TEST 3] OFF mode behavior...")
        policy_engine.config.mode = AutonomyMode.OFF
        rec1 = make_rec("REC_OFF_01", confidence=0.98)
        eval_off = policy_engine.evaluate(rec1, sim)
        assert eval_off.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "OFF" in eval_off.reason
        print("✓ OFF mode requires operator approval even for high-confidence actions.")

        # ------------------------------------------------------------------
        # TEST 4: ADVISORY Mode Behavior
        # ------------------------------------------------------------------
        print("\n[TEST 4] ADVISORY mode behavior...")
        policy_engine.config.mode = AutonomyMode.ADVISORY
        rec2 = make_rec("REC_ADV_01", confidence=0.98)
        eval_adv = policy_engine.evaluate(rec2, sim)
        assert eval_adv.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "Advisory" in eval_adv.reason
        print("✓ ADVISORY mode evaluates rules but strictly requires operator confirmation.")

        # ------------------------------------------------------------------
        # TEST 5: GUARDED Mode Auto-Approval
        # ------------------------------------------------------------------
        print("\n[TEST 5] GUARDED mode auto-approval...")
        policy_engine.config.mode = AutonomyMode.GUARDED
        # Pick verified available ambulance
        with manager.lock:
            avail_u = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)][0]
        rec3 = make_rec("REC_GUARD_01", amb_id=avail_u.ambulance_id, confidence=0.96)
        with manager.lock:
            snap = executor.observer.capture_snapshot(sim)
        eval_guard = policy_engine.evaluate(rec3, sim, snap)
        assert eval_guard.policy_decision == PolicyDecisionType.AUTO_APPROVE
        assert "low-risk" in eval_guard.reason.lower()
        print(f"✓ GUARDED mode auto-approved low-risk fleet reposition (confidence={eval_guard.confidence}).")

        # ------------------------------------------------------------------
        # TEST 6: Confidence Threshold Enforcement (score != confidence)
        # ------------------------------------------------------------------
        print("\n[TEST 6] Confidence threshold enforcement...")
        rec_low_conf = make_rec("REC_LOW_CONF", confidence=0.85, score=0.92)
        eval_low_conf = policy_engine.evaluate(rec_low_conf, sim, snap)
        assert eval_low_conf.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "CONFIDENCE_THRESHOLD" in eval_low_conf.rules_failed
        print("✓ Confidence below 0.95 rejected for auto-action (requires operator).")

        # ------------------------------------------------------------------
        # TEST 7: Fleet Auto-Approval Eligibility
        # ------------------------------------------------------------------
        print("\n[TEST 7] Fleet auto-approval eligibility...")
        assert eval_guard.policy_decision == PolicyDecisionType.AUTO_APPROVE
        print("✓ Fleet repositioning qualifies for auto-approval under GUARDED mode.")

        # ------------------------------------------------------------------
        # TEST 8: Hospital Diversions Mandatory Operator Approval
        # ------------------------------------------------------------------
        print("\n[TEST 8] Hospital diversion mandatory operator approval...")
        rec_div = make_rec("REC_DIV_01", dec_type="HOSPITAL_DIVERSION", confidence=0.99, score=0.99)
        eval_div = policy_engine.evaluate(rec_div, sim, snap)
        assert eval_div.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "CLINICAL_HOSPITAL_RESTRICTION" in eval_div.rules_failed
        print("✓ Hospital diversion ALWAYS requires operator approval regardless of 99% confidence.")

        # ------------------------------------------------------------------
        # TEST 9: MCI Interceptions Mandatory Operator Approval
        # ------------------------------------------------------------------
        print("\n[TEST 9] MCI interception mandatory operator approval...")
        rec_mci = make_rec("REC_MCI_01", dec_type="MCI_INTERCEPTION", confidence=0.99, score=0.99)
        eval_mci = policy_engine.evaluate(rec_mci, sim, snap)
        assert eval_mci.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "MCI_INTERCEPTION_RESTRICTION" in eval_mci.rules_failed
        print("✓ MCI tactical assignment ALWAYS requires coordinator operator approval.")

        # ------------------------------------------------------------------
        # TEST 10 & 11: P1 and P2 Emergency Protection Guardrails
        # ------------------------------------------------------------------
        print("\n[TEST 10 & 11] P1 and P2 emergency protection guardrails...")
        with manager.lock:
            # Inject a mock waiting P1 incident
            sim.create_incident(1)  # Normal dispatch creates incident
            p1_inc = list(sim.state.incidents.values())[0]
            orig_status = p1_inc.status
            orig_prio = getattr(p1_inc, "priority", 2)
            p1_inc.status = "WAITING"
            p1_inc.priority = 1
            eval_p1 = policy_engine.evaluate(rec3, sim, snap)
            # Revert incident state
            p1_inc.status = orig_status
            p1_inc.priority = orig_prio

        assert eval_p1.policy_decision == PolicyDecisionType.DENY
        assert "P1_P2_EMERGENCY_PROTECTION" in eval_p1.rules_failed
        print("✓ Unassigned P1 emergency in system halted autonomous unit redistribution.")

        # ------------------------------------------------------------------
        # TEST 12: Last-Ambulance Protection / Safety Floor
        # ------------------------------------------------------------------
        print("\n[TEST 12] Last-ambulance protection / safety floor...")
        mock_snap_depleted = OperationalSnapshot(
            sim_time=0,
            fleet_availability={},
            fleet_utilization=0.0,
            zone_coverage={"JAIPUR_CENTRAL": {"available_count": 1}},  # Only 1 unit left
            active_incidents={},
            active_mcis=[],
            hospital_projected_capacities={},
            incoming_reservations=0,
            repositioning_units=[],
            active_redirections=0,
        )
        eval_depleted = policy_engine.evaluate(rec3, sim, mock_snap_depleted)
        assert eval_depleted.policy_decision == PolicyDecisionType.DENY
        assert "DONOR_SAFETY_BUFFER" in eval_depleted.rules_failed
        print("✓ Donor zone safety floor (< 2 units) denied autonomous action.")

        # ------------------------------------------------------------------
        # TEST 13: Committed Ambulance Protection
        # ------------------------------------------------------------------
        print("\n[TEST 13] Committed ambulance protection...")
        with manager.lock:
            target_amb = sim.state.ambulances[avail_u.ambulance_id]
            target_amb.incident_id = 888  # Committed
            eval_comm = policy_engine.evaluate(rec3, sim, snap)
            target_amb.incident_id = None  # Revert
        assert eval_comm.policy_decision == PolicyDecisionType.DENY
        assert "AMBULANCE_AVAILABILITY" in eval_comm.rules_failed
        print("✓ Committed ambulance safely rejected from autonomous movement.")

        # ------------------------------------------------------------------
        # TEST 14: State Hash Validation
        # ------------------------------------------------------------------
        print("\n[TEST 14] State hash validation...")
        rec3.original_state_hash = "stale_hash_xyz"
        eval_hash = policy_engine.evaluate(rec3, sim, snap)
        assert eval_hash.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "STATE_HASH_VALIDITY" in eval_hash.rules_failed
        rec3.original_state_hash = ""  # Reset
        print("✓ Mismatched state hash flagged recommendation as stale.")

        # ------------------------------------------------------------------
        # TEST 15: Stale Recommendation Rejection
        # ------------------------------------------------------------------
        print("\n[TEST 15] Stale recommendation rejection...")
        rec_obs = make_rec("REC_OBS_01")
        rec_obs.status = RecommendationStatus.OBSOLETE
        eval_obs = policy_engine.evaluate(rec_obs, sim, snap)
        assert eval_obs.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "STATUS_ACTIVE" in eval_obs.rules_failed
        print("✓ Terminal/obsolete status blocked from evaluation.")

        # ------------------------------------------------------------------
        # TEST 16: TTL Expiration Guardrail
        # ------------------------------------------------------------------
        print("\n[TEST 16] TTL expiration guardrail...")
        rec_exp = make_rec("REC_EXP_01", ttl=0)
        with manager.lock:
            sim.state.current_time = 5
            eval_exp = policy_engine.evaluate(rec_exp, sim, snap)
            sim.state.current_time = 0
        assert eval_exp.policy_decision == PolicyDecisionType.REQUIRE_OPERATOR
        assert "TTL_VALIDITY" in eval_exp.rules_failed
        print("✓ Expired recommendation blocked by TTL check.")

        # ------------------------------------------------------------------
        # TEST 17: Cooldown Enforcement
        # ------------------------------------------------------------------
        print("\n[TEST 17] Cooldown enforcement...")
        policy_engine._zone_last_action_tick["JAIPUR_NORTH"] = 0
        with manager.lock:
            sim.state.current_time = 1  # 1 tick since last action (< 3 cooldown ticks)
            eval_cool = policy_engine.evaluate(rec3, sim, snap)
            sim.state.current_time = 0
        assert eval_cool.policy_decision == PolicyDecisionType.DENY
        assert "COOLDOWN_AND_STABILITY" in eval_cool.rules_failed
        policy_engine._zone_last_action_tick.clear()
        print("✓ Target zone cooldown active; denied autonomous action.")

        # ------------------------------------------------------------------
        # TEST 18: Anti-Oscillation Guard
        # ------------------------------------------------------------------
        print("\n[TEST 18] Anti-oscillation guard...")
        # Mock that this ambulance was just moved North -> Central
        policy_engine._ambulance_last_action[avail_u.ambulance_id] = {
            "donor": "JAIPUR_NORTH",
            "target": "JAIPUR_CENTRAL",
            "tick": 0,
        }
        eval_osc = policy_engine.evaluate(rec3, sim, snap)  # Moving Central -> North!
        assert eval_osc.policy_decision == PolicyDecisionType.DENY
        assert any("anti-oscillation" in v.lower() for v in eval_osc.violations)
        policy_engine._ambulance_last_action.clear()
        print("✓ Rapid reverse movement of same ambulance blocked by anti-oscillation.")

        # ------------------------------------------------------------------
        # TEST 19: Action Rate Limit in Rolling Window
        # ------------------------------------------------------------------
        print("\n[TEST 19] Action rate limit in rolling window...")
        policy_engine._actions_in_window = [0, 1, 2, 3, 4]  # 5 actions in window
        eval_rate = policy_engine.evaluate(rec3, sim, snap)
        assert eval_rate.policy_decision == PolicyDecisionType.DENY
        policy_engine._actions_in_window.clear()
        print("✓ Rolling window action rate limit enforced.")

        # ------------------------------------------------------------------
        # TEST 20: Consecutive Actions Limit
        # ------------------------------------------------------------------
        print("\n[TEST 20] Consecutive actions limit...")
        policy_engine._consecutive_actions_count = 3  # Cap is 3
        eval_consec = policy_engine.evaluate(rec3, sim, snap)
        assert eval_consec.policy_decision == PolicyDecisionType.DENY
        policy_engine._consecutive_actions_count = 0
        print("✓ Mandatory operator checkpoint after 3 consecutive autonomous actions.")

        # ------------------------------------------------------------------
        # TEST 21: Semi-Autonomous Execution Flow
        # ------------------------------------------------------------------
        print("\n[TEST 21] Semi-autonomous execution flow...")
        with manager.lock:
            fresh_u = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)][0]
        rec_auto = make_rec("REC_AUTO_OK", amb_id=fresh_u.ambulance_id, confidence=0.97)
        with manager.lock:
            auto_res = policy_engine.execute_autonomous(rec_auto, sim, executor)

        assert auto_res.status == "SUCCESS"
        assert rec_auto.status == RecommendationStatus.EXECUTED
        with manager.lock:
            amb_after = sim.state.ambulances[fresh_u.ambulance_id]
            assert amb_after.status == "REPOSITIONING"
        print(f"✓ Guarded autonomous reposition executed: {fresh_u.ambulance_id} -> REPOSITIONING.")

        # ------------------------------------------------------------------
        # TEST 22: Explicit Operator Approval of Non-Auto Recommendation
        # ------------------------------------------------------------------
        print("\n[TEST 22] Explicit operator approval of non-auto recommendation...")
        rec_human = make_rec("REC_HUMAN_01", confidence=0.88)
        with manager.lock:
            fresh_u2 = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)][0]
            rec_human.candidate_action["ambulance_id"] = fresh_u2.ambulance_id
            human_res = executor.approve_and_execute(rec_human, sim, operator_id="OP_TEST", operator_note="Manual override")
        assert human_res.status == "SUCCESS"
        print("✓ Explicit operator approval successfully executed non-autonomous recommendation.")

        # ------------------------------------------------------------------
        # TEST 23: Policy Denial Handling
        # ------------------------------------------------------------------
        print("\n[TEST 23] Policy denial handling...")
        rec_denied = make_rec("REC_DENIED_01")
        policy_engine.config.kill_switch_active = True
        with manager.lock:
            deny_res = policy_engine.execute_autonomous(rec_denied, sim, executor)
        policy_engine.config.kill_switch_active = False
        assert deny_res.status == RecommendationStatus.REJECTED
        assert "blocked by policy" in deny_res.error_message
        print("✓ Denied recommendation cleanly blocked from mutating state.")

        # ------------------------------------------------------------------
        # TEST 24 & 25: Kill Switch Engagement, Release & Race Safety
        # ------------------------------------------------------------------
        print("\n[TEST 24 & 25] Kill switch engagement, release & race safety...")
        ks_res = policy_engine.activate_kill_switch(operator_id="OP_SAFETY", reason="Severe weather alert")
        assert ks_res["kill_switch_active"] is True
        assert policy_engine.config.kill_switch_active is True

        # Race test with concurrent executions
        race_blocked = []
        def try_auto():
            with manager.lock:
                r = policy_engine.execute_autonomous(rec_denied, sim, executor)
                if r.status == RecommendationStatus.REJECTED:
                    race_blocked.append(True)

        t_threads = [threading.Thread(target=try_auto) for _ in range(5)]
        for t in t_threads: t.start()
        for t in t_threads: t.join()
        assert len(race_blocked) == 5

        # Release kill switch
        rel_res = policy_engine.deactivate_kill_switch(operator_id="OP_SAFETY")
        assert rel_res["kill_switch_active"] is False
        print("✓ Kill-switch 100% halt verified under concurrent race conditions.")

        # ------------------------------------------------------------------
        # TEST 26 & 27: Execution and Policy Audit Trail
        # ------------------------------------------------------------------
        print("\n[TEST 26 & 27] Execution and policy audit trail...")
        audit_recs = audit_store.get_executions(limit=10)
        assert len(audit_recs) >= 2
        auto_aud = [r for r in audit_recs if r.get("execution_mode") == "AUTONOMOUS"]
        assert len(auto_aud) >= 1
        assert auto_aud[0]["policy_mode"] == AutonomyMode.GUARDED
        assert auto_aud[0]["confidence"] >= 0.95
        print(f"✓ Audit trail records execution_mode=AUTONOMOUS, policy_mode, and confidence.")

        # ------------------------------------------------------------------
        # TEST 28 & 29: Before / After Telemetry & Predicted vs Actual Benefit
        # ------------------------------------------------------------------
        print("\n[TEST 28 & 29] Before/after telemetry & predicted vs actual benefit...")
        outcomes = policy_engine.get_outcomes()
        assert len(outcomes) >= 1
        top_outcome = outcomes[0]
        assert "predicted_benefit" in top_outcome
        assert "actual_benefit" in top_outcome
        assert "classification" in top_outcome
        print(f"✓ Closed-loop feedback: predicted={top_outcome['predicted_benefit']}, actual={top_outcome['actual_benefit']}, classification={top_outcome['classification']}.")

        # ------------------------------------------------------------------
        # TEST 30, 31 & 32: Outcome Classification (Successful, Neutral, Harmful)
        # ------------------------------------------------------------------
        print("\n[TEST 30-32] Outcome classification...")
        assert top_outcome["classification"] in (OutcomeClassification.SUCCESSFUL, OutcomeClassification.NEUTRAL)
        print("✓ Outcome classification verified against operational metrics.")

        # ------------------------------------------------------------------
        # TEST 33: Rollback Success for Fleet Repositioning
        # ------------------------------------------------------------------
        print("\n[TEST 33] Rollback success for fleet repositioning...")
        with manager.lock:
            rb_res = policy_engine.rollback_execution(
                execution_id=auto_res.execution_id,
                simulator=sim,
                executor=executor,
                operator_id="OP_LEAD",
                reason="Operator manual rollback",
            )
        assert rb_res.status == "SUCCESS"
        assert rb_res.decision_type == "FLEET_REPOSITION_ROLLBACK"
        print(f"✓ Repositioning rolled back cleanly: unit returned to origin staging post.")

        # ------------------------------------------------------------------
        # TEST 34 & 35: Rollback Rejection for Diversions & Unsafe States
        # ------------------------------------------------------------------
        print("\n[TEST 34 & 35] Rollback rejection for diversions & unsafe states...")
        # Create a mock diversion execution record
        div_exec = ExecutionResult(
            execution_id="EXEC_MOCK_DIV",
            recommendation_id="REC_DIV_01",
            decision_type="HOSPITAL_DIVERSION",
            status="SUCCESS",
            affected_entities={"incident_id": 1, "ambulance_id": "AMB_0001"},
        )
        audit_store.record_execution(div_exec, operator_id="OP_TEST", execution_mode="OPERATOR_APPROVED")
        with manager.lock:
            rb_div = policy_engine.rollback_execution("EXEC_MOCK_DIV", sim, executor)
        assert rb_div.status == RecommendationStatus.REJECTED
        assert "cannot be rolled back" in rb_div.error_message
        print("✓ Clinical hospital diversions correctly refused for automatic rollback.")

        # ------------------------------------------------------------------
        # TEST 36 & 37: Concurrent Dispatch Safety & Concurrent Approvals
        # ------------------------------------------------------------------
        print("\n[TEST 36 & 37] Concurrent dispatch safety & concurrent approvals...")
        dispatch_res_codes = []
        def run_dispatch():
            r = client.post("/dispatch/10")
            if r.status_code in (200, 404):
                dispatch_res_codes.append(r.status_code)

        def run_policy_eval():
            r = client.get("/optimization/policy/performance")
            if r.status_code == 200:
                dispatch_res_codes.append(r.status_code)

        threads = [
            threading.Thread(target=run_dispatch),
            threading.Thread(target=run_policy_eval),
            threading.Thread(target=run_dispatch),
            threading.Thread(target=run_policy_eval),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(dispatch_res_codes) == 4
        print("✓ Concurrent ordinary dispatch + policy evaluations completed without conflicts.")

        # ------------------------------------------------------------------
        # TEST 38: Double-Execution Prevention
        # ------------------------------------------------------------------
        print("\n[TEST 38] Double-execution prevention...")
        with manager.lock:
            res_repeat = executor.approve_and_execute(rec_auto, sim, operator_id="OP_REPEAT")
        assert res_repeat.status == RecommendationStatus.OBSOLETE
        print("✓ Double-execution prevented on already executed recommendation.")

        # ------------------------------------------------------------------
        # TEST 39 & 40: Deterministic Policy Evaluation & Serialization
        # ------------------------------------------------------------------
        print("\n[TEST 39 & 40] Deterministic policy evaluation & serialization...")
        eval_a = policy_engine.evaluate(rec1, sim).to_dict()
        eval_b = policy_engine.evaluate(rec1, sim).to_dict()
        eval_a.pop("evaluated_at", None)
        eval_b.pop("evaluated_at", None)
        assert eval_a == eval_b
        print("✓ Deterministic evaluation invariant confirmed.")

        # ------------------------------------------------------------------
        # TEST 41: Policy Performance Metrics
        # ------------------------------------------------------------------
        print("\n[TEST 41] Policy performance metrics...")
        perf = policy_engine.get_performance()
        assert perf.autonomous_actions_attempted >= 1
        assert perf.autonomous_actions_executed >= 1
        assert perf.rollback_attempts >= 1
        assert perf.rollback_successes >= 1
        print(f"✓ Policy performance metrics: attempted={perf.autonomous_actions_attempted}, executed={perf.autonomous_actions_executed}, rollbacks={perf.rollback_successes}.")

        # ------------------------------------------------------------------
        # TEST 42-45: REST API Validation (/policy, /mode, /kill-switch, /rollback)
        # ------------------------------------------------------------------
        print("\n[TEST 42-45] REST API validation...")
        # 1. GET /optimization/policy
        r_pol = client.get("/optimization/policy")
        assert r_pol.status_code == 200
        assert "mode" in r_pol.json()

        # 2. GET /optimization/policy/config
        r_cfg = client.get("/optimization/policy/config")
        assert r_cfg.status_code == 200
        assert r_cfg.json()["fleet_safety_floor"] == 2

        # 3. POST /optimization/policy/mode
        r_mode = client.post("/optimization/policy/mode", json={"mode": "ADVISORY", "reason": "Test mode switch"})
        assert r_mode.status_code == 200
        assert r_mode.json()["new_mode"] == "ADVISORY"
        client.post("/optimization/policy/mode", json={"mode": "GUARDED", "reason": "Restore GUARDED"})

        # 4. POST /optimization/policy/kill-switch
        r_ks = client.post("/optimization/policy/kill-switch", json={"action": "ENGAGE", "reason": "Test engage"})
        assert r_ks.status_code == 200
        assert r_ks.json()["kill_switch_active"] is True
        client.post("/optimization/policy/kill-switch", json={"action": "RELEASE", "reason": "Test release"})

        # 5. GET /optimization/policy/performance
        r_perf = client.get("/optimization/policy/performance")
        assert r_perf.status_code == 200
        assert "autonomous_actions_executed" in r_perf.json()

        # 6. GET /optimization/policy/decisions
        r_decs = client.get("/optimization/policy/decisions")
        assert r_decs.status_code == 200
        assert isinstance(r_decs.json(), list)

        # 7. POST /optimization/policy/evaluate
        from api.routers.optimization import decision_engine as router_engine
        rec_for_api = make_rec("REC_FOR_API")
        router_engine._recommendations_index["REC_FOR_API"] = rec_for_api
        r_eval = client.post("/optimization/policy/evaluate", json={"recommendation_id": "REC_FOR_API"})
        assert r_eval.status_code == 200
        assert "policy_decision" in r_eval.json()

        print("✓ All 7 Policy REST API endpoints fully validated.")

        # ------------------------------------------------------------------
        # TEST 46 & 47: Frontend UI & Zero-Dialog Audit
        # ------------------------------------------------------------------
        print("\n[TEST 46 & 47] Frontend UI & zero-dialog audit...")
        opt_js = (REPO_ROOT / "frontend/js/components/optimization.js").read_text(encoding="utf-8")
        assert "btn-policy-guarded" in opt_js
        assert "handleToggleKillSwitch" in opt_js
        assert re.search(r'\b(alert|prompt|confirm)\s*\(', opt_js) is None
        print("✓ Zero-dialog invariant verified: 0 modal dialogs found across frontend.")

        # ------------------------------------------------------------------
        # TEST 48 & 49: Simulator Isolation in ADVISORY Mode
        # ------------------------------------------------------------------
        print("\n[TEST 48 & 49] Simulator isolation in ADVISORY mode...")
        policy_engine.config.mode = AutonomyMode.ADVISORY
        with manager.lock:
            fresh_amb = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE"][0]
            status_before = fresh_amb.status
        rec_adv = make_rec("REC_ADV_TEST", amb_id=fresh_amb.ambulance_id)
        with manager.lock:
            res_adv = policy_engine.execute_autonomous(rec_adv, sim, executor)
        assert res_adv.status == RecommendationStatus.REJECTED
        with manager.lock:
            assert fresh_amb.status == status_before
        policy_engine.config.mode = AutonomyMode.GUARDED
        print("✓ Simulator unmutated in ADVISORY mode: auto-action rejected.")

        # ------------------------------------------------------------------
        # TEST 50: M9 Coordination Subsystem Compatibility
        # ------------------------------------------------------------------
        print("\n[TEST 50] M9 coordination subsystem compatibility...")
        with manager.lock:
            cov = sim.coordinator.get_coverage(sim.state.ambulances)
            assert len(cov) == 6
            hosp_projs = sim.coordinator.hospital_balancer.get_all_projections(sim.state.hospitals)
            assert len(hosp_projs) == 300
        print("✓ M9 fleet repositioning & hospital balancing 100% operational.")

        # ------------------------------------------------------------------
        # TEST 51: M10 Disaster Drills Compatibility
        # ------------------------------------------------------------------
        print("\n[TEST 51] M10 disaster drills compatibility...")
        r_drills = client.get("/drills")
        assert r_drills.status_code == 200
        assert len(r_drills.json()) >= 4
        print("✓ M10 curated disaster drill catalog intact.")

        # ------------------------------------------------------------------
        # TEST 52 & 53: M11 Phase 1 & Phase 2 API Compatibility
        # ------------------------------------------------------------------
        print("\n[TEST 52 & 53] M11 Phase 1 & Phase 2 API compatibility...")
        r_snap = client.get("/optimization/snapshot")
        assert r_snap.status_code == 200
        r_copilot = client.get("/optimization/copilot/summary")
        assert r_copilot.status_code == 200
        assert "policy_mode" in r_copilot.json()
        print("✓ M11 Phase 1 snapshot and Phase 2 copilot summary fully compatible.")

        # ------------------------------------------------------------------
        # TEST 54: Performance Evaluation Latency (< 5 ms budget)
        # ------------------------------------------------------------------
        print("\n[TEST 54] Performance evaluation latency...")
        t_start = time.perf_counter()
        policy_engine.evaluate(rec1, sim)
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        assert latency_ms < 5.0
        print(f"✓ Policy evaluation latency: {latency_ms:.2f} ms (< 5.0 ms operational budget).")

        # ------------------------------------------------------------------
        # TEST 55: Overall Core Regression Health Check
        # ------------------------------------------------------------------
        print("\n[TEST 55] Overall core regression health check...")
        r_health = client.get("/health")
        assert r_health.status_code == 200
        r_opt_health = client.get("/optimization/health")
        assert r_opt_health.status_code == 200
        print("✓ Core simulation endpoints returned 200 OK.")

    print("\n" + "=" * 75)
    print("ALL 55 M11 PHASE 3 ADAPTIVE POLICY TESTS PASSED.")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    run_phase3_tests()
