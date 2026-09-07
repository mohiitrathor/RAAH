"""
RAAH Deterministic Evidence Formatter (M13 Phase 3)
===================================================

Generates pure, deterministic, human-readable operational explanations directly
from a structured DecisionEvidenceRecord.

Strict Invariants:
- Pure function: same input record -> byte-for-byte identical explanation string.
- Zero speculation, zero hallucination, zero LLM.
- Only displays evidence fields that actually exist in the record.
- Distinguishes evaluated criteria from unavailable criteria.
"""

from typing import List
from api.decision_evidence.models import DecisionEvidenceRecord, DecisionType


def format_explanation(record: DecisionEvidenceRecord) -> str:
    """
    Format a DecisionEvidenceRecord into a deterministic operational explanation.
    """
    lines: List[str] = []

    # 1. Header & Decision
    lines.append(f"Decision: {record.action}")
    if record.incident_id is not None:
        lines.append(f"Incident: INC_{record.incident_id:04d}" if isinstance(record.incident_id, int) else f"Incident: {record.incident_id}")
    lines.append(f"Decision Type: {record.decision_type.value}")
    lines.append(f"Simulation Time: {record.sim_time}s")

    # 2. Operational Rationale (Factual summary of selection)
    lines.append("")
    lines.append("Operational Summary:")
    if record.decision_type == DecisionType.INITIAL_DISPATCH:
        amb = record.selected_ambulance_id or "UNKNOWN"
        hosp = record.selected_hospital_id or "UNKNOWN"
        lines.append(
            f"Ambulance {amb} and hospital {hosp} assigned based on ML clinical triage, "
            "fleet transit estimates, and facility capability matching."
        )
    elif record.decision_type == DecisionType.HOSPITAL_REDIRECTION:
        orig = record.metadata.get("original_hospital", "UNKNOWN")
        new_h = record.selected_hospital_id or "UNKNOWN"
        lines.append(
            f"Patient redirected from {orig} to {new_h} "
            f"due to evaluated transit/facility triggers."
        )
    elif record.decision_type in (DecisionType.FLEET_REPOSITION, DecisionType.HOSPITAL_DIVERSION):
        lines.append(
            f"Proactive optimization executed for target {record.metadata.get('target', 'N/A')} "
            "to balance regional operational capacity."
        )
    else:
        lines.append(f"Action {record.action} executed.")

    # 3. Evidence (Only genuinely populated fields, in fixed order)
    lines.append("")
    lines.append("Evidence:")
    if record.severity is not None:
        prio_str = f" ({record.priority})" if record.priority else ""
        lines.append(f"- Severity: {record.severity}{prio_str}")
    if record.patient_condition is not None:
        lines.append(f"- Condition: {record.patient_condition}")
    if record.confidence is not None:
        lines.append(f"- ML Confidence: {record.confidence:.4f}")
    if record.selected_ambulance_id is not None:
        amb_type = f" [{record.selected_ambulance_type}]" if record.selected_ambulance_type else ""
        lines.append(f"- Selected Ambulance: {record.selected_ambulance_id}{amb_type}")
    if record.selected_hospital_id is not None:
        hosp_type = f" [{record.selected_hospital_type}]" if record.selected_hospital_type else ""
        lines.append(f"- Selected Hospital: {record.selected_hospital_id}{hosp_type}")
    if record.eta_minutes is not None:
        lines.append(f"- ETA: {record.eta_minutes:.2f} min")
    if record.distance_km is not None:
        lines.append(f"- Distance: {record.distance_km:.2f} km")
    if record.hospital_available_beds is not None:
        lines.append(f"- Hospital Available Beds: {record.hospital_available_beds}")
    if record.hospital_available_icu is not None:
        lines.append(f"- Hospital Available ICU: {record.hospital_available_icu}")
    if record.policy_mode is not None:
        lines.append(f"- Policy Mode: {record.policy_mode}")
    if record.policy_version is not None:
        lines.append(f"- Policy Version: {record.policy_version}")
    for score_key in sorted(record.scores.keys()):
        lines.append(f"- Score ({score_key}): {record.scores[score_key]:.4f}")

    # 4. Constraints Evaluated (Fixed alphabetical order by constraint name)
    lines.append("")
    lines.append("Constraints Evaluated:")
    if record.constraints:
        for c in sorted(record.constraints, key=lambda item: item.name):
            status = "SATISFIED" if c.satisfied else "FAILED"
            detail = f" ({c.details})" if c.details else ""
            lines.append(f"- [{status}] {c.name}{detail}")
    else:
        lines.append("- None recorded")

    # 5. Alternatives Considered
    lines.append("")
    lines.append("Alternatives:")
    if not record.alternatives_available:
        lines.append("- [UNAVAILABLE] Alternatives were not exposed by the underlying decision engine.")
    elif not record.alternatives:
        lines.append("- None evaluated")
    else:
        for alt in sorted(record.alternatives, key=lambda a: a.candidate_id):
            sel_mark = "SELECTED" if alt.selected else "NOT_SELECTED"
            score_str = f", score={alt.score:.4f}" if alt.score is not None else ""
            eta_str = f", eta={alt.eta_minutes:.2f}m" if alt.eta_minutes is not None else ""
            reason_str = f" ({alt.reason})" if alt.reason else ""
            lines.append(f"- [{sel_mark}] {alt.candidate_id} ({alt.candidate_type}{score_str}{eta_str}){reason_str}")

    return "\n".join(lines)
