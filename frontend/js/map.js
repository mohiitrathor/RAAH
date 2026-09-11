/**
 * RAAH Tactical Leaflet Map Controller
 * Implements tiered situational-awareness rendering for Jaipur EMS.
 */

import { store } from './state.js';
import * as api from './api.js';
import { openIncidentDetail } from './components/detail_drawer.js';
import { showToast } from './components/toasts.js';

class TacticalMap {
  constructor() {
    this.map = null;
    this.hospitalsLayer = null;
    this.incidentsLayer = null;
    this.enRouteAmbulancesLayer = null;
    this.routePolylinesLayer = null;
    this.canvasIdleLayer = null;

    this.hospitalMarkers = new Map(); // id -> { marker, isSaturated }
    this.activeAmbulanceMarkers = new Map(); // id -> L.marker
    this.incidentMarkers = new Map(); // id -> L.marker
    this.routeLines = new Map(); // incident_id -> L.polyline
  }

  initialize(elementId = 'leaflet-map') {
    if (this.map) return;

    // Jaipur City Center Coordinates
    const JAIPUR_CENTER = [26.9124, 75.7873];

    this.map = L.map(elementId, {
      center: JAIPUR_CENTER,
      zoom: 12,
      zoomControl: true,
      attributionControl: true,
    });

    // High-readability tactical base tiles (public raster service with attribution, no credentials required)
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 18,
      maxNativeZoom: 16,
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
    }).addTo(this.map);

    // Layer groups for clean management
    this.hospitalsLayer = L.layerGroup().addTo(this.map);
    this.routePolylinesLayer = L.layerGroup().addTo(this.map);
    this.incidentsLayer = L.layerGroup().addTo(this.map);
    this.enRouteAmbulancesLayer = L.layerGroup().addTo(this.map);
    this.mciLayer = L.layerGroup().addTo(this.map);

    this.clusterMarkers = new Map();
    this.latestHospitalsMap = null;

    // Re-render hospitals on zoom changes for smooth level-of-detail transition
    this.map.on('zoomend', () => {
      if (this.latestHospitalsMap) {
        this.renderHospitals(this.latestHospitalsMap);
      }
    });

    // Subscribe to state changes
    store.subscribe((state, changedKeys) => {
      if (changedKeys.includes('hospitals')) {
        this.renderHospitals(state.hospitals);
      }
      if (changedKeys.includes('activeIncidents') || changedKeys.includes('ambulances')) {
        this.renderActiveRoutesAndUnits(state);
      }
    });
  }

  // --- TIER 1: HOSPITALS (Level-of-Detail Presentation) ---
  renderHospitals(hospitalsMap) {
    if (!hospitalsMap) return;
    this.latestHospitalsMap = hospitalsMap;

    const currentHospIds = new Set();
    const currentClusterKeys = new Set();
    const zoom = this.map.getZoom();

    if (zoom >= 13) {
      // Zoomed in: clear clusters, render all individual hospitals
      for (const [key, marker] of this.clusterMarkers.entries()) {
        this.hospitalsLayer.removeLayer(marker);
        this.clusterMarkers.delete(key);
      }

      for (const [id, hosp] of hospitalsMap.entries()) {
        currentHospIds.add(String(id));
        this._renderIndividualHospital(id, hosp);
      }
    } else {
      // Broad view (zoom < 13):
      // Always render saturated hospitals individually as critical alerts
      const availableList = [];
      for (const [id, hosp] of hospitalsMap.entries()) {
        const isSaturated = hosp.available_beds <= 0 || hosp.is_full;
        if (isSaturated) {
          currentHospIds.add(String(id));
          this._renderIndividualHospital(id, hosp);
        } else {
          availableList.push(hosp);
        }
      }

      // Group nearby available hospitals by screen distance (~45px)
      const clusters = [];
      const assigned = new Set();

      for (let i = 0; i < availableList.length; i++) {
        if (assigned.has(i)) continue;
        const h1 = availableList[i];
        const p1 = this.map.latLngToLayerPoint([h1.latitude, h1.longitude]);
        const group = [h1];
        assigned.add(i);

        for (let j = i + 1; j < availableList.length; j++) {
          if (assigned.has(j)) continue;
          const h2 = availableList[j];
          const p2 = this.map.latLngToLayerPoint([h2.latitude, h2.longitude]);
          const dist = Math.hypot(p1.x - p2.x, p1.y - p2.y);
          if (dist < 42) {
            group.push(h2);
            assigned.add(j);
          }
        }
        clusters.push(group);
      }

      // Render clusters or singletons
      for (const group of clusters) {
        if (group.length === 1) {
          const hosp = group[0];
          currentHospIds.add(String(hosp.hospital_id));
          this._renderIndividualHospital(hosp.hospital_id, hosp);
        } else {
          const totalLat = group.reduce((sum, h) => sum + h.latitude, 0);
          const totalLon = group.reduce((sum, h) => sum + h.longitude, 0);
          const cLat = totalLat / group.length;
          const cLon = totalLon / group.length;
          const clusterKey = `cluster_${cLat.toFixed(3)}_${cLon.toFixed(3)}`;
          currentClusterKeys.add(clusterKey);

          const totalBeds = group.reduce((sum, h) => sum + (h.available_beds || 0), 0);

          const clusterIcon = L.divIcon({
            className: 'custom-hosp-cluster-icon',
            html: `
              <div class="hospital-cluster-pin" style="background: rgba(15, 23, 42, 0.92); border: 1px solid rgba(56, 189, 248, 0.5); border-radius: 12px; padding: 2px 7px; font-size: 11px; font-weight: 700; color: #38bdf8; font-family: var(--font-mono); display: flex; align-items: center; gap: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.6); cursor: pointer;">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="3"><path d="M12 5v14M5 12h14"/></svg>
                <span>${group.length}</span>
              </div>
            `,
            iconSize: [44, 22],
            iconAnchor: [22, 11],
          });

          let cMarker = this.clusterMarkers.get(clusterKey);
          if (cMarker) {
            cMarker.setLatLng([cLat, cLon]);
            cMarker.setIcon(clusterIcon);
          } else {
            cMarker = L.marker([cLat, cLon], { icon: clusterIcon });
            cMarker.bindPopup(`
              <div style="font-family: var(--font-sans); min-width: 170px;">
                <div style="font-weight: 700; font-size: 12px; color: #38bdf8; margin-bottom: 4px;">
                  ${group.length} Facilities in Sector
                </div>
                <div style="font-size: 11px; color: #94a3b8; font-family: var(--font-mono);">
                  <div>Total Available Beds: <strong>${totalBeds}</strong></div>
                  <div style="margin-top: 6px; color: #e2e8f0; font-size: 10px;">Click to zoom into sector</div>
                </div>
              </div>
            `);
            cMarker.on('click', () => {
              this.map.setView([cLat, cLon], 14);
            });
            this.clusterMarkers.set(clusterKey, cMarker);
            this.hospitalsLayer.addLayer(cMarker);
          }
        }
      }

      // Remove obsolete clusters
      for (const [key, marker] of this.clusterMarkers.entries()) {
        if (!currentClusterKeys.has(key)) {
          this.hospitalsLayer.removeLayer(marker);
          this.clusterMarkers.delete(key);
        }
      }
    }

    // Remove obsolete individual hospital markers
    for (const [id, entry] of this.hospitalMarkers.entries()) {
      if (!currentHospIds.has(String(id))) {
        this.hospitalsLayer.removeLayer(entry.marker);
        this.hospitalMarkers.delete(id);
      }
    }
  }

  _renderIndividualHospital(id, hosp) {
    const isSaturated = hosp.available_beds <= 0 || hosp.is_full;
    const markerClass = `hospital-marker-pin ${isSaturated ? 'saturated' : ''}`;
    const color = isSaturated ? '#ef4444' : '#0ea5e9';

    const popupHtml = `
      <div style="font-family: var(--font-sans); min-width: 185px;">
        <div style="font-weight: 700; font-size: 13px; margin-bottom: 4px; color: ${color};">
          ${hosp.hospital_id} (${hosp.hospital_type})
        </div>
        <div style="font-size: 11px; color: #94a3b8; font-family: var(--font-mono);">
          <div>Available Beds: <strong>${hosp.available_beds}</strong> / ${hosp.capacity}</div>
          <div>Available ICU: <strong>${hosp.available_icu}</strong> / ${hosp.icu_capacity}</div>
          <div style="margin-top: 4px; font-weight: 700; color: ${isSaturated ? '#ef4444' : '#10b981'};">
            ${isSaturated ? '⚠️ SATURATED (NO BEDS)' : '✓ CAPACITY AVAILABLE'}
          </div>
          <div style="margin-top: 8px; padding-top: 6px; border-top: 1px solid #334155;">
            <button class="btn-saturate-hosp" data-hosp="${hosp.hospital_id}" style="background:#ef4444; color:#fff; border:none; border-radius:3px; padding:4px 8px; font-size:10px; font-weight:700; cursor:pointer; width:100%; font-family:var(--font-sans);">
              ⚡ Simulate Saturation (Mark Full)
            </button>
          </div>
        </div>
      </div>
    `;

    const existing = this.hospitalMarkers.get(String(id));
    if (existing) {
      existing.marker.setLatLng([hosp.latitude, hosp.longitude]);
      if (existing.marker.getPopup()) {
        existing.marker.setPopupContent(popupHtml);
      }
      if (existing.isSaturated !== isSaturated) {
        existing.isSaturated = isSaturated;
        const icon = L.divIcon({
          className: 'custom-hosp-icon',
          html: `
            <div class="${markerClass}" style="background: ${color}; width: 20px; height: 20px; border-radius: 4px; display:flex; align-items:center; justify-content:center; border: 1px solid rgba(255,255,255,0.4); box-shadow: 0 2px 4px rgba(0,0,0,0.5);">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <path d="M12 5v14M5 12h14"/>
              </svg>
            </div>
          `,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        });
        existing.marker.setIcon(icon);
      }
    } else {
      const icon = L.divIcon({
        className: 'custom-hosp-icon',
        html: `
          <div class="${markerClass}" style="background: ${color}; width: 20px; height: 20px; border-radius: 4px; display:flex; align-items:center; justify-content:center; border: 1px solid rgba(255,255,255,0.4); box-shadow: 0 2px 4px rgba(0,0,0,0.5);">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
              <path d="M12 5v14M5 12h14"/>
            </svg>
          </div>
        `,
        iconSize: [20, 20],
        iconAnchor: [10, 10],
      });

      const marker = L.marker([hosp.latitude, hosp.longitude], { icon });
      marker.bindPopup(popupHtml);

      marker.on('popupopen', (e) => {
        const popupNode = e.popup.getElement();
        if (!popupNode) return;
        const btn = popupNode.querySelector('.btn-saturate-hosp');
        if (btn) {
          btn.onclick = async () => {
            try {
              await api.scheduleEvent(store.state.simTime, 'HOSPITAL_FULL', { hospital_id: hosp.hospital_id });
              showToast('Hospital Saturated', `Simulated 100% capacity for ${hosp.hospital_id}`, 'warning');
              marker.closePopup();
            } catch (err) {
              showToast('Event Error', err.message, 'danger');
            }
          };
        }
      });

      this.hospitalMarkers.set(String(id), { marker, isSaturated });
      this.hospitalsLayer.addLayer(marker);
    }
  }

  // --- TIER 2 & 3: ACTIVE INCIDENTS, EN_ROUTE AMBULANCES & DYNAMIC ROUTES ---
  renderActiveRoutesAndUnits(state) {
    const activeIncidents = state.activeIncidents || [];
    const activeIncidentIds = new Set();
    const activeAmbulanceIds = new Set();
    const activeRouteIds = new Set();

    for (const incident of activeIncidents) {
      const incIdStr = String(incident.incident_id);
      activeIncidentIds.add(incIdStr);

      const hasCoords = incident.latitude !== undefined && incident.longitude !== undefined && incident.latitude !== null && incident.longitude !== null;
      if (hasCoords) {
        const pStr = String(incident.priority || 'P3').toUpperCase();
        const pClass = pStr.startsWith('P') ? pStr.toLowerCase() : `p${pStr}`;
        let incColor = '#eab308';
        if (pClass === 'p1' || incident.severity === 'Critical') incColor = '#ef4444';
        else if (pClass === 'p2' || incident.severity === 'Emergency') incColor = '#f97316';
        else if (pClass === 'p4' || incident.severity === 'Low') incColor = '#0ea5e9';
        else if (pClass === 'p5' || incident.severity === 'Non-Urgent') incColor = '#64748b';

        const incPopupHtml = `
          <div style="font-family: var(--font-sans); min-width: 175px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <span style="font-weight: 800; font-size: 13px; color: ${incColor};">Incident #${incident.incident_id}</span>
              <span class="priority-pill ${pClass}" style="font-size: 9px; padding: 1px 5px;">${incident.priority || 'P?'}</span>
            </div>
            <div style="font-size: 11px; font-family: var(--font-mono); color: #cbd5e1; line-height: 1.4;">
              <div>Severity: <strong>${incident.severity || 'Unknown'}</strong></div>
              <div>Assigned: <strong>${incident.ambulance_id || 'Pending'}</strong></div>
              <div>Facility: <strong>${incident.hospital_id || 'Pending'}</strong></div>
              ${incident.eta_minutes !== undefined && incident.eta_minutes !== null ? `<div>ETA: <strong style="color: #f59e0b;">${Number(incident.eta_minutes).toFixed(1)}m</strong></div>` : ''}
            </div>
            <div style="margin-top: 6px; padding-top: 4px; border-top: 1px solid #334155; text-align: center;">
              <span style="font-size: 10px; color: #38bdf8; font-weight: 600;">🔍 Click to inspect detail</span>
            </div>
          </div>
        `;

        const existingIncMarker = this.incidentMarkers.get(incIdStr);
        if (existingIncMarker) {
          existingIncMarker.setLatLng([incident.latitude, incident.longitude]);
          if (existingIncMarker.getPopup()) {
            existingIncMarker.setPopupContent(incPopupHtml);
          }
        } else {
          const incIcon = L.divIcon({
            className: 'custom-incident-icon',
            html: `
              <div class="incident-marker-pin ${pClass}" style="background: ${incColor};">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3">
                  <path d="M12 2L2 22h20L12 2z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/>
                  <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
              </div>
            `,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          });

          const incMarker = L.marker([incident.latitude, incident.longitude], { icon: incIcon });
          incMarker.bindPopup(incPopupHtml);
          incMarker.on('click', () => {
            store.selectIncident(incident.incident_id);
            this.focusIncident(incident.incident_id);
            openIncidentDetail(incident.incident_id);
          });
          this.incidentMarkers.set(incIdStr, incMarker);
          this.incidentsLayer.addLayer(incMarker);
        }
      }

      if (!incident.ambulance_id || !incident.hospital_id) continue;

      const ambulance = state.ambulances.get(String(incident.ambulance_id));
      const hospital = state.hospitals.get(String(incident.hospital_id));

      if (!ambulance || !hospital) continue;

      const isEnRoute = ambulance.status === 'EN_ROUTE';
      const isCritical = incident.severity === 'Critical';
      const unitColor = isCritical ? '#ef4444' : '#f59e0b';
      const ambIdStr = String(ambulance.ambulance_id);

      if (isEnRoute) {
        activeAmbulanceIds.add(ambIdStr);
        activeRouteIds.add(incIdStr);

        const etaText = ambulance.eta_minutes !== null && ambulance.eta_minutes !== undefined ? `${Number(ambulance.eta_minutes).toFixed(1)} min` : 'Calculating';

        const ambPopupHtml = `
          <div style="font-family: var(--font-sans); min-width: 170px;">
            <div style="font-weight: 800; font-size: 13px; color: ${unitColor};">
              ${ambulance.ambulance_id} (${ambulance.ambulance_type || 'ALS'})
            </div>
            <div style="font-size: 11px; margin-top: 4px; font-family: var(--font-mono); color: #cbd5e1;">
              <div>Status: <strong>${ambulance.status}</strong></div>
              <div>Destination: <strong>${hospital.hospital_id}</strong></div>
              <div>ETA: <strong style="color: #f59e0b;">${etaText}</strong></div>
              <div>Traffic: ${ambulance.traffic_level || 'NORMAL'}</div>
            </div>
          </div>
        `;

        const existingAmbMarker = this.activeAmbulanceMarkers.get(ambIdStr);
        if (existingAmbMarker) {
          existingAmbMarker.setLatLng([ambulance.latitude, ambulance.longitude]);
          if (existingAmbMarker.getPopup()) {
            existingAmbMarker.setPopupContent(ambPopupHtml);
          }
          if (existingAmbMarker.getTooltip()) {
            existingAmbMarker.setTooltipContent(`${ambulance.ambulance_id} | ${etaText}`);
          }
        } else {
          const icon = L.divIcon({
            className: 'custom-amb-icon',
            html: `
              <div class="ambulance-marker-pin" style="background: ${unitColor};">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1 .4-1 1v9"/>
                  <circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>
                </svg>
              </div>
            `,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
          });

          const ambMarker = L.marker([ambulance.latitude, ambulance.longitude], { icon });
          ambMarker.bindPopup(ambPopupHtml);
          ambMarker.bindTooltip(`${ambulance.ambulance_id} | ${etaText}`, {
            permanent: true,
            direction: 'top',
            offset: [0, -14],
            className: 'amb-tooltip',
          });

          ambMarker.on('click', () => {
            store.selectIncident(incident.incident_id);
            this.focusIncident(incident.incident_id);
            openIncidentDetail(incident.incident_id);
          });

          this.activeAmbulanceMarkers.set(ambIdStr, ambMarker);
          this.enRouteAmbulancesLayer.addLayer(ambMarker);
        }

        const routePoints = (ambulance.route_waypoints && ambulance.route_waypoints.length > 1)
          ? ambulance.route_waypoints
          : [
              [ambulance.latitude, ambulance.longitude],
              [hospital.latitude, hospital.longitude],
            ];

        const existingRoute = this.routeLines.get(incIdStr);
        if (existingRoute) {
          existingRoute.setLatLngs(routePoints);
          existingRoute.setStyle({ color: unitColor });
        } else {
          const routeLine = L.polyline(
            routePoints,
            {
              color: unitColor,
              weight: 3,
              dashArray: '6, 8',
              opacity: 0.85,
            }
          );

          routeLine.on('click', () => {
            store.selectIncident(incident.incident_id);
            this.focusIncident(incident.incident_id);
            openIncidentDetail(incident.incident_id);
          });

          this.routeLines.set(incIdStr, routeLine);
          this.routePolylinesLayer.addLayer(routeLine);
        }
      }
    }

    // --- TIER 4: REPOSITIONING AMBULANCES (M9) ---
    if (state.ambulances) {
      const ambs = state.ambulances instanceof Map ? state.ambulances.values() : Object.values(state.ambulances);
      for (const ambulance of ambs) {
        if (ambulance.status === 'REPOSITIONING' || ambulance.is_repositioning) {
          const ambIdStr = String(ambulance.ambulance_id);
          const repoKey = `repo_${ambIdStr}`;
          activeAmbulanceIds.add(ambIdStr);

          const repoColor = '#06b6d4'; // Tactical Cyan
          const etaText = ambulance.eta_minutes !== null && ambulance.eta_minutes !== undefined ? `${Number(ambulance.eta_minutes).toFixed(1)} min` : 'Transit';

          const repoPopupHtml = `
            <div style="font-family: var(--font-sans); min-width: 180px;">
              <div style="font-weight: 800; font-size: 13px; color: ${repoColor};">
                ${ambulance.ambulance_id} [REPOSITIONING]
              </div>
              <div style="font-size: 11px; margin-top: 4px; font-family: var(--font-mono); color: #cbd5e1;">
                <div>Origin: <strong>${ambulance.reposition_origin_zone || 'Current'}</strong></div>
                <div>Target: <strong>${ambulance.reposition_target_zone || 'Staging'}</strong></div>
                <div>ETA: <strong style="color: ${repoColor};">${etaText}</strong></div>
              </div>
            </div>
          `;

          const existingRepoMarker = this.activeAmbulanceMarkers.get(ambIdStr);
          if (existingRepoMarker) {
            existingRepoMarker.setLatLng([ambulance.latitude, ambulance.longitude]);
            if (existingRepoMarker.getPopup()) {
              existingRepoMarker.setPopupContent(repoPopupHtml);
            }
            if (existingRepoMarker.getTooltip()) {
              existingRepoMarker.setTooltipContent(`${ambulance.ambulance_id} | ${etaText}`);
            }
          } else {
            const icon = L.divIcon({
              className: 'custom-amb-icon',
              html: `
                <div class="ambulance-marker-pin" style="background: ${repoColor}; box-shadow: 0 0 10px rgba(6, 182, 212, 0.6);">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <path d="M4 14l6-6 6 6"/>
                    <path d="M10 8v12"/>
                  </svg>
                </div>
              `,
              iconSize: [26, 26],
              iconAnchor: [13, 13],
            });

            const ambMarker = L.marker([ambulance.latitude, ambulance.longitude], { icon });
            ambMarker.bindPopup(repoPopupHtml);
            ambMarker.bindTooltip(`${ambulance.ambulance_id} | ${etaText}`, {
              permanent: true,
              direction: 'top',
              offset: [0, -14],
              className: 'amb-tooltip',
            });

            this.activeAmbulanceMarkers.set(ambIdStr, ambMarker);
            this.enRouteAmbulancesLayer.addLayer(ambMarker);
          }

          if (ambulance.route_waypoints && ambulance.route_waypoints.length > 1) {
            activeRouteIds.add(repoKey);
            const existingRepoRoute = this.routeLines.get(repoKey);
            if (existingRepoRoute) {
              existingRepoRoute.setLatLngs(ambulance.route_waypoints);
            } else {
              const routeLine = L.polyline(ambulance.route_waypoints, {
                color: repoColor,
                weight: 3,
                dashArray: '4, 6',
                opacity: 0.85,
              });
              this.routeLines.set(repoKey, routeLine);
              this.routePolylinesLayer.addLayer(routeLine);
            }
          }
        }
      }
    }

    // Prune inactive markers & polylines
    for (const [id, marker] of this.incidentMarkers.entries()) {
      if (!activeIncidentIds.has(String(id))) {
        this.incidentsLayer.removeLayer(marker);
        this.incidentMarkers.delete(id);
      }
    }
    for (const [id, marker] of this.activeAmbulanceMarkers.entries()) {
      if (!activeAmbulanceIds.has(String(id))) {
        this.enRouteAmbulancesLayer.removeLayer(marker);
        this.activeAmbulanceMarkers.delete(id);
      }
    }
    for (const [id, routeLine] of this.routeLines.entries()) {
      if (!activeRouteIds.has(String(id))) {
        this.routePolylinesLayer.removeLayer(routeLine);
        this.routeLines.delete(id);
      }
    }
  }

  focusIncident(incidentId) {
    const route = this.routeLines.get(String(incidentId));
    if (route) {
      this.map.fitBounds(route.getBounds(), { padding: [40, 40] });
      return;
    }
    const incMarker = this.incidentMarkers.get(String(incidentId));
    if (incMarker) {
      this.map.setView(incMarker.getLatLng(), 15);
      incMarker.openPopup();
    }
  }

  // --- TIER 4: MULTI-CASUALTY INCIDENTS (M9 Phase 4) ---
  renderMCIs(mcisList) {
    if (!this.mciLayer) return;
    this.mciLayer.clearLayers();

    if (!mcisList || mcisList.length === 0) return;

    for (const mci of mcisList) {
      if (mci.status === 'RESOLVED') continue;

      const isEvacuating = mci.status === 'EVACUATING';
      const statusColor = isEvacuating ? '#f59e0b' : '#ef4444';

      const icon = L.divIcon({
        className: 'custom-mci-icon',
        html: `
          <div style="position: relative; width: 34px; height: 34px; display: flex; align-items: center; justify-content: center;">
            <div style="position: absolute; inset: 0; border-radius: 50%; background: rgba(239, 68, 68, 0.4); animation: ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>
            <div style="width: 26px; height: 26px; border-radius: 50%; background: #dc2626; border: 2px solid #fecaca; display: flex; align-items: center; justify-content: center; box-shadow: 0 0 12px rgba(239, 68, 68, 0.8);">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3">
                <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
                <line x1="12" y1="9" x2="12" y2="13"/>
                <line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            </div>
          </div>
        `,
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      });

      const marker = L.marker([mci.latitude, mci.longitude], { icon });

      const pCounts = mci.casualty_counts_by_priority || {};
      const pSummary = Object.entries(pCounts).map(([k, v]) => `${k}:${v}`).join(' | ') || 'Pending';

      marker.bindPopup(`
        <div style="font-family: var(--font-sans); min-width: 200px;">
          <div style="display: flex; align-items: center; gap: 6px;">
            <span style="font-weight: 800; font-size: 13px; color: #f87171;">${mci.name}</span>
            <span style="font-size: 10px; background: rgba(239, 68, 68, 0.2); color: #fca5a5; padding: 1px 4px; border-radius: 3px; border: 1px solid #ef4444;">${mci.status}</span>
          </div>
          <div style="font-size: 11px; margin-top: 6px; font-family: var(--font-mono); color: #cbd5e1; line-height: 1.5;">
            <div>MCI ID: <strong style="color: #fff;">${mci.mci_id}</strong></div>
            <div>Total Casualties: <strong style="color: #f87171;">${mci.total_casualties}</strong></div>
            <div>Evacuated: <strong>${mci.evacuated_count} / ${mci.total_casualties}</strong></div>
            <div>Priority Breakdown: <strong style="color: #38bdf8;">${pSummary}</strong></div>
            <div>Assigned Fleet: <strong>${(mci.assigned_ambulance_ids || []).length} units</strong></div>
          </div>
        </div>
      `);

      this.mciLayer.addLayer(marker);
    }
  }

  focusMCI(mciId) {
    // Zoom in on MCI coordinates
  }
}

export const tacticalMap = new TacticalMap();
