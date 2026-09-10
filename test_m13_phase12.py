"""
RAAH Milestone 13.5 Phase 3 — Realistic CAD Intake & Triage Mapper Tests
========================================================================

Rigorous verification of the CAD intake boundary and clinical safety contract:
1. Schema Parsing & Field Validation
2. Category A: Actual measurements supplied by CAD
3. Category B: Legitimately transformed domain observations
4. Category C Prohibition: Strict rejection of fabricated physiology
   - No SpO2 = 90 when respiratory distress reported
   - No BP = 140/90 when hypertension reported
   - No Temp = 38.5 for infection
   - No GCS inferred from qualitative consciousness
   - No Pain Score = 6 for chest pain / trauma
   - No Glucose = 160 for diabetes
   - No resting physiological defaults (85, 96, 120, 80, 18, 37.0, 110)
5. ML Boundary & Immutability (protected model executed only on complete verified data)
6. Clear Rejection when Clinical Measurements are Missing (HTTP 422, no guessing)
7. M2M Scoping & Authentication
8. Durable Idempotency
9. Observability & Telemetry Exposition
10. Backwards Compatibility
"""

import time
import unittest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import manager
from api.adapters import (
    CADIntakePayload,
    CADLocationInput,
    CADCallerInfo,
    CADPatientInput,
    CADSymptoms,
    CADMedicalHistory,
    CADInjury,
    CADVitals,
    CADTriageMapper,
    CADNormalizationError,
    m2m_store,
    TEST_M2M_CAD_KEY,
    TEST_M2M_GPS_KEY,
    TEST_M2M_HOSPITAL_KEY,
    TEST_M2M_TRAFFIC_KEY,
    TEST_M2M_OMNI_KEY,
)
from api.auth.models import Role, Permission
from api.auth.security import create_test_token
from api.observability.metrics import metrics_collector
from api.settings import settings


class TestM13Phase3RealisticCAD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manager.initialize()
        cls.client = TestClient(app)
        m2m_store.reset_test_credentials()

        cls.dispatcher_token = create_test_token(role=Role.DISPATCHER, username="test_cad_dispatcher")
        cls.admin_token = create_test_token(role=Role.ADMINISTRATOR, username="test_cad_admin")

    def setUp(self):
        m2m_store.reset_test_credentials()

    # Helper to generate a complete, clinically verified set of vitals (Category A)
    def _create_verified_vitals(
        self,
        hr=78.0,
        spo2=98.0,
        sbp=122.0,
        dbp=78.0,
        rr=16.0,
        temp=36.8,
        gcs=15,
        glucose=95.0,
        pain=2,
        ox="No Oxygen",
    ):
        return CADVitals(
            heart_rate=hr,
            spo2=spo2,
            systolic_bp=sbp,
            diastolic_bp=dbp,
            respiratory_rate=rr,
            temperature=temp,
            gcs=gcs,
            blood_glucose=glucose,
            pain_score=pain,
            oxygen_requirement=ox,
        )

    # ==================================================================
    # 1. CAD SCHEMA VALIDATION
    # ==================================================================

    def test_01_valid_realistic_cad_payload_parsed(self):
        """Verify that a realistic external CAD payload parses cleanly."""
        payload = CADIntakePayload(
            external_incident_id="CAD-2026-001",
            source="METRO_911",
            call_type="CHEST_PAIN",
            chief_complaint="62yo male reporting acute retrosternal pressure radiating to jaw",
            location=CADLocationInput(
                latitude=28.6139,
                longitude=77.2090,
                address="10 Connaught Place, New Delhi",
            ),
            caller=CADCallerInfo(
                caller_name="Jane Doe",
                callback_phone="+919876543210",
                caller_type="BYSTANDER",
            ),
            patient=CADPatientInput(
                age=62,
                sex="Male",
                consciousness="Alert",
                symptoms=CADSymptoms(chest_pain=True, respiratory_distress=False),
                medical_history=CADMedicalHistory(hypertension=True, heart_disease=True),
            ),
        )
        self.assertEqual(payload.external_incident_id, "CAD-2026-001")
        self.assertEqual(payload.source, "METRO_911")
        self.assertEqual(payload.call_type, "CHEST_PAIN")
        self.assertEqual(payload.location.latitude, 28.6139)
        self.assertEqual(payload.patient.age, 62)

    def test_02_missing_required_fields_rejected(self):
        """Missing external_incident_id, call_type, or coordinates must raise validation error."""
        with self.assertRaises(Exception):
            CADIntakePayload(
                call_type="CARDIAC",
                location=CADLocationInput(latitude=28.6, longitude=77.2),
            )
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-002",
                location=CADLocationInput(latitude=28.6, longitude=77.2),
            )
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-003",
                call_type="CARDIAC",
            )

    def test_03_malformed_location_coordinates_rejected(self):
        """Latitude > 90 or Longitude > 180 must fail validation."""
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-BAD-LAT",
                call_type="CARDIAC",
                location=CADLocationInput(latitude=95.0, longitude=77.2),
            )
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-BAD-LON",
                call_type="CARDIAC",
                latitude=28.6,
                longitude=195.0,
            )

    def test_04_malformed_timestamp_rejected(self):
        """Malformed or future timestamp must be rejected."""
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-BAD-TIME",
                call_type="CARDIAC",
                location=CADLocationInput(latitude=28.6, longitude=77.2),
                occurred_at="not-a-timestamp",
            )
        far_future = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        with self.assertRaises(Exception):
            CADIntakePayload(
                external_incident_id="CAD-FUTURE-TIME",
                call_type="CARDIAC",
                location=CADLocationInput(latitude=28.6, longitude=77.2),
                occurred_at=far_future,
            )

    # ==================================================================
    # 2. CATEGORY A & B: VALIDATED EXTRACTION & DOMAIN TRANSFORMATIONS
    # ==================================================================

    def test_05_category_a_actual_measurements_faithfully_mapped(self):
        """Category A: Actual measurements supplied by CAD are faithfully preserved."""
        v = self._create_verified_vitals(
            hr=104.0,
            spo2=91.0,
            sbp=155.0,
            dbp=95.0,
            rr=24.0,
            temp=38.9,
            gcs=12,
            glucose=185.0,
            pain=7,
            ox="Oxygen Mask",
        )
        payload = CADIntakePayload(
            external_incident_id="CAD-A-01",
            call_type="CARDIAC",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(
                age=60,
                sex="Male",
                vitals=v,
            ),
        )
        norm = CADTriageMapper.normalize(payload)
        self.assertEqual(norm["Heart_Rate"], 104.0)
        self.assertEqual(norm["SpO2"], 91.0)
        self.assertEqual(norm["Systolic_BP"], 155.0)
        self.assertEqual(norm["Diastolic_BP"], 95.0)
        self.assertEqual(norm["Respiratory_Rate"], 24.0)
        self.assertEqual(norm["Temperature"], 38.9)
        self.assertEqual(norm["GCS"], 12)
        self.assertEqual(norm["Blood_Glucose"], 185.0)
        self.assertEqual(norm["Pain_Score"], 7)
        self.assertEqual(norm["Oxygen_Requirement"], "Oxygen Mask")

    def test_06_category_b_domain_transformations(self):
        """Category B: Legitimate transformations (call_type->Condition, Arrival_Mode, etc.)."""
        payload = CADIntakePayload(
            external_incident_id="CAD-B-01",
            source="TEST_SOURCE",
            call_type="CHEST_PAIN",
            location=CADLocationInput(latitude=28.6139, longitude=77.2090),
            patient=CADPatientInput(
                age=55,
                sex="Female",
                symptoms=CADSymptoms(chest_pain=True, bleeding=False),
                medical_history=CADMedicalHistory(hypertension=True, diabetes=False),
                vitals=self._create_verified_vitals(),
            ),
        )
        norm = CADTriageMapper.normalize(payload)
        # Category B transforms
        self.assertEqual(norm["Condition"], "Cardiac")
        self.assertEqual(norm["Arrival_Mode"], "Ambulance")
        self.assertEqual(norm["Injury_Type"], "No Injury")
        self.assertEqual(norm["Chest_Pain"], 1)
        self.assertEqual(norm["Bleeding"], 0)
        self.assertEqual(norm["Hypertension"], 1)
        self.assertEqual(norm["Diabetes"], 0)
        self.assertEqual(norm["external_incident_id"], "CAD-B-01")
        self.assertEqual(norm["source"], "TEST_SOURCE")

    def test_07_normalization_call_type_mapping(self):
        """Call types must normalize into clinical condition categories."""
        mappings = [
            ("CHEST_PAIN", "Cardiac"),
            ("ASTHMA", "Respiratory"),
            ("FALL", "Trauma"),
            ("STROKE", "Neurological"),
            ("SEPSIS", "Infection"),
            ("ABDOMINAL_PAIN", "Gastrointestinal"),
            ("OVERDOSE", "Other"),
        ]
        for ctype, expected_cond in mappings:
            payload = CADIntakePayload(
                external_incident_id=f"CAD-{ctype}",
                call_type=ctype,
                location=CADLocationInput(latitude=28.6, longitude=77.2),
                patient=CADPatientInput(age=50, sex="Female", vitals=self._create_verified_vitals()),
            )
            res = CADTriageMapper.normalize(payload)
            self.assertEqual(res["Condition"], expected_cond)

    def test_08_normalization_deterministic_mapping(self):
        """Identical inputs produce identical normalized dictionaries."""
        payload = CADIntakePayload(
            external_incident_id="CAD-DET-01",
            call_type="CARDIAC",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(age=55, sex="Female", vitals=self._create_verified_vitals()),
        )
        res1 = CADTriageMapper.normalize(payload)
        res2 = CADTriageMapper.normalize(payload)
        self.assertEqual(res1, res2)

    def test_09_normalization_no_fabricated_severity(self):
        """Mapper output must NOT calculate or contain severity predictions."""
        payload = CADIntakePayload(
            external_incident_id="CAD-SEV-CHECK",
            call_type="CARDIAC",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(age=70, sex="Male", vitals=self._create_verified_vitals()),
        )
        res = CADTriageMapper.normalize(payload)
        self.assertNotIn("predicted_severity", res)
        self.assertNotIn("confidence", res)
        self.assertNotIn("priority", res)

    # ==================================================================
    # 3. CATEGORY C PROHIBITION: NO FABRICATION OF CLINICAL MEASUREMENTS
    # ==================================================================

    def test_10_missing_vitals_extracted_as_none_never_defaulted(self):
        """Missing clinical measurements remain None in extract_available_fields (no fabrication)."""
        payload = CADIntakePayload(
            external_incident_id="CAD-RAW-01",
            call_type="CHEST_PAIN",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(
                age=58,
                sex="Male",
                consciousness="Alert",
                symptoms=CADSymptoms(chest_pain=True, respiratory_distress=True),
                medical_history=CADMedicalHistory(hypertension=True, diabetes=True),
            ),
        )
        extracted = CADTriageMapper.extract_available_fields(payload)

        # Category B fields present
        self.assertEqual(extracted["Condition"], "Cardiac")
        self.assertEqual(extracted["Age"], 58)
        self.assertEqual(extracted["Sex"], "Male")

        # Category C STRICTLY FORBIDDEN: Unmeasured vitals MUST NOT be defaulted
        self.assertIsNone(extracted["Heart_Rate"], "Heart_Rate must not be defaulted to 85")
        self.assertIsNone(extracted["SpO2"], "SpO2 must not be defaulted to 96 or 90")
        self.assertIsNone(extracted["Systolic_BP"], "Systolic_BP must not be defaulted to 120 or 140")
        self.assertIsNone(extracted["Diastolic_BP"], "Diastolic_BP must not be defaulted to 80 or 90")
        self.assertIsNone(extracted["Respiratory_Rate"], "Respiratory_Rate must not be defaulted to 18 or 26")
        self.assertIsNone(extracted["Temperature"], "Temperature must not be defaulted to 37.0 or 38.5")
        self.assertIsNone(extracted["GCS"], "GCS must not be inferred from consciousness")
        self.assertIsNone(extracted["Blood_Glucose"], "Blood_Glucose must not be defaulted to 110 or 160")
        self.assertIsNone(extracted["Pain_Score"], "Pain_Score must not be defaulted to 6")
        self.assertIsNone(extracted["Oxygen_Requirement"], "Oxygen_Requirement must not be defaulted")

    def test_11_normalize_refuses_missing_clinical_measurements(self):
        """normalize() raises CADNormalizationError when required measurements are absent."""
        payload = CADIntakePayload(
            external_incident_id="CAD-INCOMPLETE-01",
            call_type="CARDIAC",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(
                age=50,
                sex="Male",
                consciousness="Alert",
            ),
        )
        with self.assertRaises(CADNormalizationError) as ctx:
            CADTriageMapper.normalize(payload)
        err_msg = str(ctx.exception)
        self.assertIn("Missing required clinical measurement(s)", err_msg)
        self.assertIn("Heart_Rate", err_msg)
        self.assertIn("SpO2", err_msg)
        self.assertIn("Systolic_BP", err_msg)
        self.assertIn("GCS", err_msg)

    def test_12_prohibited_respiratory_distress_spo2_fabrication(self):
        """System strictly does NOT fabricate SpO2=90 when respiratory distress is reported."""
        payload = CADIntakePayload(
            external_incident_id="CAD-RESP-DISTRESS",
            call_type="RESPIRATORY",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(
                age=45,
                sex="Female",
                symptoms=CADSymptoms(respiratory_distress=True),
            ),
        )
        extracted = CADTriageMapper.extract_available_fields(payload)
        self.assertEqual(extracted["Respiratory_Distress"], 1)
        self.assertIsNone(extracted["SpO2"], "SpO2 must NOT be fabricated as 90")
        self.assertIsNone(extracted["Respiratory_Rate"], "RR must NOT be fabricated as 26")

    def test_13_prohibited_hypertension_bp_fabrication(self):
        """System strictly does NOT fabricate BP=140/90 when hypertension is reported."""
        payload = CADIntakePayload(
            external_incident_id="CAD-HTN",
            call_type="CARDIAC",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(
                age=65,
                sex="Male",
                medical_history=CADMedicalHistory(hypertension=True),
            ),
        )
        extracted = CADTriageMapper.extract_available_fields(payload)
        self.assertEqual(extracted["Hypertension"], 1)
        self.assertIsNone(extracted["Systolic_BP"], "Systolic_BP must NOT be fabricated as 140")
        self.assertIsNone(extracted["Diastolic_BP"], "Diastolic_BP must NOT be fabricated as 90")

    def test_14_prohibited_infection_temp_fabrication(self):
        """System strictly does NOT fabricate Temperature=38.5 for infection."""
        payload = CADIntakePayload(
            external_incident_id="CAD-INFECT",
            call_type="INFECTION",
            location=CADLocationInput(latitude=28.6, longitude=77.2),
            patient=CADPatientInput(age=35, sex="Female"),
        )
        extracted = CADTriageMapper.extract_available_fields(payload)
        self.assertEqual(extracted["Condition"], "Infection")
        self.assertIsNone(extracted["Temperature"], "Temperature must NOT be fabricated as 38.5")

    def test_15_prohibited_gcs_inference_from_consciousness(self):
        """System strictly does NOT infer numeric GCS from qualitative consciousness."""
        for c_state in ["Alert", "Confused", "Drowsy", "Unconscious"]:
            payload = CADIntakePayload(
                external_incident_id=f"CAD-CONSC-{c_state}",
                call_type="OTHER",
                location=CADLocationInput(latitude=28.6, longitude=77.2),
                patient=CADPatientInput(age=40, sex="Male", consciousness=c_state),
            )
            extracted = CADTriageMapper.extract_available_fields(payload)
            self.assertEqual(extracted["Consciousness"], c_state)
            self.assertIsNone(extracted["GCS"], f"GCS must NOT be inferred from consciousness {c_state}")

    # ==================================================================
    # 4. ML BOUNDARY & ENDPOINT BEHAVIOR
    # ==================================================================

    def test_16_ml_boundary_protected_model_invoked_on_verified_intake(self):
        """Endpoint /cad/intake executes protected clinical model when vitals are provided."""
        payload = {
            "external_incident_id": f"cad_verified_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CHEST_PAIN",
            "chief_complaint": "Acute retrosternal chest pain with diaphoresis",
            "location": {"latitude": 28.6139, "longitude": 77.2090},
            "patient": {
                "age": 65,
                "sex": "Male",
                "consciousness": "Alert",
                "symptoms": {"chest_pain": True, "respiratory_distress": False, "pain_score": 8},
                "medical_history": {"heart_disease": True, "hypertension": True},
                "vitals": {
                    "heart_rate": 96.0,
                    "spo2": 95.0,
                    "systolic_bp": 142.0,
                    "diastolic_bp": 88.0,
                    "respiratory_rate": 20.0,
                    "temperature": 37.1,
                    "gcs": 15,
                    "blood_glucose": 128.0,
                    "pain_score": 8,
                    "oxygen_requirement": "Nasal Cannula",
                },
            },
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual(data["status"], "ACCEPTED")
        self.assertIn("predicted_severity", data["result"]["patient"])
        self.assertIn("confidence", data["result"]["patient"])
        self.assertGreater(data["result"]["patient"]["confidence"], 0.0)

    def test_17_ml_boundary_distinct_verified_vitals_yield_distinct_predictions(self):
        """Distinct verified clinical inputs yield distinct authoritative ML predictions."""
        # Critical presentation
        payload_crit = {
            "external_incident_id": f"cad_crit_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "TRAUMA",
            "location": {"latitude": 28.6139, "longitude": 77.2090},
            "patient": {
                "age": 45,
                "sex": "Male",
                "consciousness": "Unconscious",
                "symptoms": {"bleeding": True, "respiratory_distress": True, "pain_score": 10},
                "injury": {"has_injury": True, "injury_type": "Internal Injury"},
                "vitals": {
                    "gcs": 4,
                    "spo2": 78.0,
                    "heart_rate": 142.0,
                    "systolic_bp": 68.0,
                    "diastolic_bp": 38.0,
                    "respiratory_rate": 36.0,
                    "temperature": 35.4,
                    "blood_glucose": 65.0,
                    "pain_score": 10,
                    "oxygen_requirement": "Ventilator",
                },
            },
        }
        res_crit = self.client.post(
            "/ingestion/cad/intake",
            json=payload_crit,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res_crit.status_code, 200)
        crit_severity = res_crit.json()["result"]["patient"]["predicted_severity"]

        # Mild presentation
        payload_mild = {
            "external_incident_id": f"cad_mild_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "OTHER",
            "location": {"latitude": 28.6139, "longitude": 77.2090},
            "patient": {
                "age": 22,
                "sex": "Female",
                "consciousness": "Alert",
                "symptoms": {"chest_pain": False, "respiratory_distress": False, "pain_score": 1},
                "vitals": {
                    "gcs": 15,
                    "spo2": 99.0,
                    "heart_rate": 72.0,
                    "systolic_bp": 118.0,
                    "diastolic_bp": 76.0,
                    "respiratory_rate": 14.0,
                    "temperature": 36.6,
                    "blood_glucose": 90.0,
                    "pain_score": 1,
                    "oxygen_requirement": "No Oxygen",
                },
            },
        }
        res_mild = self.client.post(
            "/ingestion/cad/intake",
            json=payload_mild,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res_mild.status_code, 200)
        mild_severity = res_mild.json()["result"]["patient"]["predicted_severity"]

        self.assertNotEqual(crit_severity, mild_severity)

    def test_18_endpoint_rejects_missing_clinical_measurements_with_422(self):
        """Incomplete CAD call (no vitals) is rejected with 422, stating exact missing fields."""
        payload_no_vitals = {
            "external_incident_id": f"cad_incomplete_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {
                "age": 55,
                "sex": "Female",
                "consciousness": "Alert",
            },
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload_no_vitals,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 422)
        detail = res.json()["detail"]
        self.assertIn("Missing required clinical measurement(s)", detail)
        self.assertIn("Heart_Rate", detail)
        self.assertIn("strictly prohibits fabricating patient physiological measurements", detail)

    # ==================================================================
    # 5. M2M AUTHENTICATION & PROVIDER SCOPING
    # ==================================================================

    def test_19_m2m_auth_valid_cad_credential_accepted(self):
        """Valid CAD M2M key succeeds with 200 OK on complete verified payload."""
        payload = {
            "external_incident_id": f"cad_auth_ok_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {
                "age": 50,
                "sex": "Male",
                "vitals": {
                    "heart_rate": 80.0, "spo2": 98.0, "systolic_bp": 120.0, "diastolic_bp": 80.0,
                    "respiratory_rate": 16.0, "temperature": 37.0, "gcs": 15, "blood_glucose": 100.0,
                    "pain_score": 0, "oxygen_requirement": "No Oxygen",
                },
            },
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 200)

    def test_20_m2m_auth_missing_credential_rejected(self):
        """Unauthenticated request to /cad/intake is rejected with 401."""
        payload = {
            "external_incident_id": "cad_unauth",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 50, "sex": "Male"},
        }
        orig = settings.dev_auth_fallback
        settings.dev_auth_fallback = False
        try:
            res = self.client.post("/ingestion/cad/intake", json=payload)
            self.assertEqual(res.status_code, 401)
        finally:
            settings.dev_auth_fallback = orig

    def test_21_m2m_auth_invalid_credential_rejected(self):
        """Invalid API key to /cad/intake is rejected with 401."""
        payload = {
            "external_incident_id": "cad_invalid_key",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 50, "sex": "Male"},
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": "raah_m2m_cad_forged_key_00000"},
        )
        self.assertEqual(res.status_code, 401)

    def test_22_m2m_auth_gps_credential_rejected(self):
        """GPS key attempting to post to CAD intake is rejected with 403 Forbidden."""
        payload = {
            "external_incident_id": "cad_gps_mismatch",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 50, "sex": "Male"},
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_GPS_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not permit 'INCIDENT_CALL'", res.json()["detail"])

    def test_23_m2m_auth_hospital_credential_rejected(self):
        """Hospital key attempting to post to CAD intake is rejected with 403 Forbidden."""
        payload = {
            "external_incident_id": "cad_hosp_mismatch",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 50, "sex": "Male"},
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_HOSPITAL_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not permit 'INCIDENT_CALL'", res.json()["detail"])

    def test_24_m2m_auth_traffic_credential_rejected(self):
        """Traffic key attempting to post to CAD intake is rejected with 403 Forbidden."""
        payload = {
            "external_incident_id": "cad_traffic_mismatch",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 50, "sex": "Male"},
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_TRAFFIC_KEY},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not permit 'INCIDENT_CALL'", res.json()["detail"])

    def test_25_m2m_auth_omni_credential_accepted(self):
        """Omni key with all event scopes is accepted on CAD intake."""
        payload = {
            "external_incident_id": f"cad_omni_{time.time_ns()}",
            "source": "OMNI_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {
                "age": 50,
                "sex": "Male",
                "vitals": {
                    "heart_rate": 80.0, "spo2": 98.0, "systolic_bp": 120.0, "diastolic_bp": 80.0,
                    "respiratory_rate": 16.0, "temperature": 37.0, "gcs": 15, "blood_glucose": 100.0,
                    "pain_score": 0, "oxygen_requirement": "No Oxygen",
                },
            },
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_OMNI_KEY},
        )
        self.assertEqual(res.status_code, 200)

    def test_26_auth_operator_jwt_accepted(self):
        """Operator JWT token with dispatch permission is accepted on CAD intake."""
        payload = {
            "external_incident_id": f"cad_jwt_{time.time_ns()}",
            "source": "CAD_911",
            "call_type": "RESPIRATORY",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {
                "age": 42,
                "sex": "Female",
                "vitals": {
                    "heart_rate": 84.0, "spo2": 97.0, "systolic_bp": 118.0, "diastolic_bp": 76.0,
                    "respiratory_rate": 18.0, "temperature": 36.9, "gcs": 15, "blood_glucose": 95.0,
                    "pain_score": 0, "oxygen_requirement": "No Oxygen",
                },
            },
        }
        res = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res.status_code, 200)

    # ==================================================================
    # 6. IDEMPOTENCY
    # ==================================================================

    def test_27_idempotency_duplicate_cad_event_cached_without_mutation(self):
        """Submitting duplicate external CAD incident returns cached outcome with DUPLICATE status."""
        cad_id = f"cad_idem_test_{time.time_ns()}"
        payload = {
            "external_incident_id": cad_id,
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6139, "longitude": 77.2090},
            "patient": {
                "age": 60,
                "sex": "Male",
                "vitals": {
                    "heart_rate": 82.0, "spo2": 97.0, "systolic_bp": 122.0, "diastolic_bp": 82.0,
                    "respiratory_rate": 16.0, "temperature": 36.8, "gcs": 15, "blood_glucose": 105.0,
                    "pain_score": 0, "oxygen_requirement": "No Oxygen",
                },
            },
        }

        # 1st Submission -> ACCEPTED
        res1 = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["status"], "ACCEPTED")
        inc_id1 = data1["result"]["incident_id"]

        # 2nd Submission (Exact duplicate) -> DUPLICATE
        res2 = self.client.post(
            "/ingestion/cad/intake",
            json=payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["status"], "DUPLICATE")
        self.assertGreaterEqual(data2["seen_count"], 2)
        self.assertEqual(data2["result"]["incident_id"], inc_id1)

    # ==================================================================
    # 7. OBSERVABILITY & STATUS
    # ==================================================================

    def test_28_observability_cad_intake_metrics_recorded(self):
        """Verify that accepted, rejected, and validation metrics are recorded."""
        snap_before = metrics_collector.get_snapshot()["cad_intake"]

        # Generate successful intake
        payload_ok = {
            "external_incident_id": f"cad_obs_ok_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {
                "age": 55,
                "sex": "Female",
                "vitals": {
                    "heart_rate": 75.0, "spo2": 99.0, "systolic_bp": 115.0, "diastolic_bp": 75.0,
                    "respiratory_rate": 14.0, "temperature": 36.7, "gcs": 15, "blood_glucose": 90.0,
                    "pain_score": 0, "oxygen_requirement": "No Oxygen",
                },
            },
        }
        self.client.post(
            "/ingestion/cad/intake",
            json=payload_ok,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )

        # Generate validation failure (missing coordinates)
        payload_bad = {
            "external_incident_id": f"cad_obs_bad_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
        }
        self.client.post(
            "/ingestion/cad/intake",
            json=payload_bad,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )

        # Generate normalization failure (missing vitals)
        payload_norm_err = {
            "external_incident_id": f"cad_obs_norm_{time.time_ns()}",
            "source": "CAD_MOCK",
            "call_type": "CARDIAC",
            "location": {"latitude": 28.6, "longitude": 77.2},
            "patient": {"age": 55, "sex": "Female"},
        }
        self.client.post(
            "/ingestion/cad/intake",
            json=payload_norm_err,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )

        snap_after = metrics_collector.get_snapshot()["cad_intake"]
        self.assertGreater(snap_after["accepted_total"], snap_before["accepted_total"])
        self.assertGreater(snap_after["validation_failures_total"], snap_before["validation_failures_total"])
        self.assertGreater(snap_after["normalization_failures_total"], snap_before["normalization_failures_total"])

    def test_29_observability_status_and_metrics_endpoints(self):
        """GET /ingestion/status and GET /metrics expose cad_intake data."""
        # /ingestion/status
        res_status = self.client.get(
            "/ingestion/status",
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res_status.status_code, 200)
        data_status = res_status.json()
        self.assertIn("cad_intake", data_status)
        self.assertIn("accepted_total", data_status["cad_intake"])

        # /metrics
        res_metrics = self.client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {self.dispatcher_token}"},
        )
        self.assertEqual(res_metrics.status_code, 200)
        data_metrics = res_metrics.json()
        self.assertIn("cad_intake", data_metrics)

    # ==================================================================
    # 8. BACKWARDS COMPATIBILITY
    # ==================================================================

    def test_30_backward_compatibility_cad_incident_endpoint_preserved(self):
        """Existing /ingestion/cad/incident endpoint continues to function unchanged."""
        legacy_payload = {
            "source_event_id": f"cad_legacy_{time.time_ns()}",
            "source": "CAD_MOCK",
            "patient_lat": 28.6139,
            "patient_lon": 77.2090,
            "Condition": "Cardiac",
            "Age": 58,
            "Sex": "Male",
        }
        res = self.client.post(
            "/ingestion/cad/incident",
            json=legacy_payload,
            headers={"X-API-Key": TEST_M2M_CAD_KEY},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ACCEPTED")
        self.assertIn("result", data)


if __name__ == "__main__":
    unittest.main()
