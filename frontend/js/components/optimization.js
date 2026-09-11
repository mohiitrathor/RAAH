/**
 * RAAH Real-Time Dispatch Optimization, Operator Copilot & Adaptive Learning (M11 Phase 4)
 * ======================================================================================
 *
 * Provides interactive Operator Copilot recommendations, isolated what-if simulations,
 * explicit operator approval, bounded Adaptive Policy evaluation (OFF/ADVISORY/GUARDED),
 * emergency kill-switch, closed-loop outcome telemetry, safe fleet repositioning rollback,
 * confidence calibration tables, operational drift indicators, and policy adaptation.
 *
 * STRICT INVARIANTS:
 * - Authoritative execution strictly follows bounded policy or explicit operator approval.
 * - Zero blocking browser modal dialogs; uses tactical toasts exclusively.
 */

import * as api from '../api.js';
import { showToast } from './toasts.js';

export class OptimizationController {
  constructor() {
    this.dom = {
      workspace: document.getElementById('optimization-workspace'),
      btnNavOpt: document.getElementById('nav-btn-optimization'),
      txtPolicyModeVal: document.getElementById('opt-policy-mode-val'),
      txtAutoActionsCount: document.getElementById('opt-auto-actions-count'),
      txtBlockedCount: document.getElementById('opt-blocked-count'),
      badgeHealth: document.getElementById('opt-health-badge'),
      txtPendingCount: document.getElementById('opt-pending-count'),
      txtExecutionsCount: document.getElementById('opt-executions-count'),
      txtLatestOutcome: document.getElementById('opt-latest-outcome'),
      btnRefresh: document.getElementById('btn-refresh-opt'),
      btnPolicyOff: document.getElementById('btn-policy-off'),
      btnPolicyAdvisory: document.getElementById('btn-policy-advisory'),
      btnPolicyGuarded: document.getElementById('btn-policy-guarded'),
      btnKillSwitch: document.getElementById('btn-kill-switch'),
      listRecs: document.getElementById('opt-recommendations-list'),
      detailContainer: document.getElementById('opt-detail-container'),
      selectFilterType: document.getElementById('select-opt-filter-type'),

      // Section 13 Learning & Calibration elements
      txtSafetyScore: document.getElementById('learning-safety-score-val'),
      txtPolicyVersion: document.getElementById('learning-policy-version'),
      txtPolicyMode: document.getElementById('learning-policy-mode'),
      txtPolicyThreshold: document.getElementById('learning-policy-threshold'),
      txtPolicyDrift: document.getElementById('learning-policy-drift'),
      txtLearningAutoActions: document.getElementById('learning-auto-actions'),
      txtLearningSuccessRate: document.getElementById('learning-success-rate'),
      txtLearningHarmfulActions: document.getElementById('learning-harmful-actions'),
      txtLearningRollbacks: document.getElementById('learning-rollbacks'),
      tblCalibration: document.getElementById('calibration-table-body'),
      containerAdaptiveRecs: document.getElementById('adaptive-recs-container'),
      listPolicyHistory: document.getElementById('policy-history-list'),
      badgeCalibStatus: document.getElementById('calib-status-badge'),
    };

    this.currentRecommendations = [];
    this.selectedRecId = null;
    this.policyConfig = null;
    this.currentPolicyEval = null;
  }

  init() {
    if (!this.dom.workspace) return;
    this.bindEvents();
  }

  bindEvents() {
    this.dom.btnRefresh?.addEventListener('click', () => this.loadOptimizationData());
    this.dom.selectFilterType?.addEventListener('change', () => this.renderRecommendations());

    this.dom.btnPolicyOff?.addEventListener('click', () => this.handleSetMode('OFF'));
    this.dom.btnPolicyAdvisory?.addEventListener('click', () => this.handleSetMode('ADVISORY'));
    this.dom.btnPolicyGuarded?.addEventListener('click', () => this.handleSetMode('GUARDED'));
    this.dom.btnKillSwitch?.addEventListener('click', () => this.handleToggleKillSwitch());
  }

  async handleSetMode(mode) {
    try {
      await api.setPolicyMode(mode, 'OPERATOR_COMMANDER', `Switched to ${mode} mode via Copilot`);
      showToast(`Autonomy policy updated to: ${mode}`, 'success');
      await this.loadOptimizationData();
    } catch (err) {
      showToast(`Failed to set policy mode: ${err.message}`, 'error');
    }
  }

  async handleToggleKillSwitch() {
    const isKillActive = this.policyConfig?.kill_switch_active;
    const nextAction = isKillActive ? 'RELEASE' : 'ENGAGE';
    try {
      await api.toggleKillSwitch(nextAction, 'OPERATOR_COMMANDER', `Kill-switch ${nextAction} via workstation`);
      showToast(`Emergency kill-switch: ${nextAction === 'ENGAGE' ? 'ENGAGED' : 'RELEASED'}`, nextAction === 'ENGAGE' ? 'warning' : 'success');
      await this.loadOptimizationData();
    } catch (err) {
      showToast(`Kill-switch action failed: ${err.message}`, 'error');
    }
  }

  async loadOptimizationData() {
    try {
      const [copilotSummary, recs, policyOverview, learningReport, policyHistory] = await Promise.all([
        api.getOptimizationCopilotSummary(),
        api.getOptimizationRecommendations(),
        api.getPolicyOverview(),
        api.getLearningReport(),
        api.getPolicyHistory(),
      ]);

      this.policyConfig = policyOverview.config;

      // Update Policy Mode Badges and Telemetry
      if (this.dom.txtPolicyModeVal) {
        this.dom.txtPolicyModeVal.textContent = policyOverview.mode || 'GUARDED';
      }

      // Highlight active mode button
      [this.dom.btnPolicyOff, this.dom.btnPolicyAdvisory, this.dom.btnPolicyGuarded].forEach(btn => {
        if (!btn) return;
        btn.classList.remove('active');
        btn.style.background = '';
        btn.style.color = '';
      });

      const activeBtn =
        policyOverview.mode === 'OFF' ? this.dom.btnPolicyOff :
        policyOverview.mode === 'ADVISORY' ? this.dom.btnPolicyAdvisory : this.dom.btnPolicyGuarded;
      if (activeBtn) {
        activeBtn.classList.add('active');
        activeBtn.style.background = '#0284c7';
        activeBtn.style.color = '#fff';
      }

      // Kill Switch styling
      if (this.dom.btnKillSwitch) {
        if (policyOverview.kill_switch_active) {
          this.dom.btnKillSwitch.style.background = '#ef4444';
          this.dom.btnKillSwitch.style.color = '#ffffff';
          this.dom.btnKillSwitch.innerHTML = `<i data-lucide="shield-alert"></i> KILL ACTIVE`;
        } else {
          this.dom.btnKillSwitch.style.background = '';
          this.dom.btnKillSwitch.style.color = '#f87171';
          this.dom.btnKillSwitch.innerHTML = `<i data-lucide="shield-alert"></i> KILL SWITCH`;
        }
      }

      if (this.dom.badgeHealth) {
        this.dom.badgeHealth.textContent = `HEALTH: ${copilotSummary.operational_health}`;
        if (copilotSummary.operational_health === 'NORMAL') {
          this.dom.badgeHealth.style.borderColor = '#22c55e';
          this.dom.badgeHealth.style.color = '#86efac';
        } else {
          this.dom.badgeHealth.style.borderColor = '#f59e0b';
          this.dom.badgeHealth.style.color = '#fde68a';
        }
      }

      if (this.dom.txtAutoActionsCount) {
        this.dom.txtAutoActionsCount.textContent = policyOverview.performance?.autonomous_actions_executed || 0;
      }
      if (this.dom.txtBlockedCount) {
        this.dom.txtBlockedCount.textContent = policyOverview.performance?.blocked_actions || 0;
      }
      if (this.dom.txtPendingCount) this.dom.txtPendingCount.textContent = copilotSummary.pending_recommendations_count;
      if (this.dom.txtExecutionsCount) this.dom.txtExecutionsCount.textContent = copilotSummary.recent_executions_count;
      if (this.dom.txtLatestOutcome) {
        if (copilotSummary.latest_execution_outcome) {
          const outcome = copilotSummary.latest_execution_outcome;
          const execMode = outcome.execution_mode === 'AUTONOMOUS' ? '[AUTO]' : '[OP]';
          this.dom.txtLatestOutcome.textContent = `${execMode} ${outcome.recommendation_type} (${outcome.execution_status})`;
          this.dom.txtLatestOutcome.className = outcome.execution_status === 'SUCCESS' ? 'kpi-val text-green' : 'kpi-val text-amber';
        } else {
          this.dom.txtLatestOutcome.textContent = 'NONE';
        }
      }

      // ----------------------------------------------------------------
      // Render Section 13 Learning & Calibration Telemetry
      // ----------------------------------------------------------------
      if (this.dom.txtSafetyScore) {
        this.dom.txtSafetyScore.textContent = learningReport.safety_score.score;
      }
      if (this.dom.txtPolicyVersion) {
        this.dom.txtPolicyVersion.textContent = policyOverview.config.policy_version || 'v1';
      }
      if (this.dom.txtPolicyMode) {
        this.dom.txtPolicyMode.textContent = policyOverview.mode;
      }
      if (this.dom.txtPolicyThreshold) {
        this.dom.txtPolicyThreshold.textContent = policyOverview.config.min_confidence_reposition;
      }
      if (this.dom.txtPolicyDrift) {
        this.dom.txtPolicyDrift.textContent = learningReport.drift.severity;
        this.dom.txtPolicyDrift.style.color =
          learningReport.drift.severity === 'NORMAL' ? '#86efac' :
          learningReport.drift.severity === 'WATCH' ? '#fbbf24' : '#f87171';
      }
      if (this.dom.txtLearningAutoActions) {
        this.dom.txtLearningAutoActions.textContent = learningReport.performance.autonomous_executions;
      }
      if (this.dom.txtLearningSuccessRate) {
        const totalA = learningReport.performance.autonomous_executions;
        const succA = learningReport.performance.successful_actions;
        const pct = totalA > 0 ? ((succA / totalA) * 100).toFixed(1) : '100';
        this.dom.txtLearningSuccessRate.textContent = `${pct}%`;
      }
      if (this.dom.txtLearningHarmfulActions) {
        this.dom.txtLearningHarmfulActions.textContent = learningReport.performance.harmful_actions;
      }
      if (this.dom.txtLearningRollbacks) {
        this.dom.txtLearningRollbacks.textContent = learningReport.performance.rollback_attempts;
      }

      // Render Calibration Table
      if (this.dom.tblCalibration && learningReport.calibration?.buckets) {
        const rows = learningReport.calibration.buckets.map(b => `
          <tr>
            <td>${b.min_confidence.toFixed(2)}–${b.max_confidence.toFixed(2)}</td>
            <td>${b.executed_count}</td>
            <td>${b.executed_count > 0 ? (b.empirical_success_rate * 100).toFixed(1) + '%' : '--'}</td>
            <td>${(b.calibration_error * 100).toFixed(1)}%</td>
          </tr>
        `).join('');
        this.dom.tblCalibration.innerHTML = rows;
      }
      if (this.dom.badgeCalibStatus) {
        this.dom.badgeCalibStatus.textContent = learningReport.calibration.is_well_calibrated ? 'Well-Calibrated' : 'Recalibration Advised';
        this.dom.badgeCalibStatus.style.color = learningReport.calibration.is_well_calibrated ? '#86efac' : '#fbbf24';
      }

      // Render Adaptive Policy Recommendations
      if (this.dom.containerAdaptiveRecs) {
        if (!learningReport.recommendations || learningReport.recommendations.length === 0) {
          this.dom.containerAdaptiveRecs.innerHTML = `
            <div style="font-size: 11px; color: #64748b; text-align: center; padding: 12px;">
              Nominal performance: no policy parameter tuning needed.
            </div>
          `;
        } else {
          this.dom.containerAdaptiveRecs.innerHTML = learningReport.recommendations.map(r => `
            <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid #334155; border-radius: 4px; padding: 8px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-weight: 700; color: #38bdf8; font-size: 10px;">${r.policy_parameter}</span>
                <span style="font-size: 9px; padding: 1px 4px; border-radius: 2px; background: rgba(56, 189, 248, 0.2); color: #38bdf8;">Risk: ${r.risk_level}</span>
              </div>
              <div style="font-size: 10px; color: #cbd5e1; margin-bottom: 4px;">${r.expected_effect}</div>
              <div style="font-size: 9px; color: #94a3b8; margin-bottom: 6px;">${r.evidence}</div>
              <div style="display: flex; gap: 4px;">
                <button class="btn-tactical btn-xs btn-whatif-adapt" data-param="${r.policy_parameter}" data-val="${r.proposed_value}" style="padding: 1px 5px; font-size: 9px;">What-If</button>
                <button class="btn-tactical btn-xs btn-approve-adapt" data-id="${r.recommendation_id}" style="padding: 1px 5px; font-size: 9px; background: #16a34a; color: #fff;">Approve</button>
                <button class="btn-tactical btn-xs btn-reject-adapt" data-id="${r.recommendation_id}" style="padding: 1px 5px; font-size: 9px; background: #dc2626; color: #fff;">Reject</button>
              </div>
            </div>
          `).join('');

          // Wire Adaptive Action Buttons
          this.dom.containerAdaptiveRecs.querySelectorAll('.btn-approve-adapt').forEach(btn => {
            btn.addEventListener('click', async () => {
              const id = btn.getAttribute('data-id');
              await this.handleApproveAdaptation(id);
            });
          });
          this.dom.containerAdaptiveRecs.querySelectorAll('.btn-reject-adapt').forEach(btn => {
            btn.addEventListener('click', async () => {
              const id = btn.getAttribute('data-id');
              await this.handleRejectAdaptation(id);
            });
          });
          this.dom.containerAdaptiveRecs.querySelectorAll('.btn-whatif-adapt').forEach(btn => {
            btn.addEventListener('click', async () => {
              const param = btn.getAttribute('data-param');
              const val = btn.getAttribute('data-val');
              await this.handleWhatIfAdaptation(param, val);
            });
          });
        }
      }

      // Render Policy History
      if (this.dom.listPolicyHistory && Array.isArray(policyHistory)) {
        this.dom.listPolicyHistory.innerHTML = policyHistory.length > 0
          ? policyHistory.map(v => `
            <div class="policy-history-item">
              <div>
                <span class="badge badge-subtle font-mono" style="margin-right: 6px;">${v.version}</span>
                <span style="color: var(--text-primary); font-size: 11px;">${v.change_reason || 'Configuration update'}</span>
              </div>
              <span class="font-mono text-muted" style="font-size: 10px;">${v.approved_by || 'OP'}</span>
            </div>
          `).join('')
          : '<div class="empty-operational-state" style="padding: 12px;">No historical policy mutations recorded.</div>';
      }

      this.currentRecommendations = recs;
      this.renderRecommendations();

      if (recs.length > 0 && !this.selectedRecId) {
        this.selectRecommendation(recs[0].recommendation_id);
      }
      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      showToast(`Failed to load copilot intelligence: ${err.message}`, 'error');
    }
  }

  async handleApproveAdaptation(id) {
    showToast(`Approving adaptive policy recommendation ${id}...`, 'info');
    try {
      await api.approveLearningRecommendation(id, 'OPERATOR_DISPATCHER');
      showToast(`Policy successfully updated and new version committed!`, 'success');
      await this.loadOptimizationData();
    } catch (err) {
      showToast(`Approval failed: ${err.message}`, 'error');
    }
  }

  async handleRejectAdaptation(id) {
    showToast(`Rejecting recommendation ${id}...`, 'info');
    try {
      await api.rejectLearningRecommendation(id, 'OPERATOR_DISPATCHER', 'Operator dismissed adaptive adjustment');
      showToast(`Adaptive recommendation dismissed.`, 'success');
      await this.loadOptimizationData();
    } catch (err) {
      showToast(`Rejection failed: ${err.message}`, 'error');
    }
  }

  async handleWhatIfAdaptation(param, val) {
    showToast(`Evaluating A/B offline comparison for ${param}=${val}...`, 'info');
    try {
      const candidateObj = {};
      candidateObj[param] = parseFloat(val) || val;
      const comp = await api.comparePolicies(null, candidateObj);
      showToast(`A/B Impact: ${comp.projected_benefit} (Risk: ${comp.projected_risk})`, 'info');
    } catch (err) {
      showToast(`What-If failed: ${err.message}`, 'error');
    }
  }

  renderRecommendations() {
    if (!this.dom.listRecs) return;

    const filter = this.dom.selectFilterType?.value || 'ALL';
    const filtered = this.currentRecommendations.filter(r => {
      if (filter === 'ALL') return true;
      return r.decision_type === filter;
    });

    if (filtered.length === 0) {
      this.dom.listRecs.innerHTML = `
        <div style="padding: 20px; text-align: center; color: #64748b; font-size: 11px;">
          No active optimization recommendations match current filter.
        </div>
      `;
      return;
    }

    this.dom.listRecs.innerHTML = filtered.map(rec => {
      const isSelected = rec.recommendation_id === this.selectedRecId;
      const typeColor = rec.decision_type === 'FLEET_REPOSITION' ? '#38bdf8' : '#a855f7';
      const sevBadge =
        rec.severity === 'CRITICAL' ? '<span style="background: rgba(239, 68, 68, 0.2); color: #f87171; font-size: 9px; padding: 1px 4px; border-radius: 3px; font-weight: 700;">CRITICAL</span>' :
        rec.severity === 'WARNING' ? '<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; font-size: 9px; padding: 1px 4px; border-radius: 3px; font-weight: 700;">WARNING</span>' :
        '<span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; font-size: 9px; padding: 1px 4px; border-radius: 3px; font-weight: 700;">INFO</span>';

      const statusBadge =
        rec.status === 'EXECUTED' ? '<span style="background: #22c55e; color: #000; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 800;">EXECUTED</span>' :
        rec.status === 'REJECTED' ? '<span style="background: #ef4444; color: #fff; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 800;">REJECTED</span>' :
        rec.status === 'EXPIRED' ? '<span style="background: #64748b; color: #fff; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 800;">EXPIRED</span>' :
        rec.status === 'OBSOLETE' ? '<span style="background: #d97706; color: #fff; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 800;">OBSOLETE</span>' :
        '<span style="border: 1px solid #38bdf8; color: #38bdf8; font-size: 8px; padding: 1px 4px; border-radius: 2px; font-weight: 800;">NEW</span>';

      return `
        <div class="opt-rec-card ${isSelected ? 'selected' : ''}" data-id="${rec.recommendation_id}" style="
          padding: 8px 10px;
          border-bottom: 1px solid #334155;
          cursor: pointer;
          background: ${isSelected ? 'rgba(56, 189, 248, 0.08)' : 'transparent'};
          border-left: ${isSelected ? '3px solid #38bdf8' : '3px solid transparent'};
          transition: background 0.15s ease;
        ">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span style="font-size: 10px; font-weight: 800; color: ${typeColor};">${rec.decision_type}</span>
              ${sevBadge}
              ${statusBadge}
            </div>
            <span style="font-family: monospace; font-size: 11px; font-weight: 800; color: #22c55e;">
              Score: ${(rec.score * 100).toFixed(0)}%
            </span>
          </div>
          <div style="font-size: 11px; color: #cbd5e1; margin-bottom: 4px; line-height: 1.3;">
            ${rec.explanation.summary}
          </div>
          <div style="display: flex; justify-content: space-between; font-size: 9px; color: #64748b;">
            <span>ID: ${rec.recommendation_id}</span>
            <span>Expires: T+${rec.expires_at_sim_time}m</span>
          </div>
        </div>
      `;
    }).join('');

    this.dom.listRecs.querySelectorAll('.opt-rec-card').forEach(card => {
      card.addEventListener('click', () => {
        const id = card.getAttribute('data-id');
        this.selectRecommendation(id);
      });
    });
  }

  async selectRecommendation(recId) {
    this.selectedRecId = recId;
    this.renderRecommendations();

    const rec = this.currentRecommendations.find(r => r.recommendation_id === recId);
    if (!rec || !this.dom.detailContainer) return;

    // Load policy evaluation for this recommendation
    try {
      this.currentPolicyEval = await api.evaluatePolicy(recId);
    } catch (e) {
      this.currentPolicyEval = null;
    }

    const exp = rec.explanation;
    const isTerminal = ['EXECUTED', 'REJECTED', 'EXPIRED', 'OBSOLETE', 'FAILED'].includes(rec.status);

    const policyBadge = this.currentPolicyEval ? (
      this.currentPolicyEval.policy_decision === 'AUTO_APPROVE'
        ? `<span style="background: rgba(34, 197, 94, 0.2); border: 1px solid #22c55e; color: #86efac; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 800;">AUTO-APPROVE ELIGIBLE</span>`
        : this.currentPolicyEval.policy_decision === 'REQUIRE_OPERATOR'
        ? `<span style="background: rgba(56, 189, 248, 0.2); border: 1px solid #38bdf8; color: #38bdf8; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 800;">REQUIRES OPERATOR</span>`
        : `<span style="background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; color: #f87171; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 800;">POLICY DENIED</span>`
    ) : '';

    this.dom.detailContainer.innerHTML = `
      <div style="display: flex; flex-direction: column; gap: 10px; height: 100%; overflow-y: auto; padding-right: 4px;">
        <!-- Header Card with Decision and Actions -->
        <div style="
          background: rgba(15, 23, 42, 0.6);
          border: 1px solid #334155;
          border-radius: 6px;
          padding: 12px;
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
        ">
          <div>
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
              <span style="font-size: 13px; font-weight: 800; color: #38bdf8;">${rec.decision_type}</span>
              <span style="font-size: 11px; color: #94a3b8; font-family: monospace;">(${rec.recommendation_id})</span>
              ${policyBadge}
            </div>
            <div style="font-size: 13px; font-weight: 700; color: #f8fafc; margin-bottom: 4px;">
              ${exp.summary}
            </div>
            <div style="font-size: 11px; color: #86efac;">
              🎯 Expected Benefit: ${exp.expected_benefit}
            </div>
          </div>
          <div style="display: flex; flex-direction: column; gap: 6px; align-items: flex-end;">
            <div style="display: flex; gap: 6px;">
              <button id="btn-simulate-rec" class="btn-tactical btn-xs" style="background: #4f46e5; color: #fff;">
                <i data-lucide="flask-conical"></i> What-If
              </button>
              <button id="btn-approve-rec" class="btn-tactical btn-xs" ${isTerminal ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : 'style="background: #16a34a; color: #fff; font-weight: 800;"'}>
                <i data-lucide="check-circle-2"></i> Approve & Apply
              </button>
              <button id="btn-reject-rec" class="btn-tactical btn-xs" ${isTerminal ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : 'style="background: #dc2626; color: #fff;"'}>
                <i data-lucide="x-circle"></i> Reject
              </button>
            </div>
            <div style="font-size: 10px; color: #64748b;">
              Status: <span style="font-weight: 700; color: ${isTerminal ? '#f59e0b' : '#38bdf8'};">${rec.status}</span>
            </div>
          </div>
        </div>

        <!-- Policy Decision Guardrails Card -->
        ${this.currentPolicyEval ? `
          <div style="
            background: rgba(30, 41, 59, 0.6);
            border: 1px solid #475569;
            border-radius: 6px;
            padding: 10px 12px;
          ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
              <span style="font-size: 11px; font-weight: 800; color: #38bdf8;">Adaptive Policy Decision & Guardrails</span>
              <span style="font-size: 10px; color: #94a3b8; font-family: monospace;">Confidence: ${(this.currentPolicyEval.confidence * 100).toFixed(0)}% (Threshold: ${(this.currentPolicyEval.confidence_threshold * 100).toFixed(0)}%)</span>
            </div>
            <div style="font-size: 11px; color: #e2e8f0; line-height: 1.4; margin-bottom: 6px;">
              ${this.currentPolicyEval.reason}
            </div>
            ${this.currentPolicyEval.violations.length > 0 ? `
              <div style="font-size: 10px; color: #f87171;">
                Violations: ${this.currentPolicyEval.violations.join('; ')}
              </div>
            ` : `
              <div style="font-size: 10px; color: #86efac;">
                ✓ Passed all 12 operational safety guardrails
              </div>
            `}
          </div>
        ` : ''}

        <!-- 2 Columns: Reasons & Operational Risks -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
          <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid #334155; border-radius: 6px; padding: 10px;">
            <div style="font-size: 11px; font-weight: 800; color: #94a3b8; margin-bottom: 6px;">
              📋 Decision Rationale
            </div>
            <ul style="margin: 0; padding-left: 14px; font-size: 11px; color: #cbd5e1; line-height: 1.4;">
              ${exp.reasons.map(r => `<li>${r}</li>`).join('')}
            </ul>
          </div>
          <div style="background: rgba(15, 23, 42, 0.4); border: 1px solid #334155; border-radius: 6px; padding: 10px;">
            <div style="font-size: 11px; font-weight: 800; color: #f59e0b; margin-bottom: 6px;">
              ⚠️ Operational Risks
            </div>
            <ul style="margin: 0; padding-left: 14px; font-size: 11px; color: #fde68a; line-height: 1.4;">
              ${exp.risks.map(risk => `<li>${risk}</li>`).join('')}
            </ul>
          </div>
        </div>

        <!-- What-If Simulation Impact Box -->
        <div id="whatif-impact-box" style="
          background: rgba(30, 27, 75, 0.5);
          border: 1px dashed #6366f1;
          border-radius: 6px;
          padding: 10px;
        ">
          <div style="font-size: 11px; font-weight: 800; color: #c7d2fe; margin-bottom: 4px;">
            🧪 What-If Decision Simulation
          </div>
          <div id="whatif-result-text" style="font-size: 11px; color: #e0e7ff; line-height: 1.4;">
            ${rec.simulation_impact ? rec.simulation_impact.summary : 'Click "What-If" above to evaluate isolated impact on coverage, hospitals, and resilience.'}
          </div>
        </div>
      </div>
    `;

    if (window.lucide) window.lucide.createIcons();

    document.getElementById('btn-simulate-rec')?.addEventListener('click', () => {
      this.simulateCurrentRecommendation(rec.recommendation_id);
    });

    document.getElementById('btn-approve-rec')?.addEventListener('click', () => {
      this.approveCurrentRecommendation(rec.recommendation_id);
    });

    document.getElementById('btn-reject-rec')?.addEventListener('click', () => {
      this.rejectCurrentRecommendation(rec.recommendation_id);
    });
  }

  async simulateCurrentRecommendation(recId) {
    showToast(`Simulating what-if outcome for ${recId}...`, 'info');
    try {
      const impact = await api.simulateOptimizationRecommendation(recId);
      const rec = this.currentRecommendations.find(r => r.recommendation_id === recId);
      if (rec) {
        rec.simulation_impact = impact;
        rec.status = 'SIMULATED';
      }

      const impactBox = document.getElementById('whatif-result-text');
      if (impactBox) {
        impactBox.innerHTML = `
          <div style="font-weight: 700; color: #22c55e; margin-bottom: 4px;">✓ Simulation Completed (Net Positive: ${impact.is_better_than_baseline ? 'YES' : 'NO'})</div>
          <div>${impact.summary}</div>
          <div style="margin-top: 6px; display: flex; gap: 10px; font-size: 10px; font-family: monospace;">
            <span>Resilience: +${impact.resilience_impact} pts</span>
            <span>ETA Impact: ${impact.eta_impact_minutes > 0 ? '+' : ''}${impact.eta_impact_minutes}m</span>
          </div>
        `;
      }
      showToast('What-if decision simulation complete.', 'success');
      this.renderRecommendations();
    } catch (err) {
      showToast(`Simulation failed: ${err.message}`, 'error');
    }
  }

  async approveCurrentRecommendation(recId) {
    showToast(`Submitting operator approval for ${recId}...`, 'info');
    try {
      const res = await api.approveOptimizationRecommendation(recId, {
        operator_id: 'OPERATOR_DISPATCHER',
        operator_note: 'Approved via Operator Copilot workstation',
      });

      if (res.status === 'SUCCESS') {
        showToast(`Successfully executed ${res.decision_type} (ID: ${res.execution_id})`, 'success');
      } else {
        showToast(`Execution completed with status: ${res.status} (${res.error_message || ''})`, 'warning');
      }

      await this.loadOptimizationData();
      if (this.selectedRecId) this.selectRecommendation(this.selectedRecId);
    } catch (err) {
      showToast(`Approval failed: ${err.message}`, 'error');
    }
  }

  async rejectCurrentRecommendation(recId) {
    showToast(`Dismissing recommendation ${recId}...`, 'info');
    try {
      await api.rejectOptimizationRecommendation(recId, {
        operator_id: 'OPERATOR_DISPATCHER',
        reason: 'Operator dismissed via Copilot workstation',
      });
      showToast(`Recommendation ${recId} dismissed.`, 'success');
      await this.loadOptimizationData();
      if (this.selectedRecId) this.selectRecommendation(this.selectedRecId);
    } catch (err) {
      showToast(`Rejection failed: ${err.message}`, 'error');
    }
  }
}
