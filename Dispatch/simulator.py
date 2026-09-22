from pathlib import Path
from math import radians, sin, cos, atan2, sqrt
import sys
from typing import Optional, List, Dict, Any, Tuple, Set

import pandas as pd


_AMB_SPEEDS = {"BLS": 45.0, "ALS": 50.0, "ICU": 50.0, "TRAUMA": 55.0}
_TRAF_MULTS = {"LIGHT": 1.0, "NORMAL": 1.0, "MODERATE": 1.15, "HEAVY": 1.3, "SEVERE": 1.5}
_ROAD_MULTS = {"GOOD": 1.0, "AVERAGE": 1.1, "POOR": 1.25}


def _distance_between(lat1, lon1, lat2, lon2):
    r1 = radians(float(lat1))
    o1 = radians(float(lon1))
    r2 = radians(float(lat2))
    o2 = radians(float(lon2))
    dlat = r2 - r1
    dlon = o2 - o1
    a = sin(dlat / 2) ** 2 + cos(r1) * cos(r2) * sin(dlon / 2) ** 2
    a = min(1.0, max(0.0, a))
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return round(6371.0 * c, 3)


def _estimate_eta_to_patient(amb, distance_km):
    speed = _AMB_SPEEDS.get(getattr(amb, "ambulance_type", None), 50.0)
    t_mult = _TRAF_MULTS.get(getattr(amb, "traffic_level", None), 1.0)
    r_mult = _ROAD_MULTS.get(getattr(amb, "road_condition", None), 1.0)
    return round(max(0.1, (distance_km / speed) * 60.0 * t_mult * r_mult), 2)



# ==============================================================
# PATHS
# ==============================================================

ROOT = Path(__file__).resolve().parents[1]

DISPATCH_DIR = ROOT / "Dispatch"
DATASET_DIR = ROOT / "Dataset"

if str(DISPATCH_DIR) not in sys.path:
    sys.path.insert(0, str(DISPATCH_DIR))


# ==============================================================
# DISPATCH MODULES
# ==============================================================

from dispatch_engine import (
    dispatch_incident,
    load_data,
    predict_severity,
    select_hospital,
    required_ambulance_level,
    SEVERITY_PRIORITY,
    AMBULANCE_CAPABILITY,
    hospital_suitability,
)
from redirection_engine import check_live_redirection, find_best_alternative
from events import EventEngine

from state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)

from decision_logger import DecisionLogger
from simulation_output import SimulationOutput
from routing import routing_engine
from coordination import FleetCoordinator


# ==============================================================
# SIMULATOR
# ==============================================================

class Simulator:

    ETA_CHANGE_THRESHOLD_PERCENT = 25.0
    ETA_CHANGE_THRESHOLD_MINUTES = 10.0

    MIN_REDIRECTION_IMPROVEMENT_MINUTES = 5.0
    MIN_REDIRECTION_IMPROVEMENT_PERCENT = 20.0

    def __init__(self):

        self.state = DispatchState()
        self.events = EventEngine()

        self.logger = DecisionLogger()
        self.output = SimulationOutput()

        # ------------------------------------------------------
        # ROUTING & VEHICLE KINEMATICS (M8)
        # ------------------------------------------------------

        self.routing_engine = routing_engine
        self.active_routes = {}

        # ------------------------------------------------------
        # FLEET COORDINATION & BALANCING (M9)
        # ------------------------------------------------------

        self.coordinator = FleetCoordinator()
        self.repositioning_data = {}
        self._last_coordination_time = 0
        self.reposition_recommendations = []

        # ------------------------------------------------------
        # DATASETS
        # ------------------------------------------------------

        self.patients = pd.read_csv(
            DATASET_DIR / "patient_incidents.csv"
        )

        self.ambulances = pd.read_csv(
            DATASET_DIR / "ambulances.csv"
        )

        self.hospitals = pd.read_csv(
            DATASET_DIR / "hospitals.csv"
        )

        # ------------------------------------------------------
        # RUNTIME TRACKING
        # ------------------------------------------------------

        self.redirect_history = {}
        self.last_known_eta = {}
        self.eta_recheck_required = set()
        self.mci_counter = 1
        self.tick_count = 0
        self._sim_time_accumulator = 0.0

        # ------------------------------------------------------
        # HISTORICAL PERSISTENCE HOOKS (OPTIONAL)
        # ------------------------------------------------------

        self.persistence_bridge = None
        self.run_id = None

        # ------------------------------------------------------
        # INITIALIZE
        # ------------------------------------------------------

        try:
            _, _, _, self._hospitals_df, self._model = load_data()
        except Exception:
            self._hospitals_df = None
            self._model = None

        self.load_state()
        self.register_event_handlers()

    @property
    def sim_time(self) -> int:
        """Convenience property for current simulation clock."""
        return int(self.state.current_time)

    def _record_persistence(self, callback_name, *args, **kwargs):
        """Invoke persistence hook safely if persistence_bridge is active."""
        if getattr(self, "persistence_bridge", None) and getattr(self, "run_id", None):
            fn = getattr(self.persistence_bridge, callback_name, None)
            if callable(fn):
                try:
                    fn(self.run_id, *args, **kwargs)
                except Exception:
                    pass

    # ==========================================================
    # STATE LOADING
    # ==========================================================

    def load_state(self):
        self.tick_count = 0
        self._sim_time_accumulator = 0.0

        if hasattr(self, "coordinator"):
            if hasattr(self.coordinator, "hospital_balancer"):
                self.coordinator.hospital_balancer.clear()
            if hasattr(self.coordinator, "mci_manager"):
                self.coordinator.mci_manager.clear()

        for _, row in self.ambulances.iterrows():

            ambulance = AmbulanceState(
                ambulance_id=str(
                    row["Ambulance_ID"]
                ),

                ambulance_type=str(
                    row["Ambulance_Type"]
                ),

                latitude=float(
                    row["Latitude"]
                ),

                longitude=float(
                    row["Longitude"]
                ),

                status=str(
                    row["Availability"]
                ).upper(),
            )

            self.state.add_ambulance(
                ambulance
            )

        for _, row in self.hospitals.iterrows():

            hospital = HospitalState(
                hospital_id=str(
                    row["Hospital_ID"]
                ),

                hospital_type=str(
                    row["Hospital_Type"]
                ),

                latitude=float(
                    row["Latitude"]
                ),

                longitude=float(
                    row["Longitude"]
                ),

                capacity=int(
                    row["Hospital_Capacity"]
                ),

                current_load=int(
                    row["Current_Load"]
                ),

                icu_capacity=int(
                    row["ICU_Capacity"]
                ),

                current_icu_load=int(
                    row["Current_ICU_Load"]
                ),
            )

            self.state.add_hospital(
                hospital
            )

    # ==========================================================
    # EVENT HANDLERS
    # ==========================================================

    def register_event_handlers(self):

        self.events.register_handler(
            "HOSPITAL_FULL",
            self.handle_hospital_full,
        )

        self.events.register_handler(
            "HOSPITAL_LOAD_CHANGE",
            self.handle_hospital_load_change,
        )

        self.events.register_handler(
            "ICU_LOAD_CHANGE",
            self.handle_icu_load_change,
        )

        self.events.register_handler(
            "TRAFFIC_CHANGE",
            self.handle_traffic_change,
        )

        self.events.register_handler(
            "NEW_INCIDENT",
            self.handle_new_incident,
        )

        self.events.register_handler(
            "AMBULANCE_STATUS_CHANGE",
            self.handle_ambulance_status_change,
        )

    # ==========================================================
    # DEFAULT EVENTS
    # ==========================================================

    def schedule_default_events(self):

        self.events.schedule(
            time=5,
            event_type="HOSPITAL_FULL",
            data={
                "hospital_id": "HOSP_182"
            },
        )

        self.events.schedule(
            time=7,
            event_type="TRAFFIC_CHANGE",
            data={
                "ambulance_id": "AMB_0575",
                "level": "SEVERE",
            },
        )

        self.events.schedule(
            time=9,
            event_type="ICU_LOAD_CHANGE",
            data={
                "hospital_id": "HOSP_279",
                "increase": 10,
            },
        )

        self.events.schedule(
            time=11,
            event_type="HOSPITAL_LOAD_CHANGE",
            data={
                "hospital_id": "HOSP_099",
                "increase": 20,
            },
        )

    # ==========================================================
    # CREATE INCIDENT
    # ==========================================================

    def create_incident(
        self,
        incident_id,
    ):

        incident_id = int(
            incident_id
        )

        rows = self.patients[
            self.patients["Incident_ID"]
            == incident_id
        ]

        if rows.empty:
            raise ValueError(
                f"Incident {incident_id} not found."
            )

        row = rows.iloc[0]

        # Derive live operational constraints from authoritative DispatchState
        available_ambulance_ids = {
            amb.ambulance_id
            for amb in self.state.ambulances.values()
            if amb.status == "AVAILABLE"
        }

        # Derive live operational constraints from authoritative DispatchState and HospitalBalancer
        projections = self.coordinator.get_hospital_projections(self.state.hospitals)

        suitable_hospital_ids = {
            hosp.hospital_id
            for hosp in self.state.hospitals.values()
            if not hosp.is_full
            and hosp.available_beds > 0
            and projections.get(hosp.hospital_id, {}).get("projected_available_beds", 0) > 0
        }

        live_icu_hospital_ids = {
            hosp.hospital_id
            for hosp in self.state.hospitals.values()
            if not hosp.is_full
            and hosp.available_icu > 0
            and projections.get(hosp.hospital_id, {}).get("projected_available_icu", 0) > 0
        }

        result = dispatch_incident(
            incident_id,
            available_ambulance_ids=available_ambulance_ids,
            suitable_hospital_ids=suitable_hospital_ids,
            live_icu_hospital_ids=live_icu_hospital_ids,
        )

        patient_data = result.get(
            "patient",
            {},
        )

        ambulance_data = result.get(
            "ambulance"
        )

        hospital_data = result.get(
            "hospital"
        )

        severity = str(
            patient_data.get(
                "predicted_severity",
                "Unknown",
            )
        )

        priority_text = str(
            patient_data.get(
                "priority",
                "P5",
            )
        )

        try:

            priority = int(
                priority_text.replace(
                    "P",
                    "",
                )
            )

        except ValueError:

            priority = 5

        incident = IncidentState(
            incident_id=incident_id,

            condition=str(
                row["Condition"]
            ),

            severity=severity,

            priority=priority,

            status="DISPATCHED",
        )

        self.state.add_incident(
            incident
        )

        # ------------------------------------------------------
        # Ambulance
        # ------------------------------------------------------

        if ambulance_data:

            ambulance_id = str(
                ambulance_data[
                    "ambulance_id"
                ]
            )

            ambulance = (
                self.state.ambulances.get(
                    ambulance_id
                )
            )

            if ambulance:

                if getattr(ambulance, "is_repositioning", False) or ambulance.status == "REPOSITIONING":
                    self.repositioning_data.pop(ambulance.ambulance_id, None)
                    self.active_routes.pop(ambulance.ambulance_id, None)
                    ambulance.is_repositioning = False
                    ambulance.reposition_target = None
                    ambulance.reposition_origin_zone = None
                    ambulance.reposition_target_zone = None
                    self.state.add_event(f"Ambulance {ambulance.ambulance_id} intercepted from repositioning for emergency incident {incident_id}.")
                    self._record_persistence(
                        "record_reposition_complete",
                        ambulance_id=ambulance.ambulance_id,
                        completed_sim_time=self.sim_time,
                        final_status="INTERCEPTED",
                    )

                ambulance.status = "EN_ROUTE"

                ambulance.incident_id = (
                    incident_id
                )

                ambulance.base_eta_minutes = (
                    float(
                        ambulance_data[
                            "eta_minutes"
                        ]
                    )
                )

                ambulance.traffic_level = str(
                    ambulance_data.get(
                        "traffic_level",
                        "NORMAL",
                    )
                ).upper()

                ambulance.road_condition = str(
                    ambulance_data.get(
                        "road_condition",
                        "GOOD",
                    )
                ).upper()

                ambulance.route_distance_km = (
                    ambulance_data.get(
                        "distance_km"
                    )
                )

                ambulance.recalculate_eta()

                incident.ambulance_id = (
                    ambulance_id
                )

                self.last_known_eta[
                    incident_id
                ] = ambulance.eta_minutes

        # ------------------------------------------------------
        # Hospital
        # ------------------------------------------------------

        if hospital_data:

            # Predictive hospital balancer refinement (M9 Phase 3)
            balanced_hosp_id = self.coordinator.select_balanced_hospital(
                hospitals=self.state.hospitals,
                patient_lat=float(row["Patient_Lat"] if "Patient_Lat" in row else row["Latitude"]),
                patient_lon=float(row["Patient_Lon"] if "Patient_Lon" in row else row["Longitude"]),
                severity=severity,
                condition=str(row["Condition"]),
                routing_engine=self.routing_engine,
                candidate_ids=suitable_hospital_ids,
            )

            hospital_id = balanced_hosp_id if (balanced_hosp_id and balanced_hosp_id in self.state.hospitals) else str(hospital_data["hospital_id"])

            hospital_data["hospital_id"] = hospital_id
            incident.hospital_id = (
                hospital_id
            )

            ambulance = (
                self.state.ambulances.get(
                    incident.ambulance_id
                )
            )

            if ambulance:

                ambulance.hospital_id = (
                    hospital_id
                )

            hosp = self.state.hospitals.get(
                hospital_id
            )

            # Atomic in-flight reservation on dispatch (M9 Phase 3)
            if ambulance:
                self.coordinator.hospital_balancer.register_dispatch(
                    ambulance_id=ambulance.ambulance_id,
                    hospital_id=hospital_id,
                    severity=severity,
                    eta_minutes=float(ambulance.eta_minutes or 15.0),
                    sim_time=self.sim_time,
                )
                self._record_persistence(
                    "record_simulation_event",
                    sim_time=self.sim_time,
                    event_type="HOSPITAL_RESERVATION",
                    data={
                        "ambulance_id": ambulance.ambulance_id,
                        "hospital_id": hospital_id,
                        "severity": severity,
                        "incident_id": incident_id,
                    },
                )

            if hosp and ambulance:
                route = self.routing_engine.generate_route(
                    origin=(float(ambulance.latitude), float(ambulance.longitude)),
                    destination=(float(hosp.latitude), float(hosp.longitude)),
                    vehicle_type=str(ambulance.ambulance_type),
                    traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
                    road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
                )
                if ambulance.eta_minutes is not None and ambulance.eta_minutes > 1.0:
                    min_plausible_minutes = (route.route_distance_km / 120.0) * 60.0
                    if float(ambulance.eta_minutes) >= min_plausible_minutes:
                        route.total_duration_minutes = float(ambulance.eta_minutes)
                    else:
                        route.total_duration_minutes = float(route.initial_eta_minutes)
                        ambulance.eta_minutes = route.initial_eta_minutes
                        ambulance.base_eta_minutes = route.initial_eta_minutes
                else:
                    ambulance.eta_minutes = route.initial_eta_minutes
                    ambulance.base_eta_minutes = route.initial_eta_minutes
                    route.total_duration_minutes = float(route.initial_eta_minutes)
                route.elapsed_minutes = 0.0

                self.active_routes[ambulance.ambulance_id] = route
                ambulance.route_distance_km = route.route_distance_km
                ambulance.route_waypoints = [list(wp) for wp in route.waypoints]
                ambulance.routing_engine = route.routing_engine

        # ------------------------------------------------------
        # Redirect history
        # ------------------------------------------------------

        self.redirect_history[
            incident_id
        ] = set()

        self.state.add_event(
            f"Incident {incident_id} dispatched."
        )

        # Historical persistence hook
        if ambulance_data and hospital_data:
            self._record_persistence(
                "record_dispatch",
                incident_id=incident_id,
                source="DATASET_REPLAY",
                condition=incident.condition,
                predicted_severity=incident.severity,
                priority=incident.priority,
                ml_confidence=result.get("patient", {}).get("confidence"),
                patient_lat=float(row.get("Patient_Lat", row.get("Latitude", 26.9124))),
                patient_lon=float(row.get("Patient_Lon", row.get("Longitude", 75.7873))),
                dispatched_sim_time=self.state.current_time,
                ambulance_id=str(ambulance_data["ambulance_id"]),
                ambulance_type=str(ambulance_data.get("ambulance_type", "DEFAULT")),
                hospital_id=str(hospital_data["hospital_id"]),
                initial_eta_minutes=float(ambulance_data.get("eta_minutes", 0.0)),
                route_distance_km=float(ambulance_data.get("route_distance_km")) if ambulance_data.get("route_distance_km") is not None else None,
                traffic_level=str(ambulance_data.get("traffic_level", "NORMAL")),
                road_condition=str(ambulance_data.get("road_condition", "GOOD")),
            )

        return result

    # ==========================================================
    # CREATE CUSTOM INCIDENT (LIVE EMERGENCY CALL INTAKE)
    # ==========================================================

    def create_custom_incident(
        self,
        custom_data,
    ):
        """
        Dispatch a new live emergency call dynamically.

        Validates against the actual 24-feature ML model contract,
        selects the best available ambulance and suitable hospital
        from authoritative live state, and mutates DispatchState.
        """
        if not hasattr(self, "_next_custom_id"):
            self._next_custom_id = 100001

        incident_id = self._next_custom_id
        self._next_custom_id += 1

        feature_names = [
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

        model = getattr(self, "_model", None)
        hospitals_df = getattr(self, "_hospitals_df", None)
        if model is None or hospitals_df is None:
            (
                _,
                _,
                _,
                hospitals_df,
                model,
            ) = load_data()
            self._model = model
            self._hospitals_df = hospitals_df

        (
            predicted_severity,
            confidence,
            probabilities,
        ) = predict_severity(
            model,
            custom_data,
        )

        priority_number = SEVERITY_PRIORITY.get(
            predicted_severity,
            5,
        )

        patient_lat = float(custom_data["patient_lat"])
        patient_lon = float(custom_data["patient_lon"])

        # ------------------------------------------------------
        # Select Ambulance from live available fleet
        # ------------------------------------------------------
        available_ambs = [
            amb
            for amb in self.state.ambulances.values()
            if (amb.status == "AVAILABLE" or getattr(amb, "is_repositioning", False) or amb.status == "REPOSITIONING")
            and amb.incident_id is None
        ]

        selected_amb = None
        selected_eta = None
        selected_distance = None
        cap_match = False
        fallback = False

        if available_ambs:
            required_level = required_ambulance_level(
                predicted_severity
            )

            matching_ambs = []
            fallback_ambs = []
            for amb in available_ambs:
                cap_level = AMBULANCE_CAPABILITY.get(amb.ambulance_type, 1)
                if cap_level >= required_level:
                    matching_ambs.append(amb)
                else:
                    fallback_ambs.append(amb)

            candidates = matching_ambs if matching_ambs else fallback_ambs
            is_fallback = not bool(matching_ambs)

            best_amb = None
            best_eta = float("inf")
            best_dist = float("inf")
            r2 = radians(patient_lat)
            o2 = radians(patient_lon)
            cos_r2 = cos(r2)

            for amb in candidates:
                r1 = radians(float(amb.latitude))
                dlat = r2 - r1
                dlon = o2 - radians(float(amb.longitude))
                a = sin(dlat * 0.5) ** 2 + cos(r1) * cos_r2 * sin(dlon * 0.5) ** 2
                a = min(1.0, max(0.0, a))
                dist = round(12742.0 * atan2(sqrt(a), sqrt(1.0 - a)), 3)
                if (dist / 55.0) * 60.0 > best_eta:
                    continue
                speed = _AMB_SPEEDS.get(amb.ambulance_type, 50.0)
                t_mult = _TRAF_MULTS.get(amb.traffic_level, 1.0)
                r_mult = _ROAD_MULTS.get(amb.road_condition, 1.0)
                eta = round(max(0.1, (dist / speed) * 60.0 * t_mult * r_mult), 2)
                if eta < best_eta or (eta == best_eta and dist < best_dist):
                    best_eta = eta
                    best_dist = dist
                    best_amb = amb

            selected_amb = best_amb
            selected_eta = best_eta
            selected_distance = best_dist
            cap_match = not is_fallback
            fallback = is_fallback

        if selected_amb is None:
            return {
                "status": "NO_AMBULANCE_AVAILABLE",
                "incident_id": incident_id,
                "predicted_severity": predicted_severity,
                "confidence": confidence,
                "patient": {
                    "condition": str(custom_data["Condition"]),
                    "predicted_severity": predicted_severity,
                    "priority": f"P{priority_number}",
                    "confidence": confidence,
                },
                "ambulance": None,
                "hospital": None,
            }

        # ------------------------------------------------------
        # Select Hospital from live state (with HospitalBalancer)
        # ------------------------------------------------------
        projections = self.coordinator.get_hospital_projections(self.state.hospitals)

        suitable_hospital_ids = {
            h.hospital_id
            for h in self.state.hospitals.values()
            if not h.is_full
            and h.available_beds > 0
            and projections.get(h.hospital_id, {}).get("projected_available_beds", 0) > 0
        }

        live_icu_hospital_ids = {
            h.hospital_id
            for h in self.state.hospitals.values()
            if not h.is_full
            and h.available_icu > 0
            and projections.get(h.hospital_id, {}).get("projected_available_icu", 0) > 0
        }

        # Predictive hospital balancer refinement (M9 Phase 3)
        balanced_hosp_id = self.coordinator.hospital_balancer.select_balanced_hospital(
            hospitals=self.state.hospitals,
            patient_lat=patient_lat,
            patient_lon=patient_lon,
            severity=predicted_severity,
            condition=str(custom_data["Condition"]),
            routing_engine=self.routing_engine,
            candidate_ids=suitable_hospital_ids,
            projections=projections,
        )

        selected_hospital_row = None
        if balanced_hosp_id and balanced_hosp_id in self.state.hospitals:
            hospital_id = balanced_hosp_id
        else:
            (
                selected_hospital_row,
                _,
            ) = select_hospital(
                predicted_severity,
                str(custom_data["Condition"]),
                patient_lat,
                patient_lon,
                hospitals_df,
                suitable_hospital_ids=suitable_hospital_ids,
                live_icu_hospital_ids=live_icu_hospital_ids,
            )

            if selected_hospital_row is None:
                return {
                    "status": "NO_SUITABLE_HOSPITAL",
                    "incident_id": incident_id,
                    "patient": {
                        "condition": str(custom_data["Condition"]),
                        "predicted_severity": predicted_severity,
                        "priority": f"P{priority_number}",
                        "confidence": confidence,
                    },
                    "ambulance": {
                        "ambulance_id": str(selected_amb.ambulance_id),
                        "ambulance_type": str(selected_amb.ambulance_type),
                        "eta_minutes": float(selected_eta),
                        "distance_km": float(selected_distance),
                        "traffic_level": str(selected_amb.traffic_level),
                        "road_condition": str(selected_amb.road_condition),
                        "capability_match": bool(cap_match),
                        "fallback": bool(fallback),
                    },
                    "hospital": None,
                }
            hospital_id = str(selected_hospital_row["Hospital_ID"])

        # ------------------------------------------------------
        # Mutate Authoritative Live State
        # ------------------------------------------------------
        incident = IncidentState(
            incident_id=incident_id,
            condition=str(custom_data["Condition"]),
            severity=predicted_severity,
            priority=priority_number,
            status="DISPATCHED",
            ambulance_id=str(selected_amb.ambulance_id),
            hospital_id=hospital_id,
        )
        self.state.add_incident(incident)

        if getattr(selected_amb, "is_repositioning", False) or selected_amb.status == "REPOSITIONING":
            self.repositioning_data.pop(selected_amb.ambulance_id, None)
            self.active_routes.pop(selected_amb.ambulance_id, None)
            selected_amb.is_repositioning = False
            selected_amb.reposition_target = None
            selected_amb.reposition_origin_zone = None
            selected_amb.reposition_target_zone = None
            self.state.add_event(f"Ambulance {selected_amb.ambulance_id} intercepted from repositioning for emergency incident {incident_id}.")
            self._record_persistence(
                "record_reposition_complete",
                ambulance_id=selected_amb.ambulance_id,
                completed_sim_time=self.sim_time,
                final_status="INTERCEPTED",
            )

        selected_amb.status = "EN_ROUTE"
        selected_amb.incident_id = incident_id
        selected_amb.hospital_id = hospital_id
        selected_amb.base_eta_minutes = float(selected_eta)
        selected_amb.eta_minutes = float(selected_eta)
        selected_amb.route_distance_km = float(selected_distance)
        self.last_known_eta[incident_id] = float(selected_eta)

        hosp_state = self.state.hospitals.get(hospital_id)
        if hosp_state:
            # Atomic in-flight reservation on dispatch (M9 Phase 3)
            self.coordinator.hospital_balancer.register_dispatch(
                ambulance_id=selected_amb.ambulance_id,
                hospital_id=hospital_id,
                severity=predicted_severity,
                eta_minutes=float(selected_eta),
                sim_time=self.sim_time,
            )
            self._record_persistence(
                "record_simulation_event",
                sim_time=self.sim_time,
                event_type="HOSPITAL_RESERVATION",
                data={
                    "ambulance_id": selected_amb.ambulance_id,
                    "hospital_id": hospital_id,
                    "severity": predicted_severity,
                    "incident_id": incident_id,
                },
            )

            route = self.routing_engine.generate_route(
                origin=(float(selected_amb.latitude), float(selected_amb.longitude)),
                destination=(float(hosp_state.latitude), float(hosp_state.longitude)),
                vehicle_type=str(selected_amb.ambulance_type),
                traffic_level=str(getattr(selected_amb, "traffic_level", "NORMAL")),
                road_condition=str(getattr(selected_amb, "road_condition", "GOOD")),
            )
            route.total_duration_minutes = max(0.1, float(selected_eta))
            route.elapsed_minutes = 0.0
            self.active_routes[selected_amb.ambulance_id] = route
            selected_amb.route_waypoints = [list(wp) for wp in route.waypoints]
            selected_amb.routing_engine = route.routing_engine

        self.redirect_history[incident_id] = set()

        self.state.add_event(
            f"Live Incident {incident_id} ({predicted_severity}) dispatched: "
            f"{selected_amb.ambulance_id} -> {hospital_id}."
        )

        # Historical persistence hook
        self._record_persistence(
            "record_dispatch",
            incident_id=incident_id,
            source="DYNAMIC_INTAKE",
            condition=str(custom_data["Condition"]),
            predicted_severity=predicted_severity,
            priority=priority_number,
            ml_confidence=confidence,
            patient_lat=float(custom_data.get("patient_lat", 26.9124)),
            patient_lon=float(custom_data.get("patient_lon", 75.7873)),
            dispatched_sim_time=self.state.current_time,
            ambulance_id=str(selected_amb.ambulance_id),
            ambulance_type=str(selected_amb.ambulance_type),
            hospital_id=hospital_id,
            initial_eta_minutes=float(selected_eta),
            route_distance_km=float(selected_distance),
            traffic_level=getattr(selected_amb, "traffic_level", "NORMAL"),
            road_condition=getattr(selected_amb, "road_condition", "GOOD"),
        )

        return {
            "status": "DISPATCH_RECOMMENDED",
            "incident_id": incident_id,
            "patient": {
                "condition": str(custom_data["Condition"]),
                "predicted_severity": predicted_severity,
                "priority": f"P{priority_number}",
                "confidence": confidence,
            },
            "ambulance": {
                "ambulance_id": str(selected_amb.ambulance_id),
                "ambulance_type": str(selected_amb.ambulance_type),
                "eta_minutes": float(selected_eta),
                "distance_km": float(selected_distance),
                "traffic_level": str(selected_amb.traffic_level),
                "road_condition": str(selected_amb.road_condition),
                "capability_match": bool(cap_match),
                "fallback": bool(fallback),
            },
            "hospital": {
                "hospital_id": hospital_id,
                "hospital_type": str(selected_hospital_row["Hospital_Type"]) if selected_hospital_row is not None else str(getattr(hosp_state, "hospital_type", "General")),
                "distance_km": float(selected_hospital_row["Distance_KM"]) if selected_hospital_row is not None else float(_distance_between(patient_lat, patient_lon, hosp_state.latitude, hosp_state.longitude)),
                "available_beds": int(hosp_state.available_beds if hosp_state else selected_hospital_row["Available_Beds"]),
                "available_icu": int(hosp_state.available_icu if hosp_state else selected_hospital_row["Available_ICU"]),
                "suitability": int(selected_hospital_row["Suitability"]) if selected_hospital_row is not None else int(hospital_suitability(str(custom_data["Condition"]), getattr(hosp_state, "hospital_type", "General"))),
            },
        }

    # ==========================================================
    # MULTI-CASUALTY INCIDENTS (MCI) (M9 Phase 4)
    # ==========================================================

    def _generate_mci_casualty_profile(
        self,
        index: int,
        condition: str,
        scene_lat: float,
        scene_lon: float,
    ) -> dict:
        """
        Generate a clinically realistic casualty profile for an MCI victim.
        Provides a diverse spread of severities:
          - Index 0: Critical (P1)
          - Index 1: Emergency (P2)
          - Index 2: Moderate (P3)
          - Index 3: Low (P4)
        """
        tier = index % 4
        lat_offset = ((index * 17) % 7 - 3) * 0.0003
        lon_offset = ((index * 23) % 7 - 3) * 0.0003

        if tier == 0:
            return {
                "Sex": "Male" if index % 2 == 0 else "Female",
                "Age": 28 + (index * 7) % 45,
                "Condition": str(condition),
                "Arrival_Mode": "Ambulance",
                "Injury_Type": "Severe" if condition == "Trauma" else "No Injury",
                "Heart_Rate": 138.0,
                "SpO2": 85.0,
                "Systolic_BP": 78.0,
                "Diastolic_BP": 48.0,
                "Respiratory_Rate": 30.0,
                "Temperature": 38.2,
                "Consciousness": "Unconscious",
                "Oxygen_Requirement": "Oxygen Mask",
                "GCS": 7,
                "Pain_Score": 9,
                "Blood_Glucose": 185.0,
                "Respiratory_Distress": 1,
                "Chest_Pain": 1,
                "Bleeding": 1,
                "Seizure": 0,
                "Diabetes": 0,
                "Hypertension": 1,
                "Heart_Disease": 1 if condition == "Cardiac" else 0,
                "Respiratory_Disease": 1 if condition == "Respiratory" else 0,
                "patient_lat": scene_lat + lat_offset,
                "patient_lon": scene_lon + lon_offset,
            }
        elif tier == 1:
            return {
                "Sex": "Female" if index % 2 == 0 else "Male",
                "Age": 32 + (index * 5) % 40,
                "Condition": str(condition),
                "Arrival_Mode": "Ambulance",
                "Injury_Type": "Moderate" if condition == "Trauma" else "No Injury",
                "Heart_Rate": 115.0,
                "SpO2": 91.0,
                "Systolic_BP": 105.0,
                "Diastolic_BP": 68.0,
                "Respiratory_Rate": 24.0,
                "Temperature": 37.6,
                "Consciousness": "Altered",
                "Oxygen_Requirement": "Nasal Cannula",
                "GCS": 12,
                "Pain_Score": 7,
                "Blood_Glucose": 140.0,
                "Respiratory_Distress": 1,
                "Chest_Pain": 1 if condition in ("Cardiac", "Trauma") else 0,
                "Bleeding": 1 if condition == "Trauma" else 0,
                "Seizure": 0,
                "Diabetes": 0,
                "Hypertension": 0,
                "Heart_Disease": 0,
                "Respiratory_Disease": 0,
                "patient_lat": scene_lat + lat_offset,
                "patient_lon": scene_lon + lon_offset,
            }
        elif tier == 2:
            return {
                "Sex": "Male" if index % 2 == 0 else "Female",
                "Age": 22 + (index * 9) % 50,
                "Condition": str(condition),
                "Arrival_Mode": "Ambulance",
                "Injury_Type": "Minor" if condition == "Trauma" else "No Injury",
                "Heart_Rate": 98.0,
                "SpO2": 95.0,
                "Systolic_BP": 122.0,
                "Diastolic_BP": 80.0,
                "Respiratory_Rate": 18.0,
                "Temperature": 37.0,
                "Consciousness": "Alert",
                "Oxygen_Requirement": "None",
                "GCS": 15,
                "Pain_Score": 5,
                "Blood_Glucose": 110.0,
                "Respiratory_Distress": 0,
                "Chest_Pain": 0,
                "Bleeding": 0,
                "Seizure": 0,
                "Diabetes": 0,
                "Hypertension": 0,
                "Heart_Disease": 0,
                "Respiratory_Disease": 0,
                "patient_lat": scene_lat + lat_offset,
                "patient_lon": scene_lon + lon_offset,
            }
        else:
            return {
                "Sex": "Female" if index % 2 == 0 else "Male",
                "Age": 20 + (index * 6) % 35,
                "Condition": str(condition),
                "Arrival_Mode": "Ambulance",
                "Injury_Type": "Superficial" if condition == "Trauma" else "No Injury",
                "Heart_Rate": 78.0,
                "SpO2": 98.0,
                "Systolic_BP": 120.0,
                "Diastolic_BP": 78.0,
                "Respiratory_Rate": 16.0,
                "Temperature": 36.8,
                "Consciousness": "Alert",
                "Oxygen_Requirement": "None",
                "GCS": 15,
                "Pain_Score": 2,
                "Blood_Glucose": 95.0,
                "Respiratory_Distress": 0,
                "Chest_Pain": 0,
                "Bleeding": 0,
                "Seizure": 0,
                "Diabetes": 0,
                "Hypertension": 0,
                "Heart_Disease": 0,
                "Respiratory_Disease": 0,
                "patient_lat": scene_lat + lat_offset,
                "patient_lon": scene_lon + lon_offset,
            }

    def declare_mci(
        self,
        mci_id: Optional[str] = None,
        name: Optional[str] = "Multi-Casualty Incident",
        latitude: float = 26.9124,
        longitude: float = 75.7873,
        estimated_casualties: int = 5,
        primary_condition: str = "Trauma",
        description: str = "",
        notes: str = "",
        casualties: Optional[List[dict]] = None,
    ) -> dict:
        """
        Declare a Multi-Casualty Incident.
        1. Creates parent MCIEvent in MCIManager (DECLARED).
        2. Generates and triages child incidents individually with ML pipeline.
        3. Prioritizes P1 -> P2 -> P3 -> P4 -> P5.
        4. Atomically assigns candidate ambulances (excluding committed, intercepting repositioning).
        5. Disperses casualties across balanced hospitals using HospitalBalancer and surge damping.
        6. Registers in-flight reservations and transitions MCI to EVACUATING.
        7. Persists records via M7 persistence bridge.
        """
        if not mci_id:
            mci_id = f"MCI_{self.sim_time}_{self.mci_counter:03d}"
            self.mci_counter += 1
        else:
            mci_id = str(mci_id)

        mci = self.coordinator.mci_manager.create_mci(
            mci_id=mci_id,
            name=name or "Multi-Casualty Incident",
            latitude=float(latitude),
            longitude=float(longitude),
            declared_sim_time=self.sim_time,
            estimated_casualties=estimated_casualties,
            description=description,
            notes=notes,
        )

        self.state.add_event(
            f"MCI DECLARED: {mci.name} ({mci_id}) at ({latitude:.4f}, {longitude:.4f}) with ~{estimated_casualties} casualties."
        )

        self._record_persistence(
            "record_mci_declared",
            mci_id=mci_id,
            name=mci.name,
            latitude=mci.latitude,
            longitude=mci.longitude,
            declared_sim_time=mci.declared_sim_time,
            total_casualties=estimated_casualties,
            notes=notes,
        )

        (
            patients_df,
            ambulances_df,
            scenarios_df,
            hospitals_df,
            model,
        ) = load_data()

        feature_names = [
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

        if not hasattr(self, "_next_custom_id"):
            self._next_custom_id = 100001

        triaged_casualties = []
        num_casualties = len(casualties) if casualties else max(1, estimated_casualties)

        for idx in range(num_casualties):
            incident_id = self._next_custom_id
            self._next_custom_id += 1

            if casualties and idx < len(casualties):
                cas_data = dict(casualties[idx])
                if "patient_lat" not in cas_data:
                    cas_data["patient_lat"] = latitude
                if "patient_lon" not in cas_data:
                    cas_data["patient_lon"] = longitude
            else:
                cas_data = self._generate_mci_casualty_profile(idx, primary_condition, latitude, longitude)

            # Individual ML triage
            model_input = pd.DataFrame([{col: cas_data[col] for col in feature_names}])
            predicted_severity, confidence, _ = predict_severity(model, model_input)
            priority_number = SEVERITY_PRIORITY.get(predicted_severity, 5)

            # Standard child IncidentState
            incident = IncidentState(
                incident_id=incident_id,
                condition=str(cas_data["Condition"]),
                severity=predicted_severity,
                priority=priority_number,
                status="PENDING_DISPATCH",
                ambulance_id=None,
                hospital_id=None,
            )
            self.state.add_incident(incident)

            # Associate child with parent MCI (transitions to TRIAGED)
            self.coordinator.mci_manager.attach_child_incident(
                mci_id=mci_id,
                incident_id=incident_id,
                severity=predicted_severity,
                priority=priority_number,
            )

            triaged_casualties.append({
                "incident_id": incident_id,
                "incident": incident,
                "severity": predicted_severity,
                "priority": priority_number,
                "confidence": confidence,
                "lat": float(cas_data["patient_lat"]),
                "lon": float(cas_data["patient_lon"]),
                "data": cas_data,
            })

        # Coordinated triage priority order: P1 -> P2 -> P3 -> P4 -> P5
        triaged_casualties.sort(key=lambda x: x["priority"])

        # Available fleet pool: AVAILABLE or REPOSITIONING (excluding committed)
        available_pool = [
            amb for amb in self.state.ambulances.values()
            if (amb.status == "AVAILABLE" or getattr(amb, "is_repositioning", False) or amb.status == "REPOSITIONING")
            and amb.incident_id is None
        ]

        child_summaries = []
        dispatched_count = 0
        waiting_count = 0

        for cas in triaged_casualties:
            inc = cas["incident"]
            sev = cas["severity"]
            pri = cas["priority"]
            c_lat, c_lon = cas["lat"], cas["lon"]

            if not available_pool:
                inc.status = "WAITING_AMBULANCE"
                waiting_count += 1
                child_summaries.append({
                    "incident_id": inc.incident_id,
                    "severity": sev,
                    "priority": pri,
                    "status": "WAITING_AMBULANCE",
                    "ambulance_id": None,
                    "hospital_id": None,
                    "eta_minutes": None,
                })
                continue

            # Rank candidate ambulances
            req_level = required_ambulance_level(sev)
            scored_ambs = []
            for amb in available_pool:
                d = _distance_between(amb.latitude, amb.longitude, c_lat, c_lon)
                eta = _estimate_eta_to_patient(amb, d)
                cap_level = AMBULANCE_CAPABILITY.get(amb.ambulance_type, 1)
                matches = cap_level >= req_level
                scored_ambs.append((not matches, eta, d, amb))

            scored_ambs.sort(key=lambda x: (x[0], x[1], x[2]))
            selected_amb = scored_ambs[0][3]
            selected_eta = scored_ambs[0][1]
            selected_dist = scored_ambs[0][2]

            # Remove from pool atomically (no double-booking)
            available_pool.remove(selected_amb)

            # Intercept repositioning if applicable
            if getattr(selected_amb, "is_repositioning", False) or selected_amb.status == "REPOSITIONING":
                self.repositioning_data.pop(selected_amb.ambulance_id, None)
                self.active_routes.pop(selected_amb.ambulance_id, None)
                selected_amb.is_repositioning = False
                selected_amb.reposition_target = None
                selected_amb.reposition_origin_zone = None
                selected_amb.reposition_target_zone = None
                self.state.add_event(
                    f"Ambulance {selected_amb.ambulance_id} intercepted from repositioning for MCI casualty {inc.incident_id}."
                )
                self._record_persistence(
                    "record_reposition_complete",
                    ambulance_id=selected_amb.ambulance_id,
                    completed_sim_time=self.sim_time,
                    final_status="INTERCEPTED",
                )

            # Select balanced hospital with surge damping
            projections = self.coordinator.get_hospital_projections(self.state.hospitals)
            suitable_hospital_ids = {
                hid for hid, p in projections.items()
                if p["projected_available_beds"] > 0
            }
            if sev == "Critical":
                suitable_hospital_ids = {
                    hid for hid in suitable_hospital_ids
                    if projections[hid]["projected_available_icu"] > 0
                }

            chosen_hosp_id = self.coordinator.select_balanced_hospital(
                hospitals=self.state.hospitals,
                patient_lat=c_lat,
                patient_lon=c_lon,
                severity=sev,
                condition=inc.condition,
                routing_engine=self.routing_engine,
                candidate_ids=suitable_hospital_ids,
                mci_surge_counts=mci.hospital_distribution,
            )

            if not chosen_hosp_id:
                chosen_hosp_id = next(iter(self.state.hospitals.keys()))

            # Mutate state
            inc.status = "DISPATCHED"
            inc.ambulance_id = selected_amb.ambulance_id
            inc.hospital_id = chosen_hosp_id

            selected_amb.status = "EN_ROUTE"
            selected_amb.incident_id = inc.incident_id
            selected_amb.hospital_id = chosen_hosp_id
            selected_amb.base_eta_minutes = float(selected_eta)
            selected_amb.eta_minutes = float(selected_eta)
            selected_amb.route_distance_km = float(selected_dist)
            self.last_known_eta[inc.incident_id] = float(selected_eta)
            self.redirect_history[inc.incident_id] = set()

            # M8 Route generation
            target_hosp = self.state.hospitals[chosen_hosp_id]
            route = self.routing_engine.generate_route(
                origin=(float(selected_amb.latitude), float(selected_amb.longitude)),
                destination=(float(target_hosp.latitude), float(target_hosp.longitude)),
                vehicle_type=str(selected_amb.ambulance_type),
                traffic_level=str(getattr(selected_amb, "traffic_level", "NORMAL")),
                road_condition=str(getattr(selected_amb, "road_condition", "GOOD")),
            )
            self.active_routes[selected_amb.ambulance_id] = route
            selected_amb.route_waypoints = [list(wp) for wp in route.waypoints]
            selected_amb.routing_engine = route.routing_engine

            # Register in-flight reservation in HospitalBalancer
            self.coordinator.hospital_balancer.register_dispatch(
                ambulance_id=selected_amb.ambulance_id,
                hospital_id=chosen_hosp_id,
                severity=sev,
                eta_minutes=float(selected_eta),
                sim_time=self.sim_time,
            )

            # Record in parent MCI
            self.coordinator.mci_manager.record_assignment(
                mci_id=mci_id,
                ambulance_id=selected_amb.ambulance_id,
                hospital_id=chosen_hosp_id,
            )

            # Persistence
            self._record_persistence(
                "record_mci_child",
                mci_id=mci_id,
                incident_id=inc.incident_id,
                severity=sev,
                priority=pri,
                ambulance_id=selected_amb.ambulance_id,
                hospital_id=chosen_hosp_id,
                status="DISPATCHED",
            )
            self._record_persistence(
                "record_dispatch",
                incident_id=inc.incident_id,
                source="MCI_COORDINATED",
                condition=inc.condition,
                predicted_severity=sev,
                priority=pri,
                ml_confidence=cas.get("confidence"),
                patient_lat=c_lat,
                patient_lon=c_lon,
                dispatched_sim_time=self.sim_time,
                ambulance_id=selected_amb.ambulance_id,
                ambulance_type=selected_amb.ambulance_type,
                hospital_id=chosen_hosp_id,
                initial_eta_minutes=float(selected_eta),
                route_distance_km=float(selected_dist),
                traffic_level=getattr(selected_amb, "traffic_level", "NORMAL"),
                road_condition=getattr(selected_amb, "road_condition", "GOOD"),
            )

            dispatched_count += 1
            child_summaries.append({
                "incident_id": inc.incident_id,
                "severity": sev,
                "priority": pri,
                "status": "DISPATCHED",
                "ambulance_id": selected_amb.ambulance_id,
                "hospital_id": chosen_hosp_id,
                "eta_minutes": float(selected_eta),
            })

        return {
            "mci": mci.to_dict(),
            "child_incidents": child_summaries,
            "dispatched_count": dispatched_count,
            "waiting_count": waiting_count,
        }

    # ==========================================================
    # HOSPITAL FULL
    # ==========================================================

    def handle_hospital_full(
        self,
        data,
    ):

        hospital_id = str(
            data["hospital_id"]
        )

        hospital = (
            self.state.hospitals.get(
                hospital_id
            )
        )

        if hospital is None:
            return

        hospital.current_load = (
            hospital.capacity
        )

        self.state.add_event(
            f"Hospital {hospital_id} became full."
        )

        # Historical persistence hook
        self._record_persistence(
            "record_event",
            event_type="HOSPITAL_FULL",
            sim_time=self.state.current_time,
            facility_or_unit_id=hospital_id,
            message=f"Hospital {hospital_id} became full.",
        )

    # ==========================================================
    # HOSPITAL LOAD CHANGE
    # ==========================================================

    def handle_hospital_load_change(
        self,
        data,
    ):

        hospital_id = str(
            data["hospital_id"]
        )

        hospital = (
            self.state.hospitals.get(
                hospital_id
            )
        )

        if hospital is None:
            return

        increase = int(
            data.get(
                "increase",
                0,
            )
        )

        hospital.current_load = min(
            hospital.capacity,
            hospital.current_load + increase,
        )

        self.state.add_event(
            f"Hospital {hospital_id} "
            f"load increased by {increase}."
        )

    # ==========================================================
    # ICU LOAD CHANGE
    # ==========================================================

    def handle_icu_load_change(
        self,
        data,
    ):

        hospital_id = str(
            data["hospital_id"]
        )

        hospital = (
            self.state.hospitals.get(
                hospital_id
            )
        )

        if hospital is None:
            return

        increase = int(
            data.get(
                "increase",
                0,
            )
        )

        hospital.current_icu_load = min(
            hospital.icu_capacity,
            hospital.current_icu_load + increase,
        )

        self.state.add_event(
            f"Hospital {hospital_id} "
            f"ICU load increased by {increase}."
        )

    # ==========================================================
    # TRAFFIC CHANGE
    # ==========================================================

    def handle_traffic_change(
        self,
        data,
    ):

        ambulance_id = str(
            data["ambulance_id"]
        )

        level = str(
            data["level"]
        ).upper()

        ambulance = (
            self.state.ambulances.get(
                ambulance_id
            )
        )

        if ambulance is None:
            return

        if ambulance.status != "EN_ROUTE":

            self.state.add_event(
                f"Traffic event ignored for "
                f"{ambulance_id}; ambulance is "
                f"{ambulance.status}."
            )

            return

        old_eta = ambulance.eta_minutes

        ambulance.traffic_level = level

        ambulance.recalculate_eta()

        new_eta = ambulance.eta_minutes

        if old_eta is None or new_eta is None:
            return

        self.state.add_event(
            f"Traffic for ambulance "
            f"{ambulance_id} changed to "
            f"{level}. ETA changed from "
            f"{old_eta:.1f} to "
            f"{new_eta:.1f} min."
        )

        eta_increase = (
            new_eta - old_eta
        )

        if eta_increase <= 0:
            return

        percent_increase = (
            eta_increase
            / max(old_eta, 1)
        ) * 100

        if (
            percent_increase
            >= self.ETA_CHANGE_THRESHOLD_PERCENT
            or
            eta_increase
            >= self.ETA_CHANGE_THRESHOLD_MINUTES
        ):

            if ambulance.incident_id is not None:

                incident_id = int(
                    ambulance.incident_id
                )

                self.eta_recheck_required.add(
                    incident_id
                )

                self.state.add_event(
                    f"ETA deterioration detected "
                    f"for incident "
                    f"{incident_id}. "
                    f"Hospital destination will "
                    f"be re-evaluated."
                )

    # ==========================================================
    # NEW INCIDENT
    # ==========================================================

    def handle_new_incident(
        self,
        data,
    ):

        incident_id = int(
            data["incident_id"]
        )

        if incident_id in self.state.incidents:
            return

        self.create_incident(
            incident_id
        )

    # ==========================================================
    # AMBULANCE STATUS CHANGE
    # ==========================================================

    def handle_ambulance_status_change(
        self,
        data,
    ):

        ambulance_id = str(
            data["ambulance_id"]
        )

        status = str(
            data["status"]
        ).upper()

        ambulance = (
            self.state.ambulances.get(
                ambulance_id
            )
        )

        if ambulance is None:
            return

        ambulance.status = status

        self.state.add_event(
            f"Ambulance {ambulance_id} "
            f"status changed to {status}."
        )

    # ==========================================================
    # FLEET REPOSITIONING (M9)
    # ==========================================================

    def execute_reposition(
        self,
        ambulance_id: str,
        target_lat: float,
        target_lon: float,
        reason: str = "COVERAGE_DEFICIT",
    ) -> dict:
        """
        Initiate an idle ambulance repositioning movement toward target coordinates.
        """
        amb_id = str(ambulance_id)
        ambulance = self.state.ambulances.get(amb_id)
        if not ambulance:
            raise KeyError(f"Ambulance '{amb_id}' not found.")

        # Status guards
        status = str(ambulance.status).upper()
        if status != "AVAILABLE" or getattr(ambulance, "is_repositioning", False):
            raise ValueError(
                f"Ambulance '{amb_id}' is not AVAILABLE for repositioning (status={ambulance.status})."
            )

        if ambulance.incident_id is not None:
            raise ValueError(f"Ambulance '{amb_id}' is currently assigned to incident {ambulance.incident_id}.")

        # Coordinate bounds validation
        t_lat = float(target_lat)
        t_lon = float(target_lon)
        if not (-90.0 <= t_lat <= 90.0 and -180.0 <= t_lon <= 180.0):
            raise ValueError(f"Invalid target coordinates: ({t_lat}, {t_lon}).")

        # Determine origin and target zones
        origin_zone = self.coordinator.coverage_engine.assign_zone(float(ambulance.latitude), float(ambulance.longitude))
        target_zone = self.coordinator.coverage_engine.assign_zone(t_lat, t_lon)

        # Coverage protection: source zone must not be left critically defenseless
        coverage = self.coordinator.coverage_engine.evaluate_coverage(self.state.ambulances)
        origin_metrics = coverage.get(origin_zone)
        if origin_metrics and origin_metrics.status == "DEFICIT" and len(origin_metrics.available_ambulances) <= 1:
            raise ValueError(
                f"Source zone '{origin_zone}' is in DEFICIT with {len(origin_metrics.available_ambulances)} available unit(s); cannot reposition its last unit."
            )

        # Generate M8 route from CURRENT coordinates
        route = self.routing_engine.generate_route(
            origin=(float(ambulance.latitude), float(ambulance.longitude)),
            destination=(t_lat, t_lon),
            vehicle_type=str(ambulance.ambulance_type),
            traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
            road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
        )
        route.route_type = "REPOSITIONING"

        self.active_routes[amb_id] = route
        self.repositioning_data[amb_id] = {
            "is_repositioning": True,
            "reposition_target": (t_lat, t_lon),
            "reposition_origin_zone": origin_zone,
            "reposition_target_zone": target_zone,
            "reposition_started_sim_time": self.sim_time,
            "reason": reason,
        }

        # Update ambulance state
        ambulance.status = "REPOSITIONING"
        ambulance.is_repositioning = True
        ambulance.reposition_target = [t_lat, t_lon]
        ambulance.reposition_origin_zone = origin_zone
        ambulance.reposition_target_zone = target_zone
        ambulance.route_distance_km = route.route_distance_km
        ambulance.route_waypoints = [list(wp) for wp in route.waypoints]
        ambulance.eta_minutes = route.initial_eta_minutes
        ambulance.base_eta_minutes = route.initial_eta_minutes
        ambulance.routing_engine = route.routing_engine

        self.state.add_event(
            f"Ambulance {amb_id} started repositioning {origin_zone} -> {target_zone} (ETA: {route.initial_eta_minutes}m)."
        )

        # Asynchronous historical persistence
        self._record_persistence(
            "record_reposition_start",
            ambulance_id=amb_id,
            origin_zone=origin_zone,
            target_zone=target_zone,
            origin_lat=float(route.origin[0]),
            origin_lon=float(route.origin[1]),
            target_lat=t_lat,
            target_lon=t_lon,
            started_sim_time=self.sim_time,
            reason=reason,
        )

        return {
            "status": "REPOSITIONING",
            "ambulance_id": amb_id,
            "origin_zone": origin_zone,
            "target_zone": target_zone,
            "target_coords": [t_lat, t_lon],
            "route_distance_km": route.route_distance_km,
            "eta_minutes": route.initial_eta_minutes,
            "route_waypoints": [list(wp) for wp in route.waypoints],
        }

    def cancel_reposition(
        self,
        ambulance_id: str,
        reason: str = "CANCELLED_BY_OPERATOR",
    ) -> dict:
        """
        Cancel an active repositioning movement and return ambulance to AVAILABLE at current position.
        """
        amb_id = str(ambulance_id)
        ambulance = self.state.ambulances.get(amb_id)
        if not ambulance:
            raise KeyError(f"Ambulance '{amb_id}' not found.")

        if not (getattr(ambulance, "is_repositioning", False) or ambulance.status == "REPOSITIONING"):
            raise ValueError(f"Ambulance '{amb_id}' is not currently repositioning.")

        self.active_routes.pop(amb_id, None)
        self.repositioning_data.pop(amb_id, None)

        ambulance.status = "AVAILABLE"
        ambulance.is_repositioning = False
        ambulance.reposition_target = None
        ambulance.reposition_origin_zone = None
        ambulance.reposition_target_zone = None
        ambulance.route_distance_km = None
        ambulance.route_waypoints = []
        ambulance.eta_minutes = None
        ambulance.base_eta_minutes = None

        self.state.add_event(f"Ambulance {amb_id} repositioning cancelled: {reason}.")

        self._record_persistence(
            "record_reposition_complete",
            ambulance_id=amb_id,
            completed_sim_time=self.sim_time,
            final_status="CANCELLED",
        )

        return {
            "status": "AVAILABLE",
            "ambulance_id": amb_id,
            "message": f"Repositioning cancelled: {reason}.",
        }

    # ==========================================================
    # ADVANCE AMBULANCES (KINEMATICS & TRANSIT)
    # ==========================================================

    def advance_ambulances(
        self,
        delta_minutes: float = 1.0 / 60.0,
    ):
        """
        Advance vehicle positions along road waypoints and decrement ETAs.
        Runs every 1-second tick at high temporal resolution.
        """
        delta_minutes = max(0.0, float(delta_minutes))
        if delta_minutes <= 0.0:
            return

        for ambulance in list(self.state.ambulances.values()):

            if ambulance.status == "REPOSITIONING":
                # M9 Kinematics: Advance repositioning ambulance along route waypoints
                route = self.active_routes.get(ambulance.ambulance_id)
                if route is None and getattr(ambulance, "reposition_target", None):
                    target = ambulance.reposition_target
                    route = self.routing_engine.generate_route(
                        origin=(float(ambulance.latitude), float(ambulance.longitude)),
                        destination=(float(target[0]), float(target[1])),
                        vehicle_type=str(ambulance.ambulance_type),
                    )
                    route.total_duration_minutes = max(0.1, float(ambulance.eta_minutes or route.initial_eta_minutes))
                    route.elapsed_minutes = 0.0
                    self.active_routes[ambulance.ambulance_id] = route
                    ambulance.route_waypoints = [list(wp) for wp in route.waypoints]

                if route is not None:
                    route.elapsed_minutes += delta_minutes
                    new_lat, new_lon = self.routing_engine.interpolate_position(
                        route,
                        route.elapsed_minutes,
                    )
                    ambulance.latitude = new_lat
                    ambulance.longitude = new_lon

                    total_dur = max(0.001, route.total_duration_minutes)
                    progress_ratio = min(1.0, max(0.0, route.elapsed_minutes / total_dur))
                    if len(route.waypoints) > 1:
                        idx = min(len(route.waypoints) - 2, int(progress_ratio * (len(route.waypoints) - 1)))
                        ambulance.route_waypoints = [[new_lat, new_lon]] + [list(wp) for wp in route.waypoints[idx + 1:]]

                ambulance.eta_minutes = max(
                    0.0,
                    float(ambulance.eta_minutes or 0.0) - float(delta_minutes),
                )

                if ambulance.eta_minutes <= 1e-4 or (route and route.elapsed_minutes >= (route.total_duration_minutes - 1e-4)):
                    ambulance.eta_minutes = None
                    ambulance.base_eta_minutes = None
                    ambulance.status = "AVAILABLE"
                    ambulance.is_repositioning = False
                    if route is not None:
                        ambulance.latitude = float(route.destination[0])
                        ambulance.longitude = float(route.destination[1])
                    ambulance.route_waypoints = []
                    ambulance.route_distance_km = None
                    ambulance.reposition_target = None
                    ambulance.reposition_origin_zone = None
                    ambulance.reposition_target_zone = None
                    self.active_routes.pop(ambulance.ambulance_id, None)
                    rep_info = self.repositioning_data.pop(ambulance.ambulance_id, {})

                    self.state.add_event(
                        f"Ambulance {ambulance.ambulance_id} arrived at staging post ({rep_info.get('reposition_target_zone', 'target')})."
                    )

                    self._record_persistence(
                        "record_reposition_complete",
                        ambulance_id=ambulance.ambulance_id,
                        completed_sim_time=self.sim_time,
                        final_status="COMPLETED",
                    )
                continue

            if ambulance.status != "EN_ROUTE":
                continue

            # M8 Kinematics: Advance vehicle position along active route waypoints
            route = self.active_routes.get(ambulance.ambulance_id)
            if route is None and ambulance.hospital_id:
                target_hosp = self.state.hospitals.get(ambulance.hospital_id)
                if target_hosp:
                    route = self.routing_engine.generate_route(
                        origin=(float(ambulance.latitude), float(ambulance.longitude)),
                        destination=(float(target_hosp.latitude), float(target_hosp.longitude)),
                        vehicle_type=str(ambulance.ambulance_type),
                        traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
                        road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
                    )
                    route.total_duration_minutes = max(0.1, float(ambulance.eta_minutes or route.initial_eta_minutes))
                    route.elapsed_minutes = 0.0
                    self.active_routes[ambulance.ambulance_id] = route
                    ambulance.route_waypoints = [list(wp) for wp in route.waypoints]

            if route is not None:
                route.elapsed_minutes += delta_minutes
                new_lat, new_lon = self.routing_engine.interpolate_position(
                    route,
                    route.elapsed_minutes,
                )
                ambulance.latitude = new_lat
                ambulance.longitude = new_lon

                # Trim waypoints to only remaining segment ahead
                total_dur = max(0.001, route.total_duration_minutes)
                progress_ratio = min(1.0, max(0.0, route.elapsed_minutes / total_dur))
                if len(route.waypoints) > 1:
                    idx = min(len(route.waypoints) - 2, int(progress_ratio * (len(route.waypoints) - 1)))
                    ambulance.route_waypoints = [[new_lat, new_lon]] + [list(wp) for wp in route.waypoints[idx + 1:]]

            ambulance.eta_minutes = max(
                0.0,
                float(ambulance.eta_minutes or 0.0) - float(delta_minutes),
            )

            if ambulance.eta_minutes <= 1e-4 or (route and route.elapsed_minutes >= (route.total_duration_minutes - 1e-4)):

                ambulance.eta_minutes = 0.0

                ambulance.status = "ARRIVED"

                # Snap coordinates exactly to destination hospital
                target_hosp = self.state.hospitals.get(ambulance.hospital_id)
                if target_hosp is not None:
                    ambulance.latitude = float(target_hosp.latitude)
                    ambulance.longitude = float(target_hosp.longitude)

                ambulance.route_waypoints = []
                self.active_routes.pop(ambulance.ambulance_id, None)

                incident = (
                    self.state.incidents.get(
                        ambulance.incident_id
                    )
                )

                # Conversion of reservation into actual hospital load on arrival (M9 Phase 3)
                if ambulance.hospital_id:
                    self.coordinator.hospital_balancer.register_arrival(
                        ambulance.ambulance_id,
                        ambulance.hospital_id,
                    )
                    if target_hosp is not None:
                        target_hosp.current_load = min(target_hosp.capacity, target_hosp.current_load + 1)
                        if incident and str(getattr(incident, "severity", "")).strip().lower() == "critical":
                            target_hosp.current_icu_load = min(target_hosp.icu_capacity, target_hosp.current_icu_load + 1)

                if incident:

                    incident.status = "ARRIVED"

                self.state.add_event(
                    f"Ambulance "
                    f"{ambulance.ambulance_id} "
                    f"arrived at "
                    f"{ambulance.hospital_id}."
                )

                # Historical persistence hook
                if ambulance.incident_id is not None:
                    self._record_persistence(
                        "record_arrival",
                        incident_id=ambulance.incident_id,
                        ambulance_id=ambulance.ambulance_id,
                        hospital_id=ambulance.hospital_id,
                        arrived_sim_time=self.state.current_time,
                    )

                continue

            if ambulance.incident_id is not None:

                self.last_known_eta[
                    ambulance.incident_id
                ] = ambulance.eta_minutes

    # ==========================================================
    # ADVANCE SIMULATION CLOCK (MINUTES & COORDINATION)
    # ==========================================================

    def advance_simulation_clock(
        self,
        minutes: int = 1,
    ):
        """
        Advance the discrete integer simulation clock and run periodic background tasks.
        """
        minutes = max(0, int(minutes))
        if minutes <= 0:
            return

        self.state.advance_time(minutes)

        # Periodic coordination maintenance (every 5 simulation minutes)
        if self.sim_time - self._last_coordination_time >= 5:
            self._last_coordination_time = self.sim_time
            self.reposition_recommendations = self.coordinator.get_reposition_recommendations(self.state.ambulances)

        # Check active MCIs for progress and resolution (M9 Phase 4)
        if hasattr(self, "coordinator") and hasattr(self.coordinator, "mci_manager"):
            for mci in self.coordinator.mci_manager.list_active_mcis():
                evacuated, is_resolved = self.coordinator.mci_manager.check_mci_progress(
                    mci_id=mci.mci_id,
                    incidents=self.state.incidents,
                    sim_time=self.sim_time,
                )
                if is_resolved:
                    self._record_persistence(
                        "record_mci_resolved",
                        mci_id=mci.mci_id,
                        resolved_sim_time=self.sim_time,
                    )
                    self.state.add_event(
                        f"MCI {mci.mci_id} ({mci.name}) EVACUATION COMPLETE — RESOLVED."
                    )

    # ==========================================================
    # ADVANCE TIME (COMPOSITE STEP)
    # ==========================================================

    def advance_time(
        self,
        minutes=1,
    ):
        """
        Advance simulation by whole or fractional minutes.
        """
        delta = max(0.0, float(minutes))
        self.advance_ambulances(delta)

        self._sim_time_accumulator = getattr(self, "_sim_time_accumulator", 0.0) + delta
        if self._sim_time_accumulator >= 1.0:
            int_mins = int(self._sim_time_accumulator)
            self._sim_time_accumulator -= int_mins
            self.advance_simulation_clock(int_mins)
        self.tick_count = getattr(self, "tick_count", 0) + 1

    # ==========================================================
    # ETA RECHECK
    # ==========================================================

    def should_recheck_eta(
        self,
        incident,
    ):

        return (
            incident.incident_id
            in self.eta_recheck_required
        )

    # ==========================================================
    # CALCULATE ETA TO HOSPITAL
    # ==========================================================

    def calculate_hospital_eta(
        self,
        ambulance,
        hospital,
    ):

        try:

            return float(
                self.routing_engine.calculate_eta(
                    origin=(float(ambulance.latitude), float(ambulance.longitude)),
                    destination=(float(hospital.latitude), float(hospital.longitude)),
                    vehicle_type=str(ambulance.ambulance_type),
                    traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
                    road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
                )
            )

        except (
            AttributeError,
            TypeError,
            ValueError,
        ):

            try:

                return float(
                    ambulance.estimate_eta_to_hospital(
                        hospital
                    )
                )

            except (
                AttributeError,
                TypeError,
                ValueError,
            ):

                return None

    # ==========================================================
    # ETA-BASED REDIRECTION
    # ==========================================================

    def check_eta_redirection(
        self,
        incident,
    ):

        ambulance = (
            self.state.ambulances.get(
                incident.ambulance_id
            )
        )

        if ambulance is None:
            return False

        if ambulance.status != "EN_ROUTE":
            return False

        current_hospital = (
            self.state.hospitals.get(
                incident.hospital_id
            )
        )

        if current_hospital is None:
            return False

        current_eta = (
            ambulance.eta_minutes
        )

        if current_eta is None:
            return False

        candidates = []

        for hospital in (
            self.state.hospitals.values()
        ):

            if (
                hospital.hospital_id
                == current_hospital.hospital_id
            ):
                continue

            if hospital.available_beds <= 0:
                continue

            if (
                incident.severity == "Critical"
                and hospital.available_icu <= 0
            ):
                continue

            eta = self.calculate_hospital_eta(
                ambulance,
                hospital,
            )

            if eta is None:
                continue

            candidates.append(
                (
                    eta,
                    hospital,
                )
            )

        if not candidates:
            return False

        candidates.sort(
            key=lambda item: item[0]
        )

        best_eta, best_hospital = (
            candidates[0]
        )

        # ------------------------------------------------------
        # Never redirect to a slower hospital.
        # ------------------------------------------------------

        if best_eta >= current_eta:
            return False

        improvement = (
            current_eta - best_eta
        )

        improvement_percent = (
            improvement
            / max(current_eta, 1)
        ) * 100

        # ------------------------------------------------------
        # Require meaningful improvement.
        # ------------------------------------------------------

        if (
            improvement
            < self.MIN_REDIRECTION_IMPROVEMENT_MINUTES
            and
            improvement_percent
            < self.MIN_REDIRECTION_IMPROVEMENT_PERCENT
        ):
            return False

        old_hospital_id = str(
            incident.hospital_id
        )

        new_hospital_id = str(
            best_hospital.hospital_id
        )

        history = (
            self.redirect_history.setdefault(
                incident.incident_id,
                set(),
            )
        )

        if new_hospital_id in history:
            return False

        # ------------------------------------------------------
        # Record old destination.
        # ------------------------------------------------------

        history.add(
            old_hospital_id
        )

        # ------------------------------------------------------
        # Apply new destination.
        # ------------------------------------------------------

        incident.hospital_id = (
            new_hospital_id
        )

        incident.status = "REDIRECTED"

        ambulance.hospital_id = (
            new_hospital_id
        )

        ambulance.eta_minutes = (
            best_eta
        )

        # ------------------------------------------------------
        # Log.
        # ------------------------------------------------------

        self.logger.log_redirection(
            incident_id=incident.incident_id,

            current_time=self.state.current_time,

            reason=(
                "ETA deterioration caused by "
                f"{ambulance.traffic_level.lower()} "
                "traffic."
            ),

            original_hospital=(
                old_hospital_id
            ),

            new_hospital=(
                new_hospital_id
            ),

            eta_before=current_eta,

            eta_after=best_eta,

            severity=incident.severity,

            ambulance_id=ambulance.ambulance_id,
        )

        self.state.add_event(
            f"Incident "
            f"{incident.incident_id} "
            f"redirected from "
            f"{old_hospital_id} to "
            f"{new_hospital_id}. "
            f"ETA improved from "
            f"{current_eta:.1f} to "
            f"{best_eta:.1f} min."
        )

        # Historical persistence hook
        self._record_persistence(
            "record_redirection",
            incident_id=incident.incident_id,
            ambulance_id=ambulance.ambulance_id,
            decision_type="REDIRECTED",
            trigger_type="AI_AUTONOMOUS",
            original_hospital_id=old_hospital_id,
            new_hospital_id=new_hospital_id,
            eta_before=current_eta,
            eta_after=best_eta,
            eta_saved=round(improvement, 2),
            eta_improvement_pct=round(improvement_percent, 2),
            reason=f"ETA deterioration caused by {ambulance.traffic_level.lower()} traffic",
            sim_time=self.state.current_time,
        )

        return True

    # ==========================================================
    # HOSPITAL-FAILURE REDIRECTION
    # ==========================================================

    def check_hospital_redirection(
        self,
        incident,
    ):

        if incident.hospital_id is None:
            return False

        if incident.ambulance_id is None:
            return False

        ambulance = (
            self.state.ambulances.get(
                incident.ambulance_id
            )
        )

        if ambulance is None:
            return False

        if ambulance.status != "EN_ROUTE":
            return False

        current_hospital_id = str(
            incident.hospital_id
        )

        result = check_live_redirection(
            self.state,
            incident.incident_id,
        )

        if not isinstance(result, dict):
            return False

        if not result.get(
            "redirect",
            False,
        ):
            return False

        alternative = result.get(
            "alternative_hospital"
        )

        if not isinstance(
            alternative,
            dict,
        ):
            return False

        # ------------------------------------------------------
        # Accept both key styles.
        # ------------------------------------------------------

        new_hospital_id = (
            alternative.get(
                "Hospital_ID"
            )
        )

        if new_hospital_id is None:

            new_hospital_id = (
                alternative.get(
                    "hospital_id"
                )
            )

        if new_hospital_id is None:
            return False

        new_hospital_id = str(
            new_hospital_id
        )

        if (
            new_hospital_id
            == current_hospital_id
        ):
            return False

        history = (
            self.redirect_history.setdefault(
                incident.incident_id,
                set(),
            )
        )

        if new_hospital_id in history:
            return False

        new_hospital = (
            self.state.hospitals.get(
                new_hospital_id
            )
        )

        if new_hospital is None:
            return False

        # ------------------------------------------------------
        # Current remaining ETA.
        # ------------------------------------------------------

        eta_before = (
            ambulance.eta_minutes
        )

        # ------------------------------------------------------
        # Calculate ETA from CURRENT ambulance position.
        # ------------------------------------------------------

        eta_after = (
            self.calculate_hospital_eta(
                ambulance,
                new_hospital,
            )
        )

        if eta_after is None:

            # We can redirect because the hospital failed,
            # but we must not invent an ETA improvement.
            eta_after = eta_before

        # ------------------------------------------------------
        # Record old destination.
        # ------------------------------------------------------

        history.add(
            current_hospital_id
        )

        # ------------------------------------------------------
        # Apply redirection.
        # ------------------------------------------------------

        incident.hospital_id = (
            new_hospital_id
        )

        incident.status = "REDIRECTED"

        ambulance.hospital_id = (
            new_hospital_id
        )

        # Atomic reservation transfer (M9 Phase 3)
        self.coordinator.hospital_balancer.update_redirection(
            ambulance_id=ambulance.ambulance_id,
            old_hospital_id=current_hospital_id,
            new_hospital_id=new_hospital_id,
            severity=str(incident.severity),
            new_eta_minutes=float(eta_after),
            sim_time=self.sim_time,
        )

        # M8 Kinematics: Generate new route from current position to new hospital
        new_route = self.routing_engine.generate_route(
            origin=(float(ambulance.latitude), float(ambulance.longitude)),
            destination=(float(new_hospital.latitude), float(new_hospital.longitude)),
            vehicle_type=str(ambulance.ambulance_type),
            traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
            road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
        )
        self.active_routes[ambulance.ambulance_id] = new_route
        ambulance.route_distance_km = new_route.route_distance_km
        ambulance.route_waypoints = [list(wp) for wp in new_route.waypoints]
        ambulance.routing_engine = new_route.routing_engine
        ambulance.base_eta_minutes = new_route.initial_eta_minutes

        if eta_after is not None:

            ambulance.eta_minutes = (
                float(eta_after)
            )

        # ------------------------------------------------------
        # Reason.
        # ------------------------------------------------------

        reason = str(
            result.get(
                "reason",
                "Current hospital is no longer suitable.",
            )
        ).rstrip(".")

        # ------------------------------------------------------
        # Log decision.
        # ------------------------------------------------------

        self.logger.log_redirection(
            incident_id=incident.incident_id,

            current_time=self.state.current_time,

            reason=reason,

            original_hospital=(
                current_hospital_id
            ),

            new_hospital=(
                new_hospital_id
            ),

            eta_before=eta_before,

            eta_after=eta_after,

            severity=incident.severity,

            ambulance_id=ambulance.ambulance_id,
        )

        if (
            eta_before is not None
            and eta_after is not None
        ):

            eta_change = (
                float(eta_before)
                - float(eta_after)
            )

            if eta_change > 0:

                eta_message = (
                    f" ETA improved from "
                    f"{eta_before:.1f} to "
                    f"{eta_after:.1f} min."
                )

            elif eta_change < 0:

                eta_message = (
                    f" ETA changed from "
                    f"{eta_before:.1f} to "
                    f"{eta_after:.1f} min."
                )

            else:

                eta_message = (
                    f" ETA remains "
                    f"{eta_after:.1f} min."
                )

        else:

            eta_message = ""

        self.state.add_event(
            f"Incident "
            f"{incident.incident_id} "
            f"redirected from "
            f"{current_hospital_id} to "
            f"{new_hospital_id}. "
            f"Reason: {reason}."
            f"{eta_message}"
        )

        # Historical persistence hook
        self._record_persistence(
            "record_redirection",
            incident_id=incident.incident_id,
            ambulance_id=ambulance.ambulance_id,
            decision_type="REDIRECTED",
            trigger_type="AI_AUTONOMOUS",
            original_hospital_id=current_hospital_id,
            new_hospital_id=new_hospital_id,
            eta_before=eta_before,
            eta_after=eta_after,
            eta_saved=round(float(eta_before) - float(eta_after), 2) if eta_before and eta_after else 0.0,
            eta_improvement_pct=0.0,
            reason=reason,
            sim_time=self.state.current_time,
        )

        return True

    # ==========================================================
    # MANUAL OPERATOR REDIRECTION
    # ==========================================================

    def apply_manual_redirection(
        self,
        incident_id,
        target_hospital_id=None,
        reason="Operator manual override",
    ):
        """
        Manually redirect an en-route ambulance to a new hospital.

        Validates that the incident exists and is currently EN_ROUTE.
        If target_hospital_id is provided, validates that hospital exists and has capacity.
        If target_hospital_id is omitted, automatically finds the best alternative using check_live_redirection.
        Mutates incident, ambulance, and hospital loads, logs the decision tagged with [OPERATOR],
        and emits a simulation event.
        """
        incident_id = int(incident_id)
        incident = self.state.incidents.get(incident_id)
        if incident is None:
            raise ValueError(f"Incident {incident_id} not found in state.")

        if not incident.ambulance_id:
            raise ValueError(f"Incident {incident_id} has no assigned ambulance.")

        ambulance = self.state.ambulances.get(str(incident.ambulance_id))
        if ambulance is None:
            raise ValueError(f"Ambulance {incident.ambulance_id} not found.")

        if ambulance.status != "EN_ROUTE":
            raise ValueError(
                f"Cannot redirect incident {incident_id}: ambulance status is {ambulance.status} (must be EN_ROUTE)."
            )

        current_hospital_id = str(incident.hospital_id) if incident.hospital_id else None
        current_hospital = self.state.hospitals.get(current_hospital_id) if current_hospital_id else None

        # Determine target hospital
        if target_hospital_id:
            new_hospital_id = str(target_hospital_id)
            new_hospital = self.state.hospitals.get(new_hospital_id)
            if new_hospital is None:
                raise ValueError(f"Target hospital {new_hospital_id} not found.")
            if new_hospital.is_full or new_hospital.available_beds <= 0:
                raise ValueError(f"Target hospital {new_hospital_id} is full or has no available beds.")
            if incident.severity == "Critical" and new_hospital.available_icu <= 0:
                raise ValueError(f"Target hospital {new_hospital_id} has no available ICU beds for Critical incident.")
            if new_hospital_id == current_hospital_id:
                raise ValueError(f"Ambulance is already en route to hospital {new_hospital_id}.")
        else:
            # Dynamic alternative selection via redirection engine
            eval_result = check_live_redirection(self.state, incident_id)
            alt = eval_result.get("alternative_hospital")
            if not alt:
                history_excluded = self.redirect_history.get(incident_id, set())
                best_alt = find_best_alternative(self.state, incident, exclude_hospital_ids=history_excluded)
                if best_alt:
                    h = best_alt["hospital"]
                    alt = {
                        "hospital_id": h.hospital_id,
                        "Hospital_ID": h.hospital_id,
                        "hospital_type": h.hospital_type,
                        "available_beds": h.available_beds,
                        "available_icu": h.available_icu,
                        "score": best_alt["score"],
                        "eta": best_alt["eta"],
                    }
            if not alt:
                raise ValueError("No suitable alternative hospital available for redirection.")
            new_hospital_id = str(alt.get("Hospital_ID") or alt.get("hospital_id"))
            new_hospital = self.state.hospitals.get(new_hospital_id)
            if new_hospital is None:
                raise ValueError(f"Alternative hospital {new_hospital_id} not found in state.")
            if new_hospital_id == current_hospital_id:
                raise ValueError(f"Alternative hospital is identical to current destination ({new_hospital_id}).")

        # Compute ETAs
        eta_before = ambulance.eta_minutes
        eta_after = self.calculate_hospital_eta(ambulance, new_hospital)
        if eta_after is None:
            eta_after = eta_before

        eta_saved = round(float(eta_before) - float(eta_after), 2)
        eta_improvement_pct = round((eta_saved / float(eta_before) * 100.0), 2) if (eta_before and float(eta_before) > 0) else 0.0

        # Atomic reservation transfer (M9 Phase 3)
        self.coordinator.hospital_balancer.update_redirection(
            ambulance_id=ambulance.ambulance_id,
            old_hospital_id=current_hospital_id,
            new_hospital_id=new_hospital_id,
            severity=str(incident.severity),
            new_eta_minutes=float(eta_after),
            sim_time=self.sim_time,
        )

        # Mutate Incident & Ambulance
        history = self.redirect_history.setdefault(incident_id, set())
        if current_hospital_id:
            history.add(current_hospital_id)

        incident.hospital_id = new_hospital_id
        incident.status = "REDIRECTED"
        ambulance.hospital_id = new_hospital_id
        ambulance.eta_minutes = float(eta_after)

        # M8 Kinematics: Generate new route from current position to new hospital
        new_route = self.routing_engine.generate_route(
            origin=(float(ambulance.latitude), float(ambulance.longitude)),
            destination=(float(new_hospital.latitude), float(new_hospital.longitude)),
            vehicle_type=str(ambulance.ambulance_type),
            traffic_level=str(getattr(ambulance, "traffic_level", "NORMAL")),
            road_condition=str(getattr(ambulance, "road_condition", "GOOD")),
        )
        self.active_routes[ambulance.ambulance_id] = new_route
        ambulance.route_distance_km = new_route.route_distance_km
        ambulance.route_waypoints = [list(wp) for wp in new_route.waypoints]
        ambulance.routing_engine = new_route.routing_engine
        ambulance.base_eta_minutes = new_route.initial_eta_minutes

        operator_reason = f"[OPERATOR] {reason}" if not str(reason).startswith("[OPERATOR]") else str(reason)

        # Log Decision
        decision_record = self.logger.log_redirection(
            incident_id=incident_id,
            current_time=self.state.current_time,
            reason=operator_reason,
            original_hospital=current_hospital_id,
            new_hospital=new_hospital_id,
            eta_before=eta_before,
            eta_after=eta_after,
            severity=incident.severity,
            ambulance_id=ambulance.ambulance_id,
        )

        self.state.add_event(
            f"Incident {incident_id} MANUALLY REDIRECTED: {current_hospital_id} -> {new_hospital_id} "
            f"(ETA: {eta_before}m -> {eta_after}m, Saved: {eta_saved}m)."
        )

        # Historical persistence hook
        self._record_persistence(
            "record_redirection",
            incident_id=incident_id,
            ambulance_id=ambulance.ambulance_id,
            decision_type="REDIRECTED",
            trigger_type="OPERATOR_MANUAL",
            original_hospital_id=current_hospital_id,
            new_hospital_id=new_hospital_id,
            eta_before=eta_before,
            eta_after=eta_after,
            eta_saved=eta_saved,
            eta_improvement_pct=eta_improvement_pct,
            reason=operator_reason,
            sim_time=self.state.current_time,
        )

        return decision_record

    # ==========================================================
    # REDIRECTION PIPELINE
    # ==========================================================

    def check_redirections(self):

        for incident in list(
            self.state.get_active_incidents()
        ):

            if incident.hospital_id is None:
                continue

            if incident.ambulance_id is None:
                continue

            ambulance = (
                self.state.ambulances.get(
                    incident.ambulance_id
                )
            )

            if ambulance is None:
                continue

            if ambulance.status != "EN_ROUTE":
                continue

            # --------------------------------------------------
            # ETA deterioration gets priority.
            # --------------------------------------------------

            if self.should_recheck_eta(
                incident
            ):

                redirected = (
                    self.check_eta_redirection(
                        incident
                    )
                )

                self.eta_recheck_required.discard(
                    incident.incident_id
                )

                if redirected:
                    continue

            # --------------------------------------------------
            # Hospital failure/capacity.
            # --------------------------------------------------

            self.check_hospital_redirection(
                incident
            )

    # ==========================================================
    # PROCESS EVENTS
    # ==========================================================

    def process_events(self):

        return self.events.process(
            self.state.current_time
        )

    # ==========================================================
    # OUTPUT
    # ==========================================================

    def get_snapshot(self):

        return self.output.snapshot(
            self.state
        )

    def get_dashboard_snapshot(self):

        return self.output.dashboard_snapshot(
            self.state
        )

    def get_json(self):

        return self.output.to_json(
            self.state
        )

    # ==========================================================
    # DASHBOARD
    # ==========================================================

    def print_dashboard(self):

        print()
        print("=" * 70)
        print(
            "EMERGENCY DISPATCH COMMAND CENTER"
        )
        print("=" * 70)

        print(
            f"TIME: "
            f"{self.state.current_time} min"
        )

        # ------------------------------------------------------
        # ACTIVE INCIDENTS
        # ------------------------------------------------------

        print()
        print("ACTIVE INCIDENTS")
        print("-" * 70)

        print(
            f"{'ID':<7}"
            f"{'PRI':<6}"
            f"{'SEVERITY':<15}"
            f"{'STATUS':<13}"
            f"{'AMBULANCE':<13}"
            f"HOSPITAL"
        )

        for incident in (
            self.state.get_active_incidents()
        ):

            print(
                f"#{incident.incident_id:<6}"
                f"P{incident.priority:<5}"
                f"{incident.severity:<15}"
                f"{incident.status:<13}"
                f"{str(incident.ambulance_id or '-'): <13}"
                f"{incident.hospital_id or '-'}"
            )

        # ------------------------------------------------------
        # FLEET
        # ------------------------------------------------------

        print()
        print("FLEET")
        print("-" * 70)

        counts = {}

        for ambulance in (
            self.state.ambulances.values()
        ):

            status = str(
                ambulance.status
            ).upper()

            counts[status] = (
                counts.get(status, 0) + 1
            )

        print(
            f"Available:       "
            f"{counts.get('AVAILABLE', 0)}"
        )

        print(
            f"En Route:        "
            f"{counts.get('EN_ROUTE', 0)}"
        )

        print(
            f"Busy:             "
            f"{counts.get('BUSY', 0)}"
        )

        print(
            f"Maintenance:      "
            f"{counts.get('MAINTENANCE', 0)}"
        )

        print(
            f"Arrived:          "
            f"{counts.get('ARRIVED', 0)}"
        )

        # ------------------------------------------------------
        # CURRENT INCIDENTS
        # ------------------------------------------------------

        print()
        print("CURRENT INCIDENTS")
        print("-" * 70)

        for incident in (
            self.state.get_active_incidents()
        ):

            ambulance = (
                self.state.ambulances.get(
                    incident.ambulance_id
                )
            )

            if (
                ambulance
                and ambulance.eta_minutes
                is not None
            ):

                eta = (
                    f"{ambulance.eta_minutes:.1f}"
                    f" min"
                )

            else:

                eta = "-"

            print(
                f"#{incident.incident_id} "
                f"P{incident.priority} "
                f"{incident.severity} | "
                f"AMB "
                f"{incident.ambulance_id or '-'} | "
                f"ETA {eta} | "
                f"HOSP "
                f"{incident.hospital_id or '-'}"
            )

        # ------------------------------------------------------
        # EVENTS
        # ------------------------------------------------------

        print()
        print("LATEST EVENTS")
        print("-" * 70)

        for event in self.state.events[-8:]:

            print(
                f"[{event['time']:>3} min] "
                f"{event['message']}"
            )

        print("=" * 70)

    # ==========================================================
    # RUN
    # ==========================================================

    def run(
        self,
        incident_ids,
        duration=15,
    ):

        print("=" * 70)
        print(
            "STARTING DISPATCH SIMULATION"
        )
        print("=" * 70)

        # ------------------------------------------------------
        # Initial dispatch
        # ------------------------------------------------------

        for incident_id in incident_ids:

            try:

                self.create_incident(
                    incident_id
                )

            except Exception as error:

                self.state.add_event(
                    f"Failed to dispatch "
                    f"incident {incident_id}: "
                    f"{error}"
                )

        # ------------------------------------------------------
        # Schedule events
        # ------------------------------------------------------

        self.schedule_default_events()

        # ------------------------------------------------------
        # Initial dashboard
        # ------------------------------------------------------

        self.print_dashboard()

        # ------------------------------------------------------
        # Simulation
        # ------------------------------------------------------

        for _ in range(
            duration
        ):

            self.advance_time(
                1
            )

            self.process_events()

            self.check_redirections()

            if (
                self.state.current_time % 5 == 0
                or
                self.state.current_time == duration
            ):

                self.print_dashboard()

        # ------------------------------------------------------
        # Decision summary
        # ------------------------------------------------------

        self.logger.print_summary()

        return self.get_snapshot()


# ==============================================================
# MAIN
# ==============================================================

if __name__ == "__main__":

    simulator = Simulator()

    simulator.run(
        incident_ids=[
            1,
            2,
            3,
            4,
            5,
        ],
        duration=15,
    )