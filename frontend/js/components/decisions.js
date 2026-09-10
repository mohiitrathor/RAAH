/**
 * Operational Decisions & Audit Stream Component (M13.3 Phase 3)
 * ===============================================================
 * Displays authoritative, recent decision evidence records backed by:
 * GET /decision-evidence/recent
 *
 * INVARIANTS:
 * 1. Bounded to maximum 20 visible records.
 * 2. Deduplicated locally by evidence_id.
 * 3. Observational only (zero state mutation).
 * 4. Graceful handling of empty store and API errors.
 * 5. Debounced refreshes triggered by operational SSE events.
 */

import * as api from '../api.js';
import { store } from '../state.js';
import { openExplanationModal } from './explanation_modal.js';

let debounceTimer = null;
let isFetching = false;
let cachedRecords = [];

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export function setupDecisions() {
  const tbody = document.getElementById('decisions-tbody');
  const countBadge = document.getElementById('redirection-count');

  async function refreshRecentDecisions() {
    if (isFetching) return;
    isFetching = true;

    try {
      const res = await api.getRecentDecisionEvidence(20);
      const records = (res && Array.isArray(res.records)) ? res.records : [];

      // Deduplicate locally by evidence_id while preserving reverse chronological order
      const dedupMap = new Map();
      for (const rec of records) {
        if (rec && rec.evidence && rec.evidence.evidence_id) {
          if (!dedupMap.has(rec.evidence.evidence_id)) {
            dedupMap.set(rec.evidence.evidence_id, rec);
          }
        }
      }

      // Bound to maximum 20 records
      cachedRecords = Array.from(dedupMap.values()).slice(0, 20);
      renderFeed();
    } catch (err) {
      console.warn('[DecisionsFeed] Failed to fetch recent decision evidence:', err.message);
      if (cachedRecords.length === 0 && tbody) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" class="empty-placeholder" style="padding: 10px; color: var(--text-muted);">
              Decision evidence service currently unavailable.
            </td>
          </tr>
        `;
        if (countBadge) countBadge.textContent = '0 Decisions';
      }
    } finally {
      isFetching = false;
    }
  }

  function renderFeed() {
    if (!tbody) return;

    if (countBadge) {
      countBadge.textContent = `${cachedRecords.length} Decision${cachedRecords.length === 1 ? '' : 's'}`;
    }

    if (!cachedRecords || cachedRecords.length === 0) {
      // Check if legacy decisions exist in store as fallback
      const legacy = store.state.decisions || [];
      if (legacy.length > 0) {
        renderLegacyFeed(legacy);
        return;
      }

      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="empty-placeholder" style="padding: 10px;">
            No operational decisions recorded yet.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = cachedRecords.map(rec => {
      const ev = rec.evidence;
      const decType = ev.decision_type || 'DECISION';
      const typeClass = `pill-${decType.toLowerCase().replace(/_/g, '-')}`;
      const incidentText = (ev.incident_id !== null && ev.incident_id !== undefined)
        ? `<strong style="color: #fff;">#${ev.incident_id}</strong>`
        : '<span style="color: var(--text-muted);">System</span>';
      const actionText = ev.action ? escapeHtml(ev.action) : 'DECISION';
      const explanationText = rec.explanation ? escapeHtml(rec.explanation) : actionText;

      return `
        <tr class="decision-feed-row" data-evidence-id="${escapeHtml(ev.evidence_id)}">
          <td style="color: var(--text-cyan); font-family: var(--font-mono);">T+${ev.sim_time}m</td>
          <td><span class="decision-pill ${typeClass}">${escapeHtml(decType)}</span></td>
          <td>${incidentText}</td>
          <td style="color: var(--text-primary); font-family: var(--font-mono); font-size: 11px;">${actionText}</td>
          <td style="color: var(--text-secondary); max-width: 260px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${explanationText}">
            ${explanationText}
          </td>
          <td style="text-align: right;">
            <button type="button" class="btn-tactical btn-inspect-sm btn-feed-inspect" data-evidence-id="${escapeHtml(ev.evidence_id)}" aria-label="Inspect decision ${escapeHtml(ev.evidence_id)}">
              Inspect
            </button>
          </td>
        </tr>
      `;
    }).join('');

    // Bind Inspect buttons
    tbody.querySelectorAll('.btn-feed-inspect').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const eid = btn.getAttribute('data-evidence-id');
        const found = cachedRecords.find(r => r.evidence && r.evidence.evidence_id === eid);
        openExplanationModal({ evidenceId: eid, explanationData: found });
      });
    });
  }

  function renderLegacyFeed(legacy) {
    if (countBadge) {
      countBadge.textContent = `${legacy.length} Redirection${legacy.length === 1 ? '' : 's'}`;
    }
    const reversed = legacy.slice().reverse().slice(0, 20);
    tbody.innerHTML = reversed.map(d => {
      const etaSaved = (d.eta_saved !== null && d.eta_saved !== undefined)
        ? `${Number(d.eta_saved).toFixed(1)}m`
        : '—';
      return `
        <tr>
          <td style="color: var(--text-cyan);">T+${d.time}m</td>
          <td><span class="decision-pill pill-hospital-redirection">REDIRECTION</span></td>
          <td><strong style="color: #fff;">#${d.incident_id}</strong></td>
          <td style="color: #34d399; font-weight: 600;">${d.new_hospital || '—'}</td>
          <td style="color: var(--text-secondary); max-width: 250px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${d.reason}">
            ${escapeHtml(d.reason || '')} (ETA Saved: ${etaSaved})
          </td>
          <td style="text-align: right; color: var(--text-muted); font-size: 10px;">Legacy</td>
        </tr>
      `;
    }).join('');
  }

  function debouncedRefresh() {
    if (debounceTimer) return;
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      refreshRecentDecisions();
    }, 250);
  }

  // Subscribe to store updates to trigger debounced refreshes when new events arrive
  store.subscribe((state, changedKeys) => {
    if (changedKeys.includes('activityFeed') || changedKeys.includes('decisions') || changedKeys.includes('activeIncidents')) {
      debouncedRefresh();
    }
  });

  // Initial fetch on Command Center boot
  refreshRecentDecisions();
}
