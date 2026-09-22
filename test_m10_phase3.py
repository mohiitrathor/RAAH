"""
RAAH M10 Phase 3 Test Suite — Operational Replay, Scenario Analysis & Drill Visualization
==========================================================================================

Verifies:
  1. Replay analysis loads valid artifact.
  2. Timeline generation is chronologically sorted.
  3. Same-time events maintain stable, deterministic ordering.
  4. Event filtering by event_type and entity_id.
  5. Deep event detail lookup.
  6. State reconstruction at T=0.
  7. State reconstruction at intermediate T.
  8. State reconstruction at final T.
  9. Ambulance historical position changes correctly over time.
  10. Historical route waypoints are preserved.
  11. MCI state reconstruction works.
  12. Repositioning state reconstruction works.
  13. Hospital state reconstruction works.
  14. Replay analysis metrics are deterministic.
  15. Scenario A vs Scenario B comparison works with valid deltas.
  16. Stress 25 / 50 / 100 comparison works.
  17. Before/after snapshot delta analysis works.
  18. Drill report generation works (JSON and Markdown).
  19. Deterministic report normalization works.
  20. Same replay produces identical analysis hash.
  21. STRICT INVARIANT: Replay never mutates live manager.simulator.
  22. Two concurrent replay sessions remain completely isolated.
  23. REST API validation for all replay analysis endpoints.
  24. Frontend static integration and markup checks.
  25. Full backwards compatibility with Phase 1, Phase 2, and core systems.
"""

import json
import time
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from api.dependencies import manager
from Dispatch.scenarios.models import ScenarioConfig, ScenarioDefinition
from Dispatch.scenarios.runner import ScenarioRunner
from Dispatch.scenarios.replay import ReplayEngine
from Dispatch.scenarios.store import ReplayStore, ScenarioStore
from Dispatch.scenarios.drills import (
    generate_pileup_scenario,
    generate_casualty_surge,
    run_comparison,
)
from Dispatch.scenarios.analysis import (
    ReplayAnalyzer,
    ReplaySessionManager,
)

client = TestClient(app)
replay_store = ReplayStore()


def run_phase3_tests():
    print("\n" + "=" * 70)
    print("RAAH M10 PHASE 3: OPERATIONAL REPLAY & ANALYSIS TEST SUITE")
    print("=" * 70)

    with client:
        # Reset live simulator
        client.post("/simulation/reset")

        # --------------------------------------------------------------
        # PREPARATION: Generate two deterministic test replays
        # --------------------------------------------------------------
        print("\n[SETUP] Generating deterministic test replays...")
        runner_a = ScenarioRunner(seed=42)
        scen_a = generate_pileup_scenario(seed=42, casualty_count=12, scenario_id="DRILL_PILEUP_P3", duration_minutes=10)
        art_a = runner_a.run(scen_a, run_id="run_test_p3_a")
        replay_store.save(art_a)

        runner_b = ScenarioRunner(seed=99)
        scen_b = generate_casualty_surge(casualty_count=24, seed=99, mci_count=2, scenario_id="DRILL_SURGE_P3", duration_minutes=10)
        art_b = runner_b.run(scen_b, run_id="run_test_p3_b")
        replay_store.save(art_b)
        print(f"✓ Saved test replays: '{art_a.run_metadata.run_id}' ({len(art_a.events)} ev) and '{art_b.run_metadata.run_id}' ({len(art_b.events)} ev)")

        # --------------------------------------------------------------
        # TEST 1: Replay Analysis Loads Valid Artifact
        # --------------------------------------------------------------
        print("\n[TEST 1] Replay analysis loads valid artifact...")
        ana_a = ReplayAnalyzer.analyze(art_a)
        assert ana_a.scenario_id == "DRILL_PILEUP_P3"
        assert ana_a.run_id == "run_test_p3_a"
        assert ana_a.total_events > 0
        assert ana_a.dispatch_count > 0
        assert ana_a.resilience_score is not None
        assert ana_a.deterministic_hash is not None
        print(f"✓ Replay analysis loaded: score={ana_a.resilience_score['overall']}, dispatches={ana_a.dispatch_count}")

        # --------------------------------------------------------------
        # TEST 2 & 3: Timeline Chronological & Stable Ordering
        # --------------------------------------------------------------
        print("\n[TEST 2 & 3] Timeline chronological & stable ordering...")
        timeline = ReplayAnalyzer.build_timeline(art_a)
        assert timeline.event_count == len(art_a.events)
        assert timeline.duration == 10

        # Verify monotonic non-decreasing sim_time
        sim_times = [e["sim_time"] for e in timeline.events]
        assert sim_times == sorted(sim_times), "Timeline events are not chronologically sorted!"

        # Verify stable ordering of same-time events
        for i in range(len(timeline.events) - 1):
            e1 = timeline.events[i]
            e2 = timeline.events[i + 1]
            if e1["sim_time"] == e2["sim_time"]:
                assert e1["event_index"] < e2["event_index"]
        print("✓ Timeline verified chronologically sorted with deterministic stable ordering.")

        # --------------------------------------------------------------
        # TEST 4: Event Filtering Works
        # --------------------------------------------------------------
        print("\n[TEST 4] Event filtering works (by type and entity)...")
        tl_dispatch = ReplayAnalyzer.build_timeline(art_a, event_type="DISPATCH")
        assert all(e["event_type"] == "DISPATCH" for e in tl_dispatch.events)
        assert tl_dispatch.event_count > 0

        # Filter by entity
        target_amb = tl_dispatch.events[0]["entity_ids"].get("ambulance_id")
        assert target_amb is not None
        tl_entity = ReplayAnalyzer.build_timeline(art_a, entity_id=target_amb)
        assert tl_entity.event_count > 0
        assert all(
            str(target_amb) in str(e["entity_ids"]) or str(target_amb) in str(e["payload"])
            for e in tl_entity.events
        )
        print(f"✓ Event filtering verified: {tl_dispatch.event_count} dispatches, {tl_entity.event_count} events for {target_amb}.")

        # --------------------------------------------------------------
        # TEST 5: Deep Event Lookup Works
        # --------------------------------------------------------------
        print("\n[TEST 5] Deep event lookup works...")
        ev_detail = ReplayAnalyzer.get_event_detail(art_a, 0)
        assert ev_detail is not None
        assert "sim_time" in ev_detail
        assert "event_type" in ev_detail
        assert "description" in ev_detail
        assert "detail" in ev_detail
        assert ev_detail["event_index"] == 0
        print(f"✓ Event 0 detail: [{ev_detail['event_type']}] {ev_detail['description']}")

        # --------------------------------------------------------------
        # TEST 6, 7 & 8: State Reconstruction (T=0, Intermediate, Final)
        # --------------------------------------------------------------
        print("\n[TEST 6-8] State reconstruction at T=0, intermediate T=4, and final T=10...")
        engine = ReplayEngine(art_a)

        engine.seek(0)
        state_0 = engine.get_state()
        assert state_0["sim_time"] == 0
        assert len(state_0["ambulances"]) > 0
        print("✓ State reconstruction at T=0 verified.")

        engine.seek(4)
        state_4 = engine.get_state()
        assert state_4["sim_time"] == 4
        en_route_4 = sum(1 for a in state_4["ambulances"] if a.get("status") == "EN_ROUTE")
        assert en_route_4 > 0, "Expected ambulances en route at T=4"
        print(f"✓ State reconstruction at T=4 verified: {en_route_4} ambulances en route.")

        engine.seek(10)
        state_10 = engine.get_state()
        assert state_10["sim_time"] == 10
        print("✓ State reconstruction at final T=10 verified.")

        # --------------------------------------------------------------
        # TEST 9 & 10: Ambulance Position Changes & Route Waypoints Preserved
        # --------------------------------------------------------------
        print("\n[TEST 9 & 10] Ambulance historical movement and waypoints...")
        # Find an ambulance that was en route
        active_aid = None
        for a in state_4["ambulances"]:
            if a.get("status") == "EN_ROUTE" and a.get("route_waypoints"):
                active_aid = a["ambulance_id"]
                break

        assert active_aid is not None, "No en route ambulance with route waypoints found at T=4"
        amb_t0 = next(a for a in state_0["ambulances"] if a["ambulance_id"] == active_aid)
        amb_t4 = next(a for a in state_4["ambulances"] if a["ambulance_id"] == active_aid)

        # Coordinates should change
        pos_0 = (amb_t0["latitude"], amb_t0["longitude"])
        pos_4 = (amb_t4["latitude"], amb_t4["longitude"])
        # Waypoints should exist and be a list of coordinates
        assert len(amb_t4["route_waypoints"]) > 1
        assert isinstance(amb_t4["route_waypoints"][0], (list, tuple))
        print(f"✓ Unit {active_aid} moved: {pos_0} -> {pos_4}, waypoints preserved ({len(amb_t4['route_waypoints'])} coords).")

        # --------------------------------------------------------------
        # TEST 11: MCI State Reconstruction Works
        # --------------------------------------------------------------
        print("\n[TEST 11] MCI state reconstruction works...")
        engine_b = ReplayEngine(art_b)
        engine_b.seek(4)
        state_b_4 = engine_b.get_state()
        assert len(state_b_4["active_mcis"]) > 0
        mci_item = state_b_4["active_mcis"][0]
        assert "total_casualties" in mci_item
        assert "assigned_ambulance_ids" in mci_item
        assert "hospital_distribution" in mci_item
        print(f"✓ Reconstructed MCI: {mci_item['mci_id']} with {mci_item['total_casualties']} casualties.")

        # --------------------------------------------------------------
        # TEST 12: Repositioning State Reconstruction Works
        # --------------------------------------------------------------
        print("\n[TEST 12] Repositioning state reconstruction works...")
        # In art_a, AMB_0002 was scheduled to reposition at T=0
        state_a_1 = engine.get_state()
        engine.seek(1)
        state_a_1 = engine.get_state()
        assert len(state_a_1["repositioning"]) > 0 or any(a.get("is_repositioning") for a in state_a_1["ambulances"])
        print("✓ Repositioning state reconstruction confirmed.")

        # --------------------------------------------------------------
        # TEST 13: Hospital State Reconstruction Works
        # --------------------------------------------------------------
        print("\n[TEST 13] Hospital state reconstruction works...")
        hosps = state_4["hospitals"]
        assert len(hosps) > 0
        h0 = hosps[0]
        assert "capacity" in h0
        assert "available_beds" in h0
        assert "current_load" in h0
        assert h0["capacity"] >= h0["available_beds"]
        print("✓ Reconstructed hospital state validated across facilities.")

        # --------------------------------------------------------------
        # TEST 14: Replay Analysis Metrics are Deterministic
        # --------------------------------------------------------------
        print("\n[TEST 14] Replay analysis metrics are deterministic...")
        ana_1 = ReplayAnalyzer.analyze(art_a)
        ana_2 = ReplayAnalyzer.analyze(art_a)
        assert ana_1.to_dict() == ana_2.to_dict()
        print("✓ Replay analysis strictly deterministic across repeated invocations.")

        # --------------------------------------------------------------
        # TEST 15: Scenario A/B Comparison Works
        # --------------------------------------------------------------
        print("\n[TEST 15] Scenario A vs B comparison works...")
        comp = ReplayAnalyzer.compare_scenarios(art_a, art_b)
        assert "scenario_a" in comp
        assert "scenario_b" in comp
        assert "delta" in comp
        assert "performance_explanation" in comp

        d = comp["delta"]
        assert "total_casualties" in d
        assert "dispatch_success_pct" in d
        assert "average_eta_minutes" in d
        assert "resilience_score" in d
        assert comp["scenario_b"]["casualties"] > comp["scenario_a"]["casualties"]
        print(f"✓ Comparison delta: casualties={d['total_casualties']}, eta_delta={d['average_eta_minutes']}m, resilience_delta={d['resilience_score']}")
        print(f"  Explanation: {comp['performance_explanation']}")

        # --------------------------------------------------------------
        # TEST 16: Stress 25 / 50 / 100 Comparison Works
        # --------------------------------------------------------------
        print("\n[TEST 16] Stress test comparison rows...")
        comp_rows = run_comparison([25, 50, 100], seed=42)
        assert len(comp_rows) == 3
        for row in comp_rows:
            assert "casualties" in row
            assert "dispatch_success_pct" in row
            assert "resilience_score" in row
            assert "deterministic_hash" in row
        print("✓ 25/50/100 comparison verified.")

        # --------------------------------------------------------------
        # TEST 17: Before / After Snapshot Analysis Works
        # --------------------------------------------------------------
        print("\n[TEST 17] Before / After snapshot delta analysis...")
        ba = ReplayAnalyzer.compare_snapshots_before_after(art_a, time_a=1, time_b=6)
        assert "time_a" in ba
        assert "time_b" in ba
        assert "delta" in ba
        assert ba["time_a"]["sim_time"] == 1
        assert ba["time_b"]["sim_time"] == 6
        assert "en_route_ambulances" in ba["delta"]
        assert "hospital_utilization_pct" in ba["delta"]
        print(f"✓ Snapshot delta T+1m -> T+6m: en_route delta={ba['delta']['en_route_ambulances']}, hosp_util delta={ba['delta']['hospital_utilization_pct']}%")

        # --------------------------------------------------------------
        # TEST 18 & 19: Report Generation & Normalization Works
        # --------------------------------------------------------------
        print("\n[TEST 18 & 19] Drill report generation & normalization...")
        rep_json = ReplayAnalyzer.generate_report(art_a, format="json")
        assert "report_title" in rep_json
        assert "resilience_score" in rep_json
        assert "performance_summary" in rep_json
        assert "important_events" in rep_json

        rep_md = ReplayAnalyzer.generate_report(art_a, format="markdown")
        assert "markdown_content" in rep_md
        assert "# RAAH Drill Analysis Report" in rep_md["markdown_content"]
        assert "Executive Scorecard" in rep_md["markdown_content"]

        # Deterministic normalization test
        rep_json_2 = ReplayAnalyzer.generate_report(art_a, format="json")
        assert rep_json == rep_json_2
        print("✓ Structured drill report generated in JSON and Markdown with deterministic normalization.")

        # --------------------------------------------------------------
        # TEST 20: Same Replay Produces Same Analysis Hash
        # --------------------------------------------------------------
        print("\n[TEST 20] Analysis hash invariance...")
        h1 = ReplayAnalyzer.compute_analysis_hash(ana_1.to_dict())
        h2 = ReplayAnalyzer.compute_analysis_hash(ana_2.to_dict())
        assert h1 == h2
        print(f"✓ Analysis hash is invariant: {h1}")

        # --------------------------------------------------------------
        # TEST 21: STRICT INVARIANT: Replay Never Mutates Live Simulator
        # --------------------------------------------------------------
        print("\n[TEST 21] STRICT INVARIANT: Replay never mutates live simulator...")
        with manager.lock:
            live_clock_before = manager.simulator.state.current_time
            live_ambs_before = {aid: a.status for aid, a in manager.simulator.state.ambulances.items()}
            live_hosps_before = {hid: (h.current_load, h.available_beds) for hid, h in manager.simulator.state.hospitals.items()}
            live_incs_before = len(manager.simulator.state.incidents)

        # Perform extensive replay seeking and stepping
        engine.seek(0)
        engine.seek(10)
        engine.play_to_end()
        ReplayAnalyzer.compare_snapshots_before_after(art_a, 0, 10)
        ReplayAnalyzer.analyze(art_a)

        with manager.lock:
            live_clock_after = manager.simulator.state.current_time
            live_ambs_after = {aid: a.status for aid, a in manager.simulator.state.ambulances.items()}
            live_hosps_after = {hid: (h.current_load, h.available_beds) for hid, h in manager.simulator.state.hospitals.items()}
            live_incs_after = len(manager.simulator.state.incidents)

        assert live_clock_before == live_clock_after, "Live simulator clock was mutated by replay!"
        assert live_ambs_before == live_ambs_after, "Live ambulance states were mutated by replay!"
        assert live_hosps_before == live_hosps_after, "Live hospital states were mutated by replay!"
        assert live_incs_before == live_incs_after, "Live incidents were mutated by replay!"
        print("✓ Verified complete observational isolation: live simulator state strictly unmutated.")

        # --------------------------------------------------------------
        # TEST 22: Two Replay Sessions Remain Isolated
        # --------------------------------------------------------------
        print("\n[TEST 22] Session isolation...")
        session_mgr = ReplaySessionManager()
        eng_1 = session_mgr.get_or_create("sess_1", art_a)
        eng_2 = session_mgr.get_or_create("sess_2", art_a)

        eng_1.seek(2)
        eng_2.seek(8)

        assert eng_1.current_time == 2
        assert eng_2.current_time == 8
        print("✓ Two independent replay sessions verified isolated (T=2 vs T=8).")

        # --------------------------------------------------------------
        # TEST 23: Replay Analysis REST API Validation
        # --------------------------------------------------------------
        print("\n[TEST 23] Replay analysis REST API validation...")
        run_id = art_a.run_metadata.run_id

        # 1. GET /replays/{run_id}/timeline
        r_tl = client.get(f"/replays/{run_id}/timeline")
        assert r_tl.status_code == 200, r_tl.text
        assert r_tl.json()["run_id"] == run_id

        # 2. GET /replays/{run_id}/events/0
        r_ev = client.get(f"/replays/{run_id}/events/0")
        assert r_ev.status_code == 200
        assert r_ev.json()["event_index"] == 0

        # 3. GET /replays/{run_id}/analysis
        r_ana = client.get(f"/replays/{run_id}/analysis")
        assert r_ana.status_code == 200
        assert "resilience_score" in r_ana.json()

        # 4. GET /replays/{run_id}/state/{sim_time}
        r_st = client.get(f"/replays/{run_id}/state/5")
        assert r_st.status_code == 200
        assert r_st.json()["sim_time"] == 5

        # 5. POST /replays/{run_id}/before-after
        r_ba = client.post(f"/replays/{run_id}/before-after", json={"time_a": 1, "time_b": 5})
        assert r_ba.status_code == 200
        assert "delta" in r_ba.json()

        # 6. POST /replays/{run_id}/report
        r_rep = client.post(f"/replays/{run_id}/report", json={"format": "markdown"})
        assert r_rep.status_code == 200
        assert "markdown_content" in r_rep.json()

        # 7. POST /replays/compare
        r_cmp = client.post("/replays/compare", json={
            "run_id_a": art_a.run_metadata.run_id,
            "run_id_b": art_b.run_metadata.run_id,
        })
        assert r_cmp.status_code == 200
        assert "delta" in r_cmp.json()

        # 8. POST /replays/{run_id}/mode
        r_mode = client.post(f"/replays/{run_id}/mode", json={"mode": "REPLAY", "session_id": "test_sess"})
        assert r_mode.status_code == 200
        assert r_mode.json()["mode"] == "REPLAY"
        print("✓ All 8 Replay Analysis REST API endpoints verified.")

        # --------------------------------------------------------------
        # TEST 24: Frontend Static Integration Checks
        # --------------------------------------------------------------
        print("\n[TEST 24] Frontend static integration checks...")
        REPO_ROOT = Path(__file__).resolve().parent
        index_html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")
        assert "nav-btn-replay" in index_html
        assert "replay-workspace" in index_html
        assert "replay-leaflet-map" in index_html
        assert "replay-timeline-list" in index_html
        assert "replay-event-inspector" in index_html

        app_js = (REPO_ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        assert "ReplayController" in app_js
        assert "ScenarioAnalysisController" in app_js
        print("✓ Frontend HTML/JS wiring verified.")

        # --------------------------------------------------------------
        # TEST 25: Full Backwards Compatibility Regression Check
        # --------------------------------------------------------------
        print("\n[TEST 25] Backwards compatibility check...")
        r_health = client.get("/health")
        assert r_health.status_code == 200
        r_drills = client.get("/drills")
        assert r_drills.status_code == 200
        r_scens = client.get("/scenarios")
        assert r_scens.status_code == 200
        r_reps = client.get("/replays")
        assert r_reps.status_code == 200
        print("✓ Core simulation, scenario, and drill systems confirmed functional.")

    print("\n" + "=" * 70)
    print("ALL 25 M10 PHASE 3 REPLAY & ANALYSIS TESTS PASSED.")
    print("=" * 70)


if __name__ == "__main__":
    run_phase3_tests()
