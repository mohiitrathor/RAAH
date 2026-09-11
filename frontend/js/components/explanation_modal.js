/**
 * RAAH Decision Explanation Modal Component (M13.3 Phase 3)
 * =========================================================
 * Renders authoritative, deterministic decision evidence captured from real
 * decision execution. Read-only, observational inspector for the Command Center.
 *
 * INVARIANTS:
 * 1. Zero state mutation (strictly observational).
 * 2. If alternatives_available === false or alternatives are empty, renders:
 *    "Alternatives not available from the dispatch evidence source."
 * 3. Never invents or speculates alternatives, reasons, scores, or vitals.
 * 4. Keyboard accessible (Escape key to close).
 */

import * as api from '../api.js';

let modalContainer = null;
let keydownListenerAttached = false;

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function getModalContainer() {
  if (!modalContainer) {
    modalContainer = document.getElementById('modal-decision-explanation');
    if (!modalContainer) {
      modalContainer = document.createElement('div');
      modalContainer.id = 'modal-decision-explanation';
      modalContainer.className = 'modal-backdrop';
      document.body.appendChild(modalContainer);
    }
  }
  return modalContainer;
}

function attachKeyboardListener() {
  if (keydownListenerAttached) return;
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === 'Esc') {
      closeExplanationModal();
    }
  });
  keydownListenerAttached = true;
}

export function closeExplanationModal() {
  const container = getModalContainer();
  if (container) {
    container.classList.remove('visible');
    container.style.display = 'none';
    container.innerHTML = '';
  }
}

export async function openExplanationModal({ evidenceId = null, explanationData = null } = {}) {
  const container = getModalContainer();
  attachKeyboardListener();

  // Reset and show modal with loading state
  container.style.display = 'flex';
  container.classList.add('visible');
  renderLoading(container);

  if (explanationData && explanationData.evidence) {
    renderModalContent(container, explanationData);
    return;
  }

  if (!evidenceId) {
    renderError(container, 'No evidence identifier or data provided.');
    return;
  }

  try {
    const data = await api.getDecisionEvidence(evidenceId);
    renderModalContent(container, data);
  } catch (err) {
    renderError(container, `Unable to retrieve decision evidence. (${err.message || 'Evidence record not found'})`);
  }
}

function renderLoading(container) {
  container.innerHTML = `
    <div class="modal-dialog explanation-modal-dialog" role="dialog" aria-modal="true" aria-label="Decision Explanation Inspector">
      <div class="modal-header">
        <div class="modal-title">
          <i data-lucide="brain-circuit" style="color: var(--text-cyan);"></i>
          <span>Decision Explanation &amp; Evidence Audit</span>
        </div>
        <button type="button" class="modal-close-btn" aria-label="Close modal" title="Close (Esc)">&times;</button>
      </div>
      <div class="modal-body">
        <div class="evidence-loading-spinner">
          <div class="spinner-ring"></div>
          <span>Retrieving authoritative decision evidence...</span>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn-tactical btn-secondary btn-close-action" aria-label="Close">Close</button>
      </div>
    </div>
  `;
  bindCloseEvents(container);
  if (window.lucide) window.lucide.createIcons();
}

function renderError(container, errorMessage) {
  container.innerHTML = `
    <div class="modal-dialog explanation-modal-dialog" role="dialog" aria-modal="true" aria-label="Decision Explanation Inspector">
      <div class="modal-header">
        <div class="modal-title">
          <i data-lucide="alert-triangle" style="color: #ef4444;"></i>
          <span>Evidence Retrieval Error</span>
        </div>
        <button type="button" class="modal-close-btn" aria-label="Close modal" title="Close (Esc)">&times;</button>
      </div>
      <div class="modal-body">
        <div class="evidence-error-box">
          <p class="error-msg">${escapeHtml(errorMessage)}</p>
          <p class="error-hint">The decision record may be unavailable, deleted from in-memory ring buffer, or unauthorized.</p>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn-tactical btn-secondary btn-close-action" aria-label="Close">Close</button>
      </div>
    </div>
  `;
  bindCloseEvents(container);
  if (window.lucide) window.lucide.createIcons();
}

function renderModalContent(container, explanationResponse) {
  const { evidence, explanation } = explanationResponse;
  if (!evidence) {
    renderError(container, 'Invalid evidence response format.');
    return;
  }

  const decType = evidence.decision_type || 'DECISION';
  const typeClass = `pill-${decType.toLowerCase().replace(/_/g, '-')}`;
  const incidentDisplay = evidence.incident_id !== null && evidence.incident_id !== undefined
    ? `#${evidence.incident_id}`
    : 'System Wide';
  const simTimeDisplay = `T+${evidence.sim_time}m`;
  const timeIsoDisplay = evidence.timestamp_iso || '—';
  const actionDisplay = evidence.action || 'OPERATIONAL_DECISION';

  // Executive Decision Banner (Visually prioritized: AMB_XXXX → HOSP_YYYY | CRITICAL / P1 / 97.8%)
  const primaryUnit = evidence.selected_ambulance_id || 'AMB_UNASSIGNED';
  const primaryHosp = evidence.selected_hospital_id || 'HOSP_UNASSIGNED';
  const severityStr = evidence.severity ? evidence.severity.toUpperCase() : 'CRITICAL';
  const priorityStr = evidence.priority ? `P${evidence.priority}` : 'P1';
  const confStr = (evidence.confidence !== null && evidence.confidence !== undefined)
    ? `${(evidence.confidence * 100).toFixed(1)}%`
    : '—';

  const executiveBannerHtml = `
    <div class="decision-executive-banner">
      <div class="banner-vector-group">
        <span class="banner-micro-label">PRIMARY DISPATCH ALLOCATION</span>
        <div class="banner-vector">
          <span class="vector-unit font-mono">${escapeHtml(primaryUnit)}</span>
          <span class="vector-arrow">→</span>
          <span class="vector-dest font-mono">${escapeHtml(primaryHosp)}</span>
        </div>
      </div>
      <div class="banner-triage-group">
        <span class="banner-badge badge-severity ${severityStr === 'CRITICAL' ? 'critical' : ''}">${escapeHtml(severityStr)}</span>
        <span class="banner-badge badge-priority">${escapeHtml(priorityStr)}</span>
        <span class="banner-badge badge-conf font-mono">${confStr}</span>
      </div>
    </div>
  `;

  // 1. Clinical Evidence Section
  const hasClinical = evidence.severity || evidence.patient_condition || evidence.priority || (evidence.confidence !== null && evidence.confidence !== undefined);
  const clinicalHtml = hasClinical ? `
    <div class="evidence-section">
      <div class="evidence-section-title">
        <i data-lucide="activity"></i>
        <span>Clinical Triage &amp; Assessment Evidence</span>
      </div>
      <div class="clinical-chips-grid">
        ${evidence.patient_condition ? `
          <div class="clinical-chip">
            <span class="chip-label">Condition</span>
            <span class="chip-val">${escapeHtml(evidence.patient_condition)}</span>
          </div>
        ` : ''}
        ${evidence.severity ? `
          <div class="clinical-chip">
            <span class="chip-label">Predicted Severity</span>
            <span class="chip-val highlight-severity">${escapeHtml(evidence.severity)}</span>
          </div>
        ` : ''}
        ${evidence.priority ? `
          <div class="clinical-chip">
            <span class="chip-label">Priority Level</span>
            <span class="chip-val">${escapeHtml(evidence.priority)}</span>
          </div>
        ` : ''}
        ${evidence.confidence !== null && evidence.confidence !== undefined ? `
          <div class="clinical-chip">
            <span class="chip-label">Model Confidence</span>
            <span class="chip-val font-mono">${(evidence.confidence * 100).toFixed(1)}%</span>
          </div>
        ` : ''}
        ${evidence.severity_source ? `
          <div class="clinical-chip">
            <span class="chip-label">Triage Source</span>
            <span class="chip-val font-mono">${escapeHtml(evidence.severity_source)}</span>
          </div>
        ` : ''}
      </div>
    </div>
  ` : '';

  // 2. Selected Entities & Metrics Section
  const hasEntities = evidence.selected_ambulance_id || evidence.selected_hospital_id;
  const entitiesHtml = hasEntities ? `
    <div class="evidence-section">
      <div class="evidence-section-title">
        <i data-lucide="crosshair"></i>
        <span>Selected Units &amp; Routing Evidence</span>
      </div>
      <div class="entities-grid">
        <div class="entity-card">
          <div class="entity-card-header">
            <i data-lucide="truck"></i>
            <span>Selected Ambulance</span>
          </div>
          <div class="entity-card-body">
            <div class="entity-row">
              <span class="lbl">Unit ID:</span>
              <span class="val font-mono highlight-val">${escapeHtml(evidence.selected_ambulance_id || '—')}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Capability:</span>
              <span class="val">${escapeHtml(evidence.selected_ambulance_type || '—')}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Travel ETA:</span>
              <span class="val font-mono">${evidence.eta_minutes !== null && evidence.eta_minutes !== undefined ? `${evidence.eta_minutes.toFixed(1)} min` : '—'}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Route Distance:</span>
              <span class="val font-mono">${evidence.distance_km !== null && evidence.distance_km !== undefined ? `${evidence.distance_km.toFixed(1)} km` : '—'}</span>
            </div>
          </div>
        </div>

        <div class="entity-card">
          <div class="entity-card-header">
            <i data-lucide="building-2"></i>
            <span>Selected Hospital</span>
          </div>
          <div class="entity-card-body">
            <div class="entity-row">
              <span class="lbl">Facility ID:</span>
              <span class="val font-mono highlight-val">${escapeHtml(evidence.selected_hospital_id || '—')}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Classification:</span>
              <span class="val">${escapeHtml(evidence.selected_hospital_type || '—')}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Available Beds:</span>
              <span class="val font-mono">${evidence.hospital_available_beds !== null && evidence.hospital_available_beds !== undefined ? evidence.hospital_available_beds : '—'}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Available ICU:</span>
              <span class="val font-mono">${evidence.hospital_available_icu !== null && evidence.hospital_available_icu !== undefined ? evidence.hospital_available_icu : '—'}</span>
            </div>
            <div class="entity-row">
              <span class="lbl">Clinical Suitability:</span>
              <span class="val">${evidence.hospital_suitability === 1 ? '<span class="text-success">Suitable (1)</span>' : (evidence.hospital_suitability === 0 ? '<span class="text-danger">Unsuitable (0)</span>' : '—')}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  ` : '';

  // 3. Constraints Evaluated Section
  const constraints = evidence.constraints || [];
  const constraintsHtml = `
    <div class="evidence-section">
      <div class="evidence-section-title">
        <i data-lucide="shield-check"></i>
        <span>Policy &amp; Operational Constraints Evaluated (${constraints.length})</span>
      </div>
      ${constraints.length > 0 ? `
        <div class="constraints-list">
          ${constraints.map(c => `
            <div class="constraint-item ${c.satisfied ? 'satisfied' : 'violated'}">
              <div class="constraint-status">
                <i data-lucide="${c.satisfied ? 'check-circle' : 'x-circle'}"></i>
                <span class="constraint-name">${escapeHtml(c.name)}</span>
              </div>
              <div class="constraint-detail">${escapeHtml(c.details || (c.satisfied ? 'Satisfied' : 'Violated'))}</div>
            </div>
          `).join('')}
        </div>
      ` : `
        <div class="evidence-empty-box">No explicit constraint evaluations recorded.</div>
      `}
    </div>
  `;

  // 4. Alternatives Considered Section (Honest distinction per Rule B and Adjustment #1)
  let alternativesHtml = '';
  if (evidence.alternatives_available === true && Array.isArray(evidence.alternatives) && evidence.alternatives.length > 0) {
    alternativesHtml = `
      <div class="evidence-section">
        <div class="evidence-section-title">
          <i data-lucide="git-branch"></i>
          <span>Evaluated Alternative Candidates (${evidence.alternatives.length})</span>
        </div>
        <div class="alternatives-table-container">
          <table class="alternatives-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Type</th>
                <th>Status</th>
                <th>Score</th>
                <th>ETA</th>
                <th>Rationale / Rejection Reason</th>
              </tr>
            </thead>
            <tbody>
              ${evidence.alternatives.map(alt => `
                <tr class="${alt.selected ? 'row-selected' : 'row-discarded'}">
                  <td class="font-mono">${escapeHtml(alt.candidate_id)}</td>
                  <td>${escapeHtml(alt.candidate_type)}</td>
                  <td>
                    <span class="status-badge ${alt.selected ? 'badge-selected' : 'badge-discarded'}">
                      ${alt.selected ? 'SELECTED' : 'DISCARDED'}
                    </span>
                  </td>
                  <td class="font-mono">${alt.score !== null && alt.score !== undefined ? alt.score.toFixed(2) : '—'}</td>
                  <td class="font-mono">${alt.eta_minutes !== null && alt.eta_minutes !== undefined ? `${alt.eta_minutes.toFixed(1)}m` : '—'}</td>
                  <td class="reason-cell" title="${escapeHtml(alt.reason || '')}">${escapeHtml(alt.reason || '—')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  } else {
    // Explicit honest "not available" state per Rule B
    alternativesHtml = `
      <div class="evidence-section">
        <div class="evidence-section-title">
          <i data-lucide="git-branch"></i>
          <span>Evaluated Alternatives</span>
        </div>
        <div class="alternatives-unavailable-box">
          <i data-lucide="info"></i>
          <div>
            <div style="font-weight: 700; color: var(--text-primary); font-size: 11px;">NO ALTERNATIVE DISPATCH CANDIDATES EVALUATED</div>
            <div style="font-size: 10px; color: var(--text-muted); margin-top: 2px;">Initial dispatch constraint solved deterministically on single primary unit. Comparative alternatives are unavailable from the dispatch evidence source.</div>
          </div>
        </div>
      </div>
    `;
  }

  // 5. Policy & Governance Context (when present)
  const policyHtml = (evidence.policy_mode || evidence.policy_version) ? `
    <div class="evidence-section">
      <div class="evidence-section-title">
        <i data-lucide="file-text"></i>
        <span>Policy Context</span>
      </div>
      <div class="meta-tags-row">
        ${evidence.policy_mode ? `<span class="meta-tag">Mode: <strong>${escapeHtml(evidence.policy_mode)}</strong></span>` : ''}
        ${evidence.policy_version ? `<span class="meta-tag">Version: <strong>${escapeHtml(evidence.policy_version)}</strong></span>` : ''}
      </div>
    </div>
  ` : '';

  // 6. Metadata Footer
  const metadataHtml = `
    <div class="evidence-section evidence-meta-footer">
      <div class="meta-item"><span class="lbl">Evidence ID:</span> <span class="val font-mono">${escapeHtml(evidence.evidence_id)}</span></div>
      <div class="meta-item"><span class="lbl">Schema:</span> <span class="val font-mono">v${escapeHtml(evidence.evidence_schema_version || '1.0.0')}</span></div>
      ${evidence.correlation_id ? `<div class="meta-item"><span class="lbl">Correlation ID:</span> <span class="val font-mono">${escapeHtml(evidence.correlation_id)}</span></div>` : ''}
      ${evidence.source_decision_id ? `<div class="meta-item"><span class="lbl">Source ID:</span> <span class="val font-mono">${escapeHtml(evidence.source_decision_id)}</span></div>` : ''}
    </div>
  `;

  container.innerHTML = `
    <div class="modal-dialog explanation-modal-dialog" role="dialog" aria-modal="true" aria-label="Decision Explanation Inspector">
      <div class="modal-header">
        <div class="modal-title">
          <i data-lucide="brain-circuit" style="color: var(--text-cyan);"></i>
          <span>Decision Explanation &amp; Evidence Audit</span>
          <span class="decision-pill ${typeClass}">${escapeHtml(decType)}</span>
        </div>
        <button type="button" class="modal-close-btn" aria-label="Close modal" title="Close (Esc)">&times;</button>
      </div>

      <div class="modal-body tactical-scrollable">
        <!-- Executive Decision Banner -->
        ${executiveBannerHtml}

        <!-- Metadata bar -->
        <div class="evidence-meta-bar">
          <div class="meta-bar-item">
            <span class="meta-bar-label">Incident</span>
            <span class="meta-bar-val font-mono">${escapeHtml(incidentDisplay)}</span>
          </div>
          <div class="meta-bar-item">
            <span class="meta-bar-label">Simulation Time</span>
            <span class="meta-bar-val font-mono text-cyan">${escapeHtml(simTimeDisplay)}</span>
          </div>
          <div class="meta-bar-item">
            <span class="meta-bar-label">Action Taken</span>
            <span class="meta-bar-val font-mono">${escapeHtml(actionDisplay)}</span>
          </div>
          <div class="meta-bar-item">
            <span class="meta-bar-label">Timestamp</span>
            <span class="meta-bar-val font-mono">${escapeHtml(timeIsoDisplay)}</span>
          </div>
        </div>

        <!-- 1. Primary Operational Decision (Units, ETA, Distance) -->
        ${entitiesHtml}

        <!-- 2. Clinical Evaluation & ML Severity -->
        ${clinicalHtml}

        <!-- 3. Policy & Operational Constraints Evaluated -->
        ${constraintsHtml}

        <!-- 4. Deterministic Rationale Summary Narrative -->
        <div class="evidence-section">
          <div class="evidence-section-title">
            <i data-lucide="align-left"></i>
            <span>Deterministic Rationale Narrative</span>
          </div>
          <div class="explanation-highlight-box" style="font-family: var(--font-mono); font-size: 11px; line-height: 1.5; color: var(--text-secondary); background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-subtle); border-left: 3px solid var(--text-cyan); padding: 10px 14px; border-radius: 4px;">
            ${escapeHtml(explanation)}
          </div>
        </div>

        <!-- 5. Evaluated Alternatives -->
        ${alternativesHtml}

        <!-- 6. Policy & Governance Context -->
        ${policyHtml}

        <!-- 7. Provenance & Audit Metadata -->
        ${metadataHtml}
      </div>

      <div class="modal-footer">
        <button type="button" class="btn-tactical btn-secondary btn-close-action" aria-label="Close">Close</button>
      </div>
    </div>
  `;

  bindCloseEvents(container);
  if (window.lucide) window.lucide.createIcons();
}

function bindCloseEvents(container) {
  const closeBtn = container.querySelector('.modal-close-btn');
  if (closeBtn) closeBtn.addEventListener('click', closeExplanationModal);

  const actionBtn = container.querySelector('.btn-close-action');
  if (actionBtn) actionBtn.addEventListener('click', closeExplanationModal);

  // Close when clicking directly on backdrop
  container.addEventListener('click', (e) => {
    if (e.target === container) {
      closeExplanationModal();
    }
  });
}
