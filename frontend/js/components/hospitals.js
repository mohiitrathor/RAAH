import { store } from '../state.js';
import { getHospitalProjections } from '../api.js';

let projectionsMap = new Map();

async function refreshProjections() {
  try {
    const list = await getHospitalProjections();
    if (Array.isArray(list)) {
      projectionsMap = new Map(list.map(p => [p.hospital_id, p]));
    }
  } catch (_) {}
}

export function setupHospitals() {
  const container = document.getElementById('hospitals-container');

  // Initial load and degraded background safety fallback (60 seconds)
  // Real-time capacity shifts are streamed via SSE TICK and HOSPITAL_UPDATE projections
  refreshProjections();
  setInterval(refreshProjections, 60000);

  store.subscribe((state, changedKeys) => {
    if (!changedKeys.includes('hospitals')) {
      return;
    }

    const hospitals = Array.from(state.hospitals.values());
    if (hospitals.length === 0) return;

    // Show top 20 hospitals sorted by highest occupancy percentage or saturation first
    const sorted = hospitals.slice().sort((a, b) => {
      const aSat = a.available_beds <= 0 ? 1 : 0;
      const bSat = b.available_beds <= 0 ? 1 : 0;
      if (aSat !== bSat) return bSat - aSat;

      const aOcc = a.capacity > 0 ? a.current_load / a.capacity : 0;
      const bOcc = b.capacity > 0 ? b.current_load / b.capacity : 0;
      return bOcc - aOcc;
    }).slice(0, 20);

    container.innerHTML = sorted.map(h => {
      const proj = projectionsMap.get(h.hospital_id);
      const isSaturated = h.available_beds <= 0 || h.is_full || (proj && proj.projected_available_beds <= 0);
      const occupancy = h.capacity > 0 ? Math.min(100, Math.round((h.current_load / h.capacity) * 100)) : 0;

      let fillClass = 'safe';
      if (occupancy >= 80) fillClass = 'warn';
      if (isSaturated || occupancy >= 95) fillClass = 'danger';

      const incomingBadge = (proj && proj.incoming_count > 0)
        ? `<span style="font-size: 9px; background: rgba(56, 189, 248, 0.2); color: #38bdf8; padding: 1px 4px; border-radius: 3px; margin-left: 4px;">+${proj.incoming_count} in-flight</span>`
        : '';

      const projText = proj
        ? `<span style="font-size: 10px; color: #94a3b8;">Proj: <strong style="color: ${proj.projected_available_beds <= 2 ? '#f59e0b' : '#38bdf8'};">${proj.projected_available_beds}</strong> avail</span>`
        : `<span>Beds: <strong>${h.available_beds}</strong> / ${h.capacity}</span>`;

      return `
        <div class="hospital-card ${isSaturated ? 'saturated' : ''}">
          <div class="hosp-title-row">
            <span style="font-family: var(--font-mono); font-size: 12px; color: ${isSaturated ? '#ef4444' : '#38bdf8'};">
              ${h.hospital_id} ${incomingBadge}
            </span>
            <span style="font-size: 10px; color: ${isSaturated ? '#ef4444' : 'var(--text-muted)'}; font-weight: 700;">
              ${isSaturated ? 'FULL' : `${occupancy}% LOAD`}
            </span>
          </div>
          <div class="capacity-bar-track">
            <div class="capacity-bar-fill ${fillClass}" style="width: ${occupancy}%;"></div>
          </div>
          <div class="hosp-metrics-row">
            ${projText}
            <span>ICU: <strong>${proj ? proj.projected_available_icu : h.available_icu}</strong> / ${h.icu_capacity}</span>
          </div>
        </div>
      `;
    }).join('');
  });
}
