/**
 * Incident Detail Drawer & Operator Control Plane
 * Slide-out panel providing clinical vitals, ML confidence, fleet telemetry,
 * hospital capacity, decision history, and operator manual redirection controls.
 */

import * as api from '../api.js';
import { store } from '../state.js';
import { tacticalMap } from '../map.js';
import { showToast } from './toasts.js';
import { confirmModal } from './confirmation_modal.js';
import { openExplanationModal } from './explanation_modal.js';

let drawerElement = null;
let currentIncidentId = null;
let currentIncidentData = null;

export function setupDetailDrawer() {
  drawerElement = document.getElementById('drawer-incident-detail');
  if (!drawerElement) {
    drawerElement = document.createElement('aside');
    drawerElement.id = 'drawer-incident-detail';
    drawerElement.className = 'tactical-drawer';
    document.body.appendChild(drawerElement);
  }

  // Subscribe to store updates to keep drawer data fresh in real-time
  store.subscribe((state, changedKeys) => {
    if (!currentIncidentId || !drawerElement || !drawerElement.classList.contains('open')) return;

    // Full re-render when decision evidence changes (e.g. reroute executed)
    if (changedKeys.includes('decisions')) {
      const incident = (state.activeIncidents && state.activeIncidents.find(i => i.incident_id === currentIncidentId))
        || (state.incidents && state.incidents.get(currentIncidentId))
        || currentIncidentData;
      if (incident) renderDrawerContent(incident);
      return;
    }

    if (changedKeys.includes('activeIncidents') || changedKeys.includes('ambulances') || changedKeys.includes('hospitals')) {
      let incident = (state.activeIncidents && state.activeIncidents.find(i => i.incident_id === currentIncidentId))
        || (state.incidents && state.incidents.get(currentIncidentId))
        || currentIncidentData;

      if (!incident) return;

      // Sync latest assigned ambulance telemetry and arrival status
      if (incident.ambulance_id) {
        const amb = state.ambulances.get(String(incident.ambulance_id));
        if (amb) {
          if (amb.status === 'ARRIVED') {
            incident.status = 'ARRIVED';
            incident.eta_minutes = 0.0;
          } else if (amb.eta_minutes !== undefined && amb.eta_minutes !== null) {
            incident.eta_minutes = amb.eta_minutes;
          }
        }
      }

      currentIncidentData = incident;

      // If drawer is already open for this incident, perform targeted in-place DOM updates (no flicker!)
      if (drawerElement.dataset.renderedIncidentId === String(incident.incident_id)) {
        updateDrawerTelemetry(incident);
      } else {
        renderDrawerContent(incident);
      }
    }
  });
}

/**
 * Open detail drawer for a specific incident ID
 */
export async function openIncidentDetail(incidentId) {
  currentIncidentId = incidentId;
  setupDetailDrawer();

  // Find incident from store
  let incident = (store.state.activeIncidents && store.state.activeIncidents.find(i => i.incident_id === incidentId))
    || (store.state.incidents && store.state.incidents.get(incidentId));

  if (!incident) {
    // Attempt to fetch from API
    try {
      incident = await api.getIncident(incidentId);
      if (incident && store.state.incidents) {
        store.state.incidents.set(incident.incident_id, incident);
      }
    } catch (err) {
      showToast('Incident Not Found', `ID #${incidentId} is not in active state.`, 'warning');
      return;
    }
  }

  currentIncidentData = incident;
  renderDrawerContent(incident);
  drawerElement.classList.add('open');
}

export function closeIncidentDetail() {
  if (drawerElement) {
    drawerElement.classList.remove('open');
    drawerElement.dataset.renderedIncidentId = '';
  }
  currentIncidentId = null;
  currentIncidentData = null;
}

async function renderDrawerContent(incident) {
  const ambulance = store.state.ambulances.get(String(incident.ambulance_id));
  const hospital = store.state.hospitals.get(String(incident.hospital_id));

  // Fetch incident-specific decision evidence (M13.3) and legacy decisions
  let evidenceRecords = [];
  let evidenceError = null;
  try {
    const evidenceRes = await api.getIncidentDecisionEvidence(incident.incident_id);
    evidenceRecords = (evidenceRes && evidenceRes.records) ? evidenceRes.records : [];
  } catch (err) {
    evidenceError = err.message || 'Evidence service unavailable';
  }

  let decisions = [];
  try {
    decisions = await api.getIncidentDecisions(incident.incident_id);
  } catch (_) {}

  const pClass = `p${incident.priority}`;
  const isArrived = (ambulance && ambulance.status === 'ARRIVED') || incident.status === 'ARRIVED';
  const isEnRoute = ambulance && ambulance.status === 'EN_ROUTE' && !isArrived;
  const effectiveStatus = isArrived ? 'ARRIVED' : (incident.status || 'DISPATCHED');
  const etaDisplay = isArrived
    ? '0.0 min (Arrived)'
    : (incident.eta_minutes !== null && incident.eta_minutes !== undefined
        ? `${Number(incident.eta_minutes).toFixed(1)} min`
        : (ambulance && ambulance.eta_minutes !== null && ambulance.eta_minutes !== undefined
            ? `${Number(ambulance.eta_minutes).toFixed(1)} min`
            : '—'));

  const allHospitals = Array.from((store.state.hospitals && store.state.hospitals.values()) || []);
  const suitableHospitals = allHospitals
    .filter(h => {
      if (String(h.hospital_id) === String(incident.hospital_id)) return false;
      if (h.is_full || h.available_beds <= 0) return false;
      if (incident.severity === 'Critical' && h.available_icu <= 0) return false;
      return true;
    })
    .sort((a, b) => b.available_beds - a.available_beds)
    .slice(0, 40);

  drawerElement.innerHTML = `
    <div class="drawer-header">
      <div class="drawer-title-group">
        <span class="incident-id-badge">#${incident.incident_id}</span>
        <span class="priority-pill ${pClass}">P${incident.priority} ${incident.severity}</span>
        <span id="drawer-header-status-pill" class="status-pill status-${effectiveStatus.toLowerCase()}">${effectiveStatus}</span>
      </div>
      <button class="drawer-close-btn" title="Close Drawer">&times;</button>
    </div>

    <div class="drawer-body">
      <!-- Section 1: Clinical Triage & ML Confidence -->
      <div class="drawer-section">
        <div class="section-title">
          <i data-lucide="activity"></i>
          <span>Clinical Triage & ML Evaluation</span>
        </div>
        <div class="detail-grid">
          <div class="detail-cell">
            <span class="label">Condition</span>
            <span class="value">${incident.condition || 'General Emergency'}</span>
          </div>
          <div class="detail-cell">
            <span class="label">Priority Level</span>
            <span class="value">${incident.priority ? `P${incident.priority}` : 'P3'}</span>
          </div>
          <div class="detail-cell">
            <span class="label">Severity</span>
            <span class="value ${incident.severity === 'Critical' ? 'text-danger' : 'text-warning'}">${incident.severity}</span>
          </div>
          <div class="detail-cell">
            <span class="label">Lifecycle Status</span>
            <span id="drawer-cell-lifecycle" class="value">${effectiveStatus}</span>
          </div>
        </div>
      </div>

      <!-- Section 2: Fleet Unit Assignment -->
      <div class="drawer-section">
        <div class="section-title">
          <i data-lucide="truck"></i>
          <span>Assigned Emergency Vehicle</span>
        </div>
        ${ambulance ? `
          <div class="detail-grid">
            <div class="detail-cell">
              <span class="label">Unit ID</span>
              <span class="value font-mono highlight">${ambulance.ambulance_id}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Capability Type</span>
              <span class="value">${ambulance.ambulance_type}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Operational Status</span>
              <span id="drawer-amb-status" class="value status-text-${(ambulance.status || effectiveStatus).toLowerCase()}">${ambulance.status || effectiveStatus}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Remaining Route ETA</span>
              <span id="drawer-amb-eta" class="value font-mono highlight ${isArrived ? 'text-success' : 'text-accent'}">${etaDisplay}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Route Distance</span>
              <span id="drawer-amb-distance" class="value font-mono">${isArrived ? '0.0 km' : (ambulance.route_distance_km ? `${ambulance.route_distance_km.toFixed(1)} km` : '—')}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Traffic / Road</span>
              <span id="drawer-amb-traffic" class="value font-mono">${ambulance.traffic_level || 'NORMAL'} / ${ambulance.road_condition || 'GOOD'}</span>
            </div>
          </div>
        ` : `
          <div class="empty-placeholder">No ambulance assigned to this incident.</div>
        `}
      </div>

      <!-- Section 3: Destination Hospital -->
      <div class="drawer-section">
        <div class="section-title">
          <i data-lucide="building-2"></i>
          <span>Destination Medical Center</span>
        </div>
        ${hospital ? `
          <div class="detail-grid">
            <div class="detail-cell">
              <span class="label">Facility ID</span>
              <span id="drawer-hosp-id" class="value font-mono highlight">${hospital.hospital_id}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Classification</span>
              <span class="value">${hospital.hospital_type}</span>
            </div>
            <div class="detail-cell">
              <span class="label">Available General Beds</span>
              <span id="drawer-hosp-beds" class="value font-mono ${hospital.is_full ? 'text-danger' : 'text-success'}">
                ${hospital.available_beds} / ${hospital.capacity}
              </span>
            </div>
            <div class="detail-cell">
              <span class="label">Available ICU Beds</span>
              <span id="drawer-hosp-icu" class="value font-mono ${hospital.available_icu <= 0 ? 'text-danger' : 'text-success'}">
                ${hospital.available_icu} / ${hospital.icu_capacity}
              </span>
            </div>
          </div>
          <div id="drawer-hosp-alert" class="alert-banner danger" style="margin-top: 8px; ${hospital.is_full ? '' : 'display: none;'}">
            <i data-lucide="alert-octagon"></i>
            <span>CRITICAL: Hospital reached 100% saturation! Redirection recommended.</span>
          </div>
        ` : `
          <div class="empty-placeholder">No destination hospital assigned.</div>
        `}
      </div>

      <!-- Section 4: Decision Evidence & Audit Trail (M13.3) -->
      <div class="drawer-section">
        <div class="section-title">
          <i data-lucide="git-merge"></i>
          <span>Decision Evidence &amp; Audit Trail (${evidenceRecords.length || decisions.length})</span>
        </div>
        ${evidenceError ? `
          <div class="evidence-error-box" style="padding: 8px; font-size: 11px;">
            <span>Unable to load decision evidence from server. (${evidenceError})</span>
          </div>
        ` : (evidenceRecords.length > 0 ? `
          <div class="decision-mini-list">
            ${evidenceRecords.map(rec => {
              const ev = rec.evidence;
              const decType = ev.decision_type || 'DECISION';
              const typeClass = `pill-${decType.toLowerCase().replace(/_/g, '-')}`;
              const explanationPreview = rec.explanation || 'Operational decision executed';
              return `
                <div class="decision-mini-item" data-evidence-id="${ev.evidence_id}">
                  <div class="decision-mini-header">
                    <span class="decision-pill ${typeClass}">
                      ${decType}
                    </span>
                    <span class="decision-time">T+${ev.sim_time}m</span>
                  </div>
                  <div class="decision-action-title font-mono" style="font-size: 11px; color: var(--text-primary); margin: 3px 0; font-weight: 600;">
                    ${ev.action || 'DECISION'}
                  </div>
                  <div class="decision-reason" style="font-size: 11px; color: var(--text-secondary); margin-bottom: 6px;" title="${rec.explanation}">
                    ${explanationPreview.length > 110 ? explanationPreview.substring(0, 107) + '...' : explanationPreview}
                  </div>
                  <div style="display: flex; justify-content: flex-end;">
                    <button type="button" class="btn-tactical btn-inspect-sm btn-inspect-evidence" data-evidence-id="${ev.evidence_id}">
                      <i data-lucide="search" style="width: 12px; height: 12px;"></i> Inspect Rationale
                    </button>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        ` : (decisions.length > 0 ? `
          <div class="decision-mini-list">
            ${decisions.map(d => `
              <div class="decision-mini-item">
                <div class="decision-mini-header">
                  <span class="decision-pill ${d.reason && d.reason.includes('[OPERATOR]') ? 'pill-operator' : 'pill-ai'}">
                    ${d.reason && d.reason.includes('[OPERATOR]') ? 'OPERATOR' : 'AI AUTO'}
                  </span>
                  <span class="decision-time">T+${d.time}m</span>
                </div>
                <div class="decision-route">
                  <span>${d.original_hospital}</span>
                  <i data-lucide="arrow-right" style="width: 12px; height: 12px;"></i>
                  <strong>${d.new_hospital}</strong>
                </div>
                <div class="decision-reason">${d.reason}</div>
              </div>
            `).join('')}
          </div>
        ` : `
          <div class="empty-placeholder" style="padding: 8px 0; font-size: 11px;">
            No decision evidence recorded for this incident. (Initial dispatch occurred prior to evidence recording or evidence unavailable).
          </div>
        `))}
      </div>

      <!-- Section 5: Operator Control Actions -->
      <div class="drawer-section operator-action-section">
        <div class="section-title">
          <i data-lucide="shield-alert"></i>
          <span>Operator Override Controls</span>
        </div>
        <div style="margin-bottom: 8px;">
          <label style="display: block; font-size: 11px; color: var(--text-secondary); margin-bottom: 4px; font-weight: 500;">
            Destination Override Facility:
          </label>
          <select id="select-reroute-hospital" class="tactical-select" style="width: 100%; font-size: 11px; padding: 6px 8px; background: var(--bg-surface-2, #1e293b); color: var(--text-primary, #f8fafc); border: 1px solid var(--border-subtle, #334155); border-radius: 4px;" ${!isEnRoute ? 'disabled' : ''}>
            <option value="">Auto-Assign Best Available Facility</option>
            ${suitableHospitals.map(h => `<option value="${h.hospital_id}">${h.hospital_id} (${h.hospital_type}) — ${h.available_beds} beds${incident.severity === 'Critical' ? ` / ${h.available_icu} ICU` : ''}</option>`).join('')}
          </select>
        </div>
        <div class="operator-btn-row">
          <button class="btn-tactical btn-evaluate" id="btn-eval-reroute" ${!isEnRoute ? 'disabled' : ''}>
            <i data-lucide="search"></i> Evaluate Reroute
          </button>
          <button class="btn-tactical btn-execute" id="btn-exec-reroute" ${!isEnRoute ? 'disabled' : ''}>
            <i data-lucide="corner-up-right"></i> Execute Reroute
          </button>
        </div>
        <div id="drawer-reroute-notice" style="display: ${isEnRoute ? 'none' : 'block'}; margin-top: 8px;">
          ${isArrived ? `
            <div class="alert-banner info" style="font-size: 11px; padding: 6px 10px; display: flex; align-items: center; gap: 6px; background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); border-radius: 4px; color: #86efac;">
              <i data-lucide="check-circle-2" style="width: 14px; height: 14px; color: #22c55e; flex-shrink: 0;"></i>
              <span>Ambulance has arrived at destination hospital. Redirection is locked.</span>
            </div>
          ` : `
            <div class="alert-banner warning" style="font-size: 11px; padding: 6px 10px; display: flex; align-items: center; gap: 6px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 4px; color: #fcd34d;">
              <i data-lucide="alert-triangle" style="width: 14px; height: 14px; color: #f59e0b; flex-shrink: 0;"></i>
              <span>Ambulance is not en route. Redirection unavailable.</span>
            </div>
          `}
        </div>
        <div id="eval-result-card" class="eval-result-card" style="display: none;"></div>
      </div>
    </div>
  `;

  if (window.lucide) window.lucide.createIcons();

  // Bind close button
  drawerElement.querySelector('.drawer-close-btn').addEventListener('click', closeIncidentDetail);

  // Bind Inspect Rationale buttons (M13.3)
  drawerElement.querySelectorAll('.btn-inspect-evidence').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const eid = btn.getAttribute('data-evidence-id');
      const rec = evidenceRecords.find(r => r.evidence && r.evidence.evidence_id === eid);
      openExplanationModal({ evidenceId: eid, explanationData: rec });
    });
  });

  // Bind Evaluate Reroute
  const btnEval = drawerElement.querySelector('#btn-eval-reroute');
  const evalCard = drawerElement.querySelector('#eval-result-card');
  const selectHosp = drawerElement.querySelector('#select-reroute-hospital');

  btnEval.addEventListener('click', async () => {
    const amb = store.state.ambulances.get(String(incident.ambulance_id));
    if (!amb || amb.status !== 'EN_ROUTE' || incident.status === 'ARRIVED') {
      showToast(
        'Evaluation Unavailable',
        `Incident #${incident.incident_id} cannot be evaluated for redirection: ambulance is ${amb ? amb.status : 'not en route'}.`,
        'warning'
      );
      btnEval.disabled = true;
      btnExec.disabled = true;
      if (selectHosp) selectHosp.disabled = true;
      return;
    }

    btnEval.disabled = true;
    evalCard.style.display = 'none';

    try {
      const evalRes = await api.checkRedirection(incident.incident_id);
      evalCard.style.display = 'block';

      if (evalRes.redirect && evalRes.alternative_hospital) {
        const alt = evalRes.alternative_hospital;
        evalCard.className = 'eval-result-card recommended';
        evalCard.innerHTML = `
          <div class="eval-header text-warning">
            <i data-lucide="alert-circle" style="width: 15px; height: 15px;"></i>
            <strong>Redirection Recommended</strong>
          </div>
          <div class="eval-detail">
            <div>Recommended Facility: <strong>${alt.hospital_id}</strong> (${alt.hospital_type})</div>
            <div>Available Beds: <strong>${alt.available_beds}</strong> | ICU: <strong>${alt.available_icu}</strong></div>
            <div>Trigger Reason: <em>${evalRes.reason}</em></div>
            ${evalRes.eta_saved !== null ? `<div>Estimated Time Saved: <strong class="text-success">${evalRes.eta_saved} min</strong></div>` : ''}
          </div>
        `;
        if (selectHosp && !selectHosp.value) {
          selectHosp.value = alt.hospital_id;
        }
      } else {
        const alt = evalRes.alternative_hospital;
        evalCard.className = 'eval-result-card not-recommended';
        evalCard.innerHTML = `
          <div class="eval-header text-success">
            <i data-lucide="check-circle" style="width: 15px; height: 15px;"></i>
            <strong>Current Destination Optimal</strong>
          </div>
          <div class="eval-detail">
            <div>Status: ${evalRes.reason || 'Current route remains fastest compatible facility.'}</div>
            ${alt ? `<div style="margin-top: 4px;">Best Alternative Facility: <strong>${alt.hospital_id}</strong> (${alt.hospital_type}) — Beds: <strong>${alt.available_beds}</strong>${alt.eta !== null ? ` | ETA: <strong>${alt.eta.toFixed(1)}m</strong>` : ''}</div>` : ''}
          </div>
        `;
      }
      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      evalCard.style.display = 'block';
      evalCard.className = 'eval-result-card not-recommended';
      evalCard.innerHTML = `<div class="text-danger">Evaluation failed: ${err.message}</div>`;
    } finally {
      btnEval.disabled = false;
    }
  });

  // Bind Execute Reroute
  const btnExec = drawerElement.querySelector('#btn-exec-reroute');
  btnExec.addEventListener('click', async () => {
    const amb = store.state.ambulances.get(String(incident.ambulance_id));
    if (!amb || amb.status !== 'EN_ROUTE' || incident.status === 'ARRIVED') {
      showToast(
        'Reroute Unavailable',
        `Incident #${incident.incident_id} cannot be redirected: ambulance status is ${amb ? amb.status : 'unknown'} (must be EN_ROUTE).`,
        'warning'
      );
      btnExec.disabled = true;
      btnEval.disabled = true;
      if (selectHosp) selectHosp.disabled = true;
      return;
    }

    const selectedHospId = selectHosp && selectHosp.value ? selectHosp.value : null;
    const targetDesc = selectedHospId ? `Hospital ${selectedHospId}` : 'the best available alternative hospital';

    // Open in-app tactical confirmation modal (zero window.confirm)
    const confirmed = await confirmModal({
      title: `Reroute Incident #${incident.incident_id}`,
      message: `Are you sure you want to reroute Ambulance ${ambulance ? ambulance.ambulance_id : ''} to ${targetDesc}? This will update the operational destination and log an operator override decision.`,
      confirmText: 'Execute Reroute',
      cancelText: 'Cancel',
      danger: true,
    });

    if (!confirmed) return;

    btnExec.disabled = true;

    try {
      const reason = selectedHospId
        ? `Dispatcher manual reroute to ${selectedHospId}`
        : 'Dispatcher initiated reroute';
      const decision = await api.applyRedirection(incident.incident_id, selectedHospId, reason);

      // Refresh live telemetry
      const dash = await api.getDashboard();
      store.updateFromDashboard(dash);

      const ambulances = await api.getAmbulances();
      store.setAmbulances(ambulances);

      showToast(
        'Reroute Executed',
        `Incident #${incident.incident_id} redirected to ${decision.new_hospital}. ETA: ${decision.eta_after}m`,
        'warning',
        5000
      );

      // Re-render drawer with updated state
      openIncidentDetail(incident.incident_id);
    } catch (err) {
      showToast('Reroute Error', err.message, 'danger');
      btnExec.disabled = false;
    }
  });

  // Mark drawer as rendered with current incident ID
  drawerElement.dataset.renderedIncidentId = String(incident.incident_id);
}

function updateDrawerTelemetry(incident) {
  if (!drawerElement || !incident) return;

  const ambulance = store.state.ambulances.get(String(incident.ambulance_id));
  const hospital = store.state.hospitals.get(String(incident.hospital_id));

  const isArrived = (ambulance && ambulance.status === 'ARRIVED') || incident.status === 'ARRIVED';
  const isEnRoute = ambulance && ambulance.status === 'EN_ROUTE' && !isArrived;
  const effectiveStatus = isArrived ? 'ARRIVED' : (incident.status || 'DISPATCHED');

  // Header status
  const headerStatus = drawerElement.querySelector('#drawer-header-status-pill');
  if (headerStatus) {
    headerStatus.textContent = effectiveStatus;
    headerStatus.className = `status-pill status-${effectiveStatus.toLowerCase()}`;
  }

  // Lifecycle status
  const lifecycleCell = drawerElement.querySelector('#drawer-cell-lifecycle');
  if (lifecycleCell) {
    lifecycleCell.textContent = effectiveStatus;
  }

  // Ambulance telemetry
  if (ambulance) {
    const ambStatus = drawerElement.querySelector('#drawer-amb-status');
    if (ambStatus) {
      ambStatus.textContent = ambulance.status;
      ambStatus.className = `value status-text-${(ambulance.status || '').toLowerCase()}`;
    }

    const ambEta = drawerElement.querySelector('#drawer-amb-eta');
    if (ambEta) {
      if (isArrived) {
        ambEta.textContent = '0.0 min (Arrived)';
        ambEta.className = 'value font-mono highlight text-success';
      } else {
        const eta = (incident.eta_minutes !== null && incident.eta_minutes !== undefined)
          ? incident.eta_minutes
          : ambulance.eta_minutes;
        ambEta.textContent = (eta !== null && eta !== undefined) ? `${Number(eta).toFixed(1)} min` : '—';
        ambEta.className = 'value font-mono highlight text-accent';
      }
    }

    const ambDist = drawerElement.querySelector('#drawer-amb-distance');
    if (ambDist) {
      ambDist.textContent = isArrived ? '0.0 km' : (ambulance.route_distance_km ? `${ambulance.route_distance_km.toFixed(1)} km` : '—');
    }

    const ambTraffic = drawerElement.querySelector('#drawer-amb-traffic');
    if (ambTraffic) {
      ambTraffic.textContent = `${ambulance.traffic_level || 'NORMAL'} / ${ambulance.road_condition || 'GOOD'}`;
    }
  }

  // Hospital telemetry
  if (hospital) {
    const hospBeds = drawerElement.querySelector('#drawer-hosp-beds');
    if (hospBeds) {
      hospBeds.textContent = `${hospital.available_beds} / ${hospital.capacity}`;
      hospBeds.className = `value font-mono ${hospital.is_full ? 'text-danger' : 'text-success'}`;
    }

    const hospIcu = drawerElement.querySelector('#drawer-hosp-icu');
    if (hospIcu) {
      hospIcu.textContent = `${hospital.available_icu} / ${hospital.icu_capacity}`;
      hospIcu.className = `value font-mono ${hospital.available_icu <= 0 ? 'text-danger' : 'text-success'}`;
    }

    const hospAlert = drawerElement.querySelector('#drawer-hosp-alert');
    if (hospAlert) {
      hospAlert.style.display = hospital.is_full ? 'flex' : 'none';
    }
  }

  // Operator action button state
  const selectHosp = drawerElement.querySelector('#select-reroute-hospital');
  const btnEval = drawerElement.querySelector('#btn-eval-reroute');
  const btnExec = drawerElement.querySelector('#btn-exec-reroute');
  const noticeEl = drawerElement.querySelector('#drawer-reroute-notice');

  if (selectHosp && selectHosp.disabled !== !isEnRoute) {
    selectHosp.disabled = !isEnRoute;
  }
  if (btnEval && btnEval.disabled !== !isEnRoute) {
    btnEval.disabled = !isEnRoute;
  }
  if (btnExec && btnExec.disabled !== !isEnRoute) {
    btnExec.disabled = !isEnRoute;
  }

  if (noticeEl) {
    if (!isEnRoute) {
      noticeEl.style.display = 'block';
      noticeEl.innerHTML = isArrived
        ? `
          <div class="alert-banner info" style="font-size: 11px; padding: 6px 10px; display: flex; align-items: center; gap: 6px; background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); border-radius: 4px; color: #86efac;">
            <i data-lucide="check-circle-2" style="width: 14px; height: 14px; color: #22c55e; flex-shrink: 0;"></i>
            <span>Ambulance has arrived at destination hospital. Redirection is locked.</span>
          </div>
        `
        : `
          <div class="alert-banner warning" style="font-size: 11px; padding: 6px 10px; display: flex; align-items: center; gap: 6px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 4px; color: #fcd34d;">
            <i data-lucide="alert-triangle" style="width: 14px; height: 14px; color: #f59e0b; flex-shrink: 0;"></i>
            <span>Ambulance is not en route. Redirection unavailable.</span>
          </div>
        `;
      if (window.lucide) window.lucide.createIcons();
    } else {
      noticeEl.style.display = 'none';
    }
  }
}
