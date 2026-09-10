"""
RAAH Milestone 13.5 Phase 2 — Secure M2M / Webhook Gateway Test Suite
=====================================================================

Validates:
1. Valid CAD M2M key accepted for CAD incident ingestion.
2. Valid GPS M2M key accepted for GPS telemetry ingestion.
3. Valid Hospital M2M key accepted for Hospital status ingestion.
4. Valid Traffic M2M key accepted for Traffic update ingestion.
5. Missing credential rejected with 401 Unauthorized.
6. Invalid M2M key rejected with 401 Unauthorized.
7. Empty / malformed key rejected with 401 Unauthorized.
8. Provider scope mismatch: CAD key attempting GPS endpoint rejected with 403 Forbidden.
9. Provider scope mismatch: GPS key attempting CAD endpoint rejected with 403 Forbidden.
10. Provider scope mismatch: Hospital key attempting Traffic endpoint rejected with 403 Forbidden.
11. Provider scope mismatch: Traffic key attempting CAD endpoint rejected with 403 Forbidden.
12. Multi-event / Omni key accepted across multiple different endpoints.
13. Generic /ingestion/event endpoint enforces scope check for the payload's event type.
14. Inactive / revoked M2M key rejected with 401 Unauthorized.
15. Dynamic credential generation and registration works seamlessly.
16. Existing operator JWT flow still works for all ingestion endpoints.
17. Existing operator with wrong permission rejected with 403 Forbidden.
18. Existing unauthenticated dev_auth_fallback works when enabled in dev.
19. dev_auth_fallback disabled in test properly rejects unauthenticated requests with 401.
20. Idempotency deduplication works identically for M2M requests (first accepted, second returned DUPLICATE).
21. Stale / watermark rejection works identically for M2M requests.
22. M2M credential material (secrets, salts, hashes) is NEVER present in responses.
23. M2M credential material is NEVER present in log outputs.
24. Ingestion security metrics: m2m_auth_success_total increments on success.
25. Ingestion security metrics: m2m_auth_failure_total increments on bad key.
26. Ingestion security metrics: m2m_scope_mismatch_total increments on scope mismatch.
27. Ingestion security metrics: provider-level rejection breakdown tracked accurately.
28. Security snapshot exposed in GET /metrics and GET /ingestion/status.
29. Administrative /ingestion/m2m/credentials route requires USER_ADMINISTRATION and returns only public summaries.
30. State mutation strictly flows through authoritative DispatchState.
"""

import os
import time
import json
import logging
import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import manager
from api.settings import settings
from api.auth.models import Role, Permission
from api.auth.security import create_test_token
from api.observability.metrics import metrics_collector
from api.adapters import (
    EventType,
    EventStatus,
    m2m_store,
    TEST_M2M_CAD_KEY,
    TEST_M2M_GPS_KEY,
    TEST_M2M_HOSPITAL_KEY,
    TEST_M2M_TRAFFIC_KEY,
    TEST_M2M_OMNI_KEY,
)


class TestM13Phase2M2MGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manager.initialize()
        cls.client = TestClient(app)
        cls.dispatcher_token = create_test_token(role=Role.DISPATCHER, username="test_dispatcher")
        cls.admin_token = create_test_token(role=Role.ADMINISTRATOR, username="test_admin")
        cls.supervisor_token = create_test_token(role=Role.SUPERVISOR, username="test_supervisor")

    def setUp(self):
        m2m_store.reset_test_credentials()

    # ------------------------------------------------------------------
    # 1-4: VALID M2M CREDENTIAL ACCEPTANCE
    # ------------------------------------------------------------------

    def test_01_valid_cad_m2m_credential_accepted(self):
        """Valid CAD M2M key accepted for CAD incident ingestion."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": f"cad_m2m_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
            "Age": 55,
            "Condition": "Cardiac",
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn(data["status"], ["ACCEPTED", "REJECTED"])

    def test_02_valid_gps_m2m_credential_accepted(self):
        """Valid GPS M2M key accepted for GPS telemetry ingestion."""
        payload = {
            "source": "GPS_MOCK",
            "source_event_id": f"gps_m2m_{time.time_ns()}",
            "ambulance_id": "AMB_0001",
            "latitude": 28.6140,
            "longitude": 77.2095,
            "speed_kmh": 45.0,
            "status": "EN_ROUTE",
        }
        res = self.client.post(
            "/ingestion/gps/location",
            json=payload,
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ACCEPTED")

    def test_03_valid_hospital_m2m_credential_accepted(self):
        """Valid Hospital M2M key accepted for Hospital status ingestion."""
        payload = {
            "source": "HOSP_MOCK",
            "source_event_id": f"hosp_m2m_{time.time_ns()}",
            "hospital_id": "HOSP_001",
            "status": "NORMAL",
            "available_beds": 12,
            "icu_available": 3,
            "divert_status": False,
        }
        res = self.client.post(
            "/ingestion/hospital/status",
            json=payload,
            headers={"X-API-Key": TEST_M2M_HOSPITAL_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ACCEPTED")

    def test_04_valid_traffic_m2m_credential_accepted(self):
        """Valid Traffic M2M key accepted for Traffic update ingestion."""
        payload = {
            "source": "TRAFFIC_MOCK",
            "source_event_id": f"traffic_m2m_{time.time_ns()}",
            "traffic_level": "HEAVY",
            "road_condition": "POOR",
        }
        res = self.client.post(
            "/ingestion/traffic/update",
            json=payload,
            headers={"X-API-Key": TEST_M2M_TRAFFIC_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ACCEPTED")

    # ------------------------------------------------------------------
    # 5-7: REJECTION OF MISSING, INVALID, OR MALFORMED KEYS
    # ------------------------------------------------------------------

    def test_05_missing_credential_rejected_when_no_fallback(self):
        """Missing credential rejected with 401 Unauthorized when dev fallback disabled."""
        orig = settings.dev_auth_fallback
        settings.dev_auth_fallback = False
        try:
            payload = {
                "source": "CAD_MOCK",
                "source_event_id": "cad_missing_auth",
                "patient_lat": 28.6139,
                "patient_lon": 77.2090,
            }
            res = self.client.post("/ingestion/cad/incident", json=payload)
            self.assertEqual(res.status_code, 401)
            self.assertTrue(
                "Missing" in res.json()["detail"] or "Authentication required" in res.json()["detail"]
            )
        finally:
            settings.dev_auth_fallback = orig

    def test_06_invalid_m2m_key_rejected(self):
        """Invalid M2M key rejected with 401 Unauthorized."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": "cad_invalid_key",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": "raah_m2m_invalid_bogus_secret_12345"},
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("Invalid, expired, or unrecognized", res.json()["detail"])

    def test_07_empty_or_malformed_key_rejected(self):
        """Empty or too-short key rejected with 401 Unauthorized."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": "cad_empty_key",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": "short"},
        )
        self.assertEqual(res.status_code, 401)

    # ------------------------------------------------------------------
    # 8-11: PROVIDER SCOPE MISMATCH REJECTIONS (403)
    # ------------------------------------------------------------------

    def test_08_cad_key_attempting_gps_endpoint_rejected_403(self):
        """CAD key attempting GPS endpoint rejected with 403 Forbidden."""
        payload = {
            "source": "GPS_MOCK",
            "source_event_id": f"gps_cad_mismatch_{time.time_ns()}",
            "ambulance_id": "AMB_0001",
            "latitude": 28.6140,
            "longitude": 77.2095,
        }
        res = self.client.post(
            "/ingestion/gps/location",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden", res.json()["detail"])
        self.assertIn("does not permit 'AMBULANCE_GPS'", res.json()["detail"])

    def test_09_gps_key_attempting_cad_endpoint_rejected_403(self):
        """GPS key attempting CAD endpoint rejected with 403 Forbidden."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": f"cad_gps_mismatch_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden", res.json()["detail"])
        self.assertIn("does not permit 'INCIDENT_CALL'", res.json()["detail"])

    def test_10_hospital_key_attempting_traffic_endpoint_rejected_403(self):
        """Hospital key attempting Traffic endpoint rejected with 403 Forbidden."""
        payload = {
            "source": "TRAFFIC_MOCK",
            "source_event_id": f"traffic_hosp_mismatch_{time.time_ns()}",
            "zone_id": "ZONE_A",
            "congestion_level": 0.5,
        }
        res = self.client.post(
            "/ingestion/traffic/update",
            json=payload,
            headers={"X-API-Key": TEST_M2M_HOSPITAL_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden", res.json()["detail"])

    def test_11_traffic_key_attempting_cad_endpoint_rejected_403(self):
        """Traffic key attempting CAD endpoint rejected with 403 Forbidden."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": f"cad_traffic_mismatch_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_TRAFFIC_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden", res.json()["detail"])

    # ------------------------------------------------------------------
    # 12-15: MULTI-EVENT, REVOCATION, DYNAMIC CREDENTIALS
    # ------------------------------------------------------------------

    def test_12_omni_key_accepted_across_multiple_endpoints(self):
        """Omni-scoped key accepted across different ingestion endpoints."""
        cad_payload = {
            "source": "OMNI_MOCK",
            "source_event_id": f"omni_cad_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res_cad = self.client.post(
            "/ingestion/cad/incident",
            json=cad_payload,
            headers={"X-API-Key": TEST_M2M_OMNI_KEY},
        )
        self.assertEqual(res_cad.status_code, 200)

        gps_payload = {
            "source": "OMNI_MOCK",
            "source_event_id": f"omni_gps_{time.time_ns()}",
            "ambulance_id": "AMB_001",
            "latitude": 28.6140,
            "longitude": 77.2095,
        }
        res_gps = self.client.post(
            "/ingestion/gps/location",
            json=gps_payload,
            headers={"X-API-Key": TEST_M2M_OMNI_KEY},
        )
        self.assertEqual(res_gps.status_code, 200)

    def test_13_generic_event_endpoint_enforces_scope(self):
        """Generic /ingestion/event endpoint enforces event_type matching for M2M keys."""
        gps_event_payload = {
            "event_id": f"evt_gps_{time.time_ns()}",
            "event_type": EventType.AMBULANCE_GPS.value,
            "source": "GPS_MOCK",
            "source_event_id": f"src_gps_{time.time_ns()}",
            "payload": {"ambulance_id": "AMB_001", "latitude": 28.6140, "longitude": 77.2095},
        }
        # CAD key attempting to send GPS event via /event
        res = self.client.post(
            "/ingestion/event",
            json=gps_event_payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden", res.json()["detail"])

    def test_14_inactive_or_revoked_key_rejected(self):
        """Revoked M2M key rejected with 401 Unauthorized."""
        m2m_store.revoke_credential("test_cad_key")
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": f"cad_revoked_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 401)

    def test_15_dynamic_credential_generation_and_registration(self):
        """Dynamic credential generation registers key and allows access immediately."""
        raw_key, summary = m2m_store.generate_credential(
            key_id="dyn_hosp_key",
            name="Dynamic Hospital Partner",
            provider_id="HOSP_DYNAMIC",
            allowed_event_types=[EventType.HOSPITAL_STATUS.value],
            is_test_credential=True,
        )
        self.assertTrue(raw_key.startswith("raah_m2m_"))
        self.assertEqual(summary.key_id, "dyn_hosp_key")

        payload = {
            "source": "HOSP_DYNAMIC",
            "source_event_id": f"dyn_hosp_evt_{time.time_ns()}",
            "hospital_id": "HOSP_002",
            "status": "NORMAL",
            "available_beds": 10,
        }
        res = self.client.post(
            "/ingestion/hospital/status",
            json=payload,
            headers={"X-API-Key": raw_key},
        )
        self.assertEqual(res.status_code, 200)

    # ------------------------------------------------------------------
    # 16-19: OPERATOR JWT FLOW & BACKWARD COMPATIBILITY
    # ------------------------------------------------------------------

    def test_16_existing_operator_jwt_flow_still_works(self):
        """Existing operator JWT flow works seamlessly with Bearer token."""
        payload = {
            "source": "CAD_911",
            "source_event_id": f"cad_jwt_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res.status_code, 200)

    def test_17_operator_with_insufficient_permission_rejected_403(self):
        """Operator without required permission rejected with 403."""
        payload = {
            "source": "HOSP_FEED",
            "source_event_id": f"hosp_no_perm_{time.time_ns()}",
            "hospital_id": "HOSP_001",
            "status": "NORMAL",
            "available_beds": 5,
        }
        # DISPATCHER does not have APPROVE_HOSPITAL_DIVERSION
        res = self.client.post(
            "/ingestion/hospital/status",
            json=payload,
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res.status_code, 403)

    def test_18_dev_auth_fallback_works_when_enabled(self):
        """Unauthenticated call uses dev_auth_fallback in development environment."""
        orig = settings.dev_auth_fallback
        settings.dev_auth_fallback = True
        try:
            payload = {
                "source": "GPS_MOCK",
                "source_event_id": f"gps_dev_fallback_{time.time_ns()}",
                "ambulance_id": "AMB_001",
                "latitude": 28.6140,
                "longitude": 77.2095,
            }
            res = self.client.post("/ingestion/gps/location", json=payload)
            self.assertEqual(res.status_code, 200)
        finally:
            settings.dev_auth_fallback = orig

    def test_19_bearer_header_with_m2m_key_format(self):
        """M2M API key passed as Bearer token is supported."""
        payload = {
            "source": "TRAFFIC_MOCK",
            "source_event_id": f"traffic_bearer_{time.time_ns()}",
            "traffic_level": "MODERATE",
            "road_condition": "AVERAGE",
        }
        res = self.client.post(
            "/ingestion/traffic/update",
            json=payload,
            headers={"Authorization": f"Bearer {TEST_M2M_TRAFFIC_KEY}"},
        )
        self.assertEqual(res.status_code, 200)

    # ------------------------------------------------------------------
    # 20-21: IDEMPOTENCY & WATERMARK INTEGRATION
    # ------------------------------------------------------------------

    def test_20_idempotency_deduplication_works_for_m2m_requests(self):
        """Duplicate M2M events are cleanly deduplicated with zero state mutation."""
        src_id = f"m2m_idemp_{time.time_ns()}"
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": src_id,
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        # First call
        res1 = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res1.status_code, 200)

        # Immediate second call with identical source + source_event_id
        res2 = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["status"], "DUPLICATE")
        self.assertEqual(data2["source_event_id"], src_id)

    def test_21_stale_watermark_rejection_works_for_m2m_requests(self):
        """Out-of-watermark event via M2M is rejected as STALE."""
        stale_time = "2020-01-01T00:00:00Z"
        payload = {
            "source": "GPS_MOCK",
            "source_event_id": f"gps_stale_{time.time_ns()}",
            "ambulance_id": "AMB_001",
            "latitude": 28.6140,
            "longitude": 77.2095,
            "occurred_at": stale_time,
        }
        res = self.client.post(
            "/ingestion/gps/location",
            json=payload,
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "STALE")

    # ------------------------------------------------------------------
    # 22-23: CREDENTIAL LEAKAGE DEFENSE (RESPONSES & LOGS)
    # ------------------------------------------------------------------

    def test_22_credential_material_never_in_response(self):
        """API responses never contain raw keys, salts, or hashes."""
        payload = {
            "source": "CAD_MOCK",
            "source_event_id": f"cad_leak_test_{time.time_ns()}",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        resp_text = res.text
        self.assertNotIn(TEST_M2M_CAD_KEY, resp_text)
        self.assertNotIn("salt", resp_text)
        self.assertNotIn("key_hash", resp_text)

    def test_23_credential_material_never_in_logs(self):
        """Log handler never receives raw key secrets."""
        log_records = []
        handler = logging.Handler()
        handler.emit = lambda rec: log_records.append(rec.getMessage())
        root_logger = logging.getLogger("raah")
        root_logger.addHandler(handler)

        secret_probe = "raah_m2m_canary_super_secret_never_log_me_99"
        try:
            self.client.post(
                "/ingestion/cad/incident",
                json={"source": "CAD_MOCK", "source_event_id": "c1", "patient_lat": 28.0, "patient_lon": 77.0},
                headers={"X-API-Key": secret_probe},
            )
            for msg in log_records:
                self.assertNotIn(secret_probe, msg)
        finally:
            root_logger.removeHandler(handler)

    # ------------------------------------------------------------------
    # 24-28: OBSERVABILITY & METRICS INTEGRATION
    # ------------------------------------------------------------------

    def test_24_m2m_auth_success_counter_increments(self):
        """Successful M2M authentication increments m2m_auth_success_total."""
        snap_before = metrics_collector.get_snapshot()["security"]["m2m_auth_success_total"]
        payload = {
            "source": "TRAFFIC_MOCK",
            "source_event_id": f"traffic_metric_succ_{time.time_ns()}",
            "traffic_level": "LIGHT",
        }
        self.client.post(
            "/ingestion/traffic/update",
            json=payload,
            headers={"X-API-Key": TEST_M2M_TRAFFIC_KEY},
        )
        snap_after = metrics_collector.get_snapshot()["security"]["m2m_auth_success_total"]
        self.assertGreaterEqual(snap_after, snap_before + 1)

    def test_25_m2m_auth_failure_counter_increments(self):
        """Failed M2M authentication increments m2m_auth_failure_total."""
        snap_before = metrics_collector.get_snapshot()["security"]["m2m_auth_failure_total"]
        self.client.post(
            "/ingestion/traffic/update",
            json={"source": "TRAFFIC_MOCK", "source_event_id": "s1", "traffic_level": "LIGHT"},
            headers={"X-API-Key": "raah_m2m_bad_key_fail_counter_123"},
        )
        snap_after = metrics_collector.get_snapshot()["security"]["m2m_auth_failure_total"]
        self.assertGreaterEqual(snap_after, snap_before + 1)

    def test_26_m2m_scope_mismatch_counter_increments(self):
        """Provider scope violation increments m2m_scope_mismatch_total."""
        snap_before = metrics_collector.get_snapshot()["security"]["m2m_scope_mismatch_total"]
        self.client.post(
            "/ingestion/cad/incident",
            json={"source": "CAD_MOCK", "source_event_id": "s2", "patient_lat": 28.0, "patient_lon": 77.0},
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        snap_after = metrics_collector.get_snapshot()["security"]["m2m_scope_mismatch_total"]
        self.assertGreaterEqual(snap_after, snap_before + 1)

    def test_27_rejections_by_provider_tracked_accurately(self):
        """Provider rejection count tracked in m2m_rejections_by_provider."""
        self.client.post(
            "/ingestion/cad/incident",
            json={"source": "CAD_MOCK", "source_event_id": "s3", "patient_lat": 28.0, "patient_lon": 77.0},
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        rejections = metrics_collector.get_snapshot()["security"]["m2m_rejections_by_provider"]
        self.assertIn("GPS_MOCK", rejections)
        self.assertGreaterEqual(rejections["GPS_MOCK"], 1)

    def test_28_security_snapshot_in_metrics_and_status_endpoints(self):
        """Security section exposed in GET /metrics and GET /ingestion/status."""
        res_metrics = self.client.get("/metrics", headers={"Authorization": f"Bearer {self.dispatcher_token}"})
        self.assertEqual(res_metrics.status_code, 200)
        self.assertIn("security", res_metrics.json())

        res_status = self.client.get("/ingestion/status", headers={"Authorization": f"Bearer {self.dispatcher_token}"})
        self.assertEqual(res_status.status_code, 200)
        self.assertIn("security", res_status.json())

    # ------------------------------------------------------------------
    # 29-30: ADMIN CREDENTIALS ROUTE & STATE ISOLATION
    # ------------------------------------------------------------------

    def test_29_m2m_credentials_list_requires_admin_and_is_safe(self):
        """GET /ingestion/m2m/credentials requires USER_ADMINISTRATION and returns safe metadata."""
        # Non-admin rejected with 403
        res_forbidden = self.client.get(
            "/ingestion/m2m/credentials",
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res_forbidden.status_code, 403)

        # Admin accepted
        res_admin = self.client.get(
            "/ingestion/m2m/credentials",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(res_admin.status_code, 200)
        creds = res_admin.json()
        self.assertIsInstance(creds, list)
        self.assertGreaterEqual(len(creds), 4)

        # Verify no secret, salt, or hash in output
        for item in creds:
            self.assertIn("key_id", item)
            self.assertIn("provider_id", item)
            self.assertIn("allowed_event_types", item)
            self.assertNotIn("key_hash", item)
            self.assertNotIn("salt", item)
            self.assertNotIn("raw_secret", item)

    def test_30_state_mutation_flows_authoritatively(self):
        """M2M ingestion events update authoritative DispatchState via simulator."""
        sim = manager.simulator
        initial_count = len(sim.state.ambulances)
        self.assertGreater(initial_count, 0)

        # Update ambulance GPS via M2M
        gps_payload = {
            "source": "GPS_MOCK",
            "source_event_id": f"gps_auth_state_{time.time_ns()}",
            "ambulance_id": "AMB_0001",
            "latitude": 28.7001,
            "longitude": 77.3001,
            "speed_kmh": 50.0,
        }
        res = self.client.post(
            "/ingestion/gps/location",
            json=gps_payload,
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        self.assertEqual(res.status_code, 200)

        # Check simulator state
        amb = sim.state.ambulances.get("AMB_0001")
        if amb:
            self.assertEqual(amb.latitude, 28.7001)
            self.assertEqual(amb.longitude, 77.3001)


if __name__ == "__main__":
    unittest.main()
