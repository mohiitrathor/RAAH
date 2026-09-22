import sys
import threading
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from api.config import DISPATCH_DIR
from api.settings import settings


# ==============================================================
# ENSURE DISPATCH MODULES ARE IMPORTABLE
# ==============================================================

if str(DISPATCH_DIR) not in sys.path:
    sys.path.insert(0, str(DISPATCH_DIR))

from simulator import Simulator
from simulation_output import SimulationOutput
from api.persistence import (
    persistence_bridge,
    SQLiteStateStore,
    StateRecoveryEngine,
    RecoveryStatus,
    serialize_dispatch_state,
    deserialize_dispatch_state,
    CheckpointRecord,
)

logger = logging.getLogger("raah.dependencies.manager")


# ==============================================================
# SIMULATOR MANAGER
# ==============================================================

class SimulatorManager:
    """
    Thread-safe singleton manager for the RAAH Simulator.

    Guarantees:
      1. Exactly ONE authoritative Simulator instance and live DispatchState.
      2. Thread-safe execution between concurrent API requests and
         background real-time simulation ticks.
      3. Race-safe start, stop, and reset operations.
      4. Safe thread termination prior to state reconstruction.
      5. Coordinated historical persistence run lifecycle.
      6. Authoritative state persistence, periodic checkpointing, and startup recovery.
    """

    def __init__(self):

        self._simulator: Simulator | None = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

        # Historical persistence run ID
        self._active_run_id: int | None = None

        # State persistence and recovery layer
        self._persistence_store = SQLiteStateStore(settings.database_path)
        self._recovery_status: str = RecoveryStatus.CLEAN_START
        self._recovered_checkpoint_id: Optional[str] = None
        self._checkpoint_thread: Optional[threading.Thread] = None
        self._checkpoint_stop_event = threading.Event()

        # Real-time simulation state
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._status = "STOPPED"
        self._tick_interval_seconds: float = 1.0
        self._minutes_per_tick: float = 1.0 / 60.0
        self._ticks_processed: int = 0
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._consecutive_errors: int = 0
        self._is_shutting_down: bool = False
        self._sim_time_accumulator: float = 0.0

    # ----------------------------------------------------------
    # INITIALIZE
    # ----------------------------------------------------------

    def initialize(self):
        """
        Create the Simulator instance, initialize historical run tracking,
        perform startup recovery if enabled, and launch the periodic checkpoint scheduler.
        Called once during FastAPI lifespan startup or on service restart.
        """
        self._is_shutting_down = False
        with self._lifecycle_lock:
            self._status = "STOPPED"
            self._consecutive_errors = 0
            self._last_error = None

        try:
            from api.realtime.broadcaster import broadcaster
            with broadcaster._lock:
                broadcaster._is_shutdown = False
        except Exception:
            pass

        try:
            from dispatch_engine import load_data, predict_severity
            import pandas as pd
            _, _, _, _, model = load_data()
            dummy_input = pd.DataFrame([{
                "Age": 50, "Sex": "Female", "Condition": "General", "Heart_Rate": 80.0,
                "Respiratory_Rate": 16.0, "Systolic_BP": 120.0, "Diastolic_BP": 80.0,
                "SpO2": 98.0, "Temperature": 37.0, "GCS": 15, "Pain_Score": 0,
                "Blood_Glucose": 100.0, "Oxygen_Requirement": "No Oxygen",
                "Consciousness": "Alert", "Injury_Type": "No Injury", "Arrival_Mode": "Ambulance",
                "Respiratory_Distress": 0, "Chest_Pain": 0, "Bleeding": 0, "Seizure": 0,
                "Diabetes": 0, "Hypertension": 0, "Heart_Disease": 0, "Respiratory_Disease": 0,
            }])
            predict_severity(model, dummy_input)
        except Exception:
            pass

        if not persistence_bridge._is_started:
            persistence_bridge.start()

        with self._lock:
            if self._simulator is None:
                run_id = persistence_bridge.create_run(notes="Initial simulation session")
                self._active_run_id = run_id

                # Authoritative startup recovery
                recovered_state = None
                if settings.persistence_enabled and settings.auto_recovery_enabled:
                    restored, status, cid, err_msg = StateRecoveryEngine.recover_state(
                        store=self._persistence_store,
                        fallback_to_clean=settings.recovery_fallback_to_clean,
                    )
                    self._recovery_status = status
                    self._recovered_checkpoint_id = cid
                    recovered_state = restored
                else:
                    self._recovery_status = (
                        RecoveryStatus.DISABLED
                        if not settings.persistence_enabled
                        else RecoveryStatus.CLEAN_START
                    )

                self._simulator = Simulator()
                if recovered_state is not None:
                    self._simulator.state = recovered_state
                    logger.info("Simulator initialized with recovered state from checkpoint '%s'.", cid)
                else:
                    logger.info("Simulator initialized with clean state (status: %s).", self._recovery_status)

                self._simulator.persistence_bridge = persistence_bridge
                self._simulator.run_id = run_id

        self._start_checkpoint_scheduler()

    # ----------------------------------------------------------
    # PERSISTENCE & CHECKPOINTING
    # ----------------------------------------------------------

    @property
    def persistence_store(self) -> SQLiteStateStore:
        return self._persistence_store

    @property
    def recovery_status(self) -> str:
        return self._recovery_status

    @property
    def recovered_checkpoint_id(self) -> Optional[str]:
        return self._recovered_checkpoint_id

    def create_checkpoint(self, metadata: Optional[Dict[str, Any]] = None) -> CheckpointRecord:
        """
        Atomically capture live DispatchState under lock, then persist
        it via the persistence store outside the lock to guarantee zero
        lock contention on the dispatch decision path.
        """
        with self._lock:
            if self._simulator is None or self._simulator.state is None:
                raise RuntimeError("Cannot checkpoint: Simulator is not initialized.")
            # Critical section is ONLY in-memory dict extraction (< 0.2ms)
            state_data = serialize_dispatch_state(self._simulator.state)
            sim_time = self._simulator.state.current_time

        # Persistent disk write occurs completely outside manager._lock
        record = self._persistence_store.save_checkpoint(
            state_data=state_data,
            sim_time=sim_time,
            metadata=metadata,
        )
        return record

    def restore_from_checkpoint(self, checkpoint_id: str) -> bool:
        """
        Authoritatively restore DispatchState from an explicit checkpoint record.
        """
        record = self._persistence_store.load_checkpoint(checkpoint_id)
        if not record:
            raise ValueError(f"Checkpoint '{checkpoint_id}' not found.")

        restored_state = deserialize_dispatch_state(record.payload)
        with self._lock:
            if self._simulator is None:
                self._simulator = Simulator()
            self._simulator.state = restored_state
            self._recovery_status = RecoveryStatus.RECOVERED
            self._recovered_checkpoint_id = checkpoint_id
            logger.info("Restored live DispatchState from checkpoint '%s' at sim_time=%d.", checkpoint_id, restored_state.current_time)
        return True

    def _start_checkpoint_scheduler(self):
        """Start the background checkpoint scheduler if enabled."""
        if not settings.persistence_enabled or settings.checkpoint_interval_seconds <= 0:
            return
        if self._checkpoint_thread is not None and self._checkpoint_thread.is_alive():
            return

        self._checkpoint_stop_event.clear()
        self._checkpoint_thread = threading.Thread(
            target=self._checkpoint_loop,
            name="RAAH-CheckpointScheduler",
            daemon=True,
        )
        self._checkpoint_thread.start()
        logger.info("Periodic checkpoint scheduler started (interval: %.1fs).", settings.checkpoint_interval_seconds)

    def _stop_checkpoint_scheduler(self):
        """Stop the background checkpoint scheduler cleanly."""
        self._checkpoint_stop_event.set()
        if self._checkpoint_thread is not None and self._checkpoint_thread.is_alive():
            self._checkpoint_thread.join(timeout=2.0)
        self._checkpoint_thread = None

    def _checkpoint_loop(self):
        """Worker loop for periodic state checkpointing."""
        while not self._checkpoint_stop_event.wait(timeout=settings.checkpoint_interval_seconds):
            if self._checkpoint_stop_event.is_set():
                break
            try:
                if self.is_initialized:
                    self.create_checkpoint(metadata={"trigger": "periodic"})
            except Exception as err:
                logger.warning("Periodic state checkpoint failed: %s", err)

    # ----------------------------------------------------------
    # ACTIVE RUN ID ACCESS
    # ----------------------------------------------------------

    @property
    def active_run_id(self) -> int | None:

        with self._lock:
            return self._active_run_id

    # ----------------------------------------------------------
    # INITIALIZED STATUS
    # ----------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        with self._lock:
            return self._simulator is not None

    @property
    def status(self) -> str:
        with self._lifecycle_lock:
            return self._status

    def check_readiness(self) -> tuple[bool, dict]:
        checks = {}
        if self._is_shutting_down:
            return False, {
                "status": "NOT_READY",
                "shutting_down": True,
                "message": "Service is undergoing graceful shutdown",
            }

        with self._lock:
            sim_init = self._simulator is not None
            checks["simulator_initialized"] = sim_init
            state_ok = False
            if sim_init:
                try:
                    state_ok = self._simulator.state is not None
                except Exception:
                    state_ok = False
            checks["simulator_state_available"] = state_ok

        db_ok = False
        try:
            from api.persistence.db import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            db_ok = True
            conn.close()
        except Exception:
            db_ok = False
        checks["database_reachable"] = db_ok

        # State persistence & durability health
        p_health = self._persistence_store.health_check()
        checks["persistence"] = {
            "enabled": settings.persistence_enabled,
            "backend": settings.persistence_backend,
            "healthy": p_health.get("healthy", False),
            "total_checkpoints": p_health.get("total_checkpoints", 0),
            "last_checkpoint": self._persistence_store.last_checkpoint_id,
            "recovery_status": self._recovery_status,
            "error": p_health.get("error"),
        }

        # Telemetry queue metrics & worker thread liveness
        pb_worker_alive = (
            persistence_bridge._is_started
            and persistence_bridge._worker_thread is not None
            and persistence_bridge._worker_thread.is_alive()
        )
        checks["telemetry_queue"] = {
            "depth": persistence_bridge.queue_depth,
            "capacity": persistence_bridge.queue_capacity,
            "dropped": persistence_bridge.dropped_count,
            "worker_alive": pb_worker_alive,
        }

        # Checkpoint scheduler thread
        chk_alive = True
        if settings.persistence_enabled and settings.checkpoint_interval_seconds > 0:
            chk_alive = (
                self._checkpoint_thread is not None
                and self._checkpoint_thread.is_alive()
            )
        checks["checkpoint_scheduler_alive"] = chk_alive

        # Adapter registry health
        adapters_ok = True
        try:
            from api.adapters import adapter_registry
            ad_health = adapter_registry.health_check_all()
            checks["adapters"] = ad_health
            adapters_ok = ad_health.get("healthy", True)
        except Exception as ad_err:
            checks["adapters"] = {"healthy": False, "error": str(ad_err)}
            adapters_ok = False

        with self._lifecycle_lock:
            # Detect silent death if thread is dead while status says RUNNING
            if self._status == "RUNNING" and (self._thread is None or not self._thread.is_alive()):
                self._status = "ERRORED"
                self._last_error = "Simulation thread terminated unexpectedly"
                logger.error("Readiness probe detected silent termination of simulation thread!")

            sim_status_ok = self._status in ("RUNNING", "STOPPED") and self._consecutive_errors == 0
            checks["simulation_status_valid"] = sim_status_ok
            checks["simulation_status"] = self._status
            checks["consecutive_errors"] = self._consecutive_errors

        persistence_ok = True
        if settings.persistence_enabled:
            persistence_ok = p_health.get("healthy", False)

        is_ready = all([
            sim_init,
            state_ok,
            db_ok,
            sim_status_ok,
            persistence_ok,
            pb_worker_alive,
            chk_alive,
            adapters_ok,
            not self._is_shutting_down,
        ])
        return is_ready, checks

    # ----------------------------------------------------------
    # SIMULATOR ACCESS
    # ----------------------------------------------------------

    @property
    def simulator(self) -> Simulator:

        if self._simulator is None:
            raise RuntimeError(
                "Simulator not initialized. "
                "Call manager.initialize() first."
            )

        return self._simulator

    # ----------------------------------------------------------
    # LOCK ACCESS
    # ----------------------------------------------------------

    @property
    def lock(self) -> threading.Lock:

        return self._lock

    # ----------------------------------------------------------
    # REAL-TIME RUNNING STATUS
    # ----------------------------------------------------------

    @property
    def is_realtime_running(self) -> bool:

        with self._lifecycle_lock:
            return (
                self._status == "RUNNING"
                and self._thread is not None
                and self._thread.is_alive()
            )

    # ----------------------------------------------------------
    # START REAL-TIME
    # ----------------------------------------------------------

    def start_realtime(
        self,
        tick_interval_seconds: float = 1.0,
        minutes_per_tick: float = 1.0 / 60.0,
    ) -> dict:
        """
        Start the background real-time simulation thread.
        Thread-safe and race-safe.
        """

        with self._lifecycle_lock:

            if (
                self._status == "RUNNING"
                and self._thread is not None
                and self._thread.is_alive()
            ):
                raise RuntimeError(
                    "Simulation is already running."
                )

            # Ensure any lingering dead thread handle is cleared
            if self._thread is not None and self._thread.is_alive():
                self._stop_event.set()
                self._thread.join(timeout=3.0)

            self._tick_interval_seconds = float(tick_interval_seconds)
            self._minutes_per_tick = float(minutes_per_tick)
            self._sim_time_accumulator = 0.0
            self._ticks_processed = 0
            self._consecutive_errors = 0
            self._last_error = None
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._stop_event.clear()
            self._status = "RUNNING"

            self._thread = threading.Thread(
                target=self._run_loop,
                name="RealtimeSimulationThread",
                daemon=True,
            )
            self._thread.start()

            with self._lock:
                sim_time = self.simulator.state.current_time

            return {
                "status": "RUNNING",
                "message": "Real-time simulation started.",
                "time": sim_time,
            }

    # ----------------------------------------------------------
    # STOP REAL-TIME
    # ----------------------------------------------------------

    def stop_realtime(self) -> dict:
        """
        Stop the background real-time simulation thread.
        Signals the stop event and waits for termination.
        Idempotent and race-safe.
        """

        with self._lifecycle_lock:

            if (
                self._status == "STOPPED"
                and (self._thread is None or not self._thread.is_alive())
            ):
                with self._lock:
                    sim_time = self.simulator.state.current_time
                return {
                    "status": "STOPPED",
                    "message": "Simulation is already stopped.",
                    "time": sim_time,
                }

            self._stop_event.set()

            if self._thread is not None:
                self._thread.join(timeout=3.0)
                self._thread = None

            self._status = "STOPPED"

            with self._lock:
                sim_time = self.simulator.state.current_time

            return {
                "status": "STOPPED",
                "message": "Real-time simulation stopped.",
                "time": sim_time,
            }

    # ----------------------------------------------------------
    # REAL-TIME STATUS
    # ----------------------------------------------------------

    def get_realtime_status(self) -> dict:
        """
        Retrieve live telemetry of the real-time simulation loop.
        """

        with self._lifecycle_lock:

            is_running = (
                self._status == "RUNNING"
                and self._thread is not None
                and self._thread.is_alive()
            )

            # Detect if the worker died unexpectedly
            if self._status == "RUNNING" and not is_running:
                self._status = (
                    "STOPPED"
                    if self._last_error is None
                    else "ERRORED"
                )

            with self._lock:
                current_time = self.simulator.state.current_time

            speed_multiplier = round(
                (self._minutes_per_tick * 60.0)
                / max(self._tick_interval_seconds, 0.001),
                2,
            )

            return {
                "status": self._status,
                "is_running": is_running,
                "current_time": int(current_time),
                "tick_interval_seconds": self._tick_interval_seconds,
                "minutes_per_tick": round(float(self._minutes_per_tick), 4),
                "speed_multiplier": speed_multiplier,
                "ticks_processed": self._ticks_processed,
                "started_at": self._started_at,
                "last_error": self._last_error,
            }

    # ----------------------------------------------------------
    # BACKGROUND WORKER LOOP
    # ----------------------------------------------------------

    def _run_loop(self):
        """
        Internal worker executed in a background daemon thread.
        Holds the lock ONLY during state advancement (~1ms),
        never while waiting. Includes exponential backoff retry on transient faults.
        """
        while not self._stop_event.is_set():
            interrupted = self._stop_event.wait(
                timeout=self._tick_interval_seconds
            )
            if interrupted or self._stop_event.is_set():
                break

            try:
                with self._lock:
                    delta_mins = float(self._minutes_per_tick)
                    self._simulator.advance_time(delta_mins)
                    self._simulator.process_events()
                    self._simulator.check_redirections()

                    # Extract lightweight projection under lock (M13 Phase 1)
                    sim_state = self._simulator.state
                    cur_time = int(sim_state.current_time)
                    fleet_counts = SimulationOutput.fleet_summary(sim_state.ambulances.values())
                    active_incidents_data = [
                        {
                            "incident_id": int(inc.incident_id),
                            "priority": int(inc.priority),
                            "severity": str(inc.severity),
                            "status": str(inc.status),
                            "ambulance_id": inc.ambulance_id,
                            "hospital_id": inc.hospital_id,
                            "eta_minutes": (
                                round(float(sim_state.ambulances[inc.ambulance_id].eta_minutes), 2)
                                if (inc.ambulance_id in sim_state.ambulances and sim_state.ambulances[inc.ambulance_id].eta_minutes is not None)
                                else None
                            ),
                        }
                        for inc in sim_state.get_active_incidents()
                    ]
                    active_inc_count = len(active_incidents_data)
                    moving_ambs = [
                        {
                            "ambulance_id": str(a.ambulance_id),
                            "latitude": round(float(a.latitude), 6),
                            "longitude": round(float(a.longitude), 6),
                            "status": str(a.status),
                            "eta_minutes": round(float(a.eta_minutes), 2) if a.eta_minutes is not None else None,
                            "hospital_id": getattr(a, "hospital_id", None),
                            "route_waypoints": [list(wp) for wp in (getattr(a, "route_waypoints", None) or [])],
                        }
                        for a in sim_state.ambulances.values()
                        if a.status in ("EN_ROUTE", "ARRIVED") or getattr(a, "is_repositioning", False)
                    ]
                    tick_payload = {
                        "current_time": cur_time,
                        "status": "RUNNING",
                        "speed_multiplier": float(
                            self._minutes_per_tick / max(self._tick_interval_seconds, 0.001) * 60.0
                        ),
                        "ticks_processed": self._ticks_processed + 1,
                        "fleet": fleet_counts,
                        "active_incidents_count": active_inc_count,
                        "active_incidents": active_incidents_data,
                        "moving_ambulances": moving_ambs,
                    }

                # Broadcast TICK outside _lock
                try:
                    from api.realtime.broadcaster import broadcaster
                    from api.realtime.models import EventType
                    broadcaster.broadcast(EventType.TICK, tick_payload, cur_time)
                except Exception as bcast_err:
                    logger.debug("Realtime tick broadcast exception: %s", bcast_err)

                self._ticks_processed += 1
                self._consecutive_errors = 0

            except Exception as exc:
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Background simulation tick failed (attempt %d/%d): %s",
                    self._consecutive_errors,
                    settings.consecutive_error_threshold,
                    exc,
                    extra={
                        "consecutive_errors": self._consecutive_errors,
                        "threshold": settings.consecutive_error_threshold,
                        "error": str(exc),
                    },
                )
                try:
                    from api.observability.metrics import metrics_collector
                    metrics_collector.record_retry()
                except Exception:
                    pass

                if self._consecutive_errors >= settings.consecutive_error_threshold:
                    with self._lifecycle_lock:
                        self._status = "ERRORED"
                    logger.critical(
                        "Background simulation worker terminated after reaching error threshold (%d): %s",
                        self._consecutive_errors,
                        self._last_error,
                    )
                    break

                # Exponential backoff between failed retries to avoid CPU spinning
                import time as _t
                backoff = min(1.0, 0.05 * (2 ** (self._consecutive_errors - 1)))
                _t.sleep(backoff)

    def restart_realtime(self) -> dict:
        """
        Controlled restart of the real-time simulation thread after failure.
        Preserves DispatchState integrity.
        """
        with self._lifecycle_lock:
            if self._simulator is None or self._simulator.state is None:
                raise RuntimeError("Cannot restart: DispatchState is uninitialized or corrupt.")
            logger.info("Restarting real-time simulation thread from status: %s", self._status)
            if self._thread is not None and self._thread.is_alive():
                self._stop_event.set()
                self._thread.join(timeout=2.0)

            self._consecutive_errors = 0
            self._last_error = None
            self._stop_event.clear()
            self._status = "RUNNING"
            self._thread = threading.Thread(
                target=self._run_loop,
                name="RealtimeSimulationThread",
                daemon=True,
            )
            self._thread.start()
            try:
                from api.observability.metrics import metrics_collector
                metrics_collector.record_worker_restart()
            except Exception:
                pass

            return {
                "status": "RUNNING",
                "message": "Simulation restarted successfully.",
                "time": self._simulator.state.current_time,
            }

    def shutdown(self, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """
        Execute deterministic graceful shutdown of all simulation components:
          1. Mark manager as shutting down (readiness immediately fails 503).
          2. Stop real-time simulation thread.
          3. Stop periodic checkpoint scheduler.
          4. Persist final safe shutdown checkpoint.
          5. Finalize active simulation run session.
          6. Flush and shut down persistence queue.
          7. Close persistence store connection.
          8. Report shutdown status.
        """
        import time as _t
        logger.info("Initiating graceful shutdown of SimulatorManager (timeout=%.1fs)...", timeout_seconds)
        start_t = _t.perf_counter()
        self._is_shutting_down = True

        # 1. Stop real-time simulation thread
        try:
            self.stop_realtime()
        except Exception as err:
            logger.warning("Error stopping realtime thread during shutdown: %s", err)

        # 2. Stop checkpoint scheduler
        try:
            self._stop_checkpoint_scheduler()
        except Exception as err:
            logger.warning("Error stopping checkpoint scheduler during shutdown: %s", err)

        # 3. Final safe state checkpoint
        checkpoint_ok = False
        final_sim_time = 0
        if self.is_initialized and settings.persistence_enabled:
            try:
                rec = self.create_checkpoint(metadata={"trigger": "graceful_shutdown"})
                final_sim_time = rec.simulation_time
                checkpoint_ok = True
                logger.info("Saved final graceful shutdown checkpoint '%s' at sim_time=%d.", rec.checkpoint_id, final_sim_time)
            except Exception as err:
                logger.error("Failed to save final shutdown checkpoint: %s", err)

        # 4. Finalize active simulation run
        if self._active_run_id is not None:
            try:
                persistence_bridge.finalize_run(
                    self._active_run_id,
                    final_sim_time=final_sim_time,
                    status="TERMINATED",
                )
            except Exception as err:
                logger.warning("Error finalizing simulation run during shutdown: %s", err)

        # 5. Flush and shutdown telemetry bridge
        remaining_timeout = max(0.5, timeout_seconds - (_t.perf_counter() - start_t))
        drained_ok = False
        try:
            persistence_bridge.shutdown(timeout=remaining_timeout)
            drained_ok = True
        except Exception as err:
            logger.warning("Error shutting down persistence bridge: %s", err)

        # 6. Close persistence store
        try:
            self._persistence_store.close()
        except Exception as err:
            logger.warning("Error closing persistence store: %s", err)

        # 7. Shutdown realtime broadcaster (M13 Phase 1)
        try:
            from api.realtime.broadcaster import broadcaster
            broadcaster.shutdown()
        except Exception as err:
            logger.warning("Error shutting down realtime broadcaster: %s", err)

        with self._lock:
            self._simulator = None
            self._active_run_id = None

        duration_ms = (_t.perf_counter() - start_t) * 1000.0
        logger.info("SimulatorManager graceful shutdown completed in %.2fms.", duration_ms)

        return {
            "status": "SHUTDOWN_COMPLETE",
            "duration_ms": round(duration_ms, 2),
            "final_sim_time": final_sim_time,
            "clean_checkpoint": checkpoint_ok,
            "drained_telemetry": drained_ok,
        }

    # ----------------------------------------------------------
    # RESET
    # ----------------------------------------------------------

    def reset(self):
        """
        Tear down and recreate the Simulator with fresh state and fresh historical run.

        Guaranteed order:
          1. Stop background thread if active.
          2. Wait for background thread to terminate completely.
          3. Clear thread reference.
          4. Ensure persistence queue has processed all events belonging to old run.
          5. Finalize the current run using its final simulation time and status.
          6. Create fresh historical run session.
          7. Acquire simulator lock and re-instantiate Simulator with new run_id.
          8. Leave in STOPPED state at time = 0.
        """

        with self._lifecycle_lock:

            self._stop_event.set()

            if self._thread is not None:
                self._thread.join(timeout=3.0)
                self._thread = None

            self._status = "STOPPED"
            ticks = self._ticks_processed
            self._ticks_processed = 0
            self._last_error = None
            self._started_at = None

            # Get final simulation time and run ID from old simulation
            with self._lock:
                current_time = self._simulator.state.current_time if self._simulator else 0
                old_run_id = self._active_run_id

            # Flush persistence queue to ensure all events from old run are written
            persistence_bridge.flush(timeout=3.0)
            if old_run_id is not None:
                persistence_bridge.finalize_run(
                    old_run_id,
                    final_sim_time=current_time,
                    total_ticks=ticks,
                    status="COMPLETED",
                )

            # Create new historical run session in SQLite
            new_run_id = persistence_bridge.create_run(notes="Reset simulation session")

            with self._lock:
                self._active_run_id = new_run_id
                self._simulator = Simulator()
                self._simulator.persistence_bridge = persistence_bridge
                self._simulator.run_id = new_run_id
                self._recovery_status = RecoveryStatus.CLEAN_START
                self._recovered_checkpoint_id = None

            try:
                from api.realtime.broadcaster import broadcaster
                with broadcaster._lock:
                    broadcaster._is_shutdown = False
            except Exception:
                pass


# ==============================================================
# MODULE-LEVEL SINGLETON
# ==============================================================

manager = SimulatorManager()
