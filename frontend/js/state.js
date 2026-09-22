/**
 * RAAH Reactive Store & State Manager
 */

class Store {
  constructor() {
    this.state = {
      simTime: 0,
      simStatus: 'STOPPED',
      isRealtimeRunning: false,
      speedMultiplier: 60.0,
      tickInterval: 1.0,
      activeIncidents: [],
      fleet: {
        total: 1000,
        available: 0,
        en_route: 0,
        busy: 0,
        maintenance: 0,
        arrived: 0,
      },
      hospitals: new Map(), // hospital_id -> Hospital object
      ambulances: new Map(), // ambulance_id -> Ambulance object
      incidents: new Map(), // incident_id -> Incident object (cache)
      events: [],
      decisions: [],
      selectedIncidentId: null,
      lastRenderedTime: -1,
      // M13 Phase 2: Realtime Stream & Connection State
      streamConnection: {
        state: 'DISCONNECTED', // 'CONNECTED' | 'RECONNECTING' | 'SYNCHRONIZING' | 'DISCONNECTED'
        sequence: null,
        lastEventTime: null,
        lastEventType: null,
        reconnectAttempts: 0,
      },
      activityFeed: [], // Chronological RAAH activity entries (max 100)
    };

    this.subscribers = new Set();
  }

  subscribe(callback) {
    this.subscribers.add(callback);
    return () => this.subscribers.delete(callback);
  }

  notify(changedKeys = []) {
    this.subscribers.forEach(cb => {
      try {
        cb(this.state, changedKeys);
      } catch (err) {
        console.error('Error in state subscriber:', err);
      }
    });
  }

  setHospitals(hospitalsList) {
    this.state.hospitals.clear();
    for (const h of hospitalsList) {
      this.state.hospitals.set(String(h.hospital_id), h);
    }
    this.notify(['hospitals']);
  }

  updateHospital(hospitalUpdate) {
    if (!hospitalUpdate || !hospitalUpdate.hospital_id) return;
    const hid = String(hospitalUpdate.hospital_id);
    const existing = this.state.hospitals.get(hid);
    if (existing) {
      Object.assign(existing, hospitalUpdate);
      this.notify(['hospitals']);
    }
  }

  setAmbulances(ambulancesList) {
    this.state.ambulances.clear();
    for (const a of ambulancesList) {
      this.state.ambulances.set(String(a.ambulance_id), a);
    }
    this.notify(['ambulances']);
  }

  // Non-destructive incremental position / status update for moving fleet
  updateAmbulances(movingAmbulancesList) {
    if (!movingAmbulancesList || !Array.isArray(movingAmbulancesList)) return;
    for (const moving of movingAmbulancesList) {
      const aid = String(moving.ambulance_id);
      const existing = this.state.ambulances.get(aid);
      if (existing) {
        existing.latitude = moving.latitude;
        existing.longitude = moving.longitude;
        existing.status = moving.status;
        if (moving.eta_minutes !== undefined) existing.eta_minutes = moving.eta_minutes;
        if (moving.route_waypoints) existing.route_waypoints = moving.route_waypoints;
        if (moving.hospital_id) existing.hospital_id = moving.hospital_id;
      } else {
        this.state.ambulances.set(aid, { ...moving });
      }

      // Sync ETA and arrival status to assigned active incidents & cached incidents
      for (const inc of this.state.activeIncidents) {
        if (String(inc.ambulance_id) === aid) {
          if (moving.eta_minutes !== undefined && moving.eta_minutes !== null) {
            inc.eta_minutes = moving.eta_minutes;
          }
          if (moving.status === 'ARRIVED') {
            inc.status = 'ARRIVED';
          }
        }
      }
      for (const inc of this.state.incidents.values()) {
        if (String(inc.ambulance_id) === aid) {
          if (moving.eta_minutes !== undefined && moving.eta_minutes !== null) {
            inc.eta_minutes = moving.eta_minutes;
          }
          if (moving.status === 'ARRIVED') {
            inc.status = 'ARRIVED';
          }
        }
      }
    }
    this.notify(['ambulances', 'activeIncidents']);
  }

  updateFromDashboard(dashboardData) {
    if (!dashboardData) return;
    const changed = ['simTime'];
    this.state.simTime = dashboardData.time !== undefined ? dashboardData.time : this.state.simTime;
    if (dashboardData.active_incidents) {
      const merged = dashboardData.active_incidents.map(newInc => {
        const existing = this.state.activeIncidents.find(i => i.incident_id === newInc.incident_id);
        const mergedInc = existing ? { ...existing, ...newInc } : newInc;
        this.state.incidents.set(mergedInc.incident_id, mergedInc);
        return mergedInc;
      });
      this.state.activeIncidents = merged;
      changed.push('activeIncidents');
    }
    if (dashboardData.fleet) {
      this.state.fleet = dashboardData.fleet;
      changed.push('fleet');
    }
    if (dashboardData.events && Array.isArray(dashboardData.events) && dashboardData.events.length > 0) {
      this.state.events = dashboardData.events;
      changed.push('events');
    }

    this.notify(changed);
  }

  // Non-destructive single incident addition or update
  addOrUpdateIncident(incident) {
    if (!incident || incident.incident_id === undefined) return;
    this.state.incidents.set(incident.incident_id, { ...incident });
    const idx = this.state.activeIncidents.findIndex(i => i.incident_id === incident.incident_id);
    if (idx >= 0) {
      this.state.activeIncidents[idx] = { ...this.state.activeIncidents[idx], ...incident };
    } else {
      this.state.activeIncidents.push(incident);
    }
    this.notify(['activeIncidents']);
  }

  removeIncident(incidentId) {
    const prevLen = this.state.activeIncidents.length;
    this.state.activeIncidents = this.state.activeIncidents.filter(i => i.incident_id !== incidentId);
    if (this.state.activeIncidents.length !== prevLen) {
      this.notify(['activeIncidents']);
    }
  }

  updateRealtimeStatus(statusData) {
    if (!statusData) return;
    this.state.simStatus = statusData.status || this.state.simStatus;
    this.state.isRealtimeRunning = statusData.is_running !== undefined ? statusData.is_running : this.state.isRealtimeRunning;
    this.state.speedMultiplier = statusData.speed_multiplier || this.state.speedMultiplier;
    this.state.tickInterval = statusData.tick_interval_seconds || this.state.tickInterval;
    this.notify(['realtimeStatus']);
  }

  setDecisions(decisionsList) {
    this.state.decisions = decisionsList || [];
    this.notify(['decisions']);
  }

  addDecision(decision) {
    if (!decision) return;
    this.state.decisions.unshift(decision);
    if (this.state.decisions.length > 200) {
      this.state.decisions.pop();
    }
    this.notify(['decisions']);
  }

  selectIncident(incidentId) {
    this.state.selectedIncidentId = incidentId;
    this.notify(['selectedIncidentId']);
  }

  // Realtime Stream Connection State (M13 Phase 2)
  setConnectionStatus(connUpdate) {
    Object.assign(this.state.streamConnection, connUpdate);
    this.notify(['streamConnection']);
  }

  // Activity Feed (M13 Phase 2)
  addActivityEntry(entry) {
    if (!entry) return;
    this.state.activityFeed.unshift(entry);
    if (this.state.activityFeed.length > 100) {
      this.state.activityFeed.pop();
    }
    this.notify(['activityFeed']);
  }
}

export const store = new Store();
