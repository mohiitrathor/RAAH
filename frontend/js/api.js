/**
 * RAAH Command Center API Client
 * Wraps all 17 FastAPI endpoints cleanly.
 */

const BASE_URL = '';

export async function request(path, options = {}) {
  const url = `${BASE_URL}${path}`;
  const token = typeof window !== 'undefined' && window.localStorage ? (localStorage.getItem('raah_token') || sessionStorage.getItem('raah_token')) : null;
  const authHeaders = token ? { 'Authorization': `Bearer ${token}` } : {};
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errJson = await response.json();
      if (errJson.detail) errorDetail = errJson.detail;
    } catch (_) {}
    throw new Error(errorDetail);
  }

  return response.json();
}

// --- Health ---
export const getHealth = () => request('/health');

// --- State Telemetry ---
export const getDashboard = () => request('/state/dashboard');
export const getSnapshot = () => request('/state/snapshot');
export const getHospitals = () => request('/state/hospitals');
export const getHospital = (id) => request(`/state/hospitals/${id}`);
export const getAmbulances = () => request('/state/ambulances');
export const getAmbulance = (id) => request(`/state/ambulances/${id}`);
export const getIncidents = () => request('/state/incidents');
export const getIncident = (id) => request(`/state/incidents/${id}`);

// --- Dispatch ---
export const dispatchIncident = (incidentId) => 
  request(`/dispatch/${incidentId}`, { method: 'POST' });

export const dispatchLive = (customData) =>
  request('/dispatch/live', {
    method: 'POST',
    body: JSON.stringify(customData),
  });

// --- Simulation Controls ---
export const getRealtimeStatus = () => request('/simulation/realtime/status');

export const startRealtime = (tickInterval = 1.0, minutesPerTick = 1) =>
  request('/simulation/realtime/start', {
    method: 'POST',
    body: JSON.stringify({
      tick_interval_seconds: tickInterval,
      minutes_per_tick: minutesPerTick,
    }),
  });

export const stopRealtime = () => 
  request('/simulation/realtime/stop', { method: 'POST' });

export const simulationTick = (minutes = 1) =>
  request(`/simulation/tick?minutes=${minutes}`, { method: 'POST' });

export const simulationReset = () => 
  request('/simulation/reset', { method: 'POST' });

// --- Redirection & Decisions ---
export const getDecisions = () => request('/redirect/decisions');
export const getIncidentDecisions = (id) => request(`/redirect/decisions/${id}`);
export const checkRedirection = (id) => request(`/redirect/check/${id}`, { method: 'POST' });
export const applyRedirection = (id, targetHospitalId = null, reason = "Operator manual override") =>
  request(`/redirect/apply/${id}`, {
    method: 'POST',
    body: JSON.stringify({
      target_hospital_id: targetHospitalId,
      reason: reason,
    }),
  });

// --- Decision Evidence & Explanations (M13.3) ---
export const getDecisionEvidence = (evidenceId) =>
  request(`/decision-evidence/${encodeURIComponent(evidenceId)}`);

export const getIncidentDecisionEvidence = (incidentId) =>
  request(`/decision-evidence/incident/${encodeURIComponent(incidentId)}`);

export const getRecentDecisionEvidence = (limit = 20) =>
  request(`/decision-evidence/recent?limit=${encodeURIComponent(limit)}`);

// --- Events ---
export const getPendingEvents = () => request('/events/pending');
export const scheduleEvent = (time, eventType, data = {}) =>
  request('/events', {
    method: 'POST',
    body: JSON.stringify({
      time: parseInt(time, 10),
      event_type: eventType,
      data: data,
    }),
  });

// --- Historical Analytics ---
export const getRuns = () => request('/analytics/runs');
export const getAnalyticsSummary = (runId = null) =>
  request(runId !== null ? `/analytics/summary?run_id=${runId}` : '/analytics/summary');
export const getHistoricalIncidents = (runId = null, limit = 50, offset = 0) =>
  request(runId !== null ? `/analytics/incidents?run_id=${runId}&limit=${limit}&offset=${offset}` : `/analytics/incidents?limit=${limit}&offset=${offset}`);
export const getHistoricalDecisions = (runId = null) =>
  request(runId !== null ? `/analytics/decisions?run_id=${runId}` : '/analytics/decisions');
export const getHistoricalEvents = (runId = null, limit = 100) =>
  request(runId !== null ? `/analytics/events?run_id=${runId}&limit=${limit}` : `/analytics/events?limit=${limit}`);

// --- Fleet Coordination (M9) ---
export const getCoverage = () => request('/coordination/coverage');
export const getRepositionRecommendations = () => request('/coordination/reposition/recommendations');
export const getHospitalProjections = () => request('/coordination/hospital-projections');
export const executeReposition = (ambulanceId, targetLat, targetLon, reason = 'COVERAGE_DEFICIT') =>
  request('/coordination/reposition/execute', {
    method: 'POST',
    body: JSON.stringify({
      ambulance_id: ambulanceId,
      target_lat: parseFloat(targetLat),
      target_lon: parseFloat(targetLon),
      reason: reason,
    }),
  });
export const cancelReposition = (ambulanceId) =>
  request(`/coordination/reposition/cancel/${ambulanceId}`, { method: 'POST' });

// --- MCI Coordination (M9 Phase 4) ---
export const getActiveMCIs = () => request('/coordination/mci/active');
export const getMCIDetail = (mciId) => request(`/coordination/mci/${mciId}`);
export const declareMCI = (payload) =>
  request('/coordination/mci/declare', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

// --- Disaster Drills & Stress Testing (M10 Phase 2) ---
export const getDrills = () => request('/drills');
export const runDrill = (drillName, seed = 42, parameters = {}) =>
  request('/drills/run', {
    method: 'POST',
    body: JSON.stringify({ drill_name: drillName, seed: parseInt(seed), parameters: parameters }),
  });
export const runStressTest = (casualtyCount = 50, seed = 42, mciCount = 2, hospitalSurge = false) =>
  request('/drills/stress', {
    method: 'POST',
    body: JSON.stringify({
      casualty_count: parseInt(casualtyCount),
      seed: parseInt(seed),
      mci_count: parseInt(mciCount),
      hospital_surge: Boolean(hospitalSurge),
    }),
  });
export const compareStressTests = (casualtyCounts = [25, 50, 100], seed = 42) =>
  request('/drills/compare', {
    method: 'POST',
    body: JSON.stringify({ casualty_counts: casualtyCounts, seed: parseInt(seed) }),
  });

// --- Operational Replay & Scenario Analysis (M10 Phase 3) ---
export const getReplays = () => request('/replays');
export const getReplayTimeline = (runId, eventType = null, entityId = null) => {
  const params = new URLSearchParams();
  if (eventType) params.append('event_type', eventType);
  if (entityId) params.append('entity_id', entityId);
  const q = params.toString();
  return request(`/replays/${runId}/timeline${q ? '?' + q : ''}`);
};
export const getReplayEventDetail = (runId, eventIndex) =>
  request(`/replays/${runId}/events/${eventIndex}`);
export const getReplayAnalysis = (runId) =>
  request(`/replays/${runId}/analysis`);
export const seekReplayState = (runId, simTime, sessionId = 'default') =>
  request(`/replays/${runId}/state/${simTime}?session_id=${sessionId}`);
export const compareScenarios = (runIdA, runIdB) =>
  request('/replays/compare', {
    method: 'POST',
    body: JSON.stringify({ run_id_a: runIdA, run_id_b: runIdB }),
  });
export const compareBeforeAfter = (runId, timeA, timeB) =>
  request(`/replays/${runId}/before-after`, {
    method: 'POST',
    body: JSON.stringify({ time_a: parseInt(timeA), time_b: parseInt(timeB) }),
  });
export const generateDrillReport = (runId, format = 'json') =>
  request(`/replays/${runId}/report`, {
    method: 'POST',
    body: JSON.stringify({ format: format }),
  });
export const setReplayMode = (runId, mode, sessionId = 'default') =>
  request(`/replays/${runId}/mode`, {
    method: 'POST',
    body: JSON.stringify({ mode: mode, session_id: sessionId }),
  });

// --- Post-Incident Review & Continuous Regression (M10 Phase 4) ---
export const getPIR = (runId) => request(`/replays/${runId}/pir`);
export const getPIRFindings = (runId) => request(`/replays/${runId}/findings`);
export const getPIRRootCauses = (runId) => request(`/replays/${runId}/root-causes`);
export const exportPIRReport = (runId, format = 'json') =>
  request(`/replays/${runId}/pir/report`, {
    method: 'POST',
    body: JSON.stringify({ format: format }),
  });
export const comparePIRs = (runIdA, runIdB) =>
  request('/replays/pir/compare', {
    method: 'POST',
    body: JSON.stringify({ run_id_a: runIdA, run_id_b: runIdB }),
  });
export const getRegressionBaseline = () => request('/regression/baseline');
export const createRegressionBaseline = (description = 'Standard Regression Baseline') =>
  request('/regression/baseline/create', {
    method: 'POST',
    body: JSON.stringify({ description: description }),
  });
export const runRegressionSuite = (runId = null) =>
  request('/regression/run', {
    method: 'POST',
    body: JSON.stringify({ run_id: runId }),
  });
export const getRegressionResults = () => request('/regression/results');
export const getRegressionResult = (runId) => request(`/regression/results/${runId}`);

// --- Optimization & Decision Intelligence APIs (M11 Phase 1 & 2) ---
export const getOptimizationSnapshot = () => request('/optimization/snapshot');
export const getOptimizationRecommendations = () => request('/optimization/recommendations');
export const getOptimizationRecommendation = (recId) => request(`/optimization/recommendations/${recId}`);
export const simulateOptimizationRecommendation = (recId) =>
  request('/optimization/simulate', {
    method: 'POST',
    body: JSON.stringify({ recommendation_id: recId }),
  });
export const approveOptimizationRecommendation = (recId, data = {}) =>
  request(`/optimization/recommendations/${recId}/approve`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
export const rejectOptimizationRecommendation = (recId, data = {}) =>
  request(`/optimization/recommendations/${recId}/reject`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
export const getOptimizationExecutions = (limit = 50) => request(`/optimization/executions?limit=${limit}`);
export const getOptimizationExecution = (execId) => request(`/optimization/executions/${execId}`);
export const getOptimizationCopilotSummary = () => request('/optimization/copilot/summary');
export const getOptimizationHealth = () => request('/optimization/health');

// M11 Phase 3: Adaptive Policy & Bounded Autonomy APIs
export const getPolicyOverview = () => request('/optimization/policy');
export const getPolicyConfig = () => request('/optimization/policy/config');
export const setPolicyMode = (mode, operatorId = 'OPERATOR_COMMANDER', reason = '') =>
  request('/optimization/policy/mode', {
    method: 'POST',
    body: JSON.stringify({ mode, operator_id: operatorId, reason }),
  });
export const toggleKillSwitch = (action = 'ENGAGE', operatorId = 'OPERATOR_COMMANDER', reason = '') =>
  request('/optimization/policy/kill-switch', {
    method: 'POST',
    body: JSON.stringify({ action, operator_id: operatorId, reason }),
  });
export const getPolicyPerformance = () => request('/optimization/policy/performance');
export const getPolicyDecisions = (limit = 50) => request(`/optimization/policy/decisions?limit=${limit}`);
export const evaluatePolicy = (recommendationId) =>
  request('/optimization/policy/evaluate', {
    method: 'POST',
    body: JSON.stringify({ recommendation_id: recommendationId }),
  });
export const rollbackExecution = (executionId, operatorId = 'OPERATOR_DISPATCHER', reason = '') =>
  request(`/optimization/policy/rollback/${executionId}`, {
    method: 'POST',
    body: JSON.stringify({ operator_id: operatorId, reason }),
  });

// M11 Phase 4: Operational Learning, Calibration & Adaptation APIs
export const getLearningReport = () => request('/optimization/learning');
export const getLearningPerformance = (minSim = 0, maxSim = null) => {
  const q = maxSim !== null ? `?min_sim_time=${minSim}&max_sim_time=${maxSim}` : `?min_sim_time=${minSim}`;
  return request(`/optimization/learning/performance${q}`);
};
export const getLearningCalibration = () => request('/optimization/learning/calibration');
export const getLearningDrift = () => request('/optimization/learning/drift');
export const getLearningRecommendations = () => request('/optimization/learning/recommendations');
export const getLearningRecommendation = (id) => request(`/optimization/learning/recommendations/${id}`);
export const comparePolicies = (policyA = null, policyB = null) =>
  request('/optimization/learning/compare', {
    method: 'POST',
    body: JSON.stringify({ policy_a: policyA, policy_b: policyB }),
  });
export const approveLearningRecommendation = (id, operatorId = 'OPERATOR_DISPATCHER') =>
  request(`/optimization/learning/recommendations/${id}/approve`, {
    method: 'POST',
    body: JSON.stringify({ operator_id: operatorId }),
  });
export const rejectLearningRecommendation = (id, operatorId = 'OPERATOR_DISPATCHER', reason = '') =>
  request(`/optimization/learning/recommendations/${id}/reject`, {
    method: 'POST',
    body: JSON.stringify({ operator_id: operatorId, reason }),
  });
export const rollbackPolicyVersion = (policyVersion, operatorId = 'OPERATOR_DISPATCHER', reason = '') =>
  request(`/optimization/learning/rollback/${policyVersion}`, {
    method: 'POST',
    body: JSON.stringify({ operator_id: operatorId, reason }),
  });


// --- Real-Time Server-Sent Events Stream (M13 Phase 1) ---
export function connectEventStream({
  onEvent,
  onSnapshot,
  onGap,
  onError,
  onOpen,
  sinceSequence = null,
} = {}) {
  let controller = new AbortController();
  let isClosed = false;
  let currentSeq = sinceSequence;
  let reconnectTimer = null;
  let reconnectAttempts = 0;

  async function startStream() {
    if (isClosed) return;
    controller = new AbortController();

    const token = localStorage.getItem('raah_token') || sessionStorage.getItem('raah_token');
    const headers = {
      'Accept': 'text/event-stream',
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    let url = `${BASE_URL}/events/stream`;
    if (currentSeq !== null && currentSeq !== undefined) {
      url += `?since_sequence=${encodeURIComponent(currentSeq)}`;
    }

    try {
      const response = await fetch(url, {
        headers,
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`SSE stream failed with HTTP ${response.status}`);
      }

      if (onOpen) onOpen();
      reconnectAttempts = 0;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (!isClosed) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // Retain incomplete line

        let currentEvent = 'message';
        let currentId = null;
        let currentData = '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) {
            if (currentData) {
              try {
                const parsed = JSON.parse(currentData);
                if (currentId !== null) {
                  currentSeq = parseInt(currentId, 10);
                } else if (parsed.sequence !== undefined) {
                  currentSeq = parsed.sequence;
                }

                if (parsed.event_type === 'STATE_SNAPSHOT') {
                  if (parsed.payload && parsed.payload.gap_detected && onGap) {
                    onGap(parsed);
                  } else if (onSnapshot) {
                    onSnapshot(parsed);
                  }
                }

                if (onEvent) onEvent(parsed);
              } catch (parseErr) {
                console.warn('[SSE] JSON parse error:', parseErr);
              }
            }
            currentEvent = 'message';
            currentId = null;
            currentData = '';
            continue;
          }

          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('id:')) {
            currentId = trimmed.slice(3).trim();
          } else if (trimmed.startsWith('data:')) {
            currentData += trimmed.slice(5).trim();
          }
        }
      }
    } catch (err) {
      if (!isClosed && err.name !== 'AbortError') {
        console.warn('[SSE] Stream disconnected:', err.message);
        if (onError) onError(err);
        scheduleReconnect();
      }
    }
  }

  function scheduleReconnect() {
    if (isClosed || reconnectTimer) return;
    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts - 1), 10000);
    console.log(`[SSE] Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttempts})...`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      startStream();
    }, delay);
  }

  startStream();

  return {
    close() {
      isClosed = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      controller.abort();
    },
    getCurrentSequence() {
      return currentSeq;
    },
  };
}



