/**
 * Fleet Readiness Gauges Component
 */

import { store } from '../state.js';

export function setupFleet() {
  const statAvailable = document.getElementById('stat-available');
  const statEnroute = document.getElementById('stat-enroute');
  const statBusy = document.getElementById('stat-busy');
  const statMaintenance = document.getElementById('stat-maintenance');

  store.subscribe((state, changedKeys) => {
    if (changedKeys.includes('fleet')) {
      const fleet = state.fleet;
      statAvailable.textContent = fleet.available ?? 0;
      statEnroute.textContent = fleet.en_route ?? 0;
      statBusy.textContent = fleet.busy ?? 0;
      statMaintenance.textContent = fleet.maintenance ?? 0;
    }
  });
}
