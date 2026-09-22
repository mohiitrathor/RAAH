from pathlib import Path

import threading
import joblib
import numpy as np
import pandas as pd


# ==============================================================
# PATHS
# ==============================================================

ROOT = Path(__file__).resolve().parents[1]

DATASET_DIR = ROOT / "Dataset"

PATIENTS_PATH = DATASET_DIR / "patient_incidents.csv"
AMBULANCES_PATH = DATASET_DIR / "ambulances.csv"
SCENARIOS_PATH = DATASET_DIR / "dispatch_scenarios.csv"
HOSPITALS_PATH = DATASET_DIR / "hospitals.csv"

MODEL_PATH = (
    ROOT
    / "Models"
    / "Final Model"
    / "logistic_regression_final.joblib"
)


# ==============================================================
# CONSTANTS
# ==============================================================

SEVERITY_PRIORITY = {
    "Critical": 1,
    "Emergency": 2,
    "Moderate": 3,
    "Low": 4,
    "Non-Urgent": 5,
}

AMBULANCE_CAPABILITY = {
    "Basic Life Support": 1,
    "Advanced Life Support": 2,
    "Critical Care": 3,
}


# ==============================================================
# DATA LOADING & IN-MEMORY CACHE
# ==============================================================

_DATA_CACHE = None
_CACHE_LOCK = threading.Lock()


def load_data(
    force_reload=False,
):
    """
    Load datasets and the trained ML model.

    Results are cached in memory on first load.
    Subsequent calls return the cached in-memory resources
    unless force_reload is True.
    """

    global _DATA_CACHE

    if _DATA_CACHE is not None and not force_reload:
        return _DATA_CACHE

    with _CACHE_LOCK:

        if _DATA_CACHE is not None and not force_reload:
            return _DATA_CACHE

        patients = pd.read_csv(
            PATIENTS_PATH
        )

        ambulances = pd.read_csv(
            AMBULANCES_PATH
        )

        scenarios = pd.read_csv(
            SCENARIOS_PATH
        )

        hospitals = pd.read_csv(
            HOSPITALS_PATH
        )

        model = joblib.load(
            MODEL_PATH
        )

        _DATA_CACHE = (
            patients,
            ambulances,
            scenarios,
            hospitals,
            model,
        )

        return _DATA_CACHE


def clear_data_cache():
    """
    Clear the in-memory dataset and model cache.
    """

    global _DATA_CACHE

    with _CACHE_LOCK:
        _DATA_CACHE = None


# ==============================================================
# SEVERITY
# ==============================================================

def required_ambulance_level(
    severity,
):
    """
    Determine the minimum ambulance
    capability required for a predicted
    severity.
    """

    return {
        "Critical": 2,
        "Emergency": 2,
        "Moderate": 1,
        "Low": 1,
        "Non-Urgent": 1,
    }.get(
        str(severity),
        1,
    )


def predict_severity(
    model,
    incident,
):
    """
    Predict incident severity using the
    trained ML model.

    Returns:
        predicted_severity
        confidence
        probabilities
    """

    confidence = None
    probabilities = None

    # Fast-path for single incident inference avoiding scikit-learn ColumnTransformer parallel overhead
    if hasattr(model, "named_steps") and "preprocessor" in model.named_steps and "classifier" in model.named_steps:
        try:
            if not hasattr(model, "_fast_infer_meta"):
                prep = model.named_steps["preprocessor"]
                cat_encoder = prep.named_transformers_["categorical"]
                num_scaler = prep.named_transformers_["numeric"]
                cat_cols = ['Sex', 'Condition', 'Oxygen_Requirement', 'Consciousness', 'Injury_Type', 'Arrival_Mode']
                num_cols = ['Age', 'Heart_Rate', 'SpO2', 'Systolic_BP', 'Diastolic_BP', 'Respiratory_Rate', 'Temperature', 'GCS', 'Pain_Score', 'Blood_Glucose', 'Respiratory_Distress', 'Chest_Pain', 'Bleeding', 'Seizure', 'Diabetes', 'Hypertension', 'Heart_Disease', 'Respiratory_Disease']
                cat_cats = cat_encoder.categories_
                cat_maps = [{val: idx for idx, val in enumerate(cats)} for cats in cat_cats]
                cat_offsets = [0]
                for cats in cat_cats[:-1]:
                    cat_offsets.append(cat_offsets[-1] + len(cats))
                total_cat_dim = cat_offsets[-1] + len(cat_cats[-1])
                model._fast_infer_meta = {
                    "clf": model.named_steps["classifier"],
                    "cat_cols": cat_cols,
                    "num_cols": num_cols,
                    "cat_maps": cat_maps,
                    "cat_offsets": cat_offsets,
                    "total_cat_dim": total_cat_dim,
                    "means": num_scaler.mean_,
                    "scales": num_scaler.scale_,
                    "classes": model.named_steps["classifier"].classes_,
                }

            meta = model._fast_infer_meta
            if hasattr(incident, "iloc"):
                if len(incident) == 1:
                    row = incident.iloc[0]
                else:
                    row = None
            elif isinstance(incident, dict):
                row = incident
            else:
                row = incident

            if row is not None:
                vec = np.zeros(44, dtype=np.float64)
                for col, mapping, offset in zip(meta["cat_cols"], meta["cat_maps"], meta["cat_offsets"]):
                    val = row[col] if hasattr(row, "__getitem__") else getattr(row, col, None)
                    if val in mapping:
                        vec[offset + mapping[val]] = 1.0

                num_vals = np.array([float(row[c]) if hasattr(row, "__getitem__") else float(getattr(row, c, 0.0)) for c in meta["num_cols"]], dtype=np.float64)
                vec[meta["total_cat_dim"]:] = (num_vals - meta["means"]) / meta["scales"]
                probabilities = meta["clf"].predict_proba(vec.reshape(1, -1))[0]
                confidence = float(np.max(probabilities))
                prediction = meta["classes"][np.argmax(probabilities)]
                return (
                    str(prediction),
                    confidence,
                    probabilities,
                )
        except Exception:
            pass

    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(incident)[0]
        confidence = float(np.max(probabilities))
        if hasattr(model, "classes_"):
            prediction = model.classes_[np.argmax(probabilities)]
        else:
            prediction = model.predict(incident)[0]
    else:
        prediction = model.predict(incident)[0]

    return (
        str(prediction),
        confidence,
        probabilities,
    )


# ==============================================================
# AMBULANCE SELECTION
# ==============================================================

def select_ambulance(
    predicted_severity,
    incident_id,
    ambulances,
    scenarios,
    available_ambulance_ids=None,
):
    """
    Select the best available ambulance
    for an incident.

    Capability matching is preferred.
    If no compatible ambulance exists,
    the fastest available ambulance is
    returned as a fallback.
    """

    candidates = scenarios[
        scenarios["Incident_ID"]
        == incident_id
    ].copy()

    if candidates.empty:

        raise ValueError(
            f"No dispatch scenarios found "
            f"for Incident_ID={incident_id}"
        )

    # Keep only the fleet columns needed
    # for availability and capability.
    fleet = ambulances[
        [
            "Ambulance_ID",
            "Ambulance_Type",
            "Availability",
        ]
    ].copy()

    # Avoid duplicate Ambulance_Type
    # columns if the scenario dataset
    # already contains one.
    candidates = candidates.drop(
        columns=[
            "Ambulance_Type",
        ],
        errors="ignore",
    )

    candidates = candidates.merge(
        fleet,
        on="Ambulance_ID",
        how="inner",
    )

    # Only currently available ambulances.
    candidates = candidates[
        candidates[
            "Availability"
        ]
        .astype(str)
        .str.upper()
        == "AVAILABLE"
    ].copy()

    if available_ambulance_ids is not None:
        candidates = candidates[
            candidates[
                "Ambulance_ID"
            ].isin(available_ambulance_ids)
        ].copy()

    if candidates.empty:

        return (
            None,
            pd.DataFrame(),
        )

    required_level = (
        required_ambulance_level(
            predicted_severity
        )
    )

    candidates[
        "Capability_Level"
    ] = (
        candidates[
            "Ambulance_Type"
        ]
        .map(
            AMBULANCE_CAPABILITY
        )
        .fillna(0)
    )

    candidates[
        "Capability_Match"
    ] = (
        candidates[
            "Capability_Level"
        ]
        >= required_level
    )

    compatible = candidates[
        candidates[
            "Capability_Match"
        ]
    ].copy()

    # ----------------------------------------------------------
    # CAPABILITY FIRST
    # ----------------------------------------------------------

    fallback = compatible.empty

    if not fallback:

        candidates = compatible

    # ----------------------------------------------------------
    # ETA FIRST, DISTANCE SECOND
    # ----------------------------------------------------------

    candidates = candidates.sort_values(
        by=[
            "Predicted_ETA_Minutes",
            "Distance_KM",
        ],
        ascending=[
            True,
            True,
        ],
    ).reset_index(
        drop=True
    )

    candidates[
        "Fallback"
    ] = fallback

    return (
        candidates.iloc[0].copy(),
        candidates,
    )


# ==============================================================
# HOSPITAL SUITABILITY
# ==============================================================

def hospital_suitability(
    condition,
    hospital_type,
):
    """
    Score hospital suitability based on
    patient condition and hospital type.

    3 = highly suitable
    2 = suitable
    1 = general/low suitability
    """

    condition = str(
        condition
    )

    hospital_type = str(
        hospital_type
    )

    if condition == "Cardiac":

        if hospital_type == "Cardiac Center":
            return 3

        if hospital_type in {
            "Specialty Hospital",
            "General",
        }:
            return 2

        return 1

    if condition == "Trauma":

        if hospital_type == "Trauma Center":
            return 3

        if hospital_type in {
            "Specialty Hospital",
            "General",
        }:
            return 2

        return 1

    if condition in {
        "Neurological",
        "Respiratory",
    }:

        if hospital_type == "Specialty Hospital":
            return 3

        if hospital_type == "General":
            return 2

        return 1

    if hospital_type == "General":
        return 3

    if hospital_type == "Specialty Hospital":
        return 2

    return 1


# ==============================================================
# HOSPITAL SELECTION
# ==============================================================

def select_hospital(
    predicted_severity,
    condition,
    patient_lat,
    patient_lon,
    hospitals,
    suitable_hospital_ids=None,
    live_icu_hospital_ids=None,
):
    """
    Select the most suitable hospital.

    Priority:
        1. Hospital suitability
        2. Distance
        3. ICU availability
        4. Bed availability

    Critical incidents require ICU availability.
    """

    hosp_ids = hospitals["Hospital_ID"].values
    avail_beds = np.maximum(0, hospitals["Hospital_Capacity"].values - hospitals["Current_Load"].values)
    avail_icu = np.maximum(0, hospitals["ICU_Capacity"].values - hospitals["Current_ICU_Load"].values)
    lat_diff = float(patient_lat) - hospitals["Latitude"].values
    lon_diff = float(patient_lon) - hospitals["Longitude"].values
    dist_km = np.sqrt(lat_diff ** 2 + lon_diff ** 2) * 111.0
    suit = np.array([hospital_suitability(condition, ht) for ht in hospitals["Hospital_Type"].values])

    mask = avail_beds > 0
    if suitable_hospital_ids is not None:
        suit_set = set(suitable_hospital_ids)
        mask &= np.fromiter((h in suit_set for h in hosp_ids), dtype=bool, count=len(hosp_ids))

    if predicted_severity == "Critical":
        mask &= (avail_icu > 0)
        if live_icu_hospital_ids is not None:
            icu_set = set(live_icu_hospital_ids)
            mask &= np.fromiter((h in icu_set for h in hosp_ids), dtype=bool, count=len(hosp_ids))

    if not np.any(mask):
        return (
            None,
            pd.DataFrame(),
        )

    sub_indices = np.where(mask)[0]
    # lexsort: primary key last, so (avail_beds, avail_icu, dist_km, suit)
    order = np.lexsort((
        -avail_beds[sub_indices],
        -avail_icu[sub_indices],
        dist_km[sub_indices],
        -suit[sub_indices]
    ))
    best_idx = sub_indices[order[0]]
    best_row = hospitals.iloc[best_idx].copy()
    best_row["Available_Beds"] = avail_beds[best_idx]
    best_row["Available_ICU"] = avail_icu[best_idx]
    best_row["Distance_KM"] = dist_km[best_idx]
    best_row["Suitability"] = suit[best_idx]

    return (
        best_row,
        hospitals.iloc[sub_indices[order]],
    )


# ==============================================================
# MAIN DISPATCH FUNCTION
# ==============================================================

def dispatch_incident(
    incident_id,
    available_ambulance_ids=None,
    suitable_hospital_ids=None,
    live_icu_hospital_ids=None,
):
    """
    Run the complete initial dispatch pipeline.

    ML
      ↓
    Severity prediction
      ↓
    Ambulance selection
      ↓
    Hospital selection
      ↓
    Dispatch result
    """

    (
        patients,
        ambulances,
        scenarios,
        hospitals,
        model,
    ) = load_data()

    # ----------------------------------------------------------
    # FIND INCIDENT
    # ----------------------------------------------------------

    incident_rows = patients[
        patients[
            "Incident_ID"
        ]
        == incident_id
    ].copy()

    if incident_rows.empty:

        raise ValueError(
            f"Incident_ID={incident_id} "
            f"was not found."
        )

    incident = (
        incident_rows.iloc[0]
    )

    # ----------------------------------------------------------
    # MODEL INPUT
    # ----------------------------------------------------------

    model_columns = getattr(
        model,
        "feature_names_in_",
        None,
    )

    if model_columns is None:

        raise ValueError(
            "Saved model does not contain "
            "feature_names_in_."
        )

    missing_features = [
        column
        for column in model_columns
        if column not in incident_rows.columns
    ]

    if missing_features:

        raise ValueError(
            "Missing model features: "
            + ", ".join(
                missing_features
            )
        )

    model_input = incident_rows[
        list(model_columns)
    ]

    # ----------------------------------------------------------
    # ML PREDICTION
    # ----------------------------------------------------------

    (
        predicted_severity,
        confidence,
        probabilities,
    ) = predict_severity(
        model,
        model_input,
    )

    priority_number = (
        SEVERITY_PRIORITY.get(
            predicted_severity,
            5,
        )
    )

    # ----------------------------------------------------------
    # AMBULANCE
    # ----------------------------------------------------------

    (
        selected_ambulance,
        ambulance_candidates,
    ) = select_ambulance(
        predicted_severity,
        incident_id,
        ambulances,
        scenarios,
        available_ambulance_ids=available_ambulance_ids,
    )

    # ----------------------------------------------------------
    # NO AMBULANCE
    # ----------------------------------------------------------

    if selected_ambulance is None:

        return {

            "status":
                "NO_AMBULANCE_AVAILABLE",

            "incident_id":
                int(incident_id),

            "predicted_severity":
                predicted_severity,

            "confidence":
                confidence,

            "patient": {

                "condition":
                    str(
                        incident[
                            "Condition"
                        ]
                    ),

                "predicted_severity":
                    predicted_severity,

                "priority":
                    f"P{priority_number}",

                "confidence":
                    confidence,
            },

            "ambulance":
                None,

            "hospital":
                None,
        }

    # ----------------------------------------------------------
    # HOSPITAL
    # ----------------------------------------------------------

    (
        selected_hospital,
        hospital_candidates,
    ) = select_hospital(

        predicted_severity,

        str(
            incident[
                "Condition"
            ]
        ),

        float(
            incident[
                "Patient_Lat"
            ]
        ),

        float(
            incident[
                "Patient_Lon"
            ]
        ),

        hospitals,

        suitable_hospital_ids=suitable_hospital_ids,

        live_icu_hospital_ids=live_icu_hospital_ids,
    )

    # ----------------------------------------------------------
    # BUILD RESULT
    # ----------------------------------------------------------

    result = {

        "status":
            "DISPATCH_RECOMMENDED",

        "incident_id":
            int(incident_id),

        "patient": {

            "condition":
                str(
                    incident[
                        "Condition"
                    ]
                ),

            "predicted_severity":
                predicted_severity,

            "priority":
                f"P{priority_number}",

            "confidence":
                confidence,
        },

        "ambulance": {

            "ambulance_id":
                str(
                    selected_ambulance[
                        "Ambulance_ID"
                    ]
                ),

            "ambulance_type":
                str(
                    selected_ambulance[
                        "Ambulance_Type"
                    ]
                ),

            "eta_minutes":
                float(
                    selected_ambulance[
                        "Predicted_ETA_Minutes"
                    ]
                ),

            "distance_km":
                float(
                    selected_ambulance[
                        "Distance_KM"
                    ]
                ),

            "traffic":
                str(
                    selected_ambulance.get(
                        "Traffic_Level",
                        "NORMAL",
                    )
                ),

            "road_condition":
                str(
                    selected_ambulance.get(
                        "Road_Condition",
                        "GOOD",
                    )
                ),

            "capability_match":
                bool(
                    selected_ambulance[
                        "Capability_Match"
                    ]
                ),

            "fallback":
                bool(
                    selected_ambulance[
                        "Fallback"
                    ]
                ),
        },

        "hospital":
            None,
    }

    # ----------------------------------------------------------
    # HOSPITAL RESULT
    # ----------------------------------------------------------

    if selected_hospital is not None:

        result[
            "hospital"
        ] = {

            "hospital_id":
                str(
                    selected_hospital[
                        "Hospital_ID"
                    ]
                ),

            "hospital_type":
                str(
                    selected_hospital[
                        "Hospital_Type"
                    ]
                ),

            "distance_km":
                float(
                    selected_hospital[
                        "Distance_KM"
                    ]
                ),

            "available_beds":
                int(
                    selected_hospital[
                        "Available_Beds"
                    ]
                ),

            "available_icu":
                int(
                    selected_hospital[
                        "Available_ICU"
                    ]
                ),

            "suitability":
                int(
                    selected_hospital[
                        "Suitability"
                    ]
                ),
        }

    else:

        result["status"] = (
            "NO_SUITABLE_HOSPITAL"
        )

    return result


# ==============================================================
# PRINT DISPATCH
# ==============================================================

def print_dispatch(
    result,
):

    print()
    print("=" * 70)
    print("DISPATCH DECISION")
    print("=" * 70)

    print(
        f"Incident:       "
        f"#{result['incident_id']}"
    )

    patient = result.get(
        "patient",
        {},
    )

    print(
        f"Condition:      "
        f"{patient.get('condition', '-')}"
    )

    print(
        f"Severity:       "
        f"{patient.get('predicted_severity', '-')}"
    )

    print(
        f"Priority:       "
        f"{patient.get('priority', '-')}"
    )

    confidence = (
        patient.get(
            "confidence"
        )
    )

    if confidence is not None:

        print(
            f"Confidence:     "
            f"{confidence:.2%}"
        )

    ambulance = result.get(
        "ambulance"
    )

    if ambulance:

        print()
        print("AMBULANCE")
        print("-" * 70)

        print(
            f"ID:             "
            f"{ambulance['ambulance_id']}"
        )

        print(
            f"Type:           "
            f"{ambulance['ambulance_type']}"
        )

        print(
            f"ETA:            "
            f"{ambulance['eta_minutes']:.1f} min"
        )

        print(
            f"Distance:       "
            f"{ambulance['distance_km']:.2f} km"
        )

        print(
            f"Capability:     "
            f"{'MATCH' if ambulance['capability_match'] else 'FALLBACK'}"
        )

        print(
            f"Traffic:        "
            f"{ambulance['traffic']}"
        )

        print(
            f"Road:           "
            f"{ambulance['road_condition']}"
        )

    else:

        print()
        print(
            "AMBULANCE: NONE AVAILABLE"
        )

    hospital = result.get(
        "hospital"
    )

    if hospital:

        print()
        print("HOSPITAL")
        print("-" * 70)

        print(
            f"ID:             "
            f"{hospital['hospital_id']}"
        )

        print(
            f"Type:           "
            f"{hospital['hospital_type']}"
        )

        print(
            f"Distance:       "
            f"{hospital['distance_km']:.2f} km"
        )

        print(
            f"Available beds: "
            f"{hospital['available_beds']}"
        )

        print(
            f"Available ICU:  "
            f"{hospital['available_icu']}"
        )

        print(
            f"Suitability:    "
            f"{hospital['suitability']}/3"
        )

    else:

        print()
        print(
            "HOSPITAL: NONE SUITABLE"
        )

    print()
    print(
        f"Status:         "
        f"{result['status']}"
    )

    print("=" * 70)


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    result = dispatch_incident(
        1
    )

    print_dispatch(
        result
    )
