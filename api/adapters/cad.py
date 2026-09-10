"""
RAAH Realistic CAD Intake Schema & Triage Mapper
================================================

Defines the realistic external CAD incident schema and the deterministic
CAD normalization / triage mapping layer.

Architectural & Clinical Safety Guarantees:
1. Clinical ML model remains authoritative and immutable; this mapper NEVER calculates,
   overrides, replaces, or reinterprets ML severity predictions.
2. Clinical measurements (Heart Rate, BP, SpO2, RR, Temperature, GCS, Glucose, Pain Score)
   are NEVER fabricated or defaulted.
3. Distinguishes strictly between:
   A. Actual measurements supplied by CAD / telemetry.
   B. Legitimately transformed domain observations (call_type -> Condition, Arrival_Mode = "Ambulance",
      non-trauma medical calls -> "No Injury", boolean triage flags -> 0/1).
   C. Guessed/defaulted physiological values — STRICTLY PROHIBITED.
4. When required clinical measurements are absent, the mapper refuses to invent synthetic
   physiology and raises a clear CADNormalizationError.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


class CADNormalizationError(ValueError):
    """Raised when an external CAD payload cannot be safely or honestly normalized."""
    pass


# ======================================================================
# 1. REALISTIC EXTERNAL CAD SCHEMA
# ======================================================================

class CADLocationInput(BaseModel):
    """Geographic location of the emergency incident."""
    latitude: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude")
    address: Optional[str] = Field(default=None, description="Physical street address")
    cross_street: Optional[str] = Field(default=None, description="Intersection or cross street")
    landmark: Optional[str] = Field(default=None, description="Nearby landmark or venue name")


class CADCallerInfo(BaseModel):
    """Information regarding the reporting party / 911 caller."""
    caller_name: Optional[str] = Field(default=None, description="Reporting party name")
    callback_phone: Optional[str] = Field(default=None, description="Callback telephone number")
    caller_type: Optional[str] = Field(
        default=None,
        description="Caller relationship: PATIENT | FAMILY | BYSTANDER | FIRST_RESPONDER | FACILITY",
    )


class CADSymptoms(BaseModel):
    """Emergency Medical Dispatch (EMD) symptom observations."""
    chest_pain: Optional[bool] = Field(default=None, description="Patient complains of chest pain/pressure")
    respiratory_distress: Optional[bool] = Field(default=None, description="Patient exhibiting breathing difficulty")
    bleeding: Optional[bool] = Field(default=None, description="Active external bleeding reported")
    seizure: Optional[bool] = Field(default=None, description="Active or recent seizure activity")
    pain_score: Optional[int] = Field(default=None, ge=0, le=10, description="Caller-reported pain level (0-10)")


class CADMedicalHistory(BaseModel):
    """Known pre-existing patient medical history."""
    diabetes: Optional[bool] = Field(default=None, description="History of Type 1 or Type 2 Diabetes")
    hypertension: Optional[bool] = Field(default=None, description="History of Chronic Hypertension")
    heart_disease: Optional[bool] = Field(default=None, description="History of Ischemic or Congestive Heart Disease")
    respiratory_disease: Optional[bool] = Field(default=None, description="History of COPD / Asthma / Chronic Lung Disease")


class CADInjury(BaseModel):
    """Trauma and physical injury details."""
    has_injury: Optional[bool] = Field(default=None, description="Whether trauma/injury is present")
    injury_type: Optional[str] = Field(
        default=None,
        description="Burn | Fracture | Head Injury | Internal Injury | Laceration | No Injury",
    )


class CADVitals(BaseModel):
    """On-scene, telemetry, or caller-reported clinical vitals."""
    heart_rate: Optional[float] = Field(default=None, ge=20.0, le=300.0, description="Heart rate (BPM)")
    spo2: Optional[float] = Field(default=None, ge=40.0, le=100.0, description="Oxygen saturation SpO2 (%)")
    systolic_bp: Optional[float] = Field(default=None, ge=40.0, le=300.0, description="Systolic Blood Pressure (mmHg)")
    diastolic_bp: Optional[float] = Field(default=None, ge=20.0, le=200.0, description="Diastolic Blood Pressure (mmHg)")
    respiratory_rate: Optional[float] = Field(default=None, ge=4.0, le=80.0, description="Breaths per minute")
    temperature: Optional[float] = Field(default=None, ge=30.0, le=45.0, description="Body temperature (°C)")
    gcs: Optional[int] = Field(default=None, ge=3, le=15, description="Glasgow Coma Scale score (3-15)")
    blood_glucose: Optional[float] = Field(default=None, ge=20.0, le=1000.0, description="Blood glucose level (mg/dL)")
    pain_score: Optional[int] = Field(default=None, ge=0, le=10, description="Measured or assessed pain score (0-10)")
    oxygen_requirement: Optional[str] = Field(
        default=None,
        description="No Oxygen | Nasal Cannula | Oxygen Mask | Ventilator",
    )


class CADPatientInput(BaseModel):
    """Patient demographics and triage presentation."""
    age: Optional[int] = Field(default=None, ge=0, le=125, description="Patient age in years")
    sex: Optional[str] = Field(default=None, description="Patient biological sex: Male | Female | M | F")
    consciousness: Optional[str] = Field(
        default=None,
        description="Mental status: Alert | Confused | Drowsy | Unconscious | A | V | P | U",
    )
    symptoms: Optional[CADSymptoms] = Field(default=None, description="Reported triage symptoms")
    medical_history: Optional[CADMedicalHistory] = Field(default=None, description="Known past medical history")
    injury: Optional[CADInjury] = Field(default=None, description="Trauma and injury details")
    vitals: Optional[CADVitals] = Field(default=None, description="Measured or reported vital signs")


class CADIntakePayload(BaseModel):
    """
    Realistic external CAD incident payload accepted at the integration boundary.
    Shields external providers from RAAH's internal 24-feature ML vector.
    """
    external_incident_id: str = Field(..., description="External CAD dispatch or CAD incident identifier")
    source: str = Field(default="CAD_911", description="Identifier of originating CAD provider system")
    occurred_at: Optional[str] = Field(default=None, description="ISO 8601 UTC timestamp of call intake")
    call_type: str = Field(..., description="CAD call or incident type (e.g. CHEST_PAIN, TRAUMA, MVA)")
    chief_complaint: Optional[str] = Field(default=None, description="Free text dispatch chief complaint description")
    priority_code: Optional[str] = Field(default=None, description="External CAD source priority (e.g. ECHO, DELTA, P1)")
    location: Optional[CADLocationInput] = Field(default=None, description="Structured incident location")
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0, description="Top-level latitude alternative")
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0, description="Top-level longitude alternative")
    caller: Optional[CADCallerInfo] = Field(default=None, description="Caller / reporting party details")
    patient: Optional[CADPatientInput] = Field(default=None, description="Patient triage context and presentation")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Extensible CAD provider metadata")

    @field_validator("external_incident_id", "source", "call_type")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise PydanticCustomError("value_error", "Field cannot be empty or whitespace.")
        return str(v).strip()

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v_str = str(v).strip()
        try:
            dt = datetime.fromisoformat(v_str.replace("Z", "+00:00"))
        except Exception as e:
            raise PydanticCustomError("value_error", f"Malformed ISO 8601 timestamp '{v_str}': {e}")
        now = datetime.now(timezone.utc)
        if dt > now + timedelta(minutes=5):
            raise PydanticCustomError("value_error", "occurred_at timestamp cannot be in the future (> 5m clock drift)")
        return v_str

    @model_validator(mode="after")
    def validate_coordinates_present(self):
        lat = self.location.latitude if self.location else self.latitude
        lon = self.location.longitude if self.location else self.longitude
        if lat is None or lon is None:
            raise PydanticCustomError(
                "value_error",
                "Incident coordinates required: provide either location.latitude/longitude or latitude/longitude.",
            )
        return self


# ======================================================================
# 2. DETERMINISTIC CAD TRIAGE MAPPER
# ======================================================================

class CADTriageMapper:
    """
    Deterministic normalization engine mapping realistic external CAD payloads
    into RAAH's internal 24-feature ML incident vector + coordinates.

    Strictly adheres to:
    A. Actual measurements supplied by CAD.
    B. Legitimately transformed domain observations.
    C. Absolute prohibition on fabricating patient physiological values.
    """

    # Complete list of 24 features required by RAAH's protected clinical ML model
    REQUIRED_ML_FEATURES: List[str] = [
        "Sex",
        "Condition",
        "Oxygen_Requirement",
        "Consciousness",
        "Injury_Type",
        "Arrival_Mode",
        "Age",
        "Heart_Rate",
        "SpO2",
        "Systolic_BP",
        "Diastolic_BP",
        "Respiratory_Rate",
        "Temperature",
        "GCS",
        "Pain_Score",
        "Blood_Glucose",
        "Respiratory_Distress",
        "Chest_Pain",
        "Bleeding",
        "Seizure",
        "Diabetes",
        "Hypertension",
        "Heart_Disease",
        "Respiratory_Disease",
    ]

    CALL_TYPE_MAP: Dict[str, str] = {
        # Cardiac
        "CARDIAC": "Cardiac",
        "CHEST_PAIN": "Cardiac",
        "CHEST PAIN": "Cardiac",
        "HEART_ATTACK": "Cardiac",
        "HEART ATTACK": "Cardiac",
        "CARDIAC_ARREST": "Cardiac",
        "CARDIAC ARREST": "Cardiac",
        "ANGINA": "Cardiac",
        "STEMI": "Cardiac",
        "PALPITATIONS": "Cardiac",
        "ARRHYTHMIA": "Cardiac",
        # Respiratory
        "RESPIRATORY": "Respiratory",
        "RESPIRATORY_DISTRESS": "Respiratory",
        "RESPIRATORY DISTRESS": "Respiratory",
        "ASTHMA": "Respiratory",
        "BREATHING_DIFFICULTY": "Respiratory",
        "BREATHING DIFFICULTY": "Respiratory",
        "SOB": "Respiratory",
        "DYSPNEA": "Respiratory",
        "COPD": "Respiratory",
        "CHOKING": "Respiratory",
        # Trauma
        "TRAUMA": "Trauma",
        "FALL": "Trauma",
        "FALLS": "Trauma",
        "MVA": "Trauma",
        "MOTOR_VEHICLE_ACCIDENT": "Trauma",
        "ACCIDENT": "Trauma",
        "ASSAULT": "Trauma",
        "FRACTURE": "Trauma",
        "LACERATION": "Trauma",
        "BURN": "Trauma",
        "BURNS": "Trauma",
        "STABBING": "Trauma",
        "GUNSHOT": "Trauma",
        "PENETRATING_TRAUMA": "Trauma",
        # Neurological
        "NEUROLOGICAL": "Neurological",
        "STROKE": "Neurological",
        "CVA": "Neurological",
        "SEIZURE": "Neurological",
        "SEIZURES": "Neurological",
        "SYNCOPE": "Neurological",
        "ALTERED_MENTAL": "Neurological",
        "ALTERED_MENTAL_STATUS": "Neurological",
        "UNCONSCIOUS": "Neurological",
        "HEADACHE": "Neurological",
        # Infection
        "INFECTION": "Infection",
        "SEPSIS": "Infection",
        "FEVER": "Infection",
        "PNEUMONIA": "Infection",
        "SEPTIC_SHOCK": "Infection",
        # Gastrointestinal
        "GASTROINTESTINAL": "Gastrointestinal",
        "ABDOMINAL_PAIN": "Gastrointestinal",
        "ABDOMINAL PAIN": "Gastrointestinal",
        "GI_BLEED": "Gastrointestinal",
        "VOMITING": "Gastrointestinal",
        "BOWEL_OBSTRUCTION": "Gastrointestinal",
        # Other
        "OTHER": "Other",
        "GENERAL_ILLNESS": "Other",
        "GENERAL_MEDICAL": "Other",
        "OVERDOSE": "Other",
        "ALLERGIC_REACTION": "Other",
        "ANAPHYLAXIS": "Other",
        "POISONING": "Other",
        "UNSPECIFIED": "Other",
    }

    VALID_CONDITIONS = {
        "Cardiac",
        "Gastrointestinal",
        "Infection",
        "Neurological",
        "Other",
        "Respiratory",
        "Trauma",
    }

    VALID_INJURIES = {
        "Burn",
        "Fracture",
        "Head Injury",
        "Internal Injury",
        "Laceration",
        "No Injury",
    }

    VALID_CONSCIOUSNESS = {
        "Alert",
        "Confused",
        "Drowsy",
        "Unconscious",
    }

    VALID_OXYGEN = {
        "Nasal Cannula",
        "No Oxygen",
        "Oxygen Mask",
        "Ventilator",
    }

    @classmethod
    def extract_available_fields(cls, payload: CADIntakePayload) -> Dict[str, Any]:
        """
        Extract legitimately available and verified fields without fabricating any physiology.
        
        Fields that are not measured or verified are returned as None.
        """
        # 1. Condition Normalization (Category B: Deterministic Domain Map)
        norm_call_type = payload.call_type.strip().upper()
        condition = cls.CALL_TYPE_MAP.get(norm_call_type)
        if not condition:
            for c in cls.VALID_CONDITIONS:
                if norm_call_type == c.upper():
                    condition = c
                    break

        if not condition:
            raise CADNormalizationError(
                f"Cannot safely map external call_type '{payload.call_type}' to clinical condition. "
                f"Recognized types include: CARDIAC, RESPIRATORY, TRAUMA, NEUROLOGICAL, INFECTION, GASTROINTESTINAL, OTHER."
            )

        # 2. Demographics (Category B: Validated Input)
        if payload.patient is None:
            raise CADNormalizationError(
                "Missing patient context. CAD intake requires patient presentation/demographics "
                "to safely evaluate clinical triage without fabricating clinical facts."
            )

        if payload.patient.age is None:
            raise CADNormalizationError(
                "Missing required clinical field 'patient.age'. Patient age cannot safely be derived or fabricated."
            )
        age = int(payload.patient.age)
        if not (0 <= age <= 125):
            raise CADNormalizationError(f"Invalid patient age {age}. Must be between 0 and 125.")

        if not payload.patient.sex or not str(payload.patient.sex).strip():
            raise CADNormalizationError(
                "Missing required clinical field 'patient.sex'. Patient sex cannot safely be derived or fabricated."
            )
        raw_sex = str(payload.patient.sex).strip().upper()
        if raw_sex in ("M", "MALE"):
            sex = "Male"
        elif raw_sex in ("F", "FEMALE"):
            sex = "Female"
        else:
            raise CADNormalizationError(
                f"Unrecognized patient sex '{payload.patient.sex}'. Must be 'Male' or 'Female'."
            )

        # 3. Arrival Mode (Category B: Domain constant: CAD 911 dispatch requests ambulance unit)
        arrival_mode = "Ambulance"

        # 4. Mental Status / Consciousness (Category B: Validated Qualitative Presentation)
        # If consciousness is not explicitly supplied by CAD/EMD, do NOT default to Alert.
        symptoms = payload.patient.symptoms
        history = payload.patient.medical_history
        injury = payload.patient.injury
        vitals = payload.patient.vitals

        consciousness = None
        raw_c = None
        if payload.patient.consciousness:
            raw_c = str(payload.patient.consciousness).strip()
        elif symptoms and symptoms.consciousness_status:
            raw_c = str(symptoms.consciousness_status).strip()

        if raw_c:
            raw_c_up = raw_c.upper()
            if raw_c_up in ("ALERT", "A"):
                consciousness = "Alert"
            elif raw_c_up in ("CONFUSED", "C", "P"):
                consciousness = "Confused"
            elif raw_c_up in ("DROWSY", "D", "V"):
                consciousness = "Drowsy"
            elif raw_c_up in ("UNCONSCIOUS", "U", "UNRESPONSIVE"):
                consciousness = "Unconscious"
            else:
                for cand in cls.VALID_CONSCIOUSNESS:
                    if cand.lower() == raw_c.lower():
                        consciousness = cand
                        break

        # Only map from call_type if it unambiguously establishes consciousness
        if not consciousness and norm_call_type == "UNCONSCIOUS":
            consciousness = "Unconscious"

        # 5. EMD Symptoms & History Flags (Category B: Reported Boolean Responses -> 0/1)
        chest_pain = 1 if (symptoms and symptoms.chest_pain is True) else 0
        resp_distress = 1 if (symptoms and symptoms.respiratory_distress is True) else 0
        bleeding = 1 if (symptoms and symptoms.bleeding is True) else 0
        seizure = 1 if (symptoms and symptoms.seizure is True) else 0

        # Deterministic call_type indication of primary presenting symptoms
        if norm_call_type in ("CHEST_PAIN", "CHEST PAIN", "ANGINA", "STEMI"):
            chest_pain = 1
        if norm_call_type in ("SEIZURE", "SEIZURES"):
            seizure = 1
        if norm_call_type in ("RESPIRATORY_DISTRESS", "RESPIRATORY DISTRESS", "SOB", "DYSPNEA"):
            resp_distress = 1

        diabetes = 1 if (history and history.diabetes is True) else 0
        hypertension = 1 if (history and history.hypertension is True) else 0
        heart_disease = 1 if (history and history.heart_disease is True) else 0
        resp_disease = 1 if (history and history.respiratory_disease is True) else 0

        # 6. Injury Type Normalization (Category B)
        # Safe Contract:
        # Only map Injury_Type when:
        # - CAD explicitly supplies a valid injury type, OR
        # - the call type itself unambiguously establishes the injury type (e.g. BURN, FRACTURE, LACERATION), OR
        # - CAD explicitly documents that no injury occurred (injury.injury_occurred is False or injury_type == "No Injury").
        # Generic trauma (TRAUMA, FALL, MVA, ASSAULT) without injury_type does NOT establish Fracture
        # and must remain None.
        # Non-trauma calls must NOT assume "No Injury" without explicit injury confirmation.
        injury_type = None
        if injury and injury.injury_type:
            raw_inj = str(injury.injury_type).strip().lower()
            for cand in cls.VALID_INJURIES:
                if cand.lower() == raw_inj:
                    injury_type = cand
                    break
            if not injury_type and raw_inj in ("none", "no injury", "no_injury"):
                injury_type = "No Injury"

        if not injury_type:
            if injury and injury.injury_occurred is False:
                injury_type = "No Injury"
            elif norm_call_type in ("BURN", "BURNS"):
                injury_type = "Burn"
            elif norm_call_type == "FRACTURE":
                injury_type = "Fracture"
            elif norm_call_type in ("LACERATION", "STABBING"):
                injury_type = "Laceration"

        # 7. Category A: Actual Measurements (Strictly NO Defaulting / Fabrication)
        heart_rate = float(vitals.heart_rate) if (vitals and vitals.heart_rate is not None) else None
        spo2 = float(vitals.spo2) if (vitals and vitals.spo2 is not None) else None
        systolic_bp = float(vitals.systolic_bp) if (vitals and vitals.systolic_bp is not None) else None
        diastolic_bp = float(vitals.diastolic_bp) if (vitals and vitals.diastolic_bp is not None) else None
        resp_rate = float(vitals.respiratory_rate) if (vitals and vitals.respiratory_rate is not None) else None
        temperature = float(vitals.temperature) if (vitals and vitals.temperature is not None) else None
        gcs = int(vitals.gcs) if (vitals and vitals.gcs is not None) else None
        blood_glucose = float(vitals.blood_glucose) if (vitals and vitals.blood_glucose is not None) else None

        pain_score = None
        if vitals and vitals.pain_score is not None:
            pain_score = int(vitals.pain_score)
        elif symptoms and symptoms.pain_score is not None:
            pain_score = int(symptoms.pain_score)

        oxygen_req = None
        if vitals and vitals.oxygen_requirement:
            raw_ox = str(vitals.oxygen_requirement).strip().lower()
            for cand in cls.VALID_OXYGEN:
                if cand.lower() == raw_ox:
                    oxygen_req = cand
                    break

        # 8. Coordinates & Metadata
        lat = float(payload.location.latitude if payload.location else payload.latitude)
        lon = float(payload.location.longitude if payload.location else payload.longitude)

        return {
            # 6 Categorical Features
            "Sex": sex,
            "Condition": condition,
            "Oxygen_Requirement": oxygen_req,
            "Consciousness": consciousness,
            "Injury_Type": injury_type,
            "Arrival_Mode": arrival_mode,
            # 18 Numeric Clinical Features
            "Age": age,
            "Heart_Rate": heart_rate,
            "SpO2": spo2,
            "Systolic_BP": systolic_bp,
            "Diastolic_BP": diastolic_bp,
            "Respiratory_Rate": resp_rate,
            "Temperature": temperature,
            "GCS": gcs,
            "Pain_Score": pain_score,
            "Blood_Glucose": blood_glucose,
            "Respiratory_Distress": resp_distress,
            "Chest_Pain": chest_pain,
            "Bleeding": bleeding,
            "Seizure": seizure,
            "Diabetes": diabetes,
            "Hypertension": hypertension,
            "Heart_Disease": heart_disease,
            "Respiratory_Disease": resp_disease,
            # Geographic Coordinates
            "patient_lat": lat,
            "patient_lon": lon,
            # External Reference Preservation
            "external_incident_id": payload.external_incident_id,
            "source": payload.source,
            "call_type": payload.call_type,
            "chief_complaint": payload.chief_complaint,
        }

    @classmethod
    def normalize(cls, payload: CADIntakePayload) -> Dict[str, Any]:
        """
        Deterministically map external CAD payload to RAAH's internal incident dictionary.
        
        Strict Contract:
        If any of the 24 required clinical features is absent, this mapper REFUSES to invent
        synthetic physiology and raises CADNormalizationError, stating the exact missing fields.
        """
        extracted = cls.extract_available_fields(payload)

        # Verify whether all 24 required model features are populated
        missing_measurements = [
            feat for feat in cls.REQUIRED_ML_FEATURES
            if extracted.get(feat) is None
        ]

        if missing_measurements:
            raise CADNormalizationError(
                f"Missing required clinical measurement(s) for clinical ML model: {sorted(missing_measurements)}. "
                f"The protected 24-feature clinical ML model cannot accept missing values, and RAAH strictly prohibits "
                f"fabricating patient physiological measurements. CAD payload must provide verified clinical measurements "
                f"(e.g. from paramedic telemetry or clinical CAD feeds)."
            )

        return extracted
