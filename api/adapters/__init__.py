"""
RAAH External Telemetry & CAD Adapters Package
==============================================
"""

from api.adapters.models import (
    NormalizedEvent,
    IngestionResponse,
    EventStatus,
    EventType,
    CADIncidentInput,
    AmbulanceGPSInput,
    HospitalStatusInput,
    TrafficUpdateInput,
)
from api.adapters.interfaces import (
    IncidentSource,
    LocationSource,
    HospitalStatusSource,
    TrafficSource,
)
from api.adapters.mock_providers import (
    MockCADProvider,
    MockGPSProvider,
    MockHospitalProvider,
    MockTrafficProvider,
)
from api.adapters.registry import (
    AdapterRegistry,
    adapter_registry,
)
from api.adapters.service import (
    IngestionService,
    ingestion_service,
)
from api.adapters.m2m import (
    M2MCredentialRecord,
    M2MCredentialSummary,
    IngestionIdentity,
    M2MCredentialStore,
    m2m_store,
    require_ingestion_auth,
    TEST_M2M_CAD_KEY,
    TEST_M2M_GPS_KEY,
    TEST_M2M_HOSPITAL_KEY,
    TEST_M2M_TRAFFIC_KEY,
    TEST_M2M_OMNI_KEY,
)

__all__ = [
    "NormalizedEvent",
    "IngestionResponse",
    "EventStatus",
    "EventType",
    "CADIncidentInput",
    "AmbulanceGPSInput",
    "HospitalStatusInput",
    "TrafficUpdateInput",
    "IncidentSource",
    "LocationSource",
    "HospitalStatusSource",
    "TrafficSource",
    "MockCADProvider",
    "MockGPSProvider",
    "MockHospitalProvider",
    "MockTrafficProvider",
    "AdapterRegistry",
    "adapter_registry",
    "IngestionService",
    "ingestion_service",
    "M2MCredentialRecord",
    "M2MCredentialSummary",
    "IngestionIdentity",
    "M2MCredentialStore",
    "m2m_store",
    "require_ingestion_auth",
    "TEST_M2M_CAD_KEY",
    "TEST_M2M_GPS_KEY",
    "TEST_M2M_HOSPITAL_KEY",
    "TEST_M2M_TRAFFIC_KEY",
    "TEST_M2M_OMNI_KEY",
]
