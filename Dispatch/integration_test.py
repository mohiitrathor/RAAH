from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0,
    str(ROOT / "Dispatch"),
)


from dispatch_engine import dispatch_incident
from redirection_engine import check_live_redirection
from state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)


# ==============================================================
# TEST COUNTERS
# ==============================================================

passed = 0
failed = 0


def test_pass(name):

    global passed
    passed += 1

    print(f"[PASS] {name}")


def test_fail(name, error):

    global failed
    failed += 1

    print(f"[FAIL] {name}")
    print(f"       {error}")


# ==============================================================
# TEST 1
# INITIAL DISPATCH
# ==============================================================

def test_initial_dispatch():

    result = dispatch_incident(1)

    assert isinstance(
        result,
        dict,
    ), "Dispatch result is not a dictionary."

    assert result.get(
        "patient"
    ) is not None, (
        "Patient information missing."
    )

    assert result.get(
        "ambulance"
    ) is not None, (
        "Ambulance was not assigned."
    )

    assert result.get(
        "hospital"
    ) is not None, (
        "Hospital was not assigned."
    )

    return result


# ==============================================================
# HELPERS
# ==============================================================

def get_value(obj, *keys, default=None):

    if obj is None:
        return default

    if isinstance(obj, dict):

        for key in keys:

            if key in obj:
                return obj[key]

        return default

    for key in keys:

        if hasattr(obj, key):
            return getattr(obj, key)

    return default


# ==============================================================
# BUILD LIVE STATE
# ==============================================================

def build_test_state(dispatch_result):

    state = DispatchState()

    incident_id = int(
        dispatch_result["incident_id"]
    )

    patient = dispatch_result["patient"]
    ambulance_data = dispatch_result["ambulance"]
    hospital_data = dispatch_result["hospital"]

    severity = str(
        get_value(
            patient,
            "predicted_severity",
            "Predicted_Severity",
            "severity",
            "Severity",
            default="Moderate",
        )
    )

    priority_text = str(
        get_value(
            patient,
            "priority",
            "Priority",
            default="P3",
        )
    )

    priority = int(
        priority_text.replace("P", "")
    )

    condition = str(
        get_value(
            patient,
            "condition",
            "Condition",
            default="Unknown",
        )
    )

    ambulance_id = str(
        get_value(
            ambulance_data,
            "ambulance_id",
            "Ambulance_ID",
        )
    )

    hospital_id = str(
        get_value(
            hospital_data,
            "hospital_id",
            "Hospital_ID",
        )
    )

    ambulance_type = str(
        get_value(
            ambulance_data,
            "ambulance_type",
            "Ambulance_Type",
            default="Basic",
        )
    )

    hospital_type = str(
        get_value(
            hospital_data,
            "hospital_type",
            "Hospital_Type",
            default="General",
        )
    )

    eta = float(
        get_value(
            ambulance_data,
            "eta_minutes",
            "ETA",
            default=20.0,
        )
    )

    # ----------------------------------------------------------
    # INCIDENT
    # ----------------------------------------------------------

    incident = IncidentState(
        incident_id=incident_id,
        condition=condition,
        severity=severity,
        priority=priority,
        status="DISPATCHED",
        ambulance_id=ambulance_id,
        hospital_id=hospital_id,
    )

    # ----------------------------------------------------------
    # AMBULANCE
    # ----------------------------------------------------------

    ambulance = AmbulanceState(
        ambulance_id=ambulance_id,
        ambulance_type=ambulance_type,
        latitude=0.0,
        longitude=0.0,
        status="EN_ROUTE",
        incident_id=incident_id,
        hospital_id=hospital_id,
        eta_minutes=eta,
        base_eta_minutes=eta,
        traffic_level="NORMAL",
        road_condition="GOOD",
    )

    # ----------------------------------------------------------
    # CURRENT HOSPITAL
    # ----------------------------------------------------------

    current_hospital = HospitalState(
        hospital_id=hospital_id,
        hospital_type=hospital_type,
        latitude=0.0,
        longitude=0.0,
        capacity=200,
        current_load=100,
        icu_capacity=40,
        current_icu_load=10,
    )

    # ----------------------------------------------------------
    # ALTERNATIVE HOSPITAL
    # ----------------------------------------------------------

    alternative_hospital = HospitalState(
        hospital_id="TEST_HOSPITAL",
        hospital_type="General",
        latitude=1.0,
        longitude=1.0,
        capacity=300,
        current_load=50,
        icu_capacity=50,
        current_icu_load=5,
    )

    state.add_incident(
        incident
    )

    state.add_ambulance(
        ambulance
    )

    state.add_hospital(
        current_hospital
    )

    state.add_hospital(
        alternative_hospital
    )

    return state


# ==============================================================
# TEST 2
# LIVE STATE
# ==============================================================

def test_live_state(dispatch_result):

    state = build_test_state(
        dispatch_result
    )

    assert len(
        state.incidents
    ) == 1, (
        "Incident was not added."
    )

    assert len(
        state.ambulances
    ) == 1, (
        "Ambulance was not added."
    )

    assert len(
        state.hospitals
    ) == 2, (
        "Expected current + "
        "alternative hospital."
    )

    return state


# ==============================================================
# TEST 3
# HOSPITAL FAILURE
# ==============================================================

def test_hospital_failure(
    state,
    incident_id,
):

    incident = state.incidents[
        incident_id
    ]

    current_hospital = (
        state.hospitals[
            incident.hospital_id
        ]
    )

    # Make the current destination unavailable.
    current_hospital.current_load = (
        current_hospital.capacity
    )

    result = check_live_redirection(
        state,
        incident_id,
    )

    assert isinstance(
        result,
        dict,
    ), (
        "Redirection result is not "
        "a dictionary."
    )

    assert result.get(
        "redirect"
    ) is True, (
        "Redirection was not requested."
    )

    return result


# ==============================================================
# TEST 4
# ALTERNATIVE HOSPITAL
# ==============================================================

def test_alternative_hospital(
    state,
    result,
):

    alternative = result.get(
        "alternative_hospital"
    )

    assert alternative is not None, (
        "No alternative hospital "
        "was selected."
    )

    # The engine may return either:
    #
    # 1. A dictionary
    # 2. A HospitalState object
    #
    # Support both without changing
    # the engine's contract.

    hospital_id = get_value(
        alternative,
        "Hospital_ID",
        "hospital_id",
    )

    available_beds = get_value(
        alternative,
        "Available_Beds",
        "available_beds",
    )

    available_icu = get_value(
        alternative,
        "Available_ICU",
        "available_icu",
    )

    score = get_value(
        alternative,
        "Score",
        "score",
    )

    assert hospital_id is not None, (
        "Alternative hospital has "
        "no hospital ID."
    )

    assert str(
        hospital_id
    ) != str(
        state.incidents[
            next(iter(state.incidents))
        ].hospital_id
    ), (
        "Alternative hospital is "
        "the current hospital."
    )

    assert available_beds is not None, (
        "Alternative hospital has "
        "no bed availability."
    )

    assert float(
        available_beds
    ) > 0, (
        "Alternative hospital has "
        "no available beds."
    )

    if available_icu is not None:

        assert float(
            available_icu
        ) >= 0, (
            "Invalid ICU availability."
        )

    if score is not None:

        assert 0 <= float(score) <= 1, (
            "Hospital score is outside "
            "the expected 0-1 range."
        )

    return alternative


# ==============================================================
# TEST 5
# APPLY REDIRECTION
# ==============================================================

def test_redirect_state(
    state,
    incident_id,
    alternative,
):

    incident = state.incidents[
        incident_id
    ]

    ambulance = state.ambulances[
        incident.ambulance_id
    ]

    new_hospital_id = str(
        get_value(
            alternative,
            "Hospital_ID",
            "hospital_id",
        )
    )

    old_hospital_id = str(
        incident.hospital_id
    )

    assert (
        new_hospital_id
        != old_hospital_id
    ), (
        "New hospital is identical "
        "to current hospital."
    )

    # Apply the redirect exactly as
    # the simulator does.

    incident.hospital_id = (
        new_hospital_id
    )

    incident.status = (
        "REDIRECTED"
    )

    ambulance.hospital_id = (
        new_hospital_id
    )

    assert (
        incident.hospital_id
        == new_hospital_id
    ), (
        "Incident destination was "
        "not updated."
    )

    assert (
        ambulance.hospital_id
        == new_hospital_id
    ), (
        "Ambulance destination was "
        "not updated."
    )

    assert (
        incident.status
        == "REDIRECTED"
    ), (
        "Incident status was not "
        "set to REDIRECTED."
    )


test_pass.__test__ = False
test_fail.__test__ = False
test_initial_dispatch.__test__ = False
test_live_state.__test__ = False
test_hospital_failure.__test__ = False
test_alternative_hospital.__test__ = False
test_redirect_state.__test__ = False


def test_ambulance_dispatch_integration_pipeline():
    result = test_initial_dispatch()
    state = test_live_state(result)
    incident_id = int(result["incident_id"])
    redir = test_hospital_failure(state, incident_id)
    alt = test_alternative_hospital(state, redir)
    test_redirect_state(state, incident_id, alt)


# ==============================================================
# MAIN
# ==============================================================

def main():

    print()
    print("=" * 70)
    print(
        "AMBULANCE DISPATCH "
        "INTEGRATION TEST"
    )
    print("=" * 70)

    # ----------------------------------------------------------
    # TEST 1
    # ----------------------------------------------------------

    print()
    print(
        "TEST 1: Initial dispatch"
    )

    try:

        dispatch_result = (
            test_initial_dispatch()
        )

        test_pass(
            "ML → ambulance → hospital"
        )

    except Exception as error:

        test_fail(
            "ML → ambulance → hospital",
            error,
        )

        print()
        print(
            "Cannot continue because "
            "initial dispatch failed."
        )

        return

    # ----------------------------------------------------------
    # TEST 2
    # ----------------------------------------------------------

    print()
    print(
        "TEST 2: Build live dispatch state"
    )

    try:

        state = test_live_state(
            dispatch_result
        )

        test_pass(
            "Live state constructed"
        )

    except Exception as error:

        test_fail(
            "Live state constructed",
            error,
        )

        return

    incident_id = int(
        dispatch_result[
            "incident_id"
        ]
    )

    # ----------------------------------------------------------
    # TEST 3
    # ----------------------------------------------------------

    print()
    print(
        "TEST 3: Hospital failure detection"
    )

    try:

        redirection_result = (
            test_hospital_failure(
                state,
                incident_id,
            )
        )

        test_pass(
            "Hospital failure detected"
        )

    except Exception as error:

        test_fail(
            "Hospital failure detected",
            error,
        )

        return

    # ----------------------------------------------------------
    # TEST 4
    # ----------------------------------------------------------

    print()
    print(
        "TEST 4: Alternative hospital selection"
    )

    try:

        alternative = (
            test_alternative_hospital(
                state,
                redirection_result,
            )
        )

        hospital_id = get_value(
            alternative,
            "Hospital_ID",
            "hospital_id",
        )

        beds = get_value(
            alternative,
            "Available_Beds",
            "available_beds",
        )

        icu = get_value(
            alternative,
            "Available_ICU",
            "available_icu",
        )

        score = get_value(
            alternative,
            "Score",
            "score",
        )

        print(
            f"       Selected: {hospital_id}"
        )

        if beds is not None:
            print(
                f"       Beds:     {beds}"
            )

        if icu is not None:
            print(
                f"       ICU:      {icu}"
            )

        if score is not None:
            print(
                f"       Score:    "
                f"{float(score):.3f}"
            )

        test_pass(
            "Alternative hospital selected"
        )

    except Exception as error:

        test_fail(
            "Alternative hospital selected",
            error,
        )

        return

    # ----------------------------------------------------------
    # TEST 5
    # ----------------------------------------------------------

    print()
    print(
        "TEST 5: Apply live redirection"
    )

    try:

        test_redirect_state(
            state,
            incident_id,
            alternative,
        )

        test_pass(
            "Incident and ambulance updated"
        )

    except Exception as error:

        test_fail(
            "Incident and ambulance updated",
            error,
        )

    # ----------------------------------------------------------
    # SUMMARY
    # ----------------------------------------------------------

    print()
    print("=" * 70)
    print(
        "INTEGRATION TEST SUMMARY"
    )
    print("=" * 70)

    print(
        f"Tests passed:   {passed}"
    )

    print(
        f"Tests failed:   {failed}"
    )

    print(
        f"Tests executed: "
        f"{passed + failed}"
    )

    print("-" * 70)

    if failed == 0:

        print(
            "RESULT: ALL INTEGRATION "
            "TESTS PASSED"
        )

    else:

        print(
            "RESULT: INTEGRATION "
            "TEST FAILED"
        )

    print("=" * 70)


if __name__ == "__main__":

    main()