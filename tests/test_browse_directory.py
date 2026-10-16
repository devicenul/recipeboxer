"""Unit and integration tests for native system directory browsing endpoints."""

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from recipeboxer.agent.session import SessionManager
from recipeboxer.api.app import create_app


@pytest.fixture(autouse=True)
def fake_tkinter():
    """Installs a fake ``tkinter`` / ``tkinter.filedialog`` layer for every test.

    The endpoint only needs ``tkinter.Tk`` (a hidden root window) and
    ``tkinter.filedialog.askdirectory``. Real Tk needs an X display (and tkinter is not
    installed at all in some headless environments), so the tests replace the whole GUI layer;
    the individual tests then ``patch("tkinter.filedialog.askdirectory", ...)`` as intended.
    """
    fake_tk = types.ModuleType("tkinter")
    fake_dialog = types.ModuleType("tkinter.filedialog")
    fake_dialog.askdirectory = MagicMock(return_value="")
    fake_tk.Tk = MagicMock(name="Tk")
    fake_tk.filedialog = fake_dialog
    with patch.dict(sys.modules, {"tkinter": fake_tk, "tkinter.filedialog": fake_dialog}):
        yield fake_tk


@pytest.fixture
def app() -> Any:
    manager = SessionManager()
    return create_app(session_manager=manager)


@pytest.fixture
def client(app) -> httpx.AsyncClient:
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestBrowseDirectoryEndpoints:
    """Test suite covering native folder browsing endpoint behavior, dialog mocking, and pipeline integration."""

    @pytest.mark.asyncio
    async def test_browse_directory_success_with_selected_path(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test POST /api/system/browse-directory returns absolute path when user selects directory."""
        mock_path = str(tmp_path.resolve())

        with patch("recipeboxer.api.routes.asyncio.get_running_loop") as mock_loop_fn:
            # We can mock the dialog helper directly
            pass

        with patch("tkinter.filedialog.askdirectory", return_value=mock_path):
            res = await client.post("/api/system/browse-directory", json={"initial_dir": str(tmp_path)})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["canceled"] is False
            assert data["path"] == mock_path
            assert data["error"] is None

    @pytest.mark.asyncio
    async def test_browse_directory_canceled_returns_null_path(self, client: httpx.AsyncClient):
        """Test POST /api/system/browse-directory returns canceled=True when user cancels folder picker."""
        with patch("tkinter.filedialog.askdirectory", return_value=""):
            res = await client.post("/api/system/browse-directory", json={})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["canceled"] is True
            assert data["path"] is None

    @pytest.mark.asyncio
    async def test_browse_directory_alias_endpoint(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test POST /api/browse/directory alias returns the same response."""
        mock_path = str(tmp_path.resolve())
        with patch("tkinter.filedialog.askdirectory", return_value=mock_path):
            res = await client.post("/api/browse/directory", json={"initial_dir": str(tmp_path)})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["path"] == mock_path

    @pytest.mark.asyncio
    async def test_browse_directory_get_endpoint(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test GET /api/system/browse-directory works with query parameter."""
        mock_path = str(tmp_path.resolve())
        with patch("tkinter.filedialog.askdirectory", return_value=mock_path):
            res = await client.get(f"/api/system/browse-directory?initial_dir={mock_path}")
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["path"] == mock_path

    @pytest.mark.asyncio
    async def test_browse_directory_get_alias_with_title(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test GET /api/browse/directory alias works with initial_dir and custom title."""
        mock_path = str(tmp_path.resolve())
        with patch("tkinter.filedialog.askdirectory", return_value=mock_path) as mock_ask:
            res = await client.get(f"/api/browse/directory?initial_dir={mock_path}&title=Custom+Title")
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["path"] == mock_path
            mock_ask.assert_called_once()
            _, kwargs = mock_ask.call_args
            assert kwargs["title"] == "Custom Title"

    @pytest.mark.asyncio
    async def test_browse_directory_dialog_exception_gracefully_handled(self, client: httpx.AsyncClient):
        """Test unexpected tkinter failure returns success=False with error message without crashing."""
        with patch("tkinter.filedialog.askdirectory", side_effect=RuntimeError("GUI subsystem unavailable")):
            res = await client.post("/api/system/browse-directory", json={})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is False
            assert data["path"] is None
            assert "GUI subsystem unavailable" in str(data["error"])

    @pytest.mark.asyncio
    async def test_browse_directory_whitespace_inputs_and_nonexistent_dir(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test whitespace initial_dir and title are sanitized cleanly without crash."""
        mock_path = str(tmp_path.resolve())
        with patch("tkinter.filedialog.askdirectory", return_value=mock_path) as mock_ask:
            res = await client.post(
                "/api/system/browse-directory",
                json={"initial_dir": "   ", "title": "   "},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["path"] == mock_path
            # Verify askdirectory was called with fallback title and cwd
            mock_ask.assert_called_once()
            _, kwargs = mock_ask.call_args
            assert kwargs["title"] == "Select Recipe Scans Directory"

    @pytest.mark.asyncio
    async def test_selected_absolute_directory_starts_pipeline(self, client: httpx.AsyncClient, tmp_path: Path):
        """Test that an absolute path selected via browse starts pipeline successfully."""
        # Create a sample image card
        card_file = tmp_path / "card_01.jpg"
        card_file.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xd9")

        abs_path = str(tmp_path.resolve())
        res = await client.post(
            "/api/pipeline/start",
            json={
                "directory_path": abs_path,
                "mode": "parallel",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["total_scans_found"] >= 1

    @pytest.mark.asyncio
    async def test_browse_directory_no_display_returns_gui_unavailable(self, client: httpx.AsyncClient, fake_tkinter):
        """Headless host: Tk() fails (no $DISPLAY) -> documented 'GUI subsystem unavailable' error, no crash."""
        fake_tkinter.Tk.side_effect = RuntimeError("no display name and no $DISPLAY environment variable")
        with patch("tkinter.filedialog.askdirectory") as mock_ask:
            res = await client.post("/api/system/browse-directory", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["canceled"] is False
        assert data["path"] is None
        assert data["error"].startswith("GUI subsystem unavailable")
        assert "$DISPLAY" in data["error"]
        mock_ask.assert_not_called()

    @pytest.mark.asyncio
    async def test_browse_directory_tkinter_missing_returns_gui_unavailable(self, client: httpx.AsyncClient):
        """tkinter not installed -> documented 'GUI subsystem unavailable' error, no crash."""
        with patch.dict(sys.modules, {"tkinter": None, "tkinter.filedialog": None}):
            res = await client.post("/api/system/browse-directory", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["path"] is None
        assert data["error"].startswith("GUI subsystem unavailable")
