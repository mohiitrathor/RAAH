from dataclasses import asdict, is_dataclass
from typing import Any
import json


class SimulationOutput:

    # ==========================================================
    # GENERIC SERIALIZATION
    # ==========================================================

    @staticmethod
    def serialize(value: Any):
        """
        Convert dataclasses, dictionaries, lists and basic Python
        values into JSON-safe structures.
        """

        if is_dataclass(value):
            return {
                key: SimulationOutput.serialize(val)
                for key, val in asdict(value).items()
            }

        if isinstance(value, dict):
            return {
                str(key): SimulationOutput.serialize(val)
                for key, val in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [
                SimulationOutput.serialize(item)
                for item in value
            ]

        if isinstance(value, float):
            if value != value:  # NaN
                return None

            if value == float("inf"):
                return None

            if value == float("-inf"):
                return None

            return round(value, 3)

        if isinstance(value, (str, int, bool)) or value is None:
            return value

        return str(value)

    # ==========================================================
    # INCIDENT
    # ==========================================================

    @staticmethod
    def incident(incident):
        """
        Convert an IncidentState into frontend/API-friendly data.
        """

        return {
            "incident_id": int(
                incident.incident_id
            ),

            "condition": str(
                incident.condition
            ),

            "severity": str(
                incident.severity
            ),

            "priority": int(
                incident.priority
            ),

            "status": str(
                incident.status
            ),

            "ambulance_id": (
                str(incident.ambulance_id)
                if incident.ambulance_id is not None
                else None
            ),

            "hospital_id": (
                str(incident.hospital_id)
                if incident.hospital_id is not None
                else None
            ),
        }

    # ==========================================================
    # AMBULANCE
    # ==========================================================

    @staticmethod
    def ambulance(ambulance):
        """
        Convert an AmbulanceState into frontend/API-friendly data.
        """

        return {
            "ambulance_id": str(
                ambulance.ambulance_id
            ),

            "ambulance_type": str(
                ambulance.ambulance_type
            ),

            "latitude": round(
                float(ambulance.latitude),
                6,
            ),

            "longitude": round(
                float(ambulance.longitude),
                6,
            ),

            "status": str(
                ambulance.status
            ),

            "incident_id": (
                int(ambulance.incident_id)
                if ambulance.incident_id is not None
                else None
            ),

            "hospital_id": (
                str(ambulance.hospital_id)
                if ambulance.hospital_id is not None
                else None
            ),

            "eta_minutes": (
                round(
                    float(ambulance.eta_minutes),
                    2,
                )
                if ambulance.eta_minutes is not None
                else None
            ),

            "base_eta_minutes": (
                round(
                    float(
                        ambulance.base_eta_minutes
                    ),
                    2,
                )
                if ambulance.base_eta_minutes is not None
                else None
            ),

            "traffic_level": str(
                getattr(
                    ambulance,
                    "traffic_level",
                    "NORMAL",
                )
            ),

            "road_condition": str(
                getattr(
                    ambulance,
                    "road_condition",
                    "GOOD",
                )
            ),

            "route_distance_km": (
                round(float(ambulance.route_distance_km), 3)
                if getattr(ambulance, "route_distance_km", None) is not None
                else None
            ),

            "route_waypoints": getattr(ambulance, "route_waypoints", None),

            "routing_engine": getattr(ambulance, "routing_engine", None),

            "is_repositioning": bool(getattr(ambulance, "is_repositioning", False)),

            "reposition_target": getattr(ambulance, "reposition_target", None),

            "reposition_origin_zone": getattr(ambulance, "reposition_origin_zone", None),

            "reposition_target_zone": getattr(ambulance, "reposition_target_zone", None),
        }

    # ==========================================================
    # HOSPITAL
    # ==========================================================

    @staticmethod
    def hospital(hospital):
        """
        Convert a HospitalState into frontend/API-friendly data.
        """

        return {
            "hospital_id": str(
                hospital.hospital_id
            ),

            "hospital_type": str(
                hospital.hospital_type
            ),

            "latitude": round(
                float(hospital.latitude),
                6,
            ),

            "longitude": round(
                float(hospital.longitude),
                6,
            ),

            "capacity": int(
                hospital.capacity
            ),

            "current_load": int(
                hospital.current_load
            ),

            "available_beds": int(
                hospital.available_beds
            ),

            "icu_capacity": int(
                hospital.icu_capacity
            ),

            "current_icu_load": int(
                hospital.current_icu_load
            ),

            "available_icu": int(
                hospital.available_icu
            ),

            "is_full": bool(
                hospital.is_full
            ),

            "icu_available": bool(
                hospital.icu_available
            ),
        }

    # ==========================================================
    # FLEET SUMMARY
    # ==========================================================

    @staticmethod
    def fleet_summary(ambulances):
        """
        Produce aggregate fleet statistics.
        """

        counts = {
            "AVAILABLE": 0,
            "EN_ROUTE": 0,
            "BUSY": 0,
            "MAINTENANCE": 0,
            "ARRIVED": 0,
        }

        for ambulance in ambulances:

            status = str(
                ambulance.status
            ).upper()

            counts[status] = (
                counts.get(status, 0) + 1
            )

        total = len(ambulances)

        return {
            "total": total,
            "available": counts.get(
                "AVAILABLE",
                0,
            ),
            "en_route": counts.get(
                "EN_ROUTE",
                0,
            ),
            "busy": counts.get(
                "BUSY",
                0,
            ),
            "maintenance": counts.get(
                "MAINTENANCE",
                0,
            ),
            "arrived": counts.get(
                "ARRIVED",
                0,
            ),
        }

    # ==========================================================
    # EVENTS
    # ==========================================================

    @staticmethod
    def events(state):
        """
        Convert simulator events into clean API data.
        """

        return [
            {
                "time": round(
                    float(
                        event.get(
                            "time",
                            0,
                        )
                    ),
                    2,
                ),

                "message": str(
                    event.get(
                        "message",
                        "",
                    )
                ),
            }
            for event in state.events
        ]

    # ==========================================================
    # COMPLETE SNAPSHOT
    # ==========================================================

    @staticmethod
    def snapshot(state):
        """
        Create a complete point-in-time representation of the
        dispatch system.

        This is the primary method the backend/frontend should use.
        """

        incidents = [
            SimulationOutput.incident(
                incident
            )
            for incident
            in state.incidents.values()
        ]

        ambulances = [
            SimulationOutput.ambulance(
                ambulance
            )
            for ambulance
            in state.ambulances.values()
        ]

        hospitals = [
            SimulationOutput.hospital(
                hospital
            )
            for hospital
            in state.hospitals.values()
        ]

        fleet = SimulationOutput.fleet_summary(
            state.ambulances.values()
        )

        return {
            "time": int(state.current_time),

            "incidents": incidents,

            "ambulances": ambulances,

            "hospitals": hospitals,

            "fleet": fleet,

            "events": SimulationOutput.events(
                state
            ),
        }

    # ==========================================================
    # DASHBOARD SNAPSHOT
    # ==========================================================

    @staticmethod
    def dashboard_snapshot(state):
        """
        Smaller snapshot intended for frequent frontend updates.
        """

        active_incidents = []

        for incident in (
            state.get_active_incidents()
        ):

            ambulance = (
                state.ambulances.get(
                    incident.ambulance_id
                )
            )

            active_incidents.append({
                "incident_id": int(
                    incident.incident_id
                ),

                "priority": int(
                    incident.priority
                ),

                "severity": str(
                    incident.severity
                ),

                "status": str(
                    incident.status
                ),

                "ambulance_id": (
                    incident.ambulance_id
                ),

                "hospital_id": (
                    incident.hospital_id
                ),

                "eta_minutes": (
                    round(
                        float(
                            ambulance.eta_minutes
                        ),
                        2,
                    )
                    if (
                        ambulance
                        and ambulance.eta_minutes
                        is not None
                    )
                    else None
                ),
            })

        return {
            "time": int(state.current_time),

            "active_incidents": (
                active_incidents
            ),

            "fleet": (
                SimulationOutput.fleet_summary(
                    state.ambulances.values()
                )
            ),

            "events": (
                SimulationOutput.events(
                    state
                )[-10:]
            ),
        }

    # ==========================================================
    # JSON
    # ==========================================================

    @staticmethod
    def to_json(state):
        """
        Return a JSON-serializable snapshot.

        The backend can pass this directly to json.dumps().
        """

        return SimulationOutput.snapshot(
            state
        )


# ==============================================================
# TEST
# ==============================================================

if __name__ == "__main__":

    from state import (
        DispatchState,
        IncidentState,
        AmbulanceState,
        HospitalState,
    )

    state = DispatchState()

    state.add_incident(
        IncidentState(
            incident_id=1,
            condition="Trauma",
            severity="Critical",
            priority=1,
            status="DISPATCHED",
            ambulance_id="AMB_TEST",
            hospital_id="HOSP_TEST",
        )
    )

    state.add_ambulance(
        AmbulanceState(
            ambulance_id="AMB_TEST",
            ambulance_type="ALS",
            latitude=26.9124,
            longitude=75.7873,
            status="EN_ROUTE",
            incident_id=1,
            hospital_id="HOSP_TEST",
            eta_minutes=12.5,
            base_eta_minutes=10.0,
            traffic_level="MODERATE",
            road_condition="GOOD",
        )
    )

    state.add_hospital(
        HospitalState(
            hospital_id="HOSP_TEST",
            hospital_type="Trauma Center",
            latitude=26.9200,
            longitude=75.8000,
            capacity=300,
            current_load=240,
            icu_capacity=50,
            current_icu_load=35,
        )
    )

    state.add_event(
        "Test simulation snapshot created."
    )

    output = SimulationOutput.snapshot(
        state
    )

    print("=" * 70)
    print(
        "SIMULATION OUTPUT TEST"
    )
    print("=" * 70)

    print(
        json.dumps(
            output,
            indent=4,
        )
    )

    print("=" * 70)