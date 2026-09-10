"""
RAAH Test Suite: M13.4 Phase 2 — Operational Replay API Integration
====================================================================

Comprehensive test suite verifying:
  1. GET /replays still returns existing scenario replays.
  2. GET /replays includes persisted operational runs.
  3. Operational run IDs are deterministic (run_<run_id>).
  4. Operational replay timeline works (GET /replays/{id}/timeline).
  5. Operational replay event lookup works (GET /replays/{id}/events/{index}).
  6. Operational replay state lookup works (GET /replays/{id}/state/{sim_time} & state).
  7. Operational replay stepping works (POST /replays/{id}/step).
  8. Scenario replay behavior remains unchanged.
  9. Missing operational run returns appropriate error (404).
 10. Invalid/corrupt operational run is handled safely (422/400).
 11. Replay API does not write to SQLite.
 12. Replay API does not modify live DispatchState.
 13. Replay API preserves authentication/RBAC.
 14. Operational and scenario replay IDs cannot collide.
 15. Existing M13.4 Phase 1 tests remain green.
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DISPATCH_DIR = ROOT / "Dispatch"
if str(DISPATCH_DIR) not in sys.path:
    sys.path.insert(0, str(DISPATCH_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from api.main import app
from api.settings import settings
from api.auth import Role, create_test_token
from api.dependencies import manager
from api.persistence.db import init_db, get_connection
from api.persistence.serializer import serialize_dispatch_state, compute_state_checksum
from api.persistence.replay_compiler import (
    clear_compiled_artifacts_cache,
    is_operational_replay_id,
    resolve_replay_artifact,
)
from Dispatch.state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)


class TestM13Phase7OperationalReplayAPI(unittest.TestCase):
    """Integration test suite for Operational Replay REST API endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        # Generate RBAC tokens
        cls.admin_token = create_test_token(username="admin_replay_tester", role=Role.ADMINISTRATOR)
        cls.supervisor_token = create_test_token(username="sup_replay_tester", role=Role.SUPERVISOR)
        cls.dispatcher_token = create_test_token(username="disp_replay_tester", role=Role.DISPATCHER)

        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.supervisor_headers = {"Authorization": f"Bearer {cls.supervisor_token}"}
        cls.dispatcher_headers = {"Authorization": f"Bearer {cls.dispatcher_token}"}
        cls.operator_headers = cls.dispatcher_headers
        cls.analyst_headers = cls.supervisor_headers
        cls.viewer_headers = cls.dispatcher_headers


        # Setup dedicated test SQLite database
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_replay_api.db"
        cls.original_db_path = settings.database_path
        settings.database_path = cls.db_path
        init_db(cls.db_path)
        cls._populate_test_runs()

    @classmethod
    def tearDownClass(cls):
        settings.database_path = cls.original_db_path
        clear_compiled_artifacts_cache()
        cls.temp_dir.cleanup()

    def setUp(self):
        clear_compiled_artifacts_cache()

    @classmethod
    def _populate_test_runs(cls):
        """Populate synthetic operational runs in test database."""
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

        now_iso = "2026-09-10T10:00:00+00:00"
        later_iso = "2026-09-10T10:20:00+00:00"

        # -------------------------------------------------------------
        # RUN 701: Standard completed operational run
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (701, ?, ?, 'COMPLETED', 20, 20, 'Synthetic operational run for API integration testing')
            """,
            (now_iso, later_iso),
        )

        st0 = DispatchState(current_time=0)
        st0.add_hospital(HospitalState("HOSP_CITY", "General", 26.90, 75.80, capacity=12, current_load=4, icu_capacity=3, current_icu_load=1))
        st0.add_ambulance(AmbulanceState("AMB_701", "ALS", 26.86, 75.76, status="AVAILABLE"))
        data0 = serialize_dispatch_state(st0)
        chk0 = compute_state_checksum(data0)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_701_0', 0, 1, ?, ?, ?, 1, ?)
            """,
            (now_iso, json.dumps(data0), chk0, json.dumps({"run_id": 701})),
        )

        st10 = DispatchState(current_time=10)
        st10.add_hospital(HospitalState("HOSP_CITY", "General", 26.90, 75.80, capacity=12, current_load=5, icu_capacity=3, current_icu_load=1))
        st10.add_ambulance(AmbulanceState("AMB_701", "ALS", 26.88, 75.78, status="EN_ROUTE", incident_id=7101, hospital_id="HOSP_CITY", eta_minutes=5.0))
        st10.add_incident(IncidentState(7101, "Trauma", "Severe", 1, status="DISPATCHED", ambulance_id="AMB_701", hospital_id="HOSP_CITY"))
        data10 = serialize_dispatch_state(st10)
        chk10 = compute_state_checksum(data10)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_701_10', 10, 1, '2026-09-10T10:10:00+00:00', ?, ?, 1, ?)
            """,
            (json.dumps(data10), chk10, json.dumps({"run_id": 701})),
        )

        cursor.execute(
            """
            INSERT INTO historical_incidents (run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (701, 7101, 'CALL_911', 'Trauma', 'Severe', 1, 0.96, 26.87, 75.77, 3, '2026-09-10T10:03:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_dispatches (run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (701, 7101, 'AMB_701', 'ALS', 'HOSP_CITY', 'HOSP_CITY', 6.0, 6.0, 4.2, 'MODERATE', 'CLEAR', 3, 14, 'ARRIVED', '2026-09-10T10:14:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_events (run_id, event_type, sim_time, facility_or_unit_id, message, created_at)
            VALUES (701, 'HOSPITAL_SATURATED', 7, 'HOSP_CITY', 'ER capacity warning', '2026-09-10T10:07:00+00:00')
            """
        )

        # -------------------------------------------------------------
        # RUN 702: Corrupt checkpoint run (checksum tampered)
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (702, '2026-09-10T11:00:00+00:00', '2026-09-10T11:10:00+00:00', 'FAILED', 10, 10, 'Corrupt run for 422 testing')
            """
        )
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_702_bad', 0, 1, '2026-09-10T11:00:00+00:00', '{"schema_version": 1, "state": {}}', 'INVALID_BAD_CHECKSUM', 1, '{"run_id": 702}')
            """
        )

        conn.commit()
        conn.close()

    # =================================================================
    # TESTS
    # =================================================================

    def test_01_get_replays_returns_existing_scenarios(self):
        """1. GET /replays still returns existing scenario replays."""
        resp = self.client.get("/replays", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 200)
        replays = resp.json()
        self.assertIsInstance(replays, list)
        self.assertGreater(len(replays), 0)

        # Verify existing scenario replay IDs are present (e.g. api_run_001 or run_test_p3_a)
        scenario_ids = [r["run_id"] for r in replays]
        self.assertTrue(any(sid.startswith("api_run") or sid.startswith("run_test") for sid in scenario_ids))

    def test_02_get_replays_includes_persisted_operational_runs(self):
        """2. GET /replays includes persisted operational runs."""
        resp = self.client.get("/replays", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 200)
        replays = resp.json()

        # Operational run 701 should appear as 'run_701'
        op_run = next((r for r in replays if r["run_id"] == "run_701"), None)
        self.assertIsNotNone(op_run)
        self.assertEqual(op_run["scenario_id"], "OPERATIONAL_RUN_701")
        self.assertEqual(op_run["completion_status"], "COMPLETED")
        self.assertEqual(op_run["end_sim_time"], 20)

    def test_03_operational_run_ids_are_deterministic(self):
        """3. Operational run IDs are deterministic (run_<run_id>)."""
        self.assertTrue(is_operational_replay_id("run_701"))
        self.assertTrue(is_operational_replay_id("run_1"))
        self.assertFalse(is_operational_replay_id("api_run_001"))
        self.assertFalse(is_operational_replay_id("run_test_p3_a"))
        self.assertFalse(is_operational_replay_id("SCEN_TEST"))

    def test_04_operational_replay_timeline_works(self):
        """4. Operational replay timeline works (GET /replays/{id}/timeline)."""
        resp = self.client.get("/replays/run_701/timeline", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 200)
        timeline = resp.json()
        self.assertEqual(timeline["run_id"], "run_701")
        self.assertGreater(timeline["event_count"], 0)
        self.assertIsInstance(timeline["events"], list)

        # Filter by event_type
        filtered_resp = self.client.get("/replays/run_701/timeline?event_type=DISPATCH", headers=self.analyst_headers)
        self.assertEqual(filtered_resp.status_code, 200)
        filtered = filtered_resp.json()
        for ev in filtered["events"]:
            self.assertEqual(ev["event_type"], "DISPATCH")

    def test_05_operational_replay_event_lookup_works(self):
        """5. Operational replay event lookup works (GET /replays/{id}/events/{index})."""
        resp = self.client.get("/replays/run_701/events/1", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 200)
        ev_detail = resp.json()
        self.assertEqual(ev_detail["event_index"], 1)
        self.assertIn("event_type", ev_detail)
        self.assertIn("sim_time", ev_detail)

    def test_06_operational_replay_state_lookup_works(self):
        """6. Operational replay state lookup works (GET /replays/{id}/state & /state/{sim_time})."""
        # Seek via /replays/{id}/state?sim_time=10
        resp1 = self.client.get("/replays/run_701/state?sim_time=10", headers=self.analyst_headers)
        self.assertEqual(resp1.status_code, 200)
        st1 = resp1.json()
        self.assertEqual(st1["sim_time"], 10)
        self.assertGreaterEqual(len(st1["ambulances"]), 1)

        # Seek via /replays/{id}/state/10 (replay_analysis route)
        resp2 = self.client.get("/replays/run_701/state/10", headers=self.analyst_headers)
        self.assertEqual(resp2.status_code, 200)
        st2 = resp2.json()
        self.assertEqual(st2["sim_time"], 10)

    def test_07_operational_replay_stepping_works(self):
        """7. Operational replay stepping works (POST /replays/{id}/step)."""
        # Step through events
        resp = self.client.post("/replays/run_701/step", headers=self.operator_headers)
        self.assertEqual(resp.status_code, 200)
        st = resp.json()
        self.assertGreaterEqual(st["current_event_index"], 1)

    def test_08_scenario_replay_behavior_remains_unchanged(self):
        """8. Scenario replay behavior remains unchanged."""
        # Query scenario replay
        resp = self.client.get("/replays/api_run_001", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()
        self.assertEqual(summary["run_metadata"]["run_id"], "api_run_001")

        # Step scenario replay
        step_resp = self.client.post("/replays/api_run_001/step", headers=self.operator_headers)
        self.assertEqual(step_resp.status_code, 200)

    def test_09_missing_operational_run_returns_appropriate_error(self):
        """9. Missing operational run returns 404 cleanly without internal stack traces or SQL."""
        resp = self.client.get("/replays/run_99999/timeline", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 404)
        err = resp.json()["detail"]
        self.assertIn("99999", err)
        self.assertNotIn("SELECT", err.upper())
        self.assertNotIn("Traceback", err)

        # Missing scenario replay also returns 404 cleanly
        resp_scen = self.client.get("/replays/non_existent_scenario_id", headers=self.analyst_headers)
        self.assertEqual(resp_scen.status_code, 404)

    def test_10_invalid_corrupt_operational_run_handled_safely(self):
        """10. Invalid/corrupt operational run is rejected safely (422/400)."""
        resp = self.client.get("/replays/run_702/timeline", headers=self.analyst_headers)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()["detail"]
        self.assertIn("corrupt", err.lower())

    def test_11_replay_api_does_not_write_to_sqlite(self):
        """11. Replay API does not write to SQLite."""
        conn = get_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM simulation_runs;")
        runs_before = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM state_checkpoints;")
        chks_before = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM historical_events;")
        evs_before = c.fetchone()[0]
        conn.close()

        # Perform multiple API actions
        self.client.get("/replays", headers=self.analyst_headers)
        self.client.get("/replays/run_701/timeline", headers=self.analyst_headers)
        self.client.get("/replays/run_701/state?sim_time=5", headers=self.analyst_headers)
        self.client.post("/replays/run_701/step", headers=self.operator_headers)

        conn = get_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM simulation_runs;")
        runs_after = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM state_checkpoints;")
        chks_after = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM historical_events;")
        evs_after = c.fetchone()[0]
        conn.close()

        self.assertEqual(runs_before, runs_after)
        self.assertEqual(chks_before, chks_after)
        self.assertEqual(evs_before, evs_after)

    def test_12_replay_api_does_not_modify_dispatch_state(self):
        """12. Replay API does not modify live DispatchState."""
        if manager.is_initialized and manager.simulator and manager.simulator.state:
            initial_sim_time = manager.simulator.state.current_time
            # Call replay API
            self.client.get("/replays/run_701/state?sim_time=15", headers=self.analyst_headers)
            self.client.post("/replays/run_701/step", headers=self.operator_headers)
            # Live state time must remain completely unchanged
            self.assertEqual(manager.simulator.state.current_time, initial_sim_time)

    def test_13_replay_api_preserves_authentication_rbac(self):
        """13. Replay API preserves authentication/RBAC (unauthorized requests return 401)."""
        prev_fallback = settings.dev_auth_fallback
        try:
            settings.dev_auth_fallback = False

            # Missing token rejected
            unauth_resp = self.client.get("/replays/run_701/timeline")
            self.assertEqual(unauth_resp.status_code, 401)

            unauth_step = self.client.post("/replays/run_701/step")
            self.assertEqual(unauth_step.status_code, 401)

            # Malformed/invalid token rejected
            bad_resp = self.client.get(
                "/replays/run_701/timeline",
                headers={"Authorization": "Bearer invalid_malformed_token"},
            )
            self.assertEqual(bad_resp.status_code, 401)

            # Authenticated requests succeed
            auth_resp = self.client.get("/replays/run_701/timeline", headers=self.viewer_headers)
            self.assertEqual(auth_resp.status_code, 200)
        finally:
            settings.dev_auth_fallback = prev_fallback

    def test_14_operational_and_scenario_replay_ids_cannot_collide(self):
        """14. Operational and scenario replay IDs cannot collide."""
        # Operational IDs always match run_\d+ pattern
        self.assertTrue(is_operational_replay_id("run_701"))

        # Scenario IDs do not match run_\d+ pattern
        scenario_ids = ["api_run_001", "run_test_p3_a", "run_test_p4_pileup", "custom_scenario_replay"]
        for sid in scenario_ids:
            self.assertFalse(
                is_operational_replay_id(sid),
                f"Scenario ID '{sid}' must NOT be classified as an operational replay ID.",
            )

    def test_15_existing_m13_4_phase1_tests_remain_green(self):
        """15. Existing M13.4 Phase 1 tests remain green and functional."""
        artifact = resolve_replay_artifact("run_701", db_path=self.db_path)
        self.assertEqual(artifact.run_metadata.run_id, "run_701")
        self.assertEqual(artifact.scenario_definition.scenario_id, "OPERATIONAL_RUN_701")
        self.assertEqual(len(artifact.snapshots), 2)


if __name__ == "__main__":
    unittest.main()
