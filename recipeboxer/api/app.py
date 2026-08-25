"""FastAPI application factory and middleware configuration for RecipeBoxer."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from recipeboxer.agent.session import SessionManager, get_session_manager
from recipeboxer.api.routes import router as api_router
from recipeboxer.config import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(
    settings: Optional[Settings] = None,
    session_manager: Optional[SessionManager] = None,
) -> FastAPI:
    """Creates and configures a FastAPI application instance.

    Args:
        settings: Optional Settings override (defaults to global settings).
        session_manager: Optional SessionManager override for dependency injection in tests.

    Returns:
        Configured FastAPI application instance.
    """
    app_settings = settings or get_settings()
    app_session_manager = session_manager or get_session_manager()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manages application lifecycle startup and shutdown events."""
        # 1. Startup
        logger.info("Initializing RecipeBoxer application...")
        app_settings.ensure_directories()
        app.state.settings = app_settings
        app.state.session_manager = app_session_manager
        logger.info(
            f"RecipeBoxer startup complete. Cache DB: {app_settings.cache_db_path}, "
            f"Export Dir: {app_settings.export_dir}"
        )
        yield
        # 2. Shutdown
        logger.info("Shutting down RecipeBoxer application...")
        for session in app_session_manager.get_all_sessions():
            if not session.approval_event.is_set() and session.state not in (
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            ):
                session.cancel()
        logger.info("RecipeBoxer shutdown complete.")

    app = FastAPI(
        title="RecipeBoxer API",
        description="Intelligent Recipe Card Digitization and HITL Correlation Engine",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Attach shared state
    app.state.settings = app_settings
    app.state.session_manager = app_session_manager

    # 1. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Global Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(l) for l in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg', 'Invalid value')}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation error", "errors": errors},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(f"Unhandled internal server error: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "error": str(exc)},
        )

    # 3. Mount REST API Routes under /api
    app.include_router(api_router, prefix="/api")

    return app
