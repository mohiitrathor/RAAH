"""
RAAH Mock External Integration Providers
========================================

Deterministic mock implementations of IncidentSource, LocationSource,
HospitalStatusSource, and TrafficSource for testing and development.
"""

import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from api.adapters.interfaces import (
    IncidentSource,
    LocationSource,
    HospitalStatusSource,
    TrafficSource,
)
from api.adapters.models import NormalizedEvent, EventType


class MockCADProvider(IncidentSource):
    """Deterministic Mock CAD / 911 feed provider."""

    def __init__(self, provider_id: str = "CAD_MOCK"):
        self.provider_id = provider_id
        self.is_healthy: bool = True
        self.simulate_timeout: bool = False
        self.pending_queue: List[NormalizedEvent] = []
        self.acknowledged_ids: List[str] = []

    def queue_incident(self, event: NormalizedEvent):
        self.pending_queue.append(event)

    def fetch_pending_incidents(self) -> List[NormalizedEvent]:
        if self.simulate_timeout:
            time.sleep(0.05)
            raise TimeoutError("CAD provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("CAD provider is unreachable.")
        events = list(self.pending_queue)
        self.pending_queue.clear()
        return events

    def acknowledge_incident(self, source_event_id: str) -> bool:
        self.acknowledged_ids.append(source_event_id)
        return True

    def health_check(self) -> Dict[str, Any]:
        if self.simulate_timeout:
            raise TimeoutError("CAD provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("CAD provider is unreachable.")
        return {
            "provider_id": self.provider_id,
            "type": "CAD",
            "healthy": self.is_healthy,
            "pending_count": len(self.pending_queue),
            "acknowledged_count": len(self.acknowledged_ids),
        }


class MockGPSProvider(LocationSource):
    """Deterministic Mock AVL / GPS vehicle location provider."""

    def __init__(self, provider_id: str = "GPS_MOCK"):
        self.provider_id = provider_id
        self.is_healthy: bool = True
        self.simulate_timeout: bool = False
        self.pending_queue: List[NormalizedEvent] = []

    def queue_location(self, event: NormalizedEvent):
        self.pending_queue.append(event)

    def fetch_locations(self) -> List[NormalizedEvent]:
        if self.simulate_timeout:
            raise TimeoutError("GPS provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("GPS provider is unreachable.")
        events = list(self.pending_queue)
        self.pending_queue.clear()
        return events

    def health_check(self) -> Dict[str, Any]:
        if self.simulate_timeout:
            raise TimeoutError("GPS provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("GPS provider is unreachable.")
        return {
            "provider_id": self.provider_id,
            "type": "GPS",
            "healthy": self.is_healthy,
            "buffered_fixes": len(self.pending_queue),
        }


class MockHospitalProvider(HospitalStatusSource):
    """Deterministic Mock Hospital status and bed capacity provider."""

    def __init__(self, provider_id: str = "HOSP_MOCK"):
        self.provider_id = provider_id
        self.is_healthy: bool = True
        self.simulate_timeout: bool = False
        self.pending_queue: List[NormalizedEvent] = []

    def queue_status(self, event: NormalizedEvent):
        self.pending_queue.append(event)

    def fetch_hospital_statuses(self) -> List[NormalizedEvent]:
        if self.simulate_timeout:
            raise TimeoutError("Hospital status provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("Hospital status provider is unreachable.")
        events = list(self.pending_queue)
        self.pending_queue.clear()
        return events

    def health_check(self) -> Dict[str, Any]:
        if self.simulate_timeout:
            raise TimeoutError("Hospital status provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("Hospital status provider is unreachable.")
        return {
            "provider_id": self.provider_id,
            "type": "HOSPITAL",
            "healthy": self.is_healthy,
            "status_records": len(self.pending_queue),
        }


class MockTrafficProvider(TrafficSource):
    """Deterministic Mock Traffic and road condition provider."""

    def __init__(self, provider_id: str = "TRAFFIC_MOCK"):
        self.provider_id = provider_id
        self.is_healthy: bool = True
        self.simulate_timeout: bool = False
        self.pending_queue: List[NormalizedEvent] = []

    def queue_traffic(self, event: NormalizedEvent):
        self.pending_queue.append(event)

    def fetch_traffic_updates(self) -> List[NormalizedEvent]:
        if self.simulate_timeout:
            raise TimeoutError("Traffic provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("Traffic provider is unreachable.")
        events = list(self.pending_queue)
        self.pending_queue.clear()
        return events

    def health_check(self) -> Dict[str, Any]:
        if self.simulate_timeout:
            raise TimeoutError("Traffic provider connection timed out.")
        if not self.is_healthy:
            raise ConnectionError("Traffic provider is unreachable.")
        return {
            "provider_id": self.provider_id,
            "type": "TRAFFIC",
            "healthy": self.is_healthy,
            "advisories_count": len(self.pending_queue),
        }
