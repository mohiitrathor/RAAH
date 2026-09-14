"""
RAAH Production Configuration & Settings Layer
==============================================

Centralizes all environment-driven configuration for RAAH using Pydantic Settings.
Supports overrides via environment variables prefixed with `RAAH_` (or direct variables)
and optional `.env` files, providing safe production and development defaults.

Machine-specific paths are strictly avoided; paths are dynamically anchored to the
repository root directory.
"""

from pathlib import Path
from typing import List, Optional, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Dynamic repository root anchor
_REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """
    Core application settings with environment variable override support.
    """

    model_config = SettingsConfigDict(
        env_prefix="RAAH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ==============================================================
    # APPLICATION METADATA & SERVER
    # ==============================================================
    app_name: str = Field(
        default="RAAH — Emergency Dispatch & Coordination Platform",
        description="Public title of the FastAPI application",
    )
    app_version: str = Field(
        default="1.0.0",
        description="Application version string",
    )
    environment: str = Field(
        default="development",
        description="Deployment environment: development | staging | production | testing",
    )
    debug: bool = Field(
        default=False,
        description="Debug mode toggle (enables verbose errors in dev)",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Server bind host address",
    )
    port: int = Field(
        default=8000,
        description="Server bind port number",
    )

    # ==============================================================
    # PATH CONFIGURATION (Dynamic, not machine-specific)
    # ==============================================================
    root_dir: Path = Field(
        default=_REPO_ROOT,
        description="Repository root directory",
    )
    dispatch_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "Dispatch",
        description="Dispatch core Python modules directory",
    )
    dataset_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "Dataset",
        description="Authoritative baseline dataset CSV directory",
    )
    data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data",
        description="Persistent data storage directory (SQLite, JSON stores)",
    )
    frontend_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "frontend",
        description="Web dashboard and operator console static files",
    )
    database_path: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "raah_history.db",
        description="Path to SQLite historical analytics and state persistence database",
    )

    # State Persistence, Recovery & Durability
    persistence_backend: str = Field(
        default="sqlite",
        description="Authoritative state persistence backend: sqlite | memory",
    )
    persistence_enabled: bool = Field(
        default=True,
        description="Whether authoritative state persistence and checkpointing are enabled",
    )
    checkpoint_interval_seconds: float = Field(
        default=30.0,
        ge=0.0,
        description="Periodic state checkpoint interval in seconds (0 = manual only)",
    )
    persistence_queue_capacity: int = Field(
        default=10000,
        ge=100,
        description="Maximum capacity of asynchronous persistence telemetry queue",
    )
    auto_recovery_enabled: bool = Field(
        default=True,
        description="Whether to automatically restore latest valid state checkpoint on startup",
    )
    recovery_fallback_to_clean: bool = Field(
        default=True,
        description="Whether to fall back to clean state if latest checkpoint is corrupt or incompatible",
    )

    # ==============================================================
    # EXTERNAL ADAPTERS & INGESTION
    # ==============================================================
    cad_provider: str = Field(
        default="mock",
        description="Active CAD ingestion provider: mock | webhook | vendor",
    )
    hospital_provider: str = Field(
        default="mock",
        description="Active hospital status provider: mock | fhir | vendor",
    )
    traffic_provider: str = Field(
        default="mock",
        description="Active traffic feed provider: mock | here | tomtom | vendor",
    )
    gps_provider: str = Field(
        default="mock",
        description="Active ambulance GPS provider: mock | avl | vendor",
    )
    idempotency_ttl_hours: int = Field(
        default=24,
        ge=1,
        description="Retention TTL for event deduplication / idempotency records in hours",
    )
    max_event_age_seconds: int = Field(
        default=3600,
        ge=1,
        description="Maximum allowed event age before classifying as STALE (seconds)",
    )
    ingestion_burst_limit: int = Field(
        default=1000,
        ge=10,
        description="Maximum concurrent ingestion burst capacity",
    )

    # Optimization and Scenario data paths
    optimization_data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "optimization",
        description="Optimization audit, policy versions, and learning outcomes directory",
    )
    scenarios_data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "scenarios",
        description="Deterministic operational scenarios directory",
    )
    drills_data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "drills",
        description="Disaster drill and stress testing results directory",
    )
    replays_data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "replays",
        description="Serialized operational replay artifacts directory",
    )
    regression_data_dir: Path = Field(
        default_factory=lambda: _REPO_ROOT / "data" / "regression",
        description="Regression test runs and baseline comparisons directory",
    )

    # ==============================================================
    # LOGGING & OBSERVABILITY
    # ==============================================================
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )
    log_format: str = Field(
        default="json",
        description="Log output format: json | text",
    )
    log_requests: bool = Field(
        default=True,
        description="Whether to log all incoming HTTP requests with latency & correlation IDs",
    )

    # ==============================================================
    # SECURITY, AUTHENTICATION & CORS
    # ==============================================================
    jwt_secret_key: str = Field(
        default="raah-emergency-dispatch-jwt-signing-secret-key-at-least-32-bytes-long",
        description="Cryptographic HMAC-SHA256 secret key for signing JWT tokens (min 32 bytes)",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="Cryptographic algorithm for JWT token signing",
    )
    jwt_issuer: Optional[str] = Field(
        default="raah.ems.internal",
        description="Issuer identifier (iss claim) for JWT tokens",
    )
    jwt_expiration_minutes: int = Field(
        default=60,
        description="Access token lifespan in minutes",
    )
    dev_auth_fallback: bool = Field(
        default=True,
        description="Unsafe development-only fallback when no Authorization header is provided. Strictly disabled in production.",
    )
    auth_enforced: bool = Field(
        default=True,
        description="Whether authentication checks are actively enforced on protected endpoints",
    )

    cors_origins: List[str] = Field(
        default=["*"],
        description="Allowed CORS origin list. Wildcard is never combined with credentials in production.",
    )
    cors_allow_credentials: bool = Field(
        default=True,
        description="Allow credentials in CORS preflight",
    )
    cors_allow_methods: List[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        description="Allowed HTTP methods",
    )
    cors_allow_headers: List[str] = Field(
        default=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
        description="Allowed HTTP headers",
    )

    @property
    def effective_cors_origins(self) -> List[str]:
        """
        Return the secure effective CORS origins list.
        If wildcard '*' is present with allow_credentials=True, sanitizes by restricting
        to trusted local origins to prevent insecure CORS reflection vulnerabilities.
        """
        if "*" in self.cors_origins and self.cors_allow_credentials:
            return ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"]
        return self.cors_origins

    # ==============================================================
    # SIMULATION & KINEMATICS ENGINE
    # ==============================================================
    simulation_tick_interval_seconds: float = Field(
        default=1.0,
        description="Wall-clock sleep duration between background simulation ticks",
    )
    simulation_minutes_per_tick: int = Field(
        default=1,
        description="Simulation minutes advanced per tick",
    )
    consecutive_error_threshold: int = Field(
        default=3,
        description="Consecutive background thread exceptions before setting ERRORED status",
    )
    request_timeout_seconds: float = Field(
        default=30.0,
        description="Standard request/background worker timeout in seconds",
    )

    # ==============================================================
    # OPTIMIZATION & POLICY CONFIGURABLE THRESHOLDS
    # ==============================================================
    default_min_confidence_reposition: float = Field(
        default=0.95,
        description="Default confidence threshold for automated fleet repositioning",
    )
    default_min_confidence_diversion: float = Field(
        default=0.95,
        description="Default confidence threshold for hospital diversion",
    )
    fleet_safety_floor: int = Field(
        default=2,
        description="Hard minimum number of ambulances retained per zone",
    )
    max_autonomous_actions_per_window: int = Field(
        default=5,
        description="Rate limit on autonomous optimization actions per window",
    )
    window_size_ticks: int = Field(
        default=15,
        description="Window size in simulation ticks for action rate limiting",
    )
    zone_cooldown_ticks: int = Field(
        default=3,
        description="Cooldown ticks before a zone can be re-targeted for auto-reposition",
    )
    min_action_interval_seconds: float = Field(
        default=15.0,
        description="Minimum seconds required between successive autonomous actions",
    )

    # ==============================================================
    # VALIDATORS
    # ==============================================================
    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("simulation_tick_interval_seconds")
    @classmethod
    def validate_tick_interval(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"simulation_tick_interval_seconds must be positive, got {v}")
        return v

    @field_validator("fleet_safety_floor")
    @classmethod
    def validate_safety_floor(cls, v: int) -> int:
        if v < 2:
            raise ValueError(f"fleet_safety_floor cannot be set below the hard safety floor of 2, got {v}")
        return v

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, v: str, info) -> str:
        if len(v.encode()) < 32:
            raise ValueError(f"jwt_secret_key must be at least 32 bytes (256 bits), got {len(v.encode())} bytes")
        env = info.data.get("environment", "development")
        default_sec = "raah-insecure-dev-signing-key-for-local-testing-only-change-in-production"
        if env == "production" and v == default_sec:
            raise ValueError("Production environment cannot use default insecure JWT secret key!")
        return v

    @field_validator("dev_auth_fallback")
    @classmethod
    def validate_dev_auth_fallback(cls, v: bool, info) -> bool:
        env = info.data.get("environment", "development")
        if env == "production" and v:
            raise ValueError("dev_auth_fallback cannot be enabled in production environment!")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, v: List[str], info) -> List[str]:
        env = info.data.get("environment", "development")
        allow_cred = info.data.get("cors_allow_credentials", True)
        if env == "production" and "*" in v and allow_cred:
            raise ValueError("Insecure CORS: Wildcard origin '*' combined with credentials is prohibited in production!")
        return v

    def validate_production_settings(self) -> List[str]:
        """
        Comprehensive production readiness audit for configuration.
        Returns a list of violation messages if any are found.
        """
        violations = []
        if self.environment == "production":
            default_sec = "raah-insecure-dev-signing-key-for-local-testing-only-change-in-production"
            if self.jwt_secret_key == default_sec:
                violations.append("Production environment cannot use default insecure JWT secret key.")
            if not self.auth_enforced:
                violations.append("auth_enforced must be True in production.")
            if self.dev_auth_fallback:
                violations.append("dev_auth_fallback must be False in production.")
            if self.cors_allow_credentials and "*" in self.cors_origins:
                violations.append("Wildcard origin '*' with credentials allowed is prohibited in production.")
            if self.checkpoint_interval_seconds <= 0:
                violations.append("checkpoint_interval_seconds must be positive in production.")
            if self.persistence_queue_capacity < 100:
                violations.append("persistence_queue_capacity must be at least 100 in production.")
        return violations


# Global singleton settings instance
settings = Settings()
