/**
 * Event Console & Live Activity Feed Component
 * Renders chronological operational events, dispatches, redirections, and alerts.
 */

import { store } from '../state.js';
import * as api from '../api.js';
import { openEventScheduleModal } from './event_modal.js';
import { tacticalMap } from '../map.js';
import { openIncidentDetail } from './detail_drawer.js';

export function setupEvents() {
  const container = document.getElementById('event-feed-container');
  const btnSchedule = document.getElementById('btn-schedule-event');

  // --- Dynamic Event Injection ---
  if (btnSchedule) {
    btnSchedule.addEventListener('click', () => {
      openEventScheduleModal();
    });
  }

  // --- Render Activity & Event Feed ---
  store.subscribe((state, changedKeys) => {
    if (!changedKeys.includes('activityFeed') && !changedKeys.includes('events')) {
      return;
    }

    if (!container) return;

    // Prioritize rich activityFeed if populated, otherwise use legacy events
    const feed = state.activityFeed && state.activityFeed.length > 0 ? state.activityFeed : null;

    if (feed && feed.length > 0) {
      // Most recent first
      const reversed = feed.slice().reverse();
      container.innerHTML = reversed.map(item => {
        const badgeType = String(item.badge || item.type || 'INFO').toLowerCase();
        const timeLabel = item.time !== undefined && item.time !== null ? `T+${item.time}m` : 'LIVE';
        const incLink = item.incident_id ? `<span class="event-link" data-incident-id="${item.incident_id}">#${item.incident_id}</span>` : '';

        return `
          <div class="event-entry">
            <span class="event-time-pill">${timeLabel}</span>
            <span class="event-badge badge-${badgeType}">${item.badge || item.type || 'INFO'}</span>
            <span class="event-msg">${item.message} ${incLink}</span>
          </div>
        `;
      }).join('');

      // Attach click listeners for incident links
      container.querySelectorAll('.event-link').forEach(link => {
        link.addEventListener('click', (e) => {
          const incId = e.currentTarget.getAttribute('data-incident-id');
          if (incId) {
            store.selectIncident(incId);
            tacticalMap.focusIncident(incId);
            openIncidentDetail(incId);
          }
        });
      });
      return;
    }

    const events = state.events;
    if (!events || events.length === 0) {
      container.innerHTML = `<div class="empty-placeholder" style="padding: 10px;">Awaiting simulation events...</div>`;
      return;
    }

    // Display events (most recent first)
    const reversed = events.slice().reverse();

    container.innerHTML = reversed.map(ev => `
      <div class="event-entry">
        <span class="event-time-pill">[${ev.time}m]</span>
        <span class="event-badge badge-info">EVENT</span>
        <span class="event-msg">${ev.message}</span>
      </div>
    `).join('');
  });
}
