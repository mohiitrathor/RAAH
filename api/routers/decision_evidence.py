"""
RAAH Decision Evidence / Explanation API Router (M13 Phase 2)
============================================================

Read-only view over the authoritative Decision Evidence Backbone.

Strict Invariants:
1. DispatchState remains the sole authoritative live state.
2. The Explanation API is strictly observational and read-only.
3. It does NOT dispatch, reroute, reposition fleet, modify hospitals, change policy,
   change severity, or mutate DispatchState.
4. Zero LLM, zero speculation, zero fabricated explanations/alternatives.
5. Deterministic output: same evidence -> identical explanation.
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import AuthenticatedUser, Permission, require_permission
from api.decision_evidence import evidence_store, format_explanation
from api.schemas.decision_evidence import (
    DecisionExplanationResponse,
    IncidentEvidenceResponse,
    RecentEvidenceResponse,
)

logger = logging.getLogger("raah.decision_evidence.api")

router = APIRouter()


@router.get(
    "/recent",
    response_model=RecentEvidenceResponse,
    summary="List recent decision evidence records",
    description="Retrieve recent decision evidence records and explanations (newest first).",
)
def list_recent_evidence(
    limit: int = Query(default=50, ge=1, le=500, description="Max records to return"),
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
) -> RecentEvidenceResponse:
    """Retrieve recent decision evidence and deterministic explanations."""
    try:
        records = evidence_store.list_recent(limit=limit)
        items = [
            DecisionExplanationResponse(
                evidence=r,
                explanation=format_explanation(r),
            )
            for r in records
        ]
        return RecentEvidenceResponse(count=len(items), records=items)
    except Exception as ex:
        logger.error("Failed to retrieve recent decision evidence: %s", ex, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve decision evidence.",
        )


@router.get(
    "/incident/{incident_id}",
    response_model=IncidentEvidenceResponse,
    summary="Get decision evidence for an incident",
    description="Retrieve all decision evidence records for an incident in deterministic chronological order.",
)
def get_incident_evidence(
    incident_id: int,
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
) -> IncidentEvidenceResponse:
    """
    Retrieve decision evidence for an incident in deterministic chronological order.
    Returns empty records list if no evidence exists for the incident.
    """
    try:
        records = evidence_store.get_by_incident(incident_id)
        # Deterministic chronological sort: sim_time asc, timestamp_iso asc, evidence_id asc
        sorted_records = sorted(
            records,
            key=lambda r: (r.sim_time, r.timestamp_iso, r.evidence_id),
        )
        items = [
            DecisionExplanationResponse(
                evidence=r,
                explanation=format_explanation(r),
            )
            for r in sorted_records
        ]
        return IncidentEvidenceResponse(incident_id=incident_id, records=items)
    except Exception as ex:
        logger.error("Failed to retrieve evidence for incident %s: %s", incident_id, ex, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve incident decision evidence.",
        )


@router.get(
    "/{evidence_id}",
    response_model=DecisionExplanationResponse,
    summary="Get single decision evidence record by ID",
    description="Retrieve a complete structured evidence record and deterministic explanation by evidence ID.",
)
def get_decision_evidence(
    evidence_id: str,
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
) -> DecisionExplanationResponse:
    """
    Retrieve single decision evidence record and its deterministic explanation.
    Raises 404 if evidence ID does not exist.
    """
    try:
        record = evidence_store.get(evidence_id)
    except Exception as ex:
        logger.error("Error retrieving evidence record %s: %s", evidence_id, ex, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve decision evidence.",
        )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Decision evidence record '{evidence_id}' not found.",
        )

    return DecisionExplanationResponse(
        evidence=record,
        explanation=format_explanation(record),
    )
