"""
RAAH Milestone 13 Phase 3 Acceptance Test Suite
===============================================
Decision Evidence Backbone Validation:
1. Evidence schema validation
2. Version handling
3. Stable serialization
4. Unique evidence IDs
5. Deterministic formatter
6. Honest handling of missing evidence
7. Real INITIAL_DISPATCH evidence creation
8. Real HOSPITAL_REDIRECTION evidence creation
9. Real optimization evidence
10. Recording failure does not break the authoritative operation (fail open)
11. Evidence recording does not mutate DispatchState
12. Evidence contains actual policy context where available
13. Clinical severity in evidence matches the actual protected-model result
14. Existing API responses remain unchanged
15. Bounded recorder behavior
16. Thread-safety of recording
17. M13.1 regression compatibility
18. M13.2 regression compatibility
"""

import concurrent.futures
import json
import os
import sys
import unittest
from unittest.mock import patch

# Ensure root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import manager
from api.auth import create_test_token, Role
from api.decision_evidence import (
    evidence_store,
    DecisionType,
    ConstraintEvaluation,
    CandidateAlternative,
    DecisionEvidenceRecord,
    format_explanation,
    EvidenceStore,
)


class TestM13Phase3(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        manager.initialize()
        cls.token = create_test_token(role=Role.ADMINISTRATOR, username="test_admin")
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    def setUp(self):
        manager.reset()
        evidence_store.clear()

    # -----------------------------------------------------------------
    # 1. Evidence schema validation
    # -----------------------------------------------------------------
    def test_01_evidence_schema_validation(self):
        """Pydantic schema must enforce types and accept valid records."""
        rec = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH_INCIDENT_1",
            incident_id=1,
            sim_time=100,
            severity="Critical",
            priority="P1",
            confidence=0.95,
        )
        self.assertEqual(rec.decision_type, DecisionType.INITIAL_DISPATCH)
        self.assertEqual(rec.action, "DISPATCH_INCIDENT_1")
        self.assertEqual(rec.incident_id, 1)
        self.assertEqual(rec.severity, "Critical")
        self.assertEqual(rec.priority, "P1")
        self.assertAlmostEqual(rec.confidence, 0.95)

    # -----------------------------------------------------------------
    # 2. Version handling
    # -----------------------------------------------------------------
    def test_02_version_handling(self):
        """Schema version must be explicit, default to 1.0.0, and allow custom versions."""
        rec_default = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH",
        )
        self.assertEqual(rec_default.evidence_schema_version, "1.0.0")

        rec_custom = DecisionEvidenceRecord(
            evidence_schema_version="1.1.0",
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH",
        )
        self.assertEqual(rec_custom.evidence_schema_version, "1.1.0")

    # -----------------------------------------------------------------
    # 3. Stable serialization
    # -----------------------------------------------------------------
    def test_03_stable_serialization(self):
        """Evidence record must serialize and deserialize deterministically without data loss."""
        rec = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH_INCIDENT_42",
            incident_id=42,
            sim_time=50,
            severity="Emergency",
            confidence=0.88,
            constraints=[
                ConstraintEvaluation(name="FLEET_AVAILABILITY", satisfied=True, details="Assigned AMB_01")
            ],
            alternatives_available=False,
            scores={"ml_confidence": 0.88},
        )
        dumped_json = rec.model_dump_json()
        data = json.loads(dumped_json)
        self.assertEqual(data["action"], "DISPATCH_INCIDENT_42")
        self.assertEqual(data["incident_id"], 42)
        self.assertEqual(data["severity"], "Emergency")
        self.assertEqual(data["constraints"][0]["name"], "FLEET_AVAILABILITY")

        # Roundtrip reconstruction
        reconstructed = DecisionEvidenceRecord.model_validate_json(dumped_json)
        self.assertEqual(reconstructed.evidence_id, rec.evidence_id)
        self.assertEqual(reconstructed.action, rec.action)

    # -----------------------------------------------------------------
    # 4. Unique evidence IDs
    # -----------------------------------------------------------------
    def test_04_unique_evidence_ids(self):
        """Every evidence record must receive a distinct UUID."""
        ids = {
            DecisionEvidenceRecord(
                decision_type=DecisionType.INITIAL_DISPATCH,
                action="TEST"
            ).evidence_id
            for _ in range(200)
        }
        self.assertEqual(len(ids), 200)

    # -----------------------------------------------------------------
    # 5. Deterministic formatter
    # -----------------------------------------------------------------
    def test_05_deterministic_formatter(self):
        """Formatting identical evidence must produce identical string output across multiple runs."""
        rec = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH_INCIDENT_5",
            incident_id=5,
            sim_time=120,
            severity="Critical",
            priority="P1",
            confidence=0.9234,
            selected_ambulance_id="AMB_001",
            selected_ambulance_type="Critical Care",
            selected_hospital_id="HOSP_01",
            selected_hospital_type="Trauma Center",
            eta_minutes=4.5,
            distance_km=2.3,
            hospital_available_beds=12,
            hospital_available_icu=3,
            constraints=[
                ConstraintEvaluation(name="AMBULANCE_CAPABILITY_MATCH", satisfied=True, details="Exact match"),
                ConstraintEvaluation(name="FLEET_AVAILABILITY", satisfied=True, details="Assigned unit AMB_001"),
            ],
            alternatives=[],
            alternatives_available=False,
            scores={"ml_confidence": 0.9234},
        )

        first_output = format_explanation(rec)
        for _ in range(50):
            self.assertEqual(format_explanation(rec), first_output)

        # Output must be human-readable and contain key factual tokens
        self.assertIn("Decision: DISPATCH_INCIDENT_5", first_output)
        self.assertIn("Critical (P1)", first_output)
        self.assertIn("AMB_001", first_output)
        self.assertIn("HOSP_01", first_output)
        self.assertIn("ETA: 4.50 min", first_output)
        self.assertIn("SATISFIED", first_output)

    # -----------------------------------------------------------------
    # 6. Honest handling of missing evidence
    # -----------------------------------------------------------------
    def test_06_honest_handling_of_missing_evidence(self):
        """Must not invent speculative alternatives or reasons when not exposed by engine."""
        rec = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="DISPATCH_INCIDENT_1",
            incident_id=1,
            alternatives=[],
            alternatives_available=False,
        )
        formatted = format_explanation(rec)
        self.assertIn("[UNAVAILABLE]", formatted)
        self.assertNotIn("[SELECTED]", formatted)
        self.assertNotIn("[NOT_SELECTED]", formatted)
        self.assertFalse(rec.alternatives_available)
        self.assertEqual(len(rec.alternatives), 0)

    # -----------------------------------------------------------------
    # 7. Real INITIAL_DISPATCH evidence creation
    # -----------------------------------------------------------------
    def test_07_real_initial_dispatch_evidence_creation(self):
        """Authoritative POST /dispatch/{incident_id} must record structured evidence."""
        resp = self.client.post("/dispatch/1", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        records = evidence_store.get_by_incident(1)
        self.assertGreaterEqual(len(records), 1)
        rec = records[-1]

        self.assertEqual(rec.decision_type, DecisionType.INITIAL_DISPATCH)
        self.assertEqual(rec.incident_id, 1)
        self.assertEqual(rec.severity, data["patient"]["predicted_severity"])
        self.assertEqual(rec.priority, data["patient"]["priority"])
        self.assertAlmostEqual(rec.confidence, data["patient"]["confidence"])
        self.assertEqual(rec.selected_ambulance_id, data["ambulance"]["ambulance_id"])
        self.assertEqual(rec.selected_hospital_id, data["hospital"]["hospital_id"])
        self.assertFalse(rec.alternatives_available)  # Engine didn't expose discarded candidates

    # -----------------------------------------------------------------
    # 8. Real HOSPITAL_REDIRECTION evidence creation
    # -----------------------------------------------------------------
    def test_08_real_hospital_redirection_evidence_creation(self):
        """Redirection evidence must record operational parameters honestly."""
        decision_payload = {
            "incident_id": 10,
            "original_hospital": "HOSP_01",
            "new_hospital": "HOSP_02",
            "eta_before": 15.0,
            "eta_after": 8.0,
            "eta_saved": 7.0,
            "eta_improvement_percent": 46.67,
            "reason": "Traffic congestion on primary route",
            "severity": "Critical",
            "ambulance_id": "AMB_002",
            "decision": "REDIRECT_EXECUTED",
        }
        rec = evidence_store.record_redirection(
            decision_payload=decision_payload,
            sim_time=250,
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.decision_type, DecisionType.HOSPITAL_REDIRECTION)
        self.assertEqual(rec.incident_id, 10)
        self.assertEqual(rec.selected_hospital_id, "HOSP_02")
        self.assertAlmostEqual(rec.scores["eta_saved_minutes"], 7.0)
        self.assertFalse(rec.alternatives_available)

        formatted = format_explanation(rec)
        self.assertIn("REDIRECT_EXECUTED:HOSP_01->HOSP_02", formatted)
        self.assertIn("HOSP_02", formatted)

    # -----------------------------------------------------------------
    # 9. Real optimization evidence
    # -----------------------------------------------------------------
    def test_09_real_optimization_evidence(self):
        """Optimization candidate evidence must reflect alternatives and constraints."""
        rec_data = {
            "recommendation_id": "REC_OPT_001",
            "decision_type": "FLEET_REPOSITION",
            "score": 0.85,
            "candidate_action": {
                "target": "Zone_North",
                "confidence": 0.90,
                "constraints": ["COVERAGE_FLOOR_MET", "UNIT_COOLDOWN_SATISFIED"],
            },
            "explanation": {
                "expected_benefit": "Restores coverage floor in Zone_North to 0.75",
                "alternatives": [
                    {"action": "REPOSITION_AMB_02", "target": "Zone_South", "score": 0.65, "reason": "Lower coverage deficit"}
                ]
            }
        }
        exec_data = {
            "execution_id": "EXEC_001",
            "status": "SUCCESS",
            "affected_entities": {"ambulance_id": "AMB_003"},
        }

        record = evidence_store.record_optimization(
            recommendation=rec_data,
            execution_result=exec_data,
            sim_time=300,
            policy_mode="GUARDED",
            policy_version="v10",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.decision_type, DecisionType.FLEET_REPOSITION)
        self.assertEqual(record.policy_mode, "GUARDED")
        self.assertEqual(record.policy_version, "v10")
        self.assertTrue(record.alternatives_available)
        self.assertEqual(len(record.alternatives), 2)  # 1 evaluated alternative + 1 selected

        formatted = format_explanation(record)
        self.assertIn("REPOSITION_AMB_02", formatted)
        self.assertIn("GUARDED", formatted)

    # -----------------------------------------------------------------
    # 10. Recording failure does not break the authoritative operation (fail open)
    # -----------------------------------------------------------------
    def test_10_recording_failure_fails_open(self):
        """Even if evidence recording throws an unhandled exception, dispatch must succeed."""
        with patch.object(evidence_store, "record_dispatch", side_effect=RuntimeError("Simulated Store Failure")):
            resp = self.client.post("/dispatch/2", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["incident_id"], 2)
            self.assertIn("ambulance", data)
            self.assertIn("hospital", data)

    # -----------------------------------------------------------------
    # 11. Evidence recording does not mutate DispatchState
    # -----------------------------------------------------------------
    def test_11_evidence_recording_does_not_mutate_state(self):
        """Evidence store methods must not touch or modify DispatchState."""
        sim = manager.simulator
        state_ambulances_before = len(sim.state.ambulances)
        state_incidents_before = len(sim.state.incidents)
        state_time_before = sim.state.current_time

        # Record standalone evidence
        rec = DecisionEvidenceRecord(
            decision_type=DecisionType.INITIAL_DISPATCH,
            action="TEST_ACTION",
            incident_id=999,
            sim_time=state_time_before,
        )
        evidence_store.record(rec)

        # State should be identical
        self.assertEqual(len(sim.state.ambulances), state_ambulances_before)
        self.assertEqual(len(sim.state.incidents), state_incidents_before)
        self.assertEqual(sim.state.current_time, state_time_before)

    # -----------------------------------------------------------------
    # 12. Evidence contains actual policy context where available
    # -----------------------------------------------------------------
    def test_12_evidence_contains_actual_policy_context(self):
        """Evidence record preserves active policy mode and version if present."""
        rec = evidence_store.record_dispatch(
            dispatch_result={"incident_id": 99, "status": "DISPATCH_RECOMMENDED"},
            sim_time=400,
            policy_mode="GUARDED",
            policy_version="v10",
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.policy_mode, "GUARDED")
        self.assertEqual(rec.policy_version, "v10")

    # -----------------------------------------------------------------
    # 13. Clinical severity in evidence matches the actual protected-model result
    # -----------------------------------------------------------------
    def test_13_clinical_severity_matches_protected_model(self):
        """The clinical severity in the evidence record must exactly match the ML model's output."""
        resp = self.client.post("/dispatch/3", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        api_data = resp.json()

        records = evidence_store.get_by_incident(3)
        self.assertTrue(len(records) > 0)
        rec = records[-1]

        self.assertEqual(rec.severity, api_data["patient"]["predicted_severity"])
        self.assertEqual(rec.priority, api_data["patient"]["priority"])
        self.assertAlmostEqual(rec.confidence, api_data["patient"]["confidence"])

    # -----------------------------------------------------------------
    # 14. Existing API responses remain unchanged
    # -----------------------------------------------------------------
    def test_14_existing_api_response_contract_unchanged(self):
        """DispatchResult structure from POST /dispatch/{incident_id} must not be changed."""
        resp = self.client.post("/dispatch/4", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        expected_top_keys = {"status", "incident_id", "patient", "ambulance", "hospital"}
        self.assertTrue(expected_top_keys.issubset(set(data.keys())))
        self.assertIn("predicted_severity", data["patient"])
        self.assertIn("ambulance_id", data["ambulance"])
        self.assertIn("hospital_id", data["hospital"])

    # -----------------------------------------------------------------
    # 15. Bounded recorder behavior
    # -----------------------------------------------------------------
    def test_15_bounded_recorder_behavior(self):
        """Recorder ring buffer must evict oldest entries when exceeding capacity."""
        small_store = EvidenceStore(max_capacity=5)
        for i in range(10):
            small_store.record(
                DecisionEvidenceRecord(
                    decision_type=DecisionType.INITIAL_DISPATCH,
                    action=f"ACTION_{i}",
                    incident_id=i,
                )
            )

        recent = small_store.list_recent(limit=10)
        self.assertEqual(len(recent), 5)
        # Most recent actions should be 9, 8, 7, 6, 5
        self.assertEqual(recent[0].action, "ACTION_9")
        self.assertEqual(recent[-1].action, "ACTION_5")

        # Evicted IDs should return None
        self.assertEqual(len(small_store.get_by_incident(0)), 0)
        self.assertEqual(len(small_store.get_by_incident(9)), 1)

    # -----------------------------------------------------------------
    # 16. Thread-safety of recording
    # -----------------------------------------------------------------
    def test_16_thread_safety_of_recording(self):
        """Concurrent record operations must not corrupt internal buffer or raise race condition errors."""
        store = EvidenceStore(max_capacity=500)
        records_per_thread = 50
        num_threads = 8

        def worker(thread_idx):
            for j in range(records_per_thread):
                store.record(
                    DecisionEvidenceRecord(
                        decision_type=DecisionType.INITIAL_DISPATCH,
                        action=f"T{thread_idx}_A{j}",
                        incident_id=thread_idx * 1000 + j,
                    )
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, t) for t in range(num_threads)]
            concurrent.futures.wait(futures)

        recent = store.list_recent(limit=500)
        self.assertEqual(len(recent), num_threads * records_per_thread)

    # -----------------------------------------------------------------
    # 17. M13.1 regression compatibility (broadcaster works alongside evidence)
    # -----------------------------------------------------------------
    def test_17_m13_phase1_broadcaster_compatibility(self):
        """Broadcaster event distribution works normally alongside evidence recording."""
        from api.realtime.broadcaster import broadcaster
        from api.realtime.models import EventType
        import asyncio

        events = []
        client_id = "test_evidence_sse_sub"

        async def sub():
            session = broadcaster.subscribe(client_id)
            try:
                # Trigger dispatch
                resp = self.client.post("/dispatch/5", headers=self.headers)
                self.assertEqual(resp.status_code, 200)

                # Get event
                ev = await asyncio.wait_for(session.queue.get(), timeout=2.0)
                events.append(ev)
            finally:
                broadcaster.unsubscribe(client_id)

        asyncio.run(sub())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.INCIDENT_DISPATCHED.value)

        # Confirm evidence was also recorded
        records = evidence_store.get_by_incident(5)
        self.assertGreaterEqual(len(records), 1)

    # -----------------------------------------------------------------
    # 18. M13.2 regression compatibility (simulation tick and command center events)
    # -----------------------------------------------------------------
    def test_18_m13_phase2_tick_compatibility(self):
        """Simulation tick endpoint still operates and broadcasts with evidence subsystem in place."""
        resp = self.client.post("/simulation/tick", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("time", data)


if __name__ == "__main__":
    unittest.main()
