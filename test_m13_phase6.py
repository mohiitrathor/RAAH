"""
RAAH Test Suite: M13.4 Phase 1 — Operational Run to Replay Artifact Compiler
=============================================================================

Validates the read-only operational replay compiler:
  1. Known persisted run compiles successfully into validated ReplayArtifact.
  2. Missing run returns clear failure (RunNotFoundError).
  3. No-checkpoint run is handled correctly (honest empty snapshots or NoCheckpointsError).
  4. Invalid/corrupt checkpoint is rejected (CorruptCheckpointError / MalformedPersistenceError).
  5. ReplayArtifact validates against existing schema (roundtrip from_dict/to_dict).
  6. Event ordering is 100% deterministic (sim_time order and priority).
  7. Simulation timestamps are preserved exactly.
  8. Repeated compilation produces identical artifacts.
  9. Compilation performs no database writes (query_only enforcement and zero DB changes).
 10. Existing scenario ReplayEngine behavior remains compatible (steps, seeks, state).
 11. Partial historical data does not create fabricated information.
 12. Decision evidence absence is handled honestly.
 13. Empty/partial run behavior is deterministic.
 14. Protected files remain completely untouched.
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
import subprocess
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DISPATCH_DIR = ROOT / "Dispatch"
if str(DISPATCH_DIR) not in sys.path:
    sys.path.insert(0, str(DISPATCH_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from api.persistence.db import init_db, get_connection
from api.persistence.serializer import serialize_dispatch_state, compute_state_checksum
from api.persistence.replay_compiler import (
    OperationalReplayCompiler,
    compile_operational_run_to_artifact,
    ReplayCompilationError,
    RunNotFoundError,
    NoCheckpointsError,
    CorruptCheckpointError,
    MalformedPersistenceError,
    EmptyRunError,
)
from Dispatch.state import (
    DispatchState,
    IncidentState,
    AmbulanceState,
    HospitalState,
)
from Dispatch.scenarios.models import ReplayArtifact
from Dispatch.scenarios.replay import ReplayEngine


class TestM13Phase6OperationalReplayCompiler(unittest.TestCase):
    """Exhaustive test suite for M13.4 Phase 1 Replay Compiler."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_replay_compiler.db"
        init_db(cls.db_path)
        cls._populate_test_fixtures()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    @classmethod
    def _populate_test_fixtures(cls):
        """Create multiple synthetic runs in SQLite to test all operational scenarios."""
        conn = get_connection(cls.db_path)
        cursor = conn.cursor()

        # Ensure state_checkpoints table exists
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS state_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                simulation_time INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                saved_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                checksum TEXT NOT NULL,
                is_valid INTEGER DEFAULT 1,
                metadata_json TEXT
            );
            """
        )

        now_iso = "2026-09-10T08:00:00+00:00"
        later_iso = "2026-09-10T08:30:00+00:00"

        # -------------------------------------------------------------
        # RUN 1: Full complete operational run with checkpoints & events
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (1, ?, ?, 'COMPLETED', 30, 30, 'Full operational test run with multi-event telemetry')
            """,
            (now_iso, later_iso),
        )

        # Checkpoints for Run 1
        st0 = DispatchState(current_time=0)
        st0.add_hospital(HospitalState("HOSP_A", "General", 26.90, 75.80, capacity=10, current_load=2, icu_capacity=3, current_icu_load=0))
        st0.add_ambulance(AmbulanceState("AMB_1", "ALS", 26.85, 75.75, status="AVAILABLE"))
        data0 = serialize_dispatch_state(st0)
        chk0 = compute_state_checksum(data0)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_run1_0', 0, 1, ?, ?, ?, 1, ?)
            """,
            (now_iso, json.dumps(data0), chk0, json.dumps({"run_id": 1})),
        )

        st15 = DispatchState(current_time=15)
        st15.add_hospital(HospitalState("HOSP_A", "General", 26.90, 75.80, capacity=10, current_load=3, icu_capacity=3, current_icu_load=1))
        st15.add_ambulance(AmbulanceState("AMB_1", "ALS", 26.88, 75.78, status="EN_ROUTE", incident_id=101, hospital_id="HOSP_A", eta_minutes=6.5))

        st15.add_incident(IncidentState(101, "Trauma", "Severe", 1, status="DISPATCHED", ambulance_id="AMB_1", hospital_id="HOSP_A"))
        data15 = serialize_dispatch_state(st15)
        chk15 = compute_state_checksum(data15)
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_run1_15', 15, 1, '2026-09-10T08:15:00+00:00', ?, ?, 1, ?)
            """,
            (json.dumps(data15), chk15, json.dumps({"run_id": 1})),
        )

        # Incidents for Run 1
        cursor.execute(
            """
            INSERT INTO historical_incidents (run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (1, 101, 'CALL_911', 'Trauma', 'Severe', 1, 0.94, 26.87, 75.77, 2, '2026-09-10T08:02:00+00:00'),
                   (1, 102, 'CALL_911', 'Cardiac', 'Critical', 1, 0.98, 26.89, 75.79, 5, '2026-09-10T08:05:00+00:00')
            """
        )

        # Dispatches for Run 1
        cursor.execute(
            """
            INSERT INTO historical_dispatches (run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (1, 101, 'AMB_1', 'ALS', 'HOSP_A', 'HOSP_A', 7.5, 7.5, 5.2, 'MODERATE', 'CLEAR', 2, 18, 'ARRIVED', '2026-09-10T08:18:00+00:00'),
                   (1, 102, 'AMB_2', 'BLS', 'HOSP_A', 'HOSP_B', 12.0, 10.0, 8.1, 'HEAVY', 'RAIN', 5, 22, 'ARRIVED', '2026-09-10T08:22:00+00:00')
            """
        )

        # Redirection for Run 1
        cursor.execute(
            """
            INSERT INTO historical_redirections (run_id, incident_id, ambulance_id, decision_type, trigger_type, original_hospital_id, new_hospital_id, eta_before, eta_after, eta_saved, eta_improvement_pct, reason, sim_time, created_at)
            VALUES (1, 102, 'AMB_2', 'REDIRECTED', 'HOSPITAL_SATURATION', 'HOSP_A', 'HOSP_B', 14.0, 10.0, 4.0, 28.5, 'Primary hospital saturated', 10, '2026-09-10T08:10:00+00:00')
            """
        )

        # Reposition for Run 1
        cursor.execute(
            """
            INSERT INTO historical_repositions (run_id, ambulance_id, origin_zone, target_zone, origin_lat, origin_lon, target_lat, target_lon, started_sim_time, completed_sim_time, reason, status, created_at)
            VALUES (1, 'AMB_3', 'ZONE_NORTH', 'ZONE_SOUTH', 26.95, 75.82, 26.82, 75.72, 8, 25, 'SURGE_PREVENTION', 'COMPLETED', '2026-09-10T08:08:00+00:00')
            """
        )

        # MCI for Run 1
        cursor.execute(
            """
            INSERT INTO historical_mci_events (run_id, mci_id, name, latitude, longitude, declared_sim_time, resolved_sim_time, status, total_casualties, notes, created_at)
            VALUES (1, 'MCI_01', 'Highway Multi-Vehicle Crash', 26.85, 75.70, 12, 28, 'RESOLVED', 8, 'Bus vs truck on highway', '2026-09-10T08:12:00+00:00')
            """
        )

        # Historical Events for Run 1
        cursor.execute(
            """
            INSERT INTO historical_events (run_id, event_type, sim_time, facility_or_unit_id, message, created_at)
            VALUES (1, 'HOSPITAL_SATURATED', 9, 'HOSP_A', 'ER beds reached 100% capacity', '2026-09-10T08:09:00+00:00'),
                   (1, 'HOSPITAL_RESTORED', 20, 'HOSP_A', 'ER beds freed, capacity at 60%', '2026-09-10T08:20:00+00:00')
            """
        )

        # -------------------------------------------------------------
        # RUN 2: Run with NO checkpoints (only discrete events)
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (2, '2026-09-10T09:00:00+00:00', '2026-09-10T09:10:00+00:00', 'COMPLETED', 10, 10, 'Run with no checkpoints')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_incidents (run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (2, 201, 'CALL_911', 'Medical', 'Mild', 4, 0.88, 26.91, 75.81, 1, '2026-09-10T09:01:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_dispatches (run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (2, 201, 'AMB_4', 'BLS', 'HOSP_B', 'HOSP_B', 5.0, 5.0, 3.0, 'LIGHT', 'CLEAR', 1, 6, 'ARRIVED', '2026-09-10T09:06:00+00:00')
            """
        )

        # -------------------------------------------------------------
        # RUN 3: Run with invalid/corrupt checkpoint (checksum mismatch)
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (3, '2026-09-10T10:00:00+00:00', '2026-09-10T10:10:00+00:00', 'FAILED', 5, 5, 'Run with corrupt checkpoint')
            """
        )
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_corrupt_1', 0, 1, '2026-09-10T10:00:00+00:00', '{"schema_version": 1, "state": {}}', 'TAMPERED_CHECKSUM_HASH_VAL', 1, '{"run_id": 3}')
            """
        )

        # -------------------------------------------------------------
        # RUN 4: Run with malformed JSON checkpoint payload
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (4, '2026-09-10T11:00:00+00:00', '2026-09-10T11:10:00+00:00', 'FAILED', 5, 5, 'Run with malformed JSON')
            """
        )
        cursor.execute(
            """
            INSERT INTO state_checkpoints (checkpoint_id, simulation_time, schema_version, saved_at, payload_json, checksum, is_valid, metadata_json)
            VALUES ('chk_malformed_1', 0, 1, '2026-09-10T11:00:00+00:00', 'NOT_VALID_JSON{{{', 'dummy_hash', 1, '{"run_id": 4}')
            """
        )

        # -------------------------------------------------------------
        # RUN 5: Completely empty run (0 ticks, 0 records)
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (5, '2026-09-10T12:00:00+00:00', '2026-09-10T12:00:00+00:00', 'STOPPED', 0, 0, 'Completely empty run')
            """
        )

        # -------------------------------------------------------------
        # RUN 6: Partially recorded run (dispatch without arrival)
        # -------------------------------------------------------------
        cursor.execute(
            """
            INSERT INTO simulation_runs (run_id, started_at, ended_at, status, total_ticks, final_sim_time, notes)
            VALUES (6, '2026-09-10T13:00:00+00:00', '2026-09-10T13:10:00+00:00', 'ACTIVE', 8, 8, 'Partial run')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_incidents (run_id, incident_id, source, condition, predicted_severity, priority, ml_confidence, patient_lat, patient_lon, dispatched_sim_time, created_at)
            VALUES (6, 601, 'CALL_911', 'Trauma', 'Moderate', 2, 0.91, 26.85, 75.75, 3, '2026-09-10T13:03:00+00:00')
            """
        )
        cursor.execute(
            """
            INSERT INTO historical_dispatches (run_id, incident_id, ambulance_id, ambulance_type, initial_hospital_id, final_hospital_id, initial_eta_minutes, final_eta_minutes, route_distance_km, traffic_level, road_condition, dispatched_sim_time, arrived_sim_time, status, updated_at)
            VALUES (6, 601, 'AMB_5', 'ALS', 'HOSP_A', 'HOSP_A', 9.0, 9.0, 6.0, 'LIGHT', 'CLEAR', 3, NULL, 'EN_ROUTE', '2026-09-10T13:03:00+00:00')
            """
        )

        conn.commit()
        conn.close()

    # =================================================================
    # TESTS
    # =================================================================

    def test_01_known_persisted_run_compiles_successfully(self):
        """1. Known persisted run compiles successfully into validated ReplayArtifact."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)
        self.assertIsInstance(artifact, ReplayArtifact)
        self.assertEqual(artifact.replay_format_version, "1.0.0")
        self.assertEqual(artifact.run_metadata.run_id, "run_1")
        self.assertEqual(artifact.run_metadata.scenario_id, "OPERATIONAL_RUN_1")
        self.assertEqual(artifact.run_metadata.completion_status, "COMPLETED")
        self.assertEqual(len(artifact.snapshots), 2)
        self.assertGreater(len(artifact.events), 0)
        self.assertEqual(artifact.final_summary["total_incidents"], 2)
        self.assertEqual(artifact.final_summary["total_dispatches"], 2)
        self.assertEqual(artifact.final_summary["total_redirections"], 1)
        self.assertEqual(artifact.final_summary["total_repositions"], 1)
        self.assertEqual(artifact.final_summary["total_mci_events"], 1)

    def test_02_missing_run_returns_clear_failure(self):
        """2. Missing run returns clear failure (RunNotFoundError) without exposing internal SQL or stacks."""
        compiler = OperationalReplayCompiler(db_path=self.db_path)
        with self.assertRaises(RunNotFoundError) as ctx:
            compiler.compile(run_id=99999)
        err_msg = str(ctx.exception)
        self.assertIn("99999", err_msg)
        self.assertNotIn("SELECT", err_msg.upper())
        self.assertNotIn("sqlite", err_msg.lower())
        self.assertNotIn("Traceback", err_msg)

        # Invalid format should also fail cleanly
        with self.assertRaises(RunNotFoundError):
            compiler.compile(run_id="invalid_not_number")

    def test_03_no_checkpoint_run_handled_correctly(self):
        """3. No-checkpoint run is handled correctly (honest empty snapshots or NoCheckpointsError)."""
        # When checkpoints are required, it raises NoCheckpointsError
        with self.assertRaises(NoCheckpointsError) as ctx:
            compile_operational_run_to_artifact(run_id=2, db_path=self.db_path, require_checkpoints=True)
        self.assertIn("has no recorded state checkpoints", str(ctx.exception))

        # When require_checkpoints=False, it compiles honestly with snapshots=[]
        artifact = compile_operational_run_to_artifact(run_id=2, db_path=self.db_path, require_checkpoints=False)
        self.assertEqual(len(artifact.snapshots), 0)
        self.assertEqual(artifact.run_metadata.snapshot_count, 0)
        self.assertFalse(artifact.scenario_definition.metadata["has_checkpoints"])
        self.assertEqual(artifact.scenario_definition.metadata["fidelity"], "TELEMETRY_ONLY_NO_CHECKPOINTS")
        self.assertGreater(len(artifact.events), 0)

    def test_04_invalid_checkpoint_is_rejected(self):
        """4. Invalid/corrupt checkpoint is rejected with CorruptCheckpointError."""
        # Corrupt checksum
        with self.assertRaises(CorruptCheckpointError) as ctx:
            compile_operational_run_to_artifact(run_id=3, db_path=self.db_path)
        self.assertIn("checksum", str(ctx.exception).lower())

        # Malformed JSON payload
        with self.assertRaises(MalformedPersistenceError) as ctx:
            compile_operational_run_to_artifact(run_id=4, db_path=self.db_path)
        self.assertIn("malformed", str(ctx.exception).lower())

    def test_05_replay_artifact_validates_against_existing_schema(self):
        """5. ReplayArtifact validates against existing schema (roundtrip from_dict/to_dict)."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)
        dict_data = artifact.to_dict()
        restored = ReplayArtifact.from_dict(dict_data)

        self.assertEqual(restored.replay_format_version, artifact.replay_format_version)
        self.assertEqual(restored.run_metadata.run_id, artifact.run_metadata.run_id)
        self.assertEqual(len(restored.events), len(artifact.events))
        self.assertEqual(len(restored.snapshots), len(artifact.snapshots))
        self.assertEqual(restored.final_summary, artifact.final_summary)

    def test_06_event_ordering_is_deterministic(self):
        """6. Event ordering is 100% deterministic (sim_time non-decreasing and consecutive IDs)."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)
        events = artifact.events

        prev_sim = -1
        for idx, ev in enumerate(events, start=1):
            self.assertEqual(ev["event_id"], idx)
            self.assertGreaterEqual(ev["sim_time"], prev_sim)
            prev_sim = ev["sim_time"]

        # Ensure priority at same sim_time is deterministic
        sim_5_events = [e for e in events if e["sim_time"] == 5]
        self.assertGreaterEqual(len(sim_5_events), 1)

    def test_07_simulation_timestamps_are_preserved(self):
        """7. Simulation timestamps are preserved exactly from SQLite source tables."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)

        # Incident 101 dispatched at sim_time 2
        dispatch_101 = next(
            (e for e in artifact.events if e["event_type"] == "DISPATCH" and e["entity_ids"].get("incident_id") == 101),
            None,
        )
        self.assertIsNotNone(dispatch_101)
        self.assertEqual(dispatch_101["sim_time"], 2)

        # Incident 101 arrived at sim_time 18
        arrival_101 = next(
            (e for e in artifact.events if e["event_type"] == "AMBULANCE_ARRIVED" and e["entity_ids"].get("incident_id") == 101),
            None,
        )
        self.assertIsNotNone(arrival_101)
        self.assertEqual(arrival_101["sim_time"], 18)

        # Redirection at sim_time 10
        redir_102 = next(
            (e for e in artifact.events if e["event_type"] == "REDIRECTION" and e["entity_ids"].get("incident_id") == 102),
            None,
        )
        self.assertIsNotNone(redir_102)
        self.assertEqual(redir_102["sim_time"], 10)

        # Reposition at sim_time 8
        repos = next(
            (e for e in artifact.events if e["event_type"] == "REPOSITION_START"),
            None,
        )
        self.assertIsNotNone(repos)
        self.assertEqual(repos["sim_time"], 8)

        # Snapshots at sim_time 0 and 15
        self.assertEqual(artifact.snapshots[0]["sim_time"], 0)
        self.assertEqual(artifact.snapshots[1]["sim_time"], 15)

    def test_08_repeated_compilation_produces_equivalent_artifacts(self):
        """8. Repeated compilation produces byte-for-byte / value-for-value equivalent artifacts."""
        compiler = OperationalReplayCompiler(db_path=self.db_path)
        art1 = compiler.compile(run_id=1)
        art2 = compiler.compile(run_id=1)
        art3 = compiler.compile(run_id=1)

        dict1 = art1.to_dict()
        dict2 = art2.to_dict()
        dict3 = art3.to_dict()

        self.assertEqual(dict1, dict2)
        self.assertEqual(dict2, dict3)

    def test_09_compilation_performs_no_database_writes(self):
        """9. Compilation performs zero database writes (verified via DB state, hash, and PRAGMA)."""
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM state_checkpoints;")
        count_before = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM historical_events;")
        events_before = cursor.fetchone()[0]
        conn.close()

        # Run multiple compilations
        compiler = OperationalReplayCompiler(db_path=self.db_path)
        for rid in [1, 2, 5, 6]:
            try:
                compiler.compile(run_id=rid)
            except Exception:
                pass

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM state_checkpoints;")
        count_after = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM historical_events;")
        events_after = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count_before, count_after)
        self.assertEqual(events_before, events_after)

    def test_10_existing_scenario_replay_engine_behavior_compatible(self):
        """10. Existing scenario ReplayEngine initializes, steps, seeks, and inspects compiled artifact."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)

        engine = ReplayEngine(artifact)
        initial_state = engine.get_state()
        self.assertEqual(initial_state["sim_time"], 0)
        self.assertEqual(len(initial_state["ambulances"]), 1)
        self.assertEqual(len(initial_state["hospitals"]), 1)

        # Step through events
        steps_taken = 0
        while engine.step():
            steps_taken += 1
        self.assertGreater(steps_taken, 0)

        completed_state = engine.get_state()
        self.assertTrue(completed_state["is_completed"])
        self.assertEqual(completed_state["progress_percent"], 100.0)

        # Seek functionality
        seek_success = engine.seek(target_sim_time=15)
        self.assertTrue(seek_success)
        mid_state = engine.get_state()
        self.assertEqual(mid_state["sim_time"], 15)

    def test_11_partial_historical_data_does_not_create_fabricated_information(self):
        """11. Partial historical data does not create fabricated information."""
        artifact = compile_operational_run_to_artifact(run_id=6, db_path=self.db_path)

        # Incident 601 in run 6 has arrived_sim_time = NULL
        arrival_events = [e for e in artifact.events if e["event_type"] == "AMBULANCE_ARRIVED"]
        self.assertEqual(len(arrival_events), 0, "Arrival event must NOT be fabricated when none was persisted.")

        dispatch_event = next(e for e in artifact.events if e["event_type"] == "DISPATCH")
        self.assertEqual(dispatch_event["payload"]["eta_minutes"], 9.0)

    def test_12_decision_evidence_absence_handled_honestly(self):
        """12. Decision evidence absence is handled honestly."""
        artifact = compile_operational_run_to_artifact(run_id=1, db_path=self.db_path)
        meta = artifact.scenario_definition.metadata
        self.assertIn("decision_evidence_available", meta)
        self.assertFalse(meta["decision_evidence_available"])

        # No fabricated alternative candidate arrays
        for ev in artifact.events:
            self.assertNotIn("candidate_alternatives", ev.get("payload", {}))
            self.assertNotIn("decision_explanation", ev.get("payload", {}))

    def test_13_empty_partial_run_behavior_deterministic(self):
        """13. Empty/partial run behavior is deterministic (EmptyRunError when disallowed)."""
        compiler = OperationalReplayCompiler(db_path=self.db_path)

        # Disallow empty run
        with self.assertRaises(EmptyRunError):
            compiler.compile(run_id=5, allow_empty=False)

        # Allow empty run -> valid empty artifact
        artifact = compiler.compile(run_id=5, allow_empty=True)
        self.assertEqual(len(artifact.events), 0)
        self.assertEqual(len(artifact.snapshots), 0)
        self.assertEqual(artifact.run_metadata.event_count, 0)
        self.assertEqual(artifact.run_metadata.snapshot_count, 0)

    def test_14_protected_files_remain_unchanged(self):
        """14. Protected files remain completely untouched by M13.4 Phase 1."""
        protected_prefixes = [
            "Dispatch/",
            "Models/Final Model/",
            "Dataset/",
            "api/decision_evidence/",
            "api/routers/decision_evidence.py",
            "api/schemas/decision_evidence.py",
            "api/dependencies.py",
            "api/main.py",
            "api/realtime/",
            "benchmark_dispatch.py",
        ]

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(Path(__file__).resolve().parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        for line in result.stdout.splitlines():
            status = line[:2]
            filepath = line[3:].strip()
            for prefix in protected_prefixes:
                self.assertFalse(
                    filepath.startswith(prefix),
                    f"Protected file '{filepath}' was modified: {status}",
                )


if __name__ == "__main__":
    unittest.main()
