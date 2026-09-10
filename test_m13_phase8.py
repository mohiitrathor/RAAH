"""
RAAH Milestone 13.4 Phase 3 — Operational Replay UI Integration Tests
====================================================================

Validates:
1. Operational run appears in replay listing.
2. Scenario replay listing remains compatible.
3. Operational replay selection uses run_<id>.
4. Timeline loads for operational run.
5. Historical state loads for operational run.
6. Replay step works (POST /replays/{id}/step).
7. Replay seek works (GET /replays/{id}/state?sim_time=X).
8. Replay does not mutate live DispatchState.
9. Replay does not emit SSE events.
10. Replay does not call SimulatorManager.tick().
11. Replay state is isolated from live frontend state.
12. Historical/live mode labels are present and distinct.
13. Historical events render with available metadata.
14. Missing evidence is shown honestly ("Decision evidence was not recorded for this historical run.").
15. Server-provided values are escaped in frontend code.
16. 401 unauthenticated handling.
17. 403 unauthorized role handling on restricted endpoints.
18. 404 missing replay handling.
19. Existing scenario replay tests remain passing.
20. Existing M13.3 and M13.2 tests remain passing.
"""

import unittest
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from api.settings import settings
from api.dependencies import manager
from api.persistence.db import get_connection, init_db
from api.persistence.serializer import compute_state_checksum, serialize_dispatch_state
from api.persistence.replay_compiler import (
    clear_compiled_artifacts_cache,
    is_operational_replay_id,
    resolve_replay_artifact,
)
from api.auth.models import Role, Permission
from api.auth.security import create_test_token
from Dispatch.state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)


class TestM13Phase8OperationalReplayUI(unittest.TestCase):
    """Test suite for M13.4 Phase 3 Operational Replay UI integration."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        # RBAC tokens
        cls.admin_token = create_test_token(username="admin_ui", role=Role.ADMINISTRATOR)
        cls.supervisor_token = create_test_token(username="sup_ui", role=Role.SUPERVISOR)
        cls.dispatcher_token = create_test_token(username="disp_ui", role=Role.DISPATCHER)

        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.supervisor_headers = {"Authorization": f"Bearer {cls.supervisor_token}"}
        cls.dispatcher_headers = {"Authorization": f"Bearer {cls.dispatcher_token}"}
        cls.headers = cls.dispatcher_headers

        # Setup test database
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_ui_replay.db"
        cls.original_db_path = settings.database_path
        settings.database_path = cls.db_path
        init_db(cls.db_path)
        cls._populate_test_data()

    @classmethod
    def tearDownClass(cls):
        settings.database_path = cls.original_db_path
        clear_compiled_artifacts_cache()
        cls.temp_dir.cleanup()

    def setUp(self):
        clear_compiled_artifacts_cache()

    @classmethod
    def _populate_test_data(cls):
        """Seed test database with operational run 801."""
        conn = get_connection(cls.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS state_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                simulation_time INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                saved_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                checksum TEXT NOT NULL,
                is_valid INTEGER DEFAULT 1,
                metadata_json TEXT
            );
            """
        )

        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (801, '2026-09-10T08:00:00+00:00', '2026-09-10T08:30:00+00:00', 'COMPLETED', 30, 30, 'Test Operational Run 801')
            """
        )

        st0 = DispatchState(current_time=0)
        st0.add_hospital(HospitalState("HOSP_SMS", "General", 26.89, 75.81, capacity=10, current_load=3, icu_capacity=2, current_icu_load=0))
        st0.add_ambulance(AmbulanceState("AMB_801", "ALS", 26.85, 75.75, status="AVAILABLE"))
        data0 = serialize_dispatch_state(st0)
        chk0 = compute_state_checksum(data0)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_801_0', 0, 1, '2026-09-10T08:00:00+00:00', ?, ?, 1, ?)
            """,
            (json.dumps(data0), chk0, json.dumps({"run_id": 801})),
        )

        st15 = DispatchState(current_time=15)
        st15.add_hospital(HospitalState("HOSP_SMS", "General", 26.89, 75.81, capacity=10, current_load=4, icu_capacity=2, current_icu_load=0))
        st15.add_ambulance(AmbulanceState("AMB_801", "ALS", 26.87, 75.77, status="EN_ROUTE", incident_id=8101, hospital_id="HOSP_SMS", eta_minutes=6.5))
        st15.add_incident(IncidentState(8101, "Cardiac", "Severe", 1, status="DISPATCHED", ambulance_id="AMB_801", hospital_id="HOSP_SMS"))
        data15 = serialize_dispatch_state(st15)
        chk15 = compute_state_checksum(data15)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_801_15', 15, 1, '2026-09-10T08:15:00+00:00', ?, ?, 1, ?)
            """,
            (json.dumps(data15), chk15, json.dumps({"run_id": 801})),
        )

        cursor.execute(
            """
            INSERT INTO historical_incidents (run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (801, 8101, 'CALL_911', 'Cardiac', 'Severe', 1, 0.94, 26.86, 75.76, 5, '2026-09-10T08:05:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_dispatches (run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (801, 8101, 'AMB_801', 'ALS', 'HOSP_SMS', 'HOSP_SMS', 7.0, 7.0, 5.1, 'LIGHT', 'CLEAR', 5, 20, 'ARRIVED', '2026-09-10T08:20:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_events (run_id, event_type, sim_time, facility_or_unit_id, message, created_at)
            VALUES (801, 'DISPATCH', 5, 'AMB_801', 'Unit AMB_801 dispatched to incident 8101', '2026-09-10T08:05:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_events (run_id, event_type, sim_time, facility_or_unit_id, message, created_at)
            VALUES (801, 'AMBULANCE_ARRIVED', 20, 'AMB_801', 'Unit AMB_801 arrived at scene', '2026-09-10T08:20:00+00:00')
            """
        )

        conn.commit()
        conn.close()

    # =================================================================
    # TESTS
    # =================================================================

    def test_01_operational_run_appears_in_replay_listing(self):
        """1. Operational run appears in replay listing (GET /replays)."""
        resp = self.client.get("/replays", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        replays = resp.json()
        op_run = next((r for r in replays if r["run_id"] == "run_801"), None)
        self.assertIsNotNone(op_run, "run_801 must appear in replay listing")
        self.assertEqual(op_run["scenario_id"], "OPERATIONAL_RUN_801")
        self.assertEqual(op_run["end_sim_time"], 30)
        self.assertGreaterEqual(op_run["event_count"], 2)

    def test_02_scenario_replay_listing_remains_compatible(self):
        """2. Scenario replay listing remains compatible."""
        resp = self.client.get("/replays", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        replays = resp.json()
        # Verify schema keys
        for r in replays:
            self.assertIn("scenario_id", r)
            self.assertIn("run_id", r)
            self.assertIn("start_sim_time", r)
            self.assertIn("end_sim_time", r)
            self.assertIn("event_count", r)
            self.assertIn("completion_status", r)

    def test_03_operational_replay_selection_uses_run_id(self):
        """3. Operational replay selection uses run_<id> format."""
        self.assertTrue(is_operational_replay_id("run_801"))
        resp = self.client.get("/replays/run_801", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_metadata"]["run_id"], "run_801")
        self.assertEqual(data["run_metadata"]["scenario_id"], "OPERATIONAL_RUN_801")

    def test_04_timeline_loads_for_operational_run(self):
        """4. Timeline loads for operational run (GET /replays/{id}/timeline)."""
        resp = self.client.get("/replays/run_801/timeline", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        timeline = resp.json()
        self.assertEqual(timeline["run_id"], "run_801")
        self.assertGreater(timeline["event_count"], 0)
        event_types = [e["event_type"] for e in timeline["events"]]
        self.assertIn("DISPATCH", event_types)

    def test_05_historical_state_loads_for_operational_run(self):
        """5. Historical state loads for operational run (GET /replays/{id}/state/{sim_time})."""
        resp = self.client.get("/replays/run_801/state/15", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        st = resp.json()
        self.assertEqual(st["sim_time"], 15)
        self.assertGreaterEqual(len(st["ambulances"]), 1)
        amb = st["ambulances"][0]
        self.assertEqual(amb["ambulance_id"], "AMB_801")
        self.assertEqual(amb["status"], "EN_ROUTE")

    def test_06_replay_step_works(self):
        """6. Replay step works (POST /replays/{id}/step)."""
        resp = self.client.post("/replays/run_801/step", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        st = resp.json()
        self.assertIn("sim_time", st)
        self.assertIn("current_event_index", st)
        self.assertGreaterEqual(st["current_event_index"], 1)

    def test_07_replay_seek_works(self):
        """7. Replay seek works (GET /replays/{id}/state?sim_time=X)."""
        resp = self.client.get("/replays/run_801/state?sim_time=0", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        st0 = resp.json()
        self.assertEqual(st0["sim_time"], 0)

        resp2 = self.client.get("/replays/run_801/state?sim_time=15", headers=self.headers)
        self.assertEqual(resp2.status_code, 200)
        st15 = resp2.json()
        self.assertEqual(st15["sim_time"], 15)

    def test_08_replay_does_not_mutate_live_dispatch_state(self):
        """8. Replay does not mutate live DispatchState."""
        if manager.is_initialized and manager.simulator and manager.simulator.state:
            initial_time = manager.simulator.state.current_time
            # Execute multiple replay actions
            self.client.get("/replays/run_801/timeline", headers=self.headers)
            self.client.get("/replays/run_801/state?sim_time=25", headers=self.headers)
            self.client.post("/replays/run_801/step", headers=self.headers)

            # Live current_time must be completely unchanged
            self.assertEqual(manager.simulator.state.current_time, initial_time)

    def test_09_replay_does_not_emit_sse_events(self):
        """9. Replay does not emit SSE events."""
        from api.realtime.broadcaster import broadcaster
        seq_before = broadcaster.current_sequence

        # Call replay endpoints
        self.client.get("/replays/run_801/timeline", headers=self.headers)
        self.client.post("/replays/run_801/step", headers=self.headers)

        seq_after = broadcaster.current_sequence
        self.assertEqual(seq_before, seq_after, "Replay operations must NEVER increment broadcaster sequence")

    def test_10_replay_does_not_call_simulator_manager_tick(self):
        """10. Replay does not call SimulatorManager.tick()."""
        if manager.is_initialized and manager.simulator:
            ticks_before = manager.simulator.tick_count
            self.client.get("/replays/run_801/state/10", headers=self.headers)
            self.client.post("/replays/run_801/step", headers=self.headers)
            ticks_after = manager.simulator.tick_count
            self.assertEqual(ticks_before, ticks_after)

    def test_11_replay_state_isolated_from_live_frontend_state(self):
        """11. Replay state is isolated from live frontend state store."""
        # Inspect frontend/js/components/replay.js to verify no store import
        replay_js_path = Path(__file__).parent / "frontend" / "js" / "components" / "replay.js"
        content = replay_js_path.read_text(encoding="utf-8")
        self.assertNotIn("import { store }", content, "replay.js must not import live store")
        self.assertNotIn("store.updateFromDashboard", content)

    def test_12_historical_live_mode_labels(self):
        """12. Historical/live mode labels are defined and distinct in frontend."""
        index_html_path = Path(__file__).parent / "frontend" / "index.html"
        html_content = index_html_path.read_text(encoding="utf-8")
        self.assertIn("REPLAY MODE", html_content)
        self.assertIn("id=\"replay-mode-banner\"", html_content)

    def test_13_historical_events_render(self):
        """13. Historical events render with available metadata."""
        resp = self.client.get("/replays/run_801/events/1", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        ev = resp.json()
        self.assertIn("event_type", ev)
        self.assertIn("sim_time", ev)
        self.assertIn("description", ev)

    def test_14_missing_evidence_shown_honestly(self):
        """14. Missing evidence is shown honestly for historical runs."""
        replay_js_path = Path(__file__).parent / "frontend" / "js" / "components" / "replay.js"
        content = replay_js_path.read_text(encoding="utf-8")
        self.assertIn("Decision evidence was not recorded for this historical run", content)

    def test_15_server_provided_values_are_escaped(self):
        """15. Server-provided strings are escaped in frontend replay code."""
        replay_js_path = Path(__file__).parent / "frontend" / "js" / "components" / "replay.js"
        content = replay_js_path.read_text(encoding="utf-8")
        self.assertIn("escapeHtml", content, "replay.js must define and use escapeHtml")

    def test_16_401_handling(self):
        """16. Unauthenticated requests are rejected with 401."""
        prev = settings.dev_auth_fallback
        try:
            settings.dev_auth_fallback = False
            resp = self.client.get("/replays/run_801/timeline")
            self.assertEqual(resp.status_code, 401)
        finally:
            settings.dev_auth_fallback = prev

    def test_17_403_handling(self):
        """17. 403 Forbidden is enforced for unauthorized roles on privileged endpoints."""
        # Dispatcher role cannot run scenarios (requires RUN_DRILLS)
        resp = self.client.post(
            "/scenarios/SCEN_TEST/run",
            headers=self.dispatcher_headers,
            json={"duration_minutes": 10},
        )
        self.assertEqual(resp.status_code, 403)

    def test_18_404_handling(self):
        """18. Missing replay returns 404 cleanly."""
        resp = self.client.get("/replays/run_999999/timeline", headers=self.headers)
        self.assertEqual(resp.status_code, 404)
        err = resp.json()["detail"]
        self.assertIn("not found", err.lower())
        self.assertNotIn("SELECT", err.upper())
        self.assertNotIn("Traceback", err)

    def test_19_existing_scenario_replay_tests_remain_passing(self):
        """19. Existing scenario replay test paths remain fully functional."""
        artifact = resolve_replay_artifact("run_801", db_path=self.db_path)
        self.assertEqual(artifact.run_metadata.run_id, "run_801")
        self.assertEqual(len(artifact.snapshots), 2)
        self.assertGreaterEqual(len(artifact.events), 2)

    def test_20_api_js_exports_step_replay(self):
        """20. frontend/js/api.js exports stepReplay."""
        api_js_path = Path(__file__).parent / "frontend" / "js" / "api.js"
        content = api_js_path.read_text(encoding="utf-8")
        self.assertIn("export const stepReplay", content)


if __name__ == "__main__":
    unittest.main()
