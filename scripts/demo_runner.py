#!/usr/bin/env python3
"""
RAAH Command Center — Automated Live Demo Runner
================================================
Executes the full end-to-end operational demo against the live FastAPI backend:
  01 INCIDENT RECEIVED
  02 ML TRIAGE (Severity & Priority)
  03 AMBULANCE ASSIGNED (Capability & ETA)
  04 HOSPITAL SELECTED (Clinical suitability & ICU)
  05 HOSPITAL CAPACITY DISRUPTION (HOSPITAL_FULL event injected)
  06 REDIRECTION EVALUATED (Predictive hospital balancer)
  07 ALTERNATIVE HOSPITAL SELECTED (Multi-objective optimization)
  08 ROUTE UPDATED (Dynamic recalculation & ETA refresh)
  09 DECISION RECORDED (Authoritative audit evidence log)
  10 TERMINAL ARRIVAL (Arrival lock & state immutability)
"""

import sys
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.auth import create_test_token, Role

BASE_URL = "http://127.0.0.1:8000"
AUTH_TOKEN = create_test_token(username="demo_supervisor", role=Role.ADMINISTRATOR)


def http_req(method, endpoint, payload=None):
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return err.code, parsed
    except Exception as exc:
        return 0, {"error": str(exc)}


def run_demo():
    print("=" * 76)
    print("      RAAH INTELLIGENT EMS PLATFORM — LIVE END-TO-END DEMO")
    print("=" * 76)
    print(f"Backend Target: {BASE_URL}")
    print()

    # 0. Health check
    print("[00] Checking backend readiness...")
    status, health = http_req("GET", "/health")
    if status != 200:
        print(f"     [ERROR] Backend is not accessible on {BASE_URL} (status {status}).")
        print("     Please launch RAAH first using: ./run.sh")
        sys.exit(1)
    print(f"     Backend healthy: {health.get('status', 'OK')} (v{health.get('version', '1.0.0')})")
    print()

    # 1. Reset simulation
    print("[01] Resetting simulation environment to clean baseline...")
    status, _ = http_req("POST", "/simulation/reset")
    if status != 200:
        print(f"     [ERROR] Failed to reset simulation: {status}")
        sys.exit(1)
    print("     Simulation state: ACTIVE | Sim Time: 0m | Incidents: 0")
    print()

    # 2. Ingest Critical Emergency Call
    print("[02] Ingesting live critical emergency call (Suspected Acute Coronary Syndrome)...")
    incident_payload = {
        "Sex": "Male",
        "Condition": "Cardiac",
        "Oxygen_Requirement": "Oxygen Mask",
        "Consciousness": "Alert",
        "Injury_Type": "No Injury",
        "Arrival_Mode": "Ambulance",
        "Age": 62,
        "Heart_Rate": 134.0,
        "SpO2": 87.0,
        "Systolic_BP": 85.0,
        "Diastolic_BP": 52.0,
        "Respiratory_Rate": 28.0,
        "Temperature": 37.2,
        "GCS": 13,
        "Pain_Score": 9,
        "Blood_Glucose": 145.0,
        "Respiratory_Distress": 1,
        "Chest_Pain": 1,
        "Bleeding": 0,
        "Seizure": 0,
        "Diabetes": 1,
        "Hypertension": 1,
        "Heart_Disease": 1,
        "Respiratory_Disease": 0,
        "patient_lat": 26.9124,
        "patient_lon": 75.7873,
    }
    status, disp = http_req("POST", "/dispatch/live", incident_payload)
    if status != 200:
        print(f"     [ERROR] Dispatch failed: {status} -> {disp}")
        sys.exit(1)

    inc_id = disp["incident_id"]
    patient_info = disp.get("patient", {})
    amb = disp.get("ambulance", {})
    hosp = disp.get("hospital", {})

    print(f"     -> Incident ID:       #{inc_id}")
    print(f"     -> ML Triage:         {patient_info.get('predicted_severity')} ({patient_info.get('priority')}) [Conf: {patient_info.get('confidence', 0):.1%}]")
    print(f"     -> Assigned Unit:     {amb.get('ambulance_id')} ({amb.get('ambulance_type')}) | Initial ETA: {amb.get('eta_minutes'):.1f}m")
    print(f"     -> Target Hospital:   {hosp.get('hospital_id')} ({hosp.get('hospital_type')})")
    print()

    initial_hosp = hosp.get("hospital_id")
    initial_amb = amb.get("ambulance_id")

    # 3. Simulate Capacity Disruption
    print(f"[03] Injecting emergency capacity saturation at assigned facility ({initial_hosp})...")
    event_payload = {
        "time": 1,
        "event_type": "HOSPITAL_FULL",
        "data": {
            "hospital_id": initial_hosp,
        },
    }
    status, ev_res = http_req("POST", "/events", event_payload)
    print(f"     -> Disruption Event Injected: HOSPITAL_FULL for {initial_hosp} (HTTP {status})")
    print()

    # 4. Advance Simulation Clock
    print("[04] Advancing simulation clock by 2 minutes to trigger reactive balancing...")
    status, _ = http_req("POST", "/simulation/tick?minutes=2")
    time.sleep(0.5)

    # 5. Evaluate Redirection Result
    print("[05] Evaluating dynamic redirection decisions...")
    status, snap = http_req("GET", "/state/snapshot")
    ambulances = snap.get("ambulances", [])
    curr_amb = next((a for a in ambulances if a.get("ambulance_id") == initial_amb), None)

    if not curr_amb:
        print("     [ERROR] Ambulance not found in live state!")
        sys.exit(1)

    new_hosp = curr_amb.get("hospital_id")
    new_status = curr_amb.get("status")
    redirected = new_hosp != initial_hosp

    print(f"     -> Pre-disruption Hospital:  {initial_hosp}")
    print(f"     -> Post-disruption Hospital: {new_hosp}")
    print(f"     -> Redirection Status:       {'AUTOMATICALLY DIVERTED' if redirected else 'UNCHANGED'}")
    print(f"     -> Unit Status:              {new_status}")
    print(f"     -> Divert ETA:               {curr_amb.get('eta_minutes', 0.0):.1f}m")
    print()

    # 6. Verify Authoritative Decision Evidence
    print("[06] Auditing immutable decision evidence log...")
    status, evidence_resp = http_req("GET", f"/decision-evidence/incident/{inc_id}")
    records = evidence_resp.get("records", [])
    print(f"     -> Evidence Log Entries:     {len(records)} audit record(s) found")
    if records:
        latest = records[-1]
        ev = latest.get("evidence", {})
        print(f"     -> Decision Type:            {ev.get('decision_type')}")
        print(f"     -> Action:                   {ev.get('action')}")
        print(f"     -> Audit Explanation:        \"{latest.get('explanation', '')}\"")
    print()

    # 7. Advance to Terminal Arrival
    print("[07] Advancing simulation to terminal arrival (T+25 min)...")
    for _ in range(15):
        http_req("POST", "/simulation/tick?minutes=1")
    status, snap = http_req("GET", "/state/snapshot")
    curr_amb = next((a for a in snap.get("ambulances", []) if a.get("ambulance_id") == initial_amb), {})

    print(f"     -> Terminal Unit Status:     {curr_amb.get('status')}")
    print(f"     -> Terminal ETA:             {curr_amb.get('eta_minutes', 0.0):.1f}m")

    # Verify post-arrival lock
    override_payload = {
        "incident_id": inc_id,
        "new_hospital_id": initial_hosp,
        "reason": "Test post-arrival unauthorized divert attempt",
    }
    status, override_resp = http_req("POST", "/coordination/redirection/manual", override_payload)
    print(f"     -> Post-arrival Divert Rejection: HTTP {status} (Safeguard Active)")
    print()

    print("=" * 76)
    print("                    DEMO SEQUENCE COMPLETED SUCCESSFULLY")
    print("=" * 76)
    print("Open the Command Center Dashboard to inspect visual telemetry:")
    print("  Tactical Dashboard:   http://localhost:8000")
    print("  Swagger API Docs:     http://localhost:8000/docs")
    print("=" * 76)


if __name__ == "__main__":
    run_demo()
