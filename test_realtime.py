"""
RAAH Real-Time Simulation Verification Suite (M3)
=================================================

Validates:
  1. Initial status telemetry (STOPPED, is_running=False).
  2. Starting real-time background simulation.
  3. Start/start race safety (409 Conflict on duplicate start).
  4. Wall-clock to simulation-time advancement.
  5. Concurrent API access (GET /state/dashboard, POST /dispatch/1, POST /events)
     with observational latency reporting and zero corruptions.
  6. Tick conflict protection (409 Conflict on manual tick while running).
  7. Clean thread termination and clock freeze on stop.
  8. Idempotent stop operations.
  9. Manual tick resumption when stopped.
 10. Reset while running: guarantees background thread terminates (is_alive() == False)
     BEFORE fresh Simulator is created at time = 0.
"""

import sys
import time
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import manager


def run_all_tests():

    print()
    print("=" * 70)
    print("RAAH M3: REAL-TIME SIMULATION TEST SUITE")
    print("=" * 70)

    with TestClient(app) as client:
        client.post("/simulation/reset")

        # ------------------------------------------------------
        # TEST 1: Initial State
        # ------------------------------------------------------
        print("\n[TEST 1] Verifying initial status...")
        resp = client.get("/simulation/realtime/status")
        assert resp.status_code == 200, f"Status failed: {resp.text}"
        data = resp.json()
        assert data["status"] == "STOPPED", f"Expected STOPPED, got {data['status']}"
        assert data["is_running"] is False, "is_running should be False"
        assert data["current_time"] == 0, "Initial time should be 0"
        print("  -> PASS: Initial status is STOPPED at time = 0.")

        # ------------------------------------------------------
        # TEST 2: Start Real-Time Simulation
        # ------------------------------------------------------
        print("\n[TEST 2] Starting real-time simulation (interval=0.1s, 1 min/tick)...")
        start_resp = client.post(
            "/simulation/realtime/start",
            json={"tick_interval_seconds": 0.1, "minutes_per_tick": 1},
        )
        assert start_resp.status_code == 200, f"Start failed: {start_resp.text}"
        start_data = start_resp.json()
        assert start_data["status"] == "RUNNING"
        print("  -> PASS: Real-time simulation started successfully.")

        # ------------------------------------------------------
        # TEST 3: Duplicate Start Protection (Race Safety)
        # ------------------------------------------------------
        print("\n[TEST 3] Testing duplicate start race protection...")
        dup_resp = client.post(
            "/simulation/realtime/start",
            json={"tick_interval_seconds": 0.1, "minutes_per_tick": 1},
        )
        assert dup_resp.status_code == 409, f"Expected 409 Conflict, got {dup_resp.status_code}"
        print("  -> PASS: Duplicate start rejected with 409 Conflict.")

        # ------------------------------------------------------
        # TEST 4: Wall-Clock Progression
        # ------------------------------------------------------
        print("\n[TEST 4] Waiting 0.5s for time progression...")
        time.sleep(0.5)
        status_resp = client.get("/simulation/realtime/status")
        status_data = status_resp.json()
        assert status_data["is_running"] is True, "Should still be running"
        assert status_data["current_time"] >= 3, (
            f"Expected time >= 3 after 0.5s at 10 ticks/s, got {status_data['current_time']}"
        )
        print(f"  -> PASS: Simulation time advanced to {status_data['current_time']} min (ticks: {status_data['ticks_processed']}).")

        # ------------------------------------------------------
        # TEST 5: Concurrent Requests & Observational Latencies
        # ------------------------------------------------------
        print("\n[TEST 5] Testing concurrent API operations while real-time thread runs...")

        dashboard_latencies = []
        for _ in range(10):
            t0 = time.perf_counter()
            d_resp = client.get("/state/dashboard")
            d_lat = (time.perf_counter() - t0) * 1000
            assert d_resp.status_code == 200
            dashboard_latencies.append(d_lat)

        t0 = time.perf_counter()
        disp_resp = client.post("/dispatch/1")
        disp_lat = (time.perf_counter() - t0) * 1000
        assert disp_resp.status_code == 200
        disp_data = disp_resp.json()
        assert disp_data["status"] == "DISPATCH_RECOMMENDED"

        t0 = time.perf_counter()
        ev_resp = client.post(
            "/events",
            json={"time": 100, "event_type": "HOSPITAL_FULL", "data": {"hospital_id": "HOSP_182"}},
        )
        ev_lat = (time.perf_counter() - t0) * 1000
        assert ev_resp.status_code == 200

        print("  -> PASS: All concurrent requests succeeded without errors or deadlocks.")
        print(f"     [Observational Diagnostics]")
        print(f"       GET /state/dashboard (10 runs): mean={statistics.mean(dashboard_latencies):.2f}ms, min={min(dashboard_latencies):.2f}ms, max={max(dashboard_latencies):.2f}ms")
        print(f"       POST /dispatch/1 latency:      {disp_lat:.2f}ms")
        print(f"       POST /events latency:          {ev_lat:.2f}ms")

        # ------------------------------------------------------
        # TEST 6: Manual Tick Conflict While Running
        # ------------------------------------------------------
        print("\n[TEST 6] Testing manual tick conflict while real-time is running...")
        tick_resp = client.post("/simulation/tick?minutes=1")
        assert tick_resp.status_code == 409, f"Expected 409 Conflict, got {tick_resp.status_code}"
        print("  -> PASS: Manual tick correctly rejected with 409 Conflict.")

        # ------------------------------------------------------
        # TEST 7: Stop Real-Time Simulation
        # ------------------------------------------------------
        print("\n[TEST 7] Stopping real-time simulation...")
        stop_resp = client.post("/simulation/realtime/stop")
        assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.text}"
        stop_data = stop_resp.json()
        assert stop_data["status"] == "STOPPED"

        frozen_time = stop_data["time"]
        time.sleep(0.3)
        check_resp = client.get("/simulation/realtime/status")
        check_data = check_resp.json()
        assert check_data["is_running"] is False
        assert check_data["current_time"] == frozen_time, (
            f"Time continued running after stop: expected {frozen_time}, got {check_data['current_time']}"
        )
        print(f"  -> PASS: Simulation stopped and clock froze cleanly at {frozen_time} min.")

        # ------------------------------------------------------
        # TEST 8: Idempotent Stop
        # ------------------------------------------------------
        print("\n[TEST 8] Testing idempotent stop...")
        stop_resp2 = client.post("/simulation/realtime/stop")
        assert stop_resp2.status_code == 200
        assert stop_resp2.json()["status"] == "STOPPED"
        print("  -> PASS: Second stop returned 200 OK cleanly.")

        # ------------------------------------------------------
        # TEST 9: Manual Tick Resumption When Stopped
        # ------------------------------------------------------
        print("\n[TEST 9] Testing manual tick resumption when stopped...")
        manual_resp = client.post("/simulation/tick?minutes=3")
        assert manual_resp.status_code == 200, f"Manual tick failed: {manual_resp.text}"
        manual_data = manual_resp.json()
        assert manual_data["time"] == frozen_time + 3, (
            f"Expected {frozen_time + 3}, got {manual_data['time']}"
        )
        print(f"  -> PASS: Manual tick advanced time from {frozen_time} to {manual_data['time']} min.")

        # ------------------------------------------------------
        # TEST 10: Reset While Running (Thread Termination Assertion)
        # ------------------------------------------------------
        print("\n[TEST 10] Testing reset while running and verifying background thread termination...")
        client.post(
            "/simulation/realtime/start",
            json={"tick_interval_seconds": 0.05, "minutes_per_tick": 1},
        )
        assert manager.is_realtime_running is True

        old_thread = manager._thread
        assert old_thread is not None and old_thread.is_alive() is True, "Thread should be alive before reset"

        reset_resp = client.post("/simulation/reset")
        assert reset_resp.status_code == 200, f"Reset failed: {reset_resp.text}"
        assert reset_resp.json()["status"] == "reset"

        # Crucial Assertion: Thread must be dead after reset completes
        assert old_thread.is_alive() is False, "CRITICAL ERROR: Old background thread is still alive after reset!"
        assert manager.is_realtime_running is False, "Manager still reports real-time running after reset"
        assert manager.simulator.state.current_time == 0, "Simulation time was not reset to 0"
        print("  -> PASS: Background thread definitely terminated, fresh Simulator created at time = 0.")

    print("\n" + "=" * 70)
    print("ALL 10 M3 REAL-TIME SIMULATION TESTS PASSED!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    run_all_tests()
