/**
 * RAAH Command Center Application Bootstrapper
 * Orchestrates conditional polling, state updates, and component lifecycles.
 */

import { store } from './state.js';
import * as api from './api.js';
import { tacticalMap } from './map.js';

import { setupControls } from './components/controls.js';
import { setupIncidents } from './components/incidents.js';
import { setupFleet } from './components/fleet.js';
import { setupHospitals } from './components/hospitals.js';
import { setupEvents } from './components/events.js';
import { setupDecisions } from './components/decisions.js';
import { setupDetailDrawer } from './components/detail_drawer.js';
import { setupAnalytics } from './components/analytics.js';
import { setupConnectionStatus } from './components/connection_status.js';
import { coordinationComponent } from './components/coordination.js';
import { DrillsController } from './components/drills.js';
import { ReplayController } from './components/replay.js';
import { ScenarioAnalysisController } from './components/scenario_analysis.js';
import { PIRController } from './components/pir.js';
import { RegressionController } from './components/regression.js';
import { OptimizationController } from './components/optimization.js';
import { IntegrationController } from './components/integrations.js';
import { showToast } from './components/toasts.js';

let pollCounter = 0;
let isPolling = false;

async function bootstrap() {
  console.log('🚀 Initializing RAAH Tactical Command Center...');

  // 1. Initialize Map
  tacticalMap.initialize('leaflet-map');

  // 2. Setup Components
  setupControls();
  setupIncidents();
  setupFleet();
  setupHospitals();
  setupEvents();
  setupDecisions();
  setupDetailDrawer();
  setupAnalytics();
  setupConnectionStatus();
  coordinationComponent.init();
  new DrillsController();

  const replayCtrl = new ReplayController();
  replayCtrl.init();
  const analysisCtrl = new ScenarioAnalysisController(replayCtrl);
  analysisCtrl.init();

  const pirCtrl = new PIRController();
  pirCtrl.init();
  const regCtrl = new RegressionController();
  regCtrl.init();

  const optCtrl = new OptimizationController();
  optCtrl.init();
  document.getElementById('nav-btn-optimization')?.addEventListener('click', () => {
    optCtrl.loadOptimizationData();
  });

  const intCtrl = new IntegrationController();
  intCtrl.init();
  document.getElementById('nav-btn-integrations')?.addEventListener('click', () => {
    intCtrl.loadStatus();
  });

  // 3. Initial Lucide Icons Render
  if (window.lucide) {
    window.lucide.createIcons();
  }

  // 4. Initial Real API Data Fetch (Validating contract on day one)
  try {
    const [health, dashboard, hospitals, rtStatus, decisions] = await Promise.all([
      api.getHealth(),
      api.getDashboard(),
      api.getHospitals(),
      api.getRealtimeStatus(),
      api.getDecisions(),
    ]);

    console.log('✓ Initial backend sync completed. Sim time:', health.time);

    store.setHospitals(hospitals);
    store.updateRealtimeStatus(rtStatus);
    store.updateFromDashboard(dashboard);
    store.setDecisions(decisions);

    // If initial active incidents exist, fetch ambulances immediately
    if (dashboard.fleet && dashboard.fleet.en_route > 0) {
      const ambulances = await api.getAmbulances();
      store.setAmbulances(ambulances);
    }
  } catch (err) {
    console.error('Initial backend connection failed:', err);
  }

  // 5. Establish Real-Time SSE Stream (Replaces 1-second polling loop)
  initRealtimeStream();
}

let streamHandle = null;
let lastSequence = null;
let isRecovering = false;

async function triggerAuthoritativeRecovery(reason = '') {
  if (isRecovering) return;
  isRecovering = true;
  console.log(`[Realtime] Triggering authoritative REST recovery (${reason})...`);
  try {
    const dashboard = await api.getDashboard();
    store.updateFromDashboard(dashboard);
    if (dashboard.fleet && dashboard.fleet.en_route > 0) {
      const ambulances = await api.getAmbulances();
      store.setAmbulances(ambulances);
    }
    if (streamHandle) {
      lastSequence = streamHandle.getCurrentSequence();
    }
  } catch (err) {
    console.warn('[Realtime] Snapshot recovery failed:', err.message);
  } finally {
    isRecovering = false;
  }
}

function initRealtimeStream() {
  if (streamHandle) {
    console.warn('[Realtime] Stream already active. Skipping duplicate connection.');
    return;
  }

  streamHandle = api.connectEventStream({
    onOpen: () => {
      console.log('✓ Real-time SSE command center stream active.');
      store.setConnectionStatus({
        state: 'connected',
        sequence: streamHandle ? streamHandle.getCurrentSequence() : 0,
        reconnectAttempts: 0,
      });
    },
    onSnapshot: (event) => {
      if (event.payload && event.payload.dashboard) {
        store.updateFromDashboard(event.payload.dashboard);
        if (event.payload.dashboard.hospitals) {
          store.setHospitals(event.payload.dashboard.hospitals);
        }
      }
      lastSequence = event.sequence;
      store.setConnectionStatus({
        state: 'connected',
        sequence: event.sequence,
        lastEventTime: event.timestamp || new Date().toISOString(),
        lastEventType: 'STATE_SNAPSHOT',
      });
    },
    onGap: () => {
      store.setConnectionStatus({ state: 'syncing' });
      triggerAuthoritativeRecovery('gap_detected_in_stream');
    },
    onEvent: (event) => {
      // Check sequence monotonicity & gap
      if (lastSequence !== null && event.sequence > lastSequence + 1 && event.event_type !== 'STATE_SNAPSHOT') {
        store.setConnectionStatus({ state: 'syncing' });
        triggerAuthoritativeRecovery(`sequence_gap_${lastSequence}_to_${event.sequence}`);
      }
      lastSequence = event.sequence;

      store.setConnectionStatus({
        sequence: event.sequence,
        lastEventTime: event.timestamp || new Date().toISOString(),
        lastEventType: event.event_type,
      });

      switch (event.event_type) {
        case 'TICK':
          if (event.payload) {
            store.updateRealtimeStatus({
              status: event.payload.status,
              is_running: event.payload.status === 'RUNNING',
              current_time: event.payload.current_time,
              speed_multiplier: event.payload.speed_multiplier,
            });
            store.updateFromDashboard(event.payload);
            if (event.payload.moving_ambulances && event.payload.moving_ambulances.length > 0) {
              store.updateAmbulances(event.payload.moving_ambulances);
            }
          }
          break;

        case 'INCIDENT_DISPATCHED':
          if (event.payload) {
            store.addOrUpdateIncident(event.payload);
            const ambId = event.payload.ambulance_id || (event.payload.ambulance ? event.payload.ambulance.ambulance_id : 'unit');
            const hospId = event.payload.hospital_id || (event.payload.hospital ? event.payload.hospital.hospital_id : 'facility');
            store.addActivityEntry({
              type: 'DISPATCH',
              badge: 'DISPATCH',
              time: event.payload.time !== undefined ? event.payload.time : store.state.simTime,
              message: `Ambulance ${ambId} dispatched to Incident #${event.payload.incident_id} → ${hospId}`,
              incident_id: event.payload.incident_id,
              details: event.payload,
            });
            showToast('Incident Dispatched', `Ambulance ${ambId} assigned to incident #${event.payload.incident_id}`, 'info');
          }
          break;

        case 'AMBULANCE_UPDATE':
          if (event.payload) {
            store.updateAmbulances([event.payload]);
            store.addActivityEntry({
              type: 'AMBULANCE',
              badge: 'AMBULANCE',
              time: store.state.simTime,
              message: `Ambulance ${event.payload.ambulance_id}: ${event.payload.status || 'position update'}${event.payload.eta_minutes !== undefined && event.payload.eta_minutes !== null ? ` (ETA: ${Number(event.payload.eta_minutes).toFixed(1)}m)` : ''}`,
              details: event.payload,
            });
          }
          break;

        case 'HOSPITAL_UPDATE':
          if (event.payload) {
            store.updateHospital(event.payload);
            const isFull = event.payload.available_beds <= 0 || event.payload.is_full;
            store.addActivityEntry({
              type: 'HOSPITAL',
              badge: 'HOSPITAL',
              time: store.state.simTime,
              message: `Hospital ${event.payload.hospital_id} capacity: ${event.payload.available_beds} beds available${isFull ? ' ⚠️ SATURATED' : ''}`,
              details: event.payload,
            });
            if (isFull) {
              showToast('Hospital Saturated', `Hospital ${event.payload.hospital_id} at 100% capacity!`, 'warning');
            }
          }
          break;

        case 'REDIRECTION_EXECUTED':
          if (event.payload) {
            store.addDecision(event.payload);
            const saved = event.payload.eta_saved !== undefined ? Number(event.payload.eta_saved).toFixed(1) : '0.0';
            store.addActivityEntry({
              type: 'REDIRECT',
              badge: 'REDIRECT',
              time: event.payload.time !== undefined ? event.payload.time : store.state.simTime,
              message: `Incident #${event.payload.incident_id} diverted to ${event.payload.new_hospital_id} (ETA saved: ${saved}m)`,
              incident_id: event.payload.incident_id,
              details: event.payload,
            });
            showToast('Hospital Redirection', `Diverted to hospital ${event.payload.new_hospital_id} (saved ${saved}m)`, 'warning');
          }
          break;

        case 'MCI_ALERT':
          if (event.payload) {
            const mciName = event.payload.mci ? event.payload.mci.name : 'Emergency';
            store.addActivityEntry({
              type: 'MCI',
              badge: 'MCI',
              time: store.state.simTime,
              message: `MCI Alert: ${event.payload.action} - ${mciName} (${event.payload.dispatched_count || 0} units assigned)`,
              details: event.payload,
            });
            showToast('MCI Coordination Alert', `${event.payload.action}: ${mciName}`, 'danger');
          }
          break;

        case 'SYSTEM_ALERT':
          if (event.payload) {
            store.addActivityEntry({
              type: 'ALERT',
              badge: 'ALERT',
              time: store.state.simTime,
              message: event.payload.message || 'System alert triggered',
              details: event.payload,
            });
          }
          break;

        case 'HEARTBEAT':
          break;
      }
    },
    onError: (err) => {
      console.warn('[Realtime] Stream disconnected:', err.message);
      store.setConnectionStatus({ state: 'reconnecting' });
    },
  });
}

// Start on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootstrap);
} else {
  bootstrap();
}
