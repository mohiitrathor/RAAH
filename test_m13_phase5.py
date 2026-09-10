"""
RAAH Milestone 13 Phase 3 Acceptance Test Suite
================================================
Validates M13.3 Phase 3: Command Center Decision Explanation Panel & Operator Audit Inspector

Scope & Invariants:
1. API client methods exist and use correct endpoints.
2. Evidence detail request handling.
3. Incident evidence request handling.
4. Recent evidence request handling.
5. 404 handling for nonexistent evidence.
6. 422 limit validation handling.
7. Decision feed renders multiple decision types.
8. Feed deduplicates by evidence_id.
9. Feed remains bounded to 20 visible records.
10. Detail drawer requests incident evidence.
11. "Inspect Rationale" opens the explanation modal.
12. Modal renders deterministic explanation content.
13. Modal renders clinical evidence when present.
14. Modal renders selected ambulance/hospital evidence when present.
15. Modal correctly renders: alternatives_available = false without inventing alternatives.
16. Empty/legacy incident state is handled gracefully.
17. API failure state is handled gracefully.
18. HTML contains required modal/container/script wiring.
19. JavaScript syntax/static integrity checks.
20. No Phase 3 frontend action mutates DispatchState or calls mutation endpoints.
21. Existing SSE schema is unchanged.
22. No new polling interval is introduced for decision evidence.
"""

import os
import re
import sys
import unittest

# Ensure root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import manager
from api.auth import create_test_token, Role
from api.realtime.models import EventType, RealtimeEvent
from api.decision_evidence import evidence_store
from api.decision_evidence.models import DecisionType, DecisionEvidenceRecord, ConstraintEvaluation, CandidateAlternative


class TestM13Phase3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        manager.initialize()
        cls.admin_token = create_test_token(role=Role.ADMINISTRATOR, username="commander_audit")
        cls.dispatcher_token = create_test_token(role=Role.DISPATCHER, username="tactical_dispatcher")
        cls.controller_token = create_test_token(role=Role.MEDICAL_CONTROLLER, username="med_controller")
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.dispatcher_headers = {"Authorization": f"Bearer {cls.dispatcher_token}"}
        cls.controller_headers = {"Authorization": f"Bearer {cls.controller_token}"}

        cls.frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
        cls.js_dir = os.path.join(cls.frontend_dir, "js")
        cls.components_dir = os.path.join(cls.js_dir, "components")
        cls.css_dir = os.path.join(cls.frontend_dir, "css")

    def setUp(self):
        manager.reset()
        evidence_store.clear()

    # -------------------------------------------------------------
    # 1. API client methods exist and use correct endpoints
    # -------------------------------------------------------------
    def test_01_api_client_methods_exist_and_use_correct_endpoints(self):
        api_path = os.path.join(self.js_dir, "api.js")
        self.assertTrue(os.path.exists(api_path), "frontend/js/api.js not found")
        with open(api_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("getDecisionEvidence", content)
        self.assertIn("getIncidentDecisionEvidence", content)
        self.assertIn("getRecentDecisionEvidence", content)

        self.assertIn("/decision-evidence/", content)
        self.assertIn("/decision-evidence/incident/", content)
        self.assertIn("/decision-evidence/recent", content)

    # -------------------------------------------------------------
    # 2. Evidence detail request handling
    # -------------------------------------------------------------
    def test_02_evidence_detail_request_handling(self):
        # Dispatch incident 1 to generate authoritative evidence
        dispatch_resp = self.client.post("/dispatch/1", headers=self.admin_headers)
        self.assertEqual(dispatch_resp.status_code, 200)

        # Get evidence ID from store
        records = evidence_store.get_by_incident(1)
        self.assertGreaterEqual(len(records), 1)
        evidence_id = records[0].evidence_id

        # Query GET /decision-evidence/{evidence_id}
        resp = self.client.get(f"/decision-evidence/{evidence_id}", headers=self.dispatcher_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("evidence", data)
        self.assertIn("explanation", data)
        self.assertEqual(data["evidence"]["evidence_id"], evidence_id)
        self.assertEqual(data["evidence"]["incident_id"], 1)
        self.assertIsInstance(data["explanation"], str)
        self.assertGreater(len(data["explanation"]), 10)

    # -------------------------------------------------------------
    # 3. Incident evidence request handling
    # -------------------------------------------------------------
    def test_03_incident_evidence_request_handling(self):
        self.client.post("/dispatch/1", headers=self.admin_headers)
        resp = self.client.get("/decision-evidence/incident/1", headers=self.dispatcher_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["incident_id"], 1)
        self.assertIsInstance(data["records"], list)
        self.assertGreaterEqual(len(data["records"]), 1)
        self.assertEqual(data["records"][0]["evidence"]["incident_id"], 1)

    # -------------------------------------------------------------
    # 4. Recent evidence request handling
    # -------------------------------------------------------------
    def test_04_recent_evidence_request_handling(self):
        self.client.post("/dispatch/1", headers=self.admin_headers)
        self.client.post("/dispatch/2", headers=self.admin_headers)

        resp = self.client.get("/decision-evidence/recent?limit=20", headers=self.dispatcher_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("count", data)
        self.assertIn("records", data)
        self.assertEqual(data["count"], len(data["records"]))
        self.assertGreaterEqual(data["count"], 2)

    # -------------------------------------------------------------
    # 5. 404 handling for nonexistent evidence
    # -------------------------------------------------------------
    def test_05_404_handling_for_nonexistent_evidence(self):
        resp = self.client.get("/decision-evidence/00000000-0000-0000-0000-000000000000", headers=self.dispatcher_headers)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("detail", resp.json())

    # -------------------------------------------------------------
    # 6. 422 limit validation handling
    # -------------------------------------------------------------
    def test_06_422_limit_validation_handling(self):
        resp_zero = self.client.get("/decision-evidence/recent?limit=0", headers=self.dispatcher_headers)
        self.assertEqual(resp_zero.status_code, 422)

        resp_over = self.client.get("/decision-evidence/recent?limit=501", headers=self.dispatcher_headers)
        self.assertEqual(resp_over.status_code, 422)

    # -------------------------------------------------------------
    # 7. Decision feed renders multiple decision types
    # -------------------------------------------------------------
    def test_07_decision_feed_renders_multiple_decision_types(self):
        decisions_path = os.path.join(self.components_dir, "decisions.js")
        with open(decisions_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("decision_type", content)
        self.assertIn("decision-pill", content)
        self.assertIn("pill-", content)

        css_path = os.path.join(self.css_dir, "command_center.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css_content = f.read()

        self.assertIn("pill-initial-dispatch", css_content)
        self.assertIn("pill-hospital-redirection", css_content)
        self.assertIn("pill-fleet-reposition", css_content)
        self.assertIn("pill-hospital-diversion", css_content)

    # -------------------------------------------------------------
    # 8. Feed deduplicates by evidence_id
    # -------------------------------------------------------------
    def test_08_feed_deduplicates_by_evidence_id(self):
        decisions_path = os.path.join(self.components_dir, "decisions.js")
        with open(decisions_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("evidence_id", content)
        # Check Map or Set or deduplication check
        self.assertTrue("dedupMap" in content or "has(" in content or "filter" in content)

    # -------------------------------------------------------------
    # 9. Feed remains bounded to 20 visible records
    # -------------------------------------------------------------
    def test_09_feed_remains_bounded_to_20_visible_records(self):
        decisions_path = os.path.join(self.components_dir, "decisions.js")
        with open(decisions_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(".slice(0, 20)", content)

    # -------------------------------------------------------------
    # 10. Detail drawer requests incident evidence
    # -------------------------------------------------------------
    def test_10_detail_drawer_requests_incident_evidence(self):
        drawer_path = os.path.join(self.components_dir, "detail_drawer.js")
        with open(drawer_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("getIncidentDecisionEvidence", content)
        self.assertIn("Decision Evidence &amp; Audit Trail", content)
        self.assertIn("btn-inspect-evidence", content)

    # -------------------------------------------------------------
    # 11. "Inspect Rationale" opens the explanation modal
    # -------------------------------------------------------------
    def test_11_inspect_rationale_opens_explanation_modal(self):
        drawer_path = os.path.join(self.components_dir, "detail_drawer.js")
        with open(drawer_path, "r", encoding="utf-8") as f:
            drawer_content = f.read()

        self.assertIn("openExplanationModal", drawer_content)
        self.assertIn("Inspect Rationale", drawer_content)

        decisions_path = os.path.join(self.components_dir, "decisions.js")
        with open(decisions_path, "r", encoding="utf-8") as f:
            decisions_content = f.read()

        self.assertIn("openExplanationModal", decisions_content)
        self.assertIn("btn-feed-inspect", decisions_content)

    # -------------------------------------------------------------
    # 12. Modal renders deterministic explanation content
    # -------------------------------------------------------------
    def test_12_modal_renders_deterministic_explanation_content(self):
        modal_path = os.path.join(self.components_dir, "explanation_modal.js")
        self.assertTrue(os.path.exists(modal_path), "explanation_modal.js not found")
        with open(modal_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("explanation-highlight-box", content)
        self.assertIn("Deterministic Rationale Summary", content)
        self.assertIn("sim_time", content)
        self.assertIn("decision_type", content)
        self.assertIn("action", content)
        self.assertIn("evidence_id", content)

    # -------------------------------------------------------------
    # 13. Modal renders clinical evidence when present
    # -------------------------------------------------------------
    def test_13_modal_renders_clinical_evidence_when_present(self):
        modal_path = os.path.join(self.components_dir, "explanation_modal.js")
        with open(modal_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Clinical Triage &amp; Assessment Evidence", content)
        self.assertIn("patient_condition", content)
        self.assertIn("severity", content)
        self.assertIn("priority", content)
        self.assertIn("confidence", content)
        self.assertIn("severity_source", content)

    # -------------------------------------------------------------
    # 14. Modal renders selected ambulance/hospital evidence when present
    # -------------------------------------------------------------
    def test_14_modal_renders_selected_ambulance_hospital_evidence_when_present(self):
        modal_path = os.path.join(self.components_dir, "explanation_modal.js")
        with open(modal_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Selected Units &amp; Routing Evidence", content)
        self.assertIn("selected_ambulance_id", content)
        self.assertIn("selected_ambulance_type", content)
        self.assertIn("eta_minutes", content)
        self.assertIn("distance_km", content)
        self.assertIn("selected_hospital_id", content)
        self.assertIn("selected_hospital_type", content)
        self.assertIn("hospital_available_beds", content)
        self.assertIn("hospital_available_icu", content)
        self.assertIn("hospital_suitability", content)

    # -------------------------------------------------------------
    # 15. Modal correctly renders: alternatives_available = false
    # -------------------------------------------------------------
    def test_15_modal_correctly_renders_alternatives_unavailable_without_inventing(self):
        modal_path = os.path.join(self.components_dir, "explanation_modal.js")
        with open(modal_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("alternatives_available === true", content)
        self.assertIn("Alternatives not available from the dispatch evidence source.", content)
        self.assertIn("alternatives-unavailable-box", content)

    # -------------------------------------------------------------
    # 16. Empty/legacy incident state is handled gracefully
    # -------------------------------------------------------------
    def test_16_empty_legacy_incident_state_handled_gracefully(self):
        drawer_path = os.path.join(self.components_dir, "detail_drawer.js")
        with open(drawer_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("No decision evidence recorded for this incident", content)

        decisions_path = os.path.join(self.components_dir, "decisions.js")
        with open(decisions_path, "r", encoding="utf-8") as f:
            d_content = f.read()

        self.assertIn("No operational decisions recorded yet", d_content)

    # -------------------------------------------------------------
    # 17. API failure state is handled gracefully
    # -------------------------------------------------------------
    def test_17_api_failure_state_handled_gracefully(self):
        modal_path = os.path.join(self.components_dir, "explanation_modal.js")
        with open(modal_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("renderError", content)
        self.assertIn("evidence-error-box", content)

        drawer_path = os.path.join(self.components_dir, "detail_drawer.js")
        with open(drawer_path, "r", encoding="utf-8") as f:
            d_content = f.read()

        self.assertIn("evidenceError", d_content)

    # -------------------------------------------------------------
    # 18. HTML contains required modal/container/script wiring
    # -------------------------------------------------------------
    def test_18_html_contains_required_modal_container_and_script(self):
        index_path = os.path.join(self.frontend_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('id="modal-decision-explanation"', content)
        self.assertIn('src="js/components/explanation_modal.js"', content)
        self.assertIn('id="decisions-tbody"', content)
        self.assertIn('id="drawer-incident-detail"', content)

    # -------------------------------------------------------------
    # 19. JavaScript syntax/static integrity checks
    # -------------------------------------------------------------
    def test_19_javascript_static_integrity_checks(self):
        js_files = [
            os.path.join(self.js_dir, "api.js"),
            os.path.join(self.components_dir, "explanation_modal.js"),
            os.path.join(self.components_dir, "detail_drawer.js"),
            os.path.join(self.components_dir, "decisions.js"),
        ]

        for filepath in js_files:
            self.assertTrue(os.path.exists(filepath), f"Missing JS file: {filepath}")
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()

            # Balanced delimiters check (excluding strings/comments)
            # Basic sanity check on braces and parenthesis counts
            # Strip single line comments and multi-line comments
            stripped = re.sub(r'//.*', '', code)
            stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)

            open_braces = stripped.count('{')
            close_braces = stripped.count('}')
            self.assertEqual(open_braces, close_braces, f"Mismatched braces in {os.path.basename(filepath)}: {open_braces} vs {close_braces}")

            open_parens = stripped.count('(')
            close_parens = stripped.count(')')
            self.assertEqual(open_parens, close_parens, f"Mismatched parens in {os.path.basename(filepath)}: {open_parens} vs {close_parens}")

            open_brackets = stripped.count('[')
            close_brackets = stripped.count(']')
            self.assertEqual(open_brackets, close_brackets, f"Mismatched brackets in {os.path.basename(filepath)}: {open_brackets} vs {close_brackets}")

    # -------------------------------------------------------------
    # 20. No Phase 3 frontend action mutates DispatchState
    # -------------------------------------------------------------
    def test_20_no_phase3_frontend_action_mutates_dispatch_state(self):
        # Pre-populate state with an incident dispatch
        self.client.post("/dispatch/1", headers=self.admin_headers)
        sim = manager.simulator
        ambulances_before = len(sim.state.ambulances)
        incidents_before = len(sim.state.incidents)
        time_before = sim.state.current_time

        # Perform multiple read-only explanation queries (simulating operator inspecting modal and drawer)
        records = evidence_store.get_by_incident(1)
        self.assertGreaterEqual(len(records), 1)
        eid = records[0].evidence_id

        for _ in range(5):
            r1 = self.client.get(f"/decision-evidence/{eid}", headers=self.dispatcher_headers)
            self.assertEqual(r1.status_code, 200)
            r2 = self.client.get("/decision-evidence/incident/1", headers=self.dispatcher_headers)
            self.assertEqual(r2.status_code, 200)
            r3 = self.client.get("/decision-evidence/recent?limit=20", headers=self.dispatcher_headers)
            self.assertEqual(r3.status_code, 200)

        self.assertEqual(sim.state.current_time, time_before, "Simulation time mutated by read-only queries!")
        self.assertEqual(len(sim.state.incidents), incidents_before, "Incidents mutated!")
        self.assertEqual(len(sim.state.ambulances), ambulances_before, "Ambulances mutated!")

    # -------------------------------------------------------------
    # 21. Existing SSE schema is unchanged
    # -------------------------------------------------------------
    def test_21_existing_sse_schema_is_unchanged(self):
        expected_event_types = {
            "STATE_SNAPSHOT",
            "TICK",
            "INCIDENT_DISPATCHED",
            "AMBULANCE_UPDATE",
            "REDIRECTION_EXECUTED",
            "MCI_ALERT",
            "HOSPITAL_UPDATE",
            "SYSTEM_ALERT",
            "HEARTBEAT",
        }
        actual_event_types = {e.value for e in EventType}
        self.assertEqual(expected_event_types, actual_event_types, "EventType enum was modified!")

        # Verify RealtimeEvent fields
        fields = RealtimeEvent.model_fields.keys()
        self.assertIn("schema_version", fields)
        self.assertIn("event_id", fields)
        self.assertIn("event_type", fields)
        self.assertIn("occurred_at", fields)
        self.assertIn("simulation_time", fields)
        self.assertIn("sequence", fields)
        self.assertIn("payload", fields)

    # -------------------------------------------------------------
    # 22. No new polling interval is introduced for decision evidence
    # -------------------------------------------------------------
    def test_22_no_new_polling_interval_introduced(self):
        phase3_files = [
            os.path.join(self.components_dir, "explanation_modal.js"),
            os.path.join(self.components_dir, "detail_drawer.js"),
            os.path.join(self.components_dir, "decisions.js"),
        ]

        for filepath in phase3_files:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertNotIn("setInterval", content, f"Disallowed setInterval found in {os.path.basename(filepath)}")


if __name__ == "__main__":
    unittest.main()
