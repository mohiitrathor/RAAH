/**
 * Historical Analytics View Controller
 * Manages run selection, KPI aggregation, redirection analysis, and incident audit querying.
 */

import * as api from '../api.js';
import { showToast } from './toasts.js';

import { navigation } from '../navigation.js';

let selectedRunId = null;

export function setupAnalytics() {
  const btnRefresh = document.getElementById('btn-refresh-analytics');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => loadRuns());
  }

  navigation.on('analytics', 'activate', () => {
    loadRuns();
  });
}

async function loadRuns() {
  const runSelect = document.getElementById('select-analytics-run');
  const btnRefresh = document.getElementById('btn-refresh-analytics');
  if (!runSelect) return;

  try {
    const runs = await api.getRuns();
    if (!runs || runs.length === 0) {
      runSelect.innerHTML = '<option value="">No simulation runs recorded</option>';
      renderEmptyAnalyticsState();
      return;
    }

    const currentSelected = selectedRunId || runs[0].run_id;
    selectedRunId = currentSelected;

    runSelect.innerHTML = runs.map(r => `
      <option value="${r.run_id}" ${r.run_id === selectedRunId ? 'selected' : ''}>
        Run #${r.run_id} (${r.status}) — ${r.total_incidents} Incidents | ${r.total_redirections} Redirections
      </option>
    `).join('');

    runSelect.onchange = () => {
      selectedRunId = parseInt(runSelect.value, 10);
      loadAnalyticsData(selectedRunId);
    };

    if (btnRefresh) {
      btnRefresh.onclick = () => loadAnalyticsData(selectedRunId);
    }

    await loadAnalyticsData(selectedRunId);
  } catch (err) {
    showToast('Analytics Error', `Failed to load runs: ${err.message}`, 'danger');
  }
}

function renderEmptyAnalyticsState() {
  const tbody = document.getElementById('analytics-incidents-tbody');
  if (tbody) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="padding: 0; border: none;">
          <div class="operational-archive-ledger-empty">
            <div class="archive-ledger-status-line">
              <span class="badge-status-armed">ARCHIVE LEDGER ACTIVE</span>
              <span class="font-mono text-muted">AWAITING SIMULATION INGESTION &bull; DISPATCH STATE: 0 LOGGED CALLS</span>
            </div>
            <div class="archive-ledger-body">
              <div class="archive-ledger-title">Awaiting Incident Ingestion &amp; Dispatch Discharge</div>
              <div class="archive-ledger-desc">
                Historical triage classifications, assigned ambulance fleet telemetry, destination hospitals, and initial transit ETAs index here in real time as simulation dispatches are executed.
              </div>
              <div class="archive-ledger-specs">
                <div class="spec-item"><span class="spec-k">Coverage Sector:</span> <span class="spec-v">Jaipur Urban Core</span></div>
                <div class="spec-item"><span class="spec-k">Monitored Facilities:</span> <span class="spec-v">22 Receiving Hospitals</span></div>
                <div class="spec-item"><span class="spec-k">Tracking Fleet:</span> <span class="spec-v">500 ALS/BLS Units</span></div>
                <div class="spec-item"><span class="spec-k">Persistence:</span> <span class="spec-v">SQLite Operational Ledger</span></div>
              </div>
            </div>
          </div>
        </td>
      </tr>
    `;
  }
  const listContainer = document.getElementById('analytics-decisions-list');
  if (listContainer) {
    listContainer.innerHTML = `
      <div class="operational-archive-audit-empty">
        <div class="audit-empty-badge">DYNAMIC DIVERT MONITOR &bull; ARMED</div>
        <div class="audit-empty-title">Zero Redirection Decisions Required</div>
        <div class="audit-empty-desc">
          All dispatched units proceeded directly to initial destination hospitals without dynamic diversion.
          Redirection engine triggers automatically if destination emergency departments report saturation or unexpected traffic delays exceed threshold.
        </div>
        <div class="audit-empty-specs">
          <div>Threshold: <strong>&ge; 1.5m ETA Delta</strong></div>
          <div>Bed Safeguard: <strong>Preserve ICU Capacity</strong></div>
        </div>
      </div>
    `;
  }
}

async function loadAnalyticsData(runId) {
  if (!runId) return;

  try {
    const [summary, incidents, decisions] = await Promise.all([
      api.getAnalyticsSummary(runId),
      api.getHistoricalIncidents(runId, 50, 0),
      api.getHistoricalDecisions(runId),
    ]);

    renderKPIs(summary);
    renderRedirectionIntelligence(summary, decisions);
    renderIncidentTable(incidents);
  } catch (err) {
    showToast('Analytics Error', err.message, 'danger');
  }
}

function renderKPIs(summary) {
  // Scorecard values
  document.getElementById('kpi-total-dispatches').textContent = summary.total_incidents ?? 0;
  document.getElementById('kpi-avg-eta').textContent = `${(summary.average_initial_eta ?? 0).toFixed(1)}m`;
  document.getElementById('kpi-redir-rate').textContent = `${(summary.redirections?.redirection_rate_pct ?? 0).toFixed(1)}%`;
  document.getElementById('kpi-eta-saved').textContent = `${(summary.redirections?.total_eta_saved ?? 0).toFixed(1)}m`;
  document.getElementById('kpi-saturation-events').textContent = summary.hospital_saturation_events ?? 0;
  document.getElementById('kpi-ml-confidence').textContent = `${((summary.average_ml_confidence ?? 0) * 100).toFixed(1)}%`;

  // Priority breakdown bar
  const pMap = summary.incidents_by_priority || {};
  const total = summary.total_incidents || 1;
  const p1Pct = ((pMap['P1'] || 0) / total) * 100;
  const p2Pct = ((pMap['P2'] || 0) / total) * 100;
  const p3Pct = ((pMap['P3'] || 0) / total) * 100;
  const p4Pct = ((pMap['P4'] || 0) / total) * 100;
  const p5Pct = ((pMap['P5'] || 0) / total) * 100;

  const bar = document.getElementById('kpi-priority-bar');
  if (bar) {
    bar.innerHTML = `
      <div style="width:${p1Pct}%; background:var(--p1-critical);" title="P1 Critical: ${pMap['P1'] || 0}"></div>
      <div style="width:${p2Pct}%; background:var(--p2-emergency);" title="P2 Emergency: ${pMap['P2'] || 0}"></div>
      <div style="width:${p3Pct}%; background:var(--p3-urgent);" title="P3 Urgent: ${pMap['P3'] || 0}"></div>
      <div style="width:${p4Pct}%; background:var(--p4-semi);" title="P4 Semi-Urgent: ${pMap['P4'] || 0}"></div>
      <div style="width:${p5Pct}%; background:var(--p5-non);" title="P5 Non-Urgent: ${pMap['P5'] || 0}"></div>
    `;
  }
}

function renderRedirectionIntelligence(summary, decisions) {
  const r = summary.redirections || {};
  const totalR = r.total || 0;
  const aiPct = totalR > 0 ? ((r.ai_autonomous / totalR) * 100).toFixed(0) : '0';
  const opPct = totalR > 0 ? ((r.operator_manual / totalR) * 100).toFixed(0) : '0';

  const statsContainer = document.getElementById('analytics-redir-stats');
  if (statsContainer) {
    statsContainer.innerHTML = `
      <div class="analytics-stat-row">
        <span style="color: var(--text-secondary);">AI Autonomous Redirections:</span>
        <strong style="color: var(--accent-cyan); font-family: var(--font-mono);">${r.ai_autonomous || 0} (${aiPct}%)</strong>
      </div>
      <div class="analytics-stat-row">
        <span style="color: var(--text-secondary);">Operator Manual Overrides:</span>
        <strong style="color: var(--warning-amber); font-family: var(--font-mono);">${r.operator_manual || 0} (${opPct}%)</strong>
      </div>
      <div class="analytics-stat-row">
        <span style="color: var(--text-secondary);">Mean Time Saved / Redirection:</span>
        <strong style="color: var(--success-emerald); font-family: var(--font-mono);">${(r.avg_eta_saved || 0).toFixed(2)} min</strong>
      </div>
      <div class="analytics-stat-row">
        <span style="color: var(--text-secondary);">Total Fleet Transit Saved:</span>
        <strong style="color: var(--success-emerald); font-family: var(--font-mono);">${(r.total_eta_saved || 0).toFixed(1)} min</strong>
      </div>
    `;
  }

  // Decisions list
  const listContainer = document.getElementById('analytics-decisions-list');
  if (listContainer) {
    if (!decisions || decisions.length === 0) {
      listContainer.innerHTML = `
        <div class="operational-archive-audit-empty">
          <div class="audit-empty-badge">DYNAMIC DIVERT MONITOR &bull; ARMED</div>
          <div class="audit-empty-title">Zero Redirection Decisions Required</div>
          <div class="audit-empty-desc">
            All dispatched units proceeded directly to initial destination hospitals without dynamic diversion.
            Redirection engine triggers automatically if destination emergency departments report saturation or unexpected traffic delays exceed threshold.
          </div>
          <div class="audit-empty-specs">
            <div>Threshold: <strong>&ge; 1.5m ETA Delta</strong></div>
            <div>Bed Safeguard: <strong>Preserve ICU Capacity</strong></div>
          </div>
        </div>
      `;
      return;
    }

    listContainer.innerHTML = decisions.map(d => `
      <div class="decision-mini-item">
        <div class="decision-mini-header">
          <span class="decision-pill ${d.trigger_type === 'OPERATOR_MANUAL' ? 'pill-operator' : 'pill-ai'}">
            ${d.trigger_type === 'OPERATOR_MANUAL' ? 'OPERATOR OVERRIDE' : 'AUTONOMOUS DIVERT'}
          </span>
          <span class="decision-time">T+${d.sim_time}m</span>
        </div>
        <div class="decision-route">
          <span>${d.original_hospital_id || 'Initial'}</span>
          <i data-lucide="arrow-right" style="width: 12px; height: 12px; opacity: 0.6;"></i>
          <strong style="color: var(--text-primary);">${d.new_hospital_id}</strong>
        </div>
        <div class="decision-reason" style="color: var(--text-secondary); font-size: 11px; margin-top: 3px;">${d.reason}</div>
        ${d.eta_saved !== null && d.eta_saved !== undefined ? `
          <div class="decision-delta" style="margin-top: 4px; font-size: 10px; color: var(--success-emerald); font-family: var(--font-mono);">
            ETA Saved: <strong>${d.eta_saved.toFixed(1)}m</strong> (${d.eta_before}m → ${d.eta_after}m)
          </div>
        ` : ''}
      </div>
    `).join('');
    if (window.lucide) window.lucide.createIcons();
  }
}

function renderIncidentTable(incidents) {
  const tbody = document.getElementById('analytics-incidents-tbody');
  if (!tbody) return;

  if (!incidents || incidents.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="padding: 0; border: none;">
          <div class="operational-archive-ledger-empty">
            <div class="archive-ledger-status-line">
              <span class="badge-status-armed">ARCHIVE LEDGER ACTIVE</span>
              <span class="font-mono text-muted">SESSION #${selectedRunId || 'CURRENT'} &bull; DISPATCH STATE: 0 LOGGED CALLS</span>
            </div>
            <div class="archive-ledger-body">
              <div class="archive-ledger-title">Awaiting Incident Ingestion &amp; Dispatch Discharge</div>
              <div class="archive-ledger-desc">
                Historical triage classifications, assigned ambulance fleet telemetry, destination hospitals, and initial transit ETAs index here in real time as simulation dispatches are executed.
              </div>
              <div class="archive-ledger-specs">
                <div class="spec-item"><span class="spec-k">Coverage Sector:</span> <span class="spec-v">Jaipur Urban Core</span></div>
                <div class="spec-item"><span class="spec-k">Monitored Facilities:</span> <span class="spec-v">22 Receiving Hospitals</span></div>
                <div class="spec-item"><span class="spec-k">Tracking Fleet:</span> <span class="spec-v">500 ALS/BLS Units</span></div>
                <div class="spec-item"><span class="spec-k">Persistence:</span> <span class="spec-v">SQLite Operational Ledger</span></div>
              </div>
            </div>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = incidents.map(inc => {
    const pClass = `p${inc.priority || 3}`;
    return `
      <tr>
        <td class="font-mono" style="font-weight: 700; color: var(--text-primary);">#${inc.incident_id}</td>
        <td><span class="priority-pill ${pClass}">P${inc.priority} ${inc.predicted_severity || 'Urgent'}</span></td>
        <td style="color: var(--text-primary);">${inc.condition || 'General Emergency'}</td>
        <td class="font-mono" style="color: var(--accent-cyan);">${inc.ambulance_id || '—'}</td>
        <td class="font-mono" style="color: var(--text-secondary);">${inc.final_hospital_id || inc.initial_hospital_id || '—'}</td>
        <td class="font-mono" style="font-weight: 700; color: var(--text-primary);">${inc.initial_eta_minutes !== null && inc.initial_eta_minutes !== undefined ? `${inc.initial_eta_minutes.toFixed(1)}m` : '—'}</td>
        <td><span class="status-pill status-${(inc.dispatch_status || 'en_route').toLowerCase()}">${inc.dispatch_status || 'EN_ROUTE'}</span></td>
      </tr>
    `;
  }).join('');
}
