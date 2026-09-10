"""
RAAH State Persistence & Durability Package
===========================================
"""

from api.persistence.interface import (
    StatePersistenceStore,
    CheckpointRecord,
    IdempotencyRecord,
    PersistenceError,
    CorruptCheckpointError,
    IncompatibleSchemaError,
    CorruptStateError,
    DatabaseUnavailableError,
    DatabaseLockedError,
)
from api.persistence.serializer import (
    SCHEMA_VERSION,
    serialize_dispatch_state,
    deserialize_dispatch_state,
    compute_state_checksum,
    validate_state_payload,
)
from api.persistence.sqlite_store import SQLiteStateStore
from api.persistence.recovery import StateRecoveryEngine, RecoveryStatus
from api.persistence.bridge import persistence_bridge
from api.persistence.replay_compiler import (
    OperationalReplayCompiler,
    compile_operational_run_to_artifact,
    resolve_replay_artifact,
    list_operational_runs_metadata,
    is_operational_replay_id,
    clear_compiled_artifacts_cache,
    ReplayCompilationError,
    RunNotFoundError,
    NoCheckpointsError,
    CorruptCheckpointError as ReplayCorruptCheckpointError,
    MalformedPersistenceError,
    EmptyRunError,
)

__all__ = [
    "StatePersistenceStore",
    "CheckpointRecord",
    "IdempotencyRecord",
    "SQLiteStateStore",
    "StateRecoveryEngine",
    "RecoveryStatus",
    "SCHEMA_VERSION",
    "serialize_dispatch_state",
    "deserialize_dispatch_state",
    "compute_state_checksum",
    "validate_state_payload",
    "persistence_bridge",
    "PersistenceError",
    "CorruptCheckpointError",
    "IncompatibleSchemaError",
    "CorruptStateError",
    "DatabaseUnavailableError",
    "DatabaseLockedError",
    "OperationalReplayCompiler",
    "compile_operational_run_to_artifact",
    "resolve_replay_artifact",
    "list_operational_runs_metadata",
    "is_operational_replay_id",
    "clear_compiled_artifacts_cache",
    "ReplayCompilationError",
    "RunNotFoundError",
    "NoCheckpointsError",
    "ReplayCorruptCheckpointError",
    "MalformedPersistenceError",
    "EmptyRunError",
]
