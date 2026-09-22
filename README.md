# RAAH — Real-Time Ambulance Allocation & Hospital Redirection Platform

RAAH is an intelligent Emergency Medical Services (EMS) dispatch, triage, and tactical coordination platform designed to optimize emergency response during routine operations, hospital saturation surges, and mass-casualty incidents (MCIs).

The platform combines calibrated machine learning for clinical severity prediction with deterministic, explainable algorithms for ambulance allocation, hospital destination selection, and dynamic en-route patient redirection.

> [!IMPORTANT]
> **RESEARCH & DEMONSTRATION EMS PLATFORM DISCLAIMER**
> RAAH is an operational research and demonstration software platform, **not a certified medical device** or clinical diagnostic tool.
> - Machine learning models provide clinical risk/severity classification solely for tactical prioritization and resource routing.
> - Fleet dispatch and hospital balancing algorithms are deterministic, auditable, and advisory.
> - Dynamic redirection continuously monitors receiving facility capacities and travel constraints to assist dispatchers.
> - Human dispatchers, incident commanders, and licensed healthcare professionals remain in authoritative control of all clinical care and dispatch decisions at all times.

---

## Quick Start

Launch RAAH in seconds using the unified root launcher:

```bash
# 1. Clone the repository
git clone https://github.com/mohiitrathor/RAAH.git
cd RAAH

# 2. Run the unified launcher
./run.sh
```

The launcher automatically detects your Python environment (virtual environment, Conda `ai_env`, or system Python), verifies required packages, creates runtime directories, and boots the FastAPI backend. Open your browser to `http://localhost:8000`.

### Primary Operational Interfaces

| Command | Action |
| :--- | :--- |
| **`./run.sh`** | Launches FastAPI backend & Tactical Command Center on `http://localhost:8000` |
| **`./run.sh demo`** | Executes the automated end-to-end live surge & divert demonstration |
| **`./run.sh benchmark`** | Runs dispatch engine benchmarks, latency percentiles, and triage audits |
| **`./run.sh test`** | Executes the complete automated regression test suite (`pytest -q`) |
| **`./run.sh desktop`** | Launches the native Linux GTK3/WebKit2 desktop application |
| **`./run.sh docker`** | Displays Docker containerization build and execution commands |
| **`./run.sh --help`** | Displays detailed command line usage and options |

---

## System Architecture

RAAH maintains a strict architectural separation of concerns across clinical inference, tactical allocation, simulation kinetics, authoritative state persistence, and operator interfaces:

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
│  - Frozen Scikit-Learn Pipeline (Trained Logistic Regr.)    │
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
│  - One-Click Surge Demo & 9-Step Interactive Timeline       │
│  - Decision Evidence Modal & PIR Run Comparison             │
└─────────────────────────────────────────────────────────────┘
```

### Core Components and Responsibilities

1. **Clinical Machine Learning Layer (`Models/Final Model/Predict_Severity.py`)**:
   - Evaluates a calibrated 24-feature clinical vector (patient age, vitals, GCS, condition, respiratory distress, pain score, etc.).
   - Predicts clinical severity (`Critical`, `Emergency`, `Urgent`, `Non-Urgent`) and priority level (`P1`–`P4`).
   - The trained model pipeline (`logistic_regression_final.joblib`) is immutable and never modified at runtime.
2. **Deterministic Dispatch Engine (`Dispatch/dispatch_engine.py`)**:
   - Maps clinical priority to ambulance capability: Critical/Emergency (P1/P2) calls require Advanced Life Support (ALS) units; lower-acuity calls receive Basic Life Support (BLS) units.
   - Calculates spherical Haversine distances and zone-adjusted ETAs with deterministic tie-breaking.
3. **Deterministic Hospital Capacity Balancer (`Dispatch/coordination/hospital_balancer.py`)**:
   - Tracks active in-flight bed and ICU reservations to prevent emergency department saturation.
   - Evaluates department suitability (e.g., Trauma Center, Cardiac Center, ICU availability).
4. **Dynamic Redirection Engine (`Dispatch/redirection_engine.py`)**:
   - Event-driven monitoring of in-flight transports against hospital capacity disruptions (e.g. `HOSPITAL_FULL`).
   - Diverts en-route ambulances to qualified alternative hospitals before arrival.
   - Arrival-locking safeguards prevent redirection after an ambulance reaches `ARRIVED` status.
5. **Authoritative DispatchState & Persistence (`Dispatch/state.py`, `api/persistence/`)**:
   - Single source of truth managed under an internal thread lock.
   - SQLite event telemetry with automated periodic checkpointing and crash recovery.
6. **Tactical Command Center Frontend (`frontend/`)**:
   - Built with zero-build vanilla ES6 JavaScript, modern CSS, and vendored Leaflet and Lucide assets.
   - Connects to real-time Server-Sent Events (`/events/stream`) with automatic reconnection.

---

## Key Workflows & System Capabilities

### 1. End-to-End Incident Intake & ML Triage
Operators or CAD adapters submit patient vital signs, Glasgow Coma Scale (GCS), chief complaint, and scene coordinates. The system passes the 24-dimensional feature vector through the clinical model, outputting predicted severity, priority class, and clinical confidence.

### 2. Deterministic ALS/BLS Fleet Allocation
Based on the assessed priority, the engine matches incident requirements to available vehicle capabilities (ALS for P1/P2, BLS for P3/P4). It evaluates road network travel times, vehicle status, and station zones to dispatch the optimal unit.

### 3. Capacity-Aware Hospital Selection
The hospital balancer selects destination facilities matching the patient's clinical needs (e.g., Trauma, Cardiac, Neurological) while reserving bed/ICU slots in real time to prevent sudden emergency department overcrowding.

### 4. Dynamic En-Route Redirection
If a receiving hospital experiences unexpected saturation or closure while an ambulance is en route, the redirection engine triggers an automated divert evaluation, identifies the next-best qualified facility, updates transit waypoints, and alerts dispatchers. Redirection is locked once arrival occurs.

### 5. Post-Incident Review (PIR) & Historical Replay
Every state transition, dispatch decision, and telemetry update is captured in SQLite event logs. Operators can replay historical runs at variable speeds ($1\times$ to $10\times$), inspect side-by-side run comparisons, and generate auditable Post-Incident Review packages.

### 6. Mass-Casualty Incidents (MCI) & Mutual Aid
During large-scale incidents, RAAH activates MCI protocols: secondary triage queues, mutual-aid agency coordination, and multi-zone casualty distribution across regional trauma networks.

### 7. External Ingestion Adapters
Standardized M2M ingestion endpoints accept external CAD incidents, GPS automatic vehicle location (AVL) pings, hospital capacity FHIR updates, and traffic feeds with cryptographic key validation and idempotency caching.

---

## One-Click Surge & Divert Demonstration

The Tactical Command Center includes a built-in one-click demonstration mode:

1. Start RAAH with `./run.sh` and open `http://localhost:8000`.
2. Click the prominent **`[⚡ SIMULATE SURGE & DIVERT]`** button in the header bar.
3. The interactive **Demo Workflow Timeline** overlay tracks 9 real-time milestones:
   - **`01 INCIDENT RECEIVED`**: Baseline reset and emergency cardiac call intake.
   - **`02 ML TRIAGE`**: High-acuity patient classified as **Critical (P1)** by clinical model.
   - **`03 AMBULANCE ASSIGNED`**: Nearest available ALS unit dispatched to the scene.
   - **`04 HOSPITAL SELECTED`**: Initial destination hospital with Cardiac ICU chosen.
   - **`05 CAPACITY DISRUPTION`**: Injects sudden hospital saturation (`HOSPITAL_FULL`).
   - **`06 REDIRECTION EVALUATED`**: Balancer computes alternative receiving facilities.
   - **`07 ALT HOSPITAL SELECTED`**: Next-best qualified specialty facility selected.
   - **`08 ROUTE UPDATED`**: Dynamic transit route recalculated and updated on GIS map.
   - **`09 DECISION RECORDED`**: Immutable decision evidence log permanently recorded.

You can also execute the exact same end-to-end demonstration headlessly from your terminal:

```bash
./run.sh demo
```

---

## Benchmarks & Evaluation

RAAH provides a clear separation between **offline clinical model evaluation** and **live operational dispatch benchmarks**.

### 1. Offline Clinical Model Evaluation (Training & Validation)

The clinical severity prediction model was trained and evaluated on 100,000 emergency patient incidents (80,000 train / 20,000 test stratified split) across 24 input features:

| Metric | Logistic Regression (Final Pipeline) |
| :--- | :--- |
| **Overall Accuracy** | **68.95%** |
| **Balanced Accuracy** | **67.07%** |
| **Feature Dimensionality** | 24 clinical features (18 numeric, 6 categorical) |
| **Target Classes** | 5 levels (`Non-Urgent`, `Low`, `Moderate`, `Emergency`, `Critical`) |
| **Model Artifact** | Frozen Scikit-Learn Pipeline (`Models/Final Model/logistic_regression_final.joblib`) |

*Balanced accuracy was prioritized during model selection to ensure sensitive detection of rare, high-acuity Critical cases within imbalanced emergency incident data.*

### 2. Live Operational Dispatch Benchmarks (Runtime Performance)

Run the unified runtime benchmark suite:

```bash
./run.sh benchmark
```

Results measured across simulated emergency dispatch and dynamic divert workloads:

| Metric | Target | Measured Result | Status |
| :--- | :--- | :--- | :--- |
| **Mean Dispatch Latency** | `< 50.0 ms` | **~8.0 ms** | PASS |
| **Median Dispatch Latency** | `< 20.0 ms` | **~7.5 ms** | PASS |
| **Dynamic Divert Latency** | `< 20.0 ms` | **~1.5 ms** | PASS |
| **Ambulance Allocation Rate** | `100.0%` | **100.0%** | PASS |
| **Hospital Allocation Rate** | `100.0%` | **100.0%** | PASS |
| **Capability Match Rate** | `> 95.0%` | **100.0%** | PASS |
| **Engine Errors** | `0` | **0** | ZERO ERRORS |

---

## Containerization & Deployment

RAAH provides production container specifications for portable deployment.

### Container Build & Execution Commands

```bash
# 1. Build the Docker image
docker build -t raah:latest .

# 2. Run with environment variables
docker run -d \
  --name raah \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  raah:latest

# Or launch with Docker Compose
docker compose up -d
```

> [!NOTE]
> The multi-stage `Dockerfile` and `docker-compose.yml` configurations have been validated against production deployment contracts. During this release preparation phase, local Docker daemon execution was unavailable on the build host, so containerized execution requires an active Docker/Podman runtime on the target host.

---

## Testing & Quality Assurance

Execute the complete regression test suite:

```bash
./run.sh test
```

All 418 milestone test cases pass with zero errors, zero failures, and zero unhandled skips across:
- Clinical ML inference and fast-path analytical equivalence
- Deterministic ALS/BLS capability matching and proximity calculations
- Dynamic mid-transit hospital redirection and arrival-lock invariants
- Multi-agency mutual aid and mass casualty incident coordination
- Ingestion adapters, M2M authentication, and idempotency caching
- SQLite persistence, periodic checkpoints, and crash recovery
- Native Linux desktop wrapper and process supervisor

---

## Production Configuration & Environment

Configuration is managed via environment variables prefixed with `RAAH_`, defined in `.env.example`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `RAAH_APP_NAME` | `"RAAH — Emergency Dispatch & Coordination Platform"` | Canonical platform name |
| `RAAH_ENVIRONMENT` | `development` | Deployment environment (`development`, `staging`, `production`) |
| `RAAH_HOST` | `0.0.0.0` | Bind host address |
| `RAAH_PORT` | `8000` | Bind port |
| `RAAH_AUTH_ENFORCED` | `true` | Enforces authentication on protected API routes |
| `RAAH_DEV_AUTH_FALLBACK` | `false` (in prod) | Development token fallback; **must be false in production** |
| `RAAH_JWT_SECRET_KEY` | *(Secret string)* | Cryptographic signing key ($\ge 32$ bytes in production) |
| `RAAH_CORS_ORIGINS` | `http://localhost:8000` | Allowed CORS origins (comma-separated, no wildcards in prod) |
| `RAAH_DATABASE_PATH` | `data/raah_history.db` | Persistent SQLite database file path |
| `RAAH_CHECKPOINT_INTERVAL_SECONDS` | `30.0` | Periodic state snapshot interval |
| `RAAH_CAD_PROVIDER` | `mock` | CAD adapter provider (`mock`, `webhook`, `vendor`) |
| `RAAH_HOSPITAL_PROVIDER` | `mock` | Hospital capacity provider (`mock`, `fhir`, `vendor`) |

---

## System Requirements

- **Python**: Python 3.10 – 3.13 (Python 3.12 recommended)
- **Scikit-Learn**: Exactly pinned to `scikit-learn==1.7.2` for binary model artifact compatibility
- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, RHEL/Fedora), macOS 12+, or Windows via WSL2
- **Frontend Dependencies**: None (Zero-build ES6 modules with vendored Leaflet and Lucide assets)
- **Desktop Application (Optional)**: Python 3 with `PyGObject` (GTK 3.0) and `WebKit2` 4.1

---

## Useful Endpoints

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `http://localhost:8000/` | `GET` | Tactical Operations Command Center (redirects to `/dashboard/`) |
| `http://localhost:8000/docs` | `GET` | Interactive OpenAPI Swagger UI documentation |
| `http://localhost:8000/health` | `GET` | Process liveness probe |
| `http://localhost:8000/health/ready` | `GET` | Deep readiness probe (database, simulator, ML, adapters) |
| `http://localhost:8000/events/stream` | `GET` | Real-time Server-Sent Events (SSE) telemetry feed |
| `http://localhost:8000/state/snapshot` | `GET` | Current full simulation state snapshot |
| `http://localhost:8000/decision-evidence/incident/{id}` | `GET` | Auditable decision evidence records for an incident |

---

## Limitations & Non-Goals

1. **Simulation Environment**: The live dispatch kinematics operate in discrete simulation time. Vehicle velocities, acceleration profiles, and path traversal reflect mathematical kinematics models rather than live hardware telemetry.
2. **Synthetic Demonstration Data**: The provided datasets (`ambulances.csv`, `hospitals.csv`, `patient_incidents.csv`) represent realistic synthetic data designed for research, simulation drills, and performance evaluation.
3. **External Routing Providers**: When external routing providers (e.g. OSRM, Google Maps Platform) are not configured with live API credentials, RAAH automatically falls back to local spherical Haversine distance with zone velocity approximations.
4. **Regulatory Non-Certification**: RAAH is an operational research and tactical coordination software prototype. It is not certified under FDA, CE, or regional medical device regulations for automated diagnosis or autonomous dispatch.
5. **Host Environment Notice**: Docker and Podman were not installed on the build host during this release cycle; container specifications have been statically validated against the configuration contract.

---

## Project Structure

```text
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
│   └── patient_incidents.csv        # Baseline historical emergency call data (100k incidents)
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
│   │   └── components/              # Modals (intake, evidence), replay controls, PIR, drills, demo
│   ├── vendor/                      # Local vendor assets (Leaflet, Lucide icons)
│   └── index.html                   # Command Center single-page application layout
├── Models/                          # Clinical Machine Learning Artifacts & Reports
│   ├── Final Model/                 # Frozen clinical model, scaler, feature vector, & inference script
│   ├── Analysis/                    # Model confidence, error, and calibration analysis scripts
│   ├── Model Tuning/                # Hyperparameter tuning records (Logistic Regression, XGBoost)
│   └── Reports/                     # Evaluation metrics, calibration curves, and comparison reports
├── scripts/                         # CLI Automation & Benchmark Scripts
│   ├── benchmark_suite.py           # Unified performance and dispatch evaluation benchmark
│   └── demo_runner.py               # Headless automated end-to-end demo execution
├── bin/                             # Desktop launcher script
│   └── raah-desktop                 # Portable Linux GTK/WebKit launcher
├── desktop/                         # Desktop application and supervisor
│   ├── app.py                       # Native GTK3/WebKit2 application wrapper
│   ├── supervisor.py                # Backend process supervisor
│   └── raah.desktop                 # Freedesktop application entry
├── data/                            # Local persistent storage directory (SQLite DB, checkpoints)
├── requirements.txt                 # Unified Python package dependencies
├── run.sh                           # Unified root entrypoint script
├── Dockerfile                       # Multi-stage production container image
├── docker-compose.yml               # Containerized stack definition
├── LICENSE                          # MIT License (Mohit Rathore)
├── .gitattributes                   # Cross-platform LF line ending enforcement
├── .gitignore                       # Repository exclusion rules
├── .env.example                     # Environment configuration template
└── README.md                        # Project documentation and operational guide
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](file:///home/glitchedpotato/Downloads/RAAH/LICENSE) file for details.

Copyright © 2026 Mohit Rathore.
