/**
 * RAAH External Integrations & Telemetry Ingestion Health Controller (M13.5 Phase 1)
 * =================================================================================
 *
 * Provides real-time operator observability into external provider telemetry feeds
 * (CAD, AVL/GPS, Hospital Capacity, Traffic) and ingestion metrics (throughput,
 * deduplication, staleness, errors, latency).
 *
 * Invariants:
 * - Read-only observational cockpit; strictly zero mutation of DispatchState.
 * - Conservative refresh interval (12 seconds); never 1-second polling.
 * - Text sanitization on all dynamic strings (XSS defense).
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

export class IntegrationController {
  constructor() {
    this.refreshIntervalMs = 12000; // 12s conservative monitoring refresh
    this.timer = null;
    this.isLoading = false;

    this.dom = {
      workspace: document.getElementById('integrations-workspace'),
      btnNavIntegrations: document.getElementById('nav-btn-integrations'),
      tickerPill: document.getElementById('integrations-status-pill'),
      tickerText: document.getElementById('integrations-status-text'),
      btnRefresh: document.getElementById('btn-refresh-integrations'),
      txtLastUpdated: document.getElementById('integrations-last-updated'),

      // Summary KPIs
      kpiTotalIngested: document.getElementById('int-kpi-total-ingested'),
      kpiTotalAccepted: document.getElementById('int-kpi-total-accepted'),
      kpiTotalDuplicates: document.getElementById('int-kpi-total-duplicates'),
      kpiTotalRejected: document.getElementById('int-kpi-total-rejected'),
      kpiMeanLatency: document.getElementById('int-kpi-mean-latency'),

      // Provider Card Containers
      cardCad: document.getElementById('card-integration-cad'),
      cardGps: document.getElementById('card-integration-gps'),
      cardHospital: document.getElementById('card-integration-hospital'),
      cardTraffic: document.getElementById('card-integration-traffic'),
    };
  }

  init() {
    this.bindEvents();
    this.loadStatus();
    this.startPolling();
  }

  bindEvents() {
    this.dom.btnRefresh?.addEventListener('click', () => {
      this.loadStatus(true);
    });

    this.dom.tickerPill?.addEventListener('click', () => {
      this.dom.btnNavIntegrations?.click();
    });
  }

  startPolling() {
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => {
      // Conservative background refresh
      this.loadStatus(false);
    }, this.refreshIntervalMs);
  }

  stopPolling() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  async loadStatus(showManualFeedback = false) {
    if (this.isLoading) return;
    this.isLoading = true;

    if (showManualFeedback && this.dom.btnRefresh) {
      this.dom.btnRefresh.disabled = true;
      this.dom.btnRefresh.innerHTML = '<i data-lucide="rotate-cw" class="animate-spin"></i> Probing...';
      if (window.lucide) window.lucide.createIcons();
    }

    try {
      const data = await api.getIngestionStatus();
      this.render(data);
      if (showManualFeedback) {
        showToast('Integration Status', 'Probed all external adapter endpoints successfully.', 'success');
      }
    } catch (err) {
      console.warn('[Integrations] Status probe failed:', err);
      this.renderError(err.message);
      if (showManualFeedback) {
        showToast('Probe Failed', `Failed to retrieve integration health: ${err.message}`, 'error');
      }
    } finally {
      this.isLoading = false;
      if (showManualFeedback && this.dom.btnRefresh) {
        this.dom.btnRefresh.disabled = false;
        this.dom.btnRefresh.innerHTML = '<i data-lucide="rotate-cw"></i> Probe Feeds';
        if (window.lucide) window.lucide.createIcons();
      }
    }
  }

  render(data) {
    const metrics = data.metrics || {};
    const adapters = data.adapters || {};
    const providers = adapters.providers || {};
    const overallHealthy = adapters.healthy === true;

    // 1. Update Header Ticker Status Pill
    if (this.dom.tickerPill && this.dom.tickerText) {
      this.dom.tickerPill.className = 'status-pill ' + (overallHealthy ? 'healthy' : 'warning');
      this.dom.tickerText.textContent = overallHealthy ? 'INTEGRATIONS: OK' : 'INTEGRATIONS: DEGRADED';
    }

    // 2. Update Timestamp
    if (this.dom.txtLastUpdated) {
      const ts = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
      this.dom.txtLastUpdated.textContent = `Last Probed: ${ts}`;
    }

    // 3. Update Summary KPIs
    if (this.dom.kpiTotalIngested) this.dom.kpiTotalIngested.textContent = (metrics.total_ingested || 0).toLocaleString();
    if (this.dom.kpiTotalAccepted) this.dom.kpiTotalAccepted.textContent = (metrics.total_accepted || 0).toLocaleString();
    if (this.dom.kpiTotalDuplicates) this.dom.kpiTotalDuplicates.textContent = (metrics.total_duplicates || 0).toLocaleString();
    if (this.dom.kpiTotalRejected) {
      const rejected = (metrics.total_rejected || 0) + (metrics.total_stale || 0);
      this.dom.kpiTotalRejected.textContent = rejected.toLocaleString();
    }
    if (this.dom.kpiMeanLatency) {
      this.dom.kpiMeanLatency.textContent = `${metrics.mean_latency_ms || 0.0}ms`;
    }

    const byEvent = metrics.by_event_type || {};

    // 4. Render 4 Provider Cards
    this.renderCard(
      this.dom.cardCad,
      'Computer-Aided Dispatch (CAD)',
      'phone-call',
      providers.cad || {},
      byEvent.INCIDENT_CALL || {},
      'Emergency Call Ingestion'
    );

    this.renderCard(
      this.dom.cardGps,
      'Automatic Vehicle Location (AVL / GPS)',
      'navigation',
      providers.gps || {},
      byEvent.AMBULANCE_GPS || {},
      'Fleet Telemetry & Kinematics'
    );

    this.renderCard(
      this.dom.cardHospital,
      'Hospital Capacity Telemetry',
      'building-2',
      providers.hospital || {},
      byEvent.HOSPITAL_STATUS || {},
      'Emergency Department Bed & ICU Feed'
    );

    this.renderCard(
      this.dom.cardTraffic,
      'Real-Time Traffic Advisories',
      'traffic-cone',
      providers.traffic || {},
      byEvent.TRAFFIC_UPDATE || {},
      'Transit Congestion & Road Conditions'
    );

    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  renderCard(container, title, iconName, providerData, eventMetrics, subtitle) {
    if (!container) return;

    const rawStatus = providerData.status || (providerData.healthy ? 'HEALTHY' : 'DISCONNECTED');
    const status = String(rawStatus).toUpperCase();

    let badgeColor = '#ef4444';
    let badgeBg = 'rgba(239, 68, 68, 0.15)';
    let badgeBorder = '#b91c1c';

    if (status === 'HEALTHY') {
      badgeColor = '#10b981';
      badgeBg = 'rgba(16, 185, 129, 0.15)';
      badgeBorder = '#059669';
    } else if (status === 'DEGRADED') {
      badgeColor = '#f59e0b';
      badgeBg = 'rgba(245, 158, 11, 0.15)';
      badgeBorder = '#d97706';
    } else if (status === 'NOT_CONFIGURED') {
      badgeColor = '#94a3b8';
      badgeBg = 'rgba(148, 163, 184, 0.15)';
      badgeBorder = '#475569';
    }

    const providerId = providerData.provider_id || 'UNKNOWN';
    const providerType = providerData.type || 'TELEMETRY';
    const errorMsg = providerData.error ? escapeHtml(providerData.error) : null;

    // Diagnostics extraction
    const diagEntries = [];
    if (providerData.pending_count !== undefined) diagEntries.push(`Pending Queue: <strong>${providerData.pending_count}</strong>`);
    if (providerData.acknowledged_count !== undefined) diagEntries.push(`Acknowledged: <strong>${providerData.acknowledged_count}</strong>`);
    if (providerData.buffered_fixes !== undefined) diagEntries.push(`Buffered Fixes: <strong>${providerData.buffered_fixes}</strong>`);
    if (providerData.status_records !== undefined) diagEntries.push(`Status Records: <strong>${providerData.status_records}</strong>`);
    if (providerData.advisories_count !== undefined) diagEntries.push(`Advisories: <strong>${providerData.advisories_count}</strong>`);

    const acceptedCount = eventMetrics.accepted || 0;
    const dupCount = eventMetrics.duplicate || 0;
    const rejCount = (eventMetrics.rejected || 0) + (eventMetrics.stale || 0);
    const ingestedCount = eventMetrics.ingested || 0;

    container.innerHTML = `
      <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid #1e293b; border-radius: 8px; padding: 14px; display: flex; flex-direction: column; gap: 10px; height: 100%; box-sizing: border-box;">
        <!-- Card Header -->
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
          <div style="display: flex; align-items: center; gap: 8px;">
            <div style="width: 32px; height: 32px; border-radius: 6px; background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.2); display: flex; align-items: center; justify-content: center; color: #38bdf8;">
              <i data-lucide="${escapeHtml(iconName)}" style="width: 16px; height: 16px;"></i>
            </div>
            <div>
              <div style="font-weight: 700; color: #f1f5f9; font-size: 12px;">${escapeHtml(title)}</div>
              <div style="font-size: 10px; color: #64748b;">${escapeHtml(subtitle)}</div>
            </div>
          </div>
          <span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 10px; font-family: var(--font-mono, monospace); color: ${badgeColor}; background: ${badgeBg}; border: 1px solid ${badgeBorder};">
            <span style="width: 6px; height: 6px; border-radius: 50%; background: ${badgeColor};"></span>
            ${escapeHtml(status)}
          </span>
        </div>

        <!-- Provider Metadata -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 10px; padding: 6px 8px; background: rgba(30, 41, 59, 0.5); border-radius: 4px;">
          <div><span style="color: #64748b;">Provider ID:</span> <span style="color: #cbd5e1; font-family: var(--font-mono, monospace);">${escapeHtml(providerId)}</span></div>
          <div><span style="color: #64748b;">Protocol:</span> <span style="color: #cbd5e1;">${escapeHtml(providerType)}</span></div>
        </div>

        <!-- Ingestion Telemetry Numbers -->
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; text-align: center;">
          <div style="background: rgba(15, 23, 42, 0.5); padding: 6px; border-radius: 4px; border: 1px solid #1e293b;">
            <div style="font-size: 9px; color: #64748b;">Ingested</div>
            <div style="font-weight: 700; color: #38bdf8; font-size: 13px;">${ingestedCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.5); padding: 6px; border-radius: 4px; border: 1px solid #1e293b;">
            <div style="font-size: 9px; color: #64748b;">Accepted</div>
            <div style="font-weight: 700; color: #10b981; font-size: 13px;">${acceptedCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.5); padding: 6px; border-radius: 4px; border: 1px solid #1e293b;">
            <div style="font-size: 9px; color: #64748b;">Duplicates</div>
            <div style="font-weight: 700; color: #f59e0b; font-size: 13px;">${dupCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.5); padding: 6px; border-radius: 4px; border: 1px solid #1e293b;">
            <div style="font-size: 9px; color: #64748b;">Rejected</div>
            <div style="font-weight: 700; color: #ef4444; font-size: 13px;">${rejCount}</div>
          </div>
        </div>

        <!-- Diagnostics & Diagnostics Footer -->
        <div style="margin-top: auto; font-size: 10px; color: #94a3b8; border-top: 1px solid #1e293b; padding-top: 8px;">
          ${errorMsg ? `
            <div style="color: #f87171; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; padding: 4px 6px; margin-bottom: 4px;">
              ⚠️ ${errorMsg}
            </div>
          ` : ''}
          <div style="display: flex; flex-wrap: wrap; gap: 8px;">
            ${diagEntries.length > 0 ? diagEntries.join(' &bull; ') : '<span>No provider diagnostics reported.</span>'}
          </div>
        </div>
      </div>
    `;
  }

  renderError(errorMsg) {
    if (this.dom.tickerPill && this.dom.tickerText) {
      this.dom.tickerPill.className = 'status-pill warning';
      this.dom.tickerText.textContent = 'INTEGRATIONS: UNREACHABLE';
    }
    if (this.dom.txtLastUpdated) {
      this.dom.txtLastUpdated.textContent = 'Probe Failed';
    }
  }
}
