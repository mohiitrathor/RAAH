/**
 * RAAH Post-Incident Review (PIR) Controller (M10 Phase 4)
 * =========================================================
 *
 * Renders automated post-incident reviews, root-cause causal chains,
 * categorized operational findings, and actionable recommendations.
 *
 * STRICT INVARIANT:
 * Purely observational. Never mutates live simulator or dispatch state.
 */

import * as api from '../api.js';
import { showToast } from './toasts.js';

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export class PIRController {
  constructor() {
    this.activeRunId = null;
    this.currentPIR = null;

    this.dom = {
      selectPIRRun: document.getElementById('select-pir-run'),
      btnRefreshPIR: document.getElementById('btn-refresh-pir'),
      txtSeverityBadge: document.getElementById('pir-severity-badge'),
      txtResilienceScore: document.getElementById('pir-resilience-score'),
      txtPIRSummary: document.getElementById('pir-summary-text'),
      containerRootCauseGraph: document.getElementById('pir-root-cause-graph'),
      containerFindingsList: document.getElementById('pir-findings-list'),
      selectFindingCategory: document.getElementById('select-pir-finding-category'),
      containerRecommendations: document.getElementById('pir-recommendations-list'),
      btnExportPIRJson: document.getElementById('btn-export-pir-json'),
      btnExportPIRMd: document.getElementById('btn-export-pir-md'),
    };
  }

  init() {
    if (!this.dom.selectPIRRun) return;
    this.bindEvents();
    this.refreshRunList();
  }

  bindEvents() {
    this.dom.selectPIRRun?.addEventListener('change', (e) => {
      this.loadPIR(e.target.value);
    });

    this.dom.btnRefreshPIR?.addEventListener('click', () => {
      this.refreshRunList();
    });

    this.dom.selectFindingCategory?.addEventListener('change', () => {
      this.renderFindings();
    });

    this.dom.btnExportPIRJson?.addEventListener('click', () => {
      this.exportReport('json');
    });

    this.dom.btnExportPIRMd?.addEventListener('click', () => {
      this.exportReport('markdown');
    });
  }

  async refreshRunList() {
    try {
      const replays = await api.getReplays();
      if (!this.dom.selectPIRRun) return;

      this.dom.selectPIRRun.innerHTML = '';
      if (!replays || replays.length === 0) {
        this.dom.selectPIRRun.innerHTML = '<option value="">No recorded replays available</option>';
        return;
      }

      replays.forEach((r, idx) => {
        const isOp = (r.run_id && /^run_\d+$/.test(r.run_id)) || (r.scenario_id && r.scenario_id.startsWith('OPERATIONAL_RUN_'));
        const prefix = isOp ? '[OPERATIONAL] ' : '[SCENARIO] ';
        const dur = r.end_sim_time !== undefined ? `${r.end_sim_time}m` : (r.duration_minutes !== undefined ? `${r.duration_minutes}m` : 'N/A');
        const opt = document.createElement('option');
        opt.value = r.run_id;
        opt.textContent = `${prefix}${r.scenario_id || r.run_id} (${r.run_id.slice(0, 14)}) - ${dur}`;
        if (idx === 0) opt.selected = true;
        this.dom.selectPIRRun.appendChild(opt);
      });

      if (replays.length > 0) {
        this.loadPIR(replays[0].run_id);
      }
    } catch (err) {
      console.warn('Failed to refresh PIR run list:', err);
    }
  }

  async loadPIR(runId) {
    if (!runId) return;
    this.activeRunId = runId;

    try {
      const pir = await api.getPIR(runId);
      this.currentPIR = pir;
      this.renderReviewHeader();
      this.renderRootCauseGraph();
      this.renderFindings();
      this.renderRecommendations();
    } catch (err) {
      console.error('Failed to load PIR:', err);
      showToast(`Failed to load PIR: ${err.message}`, 'error');
    }
  }

  renderReviewHeader() {
    if (!this.currentPIR) return;
    const pir = this.currentPIR;

    if (this.dom.txtSeverityBadge) {
      const sev = pir.overall_severity || 'NORMAL';
      const color =
        sev === 'CRITICAL_FAILURE' ? '#ef4444' :
        sev === 'ELEVATED_RISK' ? '#f59e0b' :
        sev === 'MINOR_ISSUES' ? '#38bdf8' : '#22c55e';

      this.dom.txtSeverityBadge.style.background = color;
      this.dom.txtSeverityBadge.style.color = sev === 'ELEVATED_RISK' ? '#000' : '#fff';
      this.dom.txtSeverityBadge.textContent = sev.replace('_', ' ');
    }

    if (this.dom.txtResilienceScore) {
      this.dom.txtResilienceScore.textContent = `${pir.resilience_score}/100`;
    }

    if (this.dom.txtPIRSummary) {
      this.dom.txtPIRSummary.textContent = pir.summary;
    }
  }

  renderRootCauseGraph() {
    if (!this.dom.containerRootCauseGraph || !this.currentPIR) return;
    const g = this.currentPIR.root_cause_graph || { nodes: [], edges: [] };
    const cascades = this.currentPIR.cascading_failures || [];

    if (g.nodes.length === 0) {
      this.dom.containerRootCauseGraph.innerHTML = `
        <div style="padding: 14px; color: #64748b; font-size: 11px; text-align: center;">
          ✓ No systemic root-cause anomalies detected. Operations remained within baseline parameters.
        </div>
      `;
      return;
    }

    // Build visual causal node sequence
    let html = `
      <div style="background: rgba(15, 23, 42, 0.95); border: 1px solid #334155; border-radius: 6px; padding: 12px; font-size: 11px;">
        <div style="font-weight: 700; color: #38bdf8; margin-bottom: 8px;">Causal Failure Chain</div>
        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px;">
    `;

    g.nodes.forEach((n, idx) => {
      const bColor = n.severity === 'CRITICAL' ? '#ef4444' : '#f59e0b';
      html += `
        <div style="background: #1e293b; border: 1px solid ${bColor}; border-radius: 6px; padding: 6px 10px;">
          <div style="font-size: 9px; color: #94a3b8; text-transform: uppercase;">${escapeHtml(n.category)}</div>
          <div style="font-weight: 700; color: #f1f5f9;">${escapeHtml(n.label)}</div>
        </div>
      `;
      if (idx < g.nodes.length - 1) {
        html += `<span style="color: #64748b; font-weight: 800;">→</span>`;
      }
    });

    html += `</div>`;

    if (cascades.length > 0) {
      html += `<div style="color: #f87171; font-weight: 700; font-size: 10px; margin-bottom: 4px;">Detected Cascading Sequences:</div>`;
      cascades.forEach((chain) => {
        const safeChain = Array.isArray(chain) ? chain.map(escapeHtml).join(' ➔ ') : escapeHtml(chain);
        html += `
          <div style="background: rgba(239, 68, 68, 0.1); border-left: 3px solid #ef4444; padding: 4px 8px; font-family: monospace; font-size: 10px; color: #fca5a5; margin-bottom: 4px;">
            ${safeChain}
          </div>
        `;
      });
    }

    html += `</div>`;
    this.dom.containerRootCauseGraph.innerHTML = html;
  }

  renderFindings() {
    if (!this.dom.containerFindingsList || !this.currentPIR) return;
    const cat = this.dom.selectFindingCategory?.value || 'ALL';
    let list = this.currentPIR.findings || [];
    if (cat !== 'ALL') {
      list = list.filter(f => f.category === cat);
    }

    if (list.length === 0) {
      this.dom.containerFindingsList.innerHTML = `
        <div style="padding: 14px; color: #64748b; font-size: 11px; text-align: center;">
          No findings matching selected filter.
        </div>
      `;
      return;
    }

    this.dom.containerFindingsList.innerHTML = list.map(f => {
      const badgeColor = f.severity === 'CRITICAL' ? '#ef4444' : f.severity === 'WARNING' ? '#f59e0b' : '#38bdf8';
      const causes = Array.isArray(f.potential_causes) ? f.potential_causes.map(escapeHtml).join(', ') : escapeHtml(f.potential_causes);
      return `
        <div style="background: rgba(15, 23, 42, 0.9); border: 1px solid #334155; border-radius: 6px; padding: 10px; margin-bottom: 8px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: 700; color: #f1f5f9;">${escapeHtml(f.title)}</span>
            <span style="background: ${badgeColor}; color: #000; font-weight: 800; font-size: 9px; padding: 2px 6px; border-radius: 3px;">
              ${escapeHtml(f.severity)}
            </span>
          </div>
          <div style="color: #94a3b8; margin-bottom: 6px;">${escapeHtml(f.description)}</div>
          <div style="color: #cbd5e1; font-size: 10px; margin-bottom: 4px;"><b>Impact:</b> ${escapeHtml(f.measurable_impact)}</div>
          <div style="color: #64748b; font-size: 10px;"><b>Potential Causes:</b> ${causes}</div>
        </div>
      `;
    }).join('');
  }

  renderRecommendations() {
    if (!this.dom.containerRecommendations || !this.currentPIR) return;
    const recs = this.currentPIR.recommendations || [];

    if (recs.length === 0) {
      this.dom.containerRecommendations.innerHTML = `
        <div style="padding: 14px; color: #64748b; font-size: 11px; text-align: center;">
          No corrective recommendations required for this scenario.
        </div>
      `;
      return;
    }

    this.dom.containerRecommendations.innerHTML = recs.map(r => {
      const pColor = r.priority === 'URGENT' ? '#ef4444' : r.priority === 'HIGH' ? '#f97316' : '#38bdf8';
      return `
        <div style="background: rgba(15, 23, 42, 0.9); border-left: 3px solid ${pColor}; border-top: 1px solid #334155; border-right: 1px solid #334155; border-bottom: 1px solid #334155; border-radius: 4px; padding: 8px 10px; margin-bottom: 6px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-weight: 700; color: #f1f5f9;">${escapeHtml(r.issue)}</span>
            <span style="color: ${pColor}; font-weight: 800; font-size: 9px;">[${escapeHtml(r.priority)}]</span>
          </div>
          <div style="color: #7dd3fc; margin-bottom: 2px;"><b>Action:</b> ${escapeHtml(r.action)}</div>
          <div style="color: #94a3b8; font-size: 10px;"><b>Expected Benefit:</b> ${escapeHtml(r.expected_benefit)}</div>
        </div>
      `;
    }).join('');
  }

  async exportReport(format) {
    if (!this.activeRunId) {
      showToast('Please select a PIR run to export.', 'warning');
      return;
    }

    try {
      const res = await api.exportPIRReport(this.activeRunId, format);
      const filename = `PIR_Report_${this.activeRunId}.${format === 'markdown' ? 'md' : format === 'html' ? 'html' : 'json'}`;
      const content = typeof res.content === 'string' ? res.content : JSON.stringify(res.content, null, 2);
      const mime = format === 'markdown' ? 'text/markdown' : format === 'html' ? 'text/html' : 'application/json';

      const blob = new Blob([content], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast(`Exported PIR as ${format.toUpperCase()} successfully`, 'success');
    } catch (err) {
      showToast(`Export failed: ${err.message}`, 'error');
    }
  }
}
