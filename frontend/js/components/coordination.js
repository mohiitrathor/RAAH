/**
 * RAAH Fleet Coordination & MCI Component (M9)
 * Handles zone coverage visualization, operator repositioning controls,
 * and Multi-Casualty Incident (MCI) triage & evacuation orchestration.
 */

import {
  getCoverage,
  getRepositionRecommendations,
  executeReposition,
  cancelReposition,
  getActiveMCIs,
  declareMCI,
} from '../api.js';
import { showToast } from './toasts.js';
import { tacticalMap } from '../map.js';

class CoordinationComponent {
  constructor() {
    this.coverageContainer = null;
    this.repositionContainer = null;
    this.mciContainer = null;
    this.mciModal = null;
    this.pollInterval = null;
  }

  init() {
    this.coverageContainer = document.getElementById('coordination-coverage-container');
    this.repositionContainer = document.getElementById('coordination-reposition-container');
    this.mciContainer = document.getElementById('mci-container');
    this.mciModal = document.getElementById('mci-modal');

    const refreshBtn = document.getElementById('btn-refresh-coverage');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => this.refresh());
    }

    this.initMCIControls();

    // Initial load and degraded background safety fallback (60 seconds)
    // Primary real-time synchronization is delivered via SSE MCI_ALERT events
    this.refresh();
    this.pollInterval = setInterval(() => this.refresh(), 60000);
  }

  initMCIControls() {
    const btnOpen = document.getElementById('btn-open-mci-modal');
    const btnClose = document.getElementById('btn-close-mci-modal');
    const btnCancel = document.getElementById('btn-cancel-mci-modal');
    const formDeclare = document.getElementById('form-declare-mci');

    if (btnOpen && this.mciModal) {
      btnOpen.addEventListener('click', () => {
        this.mciModal.style.display = 'flex';
      });
    }

    const closeModal = () => {
      if (this.mciModal) {
        this.mciModal.style.display = 'none';
      }
    };

    if (btnClose) btnClose.addEventListener('click', closeModal);
    if (btnCancel) btnCancel.addEventListener('click', closeModal);

    if (formDeclare) {
      formDeclare.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('mci-name').value;
        const lat = parseFloat(document.getElementById('mci-lat').value);
        const lon = parseFloat(document.getElementById('mci-lon').value);
        const casualties = parseInt(document.getElementById('mci-casualties').value, 10);
        const condition = document.getElementById('mci-condition').value;
        const notes = document.getElementById('mci-notes').value;

        const submitBtn = formDeclare.querySelector('button[type="submit"]');
        try {
          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Triaging & Dispatching...';
          }

          const res = await declareMCI({
            name,
            latitude: lat,
            longitude: lon,
            estimated_casualties: casualties,
            primary_condition: condition,
            notes,
          });

          showToast(`MCI Declared: ${res.dispatched_count} dispatched, ${res.waiting_count} waiting`, 'success');
          closeModal();
          await this.refresh();
        } catch (err) {
          showToast(`MCI Declaration failed: ${err.message}`, 'error');
        } finally {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Declare & Dispatch';
          }
        }
      });
    }
  }

  async refresh() {
    try {
      const [coverageData, recommendations, activeMCIs] = await Promise.all([
        getCoverage().catch(() => null),
        getRepositionRecommendations().catch(() => []),
        getActiveMCIs().catch(() => []),
      ]);

      if (coverageData && this.coverageContainer) {
        this.renderCoverage(coverageData);
      }

      if (this.repositionContainer) {
        this.renderRecommendations(recommendations || []);
      }

      if (this.mciContainer) {
        this.renderMCIs(activeMCIs || []);
      }

      if (tacticalMap && tacticalMap.renderMCIs) {
        tacticalMap.renderMCIs(activeMCIs || []);
      }
    } catch (err) {
      console.warn('Failed to refresh coordination data:', err);
    }
  }

  renderCoverage(coverageData) {
    if (!this.coverageContainer || !coverageData.zones) return;

    const zones = Object.values(coverageData.zones);
    let html = `
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-bottom: 6px; font-size: 11px;">
    `;

    for (const z of zones) {
      const statusColor = z.status === 'DEFICIT' ? '#ef4444' : (z.status === 'SURPLUS' ? '#06b6d4' : '#10b981');
      const shortName = z.zone_name.split('/')[0].trim();
      html += `
        <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-left: 3px solid ${statusColor}; padding: 4px 6px; border-radius: 4px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: 600; color: #e2e8f0; font-size: 10px;">${shortName}</span>
            <span style="font-family: var(--font-mono); color: ${statusColor}; font-size: 10px;">${z.status}</span>
          </div>
          <div style="display: flex; justify-content: space-between; font-size: 10px; color: #94a3b8; margin-top: 2px;">
            <span>Avail: <strong style="color: #f8fafc;">${z.available_count}</strong>/${z.target_capacity}</span>
            <span>Score: <strong style="color: #f8fafc;">${z.coverage_score}</strong></span>
          </div>
        </div>
      `;
    }

    html += `</div>`;
    this.coverageContainer.innerHTML = html;
  }

  renderRecommendations(recommendations) {
    if (!this.repositionContainer) return;

    if (!recommendations || recommendations.length === 0) {
      this.repositionContainer.innerHTML = `
        <div style="font-size: 11px; color: #64748b; padding: 4px; text-align: center; border: 1px dashed rgba(255, 255, 255, 0.06); border-radius: 4px;">
          ✓ Fleet distribution balanced (0 reposition advisories)
        </div>
      `;
      return;
    }

    let html = `
      <div style="font-size: 11px; font-weight: 700; color: #38bdf8; margin: 4px 0 4px 0; display: flex; align-items: center; gap: 4px;">
        <span>Reposition Advisories (${recommendations.length}):</span>
      </div>
    `;

    for (const rec of recommendations) {
      html += `
        <div style="background: rgba(6, 182, 212, 0.08); border: 1px solid rgba(6, 182, 212, 0.3); border-radius: 4px; padding: 6px; margin-bottom: 4px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="color: #06b6d4;">${rec.ambulance_id}</strong>
            <span style="font-size: 10px; color: #cbd5e1;">${rec.origin_zone.replace('JAIPUR_', '')} -> ${rec.target_zone.replace('JAIPUR_', '')}</span>
          </div>
          <div style="color: #94a3b8; font-size: 10px; margin: 2px 0;">${rec.reason}</div>
          <div style="display: flex; justify-content: flex-end; margin-top: 4px;">
            <button class="btn-tactical btn-xs btn-deploy-reposition" 
                    data-amb-id="${rec.ambulance_id}"
                    data-target-lat="${rec.target_staging_post[0]}"
                    data-target-lon="${rec.target_staging_post[1]}"
                    data-reason="${rec.reason}"
                    style="background: #0891b2; color: #fff; border: none; padding: 2px 8px; font-size: 10px; cursor: pointer; border-radius: 3px;">
              Deploy Reposition
            </button>
          </div>
        </div>
      `;
    }

    this.repositionContainer.innerHTML = html;

    // Attach button handlers
    const buttons = this.repositionContainer.querySelectorAll('.btn-deploy-reposition');
    buttons.forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        const ambId = btn.dataset.ambId;
        const targetLat = parseFloat(btn.dataset.targetLat);
        const targetLon = parseFloat(btn.dataset.targetLon);
        const reason = btn.dataset.reason;

        try {
          btn.disabled = true;
          btn.textContent = 'Deploying...';
          await executeReposition(ambId, targetLat, targetLon, reason);
          showToast(`Reposition started: ${ambId}`, 'success');
          this.refresh();
        } catch (err) {
          showToast(`Reposition failed: ${err.message}`, 'error');
          btn.disabled = false;
          btn.textContent = 'Deploy Reposition';
        }
      });
    });
  }

  renderMCIs(activeMCIs) {
    if (!this.mciContainer) return;

    if (!activeMCIs || activeMCIs.length === 0) {
      this.mciContainer.innerHTML = `
        <div style="font-size: 11px; color: #64748b; padding: 4px; text-align: center; border: 1px dashed rgba(239, 68, 68, 0.2); border-radius: 4px;">
          No active Multi-Casualty Incidents
        </div>
      `;
      return;
    }

    let html = '';
    for (const mci of activeMCIs) {
      const isEvacuating = mci.status === 'EVACUATING';
      const statusColor = isEvacuating ? '#f59e0b' : '#ef4444';
      const pct = mci.total_casualties > 0 ? Math.round((mci.evacuated_count / mci.total_casualties) * 100) : 0;

      const pBreakdown = Object.entries(mci.casualty_counts_by_priority || {})
        .map(([k, v]) => `<span style="background: rgba(255,255,255,0.06); padding: 1px 4px; border-radius: 2px;">${k}: <strong>${v}</strong></span>`)
        .join(' ');

      const hDist = Object.entries(mci.hospital_distribution || {})
        .map(([h, count]) => `${h} (${count})`)
        .join(', ') || 'None';

      html += `
        <div style="background: rgba(220, 38, 38, 0.08); border: 1px solid rgba(239, 68, 68, 0.3); border-left: 3px solid ${statusColor}; border-radius: 4px; padding: 6px 8px; margin-bottom: 6px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="color: #f87171; font-size: 11px;">${mci.name}</strong>
            <span style="font-size: 10px; font-family: var(--font-mono); color: ${statusColor}; background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px;">
              ${mci.status}
            </span>
          </div>
          <div style="margin-top: 4px; display: flex; justify-content: space-between; color: #94a3b8; font-size: 10px;">
            <span>Casualties: <strong style="color: #f8fafc;">${mci.total_casualties}</strong></span>
            <span>Evacuated: <strong style="color: #10b981;">${mci.evacuated_count}</strong> (${pct}%)</span>
          </div>
          <!-- Evacuation Progress Bar -->
          <div style="width: 100%; height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; margin: 4px 0; overflow: hidden;">
            <div style="width: ${pct}%; height: 100%; background: #10b981; transition: width 0.3s ease;"></div>
          </div>
          <!-- Priority Distribution -->
          <div style="margin-top: 3px; font-size: 10px; color: #cbd5e1; display: flex; gap: 4px; flex-wrap: wrap;">
            ${pBreakdown}
          </div>
          <!-- Fleet & Hospitals -->
          <div style="margin-top: 4px; font-size: 10px; color: #94a3b8;">
            <div>Fleet: <strong style="color: #38bdf8;">${(mci.assigned_ambulance_ids || []).length} units assigned</strong></div>
            <div style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Hospitals: <strong style="color: #f1f5f9;">${hDist}</strong></div>
          </div>
        </div>
      `;
    }

    this.mciContainer.innerHTML = html;
  }
}

export const coordinationComponent = new CoordinationComponent();
