"""
RAAH Machine-to-Machine (M2M) Security & Webhook Gateway
========================================================

Provides secure, production-grade API key authentication and provider-scoped
authorization for external EMS integration adapters (CAD, AVL/GPS, Hospital, Traffic).

Key Architecture:
- Stored credentials contain ONLY cryptographic hashes and salts (never plaintext secrets).
- Constant-time secret verification prevents timing side-channel attacks.
- Provider-scoped authorization ensures a CAD credential cannot ingest GPS or Hospital telemetry.
- Seamless co-existence with existing internal operator JWT / RBAC tokens.
- Zero leakage of credential material in logs, error details, or API responses.
- Observability integration tracking authentication attempts, failures, and scope violations.
"""

import hmac
import hashlib
import secrets
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import Request, HTTPException, Depends, Header, status

from api.settings import settings
from api.auth.models import Role, Permission, AuthenticatedUser, PERMISSION_ROLES
from api.auth.security import decode_access_token, AuthenticationError
from api.adapters.models import EventType
from api.observability.metrics import metrics_collector

logger = logging.getLogger("raah.adapters.m2m")


# ======================================================================
# DATA MODELS
# ======================================================================

class M2MCredentialRecord(BaseModel):
    """
    Internal storage representation of an M2M API credential.
    Stores cryptographic hash and salt only — NEVER raw plaintext secrets.
    """
    key_id: str = Field(..., description="Unique public identifier for this credential")
    name: str = Field(..., description="Human-readable description of provider or integration")
    provider_id: str = Field(..., description="Logical provider ID (e.g. CAD_911, AVL_FLEET_ALPHA)")
    allowed_event_types: List[str] = Field(..., description="List of EventType string values authorized for this key")
    key_hash: str = Field(..., description="SHA-256 hash of (salt + raw_secret)")
    salt: str = Field(..., description="Cryptographic salt used for hashing")
    prefix: str = Field(..., description="First 8 characters of key for indexing/identification")
    is_active: bool = Field(default=True, description="Whether this credential is currently enabled")
    is_test_credential: bool = Field(default=False, description="Flag indicating mock/test credential")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def verify_secret(self, raw_secret: str) -> bool:
        """Constant-time verification of the presented raw secret."""
        candidate_hash = hashlib.sha256(f"{self.salt}:{raw_secret}".encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.key_hash, candidate_hash)

    def allows_event_type(self, event_type: EventType | str) -> bool:
        """Check whether this credential is authorized for the given event type."""
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        return ("*" in self.allowed_event_types) or (et in self.allowed_event_types)


class M2MCredentialSummary(BaseModel):
    """
    Safe public representation of an M2M credential.
    Excludes hash, salt, and raw secrets.
    """
    key_id: str
    name: str
    provider_id: str
    allowed_event_types: List[str]
    prefix: str
    is_active: bool
    is_test_credential: bool
    created_at: str


class IngestionIdentity(BaseModel):
    """
    Unified authenticated identity representing either an external M2M service
    or an internal human operator.
    """
    auth_type: str = Field(..., description="'M2M' for external API key, 'OPERATOR' for human JWT")
    identifier: str = Field(..., description="key_id for M2M, or username for operator")
    provider_id: Optional[str] = Field(default=None, description="External provider ID if M2M")
    allowed_event_types: List[str] = Field(default_factory=list)
    operator_user: Optional[AuthenticatedUser] = None
    m2m_client: Optional[M2MCredentialSummary] = None

    @property
    def is_m2m(self) -> bool:
        return self.auth_type == "M2M"

    @property
    def attribution_name(self) -> str:
        """Deterministic attribution string for decision logs and event metadata."""
        if self.is_m2m:
            return f"m2m:{self.provider_id or self.identifier}"
        return self.identifier

    def allows_event_type(self, event_type: EventType | str) -> bool:
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        if self.is_m2m:
            return ("*" in self.allowed_event_types) or (et in self.allowed_event_types)
        return True


# ======================================================================
# CREDENTIAL STORE
# ======================================================================

class M2MCredentialStore:
    """
    In-memory, thread-safe registry of external machine credentials.
    Enforces secure salted hashing, constant-time verification, and provider scoping.
    """

    def __init__(self):
        self._credentials: Dict[str, M2MCredentialRecord] = {}
        # Pre-seed controlled development/mock credentials if not running in production
        if settings.environment != "production":
            self._load_default_test_credentials()

    def _hash_key(self, salt: str, secret: str) -> str:
        return hashlib.sha256(f"{salt}:{secret}".encode("utf-8")).hexdigest()

    def register_credential(
        self,
        key_id: str,
        raw_secret: str,
        name: str,
        provider_id: str,
        allowed_event_types: List[str],
        is_active: bool = True,
        is_test_credential: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> M2MCredentialSummary:
        """
        Store a new external credential.
        Computes cryptographic salt and hash; the raw secret is never stored.
        """
        salt = secrets.token_hex(16)
        key_hash = self._hash_key(salt, raw_secret)
        prefix = raw_secret[:8] if len(raw_secret) >= 8 else raw_secret

        record = M2MCredentialRecord(
            key_id=key_id,
            name=name,
            provider_id=provider_id,
            allowed_event_types=list(allowed_event_types),
            key_hash=key_hash,
            salt=salt,
            prefix=prefix,
            is_active=is_active,
            is_test_credential=is_test_credential,
            metadata=metadata or {},
        )
        self._credentials[key_id] = record

        logger.info(
            f"M2M credential registered: key_id='{key_id}' provider='{provider_id}' "
            f"scopes={allowed_event_types} test={is_test_credential}"
        )

        return self.to_summary(record)

    def generate_credential(
        self,
        key_id: str,
        name: str,
        provider_id: str,
        allowed_event_types: List[str],
        is_test_credential: bool = False,
        prefix_str: str = "raah_m2m_",
    ) -> tuple[str, M2MCredentialSummary]:
        """
        Generate a cryptographically secure random API key and register its hash.
        Returns (raw_key, summary). The raw_key is only available at this point.
        """
        random_part = secrets.token_urlsafe(32)
        raw_key = f"{prefix_str}{random_part}"
        summary = self.register_credential(
            key_id=key_id,
            raw_secret=raw_key,
            name=name,
            provider_id=provider_id,
            allowed_event_types=allowed_event_types,
            is_test_credential=is_test_credential,
        )
        return raw_key, summary

    def verify_key(self, candidate_key: str) -> Optional[M2MCredentialRecord]:
        """
        Validate a candidate raw key against all active records in constant-time.
        Never logs or returns candidate_key.
        """
        if not candidate_key or not isinstance(candidate_key, str) or len(candidate_key) < 10:
            return None

        for record in self._credentials.values():
            if not record.is_active:
                continue
            if record.verify_secret(candidate_key):
                return record

        return None

    def get_credential(self, key_id: str) -> Optional[M2MCredentialRecord]:
        return self._credentials.get(key_id)

    def revoke_credential(self, key_id: str) -> bool:
        record = self._credentials.get(key_id)
        if record:
            record.is_active = False
            logger.info(f"M2M credential revoked: key_id='{key_id}' provider='{record.provider_id}'")
            return True
        return False

    def list_credentials(self) -> List[M2MCredentialSummary]:
        return [self.to_summary(r) for r in self._credentials.values()]

    def to_summary(self, record: M2MCredentialRecord) -> M2MCredentialSummary:
        return M2MCredentialSummary(
            key_id=record.key_id,
            name=record.name,
            provider_id=record.provider_id,
            allowed_event_types=record.allowed_event_types,
            prefix=record.prefix,
            is_active=record.is_active,
            is_test_credential=record.is_test_credential,
            created_at=record.created_at,
        )

    def _load_default_test_credentials(self):
        """
        Controlled mock/test keys for automated integration testing and local simulation.
        Explicitly flagged as test credentials.
        """
        self.register_credential(
            key_id="test_cad_key",
            raw_secret=TEST_M2M_CAD_KEY,
            name="Mock CAD System Alpha",
            provider_id="CAD_MOCK",
            allowed_event_types=[EventType.INCIDENT_CALL.value],
            is_test_credential=True,
        )
        self.register_credential(
            key_id="test_gps_key",
            raw_secret=TEST_M2M_GPS_KEY,
            name="Mock AVL GPS Fleet Beta",
            provider_id="GPS_MOCK",
            allowed_event_types=[EventType.AMBULANCE_GPS.value],
            is_test_credential=True,
        )
        self.register_credential(
            key_id="test_hospital_key",
            raw_secret=TEST_M2M_HOSPITAL_KEY,
            name="Mock Regional Hospital Network Gamma",
            provider_id="HOSP_MOCK",
            allowed_event_types=[EventType.HOSPITAL_STATUS.value],
            is_test_credential=True,
        )
        self.register_credential(
            key_id="test_traffic_key",
            raw_secret=TEST_M2M_TRAFFIC_KEY,
            name="Mock City Traffic Authority Delta",
            provider_id="TRAFFIC_MOCK",
            allowed_event_types=[EventType.TRAFFIC_UPDATE.value],
            is_test_credential=True,
        )
        self.register_credential(
            key_id="test_omni_key",
            raw_secret=TEST_M2M_OMNI_KEY,
            name="Mock Multi-Feed Integration Partner",
            provider_id="OMNI_MOCK",
            allowed_event_types=[
                EventType.INCIDENT_CALL.value,
                EventType.AMBULANCE_GPS.value,
                EventType.HOSPITAL_STATUS.value,
                EventType.TRAFFIC_UPDATE.value,
            ],
            is_test_credential=True,
        )

    def reset_test_credentials(self):
        """Reset test credentials to default known state."""
        self._credentials.clear()
        if settings.environment != "production":
            self._load_default_test_credentials()


# ======================================================================
# CONSTANTS & SINGLETON
# ======================================================================

# Explicit, auditable test keys for development/testing only
TEST_M2M_CAD_KEY = "raah_m2m_test_cad_secret_key_alpha_99"
TEST_M2M_GPS_KEY = "raah_m2m_test_gps_secret_key_beta_88"
TEST_M2M_HOSPITAL_KEY = "raah_m2m_test_hosp_secret_key_gamma_77"
TEST_M2M_TRAFFIC_KEY = "raah_m2m_test_traffic_secret_key_delta_66"
TEST_M2M_OMNI_KEY = "raah_m2m_test_omni_secret_key_omega_00"

# Global singleton
m2m_store = M2MCredentialStore()


# ======================================================================
# FASTAPI DEPENDENCY: DUAL M2M / OPERATOR AUTHENTICATION
# ======================================================================

def require_ingestion_auth(
    required_event_type: EventType,
    fallback_permission: Permission,
) -> Callable:
    """
    Unified authentication and authorization dependency for external ingestion endpoints.

    Authentication Precedence:
    1. X-API-Key header (Machine-to-Machine external provider credential).
    2. Authorization header:
       a) If starts with 'ApiKey ' or 'Bearer raah_m2m_', processed as M2M API key.
       b) If starts with 'Bearer ' (JWT token), decoded and validated as internal operator JWT.
    3. If no credential provided:
       a) If settings.dev_auth_fallback is active in non-production, fallback to dev operator.
       b) Otherwise, raise HTTP 401 Unauthorized.

    Authorization Scoping:
    - M2M API key: Checked against `allowed_event_types` for `required_event_type`.
      Raises HTTP 403 Forbidden on scope mismatch.
    - Operator JWT: Checked against RBAC permission matrix for `fallback_permission`.
      Raises HTTP 403 Forbidden on insufficient operator privileges.
    """

    async def ingestion_authenticator(
        request: Request,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> IngestionIdentity:

        candidate_key: Optional[str] = None

        # 1. Inspect headers for candidate API key
        if x_api_key and x_api_key.strip():
            candidate_key = x_api_key.strip()
        elif authorization:
            auth_str = authorization.strip()
            if auth_str.lower().startswith("apikey "):
                candidate_key = auth_str[7:].strip()
            elif auth_str.startswith("Bearer raah_m2m_"):
                candidate_key = auth_str[7:].strip()

        # 2. Process M2M candidate key if present
        if candidate_key:
            record = m2m_store.verify_key(candidate_key)
            if not record:
                metrics_collector.record_m2m_auth_failure()
                logger.warning("M2M authentication rejected: invalid or unknown API key presented")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized: Invalid, expired, or unrecognized M2M API key.",
                    headers={"WWW-Authenticate": "ApiKey"},
                )

            # Check provider scope authorization
            if not record.allows_event_type(required_event_type):
                metrics_collector.record_m2m_scope_mismatch(record.provider_id)
                logger.warning(
                    f"M2M scope violation: key_id='{record.key_id}' provider='{record.provider_id}' "
                    f"attempted unauthorized event='{required_event_type.value}'. Allowed: {record.allowed_event_types}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Forbidden: M2M credential '{record.key_id}' for provider '{record.provider_id}' "
                        f"is scoped to {record.allowed_event_types}, which does not permit '{required_event_type.value}'."
                    ),
                )

            # M2M Authentication and scope authorization succeeded
            metrics_collector.record_m2m_auth_success(record.provider_id)
            summary = m2m_store.to_summary(record)
            return IngestionIdentity(
                auth_type="M2M",
                identifier=record.key_id,
                provider_id=record.provider_id,
                allowed_event_types=record.allowed_event_types,
                m2m_client=summary,
            )

        # 3. Process Operator JWT Bearer Token if present
        if authorization and authorization.strip().startswith("Bearer "):
            token = authorization.strip()[7:].strip()
            try:
                user = decode_access_token(token)
            except AuthenticationError as ex:
                raise ex
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized: Invalid or malformed bearer token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Enforce operator RBAC permission
            if not user.has_permission(fallback_permission):
                allowed_roles = [r.value for r in PERMISSION_ROLES.get(fallback_permission, set())]
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Forbidden: Permission '{fallback_permission.value}' requires one of {allowed_roles} (current: '{user.role.value}').",
                )

            return IngestionIdentity(
                auth_type="OPERATOR",
                identifier=user.username,
                operator_user=user,
                allowed_event_types=[required_event_type.value],
            )

        # 4. No credentials provided
        if settings.auth_enforced:
            if settings.environment != "production" and settings.dev_auth_fallback:
                dev_user = AuthenticatedUser(
                    username="dev_operator",
                    role=Role.ADMINISTRATOR,
                    email="dev_operator@raah.internal",
                )
                return IngestionIdentity(
                    auth_type="OPERATOR",
                    identifier="dev_operator",
                    operator_user=dev_user,
                    allowed_event_types=[required_event_type.value],
                )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required: Provide a valid 'X-API-Key' or 'Authorization: Bearer <token>'.",
                headers={"WWW-Authenticate": "ApiKey, Bearer"},
            )

        # In case auth is globally disabled
        dev_user = AuthenticatedUser(
            username="anonymous",
            role=Role.ADMINISTRATOR,
        )
        return IngestionIdentity(
            auth_type="OPERATOR",
            identifier="anonymous",
            operator_user=dev_user,
            allowed_event_types=[required_event_type.value],
        )

    return ingestion_authenticator
