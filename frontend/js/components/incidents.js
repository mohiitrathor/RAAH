/**
 * Incident Triage & Quick-Dispatch Component
 */

import { store } from '../state.js';
import * as api from '../api.js';
import { tacticalMap } from '../map.js';
import { showToast } from './toasts.js';
import { openEmergencyIntakeModal } from './intake_modal.js';
import { openIncidentDetail } from './detail_drawer.js';

export function setupIncidents() {
  const form = document.getElementById('form-quick-dispatch');
  const inputId = document.getElementById('input-incident-id');
  const container = document.getElementById('incidents-container');
  const countBadge = document.getElementById('active-incident-count');
  const btnOpenIntake = document.getElementById('btn-open-intake');

  // --- Live Emergency Call Intake Button ---
  if (btnOpenIntake) {
    btnOpenIntake.addEventListener('click', () => {
      openEmergencyIntakeModal();
    });
  }

  // --- Quick Dispatch Form Submit ---
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const incidentId = parseInt(inputId.value, 10);
    if (!incidentId || isNaN(incidentId)) return;

    try {
      const result = await api.dispatchIncident(incidentId);
      inputId.value = '';

      // Immediately refresh dashboard & state
      const dash = await api.getDashboard();
      store.updateFromDashboard(dash);

      // If active ambulance returned, refresh ambulances layer
      if (result.ambulance) {
        const ambulances = await api.getAmbulances();
        store.setAmbulances(ambulances);
      }

      showToast(
        'Incident Dispatched',
        `Incident #${result.incident_id}: ${result.ambulance ? result.ambulance.ambulance_id : 'Awaiting unit'} -> ${result.hospital ? result.hospital.hospital_id : 'Unassigned'}`,
        'success'
      );

      // Open detail drawer and focus map
      store.selectIncident(result.incident_id);
      tacticalMap.focusIncident(result.incident_id);
      openIncidentDetail(result.incident_id);
    } catch (err) {
      showToast('Dispatch Error', err.message, 'danger');
    }
  });

  // --- Reactive Render of Incident Cards ---
  store.subscribe((state, changedKeys) => {
    if (!changedKeys.includes('activeIncidents') && !changedKeys.includes('selectedIncidentId')) {
      return;
    }

    const incidents = state.activeIncidents;
    countBadge.textContent = `${incidents.length} Active`;

    if (!incidents || incidents.length === 0) {
      container.innerHTML = `
        <div class="empty-placeholder">
          <i data-lucide="inbox"></i>
          <span>No active incidents. Enter an Incident ID above to triage & dispatch.</span>
        </div>
      `;
      if (window.lucide) window.lucide.createIcons();
      return;
    }

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

    container.innerHTML = incidents.map(inc => {
      const pClass = `p${inc.priority}`;
      const isSelected = state.selectedIncidentId === inc.incident_id;
      const etaText = (inc.eta_minutes !== null && inc.eta_minutes !== undefined) ? `${Number(inc.eta_minutes).toFixed(1)} MIN` : '—';
      const demoStr = (inc.age && inc.gender)
        ? `${inc.condition || 'EMERGENCY'} — ${inc.age}${String(inc.gender)[0].toUpperCase()}`
        : (inc.condition || 'GENERAL EMERGENCY');
      const statusText = inc.status ? inc.status : 'DISPATCHED';

      return `
        <div class="incident-card ${pClass} ${isSelected ? 'selected' : ''}" data-id="${inc.incident_id}">
          <div class="incident-card-top">
            <div class="incident-identity">
              <span class="severity-marker ${pClass}">P${inc.priority}</span>
              <span class="incident-num font-mono">#${inc.incident_id}</span>
              ${inc.priority === 1 ? '<span class="status-pulse-dot" title="Active P1 Critical Incident"></span>' : ''}
            </div>
            <span class="incident-status-tag status-${statusText.toLowerCase()}">${escapeHtml(statusText)}</span>
          </div>
          <div class="incident-headline">${escapeHtml(demoStr.toUpperCase())}</div>
          <div class="incident-route-row">
            <span class="route-unit font-mono">${escapeHtml(inc.ambulance_id || 'AWAITING UNIT')}</span>
            <span class="route-arrow">→</span>
            <span class="route-dest font-mono">${escapeHtml(inc.hospital_id || 'UNASSIGNED')}</span>
          </div>
          <div class="incident-card-bottom">
            <span class="eta-label">EST. ARRIVAL</span>
            <span class="eta-figure font-mono ${inc.priority === 1 ? 'urgent' : ''}">ETA ${etaText}</span>
          </div>
        </div>
      `;
    }).join('');

    // Attach click listeners to cards to focus on map & open detail drawer
    container.querySelectorAll('.incident-card').forEach(card => {
      card.addEventListener('click', () => {
        const id = parseInt(card.getAttribute('data-id'), 10);
        store.selectIncident(id);
        tacticalMap.focusIncident(id);
        openIncidentDetail(id);
      });
    });

    if (window.lucide) window.lucide.createIcons();
  });
}
