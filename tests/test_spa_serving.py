"""Tests for embedded React SPA static file hosting and catch-all fallback routing."""

from pathlib import Path
from typing import AsyncGenerator
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.storage.session_store import SessionStore


@pytest.fixture
def app_with_static(tmp_path: Path):
    """Creates a FastAPI app instance with a populated temporary static directory."""
    session_manager = SessionManager()
    session_store = SessionStore(db_path=":memory:")
    app = create_app(session_manager=session_manager, session_store=session_store)
    return app


@pytest_asyncio.fixture
async def spa_client(app_with_static) -> AsyncGenerator[httpx.AsyncClient, None]:
    transport = ASGITransport(app=app_with_static)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestSPAServing:
    """Test suite covering static asset mounting and SPA fallback routing."""

    @pytest.mark.asyncio
    async def test_root_path_returns_html(self, spa_client: httpx.AsyncClient):
        """Test GET / returns 200 OK with HTML content."""
        res = await spa_client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "RecipeBoxer" in res.text

    @pytest.mark.asyncio
    async def test_client_subroutes_fallback_to_spa(self, spa_client: httpx.AsyncClient):
        """Test client-side navigation routes (/queue, /review, /export) return 200 HTML."""
        subroutes = ["/queue", "/review", "/export", "/editor", "/settings", "/sessions/sess_123"]
        for route in subroutes:
            res = await spa_client.get(route)
            assert res.status_code == 200
            assert "text/html" in res.headers["content-type"]
            assert "RecipeBoxer" in res.text

    @pytest.mark.asyncio
    async def test_unmatched_api_route_returns_404_json(self, spa_client: httpx.AsyncClient):
        """Test unmatched API paths under /api/* return 404 JSON, NOT HTML fallback."""
        res = await spa_client.get("/api/unknown_endpoint_xyz")
        assert res.status_code == 404
        assert "application/json" in res.headers["content-type"]

    @pytest.mark.asyncio
    async def test_static_assets_serving(self, tmp_path: Path):
        """Test static assets served under /assets/ return correct content."""
        static_dir = Path(__file__).parent.parent / "recipeboxer" / "api" / "static"
        assets_dir = static_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        test_asset = assets_dir / "test_bundle.js"
        test_asset.write_text("console.log('recipeboxer-bundle');", encoding="utf-8")

        session_manager = SessionManager()
        session_store = SessionStore(db_path=":memory:")
        app = create_app(session_manager=session_manager, session_store=session_store)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/assets/test_bundle.js")
            assert res.status_code == 200
            assert "recipeboxer-bundle" in res.text

        # Cleanup
        if test_asset.exists():
            test_asset.unlink()
