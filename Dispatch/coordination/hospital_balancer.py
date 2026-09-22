"""
RAAH Predictive Hospital Balancer
=================================

Maintains an in-memory index of in-flight ambulance reservations (general beds
and ICU beds), computes forward-projected hospital capacities that never become
negative, and provides multi-objective allocation scoring to prevent emergency
department saturation and preserve critical ICU capacity.
"""

from dataclasses import dataclass, field
from math import radians, sin, cos, atan2, sqrt
from typing import Dict, List, Optional, Tuple, Set


def _distance_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Exact spherical Haversine distance in kilometers."""
    r1, o1 = radians(float(lat1)), radians(float(lon1))
    r2, o2 = radians(float(lat2)), radians(float(lon2))
    dlat, dlon = r2 - r1, o2 - o1
    a = sin(dlat / 2) ** 2 + cos(r1) * cos(r2) * sin(dlon / 2) ** 2
    a = min(1.0, max(0.0, a))
    return round(6371.0 * (2 * atan2(sqrt(a), sqrt(1 - a))), 3)


def _hospital_suitability(condition: str, hospital_type: str) -> int:
    """Clinical suitability level (3=high, 2=medium, 1=low)."""
    cond = str(condition)
    htype = str(hospital_type)
    if cond == "Cardiac":
        if htype == "Cardiac Center":
            return 3
        if htype in {"Specialty Hospital", "General"}:
            return 2
        return 1
    if cond == "Trauma":
        if htype == "Trauma Center":
            return 3
        if htype in {"Specialty Hospital", "General"}:
            return 2
        return 1
    if cond in {"Neurological", "Respiratory"}:
        if htype == "Specialty Hospital":
            return 3
        if htype == "General":
            return 2
        return 1
    if htype == "General":
        return 3
    if htype == "Specialty Hospital":
        return 2
    return 1


@dataclass
class InFlightReservation:
    """
    Represents an ambulance currently en route to a hospital with a patient.
    """
    ambulance_id: str
    hospital_id: str
    severity: str
    eta_minutes: float
    sim_time: int = 0
    requires_icu: bool = False


class HospitalBalancer:
    """
    Predictive load balancer tracking in-flight patients, projected capacities,
    and multi-objective facility allocation scores.
    """

    def __init__(self):
        # hospital_id -> list of active InFlightReservation
        self._reservations: Dict[str, List[InFlightReservation]] = {}
        # ambulance_id -> hospital_id index for fast lookup
        self._amb_index: Dict[str, str] = {}

    def register_dispatch(
        self,
        ambulance_id: str,
        hospital_id: str,
        severity: str,
        eta_minutes: float,
        sim_time: int = 0,
    ) -> InFlightReservation:
        """
        Record a newly dispatched ambulance as an in-flight load reservation.
        Atomically releases any previous reservation for this ambulance.
        """
        aid = str(ambulance_id)
        hid = str(hospital_id)
        requires_icu = str(severity).strip().lower() == "critical"

        # Release any existing reservation for this ambulance
        self.cancel_reservation(aid)

        res = InFlightReservation(
            ambulance_id=aid,
            hospital_id=hid,
            severity=str(severity),
            eta_minutes=float(eta_minutes),
            sim_time=int(sim_time),
            requires_icu=requires_icu,
        )

        if hid not in self._reservations:
            self._reservations[hid] = []

        self._reservations[hid].append(res)
        self._amb_index[aid] = hid
        return res

    def register_arrival(self, ambulance_id: str, hospital_id: str) -> bool:
        """
        Convert an in-flight reservation on vehicle arrival at destination.
        Returns True if an active reservation was cleared.
        """
        hid = str(hospital_id)
        aid = str(ambulance_id)
        self._amb_index.pop(aid, None)

        if hid in self._reservations:
            before = len(self._reservations[hid])
            self._reservations[hid] = [r for r in self._reservations[hid] if r.ambulance_id != aid]
            return len(self._reservations[hid]) < before
        return False

    def update_redirection(
        self,
        ambulance_id: str,
        old_hospital_id: str,
        new_hospital_id: str,
        severity: str,
        new_eta_minutes: float,
        sim_time: int = 0,
    ) -> InFlightReservation:
        """
        Transfer an in-flight reservation atomically from old to new hospital on reroute.
        """
        self.register_arrival(ambulance_id, old_hospital_id)
        return self.register_dispatch(
            ambulance_id=ambulance_id,
            hospital_id=new_hospital_id,
            severity=severity,
            eta_minutes=new_eta_minutes,
            sim_time=sim_time,
        )

    def cancel_reservation(self, ambulance_id: str, hospital_id: Optional[str] = None) -> bool:
        """
        Cancel in-flight reservation for an ambulance (e.g. on emergency interception or error).
        """
        aid = str(ambulance_id)
        target_hid = hospital_id or self._amb_index.pop(aid, None)
        cancelled = False

        if target_hid and target_hid in self._reservations:
            before = len(self._reservations[target_hid])
            self._reservations[target_hid] = [r for r in self._reservations[target_hid] if r.ambulance_id != aid]
            if len(self._reservations[target_hid]) < before:
                cancelled = True
        else:
            for hid, res_list in list(self._reservations.items()):
                before = len(res_list)
                self._reservations[hid] = [r for r in res_list if r.ambulance_id != aid]
                if len(self._reservations[hid]) < before:
                    cancelled = True

        self._amb_index.pop(aid, None)
        return cancelled

    def get_in_flight(self, hospital_id: str) -> List[InFlightReservation]:
        """Return list of active in-flight reservations for a hospital."""
        return list(self._reservations.get(str(hospital_id), []))

    def get_in_flight_counts(self, hospital_id: str) -> Tuple[int, int]:
        """
        Returns (incoming_total, incoming_critical) for a hospital.
        """
        if not self._reservations:
            return 0, 0
        res_list = self._reservations.get(str(hospital_id))
        if not res_list:
            return 0, 0
        total = len(res_list)
        critical = sum(1 for r in res_list if r.requires_icu)
        return total, critical

    def get_projected_capacity(self, hospital_id: str, hospital_state) -> dict:
        """
        Compute projected remaining beds and ICU beds accounting for in-flight arrivals.
        Calculations are strictly bounded: capacities never become negative.
        """
        hid = str(hospital_id)
        incoming_count, incoming_critical = self.get_in_flight_counts(hid)

        capacity = int(getattr(hospital_state, "capacity", 0))
        current_load = int(getattr(hospital_state, "current_load", 0))
        icu_capacity = int(getattr(hospital_state, "icu_capacity", 0))
        current_icu_load = int(getattr(hospital_state, "current_icu_load", 0))

        current_available_beds = max(0, capacity - current_load)
        current_available_icu = max(0, icu_capacity - current_icu_load)

        # In-flight deductions - strictly non-negative
        projected_available_beds = max(0, current_available_beds - incoming_count)
        projected_available_icu = max(0, current_available_icu - incoming_critical)

        utilization_ratio = round(float(current_load) / max(1, capacity), 4)
        projected_utilization_ratio = round(float(current_load + incoming_count) / max(1, capacity), 4)

        if projected_available_beds <= 0:
            status = "FULL"
        elif projected_available_icu <= 0:
            status = "CRITICAL_ICU"
        elif projected_utilization_ratio >= 0.85:
            status = "NEAR_CAPACITY"
        else:
            status = "AVAILABLE"

        return {
            "hospital_id": hid,
            "current_load": current_load,
            "capacity": capacity,
            "current_available_beds": current_available_beds,
            "projected_available_beds": projected_available_beds,
            "icu_capacity": icu_capacity,
            "current_icu_load": current_icu_load,
            "projected_available_icu": projected_available_icu,
            "incoming_count": incoming_count,
            "incoming_critical": incoming_critical,
            "utilization_ratio": utilization_ratio,
            "projected_utilization_ratio": projected_utilization_ratio,
            "status": status,
            "is_projected_full": projected_available_beds <= 0,
            "icu_projected_available": projected_available_icu > 0,
        }

    def get_all_projections(self, hospitals: dict) -> Dict[str, dict]:
        """
        Compute projected remaining capacity for all active hospitals.
        Returns dict mapping hospital_id -> projection dictionary.
        """
        return {
            str(hid): self.get_projected_capacity(str(hid), hosp)
            for hid, hosp in hospitals.items()
        }

    def score_hospital(
        self,
        hospital_state,
        distance_km: float,
        eta_minutes: float,
        severity: str = "Moderate",
        condition: str = "General",
        mci_surge_counts: Optional[Dict[str, int]] = None,
        mci_surge_factor: float = 0.35,
        proj: Optional[dict] = None,
    ) -> float:
        """
        Multi-objective allocation score for a candidate hospital.
        Lower score indicates superior global allocation.

        Balances:
          1. Distance and ETA proximity.
          2. Projected load ratio (damping surge).
          3. ICU preservation (preventing non-critical cases from consuming scarce ICU).
          4. In-flight surge damping.
          5. Clinical suitability match.
          6. Optional MCI surge damping across multi-casualty incidents.
        """
        hid = str(hospital_state.hospital_id)
        if proj is None:
            proj = self.get_projected_capacity(hid, hospital_state)

        # 1. Proximity penalty (normalized [0, 1])
        norm_dist = min(1.0, max(0.0, float(distance_km) / 25.0))
        norm_eta = min(1.0, max(0.0, float(eta_minutes) / 45.0))

        # 2. Projected capacity load ratio
        cap = max(1, int(hospital_state.capacity))
        proj_occupied = cap - proj["projected_available_beds"]
        load_ratio = min(1.5, max(0.0, proj_occupied / float(cap)))

        # 3. ICU preservation penalty
        is_crit = str(severity).strip().lower() == "critical"
        icu_penalty = 0.0

        if is_crit:
            # Critical patients heavily penalized if projected ICU is 0
            if proj["projected_available_icu"] <= 0:
                icu_penalty = 5.0
            else:
                icu_penalty = 1.0 / max(1.0, float(proj["projected_available_icu"]))
        else:
            # Non-critical patients penalized if hospital has dangerously low ICU
            if proj["projected_available_icu"] <= 1:
                icu_penalty = 0.80
            elif proj["projected_available_icu"] <= 2:
                icu_penalty = 0.40

        # 4. In-flight surge penalty (discourage dumping > 1 unit simultaneously)
        surge_penalty = 0.25 * max(0, proj["incoming_count"] - 1)

        # 5. Clinical suitability weighting
        suit_score = _hospital_suitability(condition, getattr(hospital_state, "hospital_type", "General"))
        suit_penalty = (3 - suit_score) * 0.15

        # 6. MCI surge damping (distribute casualties across available facilities)
        mci_penalty = 0.0
        if mci_surge_counts and hid in mci_surge_counts:
            mci_penalty = mci_surge_factor * mci_surge_counts[hid]

        # Composite score
        score = (
            (0.30 * norm_dist)
            + (0.25 * norm_eta)
            + (0.35 * load_ratio)
            + (0.15 * icu_penalty)
            + suit_penalty
            + surge_penalty
            + mci_penalty
        )

        return round(score, 4)

    def select_balanced_hospital(
        self,
        hospitals: dict,
        patient_lat: float,
        patient_lon: float,
        severity: str = "Moderate",
        condition: str = "General",
        routing_engine=None,
        candidate_ids: Optional[Set[str]] = None,
        mci_surge_counts: Optional[Dict[str, int]] = None,
        projections: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Select optimal hospital using multi-objective load balancing.
        Excludes hospitals that cannot safely accept the patient.
        Preserves ICU capacity for Critical patients when suitable alternatives exist.
        Prefers balanced hospitals over overloaded nearer hospitals when clinically acceptable.
        """
        is_crit = str(severity).strip().lower() == "critical"
        candidates = []
        target_ids = candidate_ids if candidate_ids is not None else hospitals.keys()

        for hid in target_ids:
            hid_str = str(hid)
            hosp = hospitals.get(hid) or hospitals.get(hid_str)
            if hosp is None:
                continue

            proj = projections.get(hid_str) if (projections is not None and hid_str in projections) else self.get_projected_capacity(hid_str, hosp)

            # Exclude hospitals with no projected available general beds
            if proj["projected_available_beds"] <= 0:
                continue

            # For Critical patients, exclude hospitals with no projected available ICU
            if is_crit and proj["projected_available_icu"] <= 0:
                continue

            h_lat = float(hosp.latitude)
            h_lon = float(hosp.longitude)
            dist_km = _distance_between(patient_lat, patient_lon, h_lat, h_lon)

            if routing_engine is not None:
                try:
                    router = getattr(routing_engine, "_router", routing_engine)
                    circuity = getattr(router, "circuity_factor", 1.25)
                    eta = round(max(0.1, (dist_km * circuity / 50.0) * 60.0), 2)
                except Exception:
                    eta = round(max(0.1, (dist_km / 50.0) * 60.0), 2)
            else:
                eta = round(max(0.1, (dist_km / 50.0) * 60.0), 2)

            score = self.score_hospital(
                hospital_state=hosp,
                distance_km=dist_km,
                eta_minutes=eta,
                severity=severity,
                condition=condition,
                mci_surge_counts=mci_surge_counts,
                proj=proj,
            )

            candidates.append((score, hid_str))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def clear(self):
        """Reset all in-flight reservations and index."""
        self._reservations.clear()
        self._amb_index.clear()
