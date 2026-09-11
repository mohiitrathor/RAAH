/**
 * RAAH Operational Replay Controller (M10 Phase 3)
 * ================================================
 *
 * Controls deterministic replay playback, timeline scrubbing,
 * observational map rendering, and deep event inspection.
 *
 * STRICT INVARIANT:
 * Purely observational. Never touches live Simulator or DispatchState.
 */

import * as api from '../api.js';

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function isOperationalRun(runId) {
  return typeof runId === 'string' && /^run_\d+$/.test(runId);
}

export class ReplayController {
  constructor() {
    this.activeRunId = null;
    this.currentSimTime = 0;
    this.maxSimTime = 15;
    this.isPlaying = false;
    this.playTimer = null;
    this.playbackSpeed = 1.0; // multiplier: 1x, 2x, 5x, etc.
    this.timelineEvents = [];
    this.selectedEventIndex = null;

    // Leaflet map instance for replay
    this.replayMap = null;
    this.ambulanceMarkers = {};
    this.hospitalMarkers = {};
    this.incidentMarkers = {};
    this.routePolylines = {};

    this.dom = {
      selectReplay: document.getElementById('select-replay-run'),
      btnPlay: document.getElementById('btn-replay-play'),
      btnPause: document.getElementById('btn-replay-pause'),
      btnStepBack: document.getElementById('btn-replay-step-back'),
      btnStepForward: document.getElementById('btn-replay-step-forward'),
      btnJumpStart: document.getElementById('btn-replay-jump-start'),
      btnJumpEnd: document.getElementById('btn-replay-jump-end'),
      sliderSeek: document.getElementById('slider-replay-seek'),
      selectSpeed: document.getElementById('select-replay-speed'),
      clockDisplay: document.getElementById('replay-clock-display'),
      modeBanner: document.getElementById('replay-mode-banner'),
      timelineList: document.getElementById('replay-timeline-list'),
      eventInspector: document.getElementById('replay-event-inspector'),
      selectFilterType: document.getElementById('select-replay-filter-type'),
      inputFilterEntity: document.getElementById('input-replay-filter-entity'),
    };
  }

  init() {
    if (!this.dom.selectReplay) return;

    this.bindEvents();
    this.initReplayMap();
    this.refreshReplaysList();
  }

  initReplayMap() {
    const container = document.getElementById('replay-leaflet-map');
    if (!container || !window.L || this.replayMap) return;

    // Center on Jaipur
    this.replayMap = window.L.map('replay-leaflet-map', {
      center: [26.9124, 75.7873],
      zoom: 12,
      attributionControl: true,
    });

    // High-readability tactical base tiles (public raster service with attribution, no credentials required)
    window.L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 18,
      maxNativeZoom: 16,
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
    }).addTo(this.replayMap);
  }

  bindEvents() {
    this.dom.selectReplay?.addEventListener('change', (e) => {
      this.loadReplay(e.target.value);
    });

    this.dom.btnPlay?.addEventListener('click', () => this.play());
    this.dom.btnPause?.addEventListener('click', () => this.pause());
    this.dom.btnStepForward?.addEventListener('click', () => this.step(1));
    this.dom.btnStepBack?.addEventListener('click', () => this.step(-1));
    this.dom.btnJumpStart?.addEventListener('click', () => this.seek(0));
    this.dom.btnJumpEnd?.addEventListener('click', () => this.seek(this.maxSimTime));

    this.dom.sliderSeek?.addEventListener('input', (e) => {
      this.seek(parseInt(e.target.value, 10));
    });

    this.dom.selectSpeed?.addEventListener('change', (e) => {
      this.playbackSpeed = parseFloat(e.target.value);
      if (this.isPlaying) {
        this.pause();
        this.play();
      }
    });

    this.dom.selectFilterType?.addEventListener('change', () => {
      this.loadTimeline();
    });

    this.dom.inputFilterEntity?.addEventListener('input', () => {
      this.loadTimeline();
    });
  }

  async refreshReplaysList() {
    try {
      const replays = await api.getReplays();
      if (!this.dom.selectReplay) return;

      this.dom.selectReplay.innerHTML = '';
      if (!replays || replays.length === 0) {
        this.dom.selectReplay.innerHTML = '<option value="">No recorded replays found</option>';
        return;
      }

      replays.forEach((r, idx) => {
        const opt = document.createElement('option');
        opt.value = r.run_id;
        const isOp = isOperationalRun(r.run_id) || (r.scenario_id && r.scenario_id.startsWith('OPERATIONAL_RUN_'));
        const typeLabel = isOp ? '[HISTORICAL OPERATIONAL RUN]' : '[SCENARIO REPLAY]';
        const duration = r.end_sim_time !== undefined ? `${r.end_sim_time}m` : (r.duration_minutes !== undefined ? `${r.duration_minutes}m` : 'N/A');
        const evCount = r.event_count !== undefined ? `${r.event_count} ev` : (r.total_events !== undefined ? `${r.total_events} ev` : 'N/A');
        opt.textContent = `${typeLabel} ${r.scenario_id || r.run_id} (${r.run_id}) - ${duration} (${evCount})`;
        if (idx === 0) opt.selected = true;
        this.dom.selectReplay.appendChild(opt);
      });

      if (replays.length > 0) {
        this.loadReplay(replays[0].run_id);
      }
    } catch (err) {
      console.warn('Failed to load replays list:', err);
      if (this.dom.selectReplay) {
        this.dom.selectReplay.innerHTML = '<option value="">Failed to load replays (check authorization or server)</option>';
      }
    }
  }

  async loadReplay(runId) {
    if (!runId) return;
    this.pause();
    this.activeRunId = runId;
    this.isOperational = isOperationalRun(runId);
    this.currentSimTime = 0;

    try {
      let duration = 15;
      try {
        const analysis = await api.getReplayAnalysis(runId);
        duration = analysis.duration || 15;
      } catch (analysisErr) {
        console.warn('Could not fetch replay analysis, seeking t=0 directly:', analysisErr);
      }
      this.maxSimTime = duration;
      if (this.dom.sliderSeek) {
        this.dom.sliderSeek.max = this.maxSimTime;
        this.dom.sliderSeek.value = 0;
      }
      await this.loadTimeline();
      await this.seek(0);
      this.updateHeaderUI();
    } catch (err) {
      console.error('Error loading replay:', err);
      if (this.dom.modeBanner) {
        this.dom.modeBanner.innerHTML = `
          <span style="background: #ef4444; color: white; padding: 2px 6px; border-radius: 3px; font-weight: 800; font-size: 10px;">REPLAY ERROR</span>
          <span style="color: #f87171; font-size: 11px;">Failed to load replay ${escapeHtml(runId)}. Data may be missing or corrupt.</span>
        `;
      }
    }
  }

  async loadTimeline() {
    if (!this.activeRunId) return;
    const filterType = this.dom.selectFilterType?.value || null;
    const filterEntity = this.dom.inputFilterEntity?.value?.trim() || null;

    try {
      const data = await api.getReplayTimeline(
        this.activeRunId,
        filterType === 'ALL' ? null : filterType,
        filterEntity || null
      );
      this.timelineEvents = data.events || [];
      this.renderTimeline();
    } catch (err) {
      console.warn('Failed to load timeline:', err);
      if (this.dom.timelineList) {
        this.dom.timelineList.innerHTML = '<div style="padding: 10px; color: #f87171; font-size: 11px;">Failed to load timeline.</div>';
      }
    }
  }

  renderTimeline() {
    if (!this.dom.timelineList) return;
    this.dom.timelineList.innerHTML = '';

    if (this.timelineEvents.length === 0) {
      this.dom.timelineList.innerHTML = '<div style="padding: 10px; color: #64748b; font-size: 11px;">No matching events recorded.</div>';
      return;
    }

    this.timelineEvents.forEach((ev) => {
      const item = document.createElement('div');
      item.className = 'timeline-event-item';
      item.style.cssText = `
        padding: 6px 8px; border-bottom: 1px solid #1e293b; font-size: 11px; cursor: pointer;
        display: flex; gap: 6px; align-items: center; transition: background 0.15s;
      `;
      if (ev.sim_time === this.currentSimTime) {
        item.style.background = 'rgba(56, 189, 248, 0.15)';
      }

      const badgeColor =
        ev.event_type === 'MCI_DECLARED' ? '#ef4444' :
        ev.event_type === 'DISPATCH' ? '#38bdf8' :
        ev.event_type === 'AMBULANCE_ARRIVED' ? '#22c55e' :
        ev.event_type === 'REDIRECTION' ? '#f59e0b' :
        ev.event_type === 'REPOSITION_START' ? '#c084fc' :
        ev.event_type === 'HOSPITAL_SATURATED' ? '#f43f5e' : '#94a3b8';

      item.innerHTML = `
        <span style="font-family: monospace; color: #94a3b8; font-size: 10px; min-width: 42px;">T+${escapeHtml(ev.sim_time)}m</span>
        <span style="background: ${badgeColor}; color: #000; font-weight: 700; font-size: 9px; padding: 1px 4px; border-radius: 3px;">
          ${escapeHtml(ev.event_type ? ev.event_type.slice(0, 12) : 'EVENT')}
        </span>
        <span style="color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
          ${escapeHtml(ev.description || 'Recorded telemetry event')}
        </span>
      `;

      item.addEventListener('click', () => {
        this.seek(ev.sim_time);
        this.inspectEvent(ev.event_index);
      });

      this.dom.timelineList.appendChild(item);
    });
  }

  async inspectEvent(eventIndex) {
    if (!this.activeRunId) return;
    this.selectedEventIndex = eventIndex;

    try {
      const ev = await api.getReplayEventDetail(this.activeRunId, eventIndex);
      if (!this.dom.eventInspector) return;

      const isOp = isOperationalRun(this.activeRunId);
      const evidenceDisclaimer = isOp
        ? `<div style="margin-top: 8px; padding: 6px 8px; background: rgba(30, 41, 59, 0.6); border-left: 3px solid #64748b; border-radius: 4px; font-size: 10px; color: #94a3b8;">
             <span style="font-weight: 600; color: #cbd5e1;">Decision Evidence:</span> Decision evidence was not recorded for this historical run.
           </div>`
        : '';

      this.dom.eventInspector.innerHTML = `
        <div style="background: rgba(15, 23, 42, 0.95); border: 1px solid #334155; border-radius: 6px; padding: 10px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #334155; padding-bottom: 4px; margin-bottom: 6px;">
            <span style="font-weight: 700; color: #38bdf8;">${escapeHtml(ev.event_type)}</span>
            <span style="color: #94a3b8; font-family: monospace;">T+${escapeHtml(ev.sim_time)} min</span>
          </div>
          <div style="color: #f1f5f9; margin-bottom: 8px;">${escapeHtml(ev.description)}</div>
          <div style="color: #94a3b8; font-size: 10px; margin-bottom: 4px; text-transform: uppercase;">Affected Entities</div>
          <pre style="background: #090d16; padding: 6px; border-radius: 4px; color: #a5f3fc; font-size: 10px; margin: 0 0 6px 0; overflow-x: auto;">${escapeHtml(JSON.stringify(ev.entity_ids || {}, null, 2))}</pre>
          <div style="color: #94a3b8; font-size: 10px; margin-bottom: 4px; text-transform: uppercase;">Specialized Detail</div>
          <pre style="background: #090d16; padding: 6px; border-radius: 4px; color: #fde047; font-size: 10px; margin: 0; overflow-x: auto;">${escapeHtml(JSON.stringify(ev.detail || {}, null, 2))}</pre>
          ${evidenceDisclaimer}
        </div>
      `;
    } catch (err) {
      console.warn('Failed to inspect event:', err);
      if (this.dom.eventInspector) {
        this.dom.eventInspector.innerHTML = `
          <div style="padding: 10px; color: #f87171; font-size: 11px;">
            Failed to load event details.
          </div>
        `;
      }
    }
  }

  async seek(targetSimTime) {
    if (!this.activeRunId) return;
    const t = Math.max(0, Math.min(this.maxSimTime, targetSimTime));
    this.currentSimTime = t;

    if (this.dom.sliderSeek) {
      this.dom.sliderSeek.value = t;
    }

    try {
      const state = await api.seekReplayState(this.activeRunId, t);
      this.renderMapState(state);
      this.updateHeaderUI();
      this.renderTimeline();
    } catch (err) {
      console.error('Error seeking replay:', err);
    }
  }

  async step(delta) {
    this.pause();
    if (delta > 0 && this.activeRunId) {
      try {
        const state = await api.stepReplay(this.activeRunId);
        if (state && state.sim_time !== undefined) {
          this.currentSimTime = state.sim_time;
          if (this.dom.sliderSeek) {
            this.dom.sliderSeek.value = state.sim_time;
          }
          this.renderMapState(state);
          this.updateHeaderUI();
          this.renderTimeline();
          return;
        }
      } catch (err) {
        console.warn('Server step failed, falling back to seek:', err);
      }
    }
    this.seek(this.currentSimTime + delta);
  }

  play() {
    if (this.isPlaying || !this.activeRunId) return;
    this.isPlaying = true;
    if (this.dom.btnPlay) this.dom.btnPlay.disabled = true;
    if (this.dom.btnPause) this.dom.btnPause.disabled = false;

    const intervalMs = Math.max(200, 1000 / this.playbackSpeed);
    this.playTimer = setInterval(() => {
      if (this.currentSimTime >= this.maxSimTime) {
        this.pause();
        return;
      }
      this.seek(this.currentSimTime + 1);
    }, intervalMs);

    this.updateHeaderUI();
  }

  pause() {
    this.isPlaying = false;
    if (this.playTimer) {
      clearInterval(this.playTimer);
      this.playTimer = null;
    }
    if (this.dom.btnPlay) this.dom.btnPlay.disabled = false;
    if (this.dom.btnPause) this.dom.btnPause.disabled = true;
    this.updateHeaderUI();
  }

  updateHeaderUI() {
    if (this.dom.clockDisplay) {
      this.dom.clockDisplay.textContent = `T+${this.currentSimTime}m / T+${this.maxSimTime}m`;
    }
    if (this.dom.modeBanner) {
      const statusText = this.isPlaying ? 'PLAYING' : 'PAUSED';
      const isOp = isOperationalRun(this.activeRunId);
      const modeLabel = isOp ? 'HISTORICAL REPLAY' : 'SCENARIO REPLAY';
      const badgeBg = isOp ? '#0284c7' : '#e11d48';
      this.dom.modeBanner.innerHTML = `
        <span style="background: ${badgeBg}; color: white; padding: 2px 6px; border-radius: 3px; font-weight: 800; font-size: 10px;">${escapeHtml(modeLabel)}</span>
        <span style="color: #94a3b8; font-size: 11px;">Run: <b style="color: #f1f5f9;">${escapeHtml((this.activeRunId || 'None').slice(0, 16))}</b> | Time: <b style="color: #38bdf8;">T+${this.currentSimTime}m</b> | Status: <b>${statusText}</b></span>
      `;
    }
  }

  renderMapState(state) {
    if (!this.replayMap || !window.L) return;

    // Clear previous dynamic layers
    Object.values(this.ambulanceMarkers).forEach(m => m.remove());
    Object.values(this.incidentMarkers).forEach(m => m.remove());
    Object.values(this.hospitalMarkers).forEach(m => m.remove());
    Object.values(this.routePolylines).forEach(p => p.remove());

    this.ambulanceMarkers = {};
    this.incidentMarkers = {};
    this.hospitalMarkers = {};
    this.routePolylines = {};

    // 1. Ambulances & Waypoints
    (state.ambulances || []).forEach((amb) => {
      const lat = amb.latitude || 26.9124;
      const lon = amb.longitude || 75.7873;
      const status = amb.status || 'AVAILABLE';

      let markerColor = '#22c55e';
      if (status === 'EN_ROUTE') markerColor = '#38bdf8';
      else if (status === 'REPOSITIONING') markerColor = '#c084fc';
      else if (status === 'ARRIVED') markerColor = '#eab308';

      const icon = window.L.divIcon({
        className: 'replay-amb-icon',
        html: `<div style="background: ${markerColor}; width: 10px; height: 10px; border-radius: 50%; border: 2px solid #fff;"></div>`,
        iconSize: [14, 14],
      });

      const m = window.L.marker([lat, lon], { icon }).addTo(this.replayMap);
      m.bindPopup(`<b>${amb.ambulance_id}</b> (${status})<br>ETA: ${amb.eta_minutes || 0}m`);
      this.ambulanceMarkers[amb.ambulance_id] = m;

      // Render waypoints if en-route
      const waypoints = amb.route_waypoints || [];
      if (waypoints.length > 1) {
        const poly = window.L.polyline(waypoints, {
          color: markerColor,
          weight: 3,
          opacity: 0.7,
          dashArray: status === 'REPOSITIONING' ? '4, 4' : null,
        }).addTo(this.replayMap);
        this.routePolylines[amb.ambulance_id] = poly;
      }
    });

    // 2. Monitored Hospitals
    (state.hospitals || []).forEach((hosp) => {
      const lat = hosp.latitude;
      const lon = hosp.longitude;
      if (!lat || !lon) return;

      const isFull = hosp.is_full || (hosp.available_beds <= 0);
      const hColor = isFull ? '#ef4444' : '#10b981';

      const hIcon = window.L.divIcon({
        className: 'replay-hosp-icon',
        html: `<div style="background: ${hColor}; width: 8px; height: 8px; transform: rotate(45deg); border: 1px solid #fff;"></div>`,
        iconSize: [12, 12],
      });

      const hm = window.L.marker([lat, lon], { icon: hIcon }).addTo(this.replayMap);
      hm.bindPopup(`<b>${hosp.name || hosp.hospital_id}</b><br>Load: ${hosp.current_load}/${hosp.capacity}<br>Beds: ${hosp.available_beds} | ICU: ${hosp.available_icu || 0}`);
      this.hospitalMarkers[hosp.hospital_id] = hm;
    });

    // 3. Active Incidents & MCIs
    (state.incidents || []).forEach((inc) => {
      const lat = inc.latitude || inc.patient_lat;
      const lon = inc.longitude || inc.patient_lon;
      if (!lat || !lon) return;

      const iIcon = window.L.divIcon({
        className: 'replay-inc-icon',
        html: `<div style="background: #f43f5e; width: 8px; height: 8px; border-radius: 50%; border: 1px solid #000;"></div>`,
        iconSize: [10, 10],
      });

      const im = window.L.marker([lat, lon], { icon: iIcon }).addTo(this.replayMap);
      im.bindPopup(`<b>Incident #${inc.incident_id}</b> (${inc.priority || 'P?'})<br>Status: ${inc.status}`);
      this.incidentMarkers[inc.incident_id] = im;
    });
  }
}
