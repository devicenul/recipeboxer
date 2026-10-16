"""Portable .recipeboxer project archive storage, packaging, extraction, and merge engine."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Set, Tuple, Union

from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.config import get_settings
from recipeboxer.ingestion.scanner import ScannedCard, compute_file_sha256
from recipeboxer.models.archive import (
    ArchiveAssetRecord,
    ArchiveCardRecord,
    ArchiveRecipeRecord,
    ArchiveSequenceProposal,
    MarginaliaEntry,
    RecipeBoxerArchiveManifest,
    StepImageBinding,
)
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.pipeline import PipelineState, ProgressMetrics, TokenUsage
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.models.session import SessionDetail
from recipeboxer.storage.cache import FingerprintCache, RemoteAssetCache
from recipeboxer.storage.session_store import SessionStore

logger = logging.getLogger(__name__)

CURRENT_FORMAT_VERSION = "1.0.0"
CURRENT_SCHEMA_VERSION = "1.0.0"
CURRENT_APP_VERSION = "1.0.0"


def _portable_relative_member_path(name: str) -> str:
    """Validates an archive member path portably and returns it as a normalized POSIX relative path.

    Archives are portable between Windows, macOS and Linux, so validation must not depend on the
    host OS path semantics:

    * backslashes are treated as path separators on every OS;
    * drive-letter paths (``C:/x``, ``C:\\x``, ``C:x``), UNC paths (``\\\\server\\share``,
      ``//server/share``), NUL bytes and segments containing ``:`` (NTFS streams) are rejected;
    * a single leading separator (``/x``, ``\\x``) is stripped, keeping the entry inside the root;
    * ``..`` segments are resolved lexically and rejected if they ever climb above the root.

    Raises:
        ValueError: on any of the rejected forms (message contains "ZipSlip path traversal attempt").
    """
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError(f"ZipSlip path traversal attempt detected in archive entry: {name!r}")
    unified = name.replace("\\", "/")
    if unified.startswith("//"):
        raise ValueError(f"ZipSlip path traversal attempt detected in archive entry (UNC path): {name!r}")
    if len(unified) >= 2 and unified[1] == ":" and unified[0].isalpha():
        raise ValueError(f"ZipSlip path traversal attempt detected in archive entry (drive path): {name!r}")
    stack: List[str] = []
    for part in unified.lstrip("/").split("/"):
        if part in ("", "."):
            continue
        if ":" in part:
            raise ValueError(f"ZipSlip path traversal attempt detected in archive entry: {name!r}")
        if part == "..":
            if not stack:
                raise ValueError(f"ZipSlip path traversal attempt detected in archive entry: {name!r}")
            stack.pop()
            continue
        stack.append(part)
    if not stack:
        raise ValueError(f"ZipSlip path traversal attempt detected in archive entry (empty path): {name!r}")
    return "/".join(stack)


def _safe_extract(zip_file: zipfile.ZipFile, target_dir: Path) -> None:
    """Safely extracts all members of a ZipFile to target_dir with ZipSlip protection."""
    target_dir_resolved = target_dir.resolve()
    for member in zip_file.infolist():
        # Portable validation: rejects '..', backslash traversal, drive letters, UNC and absolute paths
        norm_name = _portable_relative_member_path(member.filename)
        dest_path = (target_dir_resolved / norm_name).resolve()

        # Defence in depth (e.g. pre-existing symlinks inside target_dir)
        if dest_path != target_dir_resolved and target_dir_resolved not in dest_path.parents:
            raise ValueError(f"ZipSlip path traversal attempt detected in archive entry: {member.filename}")

        if member.is_dir():
            dest_path.mkdir(parents=True, exist_ok=True)
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(member) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def validate_archive_manifest(
    manifest_data: Union[Dict[str, Any], str, Path, bytes],
) -> RecipeBoxerArchiveManifest:
    """Validates and parses manifest data against RecipeBoxerArchiveManifest schema.

    Args:
        manifest_data: Dictionary, JSON string, file Path, or raw bytes.

    Returns:
        Validated RecipeBoxerArchiveManifest instance.

    Raises:
        ValueError: If JSON is invalid or schema validation fails.
    """
    if isinstance(manifest_data, (str, Path)) and os.path.exists(str(manifest_data)):
        with open(manifest_data, "r", encoding="utf-8") as f:
            raw_dict = json.load(f)
    elif isinstance(manifest_data, bytes):
        raw_dict = json.loads(manifest_data.decode("utf-8"))
    elif isinstance(manifest_data, str):
        raw_dict = json.loads(manifest_data)
    elif isinstance(manifest_data, dict):
        raw_dict = manifest_data
    else:
        raise ValueError(f"Unsupported manifest data type: {type(manifest_data)}")

    try:
        manifest = RecipeBoxerArchiveManifest.model_validate(raw_dict)
    except Exception as exc:
        raise ValueError(f"Invalid RecipeBoxer archive manifest schema: {exc}") from exc

    # Asset archive paths are later joined onto extraction/project directories: enforce the same
    # portable ZipSlip rules as for zip members.
    for asset in manifest.assets:
        asset.archive_path = _portable_relative_member_path(asset.archive_path)

    return manifest


def create_recipeboxer_archive(
    session_or_id: Union[PipelineSession, str],
    store: Optional[SessionStore] = None,
    manager: Optional[SessionManager] = None,
    cache: Optional[FingerprintCache] = None,
    output_path: Optional[Union[str, Path]] = None,
    project_name: Optional[str] = None,
    compression_level: int = 6,
) -> bytes:
    """Bundles session state, visual fingerprints, proposals, recipes, marginalia,
    and original image scans into a compressed .recipeboxer ZIP archive.

    Args:
        session_or_id: Active PipelineSession or session_id string.
        store: Optional SQLite SessionStore instance.
        manager: Optional SessionManager instance.
        cache: Optional FingerprintCache instance.
        output_path: Optional destination filesystem path.
        project_name: Optional human-readable project title.
        compression_level: ZIP deflation level (default 6).

    Returns:
        Raw bytes of the .recipeboxer ZIP file.
    """
    session: Optional[PipelineSession] = None
    session_id: str

    if isinstance(session_or_id, PipelineSession):
        session = session_or_id
        session_id = session.session_id
    else:
        session_id = str(session_or_id)
        if manager:
            session = manager.get_session(session_id)

    # Gather session data from in-memory session or SQLite store
    session_detail: Optional[SessionDetail] = None
    if store:
        session_detail = store.get_session_detail(session_id)

    if session is None and session_detail is None:
        raise ValueError(f"Session '{session_id}' not found in memory or database.")

    # 1. Build session metadata
    state_str = (
        session.state.value if session
        else (session_detail.status.value if hasattr(session_detail.status, "value") else str(session_detail.status))
    )
    mode_str = (
        (getattr(session.config, "mode", None) or "parallel") if session and hasattr(session, "config")
        else (session_detail.mode if session_detail else "parallel")
    )
    default_tags: List[str] = []
    if session and hasattr(session.config, "default_tags") and session.config.default_tags:
        default_tags = list(session.config.default_tags)
    elif session_detail and isinstance(session_detail.config, dict):
        default_tags = session_detail.config.get("default_tags", [])

    token_usage_dict: Dict[str, Any] = {}
    if session and session.token_usage:
        token_usage_dict = session.token_usage.model_dump()
    elif session_detail and session_detail.token_usage:
        token_usage_dict = session_detail.token_usage.model_dump()

    elapsed = 0.0
    if session:
        end = session.end_time or time.monotonic()
        elapsed = max(0.0, end - session.start_time)
    elif session_detail and session_detail.elapsed_seconds:
        elapsed = session_detail.elapsed_seconds

    card_count = (
        len(session.cards) if session and session.cards
        else (session_detail.card_count if session_detail else 0)
    )
    recipe_count = (
        len(session.recipes) if session and session.recipes
        else (session_detail.recipe_count if session_detail else 0)
    )

    session_metadata: Dict[str, Any] = {
        "session_id": session_id,
        "status": state_str,
        "mode": mode_str,
        "default_tags": default_tags,
        "card_count": card_count,
        "recipe_count": recipe_count,
        "token_usage": token_usage_dict,
        "elapsed_seconds": elapsed,
        "created_at": session_detail.created_at if session_detail else datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 2. Collect Cards and Images
    # Map SHA-256 -> (ArchiveAssetRecord, bytes)
    asset_records: Dict[str, ArchiveAssetRecord] = {}
    asset_payloads: Dict[str, bytes] = {}
    card_records: List[ArchiveCardRecord] = []
    card_sha_map: Dict[str, str] = {}  # card_id -> sha256

    raw_cards = []
    if session and session.cards:
        raw_cards = session.cards
    elif session_detail and session_detail.cards:
        raw_cards = session_detail.cards

    remote_cache: Optional[RemoteAssetCache] = None
    if store and hasattr(store, "cache_dir"):
        try:
            remote_cache = RemoteAssetCache(cache_dir=store.cache_dir)
        except Exception:
            remote_cache = None

    for c in raw_cards:
        card_id = getattr(c, "card_id", None) or (c.get("card_id") if isinstance(c, dict) else str(c))
        filename = getattr(c, "filename", None) or (c.get("filename") if isinstance(c, dict) else f"{card_id}.jpg")
        image_path = getattr(c, "file_path", None) or (c.get("image_path") if isinstance(c, dict) else None)
        sha256_hash = getattr(c, "sha256_hash", None) or (c.get("sha256_hash") if isinstance(c, dict) else None)
        role = getattr(c, "role", None) or (c.get("role") if isinstance(c, dict) else "STANDALONE")
        if hasattr(role, "value"):
            role = role.value
        role_str = str(role)

        # Retrieve image payload
        image_bytes: Optional[bytes] = None
        if image_path and Path(image_path).is_file():
            try:
                with open(image_path, "rb") as img_f:
                    image_bytes = img_f.read()
            except Exception as e:
                logger.warning(f"Could not read card image at {image_path}: {e}")

        if image_bytes is None and sha256_hash and remote_cache:
            try:
                image_bytes = remote_cache.get(sha256_hash)
            except Exception:
                image_bytes = None

        if image_bytes is None:
            # Check session scanned_paths or cache
            if session:
                for p in session.scanned_paths:
                    if p.name == filename or p.name == card_id:
                        try:
                            with open(p, "rb") as img_f:
                                image_bytes = img_f.read()
                            break
                        except Exception:
                            pass

        if image_bytes is None:
            # Fallback 1x1 placeholder image to preserve archive structural integrity
            image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"

        computed_sha = hashlib.sha256(image_bytes).hexdigest().lower()
        if not sha256_hash:
            sha256_hash = computed_sha
        elif sha256_hash.lower() != computed_sha:
            sha256_hash = computed_sha

        card_sha_map[card_id] = sha256_hash

        # Determine extension and mime type
        ext = Path(filename).suffix.lower() if filename and Path(filename).suffix else ".jpg"
        if not ext.startswith("."):
            ext = f".{ext}"
        mime_type = mimetypes.guess_type(f"file{ext}")[0] or "image/jpeg"
        archive_path = f"assets/scans/{sha256_hash}{ext}"

        if sha256_hash not in asset_records:
            asset_records[sha256_hash] = ArchiveAssetRecord(
                sha256_hash=sha256_hash,
                filename=Path(filename).name if filename else f"{card_id}{ext}",
                archive_path=archive_path,
                mime_type=mime_type,
                size_bytes=len(image_bytes),
                width=getattr(c, "width", None) or (c.get("width") if isinstance(c, dict) else None),
                height=getattr(c, "height", None) or (c.get("height") if isinstance(c, dict) else None),
            )
            asset_payloads[sha256_hash] = image_bytes

        # Get visual fingerprint
        fp: Optional[VisualFingerprint] = None
        if session and card_id in session.fingerprints:
            fp = session.fingerprints[card_id]
        elif session and session.cache:
            fp = session.cache.get_by_hash(sha256_hash)
        elif cache:
            fp = cache.get_by_hash(sha256_hash)

        if fp is None and isinstance(c, dict) and c.get("metadata"):
            try:
                fp = VisualFingerprint.model_validate(c["metadata"])
            except Exception:
                fp = None

        card_record = ArchiveCardRecord(
            card_id=card_id,
            sha256_hash=sha256_hash,
            filename=Path(filename).name if filename else f"{card_id}{ext}",
            role=role_str,
            role_confidence=getattr(c, "role_confidence", 1.0) if hasattr(c, "role_confidence") else 1.0,
            fingerprint=fp,
        )
        card_records.append(card_record)

    # 3. Collect Proposals
    raw_proposals = []
    if session and session.proposals:
        raw_proposals = session.proposals
    elif session_detail and session_detail.proposals:
        raw_proposals = session_detail.proposals

    proposal_records: List[ArchiveSequenceProposal] = []
    for p in raw_proposals:
        prop_id = getattr(p, "proposal_id", None) or (p.get("proposal_id") if isinstance(p, dict) else f"prop_{uuid.uuid4().hex[:6]}")
        ordered_ids = getattr(p, "ordered_card_ids", None) or (p.get("ordered_card_ids") if isinstance(p, dict) else [])
        if not ordered_ids and isinstance(p, dict):
            if p.get("front_card_id"):
                ordered_ids = [p["front_card_id"]]
                if p.get("back_card_id"):
                    ordered_ids.append(p["back_card_id"])

        prop_record = ArchiveSequenceProposal(
            proposal_id=str(prop_id),
            ordered_card_ids=list(ordered_ids) if ordered_ids else ["unknown_card"],
            part_count=getattr(p, "part_count", len(ordered_ids)) if hasattr(p, "part_count") else (p.get("part_count", len(ordered_ids)) if isinstance(p, dict) else len(ordered_ids)),
            transition_scores=getattr(p, "transition_scores", []) if hasattr(p, "transition_scores") else (p.get("transition_scores", []) if isinstance(p, dict) else []),
            heuristic_score=getattr(p, "heuristic_score", 0.0) if hasattr(p, "heuristic_score") else (p.get("heuristic_score", 0.0) if isinstance(p, dict) else 0.0),
            semantic_score=getattr(p, "semantic_score", None) if hasattr(p, "semantic_score") else (p.get("semantic_score", None) if isinstance(p, dict) else None),
            confidence=getattr(p, "confidence", 0.0) if hasattr(p, "confidence") else (p.get("confidence", 0.0) if isinstance(p, dict) else 0.0),
            is_standalone=getattr(p, "is_standalone", False) if hasattr(p, "is_standalone") else (p.get("is_standalone", False) if isinstance(p, dict) else False),
            continuity_explanation=getattr(p, "continuity_explanation", "") if hasattr(p, "continuity_explanation") else (p.get("continuity_explanation", "") if isinstance(p, dict) else ""),
            recommended_action=getattr(p, "recommended_action", "SEQUENCE") if hasattr(p, "recommended_action") else (p.get("recommended_action", "SEQUENCE") if isinstance(p, dict) else "SEQUENCE"),
            decision=getattr(p, "decision", "APPROVE") if hasattr(p, "decision") else (p.get("decision", "APPROVE") if isinstance(p, dict) else "APPROVE"),
            notes=getattr(p, "notes", None) if hasattr(p, "notes") else (p.get("notes") if isinstance(p, dict) else None),
        )
        proposal_records.append(prop_record)

    # 4. Collect Recipes with step bindings and marginalia
    raw_recipes: List[RecipeSchema] = []
    if session and session.recipes:
        raw_recipes = session.recipes
    elif store:
        raw_recipes = store.list_recipes(session_id=session_id)

    recipe_records: List[ArchiveRecipeRecord] = []
    for r in raw_recipes:
        step_bindings: List[StepImageBinding] = []
        marginalia_entries: List[MarginaliaEntry] = []

        # If r is already ArchiveRecipeRecord, preserve existing bindings & marginalia
        if isinstance(r, ArchiveRecipeRecord):
            step_bindings = list(r.step_bindings)
            marginalia_entries = list(r.marginalia)
        else:
            # Synthesize step bindings from recipe.card_ids and recipe.directions
            primary_card_id = r.card_ids[0] if r.card_ids else None
            primary_sha = card_sha_map.get(primary_card_id, "") if primary_card_id else ""

            for step_idx, step_text in enumerate(r.directions, start=1):
                # Associate step with card
                target_card_id = primary_card_id or "unknown"
                target_sha = primary_sha

                # If multiple cards in recipe, distribute later steps to continuation cards
                if len(r.card_ids) > 1 and step_idx > (len(r.directions) // 2):
                    target_card_id = r.card_ids[1]
                    target_sha = card_sha_map.get(target_card_id, primary_sha)

                step_bindings.append(
                    StepImageBinding(
                        step_index=step_idx,
                        card_id=target_card_id,
                        card_sha256=target_sha,
                        bounding_box=None,
                        marginalia_note=None,
                    )
                )

            # Check for marginalia notes in source_notes or description
            if r.source_notes:
                marginalia_entries.append(
                    MarginaliaEntry(
                        id=f"marg_{uuid.uuid4().hex[:8]}",
                        card_id=primary_card_id or "unknown",
                        card_sha256=primary_sha,
                        text=r.source_notes,
                        location_quadrant="TOP_RIGHT",
                        category="attribution",
                        target_field="source_notes",
                    )
                )

        rec_dict = r.model_dump()
        rec_dict["step_bindings"] = [b.model_dump() for b in step_bindings]
        rec_dict["marginalia"] = [m.model_dump() for m in marginalia_entries]

        recipe_records.append(ArchiveRecipeRecord.model_validate(rec_dict))

    # 5. Build Top-Level Manifest
    manifest = RecipeBoxerArchiveManifest(
        format_version=CURRENT_FORMAT_VERSION,
        app_version=CURRENT_APP_VERSION,
        schema_version=CURRENT_SCHEMA_VERSION,
        project_id=f"proj_{session_id.replace('sess_', '')}",
        project_name=project_name or f"RecipeBoxer Project ({session_id})",
        created_at=session_metadata.get("created_at") or datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        generator=f"RecipeBoxer v{CURRENT_APP_VERSION}",
        session_metadata=session_metadata,
        assets=list(asset_records.values()),
        cards=card_records,
        proposals=proposal_records,
        recipes=recipe_records,
    )

    # 6. Package ZIP Archive
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=compression_level,
    ) as zf:
        # Write manifest.json
        manifest_json_str = manifest.model_dump_json(indent=2)
        zf.writestr("manifest.json", manifest_json_str.encode("utf-8"))

        # Write asset images
        for sha256_hash, asset in asset_records.items():
            payload = asset_payloads.get(sha256_hash)
            if payload:
                zf.writestr(asset.archive_path, payload)

    zip_bytes = zip_buffer.getvalue()

    if output_path:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "wb") as f:
            f.write(zip_bytes)

    return zip_bytes


def extract_recipeboxer_archive(
    archive_file_or_bytes: Union[str, Path, bytes, BinaryIO],
    target_dir: Union[str, Path],
    verify_hashes: bool = True,
) -> Tuple[RecipeBoxerArchiveManifest, Path]:
    """Unpacks a .recipeboxer archive safely with ZipSlip protection and SHA-256 asset verification.

    Args:
        archive_file_or_bytes: Path to archive file, raw bytes, or file-like binary stream.
        target_dir: Destination directory path.
        verify_hashes: Whether to calculate SHA-256 of extracted assets and verify against manifest.

    Returns:
        Tuple of (RecipeBoxerArchiveManifest, Path to extracted target directory).

    Raises:
        ValueError: If archive is corrupted, contains path traversal, or asset hash mismatch occurs.
    """
    target_path = Path(target_dir).resolve()
    target_path.mkdir(parents=True, exist_ok=True)

    if isinstance(archive_file_or_bytes, bytes):
        zip_stream: Union[BinaryIO, Path] = io.BytesIO(archive_file_or_bytes)
    elif isinstance(archive_file_or_bytes, (str, Path)):
        p = Path(archive_file_or_bytes)
        if not p.is_file():
            raise ValueError(f"Archive file does not exist: {p}")
        zip_stream = p
    else:
        zip_stream = archive_file_or_bytes

    try:
        with zipfile.ZipFile(zip_stream, "r") as zf:
            # ZipSlip protected extraction
            _safe_extract(zf, target_path)
    except Exception as exc:
        raise ValueError(f"Failed to unpack .recipeboxer archive: {exc}") from exc

    manifest_file = target_path / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError("Archive is missing required 'manifest.json' at root.")

    manifest = validate_archive_manifest(manifest_file)

    # Asset SHA-256 verification
    if verify_hashes:
        for asset in manifest.assets:
            asset_file = target_path / asset.archive_path
            if not asset_file.is_file():
                raise ValueError(f"Missing asset file in extracted archive: {asset.archive_path}")

            hasher = hashlib.sha256()
            with open(asset_file, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest().lower()

            if actual_sha != asset.sha256_hash.lower():
                raise ValueError(
                    f"Asset integrity check failed for '{asset.filename}': "
                    f"manifest SHA-256 is '{asset.sha256_hash}', but actual extracted SHA-256 is '{actual_sha}'"
                )

    return manifest, target_path


def merge_recipeboxer_archives(
    base_archive: Union[str, Path, bytes, BinaryIO],
    incoming_archive_or_session: Union[str, Path, bytes, BinaryIO, PipelineSession, str],
    conflict_strategy: str = "keep_both",
    output_path: Optional[Union[str, Path]] = None,
    store: Optional[SessionStore] = None,
    manager: Optional[SessionManager] = None,
    cache: Optional[FingerprintCache] = None,
) -> Tuple[RecipeBoxerArchiveManifest, bytes]:
    """Merges session or incoming archive data into a base archive, deduplicating image
    assets by SHA-256 and unifying recipes.

    Args:
        base_archive: Base .recipeboxer archive file path or bytes.
        incoming_archive_or_session: Incoming archive (file/bytes) or PipelineSession / session ID.
        conflict_strategy: Conflict resolution ('keep_both', 'prefer_incoming', 'prefer_base').
        output_path: Optional output path for the unified .recipeboxer archive.
        store: Optional SessionStore instance.
        manager: Optional SessionManager instance.
        cache: Optional FingerprintCache instance.

    Returns:
        Tuple of (merged RecipeBoxerArchiveManifest, raw unified .recipeboxer ZIP bytes).
    """
    with tempfile.TemporaryDirectory() as base_tmp, tempfile.TemporaryDirectory() as incoming_tmp:
        base_dir = Path(base_tmp)
        base_manifest, _ = extract_recipeboxer_archive(base_archive, base_dir, verify_hashes=True)

        incoming_dir = Path(incoming_tmp)
        incoming_manifest: RecipeBoxerArchiveManifest

        if isinstance(incoming_archive_or_session, (str, Path, bytes, io.BytesIO)):
            is_file_or_bytes = True
            if isinstance(incoming_archive_or_session, str) and not Path(incoming_archive_or_session).is_file():
                # Might be a session_id
                is_file_or_bytes = False

            if is_file_or_bytes:
                incoming_manifest, _ = extract_recipeboxer_archive(
                    incoming_archive_or_session, incoming_dir, verify_hashes=True
                )
            else:
                # Treat as session_id
                inc_bytes = create_recipeboxer_archive(
                    incoming_archive_or_session, store=store, manager=manager, cache=cache
                )
                incoming_manifest, _ = extract_recipeboxer_archive(inc_bytes, incoming_dir, verify_hashes=True)
        elif isinstance(incoming_archive_or_session, PipelineSession):
            inc_bytes = create_recipeboxer_archive(
                incoming_archive_or_session, store=store, manager=manager, cache=cache
            )
            incoming_manifest, _ = extract_recipeboxer_archive(inc_bytes, incoming_dir, verify_hashes=True)
        else:
            raise ValueError(f"Unsupported incoming archive/session parameter: {type(incoming_archive_or_session)}")

        # 1. Merge Assets (Deduplication by SHA-256)
        base_asset_map: Dict[str, ArchiveAssetRecord] = {a.sha256_hash: a for a in base_manifest.assets}
        for inc_asset in incoming_manifest.assets:
            if inc_asset.sha256_hash not in base_asset_map:
                # Copy asset file from incoming into base
                src_file = incoming_dir / inc_asset.archive_path
                dst_file = base_dir / inc_asset.archive_path
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                if src_file.is_file():
                    shutil.copy2(src_file, dst_file)
                base_manifest.assets.append(inc_asset)
                base_asset_map[inc_asset.sha256_hash] = inc_asset

        # 2. Merge Cards & Rename on ID collision with different SHA
        base_card_map: Dict[str, ArchiveCardRecord] = {c.card_id: c for c in base_manifest.cards}
        base_card_sha_set: Set[str] = {c.sha256_hash for c in base_manifest.cards}
        card_id_remapping: Dict[str, str] = {}

        for inc_card in incoming_manifest.cards:
            if inc_card.sha256_hash in base_card_sha_set:
                # Exact card scan already present
                continue

            target_card_id = inc_card.card_id
            if target_card_id in base_card_map and base_card_map[target_card_id].sha256_hash != inc_card.sha256_hash:
                # Name collision with different scan digest -> rename deterministically
                target_card_id = f"{inc_card.card_id}_{inc_card.sha256_hash[:6]}"
                card_id_remapping[inc_card.card_id] = target_card_id
                inc_card.card_id = target_card_id

            base_manifest.cards.append(inc_card)
            base_card_map[target_card_id] = inc_card
            base_card_sha_set.add(inc_card.sha256_hash)

        # 3. Merge Proposals
        base_prop_ids: Set[str] = {p.proposal_id for p in base_manifest.proposals}
        base_prop_seqs: Set[str] = {",".join(p.ordered_card_ids) for p in base_manifest.proposals}

        for inc_prop in incoming_manifest.proposals:
            # Remap card IDs if renamed
            remapped_cards = [card_id_remapping.get(cid, cid) for cid in inc_prop.ordered_card_ids]
            seq_key = ",".join(remapped_cards)
            if seq_key in base_prop_seqs:
                continue

            prop_id = inc_prop.proposal_id
            if prop_id in base_prop_ids:
                prop_id = f"prop_{uuid.uuid4().hex[:6]}"

            inc_prop.proposal_id = prop_id
            inc_prop.ordered_card_ids = remapped_cards
            base_manifest.proposals.append(inc_prop)
            base_prop_ids.add(prop_id)
            base_prop_seqs.add(seq_key)

        # 4. Merge Recipes (Conflict Resolution)
        base_recipe_ids: Dict[str, int] = {r.recipe_id: idx for idx, r in enumerate(base_manifest.recipes)}
        base_recipe_titles: Dict[str, int] = {r.title.strip().lower(): idx for idx, r in enumerate(base_manifest.recipes)}

        for inc_recipe in incoming_manifest.recipes:
            # Remap card IDs in recipe
            inc_recipe.card_ids = [card_id_remapping.get(cid, cid) for cid in inc_recipe.card_ids]
            for binding in inc_recipe.step_bindings:
                binding.card_id = card_id_remapping.get(binding.card_id, binding.card_id)
            for marg in inc_recipe.marginalia:
                marg.card_id = card_id_remapping.get(marg.card_id, marg.card_id)

            title_key = inc_recipe.title.strip().lower()
            is_id_collision = inc_recipe.recipe_id in base_recipe_ids
            is_title_collision = title_key in base_recipe_titles

            if is_id_collision or is_title_collision:
                existing_idx = base_recipe_ids.get(inc_recipe.recipe_id, base_recipe_titles.get(title_key, 0))
                if conflict_strategy == "prefer_incoming":
                    base_manifest.recipes[existing_idx] = inc_recipe
                elif conflict_strategy == "prefer_base":
                    pass  # Keep base unchanged
                else:  # "keep_both"
                    inc_recipe.recipe_id = f"rec_{uuid.uuid4().hex[:8]}"
                    base_manifest.recipes.append(inc_recipe)
            else:
                base_manifest.recipes.append(inc_recipe)

        # 5. Update session metadata and timestamp
        base_manifest.updated_at = datetime.now(timezone.utc).isoformat()
        if "card_count" in base_manifest.session_metadata:
            base_manifest.session_metadata["card_count"] = len(base_manifest.cards)
        if "recipe_count" in base_manifest.session_metadata:
            base_manifest.session_metadata["recipe_count"] = len(base_manifest.recipes)

        # 6. Re-pack merged directory into .recipeboxer ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            # Write updated manifest.json
            manifest_json_str = base_manifest.model_dump_json(indent=2)
            zf.writestr("manifest.json", manifest_json_str.encode("utf-8"))

            # Write all assets in base_dir
            for asset in base_manifest.assets:
                asset_file = base_dir / asset.archive_path
                if asset_file.is_file():
                    with open(asset_file, "rb") as af:
                        zf.writestr(asset.archive_path, af.read())

        zip_bytes = zip_buffer.getvalue()

        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with open(out_p, "wb") as f:
                f.write(zip_bytes)

        return base_manifest, zip_bytes


def restore_archive_to_session(
    archive_file_or_bytes: Union[str, Path, bytes, BinaryIO],
    store: SessionStore,
    manager: Optional[SessionManager] = None,
    cache: Optional[FingerprintCache] = None,
    target_session_id: Optional[str] = None,
    scans_target_dir: Optional[Union[str, Path]] = None,
    auto_resume: bool = False,
) -> SessionDetail:
    """Restores an uploaded .recipeboxer project archive into SQLite SessionStore,
    in-memory SessionManager, and FingerprintCache.

    Args:
        archive_file_or_bytes: Uploaded archive file or bytes.
        store: SQLite SessionStore instance.
        manager: Optional SessionManager instance.
        cache: Optional FingerprintCache instance.
        target_session_id: Desired session ID override.
        scans_target_dir: Destination directory for extracted image assets.
        auto_resume: Whether to prepare HITL or execution queue.

    Returns:
        Restored SessionDetail record.
    """
    settings = get_settings()
    base_cache_dir = getattr(store, "cache_dir", settings.cache_dir)

    # Determine session ID
    temp_extract_dir = tempfile.mkdtemp(prefix="recipeboxer_import_")
    try:
        manifest, extracted_path = extract_recipeboxer_archive(
            archive_file_or_bytes, temp_extract_dir, verify_hashes=True
        )

        sid = (
            target_session_id
            or manifest.session_metadata.get("session_id")
            or f"sess_{uuid.uuid4().hex[:8]}"
        )

        # Permanent scans destination for this session
        final_scans_dir = (
            Path(scans_target_dir) if scans_target_dir
            else Path(base_cache_dir) / "sessions" / sid / "scans"
        )
        final_scans_dir.mkdir(parents=True, exist_ok=True)

        # Copy assets and build ScannedCard objects
        scanned_cards: List[ScannedCard] = []
        card_obj_list: List[Dict[str, Any]] = []

        asset_map = {a.sha256_hash: a for a in manifest.assets}

        for card_rec in manifest.cards:
            asset = asset_map.get(card_rec.sha256_hash)
            if asset:
                src_asset_file = extracted_path / asset.archive_path
                # asset.filename is untrusted manifest data: reduce it to a portable basename
                safe_name = asset.filename.replace("\\", "/").rsplit("/", 1)[-1].replace(":", "_")
                if safe_name in ("", ".", ".."):
                    safe_name = Path(asset.archive_path).name
                dst_asset_file = final_scans_dir / safe_name
                if src_asset_file.is_file():
                    shutil.copy2(src_asset_file, dst_asset_file)

                sc_card = ScannedCard(
                    card_id=card_rec.card_id,
                    file_path=dst_asset_file,
                    filename=asset.filename,
                    sha256_hash=card_rec.sha256_hash,
                    file_size_bytes=asset.size_bytes,
                    width=asset.width or 0,
                    height=asset.height or 0,
                )
                scanned_cards.append(sc_card)

                card_dict = {
                    "card_id": card_rec.card_id,
                    "file_path": str(dst_asset_file),
                    "filename": asset.filename,
                    "sha256_hash": card_rec.sha256_hash,
                    "role": card_rec.role,
                    "role_confidence": card_rec.role_confidence,
                    "width": asset.width or 0,
                    "height": asset.height or 0,
                }
                card_obj_list.append(card_dict)

                # Persist visual fingerprint into SQLite FingerprintCache if available
                if card_rec.fingerprint:
                    card_rec.fingerprint.image_path = str(dst_asset_file)
                    if cache:
                        cache.put(card_rec.fingerprint)

        # Build recipes
        recipes: List[RecipeSchema] = []
        for r in manifest.recipes:
            recipes.append(
                RecipeSchema(
                    recipe_id=r.recipe_id,
                    title=r.title,
                    description=r.description,
                    yield_servings=r.yield_servings,
                    prep_time=r.prep_time,
                    cook_time=r.cook_time,
                    total_time=r.total_time,
                    ingredients=r.ingredients,
                    directions=r.directions,
                    tags=r.tags,
                    source_notes=r.source_notes,
                    card_ids=r.card_ids,
                    image_urls=r.image_urls,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )

        # Build proposals
        proposals: List[Dict[str, Any]] = [p.model_dump() for p in manifest.proposals]

        # Determine pipeline state
        raw_state = manifest.session_metadata.get("status", "COMPLETED")
        try:
            p_state = PipelineState(raw_state)
        except Exception:
            p_state = PipelineState.COMPLETED

        # Persist session into SQLite SessionStore
        store.save_session(
            session_id=sid,
            state=p_state,
            mode=manifest.session_metadata.get("mode", "parallel"),
            folder_path=str(final_scans_dir),
            config=manifest.session_metadata,
            card_count=len(scanned_cards),
            recipe_count=len(recipes),
            created_at=manifest.created_at,
            updated_at=manifest.updated_at,
        )

        store.save_cards(sid, card_obj_list)
        store.save_proposals(sid, proposals)
        store.save_recipes(sid, recipes)

        # Ingest into in-memory SessionManager if provided
        if manager:
            sess = PipelineSession(session_id=sid, cache=cache)
            sess.state = p_state
            sess.cards = scanned_cards
            sess.scanned_paths = [c.file_path for c in scanned_cards]
            for card_rec in manifest.cards:
                if card_rec.fingerprint:
                    sess.fingerprints[card_rec.card_id] = card_rec.fingerprint
            sess.recipes = recipes
            manager.register_session(sess)

        detail = store.get_session_detail(sid)
        if detail is None:
            raise ValueError(f"Failed to construct session detail for restored session {sid}")

        return detail

    finally:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
