from typing import Optional


# ==============================================================
# HOSPITAL SUITABILITY
# ==============================================================

def hospital_is_suitable(
    hospital,
    severity,
    exclude_hospital_id=None,
):

    if (
        exclude_hospital_id is not None
        and str(hospital.hospital_id)
        == str(exclude_hospital_id)
    ):
        return False

    if hospital.available_beds <= 0:
        return False

    if (
        severity == "Critical"
        and hospital.available_icu <= 0
    ):
        return False

    return True


# ==============================================================
# HOSPITAL SCORE
# ==============================================================

def hospital_score(
    hospital,
    severity,
):

    capacity = max(
        hospital.capacity,
        1,
    )

    bed_ratio = (
        hospital.available_beds
        / capacity
    )

    score = bed_ratio * 0.40

    if severity == "Critical":

        score += (
            min(
                hospital.available_icu / 10,
                1,
            )
            * 0.40
        )

    else:

        score += (
            min(
                hospital.available_icu / 10,
                1,
            )
            * 0.20
        )

    hospital_type = (
        hospital.hospital_type
        .lower()
    )

    if severity == "Critical":

        if any(
            word in hospital_type
            for word in (
                "trauma",
                "cardiac",
                "specialty",
            )
        ):

            score += 0.20

    else:

        if "general" in hospital_type:

            score += 0.20

    return round(
        min(score, 1.0),
        3,
    )


# ==============================================================
# ALTERNATIVE HOSPITAL SEARCH
# ==============================================================

def find_best_alternative(
    state,
    incident,
    exclude_hospital_ids=None,
):

    ambulance = None

    if incident.ambulance_id:

        ambulance = (
            state.ambulances.get(
                incident.ambulance_id
            )
        )

    candidates = []

    excluded = set()
    if incident.hospital_id:
        excluded.add(str(incident.hospital_id))
    if exclude_hospital_ids:
        if isinstance(exclude_hospital_ids, (set, list, tuple)):
            excluded.update(str(x) for x in exclude_hospital_ids)
        else:
            excluded.add(str(exclude_hospital_ids))

    for hospital in (
        state.hospitals.values()
    ):

        if str(hospital.hospital_id) in excluded:
            continue

        if not hospital_is_suitable(
            hospital,
            incident.severity,
            None,
        ):
            continue

        score = hospital_score(
            hospital,
            incident.severity,
        )

        eta = None

        if ambulance:

            eta = (
                ambulance
                .estimate_eta_to_hospital(
                    hospital
                )
            )

        candidates.append({
            "hospital": hospital,
            "score": score,
            "eta": eta,
        })

    if not candidates:

        return None

    # If we know ETA, ETA is the primary
    # decision factor. Hospital quality is
    # the secondary factor.
    if ambulance:

        candidates.sort(
            key=lambda item: (
                item["eta"],
                -item["score"],
            )
        )

    else:

        candidates.sort(
            key=lambda item: (
                -item["score"],
            )
        )

    return candidates[0]


# ==============================================================
# REDIRECTION EVALUATION
# ==============================================================

def evaluate_redirection(
    state,
    incident_id,
    trigger_reason=None,
):

    incident = state.incidents.get(
        incident_id
    )

    if incident is None:

        raise ValueError(
            f"Incident {incident_id} "
            f"not found."
        )

    if incident.status == "ARRIVED":

        return {
            "redirect": False,
            "reason": "Incident already arrived.",
            "alternative_hospital": None,
        }

    current_hospital = (
        state.hospitals.get(
            incident.hospital_id
        )
    )

    if current_hospital is None:

        return {
            "redirect": False,
            "reason": (
                "Current hospital is missing."
            ),
            "alternative_hospital": None,
        }

    ambulance = None

    if incident.ambulance_id:

        ambulance = (
            state.ambulances.get(
                incident.ambulance_id
            )
        )

    # ----------------------------------------------------------
    # CHECK CURRENT HOSPITAL
    # ----------------------------------------------------------

    current_suitable = (
        hospital_is_suitable(
            current_hospital,
            incident.severity,
        )
    )

    current_eta = None

    if ambulance:

        if ambulance.eta_minutes is not None:

            current_eta = (
                float(
                    ambulance.eta_minutes
                )
            )

    # ----------------------------------------------------------
    # IF CURRENT HOSPITAL IS INVALID,
    # REDIRECTION IS REQUIRED.
    # ----------------------------------------------------------

    if not current_suitable:

        if current_hospital.is_full:

            reason = (
                f"Hospital "
                f"{current_hospital.hospital_id} "
                f"is full."
            )

        elif (
            incident.severity == "Critical"
            and not current_hospital.icu_available
        ):

            reason = (
                f"Hospital "
                f"{current_hospital.hospital_id} "
                f"has no ICU availability."
            )

        else:

            reason = (
                "Current hospital is no longer "
                "suitable."
            )

        alternative = find_best_alternative(
            state,
            incident,
        )

        if alternative is None:

            return {
                "redirect": False,
                "reason": (
                    f"{reason} "
                    "No suitable alternative found."
                ),
                "alternative_hospital": None,
            }

        hospital = alternative[
            "hospital"
        ]

        new_eta = alternative["eta"]

        return {
            "redirect": True,
            "reason": reason,
            "trigger": "HOSPITAL_UNSUITABLE",
            "alternative_hospital": {
                "hospital_id": (
                    hospital.hospital_id
                ),
                "hospital_type": (
                    hospital.hospital_type
                ),
                "available_beds": (
                    hospital.available_beds
                ),
                "available_icu": (
                    hospital.available_icu
                ),
                "score": alternative["score"],
                "eta": new_eta,
            },
            "eta_before": current_eta,
            "eta_after": new_eta,
        }

    # ----------------------------------------------------------
    # CURRENT HOSPITAL IS STILL VALID.
    #
    # Only redirect for ETA improvement when
    # explicitly triggered by ETA deterioration.
    # ----------------------------------------------------------

    if trigger_reason in ("OPERATOR_OVERRIDE", "MANUAL"):
        alternative = find_best_alternative(
            state,
            incident,
        )
        if alternative is None:
            return {
                "redirect": False,
                "reason": "No suitable alternative hospital available.",
                "alternative_hospital": None,
            }

        hospital = alternative["hospital"]
        new_eta = alternative["eta"]
        improvement = round((current_eta - new_eta), 2) if (current_eta is not None and new_eta is not None) else 0.0

        return {
            "redirect": True,
            "reason": "Operator manual redirection override.",
            "trigger": "OPERATOR_OVERRIDE",
            "alternative_hospital": {
                "hospital_id": hospital.hospital_id,
                "hospital_type": hospital.hospital_type,
                "available_beds": hospital.available_beds,
                "available_icu": hospital.available_icu,
                "score": alternative["score"],
                "eta": new_eta,
            },
            "eta_before": current_eta,
            "eta_after": new_eta,
            "eta_saved": improvement,
            "eta_improvement_percent": round(((improvement / max(current_eta, 1)) * 100), 2) if (current_eta and current_eta > 0) else 0.0,
        }

    if trigger_reason != "ETA_DETERIORATION":

        # Identify best alternative facility for informational situational awareness
        alt_info = find_best_alternative(state, incident)
        alt_dict = None
        if alt_info:
            h = alt_info["hospital"]
            alt_dict = {
                "hospital_id": h.hospital_id,
                "hospital_type": h.hospital_type,
                "available_beds": h.available_beds,
                "available_icu": h.available_icu,
                "score": alt_info["score"],
                "eta": alt_info["eta"],
            }

        return {
            "redirect": False,
            "reason": (
                "Current hospital remains suitable."
            ),
            "alternative_hospital": alt_dict,
        }

    alternative = find_best_alternative(
        state,
        incident,
    )

    if alternative is None:

        return {
            "redirect": False,
            "reason": (
                "No suitable alternative found."
            ),
            "alternative_hospital": None,
        }

    hospital = alternative[
        "hospital"
    ]

    new_eta = alternative["eta"]

    if (
        current_eta is None
        or new_eta is None
    ):

        return {
            "redirect": False,
            "reason": (
                "Unable to compare ETAs."
            ),
            "alternative_hospital": None,
        }

    improvement = (
        current_eta - new_eta
    )

    improvement_percent = (
        improvement
        / max(current_eta, 1)
    ) * 100

    # Require a meaningful improvement.
    if (
        improvement < 5
        and improvement_percent < 20
    ):

        return {
            "redirect": False,
            "reason": (
                "Alternative hospital does not "
                "provide a meaningful ETA improvement."
            ),
            "alternative_hospital": None,
        }

    return {
        "redirect": True,
        "reason": (
            "ETA deterioration caused by "
            "route conditions."
        ),
        "trigger": "ETA_DETERIORATION",
        "alternative_hospital": {
            "hospital_id": (
                hospital.hospital_id
            ),
            "hospital_type": (
                hospital.hospital_type
            ),
            "available_beds": (
                hospital.available_beds
            ),
            "available_icu": (
                hospital.available_icu
            ),
            "score": alternative["score"],
            "eta": new_eta,
        },
        "eta_before": current_eta,
        "eta_after": new_eta,
        "eta_saved": improvement,
        "eta_improvement_percent": (
            improvement_percent
        ),
    }


# ==============================================================
# COMPATIBILITY WRAPPER
# ==============================================================

def check_live_redirection(
    state,
    incident_id,
):

    return evaluate_redirection(
        state,
        incident_id,
    )


def check_enroute_redirection(
    state,
    incident_id,
):

    return evaluate_redirection(
        state,
        incident_id,
        trigger_reason="ETA_DETERIORATION",
    )