"""
RAAH Decision Evidence Response Schemas (M13 Phase 2)
=====================================================

Versioned, typed Pydantic schemas for the read-only Explanation API.

Strict Invariant:
Never expose internal object state, secrets, or unbounded raw dictionaries.
All models provide explicit typed definitions.
"""

from typing import List
from pydantic import BaseModel, Field
from api.decision_evidence.models import DecisionEvidenceRecord


class DecisionExplanationResponse(BaseModel):
    """
    Structured evidence record paired with its deterministic human-readable explanation.
    """
    evidence: DecisionEvidenceRecord = Field(
        ...,
        description="Authoritative structured decision evidence captured from execution",
    )
    explanation: str = Field(
        ...,
        description="Deterministic human-readable operational explanation",
    )


class IncidentEvidenceResponse(BaseModel):
    """
    All decision evidence records associated with a specific incident, in stable chronological order.
    """
    incident_id: int = Field(
        ...,
        description="Authoritative incident identifier",
    )
    records: List[DecisionExplanationResponse] = Field(
        default_factory=list,
        description="Chronological list of decision evidence and explanations for this incident",
    )


class RecentEvidenceResponse(BaseModel):
    """
    Collection of recent decision evidence records.
    """
    count: int = Field(
        ...,
        description="Number of records returned",
    )
    records: List[DecisionExplanationResponse] = Field(
        default_factory=list,
        description="Recent decision evidence records",
    )
