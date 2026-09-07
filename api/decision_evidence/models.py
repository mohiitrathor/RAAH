"""
RAAH Decision Evidence Models (M13 Phase 3)
===========================================

Versioned, immutable Pydantic schemas representing structured evidence captured
from actual operational decisions (Initial Dispatch, Redirection, Fleet Repositioning,
and Hospital Diversion).

Strict Invariant:
Never populate unavailable fields with speculative defaults.
Explicitly distinguish unavailable information from evaluated information.
"""

from enum import Enum
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict


class DecisionType(str, Enum):
    INITIAL_DISPATCH = "INITIAL_DISPATCH"
    HOSPITAL_REDIRECTION = "HOSPITAL_REDIRECTION"
    FLEET_REPOSITION = "FLEET_REPOSITION"
    HOSPITAL_DIVERSION = "HOSPITAL_DIVERSION"


class ConstraintEvaluation(BaseModel):
    """
    Evaluated constraint status.
    """
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Constraint identifier or description")
    satisfied: bool = Field(..., description="Whether constraint evaluated to True (passed)")
    details: Optional[str] = Field(None, description="Operational detail or measured value")


class CandidateAlternative(BaseModel):
    """
    Candidate alternative evaluated alongside selected action.
    Only populated if the underlying engine genuinely evaluated and exposed it.
    """
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(..., description="Entity or action identifier")
    candidate_type: str = Field(..., description="Type of candidate (ambulance, hospital, zone)")
    selected: bool = Field(False, description="Whether this candidate was the chosen one")
    score: Optional[float] = Field(None, description="Evaluation score if computed")
    eta_minutes: Optional[float] = Field(None, description="Estimated arrival time if computed")
    reason: Optional[str] = Field(None, description="Selection or rejection rationale")


class DecisionEvidenceRecord(BaseModel):
    """
    Authoritative, versioned evidence record captured from real decision execution.
    """
    model_config = ConfigDict(frozen=True)

    evidence_schema_version: str = Field("1.0.0", description="Semantic schema version")
    evidence_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique evidence record ID"
    )
    decision_type: DecisionType = Field(..., description="Classification of the decision")
    action: str = Field(..., description="Action taken (e.g. DISPATCH_AMBULANCE, REDIRECT_PATIENT)")
    
    incident_id: Optional[int] = Field(None, description="Authoritative incident ID if applicable")
    sim_time: int = Field(0, description="Simulation tick/time when decision occurred")
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO8601 wall-clock timestamp"
    )
    
    # Policy Context
    policy_mode: Optional[str] = Field(None, description="Autonomy / policy mode active at decision time")
    policy_version: Optional[str] = Field(None, description="Policy version if available")
    
    # Clinical Context (from ML Model)
    severity: Optional[str] = Field(None, description="ML predicted severity (Critical, Emergency, etc.)")
    severity_source: Optional[str] = Field(None, description="Source of severity (e.g. ML model path)")
    confidence: Optional[float] = Field(None, description="ML model confidence score")
    priority: Optional[str] = Field(None, description="Priority classification (P1-P5)")
    patient_condition: Optional[str] = Field(None, description="Reported chief complaint / condition")
    
    # Selected Entities
    selected_ambulance_id: Optional[str] = Field(None, description="Assigned ambulance ID")
    selected_ambulance_type: Optional[str] = Field(None, description="Assigned ambulance type/capability")
    selected_hospital_id: Optional[str] = Field(None, description="Assigned hospital ID")
    selected_hospital_type: Optional[str] = Field(None, description="Assigned hospital facility type")
    
    # Physical / Route Metrics
    eta_minutes: Optional[float] = Field(None, description="Expected transit time in minutes")
    distance_km: Optional[float] = Field(None, description="Travel distance in kilometers")
    
    # Receiving Facility Metrics
    hospital_available_beds: Optional[int] = Field(None, description="Available beds at decision time")
    hospital_available_icu: Optional[int] = Field(None, description="Available ICU beds at decision time")
    hospital_suitability: Optional[int] = Field(None, description="Hospital suitability flag (1 or 0)")
    
    # Constraints & Alternatives
    constraints: List[ConstraintEvaluation] = Field(
        default_factory=list,
        description="Operational constraints evaluated"
    )
    alternatives: List[CandidateAlternative] = Field(
        default_factory=list,
        description="Candidate alternatives genuinely evaluated"
    )
    alternatives_available: bool = Field(
        False,
        description="Whether alternative candidates were exposed by the underlying engine"
    )
    
    # Scores & Additional Metrics
    scores: Dict[str, float] = Field(
        default_factory=dict,
        description="Evaluated decision scores (e.g. hospital score, recommendation score)"
    )
    
    # Traceability
    correlation_id: Optional[str] = Field(None, description="Correlation or request tracking ID")
    source_decision_id: Optional[str] = Field(None, description="Underlying system decision ID")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional non-speculative factual context"
    )
