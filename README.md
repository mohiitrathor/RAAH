# RAAH — Emergency Medical Dispatch & Tactical Coordination Platform

RAAH is an intelligent Emergency Medical Services (EMS) dispatch and tactical resource coordination platform designed to optimize emergency response during acute incidents and mass-casualty events. The platform combines machine learning for clinical severity prediction with deterministic, explainable algorithms for fleet allocation, hospital destination routing, and dynamic patient redirection.

RAAH features a zero-build, real-time tactical Command Center dashboard providing live incident triage, fleet tracking, automated decision explainability, operational replay scrubbing, and retrospective post-incident review (PIR).

---

## Architecture Overview

RAAH maintains a strict architectural separation of concerns between clinical inference, operational decision-making, state management, and external integration:

```
External CAD / Telemetry              Live Dispatch Intake (UI)
           │                                      │
           ▼                                      ▼
┌─────────────────────────────────────────────────────────────┐
│                 Ingestion & Adapters Layer                  │
│  - M2M API Key & Scoped Provider Authorization              │
│  - CAD Triage Mapper (Zero Fabricated Physiology)           │
└──────────────────────────────┬──────────────────────────────┘
                               │ Normalized Clinical Vector (24 Features)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 Clinical ML Inference Layer                 │
│  - Protected Scikit-Learn Model (Trained Logistic Regr.)    │
│  - Outputs Severity (Critical..Non-Urgent) & Priority (P1..P4)
└──────────────────────────────┬──────────────────────────────┘
                               │ Predicted Severity & Priority
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             Deterministic Dispatch & Routing Engine         │
│  - Rule-Based Capability Matching (ALS vs. BLS)             │
│  - Proximity Scoring & Travel Time Estimation               │
│  - Receiving Hospital Department & Capacity Matching        │
│  - En-Route Redirection Engine (Capacity Surge Divert)      │
└──────────────────────────────┬──────────────────────────────┘
                               │ State Mutation & Decision Evidence
                               ▼
┌─────────────────────────────────────────────────────────────┐
│           Authoritative DispatchState & Persistence         │
│  - Single Source of Truth under Thread Lock                 │
│  - SQLite Event Telemetry & Periodic State Checkpoints      │
│  - Read-Only Replay Analysis Engine (PRAGMA query_only=ON)  │
└──────────────────────────────┬──────────────────────────────┘
                               │ Event Broadcast
                               ▼
┌─────────────────────────────────────────────────────────────┐
│               Real-Time Command Center (SSE)                │
│  - Server-Sent Events (/events/stream)                      │
│  - Tactical Leaflet GIS Map, Hospital Occupancy Meters      │
│  - Decision Evidence Modal & PIR Run Comparison             │
└─────────────────────────────────────────────────────────────┘
```

### Core Subsystems

1. **Clinical Machine Learning Layer (`Models/Final Model/Predict_Severity.py`)**:
   - Evaluates a calibrated, 24-feature clinical vector (patient age, vital signs, GCS, condition, respiratory distress, pain score, etc.).
   - Predicts clinical severity (`Critical`, `Emergency`, `Urgent`, `Non-Urgent`), priority level (`P1`–`P4`), and calibrated class probabilities.
   - The trained model artifact (`logistic_regression_final.joblib`) is strictly immutable.
2. **Deterministic Dispatch & Resource Coordination Layer (`Dispatch/dispatch_engine.py`)**:
   - Translates clinical priority into tactical vehicle allocation. Critical and Emergency patients (P1/P2) receive Advanced Life Support (ALS) units; lower-acuity patients (P3/P4) are assigned Basic Life Support (BLS) units to preserve critical care capacity.
   - Evaluates receiving hospital capability (e.g., Trauma Level 1, Cardiac ICU, Burn Center) and current bed/ICU capacity.
   - Computes deterministic trade-off scores for all candidate ambulances and hospitals.
3. **Dynamic Hospital Redirection Engine (`Dispatch/redirection_engine.py`)**:
   - Continuously evaluates in-flight transports against hospital capacity changes. If a destination hospital saturates (e.g., during an ongoing surge), en-route ambulances are safely redirected to alternative qualified facilities.
4. **Authoritative State & Persistence (`Dispatch/state.py`, `api/persistence/`)**:
   - `DispatchState` is the authoritative in-memory state representing ambulances, hospitals, active calls, and simulation time. All state mutations occur under a threading lock.
   - An asynchronous persistence bridge buffers telemetry events into an SQLite database (`data/raah_history.db`) with automatic periodic state checkpointing (every 30s) and startup crash recovery.
5. **Real-Time Event Stream (`api/realtime/broadcaster.py`)**:
   - Server-Sent Events (SSE) at `/events/stream` broadcast dispatches, kinematics movements, hospital occupancy updates, and redirection events with monotonic sequence numbers.
6. **External Ingestion & M2M Gateway (`api/adapters/`, `api/routers/ingestion.py`)**:
   - Provides authenticated endpoints for external CAD calls, ambulance GPS/AVL, hospital bed telemetry, and traffic updates.
   - Enforces provider-scoped machine-to-machine (M2M) API keys using SHA-256 fingerprint validation alongside internal operator JWT/RBAC.

---

## Safety & Design Invariants

RAAH adheres to strict architectural boundaries for mission-critical software:

- **Immutable Clinical Model**: The trained clinical ML model and preprocessing pipeline are frozen and cannot be dynamically overwritten or retrained by the API at runtime.
- **Zero Fabricated Physiology**: The external CAD intake mapper (`CADTriageMapper`) never defaults, interpolates, or synthesizes missing vital signs (such as heart rate, blood pressure, SpO2, respiratory rate, or GCS). If any required clinical feature is absent from intake, the request is rejected with HTTP 422 Unprocessable Entity.
- **Deterministic Operational Decisions**: Ambulance dispatch, hospital selection, and redirection decisions are calculated using deterministic mathematical scoring and capability rules, not black-box heuristics.
- **No Generative LLMs in Dispatch Loop**: Generative Large Language Models (LLMs) are strictly prohibited in the safety-critical triage and dispatch decision path.
- **Observational Decision Evidence**: Every dispatch decision captures an auditable snapshot of input vitals, ML confidence scores, evaluated candidate scores, and selection rationale. Evidence is read-only and immutable.
- **Strictly Read-Only Operational Replay**: Replaying past incidents executes against historical checkpoints with database connections configured with `PRAGMA query_only = ON;`. Replay scrubbing never mutates active simulation state.

---

## Prerequisites

- **Python**: Version 3.10 or newer (tested with Python 3.10–3.13)
- **Operating System**: Linux, macOS, or Windows (WSL / Command Prompt / PowerShell)
- **Frontend Requirements**: None. The frontend is built using standard HTML5, CSS3, and ES6 modules served directly by FastAPI. No Node.js, npm, or frontend bundlers are required.

---

## Installation

Run the following commands in your shell to set up a clean environment:

```bash
# 1. Clone the repository
git clone https://github.com/mohiitrathor/RAAH.git

# 2. Navigate to repository root
cd RAAH

# 3. Create a Python virtual environment
python3 -m venv venv

# 4. Activate virtual environment
# On Linux / macOS:
source venv/bin/activate
# On Windows (cmd):
# venv\Scripts\activate.bat
# On Windows (PowerShell):
# venv\Scripts\Activate.ps1

# 5. Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Running RAAH

Start the unified backend server and static frontend using Uvicorn:

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Once started, open your browser and navigate to:

**`http://localhost:8000/`**

Visiting the root URL automatically redirects to `/dashboard/`, where the web interface connects to the real-time event stream (`/events/stream`) and displays the live Tactical Operations Command Center.

---

## Useful Endpoints

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `http://localhost:8000/` | `GET` | Tactical Operations Command Center (redirects to `/dashboard/`) |
| `http://localhost:8000/dashboard/` | `GET` | Tactical Operations Command Center (direct static mount) |
| `http://localhost:8000/docs` | `GET` | Interactive OpenAPI Swagger UI documentation |
| `http://localhost:8000/redoc` | `GET` | ReDoc API documentation |
| `http://localhost:8000/health/live` | `GET` | Process liveness probe (lightweight health check) |
| `http://localhost:8000/health/ready` | `GET` | Deep application readiness probe (DB, simulator, ML, adapters) |
| `http://localhost:8000/metrics` | `GET` | Operational metrics (request latencies, queues, ingestion rates) |
| `http://localhost:8000/ingestion/status` | `GET` | Ingestion health status and provider adapter telemetry |
| `http://localhost:8000/events/stream` | `GET` | Real-time Server-Sent Events (SSE) telemetry feed |

---

## Five-Minute Live Demo Walkthrough

Follow this step-by-step procedure for a complete, deterministic live demonstration:

1. **Tactical Operations Command Center**:
   - Open `http://localhost:8000/` in the browser.
   - Point out the dark tactical interface: the Leaflet GIS map of Jaipur, ambulance markers categorized by status (Idle, Dispatched, En Route), and hospital nodes showing live bed/ICU occupancy meters.
   - Note the **Stream Status** badge in the header reading `CONNECTED #0` (Server-Sent Events active).
2. **Live Emergency Call Intake**:
   - Click the **"+ Live Call"** button in the top-left panel to open the Emergency Call Intake modal.
   - Select the clinical preset: **"Acute STEMI Cardiac (Critical P1)"**.
   - Note how the 24-feature form populates with realistic clinical measurements (HR 135, SpO2 86%, Systolic BP 85, GCS 12, Chest Pain = Yes).
   - Click **"Submit Emergency Call"**.
3. **ML Severity Prediction & Dispatch**:
   - Point out the instant triage response: the ML model evaluates the 24 features and classifies the patient as **Critical (P1)**.
   - Observe the dispatch engine assigning the nearest available **Advanced Life Support (ALS)** ambulance and routing to a hospital with an active **Cardiac ICU**.
4. **Decision Evidence Inspection**:
   - Click on the newly created incident in the active incident list or activity feed.
   - Open the **Decision Evidence** drawer/modal.
   - Highlight the auditable explanation: the exact ML confidence probabilities, alternative candidate ambulances scored with trade-off penalties (distance vs. clinical capability), and hospital selection rationale.
5. **Simulation Movement**:
   - In the top header bar, click **"Play"** (or use the **"+1m"** step button).
   - Watch the assigned ambulance marker move in real time along the road network toward the incident location, arrive on scene, and begin patient transport.
6. **Disaster Drill & Hospital Saturation**:
   - In the header navigation, switch to the **"Tactical Operations"** drill controls or trigger the curated drill: **"NH-48 Highway Pileup"** (`NH48_MULTI_VEHICLE_PILEUP`).
   - Observe a sudden multi-casualty surge: multiple ambulances are dispatched across zones, and the receiving hospital's ICU capacity rapidly fills toward 100%.
7. **Hospital Redirection**:
   - When the primary trauma center's ICU exceeds capacity, observe the redirection engine automatically detecting in-flight transports and rerouting an en-route ambulance to the next best alternative trauma hospital.
8. **Operational Replay**:
   - In the top navigation bar, click **"Operational Replay"**.
   - Select the current session run and use the timeline scrubber to scrub backwards in time.
   - Demonstrate the ability to pause, step through event sequences tick-by-tick, filter by specific vehicle or incident, and inspect exact system state at any historical moment.
9. **Operations Review & Post-Incident Review (PIR)**:
   - In the top navigation bar, click **"Operations Review"**.
   - Show the automated PIR summary: 90th percentile response times, P1 priority SLA compliance, hospital turnaround metrics, and root-cause bottleneck identification.
   - Use the **Compare Runs** feature to display a side-by-side KPI diff between baseline operations and stress-test performance.
10. **Integrations Health Console**:
    - Click the **"Integrations"** tab.
    - Inspect the four external telemetry providers (CAD, GPS, Hospital, Traffic).
    - Point out the active provider heartbeat timestamps, M2M credential fingerprint verification, and throughput telemetry.

---

## Existing Demo Scenarios

RAAH contains four built-in, curated operational scenarios located in `Dispatch/scenarios/drills/library.py`:

- **`NH48_MULTI_VEHICLE_PILEUP`**:
  - Major highway mass-casualty pileup on the NH-48 corridor (15 casualties across triage priorities).
  - Demonstrates multi-zone fleet coordination, ALS vs. BLS capability allocation, and trauma center load balancing.
- **`DUAL_MCI_EARTHQUAKE`**:
  - Simultaneous structural collapses in North and South Jaipur (24 total casualties).
  - Stresses citywide ambulance availability and triggers cross-zone repositioning recommendations.
- **`CITYWIDE_HOSPITAL_SATURATION`**:
  - Sequential waves of acute medical emergencies directed at primary facilities until ICU thresholds exceed 90%.
  - Triggers automated en-route ambulance redirection to secondary receiving centers.
- **`CASUALTY_SURGE`**:
  - Parameterized high-volume surge (25, 50, 100 casualties) across multiple clusters to evaluate queue throughput and stability under severe overload.

---

## Project Structure

```
RAAH/
├── api/                             # FastAPI Backend & Integration Layer
│   ├── adapters/                    # External provider adapters & M2M auth (CAD, GPS, Hospital, Traffic)
│   ├── auth/                        # JWT cryptographic token security & RBAC permissions
│   ├── decision_evidence/           # Observational decision evidence capture & store
│   ├── observability/               # Metrics collection, structured JSON logging, middleware
│   ├── persistence/                 # SQLite database connection, bridge queue, schema migrations
│   ├── realtime/                    # Server-Sent Events (SSE) broadcaster & event models
│   ├── routers/                     # REST API routers (dispatch, ingestion, replays, drills, etc.)
│   ├── schemas/                     # Pydantic request and response schemas
│   ├── dependencies.py              # SimulatorManager runtime dependency & lifecycle supervisor
│   ├── main.py                      # FastAPI application definition, lifespan, & static mounts
│   └── settings.py                  # Pydantic Settings configuration layer (env prefix RAAH_)
├── Dataset/                         # Authoritative Baseline Data
│   ├── ambulances.csv               # Baseline fleet roster with vehicle types (ALS/BLS) & coordinates
│   ├── hospitals.csv                # Receiving hospitals, departments, and initial bed/ICU capacities
│   ├── patient_incidents.csv        # Baseline historical emergency call data (100k incidents)
│   └── Synthetic EMS Dataset...md   # Dataset schema definitions and clinical distributions
├── Dispatch/                        # Deterministic Dispatch & Simulation Core
│   ├── coordination/                # Multi-agency coordination, zones, and mutual aid
│   ├── optimization/                # Fleet repositioning and hospital diversion advisories
│   ├── routing/                     # Kinematics travel time estimation and road network routing
│   ├── scenarios/                   # Disaster drill generators, replay engine, PIR, and regression
│   ├── dispatch_engine.py           # Core deterministic ambulance & hospital selection algorithms
│   ├── redirection_engine.py        # Live hospital saturation detection & patient redirection
│   ├── simulator.py                 # Discrete-time simulation engine & event pipeline
│   └── state.py                     # Authoritative DispatchState representation
├── frontend/                        # Web Tactical Command Center (Zero-Build Vanilla JS/CSS)
│   ├── css/                         # Tactical dark-mode stylesheets & typography
│   ├── js/                          # Application controllers, Leaflet map engine, API client
│   │   └── components/              # Modals (intake, evidence), replay controls, PIR, drills
│   ├── vendor/                      # Local vendor assets (Leaflet, Lucide icons)
│   └── index.html                   # Command Center single-page application layout
├── Models/                          # Clinical Machine Learning Artifacts
│   ├── Final Model/                 # Frozen clinical model, scaler, feature vector, & inference script
│   └── Reports/                     # Model comparison reports (Logistic Regression vs. RF vs. XGBoost)
├── data/                            # Local persistent storage directory (SQLite DB, checkpoints)
├── requirements.txt                 # Unified Python package dependencies
└── README.md                        # Project documentation and operational guide
```

---

## Limitations & Project Honesty

- **Synthetic EMS Dataset**: All patient medical records, incident coordinates, ambulance identifiers, and hospital capacities used in RAAH are generated from calibrated synthetic EMS distributions. They reflect realistic clinical physiology and urban emergency patterns, but do not contain real Protected Health Information (PHI) or real patient records.
- **Mock Integration Adapters**: External CAD, AVL/GPS, hospital bed management, and traffic feeds are implemented as realistic mock adapters and M2M HTTP webhooks rather than live connections to production emergency dispatch networks (such as actual 911/112 CAD software or state public safety answering points).
- **Urban Kinematics**: Ambulance road travel times are computed using haversine distances adjusted by zone-based urban speed profiles and route circuity factors rather than real-time live navigation satellite turn-by-turn feeds.

---

## Testing & Verification

RAAH maintains a comprehensive regression test suite covering unit logic, integration boundaries, M2M authentication, clinical ML validation, and deterministic dispatch invariance.

To run the milestone regression suites:

```bash
python3 test_m13_phase12.py
python3 test_m13_phase11.py
python3 test_m13_phase10.py
python3 test_m13_phase9.py
python3 test_m12_phase4.py
python3 test_m12_phase5.py
```

### Verified Milestone Results

In the audited release candidate environment, **186 out of 186 automated tests passed** with zero failures across all six test suites:

- `test_m13_phase12.py`: **36 / 36 passed** (CAD intake mapping, zero-synthetic-vitals enforcement, M2M authorization)
- `test_m13_phase11.py`: **29 / 29 passed** (M2M gateway, credential hashing, provider scopes)
- `test_m13_phase10.py`: **35 / 35 passed** (Adapter registry, telemetry ingestion, deduplication, metrics)
- `test_m13_phase9.py`: **34 / 34 passed** (External adapters, ingestion service, mock providers)
- `test_m12_phase4.py`: **28 / 28 passed** (State persistence, SQLite durability, recovery, checkpointing)
- `test_m12_phase5.py`: **24 / 24 passed** (Security, RBAC, JWT validation, CORS, error handling)

*Note*: Test execution results depend on the local Python runtime meeting the prerequisites defined in `requirements.txt`.
