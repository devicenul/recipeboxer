"""FastAPI REST endpoint routes for RecipeBoxer pipeline, HITL, session management, and exports."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from pydantic import BaseModel, Field

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from recipeboxer.agent.session import (
    PipelineSession,
    SessionManager,
    get_session_manager,
)
from recipeboxer.config import Settings, get_settings
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import JsonLdExporter, export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import MarkdownExporter, export_recipes_to_markdown
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter, export_recipes_to_plan_to_eat_csv
from recipeboxer.ingestion.scanner import VALID_IMAGE_EXTENSIONS, ScannedCard, get_image_mime_type, validate_and_load_card
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.models.hitl import (
    ApprovalPayload,
    CandidateListResponse,
    CandidateScoreBreakdown,
    CardPairApproval,
    CorrelationCandidate,
    HITLDecision,
)
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineState,
    PipelineStatus,
)
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import SessionDetail, SessionSummary
from recipeboxer.sources import (
    ListContainersResponse,
    ListItemsResponse,
    SourceConnectionStatus,
    SourceContainer,
    SourceItem,
    SourceProviderConfig,
    get_source_provider,
    list_available_providers,
)
from recipeboxer.storage.session_store import SessionStore, get_session_store
from recipeboxer.vision.heuristics import (
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_stain_similarity,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_manager(request: Request) -> SessionManager:
    """Dependency injection helper to retrieve SessionManager from application state."""
    return getattr(request.app.state, "session_manager", get_session_manager())


def get_store(request: Request) -> SessionStore:
    """Dependency injection helper to retrieve SessionStore from application state."""
    return getattr(request.app.state, "session_store", get_session_store())


def get_app_settings(request: Request) -> Settings:
    """Dependency injection helper to retrieve Settings from application state."""
    return getattr(request.app.state, "settings", get_settings())


# ============================================================================
# 1. Pipeline Start & Ingestion
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
    store: SessionStore = Depends(get_store),
) -> PipelineStartResponse:
    """Validates scans directory or remote source provider, initializes session, and launches processing."""
    is_remote = bool(payload.source_provider and payload.source_provider.lower() not in ("local", ""))

    if is_remote:
        provider_name = payload.source_provider or "unknown"
        try:
            cfg_obj = None
            if payload.source_config:
                cfg_obj = SourceProviderConfig(**payload.source_config)
            provider = get_source_provider(provider_name, config=cfg_obj)

            is_connected = await provider.test_connection()
            if not is_connected and provider.capabilities.requires_auth:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Source provider '{provider_name}' connection failed or unauthorized",
                )

            card_count = 0
            if payload.source_item_ids:
                card_count = len(payload.source_item_ids)
            else:
                items_res = await provider.list_items(container_id=payload.source_container_id)
                card_count = len(items_res.items)

            if card_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No valid card scans found in source container '{payload.source_container_id or 'all'}'",
                )

            session = manager.create_session(config=payload)
            store.save_session(
                session_id=session.session_id,
                state=session.state,
                mode=payload.mode.value if hasattr(payload.mode, "value") else str(payload.mode),
                folder_path=f"source://{provider_name}/{payload.source_container_id or ''}",
                config=payload.model_dump(),
                card_count=card_count,
                recipe_count=0,
            )

            session.pipeline_task = asyncio.create_task(session.run())
            await asyncio.sleep(0.01)

            return PipelineStartResponse(
                session_id=session.session_id,
                status=session.state,
                total_scans_found=card_count,
                mode=payload.mode,
                message="Pipeline initiated successfully from remote source",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to start pipeline from source provider '{provider_name}': {exc}",
            )

    local_cfg_path = payload.source_config.get("local_path") if payload.source_config else None
    scans_dir = payload.scans_dir or payload.directory_path or local_cfg_path
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

    matching_files = [
        f for f in scans_path.iterdir() if f.is_file() and f.suffix.lower() in VALID_IMAGE_EXTENSIONS
    ]
    if not matching_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid card scans found in directory",
        )

    # Instantiate and register session
    session = manager.create_session(config=payload)

    # Persist initial session record to SQLite
    store.save_session(
        session_id=session.session_id,
        state=session.state,
        mode=payload.mode.value if hasattr(payload.mode, "value") else str(payload.mode),
        folder_path=str(scans_path),
        config=payload.model_dump(),
        card_count=len(matching_files),
        recipe_count=0,
    )

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
# 1b. Cloud & External Source Providers
# ============================================================================

@router.get(
    "/sources/providers",
    response_model=List[SourceConnectionStatus],
    status_code=status.HTTP_200_OK,
    summary="List Source Providers and Connection Statuses",
)
async def list_sources() -> List[SourceConnectionStatus]:
    """Retrieve all registered source providers (Local, Google Photos, Google Drive, S3, GCS) and connectivity."""
    return await list_available_providers()


@router.post(
    "/sources/{provider_id}/connect",
    response_model=SourceConnectionStatus,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and Connect Source Provider",
)
async def connect_source(
    provider_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> SourceConnectionStatus:
    """Validate credentials and establish active connection to a source provider."""
    try:
        config_data = payload or {}
        config_data["provider_id"] = provider_id
        config = SourceProviderConfig(**config_data)
        provider = get_source_provider(provider_id, config=config)
        _ = await provider.test_connection()
        return await provider.get_connection_status()
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source provider '{provider_id}' not found",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed connecting to provider '{provider_id}': {exc}",
        )


@router.get(
    "/sources/{provider_id}/containers",
    response_model=ListContainersResponse,
    status_code=status.HTTP_200_OK,
    summary="List Source Containers (Albums / Folders / Buckets)",
)
async def list_source_containers(
    provider_id: str,
    page_size: int = Query(50, ge=1, le=100),
    page_token: Optional[str] = Query(None),
    parent_id: Optional[str] = Query(None),
) -> ListContainersResponse:
    """Browse albums, directories, or prefixes for a source provider."""
    try:
        provider = get_source_provider(provider_id)
        return await provider.list_containers(
            page_size=page_size, page_token=page_token, parent_id=parent_id
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source provider '{provider_id}' not found",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed listing containers for '{provider_id}': {exc}",
        )


@router.get(
    "/sources/{provider_id}/items",
    response_model=ListItemsResponse,
    status_code=status.HTTP_200_OK,
    summary="List Source Items with Pagination and Filtering",
)
async def list_source_items(
    provider_id: str,
    container_id: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    page_size: int = Query(50, ge=1, le=100),
    page_token: Optional[str] = Query(None),
) -> ListItemsResponse:
    """Preview or search media items within a remote source provider or container."""
    try:
        provider = get_source_provider(provider_id)
        return await provider.list_items(
            container_id=container_id,
            query=query,
            category=category,
            page_size=page_size,
            page_token=page_token,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source provider '{provider_id}' not found",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed listing items for '{provider_id}': {exc}",
        )


@router.get(
    "/sources/{provider_id}/thumbnail/{item_id:path}",
    summary="Fetch Source Item Thumbnail Preview",
)
async def get_source_thumbnail(
    provider_id: str,
    item_id: str,
    max_size: int = Query(256, ge=32, le=1024),
) -> Response:
    """Retrieve image thumbnail bytes for preview in source picker dialog."""
    try:
        provider = get_source_provider(provider_id)
        thumb_bytes = await provider.fetch_thumbnail(item_id, max_size=max_size)
        if not thumb_bytes:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thumbnail not available for item '{item_id}'",
            )
        return Response(content=thumb_bytes, media_type="image/jpeg")
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source provider '{provider_id}' not found",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed retrieving thumbnail for '{item_id}': {exc}",
        )


@router.post(
    "/cards/upload",
    status_code=status.HTTP_200_OK,
    summary="Upload Scanned Card Images (Multipart)",
)
@router.post(
    "/ingest/upload",
    status_code=status.HTTP_200_OK,
    summary="Upload Scanned Card Images (Multipart Alias)",
)
async def upload_cards(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None),
    mode: Optional[str] = Form("parallel"),
    max_concurrency: Optional[int] = Form(5),
    slow_batch_size: Optional[int] = Form(2),
    slow_batch_interval_seconds: Optional[float] = Form(2.0),
    model: Optional[str] = Form("gemini-3.7-flash"),
    default_tags: Optional[str] = Form(None),
    auto_start: Optional[bool] = Form(False),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> Dict[str, Any]:
    """Accepts multipart card image uploads, validates each with PIL, and registers a session."""
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files uploaded")

    parsed_default_tags: List[str] = []
    if default_tags:
        try:
            parsed = json.loads(default_tags)
            if isinstance(parsed, list):
                parsed_default_tags = [str(t).strip() for t in parsed if str(t).strip()]
            elif isinstance(parsed, str):
                parsed_default_tags = [t.strip() for t in parsed.split(",") if t.strip()]
        except Exception:
            parsed_default_tags = [t.strip() for t in default_tags.split(",") if t.strip()]

    sid = session_id or f"sess_{uuid.uuid4().hex[:8]}"
    upload_dir = settings.cache_dir / "uploads" / sid
    upload_dir.mkdir(parents=True, exist_ok=True)

    valid_cards: List[ScannedCard] = []
    saved_paths: List[Path] = []

    for upload_file in files:
        filename = os.path.basename(upload_file.filename or f"card_{uuid.uuid4().hex[:6]}.jpg")
        dest_path = upload_dir / filename
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)

        try:
            card = validate_and_load_card(dest_path)
            valid_cards.append(card)
            saved_paths.append(dest_path)
        except Exception as exc:
            logger.warning(f"Skipping invalid uploaded card image '{filename}': {exc}")
            if dest_path.exists():
                dest_path.unlink()

    if not valid_cards:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="None of the uploaded files were valid recipe card images",
        )

    # Get or create PipelineSession
    session = manager.get_session(sid)
    if session is None:
        ingest_mode = IngestionMode.PARALLEL if mode != "slow-batch" else IngestionMode.SLOW_BATCH
        config = PipelineStartRequest(
            scans_dir=str(upload_dir),
            mode=ingest_mode,
            max_concurrency=max_concurrency or 5,
            slow_batch_size=slow_batch_size or 2,
            slow_batch_interval_seconds=slow_batch_interval_seconds or 2.0,
            model=model or "gemini-3.7-flash",
            default_tags=parsed_default_tags,
        )
        session = manager.create_session(config=config, session_id=sid)
    elif parsed_default_tags and hasattr(session, "config"):
        if hasattr(session.config, "default_tags"):
            session.config.default_tags = list(set(session.config.default_tags + parsed_default_tags))
        elif isinstance(session.config, dict):
            session.config["default_tags"] = list(set(session.config.get("default_tags", []) + parsed_default_tags))

    session.cards.extend(valid_cards)
    session.scanned_paths.extend(saved_paths)
    session.progress_metrics.total_scans = len(session.cards)

    # Persist cards into SQLite
    store.save_cards(sid, valid_cards)
    store.save_session(
        session_id=sid,
        state=session.state,
        mode=mode,
        folder_path=str(upload_dir),
        card_count=len(session.cards),
    )

    if auto_start:
        session.pipeline_task = asyncio.create_task(session.run())
        await asyncio.sleep(0.01)

    return {
        "session_id": sid,
        "card_ids": [c.card_id for c in valid_cards],
        "count": len(valid_cards),
        "folder_path": str(upload_dir),
        "message": f"Successfully uploaded and validated {len(valid_cards)} card scans",
    }


# ============================================================================
# 2. Status & Telemetry
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
    store: SessionStore = Depends(get_store),
) -> PipelineStatus:
    """Returns the current state, progress metrics, card counts, and elapsed time."""
    session = manager.get_session(session_id)
    if session is not None:
        return session.get_status()

    # Fallback to SQLite persistence
    if session_id:
        detail = store.get_session_detail(session_id)
        if detail:
            return PipelineStatus(
                session_id=detail.session_id,
                status=detail.status if isinstance(detail.status, PipelineState) else PipelineState(detail.status),
                mode=IngestionMode(detail.mode) if detail.mode in ("parallel", "slow-batch") else IngestionMode.PARALLEL,
                progress=detail.progress,
                token_usage=detail.token_usage,
                elapsed_seconds=detail.elapsed_seconds,
                csv_output_path=detail.csv_output_path,
                error=detail.error,
            )

    return PipelineStatus(
        session_id=session_id or "none",
        status=PipelineState.IDLE,
        error=None,
    )


@router.get(
    "/events",
    summary="Server-Sent Events (SSE) Live Progress Stream",
)
async def stream_events(
    request: Request,
    session_id: Optional[str] = Query(None, description="Target session ID"),
    manager: SessionManager = Depends(get_manager),
) -> StreamingResponse:
    """Streams Server-Sent Events (SSE) broadcasting real-time session progress and state changes."""
    if session_id is not None and manager.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        last_state: Optional[str] = None
        last_extracted_count: int = 0
        last_percent: float = -1.0
        ticks = 0

        # Initial keep-alive connection handshake comment
        yield ": connected\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break

                session = manager.get_session(session_id) if session_id else None
                if session is None:
                    yield f"event: status\ndata: {json.dumps({'status': 'IDLE', 'session_id': session_id or 'none'})}\n\n"
                    yield ": ping\n\n"
                    break

                status_obj = session.get_status()
                current_state = status_obj.status.value

                # State change event
                if current_state != last_state:
                    last_state = current_state
                    yield f"event: state_change\ndata: {status_obj.model_dump_json()}\n\n"

                # Progress change event
                curr_pct = status_obj.progress.percent_complete
                if abs(curr_pct - last_percent) >= 1.0 or ticks == 0:
                    last_percent = curr_pct
                    yield f"event: progress\ndata: {status_obj.model_dump_json()}\n\n"

                # HITL ready event
                if current_state == PipelineState.HITL_PENDING.value and (ticks == 0 or current_state != last_state):
                    yield f"event: hitl_ready\ndata: {json.dumps({'session_id': session.session_id, 'proposals_count': len(session.proposals)})}\n\n"

                # Recipe extracted event
                curr_extracted = len(session.recipes)
                if curr_extracted > last_extracted_count:
                    new_recipes = session.recipes[last_extracted_count:curr_extracted]
                    for r in new_recipes:
                        yield f"event: recipe_extracted\ndata: {r.model_dump_json()}\n\n"
                    last_extracted_count = curr_extracted

                # Terminal states: emit terminal state event and finish stream
                if current_state in (PipelineState.COMPLETED.value, PipelineState.FAILED.value, PipelineState.CANCELLED.value):
                    yield f"event: {current_state.lower()}\ndata: {status_obj.model_dump_json()}\n\n"
                    break

                ticks += 1
                if ticks % 10 == 0:
                    yield ": ping\n\n"

                await asyncio.sleep(0.5)
                if await request.is_disconnected():
                    break
        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# 3. Card Image Streaming
# ============================================================================

@router.get(
    "/cards/{card_id}/image",
    summary="Stream Scanned Card Image File",
)
async def get_card_image(
    card_id: str,
    session_id: Optional[str] = Query(None, description="Optional session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> FileResponse:
    """Safely streams a scanned card image with caching headers and content-type detection."""
    # Sanitize and validate card_id input
    if not card_id or not card_id.strip():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid card ID",
        )

    if "\0" in card_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid card ID format",
        )

    image_path: Optional[Path] = None

    # 1. Search in active session(s)
    session = manager.get_session(session_id)
    target_sessions = [session] if session else manager.list_sessions()

    for sess in target_sessions:
        if sess is None:
            continue

        # Check registered cards in session
        for c in sess.cards:
            if c.card_id == card_id or c.filename == card_id or getattr(c.file_path, "name", None) == card_id:
                candidate = Path(c.file_path).resolve()
                if candidate.is_file():
                    image_path = candidate
                    break
        if image_path:
            break

        # Check scanned_paths in session
        for p in sess.scanned_paths:
            if p.name == card_id or str(p) == card_id:
                candidate = Path(p).resolve()
                if candidate.is_file():
                    image_path = candidate
                    break
        if image_path:
            break

        # Check session cache if available
        if sess.cache:
            fp = sess.cache.get_by_card_id(card_id)
            if fp and fp.image_path:
                candidate = Path(fp.image_path).resolve()
                if candidate.is_file():
                    image_path = candidate
                    break
        if image_path:
            break

    # 2. Search in SessionStore (SQLite)
    if not image_path:
        stored_path_str = store.get_card_path(card_id, session_id)
        if stored_path_str:
            candidate = Path(stored_path_str).resolve()
            if candidate.is_file():
                image_path = candidate

    # 3. Check within configured folder_path / scans_dir for legitimate scan filenames
    if not image_path:
        root_dirs: List[Path] = []

        for sess in target_sessions:
            if sess and hasattr(sess, "config"):
                cfg = sess.config
                folder = None
                if hasattr(cfg, "scans_dir") and cfg.scans_dir:
                    folder = cfg.scans_dir
                elif hasattr(cfg, "directory_path") and cfg.directory_path:
                    folder = cfg.directory_path
                elif isinstance(cfg, dict):
                    folder = cfg.get("scans_dir") or cfg.get("directory_path") or cfg.get("folder_path")
                if folder:
                    try:
                        root_dirs.append(Path(folder).resolve())
                    except Exception:
                        pass

        if session_id:
            s_row = store.get_session(session_id)
            if s_row and s_row.get("folder_path"):
                try:
                    root_dirs.append(Path(s_row["folder_path"]).resolve())
                except Exception:
                    pass
        else:
            for s_sum in store.list_sessions():
                if s_sum.folder_path:
                    try:
                        root_dirs.append(Path(s_sum.folder_path).resolve())
                    except Exception:
                        pass

        clean_filename = Path(card_id).name
        for root_dir in root_dirs:
            if root_dir.is_dir():
                safe_candidate = (root_dir / clean_filename).resolve()
                try:
                    if safe_candidate.is_file() and safe_candidate.is_relative_to(root_dir):
                        if safe_candidate.suffix.lower() in VALID_IMAGE_EXTENSIONS:
                            image_path = safe_candidate
                            break
                except Exception:
                    continue

    if not image_path or not image_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card image '{card_id}' not found",
        )

    # 4. Enforce valid image extension
    if image_path.suffix.lower() not in VALID_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card image '{card_id}' is not a valid image format",
        )

    file_path = image_path.resolve()
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        ext = file_path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            mime_type = "image/jpeg"
        elif ext == ".png":
            mime_type = "image/png"
        elif ext == ".webp":
            mime_type = "image/webp"
        elif ext in (".tif", ".tiff"):
            mime_type = "image/tiff"
        elif ext == ".gif":
            mime_type = "image/gif"
        elif ext == ".bmp":
            mime_type = "image/bmp"
        else:
            mime_type = "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ============================================================================
# 4. Session Persistence & Management Endpoints
# ============================================================================

@router.get(
    "/sessions",
    response_model=List[SessionSummary],
    status_code=status.HTTP_200_OK,
    summary="List Historical and Active Sessions",
)
async def list_sessions(
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> List[SessionSummary]:
    """Returns a list of all historical and active sessions from SQLite storage and memory."""
    # Sync in-memory sessions to DB if they have active progress
    for sess in manager.list_sessions():
        if sess.cards or sess.recipes or sess.state != PipelineState.IDLE:
            store.save_pipeline_session(sess)

    return store.list_sessions()


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    status_code=status.HTTP_200_OK,
    summary="Get Detailed Session State and Metrics",
)
async def get_session_detail(
    session_id: str,
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> SessionDetail:
    """Retrieves comprehensive session state, metrics, cards, and recipes."""
    sess = manager.get_session(session_id)
    if sess and (sess.cards or sess.recipes or sess.state != PipelineState.IDLE):
        store.save_pipeline_session(sess)

    detail = store.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )
    return detail



@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a Session and its Associated Data",
)
async def delete_session(
    session_id: str,
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Dict[str, Any]:
    """Cancels and deletes a session, its cached cards, proposals, and recipes."""
    in_mem_deleted = manager.remove_session(session_id)
    db_deleted = store.delete_session(session_id)

    if not in_mem_deleted and not db_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )

    return {
        "session_id": session_id,
        "deleted": True,
        "message": "Session and associated records deleted successfully",
    }


@router.post(
    "/sessions/{session_id}/resume",
    response_model=SessionDetail,
    status_code=status.HTTP_200_OK,
    summary="Resume or Rehydrate Session",
)
async def resume_session(
    session_id: str,
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> SessionDetail:
    """Rehydrates a session from SQLite persistence into memory if needed and returns full detail."""
    detail = store.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )

    # Rehydrate in memory if missing
    sess = manager.get_session(session_id)
    if sess is None:
        sess = manager.create_session(config=detail.config, session_id=session_id)
        sess.recipes = list(detail.recipes)
        sess.progress_metrics = detail.progress
        sess.token_usage = detail.token_usage

    return detail


# ============================================================================
# 5. HITL Correlation Workspace & Candidate Drawer
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

    proposed_pairs_list = []
    for p in session.proposals:
        p_dump = p.model_dump()
        if "front_card_id" not in p_dump and p.ordered_card_ids:
            p_dump["front_card_id"] = p.ordered_card_ids[0]
            p_dump["back_card_id"] = p.ordered_card_ids[1] if len(p.ordered_card_ids) > 1 else None
        proposed_pairs_list.append(p_dump)

    return {
        "session_id": session.session_id,
        "status": session.state.value,
        "proposals": [p.model_dump() for p in session.proposals],
        "proposed_pairs": proposed_pairs_list,
        "unassigned_cards": unassigned_dump,
        "total_proposals": len(session.proposals),
        "total_unassigned": len(session.unassigned_cards),
    }


@router.get(
    "/hitl/candidates",
    response_model=CandidateListResponse,
    status_code=status.HTTP_200_OK,
    summary="Rank Alternate Card Candidates for Drawer",
)
async def get_hitl_candidates(
    session_id: Optional[str] = Query(None, description="Session ID"),
    card_id: Optional[str] = Query(None, description="Target Card ID"),
    front_card_id: Optional[str] = Query(None, description="Target Card ID alias"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> CandidateListResponse:
    """Calculates visual and semantic feature breakdowns across all candidate cards for candidate drawer."""
    target_card_id = card_id or front_card_id
    if not target_card_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="card_id or front_card_id parameter is required",
        )

    session = manager.get_session(session_id)
    sid = session.session_id if session else (session_id or "default")

    fingerprints: Dict[str, VisualFingerprint] = {}
    if session and session.fingerprints:
        fingerprints = session.fingerprints

    # If fingerprints not loaded in session, check cache or build basic set
    fp_front = fingerprints.get(target_card_id)

    candidates: List[CorrelationCandidate] = []

    # Check proposals for existing sequences containing this card
    existing_proposals: Dict[str, CorrelationProposal] = {}
    for p in (session.proposals if session else []):
        if target_card_id in p.ordered_card_ids:
            for other_id in p.ordered_card_ids:
                if other_id != target_card_id:
                    existing_proposals[other_id] = p

    # Iterate candidate cards
    all_cand_ids = [cid for cid in fingerprints.keys() if cid != target_card_id]
    if not all_cand_ids and session:
        all_cand_ids = [c.card_id for c in session.cards if c.card_id != target_card_id]
    if not all_cand_ids:
        db_cards = store.get_cards(sid)
        all_cand_ids = [c["card_id"] for c in db_cards if c["card_id"] != target_card_id]

    for cand_id in all_cand_ids:
        fp_cand = fingerprints.get(cand_id)
        cand_img_path = f"/api/cards/{cand_id}/image?session_id={sid}"

        prop = existing_proposals.get(cand_id)

        if fp_front and fp_cand:
            s_color = calculate_color_similarity(fp_front.paper_color, fp_cand.paper_color)
            s_ruling = calculate_ruling_similarity(fp_front.ruling, fp_cand.ruling)
            s_ink = calculate_ink_similarity(fp_front.ink, fp_cand.ink)
            s_stain = calculate_stain_similarity(fp_front.artifacts, fp_cand.artifacts)
            s_heur = calculate_heuristic_distance(fp_front, fp_cand)

            s_sem = prop.semantic_score if prop else 0.0
            comp_score = prop.confidence if prop else s_heur
            snippet = prop.continuity_explanation if prop and prop.continuity_explanation else "Stage 1 visual continuity matched."

            breakdown = CandidateScoreBreakdown(
                paper_color_score=s_color,
                paper_ruling_score=s_ruling,
                ink_score=s_ink,
                stain_score=s_stain,
                boundary_text_score=0.5,
                composite_heuristic_score=s_heur,
                semantic_score=s_sem,
                overall_score=comp_score,
            )

            candidate = CorrelationCandidate(
                candidate_card_id=cand_id,
                candidate_image_path=cand_img_path,
                score=comp_score,
                heuristic_score=s_heur,
                semantic_score=s_sem,
                composite_score=comp_score,
                reasoning_snippet=snippet,
                score_breakdown=breakdown,
            )
        elif prop:
            breakdown = CandidateScoreBreakdown(
                composite_heuristic_score=prop.heuristic_score,
                semantic_score=prop.semantic_score,
                overall_score=prop.confidence,
            )
            candidate = CorrelationCandidate(
                candidate_card_id=cand_id,
                candidate_image_path=cand_img_path,
                score=prop.confidence,
                heuristic_score=prop.heuristic_score,
                semantic_score=prop.semantic_score,
                composite_score=prop.confidence,
                reasoning_snippet=prop.continuity_explanation,
                score_breakdown=breakdown,
            )
        else:
            candidate = CorrelationCandidate(
                candidate_card_id=cand_id,
                candidate_image_path=cand_img_path,
                score=0.50,
                heuristic_score=0.50,
                semantic_score=0.0,
                composite_score=0.50,
                reasoning_snippet="Alternate scan in current deck.",
                score_breakdown=CandidateScoreBreakdown(composite_heuristic_score=0.5, overall_score=0.5),
            )

        candidates.append(candidate)

    # Sort candidates descending by confidence score
    candidates.sort(key=lambda c: c.score, reverse=True)

    return CandidateListResponse(
        session_id=sid,
        card_id=target_card_id,
        candidates=candidates,
        total_candidates=len(candidates),
    )


@router.post(
    "/hitl/decision",
    status_code=status.HTTP_200_OK,
    summary="Record Single Incremental HITL Decision",
)
async def record_single_decision(
    payload: CardPairApproval,
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    manager: SessionManager = Depends(get_manager),
) -> Dict[str, Any]:
    """Records an incremental single-card pairing, standalone, or rejection decision from Rapid Queue."""
    sid = session_id or manager.get_active_session_id()
    session = manager.get_session(sid)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Update or append in session.approvals
    existing_idx = next(
        (i for i, a in enumerate(session.approvals) if a.proposal_id == payload.proposal_id),
        None,
    )
    if existing_idx is not None:
        session.approvals[existing_idx] = payload
    else:
        session.approvals.append(payload)

    return {
        "session_id": session.session_id,
        "proposal_id": payload.proposal_id,
        "success": True,
        "message": "Decision recorded",
    }


@router.post(
    "/hitl/approve",
    status_code=status.HTTP_200_OK,
    summary="Submit Human Review Decisions and Resume Pipeline",
)
async def approve_hitl(
    payload: ApprovalPayload,
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
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

    # Persist updated session state to store
    store.save_pipeline_session(session)

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
# 6. Recipe CRUD & Inline Editing Endpoints
# ============================================================================

@router.get(
    "/recipes",
    response_model=List[RecipeSchema],
    status_code=status.HTTP_200_OK,
    summary="List Extracted Recipes for Session",
)
async def list_recipes(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> List[RecipeSchema]:
    """Returns all structured recipes extracted in this session or across all sessions."""
    session = manager.get_session(session_id)
    if session and session.recipes:
        return session.recipes

    return store.list_recipes(session_id=session_id)


@router.get(
    "/recipes/{recipe_id}",
    response_model=RecipeSchema,
    status_code=status.HTTP_200_OK,
    summary="Get Extracted Recipe by ID",
)
async def get_recipe_by_id(
    recipe_id: str,
    session_id: Optional[str] = Query(None, description="Optional session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> RecipeSchema:
    """Retrieves an individual recipe model."""
    session = manager.get_session(session_id)
    if session:
        for r in session.recipes:
            if r.recipe_id == recipe_id:
                return r

    recipe = store.get_recipe(recipe_id=recipe_id, session_id=session_id)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recipe '{recipe_id}' not found",
        )
    return recipe


@router.post(
    "/recipes",
    response_model=RecipeSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Manually Add a Recipe",
)
async def create_recipe(
    recipe: RecipeSchema,
    session_id: Optional[str] = Query(None, description="Target session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> RecipeSchema:
    """Manually creates a new recipe in the session and persists to SQLite."""
    sid = session_id or manager.get_active_session_id() or f"sess_{uuid.uuid4().hex[:8]}"

    session = manager.get_session(sid)
    if session:
        session.recipes.append(recipe)

    store.save_recipe(sid, recipe)
    return recipe


@router.put(
    "/recipes/{recipe_id}",
    response_model=RecipeSchema,
    status_code=status.HTTP_200_OK,
    summary="Update Recipe Fields (Inline Recipe Editor)",
)
async def update_recipe(
    recipe_id: str,
    recipe_update: RecipeSchema,
    session_id: Optional[str] = Query(None, description="Target session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> RecipeSchema:
    """Persists inline modifications made to ingredients, directions, times, yield, or tags."""
    recipe_update.recipe_id = recipe_id
    sid = session_id or manager.get_active_session_id()

    # Check if recipe exists
    existing = None
    session = manager.get_session(sid)
    if session:
        for idx, r in enumerate(session.recipes):
            if r.recipe_id == recipe_id:
                existing = r
                session.recipes[idx] = recipe_update
                break

    if existing is None:
        existing = store.get_recipe(recipe_id, session_id=sid)

    if existing is None and not session:
        # Check globally
        existing = store.get_recipe(recipe_id)

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recipe '{recipe_id}' not found",
        )

    target_sid = sid or "default"
    store.save_recipe(target_sid, recipe_update)

    return recipe_update


@router.delete(
    "/recipes/{recipe_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a Recipe",
)
async def delete_recipe(
    recipe_id: str,
    session_id: Optional[str] = Query(None, description="Target session ID"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Dict[str, Any]:
    """Deletes a recipe from the session and database."""
    session = manager.get_session(session_id)
    if session:
        session.recipes = [r for r in session.recipes if r.recipe_id != recipe_id]

    deleted = store.delete_recipe(recipe_id=recipe_id, session_id=session_id)
    if not deleted and (not session or not any(r.recipe_id == recipe_id for r in session.recipes)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recipe '{recipe_id}' not found",
        )

    return {
        "recipe_id": recipe_id,
        "deleted": True,
        "message": "Recipe deleted successfully",
    }


# ============================================================================
# 7. Multi-Format Exporters (Plan To Eat CSV, Schema.org JSON-LD, Raw JSON,
#    DOCX, PDF, Markdown, Cooklang, Paprika, Google Docs)
# ============================================================================

def _get_export_recipes_for_format(
    session_id: Optional[str],
    manager: SessionManager,
    store: SessionStore,
) -> List[RecipeSchema]:
    """Helper to retrieve recipes for export from active session or database with strict session isolation."""
    if session_id:
        session = manager.get_session(session_id)
        if session:
            if session.recipes:
                return session.recipes
            if session.state != PipelineState.COMPLETED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Export not ready: pipeline has not completed extraction",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No recipes available for export in session '{session_id}'",
            )

        detail = store.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session '{session_id}' not found",
            )
        recipes = store.list_recipes(session_id=session_id)
        if recipes:
            return recipes
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No recipes available for export in session '{session_id}'",
        )

    # If no session_id provided: check active in-memory session first
    active_sid = manager.get_active_session_id()
    if active_sid:
        active_sess = manager.get_session(active_sid)
        if active_sess and active_sess.recipes:
            return active_sess.recipes

    # Global database fallback only when session_id is None
    all_recipes = store.list_recipes()
    if all_recipes:
        return all_recipes

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No recipes available for export",
    )


def _get_image_resolver(
    session_id: Optional[str],
    manager: SessionManager,
    store: SessionStore,
) -> Callable[[str], Optional[Union[str, Path, bytes]]]:
    """Builds a resolver function mapping card_id to on-disk file path or bytes."""
    target_sid = session_id or manager.get_active_session_id()
    session = manager.get_session(target_sid) if target_sid else None

    def resolver(card_id: str) -> Optional[Union[str, Path, bytes]]:
        if not card_id:
            return None
        if session:
            for card in session.cards:
                if (card.card_id == card_id or card.filename == card_id or getattr(card.file_path, "name", None) == card_id) and card.file_path and Path(card.file_path).exists():
                    return str(card.file_path)
            for p in session.scanned_paths:
                if (p.name == card_id or str(p) == card_id) and p.is_file():
                    return str(p)
            if session.cache:
                fp = session.cache.get_by_card_id(card_id)
                if fp and fp.image_path and Path(fp.image_path).is_file():
                    return str(fp.image_path)

        path = store.get_card_path(card_id, session_id=target_sid)
        if path and Path(path).exists():
            return str(path)
        return None

    return resolver


def sanitize_export_filename(
    filename: Optional[str],
    default_name: str,
    expected_ext: Optional[str] = None,
) -> str:
    """Sanitizes an export filename against path traversal and illegal filesystem characters."""
    if not filename:
        return default_name

    # Strip path components and directory names
    raw = str(filename).strip().replace("\\", "/")
    clean = os.path.basename(raw)

    # Remove illegal characters (\ / : * ? " < > | and control chars)
    clean = re.sub(r'[\/\\:\*\?"<>\|\x00-\x1f\x7f]', '', clean)
    clean = clean.strip(". ")

    if not clean:
        return default_name

    if expected_ext:
        normalized_ext = f".{expected_ext.lstrip('.')}"
        if not clean.lower().endswith(normalized_ext.lower()):
            clean = f"{clean}{normalized_ext}"

    return clean


class GoogleDocsCreateDocRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Optional target session ID")
    access_token: str = Field(..., description="Google OAuth 2.0 access token")
    document_title: Optional[str] = Field("RecipeBoxer Archival Cookbook", description="Document title")
    folder_name: Optional[str] = Field("RecipeBoxer Scans", description="Drive folder for scan uploads")


@router.post(
    "/export/google-docs/create-document",
    status_code=status.HTTP_200_OK,
    summary="Create authentic Google Doc in user account via Google Docs/Drive API",
)
async def create_google_doc_document(
    payload: GoogleDocsCreateDocRequest,
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Dict[str, Any]:
    """Creates an authentic Google Doc in the authenticated user's account.

    Uploads card scan images to Google Drive, creates the document, applies batch updates,
    and returns document ID and URL.
    """
    if not payload.access_token or not payload.access_token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth access token is required",
        )

    recipes = _get_export_recipes_for_format(payload.session_id, manager, store)
    resolver = _get_image_resolver(payload.session_id, manager, store)
    exporter = GoogleDocsExporter()

    try:
        result = exporter.create_google_document(
            access_token=payload.access_token,
            recipes=recipes,
            title=payload.document_title or "RecipeBoxer Archival Cookbook",
            image_resolver=resolver,
            drive_folder_name=payload.folder_name or "RecipeBoxer Scans",
        )
        return result
    except ValueError as val_err:
        err_msg = str(val_err)
        if "401" in err_msg or "unauthorized" in err_msg.lower() or ("invalid" in err_msg.lower() and "token" in err_msg.lower()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Google OAuth authorization failed: {err_msg}",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg,
        )
    except Exception as exc:
        logger.error(f"Error creating Google Doc: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with Google Docs/Drive API: {exc}",
        )


@router.get(
    "/export/csv",
    status_code=status.HTTP_200_OK,
    summary="Download Plan To Eat RFC 4180 CSV Export",
)
async def export_csv(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns the generated Plan To Eat CSV file as text/csv attachment with RFC 4180 headers."""
    target_filename = sanitize_export_filename(filename, "recipeboxer_export.csv", "csv")

    session = manager.get_session(session_id)
    if session is None:
        if session_id is not None:
            # Check SQLite store
            detail = store.get_session_detail(session_id)
            if detail is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Session not found",
                )
            if detail.status != PipelineState.COMPLETED and detail.status != "COMPLETED":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CSV export not ready: pipeline has not completed extraction",
                )
            recipes = store.list_recipes(session_id=session_id)
            if not recipes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CSV export not ready: pipeline has not completed extraction",
                )
            csv_content = export_recipes_to_plan_to_eat_csv(recipes)
            return Response(
                content=csv_content,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

    if session.state != PipelineState.COMPLETED or not session.csv_content:
        if session.state == PipelineState.COMPLETED and session.recipes:
            session.csv_content = export_recipes_to_plan_to_eat_csv(session.recipes)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CSV export not ready: pipeline has not completed extraction",
            )

    if session.csv_output_path and Path(session.csv_output_path).exists() and not filename:
        return FileResponse(
            path=session.csv_output_path,
            media_type="text/csv; charset=utf-8",
            filename=target_filename,
            headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
        )

    return Response(
        content=session.csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/json-ld",
    status_code=status.HTTP_200_OK,
    summary="Download Schema.org Recipe JSON-LD Export",
)
@router.get(
    "/export/jsonld",
    status_code=status.HTTP_200_OK,
    summary="Download Schema.org Recipe JSON-LD Export (Alias)",
)
async def export_json_ld(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns Schema.org Recipe structured JSON-LD format."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    json_ld_str = export_recipes_to_json_ld(recipes)
    target_filename = sanitize_export_filename(filename, "recipeboxer_recipes_json_ld.json", "json")

    return Response(
        content=json_ld_str,
        media_type="application/ld+json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/json",
    status_code=status.HTTP_200_OK,
    summary="Download Raw Structured Recipe JSON",
)
async def export_json(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns standard RecipeSchema JSON array download."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    json_str = export_recipes_to_json(recipes)
    target_filename = sanitize_export_filename(filename, "recipeboxer_recipes.json", "json")

    return Response(
        content=json_str,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/docx",
    status_code=status.HTTP_200_OK,
    summary="Download Heirloom Cookbook DOCX Document",
)
async def export_docx(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Generates and downloads a standalone formatted .docx heirloom family cookbook."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    resolver = _get_image_resolver(session_id, manager, store)
    docx_bytes = export_recipes_to_docx(recipes, image_resolver=resolver)
    target_filename = sanitize_export_filename(filename, "recipeboxer_cookbook.docx", "docx")

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/pdf",
    status_code=status.HTTP_200_OK,
    summary="Download Printable Archival PDF Cookbook",
)
async def export_pdf(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Generates and downloads a print-ready PDF cookbook with embedded card scans."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    resolver = _get_image_resolver(session_id, manager, store)
    pdf_bytes = export_recipes_to_pdf(recipes, image_resolver=resolver)
    target_filename = sanitize_export_filename(filename, "recipeboxer_cookbook.pdf", "pdf")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/markdown",
    status_code=status.HTTP_200_OK,
    summary="Download Obsidian Markdown with YAML Frontmatter",
)
@router.get(
    "/export/md",
    status_code=status.HTTP_200_OK,
    summary="Download Obsidian Markdown (Alias)",
)
async def export_markdown(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    use_wikilinks: bool = Query(True, description="Whether to format card images as Obsidian wikilinks"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns Obsidian-compatible Markdown document with structured YAML frontmatter."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    resolver = _get_image_resolver(session_id, manager, store)
    md_content = export_recipes_to_markdown(recipes, use_wikilinks=use_wikilinks, image_resolver=resolver)
    target_filename = sanitize_export_filename(filename, "recipeboxer_recipes.md", "md")

    return Response(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/cooklang",
    status_code=status.HTTP_200_OK,
    summary="Download Cooklang (.cook) Serialized Recipes",
)
@router.get(
    "/export/cook",
    status_code=status.HTTP_200_OK,
    summary="Download Cooklang (.cook) Serialized Recipes (Alias)",
)
async def export_cooklang(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns Cooklang (.cook) markup formatted with ingredients, cookware, and timers."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    cook_content = export_recipes_to_cooklang(recipes)
    target_filename = sanitize_export_filename(filename, "recipeboxer_recipes.cook", "cook")

    return Response(
        content=cook_content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.get(
    "/export/paprika",
    status_code=status.HTTP_200_OK,
    summary="Download Paprika (.paprikarecipes) Gzip Archive",
)
async def export_paprika(
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    as_zip: bool = Query(False, description="Whether to format multi-recipe archive as ZIP of .paprikarecipe files"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Response:
    """Returns .paprikarecipes gzip archive containing JSON recipes and base64 card scans."""
    recipes = _get_export_recipes_for_format(session_id, manager, store)
    resolver = _get_image_resolver(session_id, manager, store)
    paprika_bytes = export_recipes_to_paprika(recipes, image_resolver=resolver, as_zip_archive=as_zip)

    default_ext = "zip" if as_zip else "paprikarecipes"
    default_name = f"recipeboxer_export.{default_ext}"
    target_filename = sanitize_export_filename(filename, default_name, default_ext)

    return Response(
        content=paprika_bytes,
        media_type="application/x-paprikarecipes",
        headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
    )


@router.post(
    "/export/google-docs",
    status_code=status.HTTP_200_OK,
    summary="Generate Google Docs Archival Batch Requests or HTML",
)
@router.get(
    "/export/google-docs",
    status_code=status.HTTP_200_OK,
    summary="Generate Google Docs Archival Batch Requests or HTML (GET Alias)",
)
async def export_google_docs(
    request: Request,
    session_id: Optional[str] = Query(None, description="Optional target session ID"),
    format: Optional[str] = Query("batch_requests", description="'batch_requests', 'json', or 'html'"),
    title: Optional[str] = Query("RecipeBoxer Archival Cookbook", description="Cookbook Title"),
    filename: Optional[str] = Query(None, description="Custom export filename"),
    manager: SessionManager = Depends(get_manager),
    store: SessionStore = Depends(get_store),
) -> Any:
    """Generates structured Google Docs API batchUpdate requests or HTML archive."""
    # Check if request has JSON body for POST
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                session_id = body.get("session_id", session_id)
                format = body.get("format", format)
                title = body.get("title", title)
                filename = body.get("filename", filename)
        except Exception:
            pass

    recipes = _get_export_recipes_for_format(session_id, manager, store)
    resolver = _get_image_resolver(session_id, manager, store)
    exporter = GoogleDocsExporter()

    fmt = (format or "batch_requests").lower().strip()
    if fmt == "html":
        html_content = exporter.export_to_html(recipes, title=title or "RecipeBoxer Archival Cookbook", image_resolver=resolver)
        target_filename = sanitize_export_filename(filename, "recipeboxer_google_docs.html", "html")
        return Response(
            content=html_content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
        )
    elif fmt == "json":
        json_str = exporter.export_to_json_string(recipes, title=title or "RecipeBoxer Archival Cookbook", image_resolver=resolver)
        target_filename = sanitize_export_filename(filename, "recipeboxer_google_docs.json", "json")
        return Response(
            content=json_str,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{target_filename}"'},
        )
    else:
        # Default: batch_requests dictionary
        data = exporter.export_to_json_dict(recipes, title=title or "RecipeBoxer Archival Cookbook", image_resolver=resolver)
        return data


# ============================================================================
# 8. System & Native Directory Browsing
# ============================================================================

class BrowseDirectoryRequest(BaseModel):
    initial_dir: Optional[str] = Field(default=None, description="Starting directory path for folder browser dialog")
    title: Optional[str] = Field(default="Select Recipe Scans Directory", description="Dialog window title")


class BrowseDirectoryResponse(BaseModel):
    path: Optional[str] = Field(default=None, description="Selected absolute directory path")
    canceled: bool = Field(default=False, description="Whether the folder browser was closed or canceled without selection")
    success: bool = Field(default=True, description="Whether the dialog ran successfully")
    error: Optional[str] = Field(default=None, description="Error message if dialog invocation failed")


@router.post(
    "/system/browse-directory",
    response_model=BrowseDirectoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Open Native System Folder Browser Dialog",
)
@router.post(
    "/browse/directory",
    response_model=BrowseDirectoryResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def browse_directory(
    payload: Optional[BrowseDirectoryRequest] = None,
) -> BrowseDirectoryResponse:
    """Invokes native Windows / OS folder picker dialog to select an absolute directory path."""
    raw_initial = payload.initial_dir if payload else None
    initial_dir = raw_initial.strip() if isinstance(raw_initial, str) and raw_initial.strip() else None
    raw_title = payload.title if payload else None
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else "Select Recipe Scans Directory"

    def _open_native_dialog() -> tuple[Optional[str], bool, bool, Optional[str]]:
        root = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            try:
                root.wm_attributes("-topmost", 1)
            except Exception:
                pass
            try:
                root.focus_force()
            except Exception:
                pass

            start_path = initial_dir if initial_dir and os.path.isdir(initial_dir) else os.getcwd()
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=start_path,
                title=title,
                mustexist=True,
            )
            if selected:
                resolved = str(Path(selected).resolve())
                return resolved, False, True, None
            else:
                return None, True, True, None
        except Exception as exc:
            logger.warning(f"Native folder dialog invocation error: {exc}")
            return None, False, False, str(exc)
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass

    loop = asyncio.get_running_loop()
    path, canceled, success, error = await loop.run_in_executor(None, _open_native_dialog)
    return BrowseDirectoryResponse(
        path=path,
        canceled=canceled,
        success=success,
        error=error,
    )


@router.get(
    "/system/browse-directory",
    response_model=BrowseDirectoryResponse,
    include_in_schema=False,
)
@router.get(
    "/browse/directory",
    response_model=BrowseDirectoryResponse,
    include_in_schema=False,
)
async def browse_directory_get(
    initial_dir: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
) -> BrowseDirectoryResponse:
    return await browse_directory(BrowseDirectoryRequest(initial_dir=initial_dir, title=title))




