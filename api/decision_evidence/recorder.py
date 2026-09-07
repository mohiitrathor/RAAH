"""
RAAH Thread-Safe Evidence Store (M13 Phase 3)
==============================================

In-memory, bounded ring-buffer storing structured decision evidence records.

Strict Invariants:
- Observational auditing infrastructure only.
- Does NOT mutate DispatchState.
- Does NOT alter decisions or API responses.
- Bounded memory footprint (FIFO ring buffer).
- Thread-safe concurrency.
- FAIL-OPEN: Any recording error logs a warning and returns None without propagating exceptions.
"""

from collections import deque
import logging
import threading
from typing import Dict, List, Any, Optional

from api.decision_evidence.models import (
    DecisionEvidenceRecord,
    DecisionType,
    ConstraintEvaluation,
    CandidateAlternative,
)

logger = logging.getLogger("raah.decision_evidence")


class EvidenceStore:
    """
    Bounded, thread-safe in-memory store for operational decision evidence records.
    """

    def __init__(self, max_capacity: int = 1000):
        self._max_capacity = max_capacity
        self._lock = threading.Lock()
        self._buffer: deque[str] = deque(maxlen=max_capacity)
        self._records: Dict[str, DecisionEvidenceRecord] = {}
        self._by_incident: Dict[int, List[str]] = {}

    def record(self, record: DecisionEvidenceRecord) -> Optional[DecisionEvidenceRecord]:
        """
        Store a pre-constructed DecisionEvidenceRecord in a thread-safe, bounded manner.
        Fails open on any error.
        """
        try:
            with self._lock:
                # Evict oldest record if at capacity
                if len(self._buffer) == self._max_capacity:
                    evicted_id = self._buffer.popleft()
                    old_rec = self._records.pop(evicted_id, None)
                    if old_rec and old_rec.incident_id is not None:
                        inc_list = self._by_incident.get(old_rec.incident_id)
                        if inc_list and evicted_id in inc_list:
                            inc_list.remove(evicted_id)
                            if not inc_list:
                                self._by_incident.pop(old_rec.incident_id, None)

                self._buffer.append(record.evidence_id)
                self._records[record.evidence_id] = record

                if record.incident_id is not None:
                    if record.incident_id not in self._by_incident:
                        self._by_incident[record.incident_id] = []
                    self._by_incident[record.incident_id].append(record.evidence_id)

            return record
        except Exception as err:
            logger.warning(f"Failed to record decision evidence (failing open): {err}")
            return None

    def record_dispatch(
        self,
        dispatch_result: Dict[str, Any],
        sim_time: int,
        policy_mode: Optional[str] = None,
        policy_version: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[DecisionEvidenceRecord]:
        """
        Record evidence from a real initial dispatch execution result.
        Fails open if recording fails.
        """
        try:
            patient_data = dispatch_result.get("patient") or {}
            ambulance_data = dispatch_result.get("ambulance") or {}
            hospital_data = dispatch_result.get("hospital") or {}

            incident_id = dispatch_result.get("incident_id")
            severity = patient_data.get("predicted_severity")
            confidence = patient_data.get("confidence")
            priority = patient_data.get("priority")
            condition = patient_data.get("condition")

            # Evaluate real constraints from dispatch result
            constraints: List[ConstraintEvaluation] = []
            if ambulance_data:
                cap_match = bool(ambulance_data.get("capability_match", False))
                is_fallback = bool(ambulance_data.get("fallback", False))
                constraints.append(
                    ConstraintEvaluation(
                        name="AMBULANCE_CAPABILITY_MATCH",
                        satisfied=cap_match,
                        details="Fallback unit assigned" if is_fallback else "Exact or higher capability matched",
                    )
                )
                constraints.append(
                    ConstraintEvaluation(
                        name="FLEET_AVAILABILITY",
                        satisfied=True,
                        details=f"Assigned unit {ambulance_data.get('ambulance_id')}",
                    )
                )
            else:
                constraints.append(
                    ConstraintEvaluation(
                        name="FLEET_AVAILABILITY",
                        satisfied=False,
                        details="No available ambulance found in fleet",
                    )
                )

            if hospital_data:
                suitability = hospital_data.get("suitability", 0)
                beds = hospital_data.get("available_beds", 0)
                icu = hospital_data.get("available_icu", 0)
                constraints.append(
                    ConstraintEvaluation(
                        name="HOSPITAL_SUITABILITY",
                        satisfied=bool(suitability == 1),
                        details=f"Available Beds: {beds}, Available ICU: {icu}",
                    )
                )
            else:
                constraints.append(
                    ConstraintEvaluation(
                        name="HOSPITAL_SUITABILITY",
                        satisfied=False,
                        details="No hospital available with required capacity",
                    )
                )

            record = DecisionEvidenceRecord(
                decision_type=DecisionType.INITIAL_DISPATCH,
                action=f"DISPATCH_INCIDENT_{incident_id}",
                incident_id=incident_id,
                sim_time=int(sim_time),
                policy_mode=policy_mode,
                policy_version=policy_version,
                severity=severity,
                severity_source="logistic_regression_final.joblib",
                confidence=confidence,
                priority=priority,
                patient_condition=condition,
                selected_ambulance_id=ambulance_data.get("ambulance_id"),
                selected_ambulance_type=ambulance_data.get("ambulance_type"),
                selected_hospital_id=hospital_data.get("hospital_id"),
                selected_hospital_type=hospital_data.get("hospital_type"),
                eta_minutes=ambulance_data.get("eta_minutes"),
                distance_km=ambulance_data.get("distance_km"),
                hospital_available_beds=hospital_data.get("available_beds"),
                hospital_available_icu=hospital_data.get("available_icu"),
                hospital_suitability=hospital_data.get("suitability"),
                constraints=constraints,
                alternatives=[],
                alternatives_available=False,  # Engine does not expose discarded candidates
                scores={"ml_confidence": float(confidence)} if confidence is not None else {},
                correlation_id=correlation_id,
                metadata={
                    "status": dispatch_result.get("status"),
                    "traffic": ambulance_data.get("traffic"),
                    "road_condition": ambulance_data.get("road_condition"),
                },
            )

            return self.record(record)
        except Exception as err:
            logger.warning(f"Failed to record dispatch evidence (failing open): {err}")
            return None

    def record_redirection(
        self,
        decision_payload: Dict[str, Any],
        sim_time: int,
        policy_mode: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[DecisionEvidenceRecord]:
        """
        Record evidence from a real hospital redirection result.
        Fails open if recording fails.
        """
        try:
            incident_id = decision_payload.get("incident_id")
            orig_hosp = decision_payload.get("original_hospital")
            new_hosp = decision_payload.get("new_hospital")
            eta_before = decision_payload.get("eta_before")
            eta_after = decision_payload.get("eta_after")
            eta_saved = decision_payload.get("eta_saved")
            eta_imp_pct = decision_payload.get("eta_improvement_percent")
            decision_str = str(decision_payload.get("decision", "UNKNOWN"))

            constraints: List[ConstraintEvaluation] = []
            if eta_saved is not None:
                passed_threshold = bool(eta_saved >= 5.0 and (eta_imp_pct or 0) >= 20.0)
                constraints.append(
                    ConstraintEvaluation(
                        name="REDIRECTION_ETA_THRESHOLD",
                        satisfied=passed_threshold,
                        details=f"Saved {eta_saved:.1f}m ({eta_imp_pct:.1f}%)" if eta_imp_pct is not None else f"Saved {eta_saved:.1f}m",
                    )
                )

            record = DecisionEvidenceRecord(
                decision_type=DecisionType.HOSPITAL_REDIRECTION,
                action=f"{decision_str}:{orig_hosp}->{new_hosp}",
                incident_id=incident_id,
                sim_time=int(sim_time),
                policy_mode=policy_mode,
                severity=decision_payload.get("severity"),
                selected_ambulance_id=decision_payload.get("ambulance_id"),
                selected_hospital_id=new_hosp,
                eta_minutes=eta_after,
                constraints=constraints,
                alternatives=[],
                alternatives_available=False,
                scores={
                    "eta_saved_minutes": float(eta_saved) if eta_saved is not None else 0.0,
                    "eta_improvement_percent": float(eta_imp_pct) if eta_imp_pct is not None else 0.0,
                },
                correlation_id=correlation_id,
                metadata={
                    "reason": decision_payload.get("reason"),
                    "original_hospital": orig_hosp,
                    "eta_before": eta_before,
                    "eta_after": eta_after,
                    "decision": decision_str,
                },
            )

            return self.record(record)
        except Exception as err:
            logger.warning(f"Failed to record redirection evidence (failing open): {err}")
            return None

    def record_optimization(
        self,
        recommendation: Any,
        execution_result: Any,
        sim_time: int,
        policy_mode: Optional[str] = None,
        policy_version: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[DecisionEvidenceRecord]:
        """
        Record evidence from an authoritative optimization execution.
        Fails open if recording fails.
        """
        try:
            rec_dict = recommendation.to_dict() if hasattr(recommendation, "to_dict") else dict(recommendation)
            exec_dict = execution_result.to_dict() if hasattr(execution_result, "to_dict") else dict(execution_result)

            dec_type_raw = rec_dict.get("decision_type", "FLEET_REPOSITION")
            if dec_type_raw == "HOSPITAL_DIVERSION":
                dec_type = DecisionType.HOSPITAL_DIVERSION
            else:
                dec_type = DecisionType.FLEET_REPOSITION

            # Extract alternatives from recommendation explanation if genuinely present
            alternatives: List[CandidateAlternative] = []
            exp_data = rec_dict.get("explanation") or {}
            raw_alts = exp_data.get("alternatives") or []
            for alt in raw_alts:
                alternatives.append(
                    CandidateAlternative(
                        candidate_id=str(alt.get("action") or alt.get("target") or "ALT_ACTION"),
                        candidate_type=dec_type.value,
                        selected=False,
                        score=float(alt.get("score")) if alt.get("score") is not None else None,
                        reason=alt.get("reason"),
                    )
                )

            # Selected candidate
            alternatives.append(
                CandidateAlternative(
                    candidate_id=str(rec_dict.get("recommendation_id")),
                    candidate_type=dec_type.value,
                    selected=True,
                    score=float(rec_dict.get("score")) if rec_dict.get("score") is not None else None,
                    reason=exp_data.get("expected_benefit"),
                )
            )

            constraints = [
                ConstraintEvaluation(name=str(c), satisfied=True)
                for c in rec_dict.get("candidate_action", {}).get("constraints", [])
            ]

            record = DecisionEvidenceRecord(
                decision_type=dec_type,
                action=f"EXECUTE_{dec_type.value}_{rec_dict.get('recommendation_id')}",
                sim_time=int(sim_time),
                policy_mode=policy_mode,
                policy_version=policy_version,
                confidence=float(rec_dict.get("candidate_action", {}).get("confidence", 1.0)),
                selected_entity_id=str(exec_dict.get("execution_id")),
                constraints=constraints,
                alternatives=alternatives,
                alternatives_available=bool(len(raw_alts) > 0),
                scores={
                    "recommendation_score": float(rec_dict.get("score", 0.0)),
                },
                correlation_id=correlation_id,
                source_decision_id=rec_dict.get("recommendation_id"),
                metadata={
                    "status": exec_dict.get("status"),
                    "affected_entities": exec_dict.get("affected_entities"),
                    "target": rec_dict.get("candidate_action", {}).get("target"),
                },
            )

            return self.record(record)
        except Exception as err:
            logger.warning(f"Failed to record optimization evidence (failing open): {err}")
            return None

    def get(self, evidence_id: str) -> Optional[DecisionEvidenceRecord]:
        """Lookup an evidence record by ID."""
        with self._lock:
            return self._records.get(evidence_id)

    def get_by_incident(self, incident_id: int) -> List[DecisionEvidenceRecord]:
        """Lookup all evidence records associated with an incident."""
        with self._lock:
            ids = self._by_incident.get(incident_id, [])
            return [self._records[eid] for eid in ids if eid in self._records]

    def list_recent(self, limit: int = 50) -> List[DecisionEvidenceRecord]:
        """List most recent evidence records (newest first)."""
        with self._lock:
            recent_ids = list(reversed(self._buffer))[:limit]
            return [self._records[eid] for eid in recent_ids if eid in self._records]

    def clear(self):
        """Clear all records (primarily for testing)."""
        with self._lock:
            self._buffer.clear()
            self._records.clear()
            self._by_incident.clear()
