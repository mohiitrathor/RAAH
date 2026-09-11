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

    const status = providerData.status || (providerData.healthy ? 'HEALTHY' : 'DISCONNECTED');
    let statusText = status;
    let badgeColor = 'var(--status-danger)';
    let badgeBg = 'rgba(239, 68, 68, 0.15)';
    let badgeBorder = 'rgba(239, 68, 68, 0.3)';

    if (status === 'HEALTHY') {
      statusText = '✓ ONLINE / HEALTHY';
      badgeColor = 'var(--status-success)';
      badgeBg = 'rgba(16, 185, 129, 0.15)';
      badgeBorder = 'rgba(16, 185, 129, 0.3)';
    } else if (status === 'DEGRADED') {
      statusText = '⚠️ DEGRADED / ELEVATED LATENCY';
      badgeColor = 'var(--status-warning)';
      badgeBg = 'rgba(245, 158, 11, 0.15)';
      badgeBorder = 'rgba(245, 158, 11, 0.3)';
    } else if (status === 'DISCONNECTED') {
      statusText = '⊘ DISCONNECTED / NO FEED';
      badgeColor = 'var(--status-danger)';
      badgeBg = 'rgba(239, 68, 68, 0.15)';
      badgeBorder = 'rgba(239, 68, 68, 0.3)';
    } else if (status === 'NOT_CONFIGURED') {
      statusText = '— NOT CONFIGURED';
      badgeColor = 'var(--text-muted)';
      badgeBg = 'rgba(148, 163, 184, 0.1)';
      badgeBorder = 'rgba(148, 163, 184, 0.25)';
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
      <div class="integration-provider-row" style="background: var(--surface-panel); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: 14px 18px; display: grid; grid-template-columns: 280px 180px 1fr 280px; align-items: center; gap: 16px;">
        <!-- Col 1: Identity & Channel -->
        <div style="display: flex; align-items: center; gap: 12px; min-width: 0;">
          <div style="width: 36px; height: 36px; border-radius: var(--radius-sm); background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.25); display: flex; align-items: center; justify-content: center; color: var(--accent-cyan); flex-shrink: 0;">
            <i data-lucide="${escapeHtml(iconName)}" style="width: 18px; height: 18px;"></i>
          </div>
          <div style="min-width: 0;">
            <div style="font-weight: 700; color: var(--text-primary); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(title)}</div>
            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;">${escapeHtml(subtitle)}</div>
            <div style="font-size: 9px; font-family: var(--font-mono); color: #64748b; margin-top: 3px;">
              ID: <span style="color: #94a3b8;">${escapeHtml(providerId)}</span> &bull; TYPE: <span style="color: #94a3b8;">${escapeHtml(providerType)}</span>
            </div>
          </div>
        </div>

        <!-- Col 2: Operational Health -->
        <div>
          <span class="provider-status-badge" style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 2px; font-weight: 800; font-size: 10px; font-family: var(--font-mono); color: ${badgeColor}; background: ${badgeBg}; border: 1px solid ${badgeBorder}; white-space: nowrap;">
            ${escapeHtml(statusText)}
          </span>
          <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px; font-family: var(--font-mono);">
            Channel: Duplex Stream
          </div>
        </div>

        <!-- Col 3: Throughput Matrix -->
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; text-align: center;">
          <div style="background: rgba(15, 23, 42, 0.6); padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid rgba(255, 255, 255, 0.05);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; font-weight: 700;">Ingested</div>
            <div style="font-weight: 800; color: var(--accent-cyan); font-size: 14px; font-family: var(--font-mono); font-variant-numeric: tabular-nums;">${ingestedCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.6); padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid rgba(255, 255, 255, 0.05);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; font-weight: 700;">Accepted</div>
            <div style="font-weight: 800; color: var(--success-emerald); font-size: 14px; font-family: var(--font-mono); font-variant-numeric: tabular-nums;">${acceptedCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.6); padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid rgba(255, 255, 255, 0.05);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; font-weight: 700;">Duplicates</div>
            <div style="font-weight: 800; color: var(--warning-amber); font-size: 14px; font-family: var(--font-mono); font-variant-numeric: tabular-nums;">${dupCount}</div>
          </div>
          <div style="background: rgba(15, 23, 42, 0.6); padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid rgba(255, 255, 255, 0.05);">
            <div style="font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b; font-weight: 700;">Rejected</div>
            <div style="font-weight: 800; color: var(--p1-critical); font-size: 14px; font-family: var(--font-mono); font-variant-numeric: tabular-nums;">${rejCount}</div>
          </div>
        </div>

        <!-- Col 4: Diagnostics & Buffer Metrics -->
        <div style="font-size: 10px; color: var(--text-secondary); font-family: var(--font-mono); display: flex; flex-direction: column; gap: 4px;">
          ${errorMsg ? `
            <div style="color: #f87171; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 2px; padding: 2px 6px;">
              ⚠️ ${errorMsg}
            </div>
          ` : ''}
          <div style="display: flex; flex-direction: column; gap: 2px;">
            ${diagEntries.length > 0 ? diagEntries.map(d => `<div>&bull; ${d}</div>`).join('') : '<div style="color: var(--text-muted);">No provider buffer alerts.</div>'}
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
