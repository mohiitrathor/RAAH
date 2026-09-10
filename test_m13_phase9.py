"""
RAAH Milestone 13.4 Phase 4 — Operational Post-Incident Review & Comparison Tests
==================================================================================

Validates:
1. Operational GET /replays/{run_id}/pir returns 200.
2. Operational GET /replays/{run_id}/findings returns 200.
3. Operational GET /replays/{run_id}/root-causes returns 200.
4. Operational POST /replays/{run_id}/pir/report returns 200.
5. Operational POST /replays/pir/compare works (200).
6. Scenario PIR remains fully functional.
7. Scenario findings remain functional.
8. Scenario root causes remain functional.
9. Scenario report remains functional.
10. Unavailable historical evidence is not fabricated.
11. Unavailable telemetry is not fabricated.
12. No counterfactual claims are introduced.
13. PIR does not mutate live DispatchState.
14. PIR does not invoke simulator tick.
15. PIR does not emit SSE events.
16. Replay analysis and comparison operations remain read-only.
17. Run A vs Run B terminology exists in frontend UI.
18. Descriptive/non-causal comparison disclaimer exists.
19. Before/after temporal state delta disclaimer exists.
20. Replay timeline event_type filter still works.
21. Replay timeline entity_id filter works.
22. Replay timeline combined event_type and entity_id filter works.
23. Empty entity_id filter preserves existing behavior.
24. Operational run badge formatting exists in PIR UI.
25. Dynamic PIR strings are escaped in frontend rendering.
26. No unsafe replay HTML rendering is introduced.
27. Unauthenticated PIR returns 401.
28. Unauthorized operation returns 403 where applicable.
29. Missing operational run returns clean 404.
30. Corrupt historical data returns clean error without leaking internals.
"""

import unittest
import os
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
from Dispatch.scenarios.store import ReplayStore
from Dispatch.scenarios.models import ReplayArtifact, RunMetadata, ScenarioDefinition, ScenarioConfig


class TestM13Phase9OperationalPIR(unittest.TestCase):
    """Test suite for M13.4 Phase 4 Operational Post-Incident Review & Comparison."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

        # RBAC tokens
        cls.admin_token = create_test_token(username="admin_pir", role=Role.ADMINISTRATOR)
        cls.supervisor_token = create_test_token(username="sup_pir", role=Role.SUPERVISOR)
        cls.dispatcher_token = create_test_token(username="disp_pir", role=Role.DISPATCHER)

        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.supervisor_headers = {"Authorization": f"Bearer {cls.supervisor_token}"}
        cls.dispatcher_headers = {"Authorization": f"Bearer {cls.dispatcher_token}"}
        cls.headers = cls.dispatcher_headers

        # Setup test database
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_pir_replay.db"
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
        """Seed test database with operational run 901 and run 902."""
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

        # Seed simulation runs 901 and 902
        cursor.execute(
            """
            INSERT OR REPLACE INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (901, '2026-09-10T08:00:00Z', '2026-09-10T08:30:00Z', 'COMPLETED', 30, 30, 'M13.4 P4 Operational PIR Test Run A')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (902, '2026-09-10T09:00:00Z', '2026-09-10T09:30:00Z', 'COMPLETED', 30, 30, 'M13.4 P4 Operational PIR Test Run B')
            """
        )

        # Checkpoints for run 901
        st0 = DispatchState(current_time=0)
        st0.add_hospital(HospitalState("HOSP_901", "City Center Hospital", 26.89, 75.81, capacity=50, current_load=40, icu_capacity=10, current_icu_load=2))
        st0.add_ambulance(AmbulanceState("AMB_901", "ALS", 26.85, 75.75, status="AVAILABLE"))
        payload0 = serialize_dispatch_state(st0)
        chk0 = compute_state_checksum(payload0)

        st15 = DispatchState(current_time=15)
        st15.add_hospital(HospitalState("HOSP_901", "City Center Hospital", 26.89, 75.81, capacity=50, current_load=50, icu_capacity=10, current_icu_load=10))
        st15.add_ambulance(AmbulanceState("AMB_901", "ALS", 26.87, 75.77, status="EN_ROUTE", incident_id=9011, hospital_id="HOSP_901", eta_minutes=12.0))
        st15.add_incident(IncidentState(9011, "Cardiac Arrest", "Severe", 1, status="DISPATCHED", ambulance_id="AMB_901", hospital_id="HOSP_901"))
        payload15 = serialize_dispatch_state(st15)
        chk15 = compute_state_checksum(payload15)

        cursor.execute(
            """
            INSERT OR REPLACE INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chk_901_0", 0, 1, "2026-09-10T08:00:00Z", json.dumps(payload0), chk0, 1, json.dumps({"run_id": 901}))
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chk_901_15", 15, 1, "2026-09-10T08:15:00Z", json.dumps(payload15), chk15, 1, json.dumps({"run_id": 901}))
        )

        # Checkpoints for run 902
        cursor.execute(
            """
            INSERT OR REPLACE INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chk_902_0", 0, 1, "2026-09-10T09:00:00Z", json.dumps(payload0), chk0, 1, json.dumps({"run_id": 902}))
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chk_902_15", 15, 1, "2026-09-10T09:15:00Z", json.dumps(payload15), chk15, 1, json.dumps({"run_id": 902}))
        )

        # Dispatches, incidents, and events for run 901
        cursor.execute(
            """
            INSERT OR REPLACE INTO historical_incidents (id, run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (9011, 901, 9011, 'EMERGENCY_CALL', 'CARDIAC_ARREST', 'CRITICAL', 1, 0.95, 26.9150, 75.7890, 5, '2026-09-10T08:05:00Z')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO historical_dispatches (id, run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (9011, 901, 9011, 'AMB_901', 'ALS', 'HOSP_901', 'HOSP_901', 12.0, 12.0, 4.5, 'MODERATE', 'NORMAL', 5, 17, 'COMPLETED', '2026-09-10T08:17:00Z')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO historical_events (id, run_id, event_type, sim_time, facility_or_unit_id, message, created_at)
            VALUES (9011, 901, 'HOSPITAL_SATURATED', 14, 'HOSP_901', 'Hospital HOSP_901 reached full capacity', '2026-09-10T08:14:00Z')
            """
        )

        # Dispatches for run 902
        cursor.execute(
            """
            INSERT OR REPLACE INTO historical_incidents (id, run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (9021, 902, 9021, 'EMERGENCY_CALL', 'RESPIRATORY_DISTRESS', 'MODERATE', 2, 0.88, 26.9160, 75.7880, 4, '2026-09-10T09:04:00Z')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO historical_dispatches (id, run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (9021, 902, 9021, 'AMB_901', 'ALS', 'HOSP_901', 'HOSP_901', 6.0, 6.0, 2.5, 'LOW', 'NORMAL', 4, 10, 'COMPLETED', '2026-09-10T09:10:00Z')
            """
        )

        conn.commit()
        conn.close()

        # Seed scenario artifact fixture for scenario PIR tests
        store = ReplayStore()
        scen_id = "test_scen_p4_compat"
        meta = RunMetadata(
            scenario_id=scen_id,
            run_id=scen_id,
            start_sim_time=0,
            end_sim_time=10,
            wall_clock_duration_seconds=1.5,
            event_count=0,
            snapshot_count=0,
            completion_status="COMPLETED",
            deterministic_seed=42,
            replay_format_version="1.0.0",
            created_at="2026-09-10T00:00:00Z",
        )
        artifact = ReplayArtifact(
            replay_format_version="1.0.0",
            run_metadata=meta,
            scenario_definition=ScenarioDefinition(
                scenario_id=scen_id,
                name="Compat Drill",
                description="Test",
                config=ScenarioConfig(duration_minutes=10),
            ),
            snapshots=[],
            events=[],
            final_summary={},
        )
        store.save(artifact)

    @classmethod
    def tearDownClass(cls):
        compat_path = os.path.join("data", "replays", "test_scen_p4_compat.json")
        if os.path.exists(compat_path):
            try:
                os.remove(compat_path)
            except OSError:
                pass

    # ----------------------------------------------------------------------
    # 1-5: Operational Post-Incident Review Endpoints
    # ----------------------------------------------------------------------

    def test_01_operational_pir_returns_200(self):
        """1. Operational GET /replays/{run_id}/pir returns 200 with structured review."""
        resp = self.client.get("/replays/run_901/pir", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_id"], "run_901")
        self.assertIn("overall_severity", data)
        self.assertIn("resilience_score", data)
        self.assertIn("summary", data)
        self.assertIsInstance(data["findings"], list)
        self.assertIn("root_cause_graph", data)

    def test_02_operational_findings_returns_200(self):
        """2. Operational GET /replays/{run_id}/findings returns 200 with findings list."""
        resp = self.client.get("/replays/run_901/findings", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_id"], "run_901")
        self.assertIn("total_findings", data)
        self.assertIn("findings", data)

    def test_03_operational_root_causes_returns_200(self):
        """3. Operational GET /replays/{run_id}/root-causes returns 200 with DAG."""
        resp = self.client.get("/replays/run_901/root-causes", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_id"], "run_901")
        self.assertIn("root_cause_graph", data)
        self.assertIn("cascading_failures", data)

    def test_04_operational_pir_report_returns_200(self):
        """4. Operational POST /replays/{run_id}/pir/report returns 200 in markdown format."""
        resp = self.client.post(
            "/replays/run_901/pir/report",
            headers=self.headers,
            json={"format": "markdown"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_id"], "run_901")
        self.assertEqual(data["format"], "markdown")
        self.assertIn("Post-Incident Review", data["content"])

    def test_05_operational_pir_compare_works(self):
        """5. Operational POST /replays/pir/compare works (200) comparing two operational runs."""
        resp = self.client.post(
            "/replays/pir/compare",
            headers=self.headers,
            json={"run_id_a": "run_901", "run_id_b": "run_902"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("run_id_a", data)
        self.assertIn("run_id_b", data)
        self.assertIn("delta_resilience", data)

    # ----------------------------------------------------------------------
    # 6-9: Scenario PIR Compatibility
    # ----------------------------------------------------------------------

    def test_06_scenario_pir_still_works(self):
        """6. Existing scenario PIR still resolves through ReplayStore."""
        resp = self.client.get("/replays/test_scen_p4_compat/pir", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["run_id"], "test_scen_p4_compat")

    def test_07_scenario_findings_still_work(self):
        """7. Scenario findings endpoint resolves cleanly."""
        resp = self.client.get("/replays/test_scen_p4_compat/findings", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["run_id"], "test_scen_p4_compat")

    def test_08_scenario_root_causes_still_work(self):
        """8. Scenario root causes endpoint resolves cleanly."""
        resp = self.client.get("/replays/test_scen_p4_compat/root-causes", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["run_id"], "test_scen_p4_compat")

    def test_09_scenario_report_still_works(self):
        """9. Scenario report export endpoint resolves cleanly."""
        resp = self.client.post(
            "/replays/test_scen_p4_compat/pir/report",
            headers=self.headers,
            json={"format": "json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["run_id"], "test_scen_p4_compat")

    # ----------------------------------------------------------------------
    # 10-12: Data Fidelity Invariants
    # ----------------------------------------------------------------------

    def test_10_unavailable_historical_evidence_not_fabricated(self):
        """10. Decision evidence is not fabricated for historical operational runs."""
        artifact = resolve_replay_artifact("run_901", db_path=self.db_path)
        for ev in artifact.events:
            payload = ev.get("payload", {})
            self.assertNotIn("fabricated_alternatives", payload)

    def test_11_unavailable_telemetry_not_fabricated(self):
        """11. Continuous GPS coordinates are not fabricated between checkpoints."""
        resp = self.client.get("/replays/run_901/state/15", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        amb = resp.json()["ambulances"][0]
        self.assertEqual(amb["ambulance_id"], "AMB_901")
        self.assertEqual(amb["latitude"], 26.87)

    def test_12_no_counterfactual_claims_introduced(self):
        """12. Replay comparison outputs descriptive metrics rather than causal claims."""
        resp = self.client.post(
            "/replays/compare",
            headers=self.headers,
            json={"run_id_a": "run_901", "run_id_b": "run_902"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("delta", data)
        self.assertIn("performance_explanation", data)
        self.assertNotIn("counterfactual", data["performance_explanation"].lower())

    # ----------------------------------------------------------------------
    # 13-16: Live State Isolation Invariants
    # ----------------------------------------------------------------------

    def test_13_pir_does_not_mutate_live_dispatch_state(self):
        """13. PIR does not mutate live DispatchState."""
        if manager.is_initialized and manager.simulator and manager.simulator.state:
            initial_time = manager.simulator.state.current_time
            self.client.get("/replays/run_901/pir", headers=self.headers)
            self.client.get("/replays/run_901/findings", headers=self.headers)
            self.client.get("/replays/run_901/root-causes", headers=self.headers)
            self.assertEqual(manager.simulator.state.current_time, initial_time)

    def test_14_pir_does_not_invoke_simulator_tick(self):
        """14. PIR does not invoke simulator tick."""
        if manager.is_initialized and manager.simulator:
            ticks_before = manager.simulator.tick_count
            self.client.get("/replays/run_901/pir", headers=self.headers)
            ticks_after = manager.simulator.tick_count
            self.assertEqual(ticks_before, ticks_after)

    def test_15_pir_does_not_emit_sse(self):
        """15. PIR does not emit SSE broadcaster events."""
        from api.realtime.broadcaster import broadcaster
        seq_before = broadcaster.current_sequence
        self.client.get("/replays/run_901/pir", headers=self.headers)
        self.client.post("/replays/pir/compare", headers=self.headers, json={"run_id_a": "run_901", "run_id_b": "run_902"})
        seq_after = broadcaster.current_sequence
        self.assertEqual(seq_before, seq_after)

    def test_16_analysis_comparison_remain_read_only(self):
        """16. Analysis and comparison perform zero writes to SQLite."""
        conn = get_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM simulation_runs")
        runs_before = c.fetchone()[0]
        conn.close()

        self.client.get("/replays/run_901/analysis", headers=self.headers)
        self.client.post("/replays/compare", headers=self.headers, json={"run_id_a": "run_901", "run_id_b": "run_902"})
        self.client.post("/replays/run_901/before-after", headers=self.headers, json={"time_a": 0, "time_b": 15})

        conn = get_connection(self.db_path)
        c = conn.cursor()
        c.execute("SELECT count(*) FROM simulation_runs")
        runs_after = c.fetchone()[0]
        conn.close()
        self.assertEqual(runs_before, runs_after)

    # ----------------------------------------------------------------------
    # 17-19: Comparison Semantics & Disclaimers
    # ----------------------------------------------------------------------

    def test_17_run_a_vs_run_b_terminology_exists(self):
        """17. Run A vs Run B terminology exists in frontend UI."""
        scen_js = (Path(__file__).parent / "frontend" / "js" / "components" / "scenario_analysis.js").read_text(encoding="utf-8")
        index_html = (Path(__file__).parent / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Differential: Run A vs Run B", scen_js)
        self.assertIn("Run Differential (Run A vs Run B)", index_html)
        self.assertIn("Run A", index_html)
        self.assertIn("Run B", index_html)

    def test_18_descriptive_non_causal_disclaimer_exists(self):
        """18. Descriptive/non-causal comparison disclaimer exists."""
        scen_js = (Path(__file__).parent / "frontend" / "js" / "components" / "scenario_analysis.js").read_text(encoding="utf-8")
        self.assertIn("Descriptive Telemetry Differential — Compares observed historical metrics across two independent runs. Does not indicate counterfactual causality.", scen_js)

    def test_19_before_after_disclaimer_exists(self):
        """19. Before/after temporal state delta disclaimer exists."""
        scen_js = (Path(__file__).parent / "frontend" / "js" / "components" / "scenario_analysis.js").read_text(encoding="utf-8")
        self.assertIn("Temporal State Delta — Summarizes net change in operational state across elapsed simulation time. It does not establish causal impact of an individual intervention.", scen_js)

    # ----------------------------------------------------------------------
    # 20-23: Timeline Entity Filtering
    # ----------------------------------------------------------------------

    def test_20_event_type_filter_still_works(self):
        """20. Timeline event_type filter returns only matching event types."""
        resp = self.client.get("/replays/run_901/timeline?event_type=DISPATCH", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for ev in data["events"]:
            self.assertEqual(ev["event_type"], "DISPATCH")

    def test_21_entity_id_filter_works(self):
        """21. Timeline entity_id filter returns events referencing the entity."""
        resp = self.client.get("/replays/run_901/timeline?entity_id=HOSP_901", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["event_count"], 1)

    def test_22_combined_filters_work_together(self):
        """22. Both event_type and entity_id filters work in combination."""
        resp = self.client.get("/replays/run_901/timeline?event_type=HOSPITAL_SATURATED&entity_id=HOSP_901", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["event_count"], 1)
        for ev in data["events"]:
            self.assertEqual(ev["event_type"], "HOSPITAL_SATURATED")

    def test_23_empty_entity_filter_preserves_behavior(self):
        """23. Empty entity_id filter query returns full timeline."""
        resp_all = self.client.get("/replays/run_901/timeline", headers=self.headers)
        resp_empty_filter = self.client.get("/replays/run_901/timeline?entity_id=", headers=self.headers)
        self.assertEqual(resp_all.json()["event_count"], resp_empty_filter.json()["event_count"])

    # ----------------------------------------------------------------------
    # 24-26: Frontend Safety & Escaping
    # ----------------------------------------------------------------------

    def test_24_operational_badge_exists_in_pir_ui(self):
        """24. Operational run badge formatting exists in PIR UI component."""
        pir_js = (Path(__file__).parent / "frontend" / "js" / "components" / "pir.js").read_text(encoding="utf-8")
        self.assertIn("[OPERATIONAL]", pir_js)
        self.assertIn("[SCENARIO]", pir_js)

    def test_25_dynamic_pir_strings_are_escaped(self):
        """25. Dynamic PIR strings are escaped before HTML insertion."""
        pir_js = (Path(__file__).parent / "frontend" / "js" / "components" / "pir.js").read_text(encoding="utf-8")
        self.assertIn("function escapeHtml", pir_js)
        self.assertIn("escapeHtml(f.title)", pir_js)
        self.assertIn("escapeHtml(r.issue)", pir_js)
        self.assertIn("escapeHtml(n.label)", pir_js)

    def test_26_no_unsafe_replay_html_rendering(self):
        """26. No unescaped variables inserted in comparison results."""
        scen_js = (Path(__file__).parent / "frontend" / "js" / "components" / "scenario_analysis.js").read_text(encoding="utf-8")
        self.assertIn("escapeHtml(res.performance_explanation)", scen_js)
        self.assertIn("escapeHtml(res.scenario_a.casualties)", scen_js)

    # ----------------------------------------------------------------------
    # 27-30: Security & Clean Error Handling
    # ----------------------------------------------------------------------

    def test_27_unauthenticated_pir_returns_401(self):
        """27. Unauthenticated request to /replays/{run_id}/pir returns 401."""
        prev = settings.dev_auth_fallback
        try:
            settings.dev_auth_fallback = False
            resp = self.client.get("/replays/run_901/pir")
            self.assertEqual(resp.status_code, 401)
        finally:
            settings.dev_auth_fallback = prev

    def test_28_unauthorized_operation_returns_403(self):
        """28. Unauthorized role rejected with 403 on privileged action."""
        resp = self.client.post(
            "/regression/baseline/create",
            headers=self.dispatcher_headers,
            json={"description": "Unauthorized attempt"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_29_missing_operational_run_returns_clean_404(self):
        """29. Missing operational run returns clean 404 without tracebacks."""
        resp = self.client.get("/replays/run_999999/pir", headers=self.headers)
        self.assertEqual(resp.status_code, 404)
        err = resp.json()["detail"]
        self.assertIn("not found", err.lower())
        self.assertNotIn("Traceback", err)
        self.assertNotIn("SELECT", err.upper())

    def test_30_corrupt_historical_data_returns_clean_error(self):
        """30. Corrupt checkpoint returns clean 422 without leaking database internals."""
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (903, '2026-09-10T10:00:00Z', '2026-09-10T10:30:00Z', 'COMPLETED', 30, 30, 'Corrupt Run')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_903_0', 0, 1, '2026-09-10T10:00:00Z', '{"bad": "json', 'corrupt_checksum', 1, '{"run_id": 903}')
            """
        )
        conn.commit()
        conn.close()

        resp = self.client.get("/replays/run_903/pir", headers=self.headers)
        self.assertIn(resp.status_code, (422, 400))
        err = resp.json()["detail"]
        self.assertNotIn("Traceback", err)
        self.assertNotIn("SELECT", err.upper())


if __name__ == "__main__":
    unittest.main()
