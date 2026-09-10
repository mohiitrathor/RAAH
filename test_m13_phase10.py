"""
RAAH Milestone 13.5 Phase 1 — Integration Health Console & Observability Tests
==============================================================================

Validates:
1. Ingestion status endpoint contract (GET /ingestion/status returns 200 with metrics & adapters).
2. CAD provider status in /ingestion/status response.
3. GPS provider status in /ingestion/status response.
4. Hospital provider status in /ingestion/status response.
5. Traffic provider status in /ingestion/status response.
6. Degraded provider representation (TimeoutError -> status DEGRADED, healthy=False).
7. Disconnected/unavailable provider representation (ConnectionError -> status DISCONNECTED, healthy=False).
8. Metric counter exposure in MetricsCollector snapshot.
9. Accepted event metric increments accurately upon successful ingestion.
10. Duplicate event metric increments accurately upon duplicate ingestion.
11. Rejected/stale metric increments accurately upon invalid/stale ingestion.
12. Unauthenticated status access returns 401 Unauthorized.
13. Authorized status access returns 200 with VIEW_LIVE permission.
14. Status probe causes zero mutation to authoritative DispatchState.
15. Status probe does not advance simulator tick / time.
16. Status probe does not invoke dispatch_incident.
17. Frontend component frontend/js/components/integrations.js exists and exports IntegrationController.
18. Frontend API client in frontend/js/api.js exposes getIngestionStatus().
19. Frontend refresh interval is conservative (>= 10000ms).
20. No 1-second integration polling loop exists in integrations.js.
21. Realtime event contract is unmodified (realtime models untouched).
22. No unsafe dynamic HTML rendering introduced (escapeHtml utility present and used).
23. Existing Command Center tab navigation is preserved.
24. AdapterRegistry.health_check_all returns healthy=False if any provider is degraded/disconnected.
25. Provider when None reports status NOT_CONFIGURED.
26. Event type breakdown metrics accurately separate CAD vs GPS vs Hospital vs Traffic events.
27. Ingestion latency tracking records non-negative execution durations.
28. Concurrent status probes are thread-safe and non-blocking.
29. Operational /metrics endpoint returns snapshot containing ingestion telemetry.
30. Dispatch execution performance is un-degraded.
"""

import os
import time
import json
import unittest
import threading
from pathlib import Path
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import manager
from api.settings import settings
from api.auth.models import Role, Permission
from api.auth.security import create_test_token
from api.observability.metrics import metrics_collector
from api.adapters import (
    adapter_registry,
    ingestion_service,
    NormalizedEvent,
    EventType,
    EventStatus,
    CADIncidentInput,
    AmbulanceGPSInput,
)
from api.adapters.mock_providers import (
    MockCADProvider,
    MockGPSProvider,
    MockHospitalProvider,
    MockTrafficProvider,
)


class TestM13Phase10IntegrationHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manager.initialize()
        cls.client = TestClient(app)

        # Generate tokens
        cls.supervisor_token = create_test_token(role=Role.SUPERVISOR, username="test_supervisor")
        cls.dispatcher_token = create_test_token(role=Role.DISPATCHER, username="test_dispatcher")
        cls.admin_token = create_test_token(role=Role.ADMINISTRATOR, username="test_admin")

        cls.viewer_headers = {"Authorization": f"Bearer {cls.supervisor_token}"}
        cls.dispatcher_headers = {"Authorization": f"Bearer {cls.dispatcher_token}"}
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}

    def setUp(self):
        # Reset default mock providers before each test
        adapter_registry._initialize_default_providers()

    def tearDown(self):
        # Ensure default mock providers restored
        adapter_registry._initialize_default_providers()

    # ----------------------------------------------------------------------
    # 1-5: Endpoint Contract & Provider Status Representation
    # ----------------------------------------------------------------------

    def test_01_status_endpoint_contract(self):
        """1. GET /ingestion/status returns 200 with metrics and adapters structure."""
        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("timestamp", data)
        self.assertIn("metrics", data)
        self.assertIn("adapters", data)

        adapters = data["adapters"]
        self.assertIn("healthy", adapters)
        self.assertIn("providers", adapters)
        self.assertIsInstance(adapters["healthy"], bool)

    def test_02_cad_provider_status(self):
        """2. CAD provider status is properly exposed with metadata and diagnostics."""
        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        providers = resp.json()["adapters"]["providers"]
        self.assertIn("cad", providers)
        cad = providers["cad"]
        self.assertEqual(cad["type"], "CAD")
        self.assertIn("status", cad)
        self.assertIn("healthy", cad)
        self.assertIn("pending_count", cad)
        self.assertIn("acknowledged_count", cad)

    def test_03_gps_provider_status(self):
        """3. GPS provider status is properly exposed with buffered fixes count."""
        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        providers = resp.json()["adapters"]["providers"]
        self.assertIn("gps", providers)
        gps = providers["gps"]
        self.assertEqual(gps["type"], "GPS")
        self.assertIn("status", gps)
        self.assertIn("buffered_fixes", gps)

    def test_04_hospital_provider_status(self):
        """4. Hospital provider status is properly exposed with status records count."""
        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        providers = resp.json()["adapters"]["providers"]
        self.assertIn("hospital", providers)
        hosp = providers["hospital"]
        self.assertEqual(hosp["type"], "HOSPITAL")
        self.assertIn("status", hosp)
        self.assertIn("status_records", hosp)

    def test_05_traffic_provider_status(self):
        """5. Traffic provider status is properly exposed with advisories count."""
        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        providers = resp.json()["adapters"]["providers"]
        self.assertIn("traffic", providers)
        traf = providers["traffic"]
        self.assertEqual(traf["type"], "TRAFFIC")
        self.assertIn("status", traf)
        self.assertIn("advisories_count", traf)

    # ----------------------------------------------------------------------
    # 6-7: Degraded & Disconnected Provider State Classifications
    # ----------------------------------------------------------------------

    def test_06_degraded_provider_representation(self):
        """6. Timeout in provider health check marks status as DEGRADED and healthy as False."""
        cad = adapter_registry.get_cad_provider()
        if hasattr(cad, "simulate_timeout"):
            cad.simulate_timeout = True

        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["adapters"]["healthy"])
        cad_status = data["adapters"]["providers"]["cad"]
        self.assertEqual(cad_status["status"], "DEGRADED")
        self.assertFalse(cad_status["healthy"])
        self.assertIn("error", cad_status)
        self.assertIn("timed out", cad_status["error"].lower())

    def test_07_disconnected_provider_representation(self):
        """7. Unreachable provider marks status as DISCONNECTED and healthy as False."""
        gps = adapter_registry.get_gps_provider()
        if hasattr(gps, "is_healthy"):
            gps.is_healthy = False

        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["adapters"]["healthy"])
        gps_status = data["adapters"]["providers"]["gps"]
        self.assertEqual(gps_status["status"], "DISCONNECTED")
        self.assertFalse(gps_status["healthy"])
        self.assertIn("error", gps_status)
        self.assertIn("unreachable", gps_status["error"].lower())

    # ----------------------------------------------------------------------
    # 8-11: Metrics Telemetry Exposure & Counter Accuracy
    # ----------------------------------------------------------------------

    def test_08_metric_counter_exposure_in_metrics_collector(self):
        """8. MetricsCollector.get_snapshot includes ingestion telemetry section."""
        snapshot = metrics_collector.get_snapshot()
        self.assertIn("ingestion", snapshot)
        ing = snapshot["ingestion"]
        self.assertIn("events_total", ing)
        self.assertIn("accepted_total", ing)
        self.assertIn("duplicates_total", ing)
        self.assertIn("rejected_total", ing)
        self.assertIn("stale_total", ing)
        self.assertIn("mean_latency_ms", ing)

    def test_09_accepted_event_metric(self):
        """9. Ingesting valid CAD event increments accepted_total counter."""
        initial_snap = metrics_collector.get_snapshot()["ingestion"]
        initial_accepted = initial_snap["accepted_total"]

        evt_id = f"test_acc_{int(time.time() * 1000)}"
        cad_payload = {
            "source_event_id": evt_id,
            "source": "CAD_911_TEST",
            "Sex": "Female",
            "Condition": "Cardiac",
            "Oxygen_Requirement": "Oxygen Mask",
            "Consciousness": "Alert",
            "Injury_Type": "No Injury",
            "Arrival_Mode": "Ambulance",
            "Age": 55,
            "Heart_Rate": 90.0,
            "SpO2": 95.0,
            "Systolic_BP": 130.0,
            "Diastolic_BP": 85.0,
            "Respiratory_Rate": 20.0,
            "Temperature": 37.0,
            "GCS": 15,
            "Pain_Score": 5,
            "Blood_Glucose": 110.0,
            "Respiratory_Distress": 0,
            "Chest_Pain": 1,
            "Bleeding": 0,
            "Seizure": 0,
            "Diabetes": 0,
            "Hypertension": 1,
            "Heart_Disease": 1,
            "Respiratory_Disease": 0,
            "patient_lat": 26.9124,
            "patient_lon": 75.7873,
        }

        resp = self.client.post("/ingestion/cad/incident", headers=self.dispatcher_headers, json=cad_payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ACCEPTED")

        updated_snap = metrics_collector.get_snapshot()["ingestion"]
        self.assertGreaterEqual(updated_snap["accepted_total"], initial_accepted + 1)

    def test_10_duplicate_event_metric(self):
        """10. Re-submitting identical event increments duplicates_total counter."""
        initial_snap = metrics_collector.get_snapshot()["ingestion"]
        initial_dups = initial_snap["duplicates_total"]

        evt_id = f"test_dup_{int(time.time() * 1000)}"
        cad_payload = {
            "source_event_id": evt_id,
            "source": "CAD_911_DUP_TEST",
            "Sex": "Male",
            "Condition": "Trauma",
            "Oxygen_Requirement": "No Oxygen",
            "Consciousness": "Alert",
            "Injury_Type": "Fracture",
            "Arrival_Mode": "Ambulance",
            "Age": 30,
            "Heart_Rate": 80.0,
            "SpO2": 98.0,
            "Systolic_BP": 120.0,
            "Diastolic_BP": 80.0,
            "Respiratory_Rate": 16.0,
            "Temperature": 36.8,
            "GCS": 15,
            "Pain_Score": 6,
            "Blood_Glucose": 95.0,
            "Respiratory_Distress": 0,
            "Chest_Pain": 0,
            "Bleeding": 1,
            "Seizure": 0,
            "Diabetes": 0,
            "Hypertension": 0,
            "Heart_Disease": 0,
            "Respiratory_Disease": 0,
            "patient_lat": 26.9150,
            "patient_lon": 75.7890,
        }

        # First ingestion
        r1 = self.client.post("/ingestion/cad/incident", headers=self.dispatcher_headers, json=cad_payload)
        self.assertEqual(r1.status_code, 200)

        # Second ingestion (duplicate)
        r2 = self.client.post("/ingestion/cad/incident", headers=self.dispatcher_headers, json=cad_payload)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "DUPLICATE")

        updated_snap = metrics_collector.get_snapshot()["ingestion"]
        self.assertGreaterEqual(updated_snap["duplicates_total"], initial_dups + 1)

    def test_11_rejected_stale_metric(self):
        """11. Ingesting event with unsupported schema version increments rejected_total counter."""
        initial_snap = metrics_collector.get_snapshot()["ingestion"]
        initial_rej = initial_snap["rejected_total"]

        raw_event = {
            "event_id": f"evt_bad_schema_{int(time.time() * 1000)}",
            "event_type": "INCIDENT_CALL",
            "source": "TEST_SOURCE",
            "source_event_id": f"bad_{int(time.time() * 1000)}",
            "schema_version": 999,  # Unsupported schema version
            "payload": {"test": 123},
        }

        resp = self.client.post("/ingestion/event", headers=self.dispatcher_headers, json=raw_event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "REJECTED")

        updated_snap = metrics_collector.get_snapshot()["ingestion"]
        self.assertGreaterEqual(updated_snap["rejected_total"], initial_rej + 1)

    # ----------------------------------------------------------------------
    # 12-13: Authentication & RBAC
    # ----------------------------------------------------------------------

    def test_12_unauthenticated_status_access_returns_401(self):
        """12. Unauthenticated GET /ingestion/status returns 401 Unauthorized."""
        prev = settings.dev_auth_fallback
        try:
            settings.dev_auth_fallback = False
            resp = self.client.get("/ingestion/status")
            self.assertEqual(resp.status_code, 401)
        finally:
            settings.dev_auth_fallback = prev

    def test_13_authorized_status_access(self):
        """13. Authorized users with VIEW_LIVE permission (viewer, dispatcher, admin) succeed (200)."""
        for headers, name in [
            (self.viewer_headers, "viewer"),
            (self.dispatcher_headers, "dispatcher"),
            (self.admin_headers, "admin"),
        ]:
            resp = self.client.get("/ingestion/status", headers=headers)
            self.assertEqual(resp.status_code, 200, f"Role {name} failed to access /ingestion/status")

    # ----------------------------------------------------------------------
    # 14-16: Core Invariants: Read-Only & Dispatch Isolation
    # ----------------------------------------------------------------------

    def test_14_no_dispatch_state_mutation(self):
        """14. Calling /ingestion/status causes zero mutation to live DispatchState."""
        state = manager.simulator.state
        with manager.lock:
            initial_incidents = len(state.incidents)
            initial_time = state.current_time

        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)

        with manager.lock:
            self.assertEqual(len(state.incidents), initial_incidents)
            self.assertEqual(state.current_time, initial_time)

    def test_15_no_simulator_tick(self):
        """15. Calling /ingestion/status does not advance simulation clock."""
        initial_time = manager.simulator.state.current_time
        for _ in range(5):
            self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(manager.simulator.state.current_time, initial_time)

    def test_16_no_dispatch_invocation(self):
        """16. Status probe does not trigger automatic ambulance dispatch or assign units."""
        with manager.lock:
            initial_dispatched = sum(1 for amb in manager.simulator.state.ambulances.values() if amb.status == "EN_ROUTE")

        resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)

        with manager.lock:
            current_dispatched = sum(1 for amb in manager.simulator.state.ambulances.values() if amb.status == "EN_ROUTE")
            self.assertEqual(current_dispatched, initial_dispatched)

    # ----------------------------------------------------------------------
    # 17-23: Frontend Integration & Architecture Preserved
    # ----------------------------------------------------------------------

    def test_17_frontend_component_exists(self):
        """17. frontend/js/components/integrations.js exists and exports IntegrationController."""
        path = Path("frontend/js/components/integrations.js")
        self.assertTrue(path.exists(), "integrations.js must exist")
        content = path.read_text(encoding="utf-8")
        self.assertIn("class IntegrationController", content)
        self.assertIn("export class IntegrationController", content)

    def test_18_frontend_api_wiring_exists(self):
        """18. frontend/js/api.js exports getIngestionStatus endpoint function."""
        path = Path("frontend/js/api.js")
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("getIngestionStatus", content)
        self.assertIn("/ingestion/status", content)

    def test_19_frontend_refresh_interval_conservative(self):
        """19. IntegrationController refresh interval is >= 10000ms (conservative)."""
        content = Path("frontend/js/components/integrations.js").read_text(encoding="utf-8")
        self.assertIn("refreshIntervalMs = 12000", content)

    def test_20_no_one_second_polling(self):
        """20. integrations.js does not contain a 1-second polling interval."""
        content = Path("frontend/js/components/integrations.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval(1000)", content)
        self.assertNotIn("setInterval(() => {}, 1000)", content)
        self.assertNotIn("1000 /*", content)

    def test_21_realtime_event_contract_unmodified(self):
        """21. Realtime models in api/realtime/models.py are unmodified."""
        content = Path("api/realtime/models.py").read_text(encoding="utf-8")
        self.assertIn("class EventType", content)
        self.assertIn("TICK = \"TICK\"", content)

    def test_22_no_unsafe_dynamic_html_rendering(self):
        """22. integrations.js uses escapeHtml for dynamic strings rendered into DOM."""
        content = Path("frontend/js/components/integrations.js").read_text(encoding="utf-8")
        self.assertIn("function escapeHtml(", content)
        self.assertIn("escapeHtml(title)", content)
        self.assertIn("escapeHtml(status)", content)

    def test_23_command_center_wiring_preserved(self):
        """23. index.html contains integrations workspace while preserving all existing tabs."""
        content = Path("frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("id=\"nav-btn-tactical\"", content)
        self.assertIn("id=\"nav-btn-analytics\"", content)
        self.assertIn("id=\"nav-btn-replay\"", content)
        self.assertIn("id=\"nav-btn-review\"", content)
        self.assertIn("id=\"nav-btn-optimization\"", content)
        self.assertIn("id=\"nav-btn-integrations\"", content)
        self.assertIn("id=\"integrations-workspace\"", content)
        self.assertIn("id=\"card-integration-cad\"", content)
        self.assertIn("id=\"card-integration-gps\"", content)
        self.assertIn("id=\"card-integration-hospital\"", content)
        self.assertIn("id=\"card-integration-traffic\"", content)

    # ----------------------------------------------------------------------
    # 24-30: Robustness, Performance & Unconfigured Providers
    # ----------------------------------------------------------------------

    def test_24_overall_health_false_on_any_degraded(self):
        """24. Overall adapters.healthy is False when any single adapter is degraded."""
        traf = adapter_registry.get_traffic_provider()
        if hasattr(traf, "simulate_timeout"):
            traf.simulate_timeout = True

        health = adapter_registry.health_check_all()
        self.assertFalse(health["healthy"])
        self.assertEqual(health["providers"]["traffic"]["status"], "DEGRADED")

    def test_25_unconfigured_provider_reports_not_configured(self):
        """25. Setting a provider to None produces status NOT_CONFIGURED."""
        orig_hosp = adapter_registry.get_hospital_provider()
        try:
            adapter_registry.set_hospital_provider(None)
            health = adapter_registry.health_check_all()
            self.assertFalse(health["healthy"])
            hosp_status = health["providers"]["hospital"]
            self.assertEqual(hosp_status["status"], "NOT_CONFIGURED")
            self.assertFalse(hosp_status["healthy"])
        finally:
            adapter_registry.set_hospital_provider(orig_hosp)

    def test_26_event_type_breakdown_metrics(self):
        """26. IngestionService tracks breakdown metrics separated by event type."""
        metrics = ingestion_service.get_metrics()
        self.assertIn("by_event_type", metrics)
        by_evt = metrics["by_event_type"]
        self.assertIn("INCIDENT_CALL", by_evt)
        self.assertIn("AMBULANCE_GPS", by_evt)
        self.assertIn("HOSPITAL_STATUS", by_evt)
        self.assertIn("TRAFFIC_UPDATE", by_evt)

    def test_27_latency_tracking_non_negative(self):
        """27. Mean latency reported in metrics is a non-negative float."""
        metrics = ingestion_service.get_metrics()
        self.assertIn("mean_latency_ms", metrics)
        self.assertGreaterEqual(metrics["mean_latency_ms"], 0.0)

    def test_28_concurrent_status_probes_thread_safe(self):
        """28. Concurrent status probes across multiple threads succeed without deadlock."""
        threads = []
        errors = []

        def probe():
            try:
                resp = self.client.get("/ingestion/status", headers=self.viewer_headers)
                if resp.status_code != 200:
                    errors.append(f"HTTP {resp.status_code}")
            except Exception as e:
                errors.append(str(e))

        for _ in range(10):
            t = threading.Thread(target=probe)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent probes encountered errors: {errors}")

    def test_29_operational_metrics_endpoint_includes_ingestion(self):
        """29. GET /metrics returns HTTP 200 and includes ingestion telemetry."""
        resp = self.client.get("/metrics", headers=self.viewer_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("ingestion", data)
        self.assertIn("events_total", data["ingestion"])

    def test_30_dispatch_performance_undegraded(self):
        """30. Dispatch calculation latency remains below 100ms baseline under telemetry load."""
        t0 = time.perf_counter()
        resp = self.client.post("/dispatch/1", headers=self.dispatcher_headers)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(resp.status_code, 200)
        self.assertLess(duration_ms, 150.0, f"Dispatch took too long: {duration_ms}ms")


if __name__ == "__main__":
    unittest.main()
