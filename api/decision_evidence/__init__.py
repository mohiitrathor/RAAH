"""
RAAH Decision Evidence Subsystem (M13 Phase 3)
==============================================

Public interface for the Explainable Decision Layer evidence backbone.
"""

from api.decision_evidence.models import (
    DecisionType,
    ConstraintEvaluation,
    CandidateAlternative,
    DecisionEvidenceRecord,
)
from api.decision_evidence.formatter import format_explanation
from api.decision_evidence.recorder import EvidenceStore

# Singleton in-memory evidence store instance
evidence_store = EvidenceStore(max_capacity=1000)

__all__ = [
    "DecisionType",
    "ConstraintEvaluation",
    "CandidateAlternative",
    "DecisionEvidenceRecord",
    "format_explanation",
    "EvidenceStore",
    "evidence_store",
]
