"""Unit and integration tests for RecipeBoxer CLI entry points."""

from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from recipeboxer import __version__
from recipeboxer.cli import create_parser, main


class TestCLIParser:
    """Test suite for CLI argument parser configuration and resolution."""

    def test_default_arguments(self, monkeypatch: pytest.MonkeyPatch):
        """Verifies parser defaults with clean environment."""
        monkeypatch.delenv("RECIPEBOXER_API_HOST", raising=False)
        monkeypatch.delenv("RECIPEBOXER_API_PORT", raising=False)

        parser = create_parser()
        args = parser.parse_args([])

        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.log_level == "info"
        assert args.reload is False

    def test_custom_host_and_port(self):
        """Verifies parsing custom --host and --port arguments."""
        parser = create_parser()
        args = parser.parse_args(["--host", "0.0.0.0", "--port", "9050"])

        assert args.host == "0.0.0.0"
        assert args.port == 9050

    def test_custom_log_level(self):
        """Verifies parsing valid --log-level options."""
        parser = create_parser()
        for level in ["critical", "error", "warning", "info", "debug", "trace"]:
            args = parser.parse_args(["--log-level", level])
            assert args.log_level == level

    def test_invalid_log_level_exits(self):
        """Verifies parser rejects invalid --log-level choices with exit code 2."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--log-level", "verbose_invalid"])
        assert exc_info.value.code == 2

    def test_reload_flag(self):
        """Verifies parsing --reload boolean flag."""
        parser = create_parser()
        args = parser.parse_args(["--reload"])
        assert args.reload is True

    def test_env_var_fallbacks(self, monkeypatch: pytest.MonkeyPatch):
        """Verifies environment variables populate defaults when CLI flags are omitted."""
        monkeypatch.setenv("RECIPEBOXER_API_HOST", "192.168.1.100")
        monkeypatch.setenv("RECIPEBOXER_API_PORT", "8888")

        parser = create_parser()
        args = parser.parse_args([])

        assert args.host == "192.168.1.100"
        assert args.port == 8888

    def test_cli_flags_override_env_vars(self, monkeypatch: pytest.MonkeyPatch):
        """Verifies explicit CLI arguments override environment variable defaults."""
        monkeypatch.setenv("RECIPEBOXER_API_HOST", "192.168.1.100")
        monkeypatch.setenv("RECIPEBOXER_API_PORT", "8888")

        parser = create_parser()
        args = parser.parse_args(["--host", "10.0.0.1", "--port", "7000"])

        assert args.host == "10.0.0.1"
        assert args.port == 7000

    @pytest.mark.parametrize("flag", ["-V", "-v", "--version"])
    def test_version_flag_output(self, flag: str, capsys: pytest.CaptureFixture):
        """Verifies -V, -v, and --version output application version and exit with code 0."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([flag])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = captured.out or captured.err
        assert f"RecipeBoxer {__version__}" in output

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_help_flag_output(self, flag: str, capsys: pytest.CaptureFixture):
        """Verifies -h and --help print usage instructions and exit with code 0."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([flag])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "RecipeBoxer" in captured.out
        assert "--host" in captured.out
        assert "--port" in captured.out
        assert "--reload" in captured.out


class TestCLIMain:
    """Test suite for CLI main execution function and uvicorn integration."""

    @patch("multiprocessing.freeze_support")
    @patch("uvicorn.run")
    @patch("recipeboxer.cli.create_app")
    def test_main_calls_freeze_support(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock, mock_freeze: MagicMock
    ):
        """Verifies multiprocessing.freeze_support is invoked before server startup."""
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        exit_code = main([])
        assert exit_code == 0
        mock_freeze.assert_called_once()
        mock_create_app.assert_called_once()

    @patch("uvicorn.run")
    @patch("recipeboxer.cli.create_app")
    def test_main_runs_uvicorn_with_defaults(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock
    ):
        """Verifies main invokes uvicorn.run with default settings."""
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        exit_code = main([])
        assert exit_code == 0
        mock_uvicorn_run.assert_called_once_with(
            mock_app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            reload=False,
            lifespan="on",
        )

    @patch("uvicorn.run")
    @patch("recipeboxer.cli.create_app")
    def test_main_runs_uvicorn_with_custom_arguments(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock
    ):
        """Verifies main forwards custom CLI arguments to uvicorn.run."""
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        exit_code = main([
            "--host", "0.0.0.0",
            "--port", "9999",
            "--log-level", "debug",
            "--reload",
        ])
        assert exit_code == 0
        mock_uvicorn_run.assert_called_once_with(
            mock_app,
            host="0.0.0.0",
            port=9999,
            log_level="debug",
            reload=True,
            lifespan="on",
        )

    @patch("uvicorn.run")
    @patch("recipeboxer.cli.create_app")
    def test_main_frozen_mode_disables_reload(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Verifies that in frozen PyInstaller execution, reload is forcefully disabled."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        mock_app = MagicMock()
        mock_create_app.return_value = mock_app

        exit_code = main(["--reload"])
        assert exit_code == 0
        mock_uvicorn_run.assert_called_once_with(
            mock_app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            reload=False,
            lifespan="on",
        )

    @patch("uvicorn.run", side_effect=KeyboardInterrupt)
    @patch("recipeboxer.cli.create_app")
    def test_main_handles_keyboard_interrupt(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock
    ):
        """Verifies KeyboardInterrupt returns exit code 0 cleanly."""
        exit_code = main([])
        assert exit_code == 0

    @patch("uvicorn.run", side_effect=RuntimeError("Address already in use"))
    @patch("recipeboxer.cli.create_app")
    def test_main_handles_server_startup_exception(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock
    ):
        """Verifies unhandled server startup exception returns exit code 1."""
        exit_code = main([])
        assert exit_code == 1

    @patch("uvicorn.run")
    @patch("recipeboxer.cli.create_app")
    def test_main_sys_argv_fallback(
        self, mock_create_app: MagicMock, mock_uvicorn_run: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Verifies main(None) consumes sys.argv."""
        monkeypatch.setattr(sys, "argv", ["recipeboxer", "--port", "5555"])
        exit_code = main(None)
        assert exit_code == 0
        mock_uvicorn_run.assert_called_once_with(
            mock_create_app.return_value,
            host="127.0.0.1",
            port=5555,
            log_level="info",
            reload=False,
            lifespan="on",
        )


class TestModuleExecution:
    """Test suite for package and module execution endpoints."""

    def test_dunder_main_module_delegates_to_cli_main(self):
        """Verifies recipeboxer.__main__ delegates directly to recipeboxer.cli:main."""
        with patch("recipeboxer.cli.main", return_value=0) as mock_main:
            with patch("sys.exit") as mock_exit:
                runpy.run_module("recipeboxer.__main__", run_name="__main__")
                mock_main.assert_called_once()
                mock_exit.assert_called_once_with(0)

    def test_subprocess_version_flag(self):
        """Verifies python -m recipeboxer --version subprocess execution."""
        result = subprocess.run(
            [sys.executable, "-m", "recipeboxer", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert f"RecipeBoxer {__version__}" in result.stdout

    def test_subprocess_help_flag(self):
        """Verifies python -m recipeboxer --help subprocess execution."""
        result = subprocess.run(
            [sys.executable, "-m", "recipeboxer", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "RecipeBoxer" in result.stdout
        assert "--host" in result.stdout
        assert "--port" in result.stdout

    def test_subprocess_invalid_flag_returns_code_2(self):
        """Verifies python -m recipeboxer --invalid-flag returns exit code 2."""
        result = subprocess.run(
            [sys.executable, "-m", "recipeboxer", "--invalid-flag-xyz"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr
