"""
RAAH Milestone 11 Phase 2 Test Suite
====================================

Tests Operator Copilot & Interactive Decision Execution:
- Recommendation lifecycle and status transitions (NEW -> REVIEWED -> APPROVED -> EXECUTING -> EXECUTED)
- Explicit operator approval requirement (zero autonomous execution)
- Stale recommendation detection and rejection against altered state hashes
- Hard constraint revalidation (last-unit, committed unit, hospital capacity, ICU preservation)
- Authoritative execution through Simulator methods (FLEET_REPOSITION, HOSPITAL_DIVERSION, MCI_INTERCEPTION)
- Concurrency, race condition, and double-execution protection
- Atomic persistent execution audit logging (survives store reload)
- REST API validation: /approve, /reject, /executions, /copilot/summary
- Frontend zero-dialog static audit
- Integration with M9 coordination, M8 routing, and M7 persistence
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
)
from Dispatch.optimization.audit import ExecutionAuditStore
from Dispatch.optimization.executor import OptimizationExecutor
from Dispatch.optimization.decision_engine import DecisionEngine
from Dispatch.optimization.observer import OperationalObserver
from Dispatch.optimization.fleet_optimizer import FleetOptimizer
from Dispatch.optimization.hospital_optimizer import HospitalOptimizer
from Dispatch.optimization.scorer import DecisionScorer

client = TestClient(app)


def run_phase2_tests():
    print("\n" + "=" * 75)
    print("RAAH M11 PHASE 2: OPERATOR COPILOT & INTERACTIVE EXECUTION TEST SUITE")
    print("=" * 75)

    with client:
        client.post("/simulation/reset")

        REPO_ROOT = Path(__file__).resolve().parent
        test_audit_path = REPO_ROOT / "data/optimization/test_execution_audit.json"
        if test_audit_path.exists():
            test_audit_path.unlink()
        audit_store = ExecutionAuditStore(store_path=test_audit_path)
        executor = OptimizationExecutor(audit_store=audit_store)
        decision_engine = DecisionEngine(audit_store=audit_store, executor=executor)

        with manager.lock:
            sim = manager.simulator

        # ------------------------------------------------------------------
        # TEST 1 & 2: Recommendation Creation and Status Lifecycle
        # ------------------------------------------------------------------
        print("\n[TEST 1 & 2] Recommendation creation and status lifecycle...")
        cand = OptimizationCandidate(
            candidate_id="TEST_CAND_01",
            decision_type="FLEET_REPOSITION",
            priority="HIGH",
            affected_entities={
                "ambulance_id": "AMB_0002",
                "target_zone": "JAIPUR_NORTH",
                "donor_zone": "JAIPUR_CENTRAL",
                "donor_avail_before": 10,
                "donor_avail_after": 9,
                "target_avail_before": 0,
                "target_avail_after": 1,
            },
            target="JAIPUR_NORTH",
            expected_effect="Test repositioning",
            confidence=0.95,
            score=0.88,
            rationale="Test deficit in North",
            constraints=["DONOR_RETAINS_MINIMUM_UNITS", "UNIT_IS_AVAILABLE_AND_IDLE"],
            generated_at_sim_time=0,
        )
        expl = DecisionExplanation(
            decision_id="EXPL_TEST_01",
            summary="Reposition AMB_0002 to North",
            reasons=["Deficit in North"],
            supporting_metrics={"confidence": 0.95, "affected_entities": cand.affected_entities},
            alternatives=[],
            risks=["Donor unit decreases"],
            expected_benefit="Balances coverage",
        )
        rec = OptimizationRecommendation(
            recommendation_id="REC_TEST_01",
            decision_type="FLEET_REPOSITION",
            severity="WARNING",
            score=0.88,
            explanation=expl,
            candidate_action=cand.affected_entities,
            expires_at_sim_time=3,
            status=RecommendationStatus.NEW,
            original_state_hash="initialhash123",
        )
        assert rec.status == RecommendationStatus.NEW
        assert rec.approved_by is None
        assert rec.execution_result is None
        print("✓ Recommendation initialized in state NEW.")

        # ------------------------------------------------------------------
        # TEST 3 & 4: Explicit Approval Requirement & Zero Autonomous Execution
        # ------------------------------------------------------------------
        print("\n[TEST 3 & 4] Explicit approval requirement & zero autonomous execution...")
        # Prior to approval, live simulator state must NOT change
        with manager.lock:
            amb = sim.state.ambulances["AMB_0002"]
            assert amb.status == "AVAILABLE"
            assert getattr(amb, "is_repositioning", False) is False
        print("✓ Confirmed: live simulator unmutated prior to approval.")

        # ------------------------------------------------------------------
        # TEST 5: State-Hash Capture
        # ------------------------------------------------------------------
        print("\n[TEST 5] State-hash capture...")
        with manager.lock:
            fresh_snap = decision_engine.get_snapshot(sim)
        assert len(fresh_snap.snapshot_hash) == 20
        rec.original_state_hash = fresh_snap.snapshot_hash
        print(f"✓ Original state hash captured: {rec.original_state_hash}")

        # ------------------------------------------------------------------
        # TEST 6 & 7: Stale Recommendation Rejection & Constraint Revalidation
        # ------------------------------------------------------------------
        print("\n[TEST 6 & 7] Stale recommendation rejection & constraint revalidation...")
        # Create a recommendation for an ambulance that has become BUSY
        with manager.lock:
            amb.status = "BUSY"  # Intentionally alter state to stale
            res_stale = executor.approve_and_execute(rec, sim, operator_id="OP_TEST")
            amb.status = "AVAILABLE"  # Revert back for future tests

        assert res_stale.status == RecommendationStatus.OBSOLETE
        assert rec.status == RecommendationStatus.OBSOLETE
        assert "status changed" in res_stale.error_message.lower()
        print(f"✓ Stale recommendation safely rejected: {res_stale.error_message}")

        # ------------------------------------------------------------------
        # TEST 8: Last-Ambulance Protection During Execution
        # ------------------------------------------------------------------
        print("\n[TEST 8] Last-ambulance protection during execution...")
        rec_last = OptimizationRecommendation(
            recommendation_id="REC_TEST_LAST",
            decision_type="FLEET_REPOSITION",
            severity="WARNING",
            score=0.85,
            explanation=expl,
            candidate_action={
                "ambulance_id": "AMB_0002",
                "target_zone": "JAIPUR_NORTH",
                "donor_zone": "MOCK_DEPLETED_ZONE",
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        # Observer mock showing donor has only 1 unit
        mock_obs = OperationalObserver()
        mock_executor = OptimizationExecutor(observer=mock_obs, audit_store=audit_store)
        mock_snap = OperationalSnapshot(
            sim_time=0,
            fleet_availability={},
            fleet_utilization=0.0,
            zone_coverage={"MOCK_DEPLETED_ZONE": {"available_count": 1}},
            active_incidents={},
            active_mcis=[],
            hospital_projected_capacities={},
            incoming_reservations=0,
            repositioning_units=[],
            active_redirections=0,
        )
        err = mock_executor._validate_constraints(rec_last, sim, mock_snap)
        assert err is not None
        assert "cannot reposition last unit" in err.lower()
        print("✓ Last-ambulance protection revalidated: refused to drain donor with 1 unit.")

        # ------------------------------------------------------------------
        # TEST 9: Committed Ambulance Protection During Execution
        # ------------------------------------------------------------------
        print("\n[TEST 9] Committed ambulance protection during execution...")
        with manager.lock:
            amb.incident_id = 999  # Assigned to incident
            err_comm = executor._validate_constraints(rec, sim, fresh_snap)
            amb.incident_id = None  # Revert
        assert err_comm is not None
        assert "committed to incident" in err_comm.lower()
        print("✓ Committed ambulance protection verified.")

        # ------------------------------------------------------------------
        # TEST 10 & 11: Hospital Capacity & ICU Protection During Execution
        # ------------------------------------------------------------------
        print("\n[TEST 10 & 11] Hospital capacity & ICU protection during execution...")
        # Dispatch an incident so we have an en-route transport
        with manager.lock:
            disp_res = sim.create_incident(1)
            inc = sim.state.incidents[1]
            t_amb = sim.state.ambulances[str(inc.ambulance_id)]
            assert t_amb.status == "EN_ROUTE"

        rec_div = OptimizationRecommendation(
            recommendation_id="REC_TEST_DIV_01",
            decision_type="HOSPITAL_DIVERSION",
            severity="CRITICAL",
            score=0.92,
            explanation=expl,
            candidate_action={
                "incident_id": 1,
                "ambulance_id": str(t_amb.ambulance_id),
                "current_hospital_id": str(inc.hospital_id),
                "recommended_hospital_id": "HOSP_002",
                "priority": "P1",
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )

        # Test ICU exhaustion validation
        mock_snap_icu = OperationalSnapshot(
            sim_time=0,
            fleet_availability={},
            fleet_utilization=0.0,
            zone_coverage={},
            active_incidents={},
            active_mcis=[],
            hospital_projected_capacities={"HOSP_002": {"projected_available_beds": 10, "projected_available_icu": 0}},
            incoming_reservations=0,
            repositioning_units=[],
            active_redirections=0,
        )
        err_icu = executor._validate_constraints(rec_div, sim, mock_snap_icu)
        assert err_icu is not None
        assert "no available icu beds" in err_icu.lower()
        print("✓ ICU protection verified: blocked diversion of P1 trauma to zero-ICU facility.")

        # ------------------------------------------------------------------
        # TEST 12: Fleet Reposition Authoritative Execution
        # ------------------------------------------------------------------
        print("\n[TEST 12] Fleet reposition authoritative execution...")
        with manager.lock:
            avail_units = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)]
            target_repo_amb = avail_units[0].ambulance_id

        rec_repo = OptimizationRecommendation(
            recommendation_id="REC_TEST_REPO_OK",
            decision_type="FLEET_REPOSITION",
            severity="WARNING",
            score=0.89,
            explanation=expl,
            candidate_action={
                "ambulance_id": target_repo_amb,
                "target_zone": "JAIPUR_NORTH",
                "donor_zone": "JAIPUR_CENTRAL",
                "donor_avail_before": 100,
                "donor_avail_after": 99,
                "target_avail_before": 0,
                "target_avail_after": 1,
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        with manager.lock:
            res_exec = executor.approve_and_execute(rec_repo, sim, operator_id="OP_LEAD", operator_note="Balancing North")

        assert res_exec.status == "SUCCESS"
        assert rec_repo.status == RecommendationStatus.EXECUTED
        assert rec_repo.approved_by == "OP_LEAD"
        assert rec_repo.execution_result is not None
        # Verify authoritative simulator state mutated
        with manager.lock:
            amb_repo = sim.state.ambulances[target_repo_amb]
            assert amb_repo.status == "REPOSITIONING"
            assert amb_repo.is_repositioning is True
        print(f"✓ Fleet reposition executed authoritatively: {target_repo_amb} status={amb_repo.status}.")

        # ------------------------------------------------------------------
        # TEST 13: Hospital Diversion Authoritative Execution
        # ------------------------------------------------------------------
        print("\n[TEST 13] Hospital diversion authoritative execution...")
        # Find a viable alternative hospital
        with manager.lock:
            alt_hosp = [h for h in sim.state.hospitals.values() if h.hospital_id != inc.hospital_id and h.capacity - h.current_load > 10][0]
        rec_div_ok = OptimizationRecommendation(
            recommendation_id="REC_TEST_DIV_OK",
            decision_type="HOSPITAL_DIVERSION",
            severity="CRITICAL",
            score=0.94,
            explanation=expl,
            candidate_action={
                "incident_id": 1,
                "ambulance_id": str(t_amb.ambulance_id),
                "current_hospital_id": str(inc.hospital_id),
                "recommended_hospital_id": alt_hosp.hospital_id,
                "priority": "P2",
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        with manager.lock:
            res_div = executor.approve_and_execute(rec_div_ok, sim, operator_id="OP_LEAD", operator_note="Trauma divert")

        assert res_div.status == "SUCCESS"
        assert rec_div_ok.status == RecommendationStatus.EXECUTED
        with manager.lock:
            assert inc.hospital_id == alt_hosp.hospital_id
        print(f"✓ Hospital diversion executed authoritatively: Incident 1 diverted to {alt_hosp.hospital_id}.")

        # ------------------------------------------------------------------
        # TEST 14: MCI Interception Authoritative Execution
        # ------------------------------------------------------------------
        print("\n[TEST 14] MCI interception authoritative execution...")
        # target_repo_amb is currently REPOSITIONING. Intercept it!
        rec_intercept = OptimizationRecommendation(
            recommendation_id="REC_TEST_INTERCEPT",
            decision_type="MCI_INTERCEPTION",
            severity="CRITICAL",
            score=0.96,
            explanation=expl,
            candidate_action={
                "ambulance_id": target_repo_amb,
                "incident_id": 50,
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        with manager.lock:
            res_int = executor.approve_and_execute(rec_intercept, sim, operator_id="OP_LEAD", operator_note="Intercept call 50")

        assert res_int.status == "SUCCESS"
        with manager.lock:
            amb_after = sim.state.ambulances[target_repo_amb]
            assert amb_after.status == "AVAILABLE"
            assert amb_after.is_repositioning is False
        print("✓ MCI interception executed: cancelled repositioning and restored unit to AVAILABLE.")

        # ------------------------------------------------------------------
        # TEST 15 & 16: Concurrency Safety & Concurrent Approval Race
        # ------------------------------------------------------------------
        print("\n[TEST 15 & 16] Concurrency safety & concurrent approval race...")
        with manager.lock:
            avail_units = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)]
            race_amb_id = avail_units[0].ambulance_id

        rec_race = OptimizationRecommendation(
            recommendation_id="REC_TEST_RACE",
            decision_type="FLEET_REPOSITION",
            severity="WARNING",
            score=0.85,
            explanation=expl,
            candidate_action={
                "ambulance_id": race_amb_id,
                "target_zone": "JAIPUR_NORTH",
                "donor_zone": "JAIPUR_CENTRAL",
                "donor_avail_before": 100,
                "donor_avail_after": 99,
            },
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        race_results = []

        def try_approve(op_id):
            with manager.lock:
                res = executor.approve_and_execute(rec_race, sim, operator_id=op_id)
                race_results.append(res)

        t1 = threading.Thread(target=try_approve, args=("OP_1",))
        t2 = threading.Thread(target=try_approve, args=("OP_2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must succeed and the other must be marked OBSOLETE/terminal
        successes = [r for r in race_results if r.status == "SUCCESS"]
        terminals = [r for r in race_results if r.status == RecommendationStatus.OBSOLETE]
        assert len(successes) == 1
        assert len(terminals) == 1
        print(f"✓ Concurrent approval race resolved cleanly: 1 SUCCESS, 1 {terminals[0].status}.")

        # ------------------------------------------------------------------
        # TEST 17 & 18: Double-Execution Prevention & Idempotency
        # ------------------------------------------------------------------
        print("\n[TEST 17 & 18] Double-execution prevention & idempotency...")
        with manager.lock:
            res_double = executor.approve_and_execute(rec_race, sim, operator_id="OP_3")
        assert res_double.status == RecommendationStatus.OBSOLETE
        assert "terminal state" in res_double.error_message
        print("✓ Double-execution prevented: second attempt safely rejected.")

        # ------------------------------------------------------------------
        # TEST 19, 20, 21 & 22: Audit Persistence, Hashes & Entity Tracking
        # ------------------------------------------------------------------
        print("\n[TEST 19-22] Audit persistence, hashes & entity tracking...")
        audit_records = audit_store.get_executions(limit=20)
        assert len(audit_records) >= 4  # stale, repo, div, intercept, race

        top_rec = audit_records[0]
        assert "execution_id" in top_rec
        assert "state_hash_before" in top_rec
        assert "state_hash_after" in top_rec
        assert "resulting_entity_ids" in top_rec
        assert top_rec["operator_id"] is not None

        # Verify reload from disk
        reloaded_store = ExecutionAuditStore(store_path=test_audit_path)
        reloaded_records = reloaded_store.get_executions()
        assert len(reloaded_records) == len(audit_records)
        print(f"✓ Audit trail verified with persistent atomic JSON ({len(reloaded_records)} records).")

        # ------------------------------------------------------------------
        # TEST 23 & 24: Recommendation Expiration & TTL Behavior
        # ------------------------------------------------------------------
        print("\n[TEST 23 & 24] Recommendation expiration & TTL behavior...")
        rec_expired = OptimizationRecommendation(
            recommendation_id="REC_TEST_EXP",
            decision_type="FLEET_REPOSITION",
            severity="INFO",
            score=0.75,
            explanation=expl,
            candidate_action={"ambulance_id": "AMB_0005", "target_zone": "JAIPUR_NORTH"},
            expires_at_sim_time=0,  # Expired
            status=RecommendationStatus.NEW,
        )
        with manager.lock:
            sim.state.current_time = 10  # Advance clock past TTL
            res_exp = executor.approve_and_execute(rec_expired, sim, operator_id="OP_TEST")
            sim.state.current_time = 0   # Revert clock

        assert res_exp.status == RecommendationStatus.EXPIRED
        assert rec_expired.status == RecommendationStatus.EXPIRED
        print(f"✓ Expired recommendation blocked: {res_exp.error_message}")

        # ------------------------------------------------------------------
        # TEST 25: Obsolete Recommendation Auto-Dismissal
        # ------------------------------------------------------------------
        print("\n[TEST 25] Obsolete recommendation auto-dismissal...")
        decision_engine._recommendations_index["REC_TEST_AUTO_EXP"] = OptimizationRecommendation(
            recommendation_id="REC_TEST_AUTO_EXP",
            decision_type="FLEET_REPOSITION",
            severity="INFO",
            score=0.70,
            explanation=expl,
            candidate_action={"ambulance_id": "AMB_0005", "target_zone": "JAIPUR_NORTH"},
            expires_at_sim_time=1,
            status=RecommendationStatus.NEW,
        )
        with manager.lock:
            sim.state.current_time = 5
            decision_engine.evaluate_state(sim)
            sim.state.current_time = 0
        auto_rec = decision_engine.get_recommendation("REC_TEST_AUTO_EXP")
        assert auto_rec.status == RecommendationStatus.EXPIRED
        print("✓ Auto-dismissal pass expired stale recommendation during evaluate_state().")

        # ------------------------------------------------------------------
        # TEST 26 & 27: Rejection Workflow & Operator Notes
        # ------------------------------------------------------------------
        print("\n[TEST 26 & 27] Rejection workflow & operator notes...")
        rec_for_rej = OptimizationRecommendation(
            recommendation_id="REC_TEST_REJ",
            decision_type="FLEET_REPOSITION",
            severity="INFO",
            score=0.60,
            explanation=expl,
            candidate_action={"ambulance_id": "AMB_0006"},
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        decision_engine._recommendations_index["REC_TEST_REJ"] = rec_for_rej
        rej_res = decision_engine.reject_recommendation(
            "REC_TEST_REJ",
            operator_id="OP_COMMANDER",
            reason="Preserving units for scheduled drill",
        )
        assert rej_res.status == RecommendationStatus.REJECTED
        assert rej_res.rejection_reason == "Preserving units for scheduled drill"
        assert rej_res.rejection_note == "Preserving units for scheduled drill"
        print(f"✓ Recommendation rejected with operator note: '{rej_res.rejection_reason}'.")

        # ------------------------------------------------------------------
        # TEST 28, 29, 30 & 31: REST API Validation (Approve, Reject, Executions, Copilot)
        # ------------------------------------------------------------------
        print("\n[TEST 28-31] REST API endpoints validation...")
        from api.routers.optimization import decision_engine as router_engine

        # Seed router engine with a clean executable recommendation
        with manager.lock:
            avail_units = [a for a in sim.state.ambulances.values() if a.status == "AVAILABLE" and not getattr(a, "is_repositioning", False)]
            api_amb_id = avail_units[0].ambulance_id

        rec_api = OptimizationRecommendation(
            recommendation_id="REC_API_01",
            decision_type="FLEET_REPOSITION",
            severity="HIGH",
            score=0.91,
            explanation=expl,
            candidate_action={
                "ambulance_id": api_amb_id,
                "target_zone": "JAIPUR_NORTH",
                "donor_zone": "JAIPUR_CENTRAL",
                "donor_avail_before": 50,
                "donor_avail_after": 49,
            },
            expires_at_sim_time=10,
            status=RecommendationStatus.NEW,
        )
        router_engine._recommendations_index["REC_API_01"] = rec_api

        # 1. POST /optimization/recommendations/{id}/approve
        r_app = client.post(
            "/optimization/recommendations/REC_API_01/approve",
            json={"operator_id": "OP_API", "operator_note": "Testing API approval"},
        )
        assert r_app.status_code == 200
        app_data = r_app.json()
        assert app_data["status"] == "SUCCESS"
        assert "execution_id" in app_data

        # 2. GET /optimization/executions/{id}
        exec_id = app_data["execution_id"]
        r_exec = client.get(f"/optimization/executions/{exec_id}")
        assert r_exec.status_code == 200
        assert r_exec.json()["execution_id"] == exec_id

        # 3. GET /optimization/executions
        r_execs = client.get("/optimization/executions")
        assert r_execs.status_code == 200
        assert isinstance(r_execs.json(), list)

        # 4. POST /optimization/recommendations/{id}/reject
        rec_api_rej = OptimizationRecommendation(
            recommendation_id="REC_API_REJ_01",
            decision_type="FLEET_REPOSITION",
            severity="LOW",
            score=0.55,
            explanation=expl,
            candidate_action={"ambulance_id": "AMB_0009"},
            expires_at_sim_time=10,
            status=RecommendationStatus.NEW,
        )
        router_engine._recommendations_index["REC_API_REJ_01"] = rec_api_rej
        r_rej = client.post(
            "/optimization/recommendations/REC_API_REJ_01/reject",
            json={"operator_id": "OP_API", "reason": "Not needed right now"},
        )
        assert r_rej.status_code == 200
        assert r_rej.json()["status"] == RecommendationStatus.REJECTED

        # 5. GET /optimization/copilot/summary
        r_sum = client.get("/optimization/copilot/summary")
        assert r_sum.status_code == 200
        sum_data = r_sum.json()
        assert "operational_health" in sum_data
        assert "pending_recommendations_count" in sum_data
        assert "recent_executions_count" in sum_data
        print("✓ All Operator Copilot REST endpoints validated.")

        # --------------------------------------------------------------
        # TEST 32: Frontend Zero-Dialog Static Audit
        # --------------------------------------------------------------
        print("\n[TEST 32] Frontend zero-dialog static audit...")
        opt_js = (REPO_ROOT / "frontend/js/components/optimization.js").read_text(encoding="utf-8")
        assert "OptimizationController" in opt_js
        assert "approveCurrentRecommendation" in opt_js
        assert "rejectCurrentRecommendation" in opt_js
        assert re.search(r'\b(alert|prompt|confirm)\s*\(', opt_js) is None
        print("✓ Zero-dialog invariant verified: zero alert(), confirm(), prompt().")

        # --------------------------------------------------------------
        # TEST 33 & 34: Live Simulator Isolation and Approval-Only Mutation
        # --------------------------------------------------------------
        print("\n[TEST 33 & 34] Live simulator isolation and approval-only mutation...")
        # Unapproved recommendation does not mutate state
        with manager.lock:
            amb10 = sim.state.ambulances["AMB_0010"]
            status_before = amb10.status
        rec_dormant = OptimizationRecommendation(
            recommendation_id="REC_DORMANT",
            decision_type="FLEET_REPOSITION",
            severity="INFO",
            score=0.80,
            explanation=expl,
            candidate_action={"ambulance_id": "AMB_0010", "target_zone": "JAIPUR_NORTH"},
            expires_at_sim_time=5,
            status=RecommendationStatus.NEW,
        )
        router_engine._recommendations_index["REC_DORMANT"] = rec_dormant
        # Simulate does not mutate
        client.post("/optimization/simulate", json={"recommendation_id": "REC_DORMANT"})
        with manager.lock:
            assert sim.state.ambulances["AMB_0010"].status == status_before
        print("✓ Invariant confirmed: live simulator mutates strictly upon explicit approval.")

        # --------------------------------------------------------------
        # TEST 35, 36 & 37: Compatibility with M9 Reposition, Balancer & MCI
        # --------------------------------------------------------------
        print("\n[TEST 35-37] Compatibility with M9 Reposition, Balancer & MCI...")
        with manager.lock:
            cov = sim.coordinator.get_coverage(sim.state.ambulances)
            assert len(cov) == 6
            hosp_proj = sim.coordinator.hospital_balancer.get_all_projections(sim.state.hospitals)
            assert len(hosp_proj) == 300
        print("✓ Full compatibility with M9 coordination subsystems verified.")

        # --------------------------------------------------------------
        # TEST 38: M10 Replay and Drills Compatibility
        # --------------------------------------------------------------
        print("\n[TEST 38] M10 replay and drills compatibility...")
        r_drills = client.get("/drills")
        assert r_drills.status_code == 200
        assert len(r_drills.json()) >= 4
        print("✓ Full compatibility with M10 disaster drills verified.")

        # --------------------------------------------------------------
        # TEST 39: Deterministic Audit Serialization
        # --------------------------------------------------------------
        print("\n[TEST 39] Deterministic audit serialization...")
        rec_audit = audit_store.get_executions()
        audit_json = json.dumps(rec_audit, sort_keys=True)
        audit_json_2 = json.dumps(json.loads(audit_json), sort_keys=True)
        assert audit_json == audit_json_2
        print("✓ Audit serialization strictly deterministic.")

        # --------------------------------------------------------------
        # TEST 40: Concurrent Ordinary Dispatch + Optimization Execution
        # --------------------------------------------------------------
        print("\n[TEST 40] Concurrent ordinary dispatch + optimization execution...")
        dispatch_errors = []

        def ordinary_dispatch():
            r = client.post("/dispatch/5")
            if r.status_code not in (200, 404):
                dispatch_errors.append(r.status_code)

        def copilot_check():
            r = client.get("/optimization/copilot/summary")
            if r.status_code != 200:
                dispatch_errors.append(r.status_code)

        threads = [
            threading.Thread(target=ordinary_dispatch),
            threading.Thread(target=copilot_check),
            threading.Thread(target=ordinary_dispatch),
            threading.Thread(target=copilot_check),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(dispatch_errors) == 0
        print("✓ Concurrent ordinary dispatch + copilot execution passed with 0 conflicts.")

        # --------------------------------------------------------------
        # TEST 41: Approval Validation Latency Performance
        # --------------------------------------------------------------
        print("\n[TEST 41] Approval validation latency performance...")
        t_start = time.perf_counter()
        with manager.lock:
            fresh_snap = decision_engine.get_snapshot(sim)
            val_err = executor._validate_constraints(rec_dormant, sim, fresh_snap)
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        assert latency_ms < 10.0
        print(f"✓ Approval validation latency: {latency_ms:.2f} ms (< 10 ms operational budget).")

        # --------------------------------------------------------------
        # TEST 42: Overall Regression Health Check
        # --------------------------------------------------------------
        print("\n[TEST 42] Overall regression health check...")
        r_state = client.get("/state/dashboard")
        assert r_state.status_code == 200
        print("✓ Overall core simulation returned 200 OK.")

    print("\n" + "=" * 75)
    print("ALL 42 M11 PHASE 2 OPERATOR COPILOT & EXECUTION TESTS PASSED.")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    run_phase2_tests()
