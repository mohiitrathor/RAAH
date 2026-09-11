/**
 * Live Emergency Call Intake Modal
 * Encapsulates the 24-feature ML model contract with clinical presets and inline validation.
 */

import * as api from '../api.js';
import { store } from '../state.js';
import { tacticalMap } from '../map.js';
import { showToast } from './toasts.js';

let intakeModalElement = null;

const CLINICAL_PRESETS = [
  {
    name: 'Acute STEMI Cardiac (Critical P1)',
    data: {
      Sex: 'Male',
      Age: 65,
      Condition: 'Cardiac',
      Arrival_Mode: 'Ambulance',
      Heart_Rate: 135,
      SpO2: 86,
      Systolic_BP: 85,
      Diastolic_BP: 55,
      Respiratory_Rate: 32,
      Temperature: 37.2,
      Consciousness: 'Drowsy',
      Oxygen_Requirement: 'Oxygen Mask',
      Injury_Type: 'No Injury',
      GCS: 12,
      Pain_Score: 9,
      Blood_Glucose: 195,
      Respiratory_Distress: 1,
      Chest_Pain: 1,
      Bleeding: 0,
      Seizure: 0,
      Diabetes: 1,
      Hypertension: 1,
      Heart_Disease: 1,
      Respiratory_Disease: 0,
      patient_lat: 26.9124,
      patient_lon: 75.7873,
    },
  },
  {
    name: 'Severe Polytrauma (Emergency P2)',
    data: {
      Sex: 'Female',
      Age: 29,
      Condition: 'Trauma',
      Arrival_Mode: 'Ambulance',
      Heart_Rate: 118,
      SpO2: 94,
      Systolic_BP: 100,
      Diastolic_BP: 65,
      Respiratory_Rate: 24,
      Temperature: 36.8,
      Consciousness: 'Alert',
      Oxygen_Requirement: 'Nasal Cannula',
      Injury_Type: 'Fracture',
      GCS: 14,
      Pain_Score: 8,
      Blood_Glucose: 120,
      Respiratory_Distress: 0,
      Chest_Pain: 0,
      Bleeding: 1,
      Seizure: 0,
      Diabetes: 0,
      Hypertension: 0,
      Heart_Disease: 0,
      Respiratory_Disease: 0,
      patient_lat: 26.9200,
      patient_lon: 75.8000,
    },
  },
  {
    name: 'Asthma Exacerbation (Emergency P2)',
    data: {
      Sex: 'Male',
      Age: 44,
      Condition: 'Respiratory',
      Arrival_Mode: 'Walk-in',
      Heart_Rate: 110,
      SpO2: 89,
      Systolic_BP: 130,
      Diastolic_BP: 85,
      Respiratory_Rate: 36,
      Temperature: 37.0,
      Consciousness: 'Alert',
      Oxygen_Requirement: 'Oxygen Mask',
      Injury_Type: 'No Injury',
      GCS: 15,
      Pain_Score: 4,
      Blood_Glucose: 105,
      Respiratory_Distress: 1,
      Chest_Pain: 0,
      Bleeding: 0,
      Seizure: 0,
      Diabetes: 0,
      Hypertension: 0,
      Heart_Disease: 0,
      Respiratory_Disease: 1,
      patient_lat: 26.9050,
      patient_lon: 75.7700,
    },
  },
  {
    name: 'Mild Gastroenteritis (Non-Urgent P5)',
    data: {
      Sex: 'Female',
      Age: 32,
      Condition: 'Gastrointestinal',
      Arrival_Mode: 'Walk-in',
      Heart_Rate: 78,
      SpO2: 99,
      Systolic_BP: 115,
      Diastolic_BP: 75,
      Respiratory_Rate: 16,
      Temperature: 37.1,
      Consciousness: 'Alert',
      Oxygen_Requirement: 'No Oxygen',
      Injury_Type: 'No Injury',
      GCS: 15,
      Pain_Score: 3,
      Blood_Glucose: 92,
      Respiratory_Distress: 0,
      Chest_Pain: 0,
      Bleeding: 0,
      Seizure: 0,
      Diabetes: 0,
      Hypertension: 0,
      Heart_Disease: 0,
      Respiratory_Disease: 0,
      patient_lat: 26.9150,
      patient_lon: 75.7950,
    },
  },
];

export function openEmergencyIntakeModal() {
  if (intakeModalElement) {
    document.body.removeChild(intakeModalElement);
    intakeModalElement = null;
  }

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop visible';

  const dialog = document.createElement('div');
  dialog.className = 'modal-dialog intake-modal-dialog';

  dialog.innerHTML = `
    <div class="modal-header">
      <div class="modal-title">
        <i data-lucide="radio" style="color: #ef4444;"></i>
        <span>Live Emergency Call Intake (ML Triage)</span>
      </div>
      <button class="modal-close-btn">&times;</button>
    </div>

    <div class="modal-body intake-modal-body">
      <!-- Operational Dispatch Workflow Progression -->
      <div class="dispatch-workflow-bar">
        <div class="workflow-step active">
          <span class="step-badge">1</span>
          <div class="step-info">
            <span class="step-label">CALL INTAKE</span>
            <span class="step-sub">911 CAD Ingest</span>
          </div>
        </div>
        <div class="workflow-arrow">→</div>
        <div class="workflow-step active">
          <span class="step-badge">2</span>
          <div class="step-info">
            <span class="step-label">TRIAGE</span>
            <span class="step-sub">ML Severity</span>
          </div>
        </div>
        <div class="workflow-arrow">→</div>
        <div class="workflow-step">
          <span class="step-badge">3</span>
          <div class="step-info">
            <span class="step-label">DISPATCH</span>
            <span class="step-sub">Fleet Unit</span>
          </div>
        </div>
        <div class="workflow-arrow">→</div>
        <div class="workflow-step">
          <span class="step-badge">4</span>
          <div class="step-info">
            <span class="step-label">ROUTE</span>
            <span class="step-sub">ETA Calc</span>
          </div>
        </div>
        <div class="workflow-arrow">→</div>
        <div class="workflow-step">
          <span class="step-badge">5</span>
          <div class="step-info">
            <span class="step-label">HOSPITAL</span>
            <span class="step-sub">Emergency Dept</span>
          </div>
        </div>
      </div>

      <!-- Preset Fill Row -->
      <div class="preset-selector-row">
        <span class="preset-label">Quick Presets:</span>
        <div class="preset-pills">
          ${CLINICAL_PRESETS.map((p, idx) => `
            <button type="button" class="btn-preset" data-idx="${idx}">${p.name}</button>
          `).join('')}
        </div>
      </div>

      <form id="form-emergency-intake" class="intake-form-grid">
        <!-- Step 1: Call & Patient Demographics -->
        <div class="intake-form-section">
          <h4><span class="step-num">1</span> CALL &amp; PATIENT DEMOGRAPHICS</h4>
          <div class="form-row-3">
            <div class="form-field">
              <label>Sex</label>
              <select name="Sex" class="tactical-select" required>
                <option value="Male">Male</option>
                <option value="Female">Female</option>
              </select>
            </div>
            <div class="form-field">
              <label>Age (0-120)</label>
              <input type="number" name="Age" class="tactical-input" value="55" min="0" max="120" required />
            </div>
            <div class="form-field">
              <label>Primary Condition</label>
              <select name="Condition" class="tactical-select" required>
                <option value="Cardiac">Cardiac</option>
                <option value="Trauma">Trauma</option>
                <option value="Respiratory">Respiratory</option>
                <option value="Neurological">Neurological</option>
                <option value="Gastrointestinal">Gastrointestinal</option>
                <option value="Infection">Infection</option>
                <option value="Other">Other</option>
              </select>
            </div>
          </div>
          <div class="form-row-2" style="margin-top: 8px;">
            <div class="form-field">
              <label>Arrival Mode</label>
              <select name="Arrival_Mode" class="tactical-select" required>
                <option value="Ambulance">Ambulance</option>
                <option value="Referral">Referral</option>
                <option value="Walk-in">Walk-in</option>
              </select>
            </div>
            <div class="form-field">
              <label>Injury Type</label>
              <select name="Injury_Type" class="tactical-select" required>
                <option value="No Injury">No Injury</option>
                <option value="Head Injury">Head Injury</option>
                <option value="Fracture">Fracture</option>
                <option value="Internal Injury">Internal Injury</option>
                <option value="Burn">Burn</option>
                <option value="Laceration">Laceration</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Step 2: Physiological Vitals -->
        <div class="intake-form-section">
          <h4><span class="step-num">2</span> PHYSIOLOGICAL VITAL SIGNS</h4>
          <div class="form-row-3">
            <div class="form-field">
              <label>Heart Rate (bpm)</label>
              <input type="number" name="Heart_Rate" class="tactical-input" value="98" min="20" max="300" step="1" required />
            </div>
            <div class="form-field">
              <label>SpO2 (%)</label>
              <input type="number" name="SpO2" class="tactical-input" value="95" min="40" max="100" step="0.5" required />
            </div>
            <div class="form-field">
              <label>Systolic BP (mmHg)</label>
              <input type="number" name="Systolic_BP" class="tactical-input" value="125" min="40" max="300" required />
            </div>
          </div>
          <div class="form-row-3" style="margin-top: 8px;">
            <div class="form-field">
              <label>Diastolic BP (mmHg)</label>
              <input type="number" name="Diastolic_BP" class="tactical-input" value="80" min="20" max="200" required />
            </div>
            <div class="form-field">
              <label>Resp. Rate (br/min)</label>
              <input type="number" name="Respiratory_Rate" class="tactical-input" value="20" min="4" max="80" required />
            </div>
            <div class="form-field">
              <label>Temp (°C)</label>
              <input type="number" name="Temperature" class="tactical-input" value="37.0" min="30" max="45" step="0.1" required />
            </div>
          </div>
        </div>

        <!-- Step 3: Clinical & Neurological Assessment -->
        <div class="intake-form-section">
          <h4><span class="step-num">3</span> CLINICAL ASSESSMENT</h4>
          <div class="form-row-3">
            <div class="form-field">
              <label>Consciousness</label>
              <select name="Consciousness" class="tactical-select" required>
                <option value="Alert">Alert</option>
                <option value="Drowsy">Drowsy</option>
                <option value="Confused">Confused</option>
                <option value="Unconscious">Unconscious</option>
              </select>
            </div>
            <div class="form-field">
              <label>Oxygen Support</label>
              <select name="Oxygen_Requirement" class="tactical-select" required>
                <option value="No Oxygen">No Oxygen</option>
                <option value="Nasal Cannula">Nasal Cannula</option>
                <option value="Oxygen Mask">Oxygen Mask</option>
                <option value="Ventilator">Ventilator</option>
              </select>
            </div>
            <div class="form-field">
              <label>GCS Score (3-15)</label>
              <input type="number" name="GCS" class="tactical-input" value="15" min="3" max="15" required />
            </div>
          </div>
          <div class="form-row-2" style="margin-top: 8px;">
            <div class="form-field">
              <label>Pain Score (0-10)</label>
              <input type="number" name="Pain_Score" class="tactical-input" value="5" min="0" max="10" required />
            </div>
            <div class="form-field">
              <label>Blood Glucose (mg/dL)</label>
              <input type="number" name="Blood_Glucose" class="tactical-input" value="110" min="20" max="1000" step="1" required />
            </div>
          </div>
        </div>

        <!-- Step 4: Symptoms & Comorbidities -->
        <div class="intake-form-section">
          <h4><span class="step-num">4</span> ACUTE SYMPTOMS &amp; COMORBIDITIES</h4>
          <div class="checkbox-grid">
            <label class="check-item"><input type="checkbox" name="Respiratory_Distress" value="1" /> Resp Distress</label>
            <label class="check-item"><input type="checkbox" name="Chest_Pain" value="1" /> Chest Pain</label>
            <label class="check-item"><input type="checkbox" name="Bleeding" value="1" /> Bleeding</label>
            <label class="check-item"><input type="checkbox" name="Seizure" value="1" /> Seizure</label>
            <label class="check-item"><input type="checkbox" name="Diabetes" value="1" /> Diabetes</label>
            <label class="check-item"><input type="checkbox" name="Hypertension" value="1" /> Hypertension</label>
            <label class="check-item"><input type="checkbox" name="Heart_Disease" value="1" /> Heart Disease</label>
            <label class="check-item"><input type="checkbox" name="Respiratory_Disease" value="1" /> Resp Disease</label>
          </div>
        </div>

        <!-- Step 5: Incident Coordinates -->
        <div class="intake-form-section">
          <h4><span class="step-num">5</span> INCIDENT LOCATION (JAIPUR METROPOLITAN)</h4>
          <div class="form-row-2">
            <div class="form-field">
              <label>Latitude</label>
              <input type="number" name="patient_lat" class="tactical-input" value="26.9124" min="26.5" max="27.5" step="0.0001" required />
            </div>
            <div class="form-field">
              <label>Longitude</label>
              <input type="number" name="patient_lon" class="tactical-input" value="75.7873" min="75.5" max="76.5" step="0.0001" required />
            </div>
          </div>
        </div>

        <div id="intake-error-box" class="intake-error-box" style="display:none;"></div>

        <div class="modal-footer" style="padding: 16px 0 0 0;">
          <button type="button" class="btn-cancel">Cancel</button>
          <button type="submit" class="btn-primary" id="btn-submit-triage">
            <i data-lucide="activity"></i> Run ML Triage & Dispatch
          </button>
        </div>
      </form>
    </div>
  `;

  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  intakeModalElement = backdrop;
  if (window.lucide) window.lucide.createIcons();

  function close() {
    backdrop.classList.remove('visible');
    setTimeout(() => {
      if (backdrop.parentElement) backdrop.parentElement.removeChild(backdrop);
      intakeModalElement = null;
    }, 200);
  }

  dialog.querySelector('.modal-close-btn').addEventListener('click', close);
  dialog.querySelector('.btn-cancel').addEventListener('click', close);
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) close();
  });

  const form = dialog.querySelector('#form-emergency-intake');
  const errorBox = dialog.querySelector('#intake-error-box');

  // Setup presets
  dialog.querySelectorAll('.btn-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.getAttribute('data-idx'), 10);
      const preset = CLINICAL_PRESETS[idx];
      if (!preset) return;

      for (const [key, val] of Object.entries(preset.data)) {
        const input = form.elements[key];
        if (!input) continue;
        if (input.type === 'checkbox') {
          input.checked = val === 1;
        } else {
          input.value = val;
        }
      }
      showToast('Preset Applied', `Populated ${preset.name}`, 'info', 2000);
    });
  });

  // Handle submit
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    errorBox.style.display = 'none';

    // Build payload matching the exact 24-feature contract + coords
    const fd = new FormData(form);
    const payload = {
      Sex: fd.get('Sex'),
      Age: parseInt(fd.get('Age'), 10),
      Condition: fd.get('Condition'),
      Arrival_Mode: fd.get('Arrival_Mode'),
      Injury_Type: fd.get('Injury_Type'),
      Heart_Rate: parseFloat(fd.get('Heart_Rate')),
      SpO2: parseFloat(fd.get('SpO2')),
      Systolic_BP: parseFloat(fd.get('Systolic_BP')),
      Diastolic_BP: parseFloat(fd.get('Diastolic_BP')),
      Respiratory_Rate: parseFloat(fd.get('Respiratory_Rate')),
      Temperature: parseFloat(fd.get('Temperature')),
      Consciousness: fd.get('Consciousness'),
      Oxygen_Requirement: fd.get('Oxygen_Requirement'),
      GCS: parseInt(fd.get('GCS'), 10),
      Pain_Score: parseInt(fd.get('Pain_Score'), 10),
      Blood_Glucose: parseFloat(fd.get('Blood_Glucose')),
      Respiratory_Distress: fd.get('Respiratory_Distress') ? 1 : 0,
      Chest_Pain: fd.get('Chest_Pain') ? 1 : 0,
      Bleeding: fd.get('Bleeding') ? 1 : 0,
      Seizure: fd.get('Seizure') ? 1 : 0,
      Diabetes: fd.get('Diabetes') ? 1 : 0,
      Hypertension: fd.get('Hypertension') ? 1 : 0,
      Heart_Disease: fd.get('Heart_Disease') ? 1 : 0,
      Respiratory_Disease: fd.get('Respiratory_Disease') ? 1 : 0,
      patient_lat: parseFloat(fd.get('patient_lat')),
      patient_lon: parseFloat(fd.get('patient_lon')),
    };

    // Client validation bounds check
    if (payload.SpO2 > 100 || payload.SpO2 < 40) {
      errorBox.textContent = 'SpO2 must be between 40% and 100%.';
      errorBox.style.display = 'block';
      return;
    }

    try {
      const btn = dialog.querySelector('#btn-submit-triage');
      btn.disabled = true;
      btn.innerHTML = `<i data-lucide="loader" class="spinning"></i> Triaging...`;
      if (window.lucide) window.lucide.createIcons();

      const result = await api.dispatchLive(payload);

      // Refresh live telemetry
      const dash = await api.getDashboard();
      store.updateFromDashboard(dash);

      if (result.ambulance) {
        const ambulances = await api.getAmbulances();
        store.setAmbulances(ambulances);
      }

      showToast(
        'Emergency Dispatched',
        `Incident #${result.incident_id}: Assigned ${result.ambulance.ambulance_id} -> ${result.hospital.hospital_id} (${result.patient.predicted_severity}, ${result.patient.priority})`,
        result.patient.predicted_severity === 'Critical' ? 'danger' : 'success',
        6000
      );

      close();

      // Focus on map
      setTimeout(() => {
        store.selectIncident(result.incident_id);
        tacticalMap.focusIncident(result.incident_id);
      }, 300);

    } catch (err) {
      errorBox.textContent = `Dispatch Failed: ${err.message}`;
      errorBox.style.display = 'block';
      const btn = dialog.querySelector('#btn-submit-triage');
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="activity"></i> Run ML Triage & Dispatch`;
      if (window.lucide) window.lucide.createIcons();
    }
  });
}
