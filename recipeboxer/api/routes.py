"""FastAPI REST endpoint routes for RecipeBoxer pipeline and HITL operations."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from recipeboxer.agent.session import (
    PipelineSession,
    SessionManager,
    get_session_manager,
)
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval
from recipeboxer.models.pipeline import (
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineState,
    PipelineStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_manager(request: Request) -> SessionManager:
    """Dependency injection helper to retrieve SessionManager from application state."""
    return getattr(request.app.state, "session_manager", get_session_manager())


# ============================================================================
# 1. POST /api/pipeline/start
# ============================================================================
@router.post(
    "/pipeline/start",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_200_OK,
    summary="Launch Recipe Digitization Pipeline",
)
async def start_pipeline(
    payload: PipelineStartRequest,
    manager: SessionManager = Depends(get_manager),
) -> PipelineStartResponse:
    """Validates scans directory, initializes a new session, and launches background processing."""
    scans_dir = payload.scans_dir or payload.directory_path
    if not scans_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scans_dir or directory_path must be provided",
        )

    scans_path = Path(scans_dir)
    if not scans_path.exists() or not scans_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid scans directory path: directory does not exist",
        )

    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"}
    matching_files = [
        f for f in scans_path.iterdir() if f.is_file() and f.suffix.lower() in valid_exts
    ]
    if not matching_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid card scans found in directory",
        )

    # Instantiate and register session
    session = manager.create_session(config=payload)

    # Launch background asynchronous pipeline task
    session.pipeline_task = asyncio.create_task(session.run())

    # Yield control briefly to allow task initialization
    await asyncio.sleep(0.01)

    return PipelineStartResponse(
        session_id=session.session_id,
        status=session.state,
        total_scans_found=len(matching_files),
        mode=payload.mode,
        message="Pipeline initiated successfully",
    )


# ============================================================================
# 2. GET /api/status
# ============================================================================
@router.get(
    "/status",
    response_model=PipelineStatus,
    status_code=status.HTTP_200_OK,
    summary="Get Pipeline Progress and Telemetry",
)
async def get_pipeline_status(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    manager: SessionManager = Depends(get_manager),
) -> PipelineStatus:
    """Returns the current state, progress metrics, card counts, and elapsed time."""
    session = manager.get_session(session_id)
    if session is None:
        return PipelineStatus(
            session_id=session_id or "none",
            status=PipelineState.IDLE,
            error=None,
        )

    return session.get_status()


# ============================================================================
# 3. GET /api/hitl/pending
# ============================================================================
@router.get(
    "/hitl/pending",
    status_code=status.HTTP_200_OK,
    summary="Retrieve Proposed Correlations Awaiting Human Review",
)
async def get_hitl_pending(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    manager: SessionManager = Depends(get_manager),
) -> Dict[str, Any]:
    """Retrieves proposals awaiting human review, card pairs, scores, and unassigned cards."""
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.state != PipelineState.HITL_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pipeline is in state '{session.state.value}', not 'HITL_PENDING'",
        )

    unassigned_dump = []
    for u in session.unassigned_cards:
        if hasattr(u, "model_dump"):
            unassigned_dump.append(u.model_dump())
        elif isinstance(u, dict):
            unassigned_dump.append(u)
        else:
            unassigned_dump.append({"card_id": str(u)})

    return {
        "session_id": session.session_id,
        "status": session.state.value,
        "proposed_pairs": [p.model_dump() for p in session.proposals],
        "unassigned_cards": unassigned_dump,
        "total_proposals": len(session.proposals),
        "total_unassigned": len(session.unassigned_cards),
    }


# ============================================================================
# 4. POST /api/hitl/approve
# ============================================================================
@router.post(
    "/hitl/approve",
    status_code=status.HTTP_200_OK,
    summary="Submit Human Review Decisions and Resume Pipeline",
)
async def approve_hitl(
    payload: ApprovalPayload,
    manager: SessionManager = Depends(get_manager),
) -> Dict[str, Any]:
    """Unblocks the approval_event, transitions to EXTRACTING, and resumes recipe transcription."""
    session = manager.get_session(payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.state != PipelineState.HITL_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve session in state '{session.state.value}'",
        )

    try:
        session.submit_approval(
            approvals=payload.approvals,
            rejected_card_ids=payload.rejected_card_ids,
            continue_pipeline=payload.continue_pipeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Give background task time to transition into EXTRACTING
    await asyncio.sleep(0.01)

    return {
        "session_id": session.session_id,
        "status": session.state.value,
        "approved_count": len(payload.approvals),
        "rejected_count": len(payload.rejected_card_ids),
        "message": "HITL approval received. Pipeline resumed extraction.",
    }


# ============================================================================
# 5. GET /api/export/csv
# ============================================================================
@router.get(
    "/export/csv",
    status_code=status.HTTP_200_OK,
    summary="Download Plan To Eat RFC 4180 CSV Export",
)
async def export_csv(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    manager: SessionManager = Depends(get_manager),
) -> Response:
    """Returns the generated Plan To Eat CSV file as text/csv attachment with RFC 4180 headers."""
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.state != PipelineState.COMPLETED or not session.csv_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV export not ready: pipeline has not completed extraction",
        )

    if session.csv_output_path and Path(session.csv_output_path).exists():
        return FileResponse(
            path=session.csv_output_path,
            media_type="text/csv; charset=utf-8",
            filename="recipeboxer_export.csv",
            headers={"Content-Disposition": 'attachment; filename="recipeboxer_export.csv"'},
        )

    return Response(
        content=session.csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="recipeboxer_export.csv"'},
    )
