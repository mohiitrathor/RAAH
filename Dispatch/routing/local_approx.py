"""
RAAH Local Approximate Router
=============================

100% offline, self-contained deterministic routing implementation.
Features:
  - Exact spherical Haversine distance.
  - Urban circuity factor (default 1.35) accounting for non-straight road networks.
  - Centralized ambulance speeds and traffic/road condition multipliers.
  - Deterministic multi-point waypoint generation for visualization.
  - Piecewise linear kinematic position interpolation along route waypoints.
"""

from math import radians, sin, cos, sqrt, atan2, pi
from typing import Tuple, List, Dict
import math

from .engine import RouterBase, RouteGeometry


class LocalApproxRouter(RouterBase):
    """
    Deterministic offline approximate router for urban EMS response.
    """

    CIRCUITY_FACTOR: float = 1.35
    EARTH_RADIUS_KM: float = 6371.0

    AMBULANCE_SPEEDS: Dict[str, float] = {
        "BLS": 45.0,
        "ALS": 50.0,
        "ICU": 50.0,
        "TRAUMA": 55.0,
        "DEFAULT": 50.0,
    }

    TRAFFIC_MULTIPLIERS: Dict[str, float] = {
        "LIGHT": 1.00,
        "NORMAL": 1.00,
        "MODERATE": 1.15,
        "HEAVY": 1.30,
        "SEVERE": 1.50,
    }

    ROAD_MULTIPLIERS: Dict[str, float] = {
        "GOOD": 1.00,
        "AVERAGE": 1.10,
        "POOR": 1.25,
    }

    def __init__(self, circuity_factor: float = CIRCUITY_FACTOR):
        self.circuity_factor = circuity_factor

    # ----------------------------------------------------------
    # DISTANCE CALCULATIONS
    # ----------------------------------------------------------

    def calculate_straight_line_distance(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> float:
        """Calculate spherical Haversine distance in km."""
        lat1, lon1 = radians(float(origin[0])), radians(float(origin[1]))
        lat2, lon2 = radians(float(destination[0])), radians(float(destination[1]))

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = sin(dlat / 2.0) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0) ** 2
        a = min(1.0, max(0.0, a))
        c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a))

        return round(self.EARTH_RADIUS_KM * c, 3)

    def calculate_distance(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
    ) -> float:
        """Calculate road network distance using the urban circuity factor."""
        straight_km = self.calculate_straight_line_distance(origin, destination)
        return round(straight_km * self.circuity_factor, 3)

    # ----------------------------------------------------------
    # ETA CALCULATION
    # ----------------------------------------------------------

    def calculate_eta(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        vehicle_type: str = "DEFAULT",
        traffic_level: str = "NORMAL",
        road_condition: str = "GOOD",
    ) -> float:
        """Compute expected travel time in minutes."""
        route_dist_km = self.calculate_distance(origin, destination)

        speed_kmh = self.AMBULANCE_SPEEDS.get(
            str(vehicle_type).upper(),
            self.AMBULANCE_SPEEDS["DEFAULT"],
        )
        if speed_kmh <= 0:
            speed_kmh = 50.0

        base_eta = (route_dist_km / speed_kmh) * 60.0

        t_mult = self.TRAFFIC_MULTIPLIERS.get(
            str(traffic_level).upper(),
            1.00,
        )
        r_mult = self.ROAD_MULTIPLIERS.get(
            str(road_condition).upper(),
            1.00,
        )

        eta = base_eta * t_mult * r_mult
        return round(max(0.1, eta), 2)

    # ----------------------------------------------------------
    # ROUTE & WAYPOINT GENERATION
    # ----------------------------------------------------------

    def generate_route(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        vehicle_type: str = "DEFAULT",
        traffic_level: str = "NORMAL",
        road_condition: str = "GOOD",
    ) -> RouteGeometry:
        """
        Generate a deterministic multi-point route between origin and destination.
        Waypoints follow an organic, natural road corridor shape.
        """
        straight_dist = self.calculate_straight_line_distance(origin, destination)
        route_dist = round(straight_dist * self.circuity_factor, 3)
        speed_kmh = self.AMBULANCE_SPEEDS.get(
            str(vehicle_type).upper(),
            self.AMBULANCE_SPEEDS["DEFAULT"],
        )
        if speed_kmh <= 0:
            speed_kmh = 50.0
        base_eta = (route_dist / speed_kmh) * 60.0
        t_mult = self.TRAFFIC_MULTIPLIERS.get(str(traffic_level).upper(), 1.0)
        r_mult = self.ROAD_MULTIPLIERS.get(str(road_condition).upper(), 1.0)
        eta_mins = round(max(0.1, base_eta * t_mult * r_mult), 2)

        lat1, lon1 = float(origin[0]), float(origin[1])
        lat2, lon2 = float(destination[0]), float(destination[1])

        # Number of segments proportional to distance (minimum 6, maximum 20)
        num_segments = max(6, min(20, int(straight_dist * 2) + 4))

        waypoints: List[Tuple[float, float]] = []
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        # Orthogonal direction for organic corridor curve
        # (normalized perpendicular vector)
        ortho_lat = -dlon
        ortho_lon = dlat * cos(radians((lat1 + lat2) / 2.0))
        ortho_norm = sqrt(ortho_lat ** 2 + ortho_lon ** 2) if (ortho_lat ** 2 + ortho_lon ** 2) > 0 else 1.0
        ortho_lat /= ortho_norm
        ortho_lon /= ortho_norm

        # Offset magnitude (approx 5-10% of total span, clamped)
        max_offset = min(0.005, sqrt(dlat ** 2 + dlon ** 2) * 0.12)

        for i in range(num_segments + 1):
            t = i / float(num_segments)

            # Base linear interpolation
            curr_lat = lat1 + t * dlat
            curr_lon = lon1 + t * dlon

            # Deterministic lateral wave (0 at start and end)
            # Uses sin(pi*t) so endpoints match exact origin and destination
            lateral_offset = sin(pi * t) * max_offset * sin(2.0 * pi * t + 0.5)

            final_lat = round(curr_lat + lateral_offset * ortho_lat, 6)
            final_lon = round(curr_lon + lateral_offset * ortho_lon, 6)

            # Guarantee exact start and end coordinates
            if i == 0:
                final_lat, final_lon = lat1, lon1
            elif i == num_segments:
                final_lat, final_lon = lat2, lon2

            waypoints.append((final_lat, final_lon))

        return RouteGeometry(
            origin=(lat1, lon1),
            destination=(lat2, lon2),
            straight_line_distance_km=straight_dist,
            route_distance_km=route_dist,
            initial_eta_minutes=eta_mins,
            waypoints=waypoints,
            routing_engine="LOCAL_APPROX",
            total_duration_minutes=eta_mins,
            elapsed_minutes=0.0,
        )

    # ----------------------------------------------------------
    # KINEMATIC POSITION INTERPOLATION
    # ----------------------------------------------------------

    def interpolate_position(
        self,
        route: RouteGeometry,
        elapsed_minutes: float,
    ) -> Tuple[float, float]:
        """
        Compute the live vehicle position along the route waypoints
        based on elapsed simulation time.
        """
        if not route.waypoints or len(route.waypoints) == 1:
            return route.destination

        total_time = max(0.001, route.total_duration_minutes)
        progress_ratio = min(1.0, max(0.0, elapsed_minutes / total_time))

        if progress_ratio >= 1.0:
            return route.destination

        # Calculate cumulative distances between waypoints
        segment_lengths: List[float] = []
        total_geom_dist = 0.0

        for i in range(len(route.waypoints) - 1):
            p1 = route.waypoints[i]
            p2 = route.waypoints[i + 1]
            seg_dist = sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)
            segment_lengths.append(seg_dist)
            total_geom_dist += seg_dist

        if total_geom_dist <= 0:
            return route.destination

        # Target cumulative distance along the geometry
        target_dist = progress_ratio * total_geom_dist
        accum_dist = 0.0

        for i, seg_len in enumerate(segment_lengths):
            if accum_dist + seg_len >= target_dist:
                seg_progress = (target_dist - accum_dist) / max(seg_len, 1e-9)
                p1 = route.waypoints[i]
                p2 = route.waypoints[i + 1]
                curr_lat = p1[0] + seg_progress * (p2[0] - p1[0])
                curr_lon = p1[1] + seg_progress * (p2[1] - p1[1])
                return (round(curr_lat, 6), round(curr_lon, 6))
            accum_dist += seg_len

        return route.destination
