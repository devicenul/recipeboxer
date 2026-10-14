"""FastAPI application factory and middleware configuration for RecipeBoxer."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from recipeboxer.agent.session import SessionManager, get_session_manager
from recipeboxer.api.routes import router as api_router
from recipeboxer.api.settings_routes import router as settings_router
from recipeboxer.config import Settings, get_settings
from recipeboxer.storage.session_store import SessionStore, get_session_store
from recipeboxer.storage.settings_store import SettingsStore, get_settings_store

logger = logging.getLogger(__name__)

FALLBACK_SPA_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RecipeBoxer</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .card { background: #1e293b; padding: 2rem 3rem; border-radius: 0.75rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 480px; }
    h1 { margin-top: 0; color: #38bdf8; font-size: 1.75rem; }
    p { color: #94a3b8; line-height: 1.5; }
    .badge { display: inline-block; background: #0369a1; color: #e0f2fe; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.875rem; margin-top: 1rem; }
  </style>
</head>
<body>
  <div id="root">
    <div class="card">
      <h1>RecipeBoxer</h1>
      <p>FastAPI Backend &amp; Embedded SPA Server is active.</p>
      <span class="badge">API Ready on /api</span>
    </div>
  </div>
</body>
</html>"""


def create_app(
    settings: Optional[Settings] = None,
    session_manager: Optional[SessionManager] = None,
    session_store: Optional[SessionStore] = None,
    settings_store: Optional[SettingsStore] = None,
) -> FastAPI:
    """Creates and configures a FastAPI application instance.

    Args:
        settings: Optional Settings override (defaults to global settings).
        session_manager: Optional SessionManager override for dependency injection in tests.
        session_store: Optional SessionStore override.
        settings_store: Optional SettingsStore override.

    Returns:
        Configured FastAPI application instance.
    """
    app_settings = settings or get_settings()
    app_session_manager = session_manager or get_session_manager()
    app_session_store = session_store or get_session_store(app_settings.cache_db_path)
    app_settings_store = settings_store or get_settings_store()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manages application lifecycle startup and shutdown events."""
        # 1. Startup
        logger.info("Initializing RecipeBoxer application...")
        app_settings.ensure_directories()
        app.state.settings = app_settings
        app.state.session_manager = app_session_manager
        app.state.session_store = app_session_store
        app.state.settings_store = app_settings_store
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
    app.state.session_store = app_session_store
    app.state.settings_store = app_settings_store

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
    app.include_router(settings_router, prefix="/api")

    # 4. Static Asset Hosting & SPA Catch-All
    static_dir = Path(__file__).parent / "static"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/assets", StaticFiles(directory=str(assets_dir), check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_spa_root() -> Any:
        """Serves SPA index.html or fallback placeholder."""
        index_file = static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse(content=FALLBACK_SPA_HTML)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_fallback(full_path: str) -> Any:
        """Catch-all route serving static files or fallback to index.html for SPA client-side routing."""
        # Never catch unmatched API routes — return 404
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API endpoint not found")

        # Static file match in static root (e.g. favicon.ico, manifest.json)
        try:
            target_file = (static_dir / full_path).resolve()
            if target_file.is_file() and target_file.is_relative_to(static_dir.resolve()):
                return FileResponse(target_file)
        except Exception:
            pass

        # SPA client-side routing: return index.html
        index_file = static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)

        return HTMLResponse(content=FALLBACK_SPA_HTML)

    return app
