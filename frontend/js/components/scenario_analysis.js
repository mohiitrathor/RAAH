/**
 * RAAH Scenario Analysis & Comparison Controller (M10 Phase 3)
 * =============================================================
 *
 * Manages the Scenario Browser, A/B Scenario Comparisons,
 * Stress Test (25/50/100) Multi-Run Visualization, Before/After Snapshot
 * Delta Analysis, and Drill Evaluation Report Generation.
 */

import * as api from '../api.js';
import { showToast } from './toasts.js';

export class ScenarioAnalysisController {
  constructor(replayController) {
    this.replayCtrl = replayController;

    this.dom = {
      // Navigation
      btnNavReplay: document.getElementById('nav-btn-replay'),
      btnNavTactical: document.getElementById('nav-btn-tactical'),
      btnNavAnalytics: document.getElementById('nav-btn-analytics'),
      btnNavReview: document.getElementById('nav-btn-review'),
      workspaceReview: document.getElementById('review-workspace'),
      btnNavOpt: document.getElementById('nav-btn-optimization'),
      workspaceOpt: document.getElementById('optimization-workspace'),
      workspaceCommand: document.getElementById('command-workspace'),
      workspaceAnalytics: document.getElementById('analytics-workspace'),
      workspaceReplay: document.getElementById('replay-workspace'),
      intelDrawer: document.getElementById('intel-drawer'),

      // Scenario & Replay Browser
      tableReplayList: document.getElementById('table-replays-browser'),
      btnRefreshBrowser: document.getElementById('btn-refresh-replay-browser'),

      // KPI Scorecards
      kpiResilience: document.getElementById('analysis-kpi-resilience'),
      kpiDispatchSuccess: document.getElementById('analysis-kpi-dispatch-success'),
      kpiAvgEta: document.getElementById('analysis-kpi-avg-eta'),
      kpiUnresolved: document.getElementById('analysis-kpi-unresolved'),
      kpiSaturation: document.getElementById('analysis-kpi-saturation'),

      // Telemetry Breakdown
      txtFleetStats: document.getElementById('analysis-fleet-stats'),
      txtHospitalStats: document.getElementById('analysis-hospital-stats'),
      txtMciStats: document.getElementById('analysis-mci-stats'),

      // Scenario Comparison
      selectCompareA: document.getElementById('select-compare-scenario-a'),
      selectCompareB: document.getElementById('select-compare-scenario-b'),
      btnExecuteCompare: document.getElementById('btn-execute-scenario-compare'),
      containerCompareResults: document.getElementById('compare-results-container'),

      // Before / After Analysis
      inputTimeA: document.getElementById('input-before-after-time-a'),
      inputTimeB: document.getElementById('input-before-after-time-b'),
      btnExecuteBeforeAfter: document.getElementById('btn-execute-before-after'),
      containerBeforeAfterResults: document.getElementById('before-after-results-container'),

      // Report Generation
      btnExportReportJson: document.getElementById('btn-export-report-json'),
      btnExportReportMd: document.getElementById('btn-export-report-md'),
      containerReportModal: document.getElementById('report-modal-container'),
    };
  }

  init() {
    this.setupWorkspaceNavigation();
    this.bindEvents();
    this.loadBrowserList();
  }

  setupWorkspaceNavigation() {
    const tabs = [
      { btn: this.dom.btnNavTactical, ws: this.dom.workspaceCommand, drawer: true },
      { btn: this.dom.btnNavAnalytics, ws: this.dom.workspaceAnalytics, drawer: false },
      { btn: this.dom.btnNavReplay, ws: this.dom.workspaceReplay, drawer: false },
      { btn: this.dom.btnNavReview, ws: this.dom.workspaceReview, drawer: false },
      { btn: this.dom.btnNavOpt, ws: this.dom.workspaceOpt, drawer: false },
    ];

    const switchTab = (activeTab) => {
      tabs.forEach(({ btn, ws }) => {
        if (!btn || !ws) return;
        if (btn === activeTab.btn) {
          btn.classList.add('active');
          ws.style.display = (ws === this.dom.workspaceReplay || ws === this.dom.workspaceReview || ws === this.dom.workspaceOpt) ? 'grid' : (ws === this.dom.workspaceAnalytics ? 'flex' : 'grid');
        } else {
          btn.classList.remove('active');
          ws.style.display = 'none';
        }
      });

      if (this.dom.intelDrawer) {
        this.dom.intelDrawer.style.display = activeTab.drawer ? 'flex' : 'none';
      }

      if (activeTab.btn === this.dom.btnNavReplay) {
        setTimeout(() => {
          if (this.replayCtrl?.replayMap) {
            this.replayCtrl.replayMap.invalidateSize();
          }
        }, 150);
        this.loadBrowserList();
      }
    };

    tabs.forEach((tab) => {
      tab.btn?.addEventListener('click', () => switchTab(tab));
    });
  }

  bindEvents() {
    this.dom.btnRefreshBrowser?.addEventListener('click', () => this.loadBrowserList());
    this.dom.btnExecuteCompare?.addEventListener('click', () => this.handleCompareScenarios());
    this.dom.btnExecuteBeforeAfter?.addEventListener('click', () => this.handleBeforeAfter());
    this.dom.btnExportReportJson?.addEventListener('click', () => this.handleExportReport('json'));
    this.dom.btnExportReportMd?.addEventListener('click', () => this.handleExportReport('markdown'));
  }

  async loadBrowserList() {
    try {
      const replays = await api.getReplays();
      if (!this.dom.tableReplayList) return;

      this.dom.tableReplayList.innerHTML = '';
      if (!replays || replays.length === 0) {
        this.dom.tableReplayList.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #64748b; padding: 12px;">No recorded replays available. Run a scenario or drill first.</td></tr>';
        return;
      }

      // Populate comparison selectors
      if (this.dom.selectCompareA && this.dom.selectCompareB) {
        const opts = replays.map(r => `<option value="${r.run_id}">${r.scenario_id} (${r.run_id.slice(0, 10)})</option>`).join('');
        this.dom.selectCompareA.innerHTML = opts;
        this.dom.selectCompareB.innerHTML = opts;
        if (replays.length > 1) {
          this.dom.selectCompareB.selectedIndex = 1;
        }
      }

      replays.forEach((rep) => {
        const isOp = (rep.run_id && /^run_\d+$/.test(rep.run_id)) || (rep.scenario_id && rep.scenario_id.startsWith('OPERATIONAL_RUN_'));
        const typeBadge = isOp
          ? '<span style="background: #0284c7; color: white; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 700; margin-right: 4px; display: inline-block;">HISTORICAL OPERATIONAL RUN</span>'
          : '<span style="background: #e11d48; color: white; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 700; margin-right: 4px; display: inline-block;">SCENARIO REPLAY</span>';
        const duration = rep.end_sim_time !== undefined ? `${rep.end_sim_time}m` : (rep.duration_minutes !== undefined ? `${rep.duration_minutes}m` : 'N/A');
        const evCount = rep.event_count !== undefined ? `${rep.event_count} ev` : (rep.total_events !== undefined ? `${rep.total_events} ev` : 'N/A');
        const hashDisplay = rep.deterministic_hash ? rep.deterministic_hash.slice(0, 12) : (isOp ? 'IMMUTABLE_RUN' : '-');

        const tr = document.createElement('tr');
        tr.style.cssText = 'border-bottom: 1px solid #1e293b; font-size: 11px;';
        tr.innerHTML = `
          <td style="padding: 6px 8px; font-weight: 700; color: #38bdf8;">${typeBadge}${rep.scenario_id || rep.run_id}</td>
          <td style="padding: 6px 8px; font-family: monospace; color: #94a3b8;">${rep.run_id.slice(0, 14)}</td>
          <td style="padding: 6px 8px; color: #f1f5f9;">${duration} (${evCount})</td>
          <td style="padding: 6px 8px; font-family: monospace; color: #a5f3fc; font-size: 10px;">${hashDisplay}</td>
          <td style="padding: 6px 8px; text-align: right;">
            <button class="btn-tactical btn-xs btn-open-rep" data-runid="${rep.run_id}" style="padding: 2px 6px; font-size: 10px; background: rgba(56, 189, 248, 0.2); border-color: #38bdf8; color: #7dd3fc;">
              Open
            </button>
            <button class="btn-tactical btn-xs btn-analyze-rep" data-runid="${rep.run_id}" style="padding: 2px 6px; font-size: 10px; background: rgba(168, 85, 247, 0.2); border-color: #a855f7; color: #d8b4fe; margin-left: 4px;">
              Analyze
            </button>
          </td>
        `;

        tr.querySelector('.btn-open-rep')?.addEventListener('click', () => {
          if (this.replayCtrl) {
            this.replayCtrl.loadReplay(rep.run_id);
          }
        });

        tr.querySelector('.btn-analyze-rep')?.addEventListener('click', () => {
          this.loadAnalysisDashboard(rep.run_id);
        });

        this.dom.tableReplayList.appendChild(tr);
      });

      // Auto-load analysis for the first replay
      if (replays.length > 0) {
        this.loadAnalysisDashboard(replays[0].run_id);
      }
    } catch (err) {
      console.warn('Failed to load replay browser:', err);
    }
  }

  async loadAnalysisDashboard(runId) {
    try {
      const data = await api.getReplayAnalysis(runId);

      // 1. KPI Cards
      const rScore = data.resilience_score?.overall || 0;
      if (this.dom.kpiResilience) {
        this.dom.kpiResilience.textContent = `${rScore}`;
        this.dom.kpiResilience.style.color = rScore >= 75 ? '#22c55e' : rScore >= 50 ? '#eab308' : '#ef4444';
      }
      if (this.dom.kpiDispatchSuccess) {
        this.dom.kpiDispatchSuccess.textContent = `${data.fleet_metrics.dispatch_success_ratio_pct}%`;
      }
      if (this.dom.kpiAvgEta) {
        this.dom.kpiAvgEta.textContent = `${data.fleet_metrics.average_dispatch_eta_minutes}m`;
      }
      if (this.dom.kpiUnresolved) {
        this.dom.kpiUnresolved.textContent = `${data.unresolved_incidents}`;
      }
      if (this.dom.kpiSaturation) {
        this.dom.kpiSaturation.textContent = `${data.hospital_saturation_count} Full`;
      }

      // 2. Telemetry sections
      if (this.dom.txtFleetStats) {
        this.dom.txtFleetStats.innerHTML = `
          <div>Peak En Route: <b>${data.peak_en_route}</b> (${data.fleet_metrics.utilization_ratio_pct}%)</div>
          <div>Peak Repositioning: <b>${data.peak_repositioning}</b></div>
          <div>Total Dispatches: <b>${data.dispatch_count}</b></div>
        `;
      }

      if (this.dom.txtHospitalStats) {
        this.dom.txtHospitalStats.innerHTML = `
          <div>Hospitals Used: <b>${data.hospital_metrics.hospitals_used_count}</b></div>
          <div>Peak Projected Load: <b>${Math.round(data.hospital_metrics.peak_projected_utilization * 100)}%</b></div>
          <div>ICU Saturated Facilities: <b>${data.hospital_metrics.hospitals_reaching_icu_full_count}</b></div>
        `;
      }

      if (this.dom.txtMciStats) {
        this.dom.txtMciStats.innerHTML = `
          <div>Total Declared MCIs: <b>${data.mci_count}</b></div>
          <div>Peak Concurrent MCIs: <b>${data.mci_metrics.peak_concurrent_mcis}</b></div>
          <div>Unresolved MCI Casualties: <b>${data.unresolved_mcis}</b></div>
        `;
      }
    } catch (err) {
      console.warn('Failed to load analysis dashboard:', err);
    }
  }

  async handleCompareScenarios() {
    const idA = this.dom.selectCompareA?.value;
    const idB = this.dom.selectCompareB?.value;
    if (!idA || !idB || !this.dom.containerCompareResults) return;

    this.dom.containerCompareResults.innerHTML = '<div style="padding: 10px; color: #38bdf8; font-size: 11px;">Computing scenario differential analysis...</div>';

    try {
      const res = await api.compareScenarios(idA, idB);
      const d = res.delta;

      this.dom.containerCompareResults.innerHTML = `
        <div style="background: rgba(15, 23, 42, 0.9); border: 1px solid #334155; border-radius: 6px; padding: 10px; font-size: 11px; margin-top: 8px;">
          <div style="font-weight: 700; color: #38bdf8; margin-bottom: 6px;">Differential: Scenario A vs Scenario B</div>
          <div style="color: #cbd5e1; margin-bottom: 8px; font-style: italic;">"${res.performance_explanation}"</div>
          <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 10px;">
            <thead>
              <tr style="border-bottom: 1px solid #475569; color: #94a3b8;">
                <th style="padding: 3px 6px;">Metric</th>
                <th style="padding: 3px 6px;">Scenario A</th>
                <th style="padding: 3px 6px;">Scenario B</th>
                <th style="padding: 3px 6px;">Delta</th>
              </tr>
            </thead>
            <tbody>
              <tr style="border-bottom: 1px solid #1e293b;">
                <td style="padding: 3px 6px; color: #94a3b8;">Casualties</td>
                <td style="padding: 3px 6px;">${res.scenario_a.casualties}</td>
                <td style="padding: 3px 6px;">${res.scenario_b.casualties}</td>
                <td style="padding: 3px 6px; font-weight: 700;">${d.total_casualties > 0 ? '+' : ''}${d.total_casualties}</td>
              </tr>
              <tr style="border-bottom: 1px solid #1e293b;">
                <td style="padding: 3px 6px; color: #94a3b8;">Dispatch Success</td>
                <td style="padding: 3px 6px;">${res.scenario_a.dispatch_success_pct}%</td>
                <td style="padding: 3px 6px;">${res.scenario_b.dispatch_success_pct}%</td>
                <td style="padding: 3px 6px; color: ${d.dispatch_success_pct >= 0 ? '#22c55e' : '#ef4444'}; font-weight: 700;">${d.dispatch_success_pct > 0 ? '+' : ''}${d.dispatch_success_pct}%</td>
              </tr>
              <tr style="border-bottom: 1px solid #1e293b;">
                <td style="padding: 3px 6px; color: #94a3b8;">Average ETA</td>
                <td style="padding: 3px 6px;">${res.scenario_a.average_eta_minutes}m</td>
                <td style="padding: 3px 6px;">${res.scenario_b.average_eta_minutes}m</td>
                <td style="padding: 3px 6px; color: ${d.average_eta_minutes <= 0 ? '#22c55e' : '#ef4444'}; font-weight: 700;">${d.average_eta_minutes > 0 ? '+' : ''}${d.average_eta_minutes}m</td>
              </tr>
              <tr style="border-bottom: 1px solid #1e293b;">
                <td style="padding: 3px 6px; color: #94a3b8;">Hosp. Saturation</td>
                <td style="padding: 3px 6px;">${res.scenario_a.hospital_saturation_count}</td>
                <td style="padding: 3px 6px;">${res.scenario_b.hospital_saturation_count}</td>
                <td style="padding: 3px 6px; font-weight: 700;">${d.hospital_saturation_events > 0 ? '+' : ''}${d.hospital_saturation_events}</td>
              </tr>
              <tr>
                <td style="padding: 3px 6px; color: #94a3b8;">Resilience Score</td>
                <td style="padding: 3px 6px; font-weight: 700;">${res.scenario_a.resilience_score}</td>
                <td style="padding: 3px 6px; font-weight: 700;">${res.scenario_b.resilience_score}</td>
                <td style="padding: 3px 6px; font-weight: 800; color: ${d.resilience_score >= 0 ? '#22c55e' : '#ef4444'};">${d.resilience_score > 0 ? '+' : ''}${d.resilience_score}</td>
              </tr>
            </tbody>
          </table>
        </div>
      `;
    } catch (err) {
      this.dom.containerCompareResults.innerHTML = `<div style="padding: 8px; color: #f87171; font-size: 11px;">Comparison failed: ${err.message}</div>`;
    }
  }

  async handleBeforeAfter() {
    const runId = this.replayCtrl?.activeRunId;
    const tA = parseInt(this.dom.inputTimeA?.value || '2', 10);
    const tB = parseInt(this.dom.inputTimeB?.value || '8', 10);
    if (!runId || !this.dom.containerBeforeAfterResults) return;

    this.dom.containerBeforeAfterResults.innerHTML = '<div style="padding: 8px; color: #38bdf8; font-size: 11px;">Comparing T=' + tA + ' vs T=' + tB + '...</div>';

    try {
      const res = await api.compareBeforeAfter(runId, tA, tB);
      const d = res.delta;

      this.dom.containerBeforeAfterResults.innerHTML = `
        <div style="background: rgba(15, 23, 42, 0.9); border: 1px solid #334155; border-radius: 6px; padding: 8px; font-size: 11px; margin-top: 6px;">
          <div style="font-weight: 700; color: #38bdf8; margin-bottom: 4px;">Snapshot Delta: T+${tA}m -> T+${tB}m</div>
          <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; font-size: 10px;">
            <div style="background: #090d16; padding: 4px; border-radius: 3px;">En Route: <b>${d.en_route_ambulances > 0 ? '+' : ''}${d.en_route_ambulances}</b></div>
            <div style="background: #090d16; padding: 4px; border-radius: 3px;">Arrived: <b>${d.arrived_ambulances > 0 ? '+' : ''}${d.arrived_ambulances}</b></div>
            <div style="background: #090d16; padding: 4px; border-radius: 3px;">Hosp Util: <b>${d.hospital_utilization_pct > 0 ? '+' : ''}${d.hospital_utilization_pct}%</b></div>
          </div>
        </div>
      `;
    } catch (err) {
      this.dom.containerBeforeAfterResults.innerHTML = `<div style="padding: 8px; color: #f87171; font-size: 11px;">Before/After analysis failed: ${err.message}</div>`;
    }
  }

  async handleExportReport(format) {
    const runId = this.replayCtrl?.activeRunId;
    if (!runId) {
      showToast('Please select or open an active replay run first.', 'warning');
      return;
    }

    try {
      const report = await api.generateDrillReport(runId, format);
      const filename = `drill_report_${runId}.${format === 'markdown' ? 'md' : 'json'}`;
      const content = format === 'markdown' ? report.markdown_content : JSON.stringify(report, null, 2);

      const blob = new Blob([content], { type: format === 'markdown' ? 'text/markdown' : 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast(`Exported ${format.toUpperCase()} report successfully`, 'success');
    } catch (err) {
      showToast(`Report export failed: ${err.message}`, 'error');
    }
  }
}
