"""FastAPI routes for user settings, credential management, cost estimation, and privacy notice."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AliasChoices, BaseModel, Field, field_validator

from recipeboxer.storage.settings_store import (
    SUPPORTED_PROFILES,
    SettingsStore,
    get_settings_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Dependency Injection
# ---------------------------------------------------------------------------

def get_store(request: Request) -> SettingsStore:
    """Dependency injection helper to retrieve SettingsStore from application state."""
    return getattr(request.app.state, "settings_store", get_settings_store())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SettingsResponse(BaseModel):
    """Public settings schema.

    CRITICAL SECURITY REQUIREMENT:
    Never returns the API key string. Only reports whether a key is configured
    (has_api_key) and its storage origin (key_source).
    """

    quality_profile: str = Field(
        ...,
        description="Active quality profile: economy, balanced, or best",
    )
    stage_models: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-stage model overrides for advanced users",
    )
    spend_cap_usd: Optional[float] = Field(
        default=None,
        description="Per-run spend cap in USD (pipeline pauses when reached)",
    )
    monthly_cap_usd: Optional[float] = Field(
        default=None,
        description="Monthly spend cap in USD",
    )
    rpm_limit: int = Field(
        default=60,
        description="Requests per minute rate limit for Gemini API calls",
    )
    extra_effort: bool = Field(
        default=False,
        description="Toggle extra reasoning / thinking budget for hard-to-read cards",
    )
    embeddings_enabled: bool = Field(
        default=False,
        description="Toggle embedding-based semantic retrieval and pairing",
    )
    has_api_key: bool = Field(
        ...,
        description="Whether a valid, usable Gemini API key is configured",
    )
    key_source: str = Field(
        ...,
        description="Source of the configured API key: 'keyring', 'settings_file', 'environment', or 'none'",
    )


class SettingsUpdateRequest(BaseModel):
    """Request schema for partial or full updates to user settings."""

    quality_profile: Optional[str] = Field(
        default=None,
        description="Active quality profile: 'economy', 'balanced', or 'best'",
    )
    stage_models: Optional[Dict[str, str]] = Field(
        default=None,
        description="Per-stage model overrides (e.g. {'FINGERPRINT': 'gemini-3.8-flash'})",
    )
    spend_cap_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Per-run spend cap in USD",
    )
    monthly_cap_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Monthly spend cap in USD",
    )
    rpm_limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Requests per minute rate limit",
    )
    extra_effort: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("extra_effort", "thinking_budget_extra"),
        description="Toggle extra thinking effort for hard-to-read cards",
    )
    embeddings_enabled: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("embeddings_enabled", "embeddings"),
        description="Toggle embedding-based features",
    )

    @field_validator("quality_profile")
    @classmethod
    def validate_quality_profile(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        norm = v.lower().strip()
        if norm not in SUPPORTED_PROFILES:
            raise ValueError(
                f"Invalid quality_profile '{v}'. Must be one of: {', '.join(SUPPORTED_PROFILES)}"
            )
        return norm


class ApiKeyRequest(BaseModel):
    """Request schema for configuring Gemini API key."""

    api_key: str = Field(
        ...,
        min_length=1,
        description="Gemini API key string",
    )


class ApiKeyResponse(BaseModel):
    """Response schema following API key configuration."""

    success: bool = True
    has_api_key: bool = True
    key_source: str
    message: str = "Gemini API key saved successfully"


class ApiKeyDeleteResponse(BaseModel):
    """Response schema following API key removal."""

    success: bool = True
    has_api_key: bool
    key_source: str
    message: str = "Gemini API key removed"


class CostEstimateResponse(BaseModel):
    """Pre-run cost estimate breakdown."""

    low: Optional[float] = Field(None, description="Lower bound cost estimate in USD")
    expected: Optional[float] = Field(None, description="Expected/mean cost estimate in USD")
    high: Optional[float] = Field(None, description="Upper bound cost estimate in USD")
    assumptions: Dict[str, Any] = Field(
        default_factory=dict,
        description="Estimation parameters and token usage assumptions",
    )


class PrivacyNoticeResponse(BaseModel):
    """Privacy notice details regarding free-tier versus paid Gemini API data usage."""

    notice: str = Field(..., description="User-facing privacy guidance notice")
    terms_url: str = Field(..., description="URL to current Google Gemini API terms")
    privacy_policy_url: str = Field(..., description="URL to Google Privacy Policy")
    paid_vs_free_summary: str = Field(..., description="Key distinctions between free and paid tiers")


# ---------------------------------------------------------------------------
# Settings Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get User Settings",
)
async def get_settings_endpoint(
    store: SettingsStore = Depends(get_store),
) -> SettingsResponse:
    """Retrieves current user settings and API key status.

    Never returns the secret API key itself.
    """
    public_dict = store.get_public_settings_dict()
    return SettingsResponse.model_validate(public_dict)


@router.put(
    "",
    response_model=SettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update User Settings",
)
async def update_settings_endpoint(
    payload: SettingsUpdateRequest,
    store: SettingsStore = Depends(get_store),
) -> SettingsResponse:
    """Updates user settings preferences and persists them to the configuration directory."""
    update_data = payload.model_dump(exclude_unset=True)
    store.update_settings(update_data)
    public_dict = store.get_public_settings_dict()
    return SettingsResponse.model_validate(public_dict)


# ---------------------------------------------------------------------------
# API Key Management Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/api-key",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_200_OK,
    summary="Set Gemini API Key",
)
async def set_api_key_endpoint(
    payload: ApiKeyRequest,
    store: SettingsStore = Depends(get_store),
) -> ApiKeyResponse:
    """Securely stores Gemini API key.

    Attempts storage via OS keyring; falls back to 0o600 settings file if
    keyring is not available.
    """
    key = payload.api_key.strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key cannot be empty or blank",
        )

    storage_source = store.set_api_key(key)
    return ApiKeyResponse(
        success=True,
        has_api_key=True,
        key_source=storage_source,
        message=f"Gemini API key saved successfully via {storage_source}",
    )


@router.delete(
    "/api-key",
    response_model=ApiKeyDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Gemini API Key",
)
async def delete_api_key_endpoint(
    store: SettingsStore = Depends(get_store),
) -> ApiKeyDeleteResponse:
    """Removes stored Gemini API key from keyring and settings file."""
    store.delete_api_key()
    return ApiKeyDeleteResponse(
        success=True,
        has_api_key=store.has_api_key(),
        key_source=store.get_key_source(),
        message="Gemini API key removed successfully",
    )


# ---------------------------------------------------------------------------
# Cost Estimation Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/estimate",
    response_model=CostEstimateResponse,
    status_code=status.HTTP_200_OK,
    summary="Estimate Pre-Run Pipeline Cost",
)
async def estimate_cost_endpoint(
    n_cards: int = Query(..., ge=1, description="Number of recipe cards to process"),
    profile: Optional[str] = Query(
        None,
        description="Quality profile override ('economy', 'balanced', 'best'). Defaults to active settings.",
    ),
    store: SettingsStore = Depends(get_store),
) -> CostEstimateResponse:
    """Calculates pre-run estimated cost using pricing tables and calibration models."""
    active_profile = (profile or store.get_settings().quality_profile).lower().strip()
    if active_profile not in SUPPORTED_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid quality profile '{active_profile}'. Must be one of: {', '.join(SUPPORTED_PROFILES)}",
        )

    from recipeboxer.ai.cost import PriceTable, estimate_run_cost

    def _usd(value: Any) -> Optional[float]:
        # None means "unpriced model in the profile" - surface it, never coerce to 0.
        return None if value is None else float(value)

    prices = PriceTable.load()
    est = estimate_run_cost(n_cards=n_cards, profile=active_profile, prices=prices)
    return CostEstimateResponse(
        low=_usd(est.get("low")),
        expected=_usd(est.get("expected")),
        high=_usd(est.get("high")),
        assumptions=dict(est.get("assumptions", {})),
    )


# ---------------------------------------------------------------------------
# Privacy Notice Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/privacy-notice",
    response_model=PrivacyNoticeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Privacy Notice",
)
async def get_privacy_notice_endpoint() -> PrivacyNoticeResponse:
    """Returns static privacy guidance regarding Gemini API tier data usage."""
    return PrivacyNoticeResponse(
        notice=(
            "When using Google's unpaid / free-tier Gemini API, prompt data and card images "
            "may be reviewed by human reviewers and utilized by Google to train and improve products. "
            "Paid Gemini API usage generally does not permit data usage for model training. "
            "Because personal and family recipe cards frequently contain private handwriting, family names, "
            "and personal margin notes, please exercise caution and review Google's terms before proceeding."
        ),
        terms_url="https://ai.google.dev/gemini-api/terms",
        privacy_policy_url="https://policies.google.com/privacy",
        paid_vs_free_summary=(
            "Free tier: Data may be retained and used for Google product improvement. "
            "Paid tier: Data is not used to train Google models and remains strictly private."
        ),
    )
