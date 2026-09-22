"""
RAAH M12 Phase 3: State Persistence, Recovery & Durability Test Suite
====================================================================

Comprehensive test suite verifying:
  1. Fresh startup with no database
  2. State save/load round trip
  3. Latest checkpoint selection
  4. Schema version validation
  5. Corrupt checkpoint handling (checksum mismatch)
  6. Malformed state handling (schema/field errors)
  7. Database unavailable handling
  8. Database locked handling
  9. Atomic/transactional checkpoint behavior
  10. Startup recovery into DispatchState
  11. Recovery fallback behavior on corrupt checkpoint
  12. Persistence health status diagnostics
  13. Liveness remains available during persistence failure
  14. Readiness reflects persistence failure
  15. Concurrent checkpoint/state access
  16. Dispatch + checkpoint concurrency
  17. Bounded persistence queue behavior & overflow metrics
  18. No hardcoded machine-specific paths
  19. No secrets or tokens in persisted state
  20. Persistence performance and checkpoint latency overhead
  21. Authoritative invariant: DispatchState is the sole live truth
"""

import os
import io
import time
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi.testclient import TestClient

from api.settings import settings, Settings
from api.main import app
from api.dependencies import manager
from api.auth import Role, create_test_token
from state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)
from api.persistence import (
    SQLiteStateStore,
    StateRecoveryEngine,
    RecoveryStatus,
    CheckpointRecord,
    serialize_dispatch_state,
    deserialize_dispatch_state,
    compute_state_checksum,
    persistence_bridge,
    PersistenceError,
    CorruptCheckpointError,
    IncompatibleSchemaError,
    CorruptStateError,
    DatabaseUnavailableError,
    DatabaseLockedError,
)

client = TestClient(app)
manager.initialize()


def auth_header(role: Role = Role.ADMINISTRATOR) -> Dict[str, str]:
    token = create_test_token(role=role, username="admin_tester")
    return {"Authorization": f"Bearer {token}"}


# ======================================================================
# 1-3: BASIC STORE OPERATIONS
# ======================================================================

def test_01_fresh_startup_with_no_database():
    """Initializing store with a non-existent database creates tables and returns CLEAN_START."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "fresh_raah.db"
        assert not db_file.exists()

        store = SQLiteStateStore(db_path=db_file)
        assert db_file.exists()

        latest = store.load_latest_checkpoint()
        assert latest is None, "Fresh database should have no checkpoints."

        state, status, cid, err = StateRecoveryEngine.recover_state(store)
        assert state is None
        assert status == RecoveryStatus.CLEAN_START
        print("✓ Fresh startup with no database handled cleanly.")


def test_02_state_save_load_round_trip():
    """Serialize a full DispatchState, save checkpoint, reload, and verify exact equality."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "roundtrip.db"
        store = SQLiteStateStore(db_path=db_file)

        # Build populated DispatchState
        orig_state = DispatchState(current_time=42)
        orig_state.add_incident(IncidentState(
            incident_id=101,
            condition="CARDIAC_ARREST",
            severity="CRITICAL",
            priority=1,
            status="ASSIGNED",
            ambulance_id="AMB_99",
            hospital_id="HOSP_01",
        ))
        orig_state.add_ambulance(AmbulanceState(
            ambulance_id="AMB_99",
            ambulance_type="ALS",
            latitude=26.9124,
            longitude=75.7873,
            status="EN_ROUTE",
            incident_id=101,
            hospital_id="HOSP_01",
            eta_minutes=6.5,
            base_eta_minutes=5.0,
            traffic_level="MODERATE",
            road_condition="GOOD",
            route_distance_km=4.2,
        ))
        orig_state.add_hospital(HospitalState(
            hospital_id="HOSP_01",
            hospital_type="Tertiary",
            latitude=26.9200,
            longitude=75.8000,
            capacity=200,
            current_load=150,
            icu_capacity=30,
            current_icu_load=22,
        ))
        orig_state.add_event("Incident 101 assigned to AMB_99")

        # Serialize and save
        state_data = serialize_dispatch_state(orig_state)
        rec = store.save_checkpoint(
            state_data=state_data,
            sim_time=orig_state.current_time,
            metadata={"operator": "test_runner", "drill": False},
        )
        assert rec.checkpoint_id.startswith("chk_")
        assert rec.simulation_time == 42

        # Reload and deserialize
        loaded_rec = store.load_checkpoint(rec.checkpoint_id)
        assert loaded_rec is not None
        assert loaded_rec.checksum == rec.checksum

        restored_state = deserialize_dispatch_state(loaded_rec.payload)
        assert restored_state.current_time == 42
        assert 101 in restored_state.incidents
        assert restored_state.incidents[101].condition == "CARDIAC_ARREST"
        assert restored_state.incidents[101].ambulance_id == "AMB_99"

        assert "AMB_99" in restored_state.ambulances
        assert restored_state.ambulances["AMB_99"].eta_minutes == 6.5
        assert restored_state.ambulances["AMB_99"].status == "EN_ROUTE"

        assert "HOSP_01" in restored_state.hospitals
        assert restored_state.hospitals["HOSP_01"].available_beds == 50
        assert restored_state.hospitals["HOSP_01"].available_icu == 8

        assert len(restored_state.events) == 1
        print("✓ DispatchState round-trip serialization and deserialization verified.")


def test_03_latest_checkpoint_selection():
    """Multiple checkpoints saved at different times; load_latest_checkpoint returns the newest."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "latest.db"
        store = SQLiteStateStore(db_path=db_file)

        for t in [5, 10, 15, 8]:  # Out of order insertion
            st = DispatchState(current_time=t)
            data = serialize_dispatch_state(st)
            store.save_checkpoint(state_data=data, sim_time=t)

        latest = store.load_latest_checkpoint()
        assert latest is not None
        assert latest.simulation_time == 15, f"Expected newest sim_time 15, got {latest.simulation_time}"
        print("✓ Latest checkpoint correctly selected by highest simulation time.")


# ======================================================================
# 4-6: VALIDATION & INTEGRITY
# ======================================================================

def test_04_schema_version_validation():
    """Payloads with unsupported schema versions must raise IncompatibleSchemaError."""
    st = DispatchState(current_time=1)
    data = serialize_dispatch_state(st)
    data["schema_version"] = 999  # Unsupported version

    try:
        deserialize_dispatch_state(data)
        assert False, "Should have raised IncompatibleSchemaError"
    except IncompatibleSchemaError:
        pass
    print("✓ Incompatible schema version cleanly rejected.")


def test_05_corrupt_checkpoint_handling():
    """Tampering with serialized JSON bytes causes checksum mismatch and raises CorruptCheckpointError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "corrupt.db"
        store = SQLiteStateStore(db_path=db_file)

        st = DispatchState(current_time=12)
        rec = store.save_checkpoint(serialize_dispatch_state(st), sim_time=12)

        # Directly tamper the payload in SQLite behind the store's back
        conn = sqlite3.connect(str(db_file))
        tampered_json = json.dumps({"schema_version": 1, "simulation_time": 999, "state": {}})
        conn.execute(
            "UPDATE state_checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            (tampered_json, rec.checkpoint_id),
        )
        conn.commit()
        conn.close()

        # Load must detect checksum mismatch
        try:
            store.load_checkpoint(rec.checkpoint_id)
            assert False, "Should have raised CorruptCheckpointError"
        except CorruptCheckpointError:
            pass
        print("✓ Tampered checkpoint checksum failure detected.")


def test_06_malformed_state_handling():
    """Missing required state collections or invalid fields raises CorruptStateError."""
    # Missing ambulances collection
    bad_payload = {
        "schema_version": 1,
        "simulation_time": 0,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "state": {
            "incidents": {},
            "hospitals": {},
            # ambulances missing!
        },
    }
    try:
        deserialize_dispatch_state(bad_payload)
        assert False, "Should have raised CorruptStateError"
    except CorruptStateError:
        pass

    # Malformed incident record
    bad_inc_payload = {
        "schema_version": 1,
        "simulation_time": 0,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "state": {
            "incidents": {"1": {"missing_priority": True}},
            "ambulances": {},
            "hospitals": {},
        },
    }
    try:
        deserialize_dispatch_state(bad_inc_payload)
        assert False, "Should have raised CorruptStateError"
    except CorruptStateError:
        pass
    print("✓ Malformed state payloads rejected with CorruptStateError.")


# ======================================================================
# 7-9: FAILURE SEMANTICS & TRANSACTIONS
# ======================================================================

def test_07_database_unavailable():
    """When the database file/directory cannot be opened, DatabaseUnavailableError is raised."""
    unwritable_path = Path("/proc/nonexistent_raah_dir/db.sqlite")
    try:
        store = SQLiteStateStore(db_path=unwritable_path)
        assert False, "Should have failed to initialize unwritable path"
    except DatabaseUnavailableError:
        pass
    print("✓ Unreachable database path raises DatabaseUnavailableError without crash.")


def test_08_database_locked():
    """When a database is held in an exclusive lock, operations timeout with DatabaseLockedError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "locked.db"
        store = SQLiteStateStore(db_path=db_file, busy_timeout_ms=100)

        # Hold exclusive lock on connection 1
        raw_conn = sqlite3.connect(str(db_file), timeout=0.1)
        raw_conn.execute("BEGIN EXCLUSIVE;")

        try:
            st = DispatchState(current_time=1)
            store.save_checkpoint(serialize_dispatch_state(st), sim_time=1)
            assert False, "Should have raised DatabaseLockedError"
        except DatabaseLockedError:
            pass
        finally:
            raw_conn.rollback()
            raw_conn.close()
        print("✓ Database lock contention handled cleanly via DatabaseLockedError.")


def test_09_atomic_transactional_checkpoint():
    """Checkpoint writes are fully atomic; failed inserts do not leave partial state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "atomic.db"
        store = SQLiteStateStore(db_path=db_file)

        # Valid write
        st = DispatchState(current_time=1)
        store.save_checkpoint(serialize_dispatch_state(st), sim_time=1, checkpoint_id="chk_first")

        # Invalid write attempt
        try:
            store.save_checkpoint({"schema_version": 1}, sim_time=2)  # Missing state block
        except CorruptStateError:
            pass

        # Verify only 1 valid row exists
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM state_checkpoints;")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1, "Failed checkpoint must not leave partial records."
        print("✓ Checkpoint writes are atomic and transactional.")


# ======================================================================
# 10-11: RECOVERY ENGINE & FALLBACK
# ======================================================================

def test_10_recovery_into_dispatch_state():
    """StateRecoveryEngine recovers DispatchState with intact simulation clock and entities."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "recovery.db"
        store = SQLiteStateStore(db_path=db_file)

        st = DispatchState(current_time=120)
        st.add_incident(IncidentState(1, "TRAUMA", "HIGH", 1))
        store.save_checkpoint(serialize_dispatch_state(st), sim_time=120, checkpoint_id="chk_target")

        recovered, status, cid, err = StateRecoveryEngine.recover_state(store)
        assert status == RecoveryStatus.RECOVERED
        assert cid == "chk_target"
        assert recovered is not None
        assert recovered.current_time == 120
        assert 1 in recovered.incidents
        print("✓ StateRecoveryEngine restored DispatchState successfully.")


def test_11_recovery_fallback_behavior():
    """When latest checkpoint is corrupt, recovery falls back safely to clean initial state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "fallback.db"
        store = SQLiteStateStore(db_path=db_file)

        st = DispatchState(current_time=50)
        rec = store.save_checkpoint(serialize_dispatch_state(st), sim_time=50)

        # Corrupt the payload in SQLite
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "UPDATE state_checkpoints SET payload_json = 'corrupted-non-json' WHERE checkpoint_id = ?",
            (rec.checkpoint_id,),
        )
        conn.commit()
        conn.close()

        # Fallback to clean state
        recovered, status, cid, err = StateRecoveryEngine.recover_state(store, fallback_to_clean=True)
        assert status == RecoveryStatus.FALLBACK_CLEAN
        assert recovered is None
        assert err is not None
        print("✓ Corrupt checkpoint safely triggers FALLBACK_CLEAN without crashing.")


# ======================================================================
# 12-14: HEALTH & READINESS INTEGRATION
# ======================================================================

def test_12_persistence_health_status():
    """Health check returns healthy=True, checkpoint count, and last checkpoint ID."""
    health = manager.persistence_store.health_check()
    assert health["healthy"] is True
    assert health["backend"] == "sqlite"
    assert "total_checkpoints" in health
    print("✓ Persistence store health check diagnostics verified.")


def test_13_liveness_remains_available_during_persistence_failure():
    """GET /health/live returns ALIVE even if persistence store fails."""
    # Temporarily set an invalid path or simulate error
    prev_path = manager.persistence_store.db_path
    try:
        manager.persistence_store.db_path = Path("/nonexistent/unwritable.db")
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ALIVE"
        print("✓ /health/live remains 200 ALIVE despite persistence backend failure.")
    finally:
        manager.persistence_store.db_path = prev_path


def test_14_readiness_reflects_persistence_failure():
    """Readiness probe reflects persistence failure when backend is down."""
    prev_path = manager.persistence_store.db_path
    try:
        manager.persistence_store.db_path = Path("/nonexistent/unwritable.db")
        is_ready, checks = manager.check_readiness()
        assert is_ready is False
        assert checks["persistence"]["healthy"] is False

        resp = client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "NOT_READY"
        print("✓ /health/ready correctly reports NOT_READY (503) when persistence fails.")
    finally:
        manager.persistence_store.db_path = prev_path


# ======================================================================
# 15-17: CONCURRENCY & BOUNDED QUEUE
# ======================================================================

def test_15_concurrent_checkpoint_state_access():
    """Multiple threads creating and reading checkpoints concurrently without race conditions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "concurrent.db"
        store = SQLiteStateStore(db_path=db_file)

        errors = []

        def worker(thread_id: int):
            for i in range(15):
                try:
                    sim_t = thread_id * 100 + i
                    st = DispatchState(current_time=sim_t)
                    st.add_incident(IncidentState(sim_t, "COND", "MED", 2))
                    rec = store.save_checkpoint(
                        state_data=serialize_dispatch_state(st),
                        sim_time=sim_t,
                        metadata={"worker": thread_id},
                    )
                    loaded = store.load_checkpoint(rec.checkpoint_id)
                    if loaded is None or loaded.simulation_time != sim_t:
                        errors.append(f"Worker {thread_id} verification failed for time {sim_t}")
                except Exception as ex:
                    errors.append(f"Worker {thread_id} error: {ex}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrency errors encountered: {errors}"
        records = store.list_checkpoints(limit=200)
        assert len(records) == 120  # 8 * 15
        print("✓ 8 concurrent threads executed 120 checkpoint operations without race conditions.")


def test_16_dispatch_plus_checkpoint_concurrency():
    """Real-time dispatch and checkpointing run concurrently without deadlocks."""
    checkpoint_errors = []
    dispatch_errors = []
    stop_signal = threading.Event()

    def dispatch_worker():
        while not stop_signal.is_set():
            try:
                resp = client.get("/state/dashboard", headers=auth_header())
                if resp.status_code != 200:
                    dispatch_errors.append(resp.status_code)
                time.sleep(0.005)
            except Exception as e:
                dispatch_errors.append(str(e))

    def checkpoint_worker():
        for i in range(10):
            try:
                rec = manager.create_checkpoint(metadata={"test_run": i})
                assert rec is not None
                time.sleep(0.01)
            except Exception as e:
                checkpoint_errors.append(str(e))

    d_threads = [threading.Thread(target=dispatch_worker) for _ in range(4)]
    c_threads = [threading.Thread(target=checkpoint_worker) for _ in range(2)]

    for t in d_threads + c_threads:
        t.start()
    for t in c_threads:
        t.join()

    stop_signal.set()
    for t in d_threads:
        t.join()

    assert len(dispatch_errors) == 0, f"Dispatch errors during checkpointing: {dispatch_errors}"
    assert len(checkpoint_errors) == 0, f"Checkpoint errors: {checkpoint_errors}"
    print("✓ Dispatch queries and state checkpointing executed concurrently with zero deadlocks.")


def test_17_bounded_persistence_queue_behavior():
    """PersistenceBridge queue enforces bounded capacity and records dropped records on saturation."""
    from api.persistence.bridge import PersistenceBridge

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "bounded_queue.db"
        bridge = PersistenceBridge(db_path=db_file, max_queue_size=10)

        assert bridge.queue_capacity == 10
        assert bridge.queue_depth == 0
        assert bridge.dropped_count == 0

        # Enqueue 15 items without starting worker -> 10 buffered, 5 dropped
        for i in range(15):
            bridge._enqueue({"op": "TEST", "index": i})

        assert bridge.queue_depth == 10
        assert bridge.dropped_count == 5
        print("✓ PersistenceBridge bounded queue verified: 10 buffered, 5 overflow dropped.")


# ======================================================================
# 18-21: HARDENING, SECURITY & ARCHITECTURAL INVARIANT
# ======================================================================

def test_18_no_hardcoded_machine_paths():
    """Verify database path and directories are dynamically anchored to repo root."""
    assert not str(settings.database_path).startswith("/Users/"), "Hardcoded developer path found!"
    assert settings.database_path.is_absolute()
    assert str(settings.root_dir) in str(settings.database_path)
    print("✓ No hardcoded machine paths found; database path dynamically anchored.")


def test_19_no_secrets_in_persisted_state():
    """Verify that serialized state and checkpoint payloads contain no JWT secrets or passwords."""
    st = DispatchState(current_time=1)
    payload = serialize_dispatch_state(st)
    payload_str = json.dumps(payload).lower()

    assert settings.jwt_secret_key.lower() not in payload_str
    assert "password" not in payload_str
    assert "secret_key" not in payload_str
    assert "bearer" not in payload_str
    print("✓ Serialized state is clean of secrets, tokens, and credentials.")


def test_20_persistence_performance_and_checkpoint_overhead():
    """Measure state snapshot critical section latency (< 5ms) and SQLite write latency (< 25ms)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "perf.db"
        store = SQLiteStateStore(db_path=db_file)

        # Build realistic fleet state
        sim_state = DispatchState(current_time=100)
        for i in range(50):
            sim_state.add_ambulance(AmbulanceState(
                ambulance_id=f"AMB_{i}",
                ambulance_type="ALS",
                latitude=26.9 + (i * 0.001),
                longitude=75.8 + (i * 0.001),
                status="AVAILABLE" if i % 2 == 0 else "EN_ROUTE",
            ))
            sim_state.add_hospital(HospitalState(
                hospital_id=f"HOSP_{i}",
                hospital_type="General",
                latitude=26.95,
                longitude=75.85,
                capacity=100,
                current_load=50,
                icu_capacity=20,
                current_icu_load=10,
            ))
            sim_state.add_incident(IncidentState(
                incident_id=i,
                condition="TRAUMA",
                severity="HIGH",
                priority=1,
            ))

        # Snapshot latency (in-memory dict capture)
        t0 = time.perf_counter()
        snapshot = serialize_dispatch_state(sim_state)
        snapshot_ms = (time.perf_counter() - t0) * 1000.0

        # Disk write latency (SQLite insert)
        t1 = time.perf_counter()
        rec = store.save_checkpoint(snapshot, sim_time=100)
        disk_ms = (time.perf_counter() - t1) * 1000.0

        assert snapshot_ms < 5.0, f"Snapshot took {snapshot_ms:.2f}ms (target < 5.0ms)"
        assert disk_ms < 25.0, f"Disk write took {disk_ms:.2f}ms (target < 25.0ms)"
        print(f"✓ Performance budgets respected: snapshot={snapshot_ms:.2f}ms, disk write={disk_ms:.2f}ms.")


def test_21_authoritative_invariant_dispatch_state_sole_truth():
    """Live state mutations are authoritative; database state does not override memory unless explicit restore."""
    sim = manager.simulator
    with manager.lock:
        initial_time = sim.state.current_time
        sim.state.advance_time(5)
        advanced_time = sim.state.current_time
        assert advanced_time == initial_time + 5

    # Checkpoint captured at advanced_time
    rec = manager.create_checkpoint()
    assert rec.simulation_time == advanced_time

    # Mutate live state further
    with manager.lock:
        sim.state.advance_time(10)
        final_time = sim.state.current_time
        assert final_time == advanced_time + 10

    # Verify checkpoint in DB still has old time, and live state was NOT reverted
    db_rec = manager.persistence_store.load_checkpoint(rec.checkpoint_id)
    assert db_rec.simulation_time == advanced_time
    assert sim.state.current_time == final_time, "DispatchState must remain the sole authoritative live memory truth."
    print("✓ Architectural Invariant strictly preserved: DispatchState is the sole live truth.")


# ======================================================================
# ENTRY POINT
# ======================================================================

if __name__ == "__main__":
    print("\n===========================================================================")
    print("RAAH M12 PHASE 3: STATE PERSISTENCE, RECOVERY & DURABILITY TEST SUITE")
    print("===========================================================================\n")

    manager.initialize()

    print("[SECTION 1: BASIC STORE & ROUND-TRIP]")
    test_01_fresh_startup_with_no_database()
    test_02_state_save_load_round_trip()
    test_03_latest_checkpoint_selection()

    print("\n[SECTION 2: VALIDATION & INTEGRITY]")
    test_04_schema_version_validation()
    test_05_corrupt_checkpoint_handling()
    test_06_malformed_state_handling()

    print("\n[SECTION 3: FAILURE SEMANTICS & TRANSACTIONS]")
    test_07_database_unavailable()
    test_08_database_locked()
    test_09_atomic_transactional_checkpoint()

    print("\n[SECTION 4: RECOVERY ENGINE & FALLBACK]")
    test_10_recovery_into_dispatch_state()
    test_11_recovery_fallback_behavior()

    print("\n[SECTION 5: HEALTH & READINESS INTEGRATION]")
    test_12_persistence_health_status()
    test_13_liveness_remains_available_during_persistence_failure()
    test_14_readiness_reflects_persistence_failure()

    print("\n[SECTION 6: CONCURRENCY & BOUNDED QUEUE]")
    test_15_concurrent_checkpoint_state_access()
    test_16_dispatch_plus_checkpoint_concurrency()
    test_17_bounded_persistence_queue_behavior()

    print("\n[SECTION 7: HARDENING, SECURITY & ARCHITECTURAL INVARIANT]")
    test_18_no_hardcoded_machine_paths()
    test_19_no_secrets_in_persisted_state()
    test_20_persistence_performance_and_checkpoint_overhead()
    test_21_authoritative_invariant_dispatch_state_sole_truth()

    print("\n===========================================================================")
    print("ALL 21 M12 PHASE 3 STATE PERSISTENCE & RECOVERY TESTS PASSED.")
    print("===========================================================================\n")
