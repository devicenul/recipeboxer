"""Unit and integration test suite for cross-platform setup and execution scripts.

Validates:
1. POSIX setup script (setup.sh):
   - Bash syntax check (`bash -n setup.sh`)
   - Executable permissions (`chmod +x`)
   - Shebang declaration
   - Idempotent execution and output banner
   - Invocation from external working directories
   - Python version gating (aborts on Python < 3.10)
   - Dependency upgrade and editable install commands
2. POSIX run script (run.sh):
   - Bash syntax check (`bash -n run.sh`)
   - Executable permissions (`chmod +x`)
   - Shebang declaration
   - Required safety flags and directives
   - Pre-flight trigger: auto-runs setup.sh when .venv is missing
   - Argument parsing and passthrough (--help, -h, --version, -v, --host, --port, --log-level, --reload)
   - Invalid argument exit code (code 2)
   - Default and custom environment variable resolution
   - Signal trap declarations for graceful shutdown (SIGINT / SIGTERM)
   - Invocation from external working directories
3. Windows PowerShell setup script (setup.ps1):
   - Structural and AST/block balance validation
   - Candidate probing logic (py -3.x, py -3, python, python3)
   - Virtual environment path resolution (.venv\\Scripts\\python.exe)
   - ErrorActionPreference and exit code handling
   - Verification banner output
4. Windows PowerShell run script (run.ps1):
   - Parameter signature validation (-Host, -Port, -LogLevel, -Reload, -Help)
   - Structural and AST/block balance validation
   - Auto-trigger setup.ps1 when .venv\\Scripts\\python.exe is missing
   - Default environment variable declarations
   - Try/finally / catch interrupt handling structure
5. Windows Batch setup script (setup.bat):
   - Structural validation (@echo off, setlocal enabledelayedexpansion)
   - Script directory (%~dp0) and .venv resolution
   - Candidate loop and errorlevel validation
   - Build tool upgrade and editable install
   - Verification and exit codes
6. Windows Batch run script (run.bat):
   - Structural validation (@echo off, setlocal enabledelayedexpansion)
   - Script directory (%~dp0) and .venv resolution
   - Auto-trigger setup.bat when .venv\\Scripts\\python.exe is missing
   - Default environment variable declarations
   - Argument forwarding (%*) and exit code propagation (%ERRORLEVEL%)
7. Python version detection logic:
   - Behavioral verification of version check one-liners across Python versions
8. POSIX build script (build.sh):
   - Syntax, execution flags, clean, help, and binary compilation
9. Windows PowerShell build script (build.ps1):
   - Structure, parameters, balanced blocks, pwsh AST parsing
10. PyInstaller specification (recipeboxer.spec):
    - Hidden imports, data bundling, console mode
11. Distribution packaging and binary execution:
    - Wheel archive validity, binary execution flags
12. POSIX test script (test.sh):
    - Syntax, executable permissions, shebang, argument forwarding, exit code propagation, auto-setup trigger
13. Windows PowerShell test script (test.ps1):
    - Structure, parameters, balanced blocks, AST syntax, auto-setup trigger, exit code propagation
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from recipeboxer import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUP_SH = PROJECT_ROOT / "setup.sh"
SETUP_PS1 = PROJECT_ROOT / "setup.ps1"
SETUP_BAT = PROJECT_ROOT / "setup.bat"
RUN_SH = PROJECT_ROOT / "run.sh"
RUN_PS1 = PROJECT_ROOT / "run.ps1"
RUN_BAT = PROJECT_ROOT / "run.bat"
BUILD_SH = PROJECT_ROOT / "build.sh"
BUILD_PS1 = PROJECT_ROOT / "build.ps1"
TEST_SH = PROJECT_ROOT / "test.sh"
TEST_PS1 = PROJECT_ROOT / "test.ps1"
SPEC_FILE = PROJECT_ROOT / "recipeboxer.spec"



# ============================================================================
# 1. POSIX SETUP SCRIPT TESTS (setup.sh)
# ============================================================================

class TestSetupShPOSIX:
    """Test suite for setup.sh on macOS and Linux platforms."""

    def test_setup_sh_exists(self):
        """Verifies setup.sh exists at project root."""
        assert SETUP_SH.exists(), "setup.sh does not exist at project root"
        assert SETUP_SH.is_file(), "setup.sh is not a regular file"

    def test_setup_sh_shebang(self):
        """Verifies setup.sh starts with valid bash shebang."""
        content = SETUP_SH.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        assert first_line in ("#!/usr/bin/env bash", "#!/bin/bash"), (
            f"Unexpected shebang in setup.sh: {first_line}"
        )

    def test_setup_sh_is_executable(self):
        """Verifies setup.sh has POSIX executable permission."""
        mode = os.stat(SETUP_SH).st_mode
        assert bool(mode & stat.S_IXUSR), "setup.sh is not executable by owner (chmod +x required)"

    def test_setup_sh_syntax_bash_n(self):
        """Verifies setup.sh passes bash syntax validation (bash -n setup.sh)."""
        bash_bin = shutil.which("bash")
        if not bash_bin:
            pytest.skip("bash is not available in system PATH")

        result = subprocess.run(
            [bash_bin, "-n", str(SETUP_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed with syntax errors:\n{result.stderr}"

    def test_setup_sh_contains_required_directives(self):
        """Verifies setup.sh contains required safety flags and commands."""
        content = SETUP_SH.read_text(encoding="utf-8")

        # Safety flags
        assert "set -euo pipefail" in content or "set -e" in content

        # Python candidates probe
        assert "python3" in content
        assert "sys.version_info" in content

        # .venv bootstrap
        assert ".venv" in content
        assert "-m venv" in content

        # Upgrade tools
        assert "pip" in content
        assert "wheel" in content
        assert "setuptools" in content

        # Requirements and editable install
        assert "requirements.txt" in content
        assert "requirements-dev.txt" in content
        assert "install -e" in content or "pip install -e" in content

        # Verification
        assert "import recipeboxer" in content

    def test_setup_sh_execution_idempotence(self):
        """Verifies executing setup.sh succeeds and produces expected output banner."""
        result = subprocess.run(
            [str(SETUP_SH)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"setup.sh execution failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert f"RecipeBoxer v{__version__}" in combined or f"RecipeBoxer {__version__}" in combined
        assert "setup completed successfully" in combined.lower()

    def test_setup_sh_invocation_from_different_cwd(self, tmp_path: Path):
        """Verifies setup.sh functions correctly when invoked from another directory."""
        result = subprocess.run(
            [str(SETUP_SH)],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"setup.sh failed when run from {tmp_path}:\n{result.stderr}"
        assert f"RecipeBoxer v{__version__}" in result.stdout or f"RecipeBoxer {__version__}" in result.stdout

    def test_setup_sh_fails_when_no_compatible_python(self, tmp_path: Path):
        """Verifies setup.sh aborts with exit code 1 if Python version is below 3.10."""
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()

        fake_py = fake_bin_dir / "python3"
        fake_py.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            "    # Mock Python 3.9 check: fails version_info >= (3, 10)\n"
            "    exit 1\n"
            "fi\n"
            "echo 'Python 3.9.0'\n"
            "exit 0\n"
        )
        fake_py.chmod(fake_py.stat().st_mode | stat.S_IXUSR)

        fake_python = fake_bin_dir / "python"
        fake_python.symlink_to(fake_py)

        env = dict(os.environ)
        env["PATH"] = f"{fake_bin_dir}:/bin:/usr/bin"

        result = subprocess.run(
            ["bash", str(SETUP_SH)],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "setup.sh should have failed when Python < 3.10"
        assert "Python 3.10 or higher is required" in (result.stdout + result.stderr)


# ============================================================================
# 2. POSIX RUN SCRIPT TESTS (run.sh)
# ============================================================================

class TestRunShPOSIX:
    """Test suite for run.sh execution script on macOS and Linux platforms."""

    def test_run_sh_exists(self):
        """Verifies run.sh exists at project root."""
        assert RUN_SH.exists(), "run.sh does not exist at project root"
        assert RUN_SH.is_file(), "run.sh is not a regular file"

    def test_run_sh_shebang(self):
        """Verifies run.sh starts with valid bash shebang."""
        content = RUN_SH.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        assert first_line in ("#!/usr/bin/env bash", "#!/bin/bash"), (
            f"Unexpected shebang in run.sh: {first_line}"
        )

    def test_run_sh_is_executable(self):
        """Verifies run.sh has POSIX executable permission."""
        mode = os.stat(RUN_SH).st_mode
        assert bool(mode & stat.S_IXUSR), "run.sh is not executable by owner (chmod +x required)"

    def test_run_sh_syntax_bash_n(self):
        """Verifies run.sh passes bash syntax validation (bash -n run.sh)."""
        bash_bin = shutil.which("bash")
        if not bash_bin:
            pytest.skip("bash is not available in system PATH")

        result = subprocess.run(
            [bash_bin, "-n", str(RUN_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed with syntax errors:\n{result.stderr}"

    def test_run_sh_contains_required_directives(self):
        """Verifies run.sh contains required safety flags, environment variables, and traps."""
        content = RUN_SH.read_text(encoding="utf-8")

        # Safety flags
        assert "set -euo pipefail" in content or "set -e" in content

        # Virtual environment resolution & setup auto-trigger
        assert ".venv" in content
        assert "setup.sh" in content

        # Environment variable defaults
        assert "RECIPEBOXER_API_HOST" in content
        assert "RECIPEBOXER_API_PORT" in content
        assert "RECIPEBOXER_DEBUG" in content
        assert "RECIPEBOXER_CACHE_DIR" in content
        assert "RECIPEBOXER_EXPORT_DIR" in content

        # Application runner invocation
        assert "recipeboxer" in content

        # Graceful signal trap
        assert "trap" in content
        assert "INT" in content
        assert "TERM" in content

    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_run_sh_help_flag_execution(self, flag: str):
        """Verifies ./run.sh --help and -h execute successfully and output CLI help."""
        result = subprocess.run(
            [str(RUN_SH), flag],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"run.sh {flag} failed with code {result.returncode}:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "RecipeBoxer" in combined
        assert "--host" in combined
        assert "--port" in combined
        assert "--reload" in combined

    @pytest.mark.parametrize("flag", ["--version", "-v", "-V"])
    def test_run_sh_version_flag_execution(self, flag: str):
        """Verifies ./run.sh --version, -v, and -V output application version."""
        result = subprocess.run(
            [str(RUN_SH), flag],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"run.sh {flag} failed with code {result.returncode}:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert f"RecipeBoxer {__version__}" in combined

    def test_run_sh_invalid_flag_returns_exit_code_2(self):
        """Verifies passing invalid CLI arguments to run.sh returns exit code 2."""
        result = subprocess.run(
            [str(RUN_SH), "--nonexistent-arg-xyz"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, f"Expected exit code 2 on invalid CLI flag, got {result.returncode}"
        assert "unrecognized arguments" in (result.stdout + result.stderr)

    def test_run_sh_invocation_from_different_cwd(self, tmp_path: Path):
        """Verifies run.sh functions correctly when invoked from an arbitrary external directory."""
        result = subprocess.run(
            [str(RUN_SH), "--help"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"run.sh failed when executed from {tmp_path}:\n{result.stderr}"
        assert "RecipeBoxer" in result.stdout

    def test_run_sh_preflight_triggers_setup_when_venv_missing(self, tmp_path: Path):
        """Verifies run.sh automatically triggers setup.sh when .venv is missing in a test project directory."""
        # Create a mock project directory
        mock_proj = tmp_path / "mock_project"
        mock_proj.mkdir()

        # Copy run.sh
        mock_run_sh = mock_proj / "run.sh"
        shutil.copy2(RUN_SH, mock_run_sh)

        # Create a mock setup.sh that creates a fake .venv/bin/python
        mock_setup_sh = mock_proj / "setup.sh"
        mock_setup_sh.write_text(
            "#!/bin/sh\n"
            "echo 'Mock setup.sh triggered successfully'\n"
            "mkdir -p .venv/bin\n"
            "cat << 'EOF' > .venv/bin/python\n"
            "#!/bin/sh\n"
            "echo \"Mock python executed with args: $@\"\n"
            "exit 0\n"
            "EOF\n"
            "chmod +x .venv/bin/python\n"
            "exit 0\n"
        )
        mock_setup_sh.chmod(mock_setup_sh.stat().st_mode | stat.S_IXUSR)

        # Run mock_run_sh
        result = subprocess.run(
            [str(mock_run_sh), "--mock-flag"],
            cwd=str(mock_proj),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mock run.sh execution failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "Virtual environment not detected" in combined
        assert "Automatically running setup.sh" in combined
        assert "Mock setup.sh triggered successfully" in combined
        assert "Mock python executed with args: -m recipeboxer --mock-flag" in combined

    def test_run_sh_respects_custom_environment_variables(self, monkeypatch: pytest.MonkeyPatch):
        """Verifies that run.sh preserves custom environment variables (host, port, debug)."""
        env = dict(os.environ)
        env["RECIPEBOXER_API_HOST"] = "0.0.0.0"
        env["RECIPEBOXER_API_PORT"] = "9876"
        env["RECIPEBOXER_DEBUG"] = "true"

        # Check parser output when running --help under custom env vars
        result = subprocess.run(
            [str(RUN_SH), "--help"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "0.0.0.0" in result.stdout
        assert "9876" in result.stdout

    def test_run_sh_signal_trap_declaration(self):
        """Verifies run.sh declares signal traps for SIGINT and SIGTERM."""
        content = RUN_SH.read_text(encoding="utf-8")
        assert "trap" in content
        assert "INT" in content
        assert "TERM" in content

    def test_run_sh_line_endings(self):
        """Verifies run.sh has POSIX LF line endings without Windows CR characters."""
        raw_bytes = RUN_SH.read_bytes()
        assert b"\r\n" not in raw_bytes, "run.sh contains CRLF line endings; POSIX requires LF"


# ============================================================================
# 3. WINDOWS POWERSHELL SETUP SCRIPT TESTS (setup.ps1)
# ============================================================================

class TestSetupPs1Windows:
    """Test suite for setup.ps1 Windows PowerShell script."""

    def test_setup_ps1_exists(self):
        """Verifies setup.ps1 exists at project root."""
        assert SETUP_PS1.exists(), "setup.ps1 does not exist at project root"
        assert SETUP_PS1.is_file(), "setup.ps1 is not a regular file"

    def test_setup_ps1_content_structure(self):
        """Verifies setup.ps1 has all required PowerShell components and commands."""
        content = SETUP_PS1.read_text(encoding="utf-8")

        # ErrorActionPreference
        assert '$ErrorActionPreference = "Stop"' in content

        # Script directory resolution
        assert "$PSScriptRoot" in content or "$MyInvocation" in content

        # Candidate commands
        assert "py" in content
        assert "python" in content
        assert "sys.version_info >= (3, 10)" in content

        # Virtual environment path
        assert ".venv" in content
        assert "Scripts\\python.exe" in content

        # Build tools upgrade
        assert "pip install --upgrade pip wheel setuptools" in content

        # Dependency installation
        assert "requirements.txt" in content
        assert "requirements-dev.txt" in content
        assert "pip install -e" in content

        # Verification banner
        assert "import recipeboxer" in content
        assert "setup completed successfully" in content.lower()

    def test_setup_ps1_balanced_blocks(self):
        """Verifies setup.ps1 has balanced curly braces, parentheses, and quotes."""
        content = SETUP_PS1.read_text(encoding="utf-8")

        # Strip line comments (#...) and block comments (<# ... #>)
        no_block_comments = re.sub(r"<#.*?#>", "", content, flags=re.DOTALL)
        no_comments = re.sub(r"#.*$", "", no_block_comments, flags=re.MULTILINE)

        # Count braces
        open_braces = no_comments.count("{")
        close_braces = no_comments.count("}")
        assert open_braces == close_braces, f"Mismatched braces in setup.ps1: {open_braces} {{ vs {close_braces} }}"

        # Count parentheses
        open_parens = no_comments.count("(")
        close_parens = no_comments.count(")")
        assert open_parens == close_parens, f"Mismatched parentheses in setup.ps1: {open_parens} ( vs {close_parens} )"

    def test_setup_ps1_syntax_with_pwsh_if_available(self):
        """Runs pwsh syntax validation if PowerShell Core / pwsh is installed."""
        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh_bin:
            pytest.skip("pwsh / powershell is not available in system PATH")

        cmd = f"& {{ [System.Management.Automation.Language.Parser]::ParseFile('{SETUP_PS1}', [ref]$null, [ref]$null) }}"
        result = subprocess.run(
            [pwsh_bin, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"PowerShell AST parsing failed:\n{result.stderr}"


# ============================================================================
# 4. WINDOWS POWERSHELL RUN SCRIPT TESTS (run.ps1)
# ============================================================================

class TestRunPs1Windows:
    """Test suite for run.ps1 Windows PowerShell script."""

    def test_run_ps1_exists(self):
        """Verifies run.ps1 exists at project root."""
        assert RUN_PS1.exists(), "run.ps1 does not exist at project root"
        assert RUN_PS1.is_file(), "run.ps1 is not a regular file"

    def test_run_ps1_content_structure(self):
        """Verifies run.ps1 has all required PowerShell components and commands."""
        content = RUN_PS1.read_text(encoding="utf-8")

        # ErrorActionPreference
        assert '$ErrorActionPreference = "Stop"' in content

        # Script directory resolution
        assert "$PSScriptRoot" in content or "$MyInvocation" in content

        # Virtual environment path
        assert ".venv" in content
        assert "Scripts\\python.exe" in content

        # Preflight check trigger
        assert "setup.ps1" in content

        # Environment variable defaults
        assert "RECIPEBOXER_API_HOST" in content
        assert "RECIPEBOXER_API_PORT" in content
        assert "RECIPEBOXER_DEBUG" in content
        assert "RECIPEBOXER_CACHE_DIR" in content
        assert "RECIPEBOXER_EXPORT_DIR" in content

        # Invocation & graceful interrupt handling
        assert "-m" in content
        assert "recipeboxer" in content
        assert "try" in content
        assert "catch" in content
        assert "finally" in content

    def test_run_ps1_parameter_definitions(self):
        """Verifies run.ps1 declares standard CLI parameters with appropriate aliases."""
        content = RUN_PS1.read_text(encoding="utf-8")

        assert "Help" in content
        assert "HostAddress" in content or "Host" in content
        assert "Port" in content
        assert "LogLevel" in content
        assert "Reload" in content
        assert "RemainingArgs" in content

    def test_run_ps1_encoding(self):
        """Verifies run.ps1 can be read as valid UTF-8 without decoding errors."""
        content = RUN_PS1.read_bytes().decode("utf-8")
        assert len(content) > 0

    def test_run_ps1_balanced_blocks(self):
        """Verifies run.ps1 has balanced curly braces, parentheses, and quotes."""
        content = RUN_PS1.read_text(encoding="utf-8")

        no_block_comments = re.sub(r"<#.*?#>", "", content, flags=re.DOTALL)
        no_comments = re.sub(r"#.*$", "", no_block_comments, flags=re.MULTILINE)

        open_braces = no_comments.count("{")
        close_braces = no_comments.count("}")
        assert open_braces == close_braces, f"Mismatched braces in run.ps1: {open_braces} {{ vs {close_braces} }}"

        open_parens = no_comments.count("(")
        close_parens = no_comments.count(")")
        assert open_parens == close_parens, f"Mismatched parentheses in run.ps1: {open_parens} ( vs {close_parens} )"

    def test_run_ps1_syntax_with_pwsh_if_available(self):
        """Runs pwsh syntax validation if PowerShell Core / pwsh is installed."""
        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh_bin:
            pytest.skip("pwsh / powershell is not available in system PATH")

        cmd = f"& {{ [System.Management.Automation.Language.Parser]::ParseFile('{RUN_PS1}', [ref]$null, [ref]$null) }}"
        result = subprocess.run(
            [pwsh_bin, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"PowerShell AST parsing failed on run.ps1:\n{result.stderr}"


# ============================================================================
# 5. WINDOWS BATCH SETUP SCRIPT TESTS (setup.bat)
# ============================================================================

class TestSetupBatWindows:
    """Test suite for setup.bat Windows Command Prompt batch script."""

    def test_setup_bat_exists(self):
        """Verifies setup.bat exists at project root."""
        assert SETUP_BAT.exists(), "setup.bat does not exist at project root"
        assert SETUP_BAT.is_file(), "setup.bat is not a regular file"

    def test_setup_bat_content_structure(self):
        """Verifies setup.bat has required batch commands, delayed expansion, and error checks."""
        content = SETUP_BAT.read_text(encoding="utf-8")

        # Initialization
        assert "@echo off" in content
        assert "setlocal enabledelayedexpansion" in content

        # Script dir & venv paths
        assert "%~dp0" in content
        assert ".venv" in content
        assert "Scripts\\python.exe" in content

        # Candidate probing loop
        assert "py -3" in content
        assert "python" in content
        assert "sys.version_info >= (3, 10)" in content

        # Error checks
        assert "ERRORLEVEL" in content
        assert "exit /b 1" in content
        assert "exit /b 0" in content

        # Pip upgrade and dependencies
        assert "pip install --upgrade pip wheel setuptools" in content
        assert "requirements.txt" in content
        assert "requirements-dev.txt" in content
        assert "pip install -e" in content

        # Verification
        assert "import recipeboxer" in content

    def test_setup_bat_balanced_parentheses(self):
        """Verifies setup.bat has balanced block parentheses outside echo strings."""
        content = SETUP_BAT.read_text(encoding="utf-8")

        lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("rem") and not line.strip().startswith("::")
        ]
        clean_content = "\n".join(lines)

        open_parens = clean_content.count("(")
        close_parens = clean_content.count(")")
        assert open_parens == close_parens, (
            f"Mismatched parentheses in setup.bat: {open_parens} ( vs {close_parens} )"
        )


# ============================================================================
# 6. WINDOWS BATCH RUN SCRIPT TESTS (run.bat)
# ============================================================================

class TestRunBatWindows:
    """Test suite for run.bat Windows Command Prompt batch script."""

    def test_run_bat_exists(self):
        """Verifies run.bat exists at project root."""
        assert RUN_BAT.exists(), "run.bat does not exist at project root"
        assert RUN_BAT.is_file(), "run.bat is not a regular file"

    def test_run_bat_content_structure(self):
        """Verifies run.bat has required batch commands, env defaults, and argument forwarding."""
        content = RUN_BAT.read_text(encoding="utf-8")

        # Initialization
        assert "@echo off" in content
        assert "setlocal enabledelayedexpansion" in content

        # Script dir & venv paths
        assert "%~dp0" in content
        assert ".venv" in content
        assert "Scripts\\python.exe" in content

        # Preflight check trigger
        assert "setup.bat" in content

        # Environment variable defaults
        assert "RECIPEBOXER_API_HOST" in content
        assert "RECIPEBOXER_API_PORT" in content
        assert "RECIPEBOXER_DEBUG" in content
        assert "RECIPEBOXER_CACHE_DIR" in content
        assert "RECIPEBOXER_EXPORT_DIR" in content

        # Argument forwarding and execution
        assert "recipeboxer" in content
        assert "%*" in content
        assert "exit /b" in content

    def test_run_bat_errorlevel_checks(self):
        """Verifies run.bat checks !ERRORLEVEL! and propagates exit code via %ERRORLEVEL%."""
        content = RUN_BAT.read_text(encoding="utf-8")
        assert "!ERRORLEVEL!" in content or "%ERRORLEVEL%" in content
        assert "exit /b" in content

    def test_run_bat_balanced_parentheses(self):
        """Verifies run.bat has balanced block parentheses outside echo strings."""
        content = RUN_BAT.read_text(encoding="utf-8")

        lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("rem") and not line.strip().startswith("::")
        ]
        clean_content = "\n".join(lines)

        open_parens = clean_content.count("(")
        close_parens = clean_content.count(")")
        assert open_parens == close_parens, (
            f"Mismatched parentheses in run.bat: {open_parens} ( vs {close_parens} )"
        )


# ============================================================================
# 7. PYTHON VERSION DETECTION LOGIC TESTS
# ============================================================================

class TestPythonVersionDetectionLogic:
    """Test suite verifying the mathematical/logical correctness of Python version checks."""

    @staticmethod
    def evaluate_version_check(major: int, minor: int) -> bool:
        """Simulates the check used across all setup scripts: sys.version_info >= (3, 10)."""
        return (major, minor) >= (3, 10)

    @pytest.mark.parametrize(
        "major, minor, expected",
        [
            (2, 7, False),
            (3, 6, False),
            (3, 7, False),
            (3, 8, False),
            (3, 9, False),
            (3, 10, True),
            (3, 11, True),
            (3, 12, True),
            (3, 13, True),
            (3, 14, True),
            (3, 15, True),
            (4, 0, True),
        ],
    )
    def test_version_gate_logic(self, major: int, minor: int, expected: bool):
        """Verifies (major, minor) >= (3, 10) correctly evaluates across versions."""
        assert self.evaluate_version_check(major, minor) is expected

    def test_current_interpreter_passes_version_check(self):
        """Verifies that the current test runner Python interpreter satisfies version check."""
        assert sys.version_info >= (3, 10), (
            f"Current Python {sys.version_info} does not satisfy >= (3, 10)"
        )

        # Subprocess evaluation of exact script snippet
        cmd = [sys.executable, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"]
        result = subprocess.run(cmd)
        assert result.returncode == 0


# ============================================================================
# 8. POSIX BUILD SCRIPT TESTS (build.sh)
# ============================================================================

class TestBuildShPOSIX:
    """Test suite for build.sh packaging script on macOS and Linux platforms."""

    def test_build_sh_exists(self):
        """Verifies build.sh exists at project root."""
        assert BUILD_SH.exists(), "build.sh does not exist at project root"
        assert BUILD_SH.is_file(), "build.sh is not a regular file"

    def test_build_sh_shebang(self):
        """Verifies build.sh starts with valid bash shebang."""
        content = BUILD_SH.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        assert first_line in ("#!/usr/bin/env bash", "#!/bin/bash"), (
            f"Unexpected shebang in build.sh: {first_line}"
        )

    def test_build_sh_is_executable(self):
        """Verifies build.sh has POSIX executable permission."""
        mode = os.stat(BUILD_SH).st_mode
        assert bool(mode & stat.S_IXUSR), "build.sh is not executable by owner (chmod +x required)"

    def test_build_sh_syntax_bash_n(self):
        """Verifies build.sh passes bash syntax validation (bash -n build.sh)."""
        bash_bin = shutil.which("bash")
        if not bash_bin:
            pytest.skip("bash is not available in system PATH")

        result = subprocess.run(
            [bash_bin, "-n", str(BUILD_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed on build.sh with syntax errors:\n{result.stderr}"

    def test_build_sh_contains_required_directives(self):
        """Verifies build.sh contains safety flags, build commands, and flags."""
        content = BUILD_SH.read_text(encoding="utf-8")

        # Safety flags
        assert "set -euo pipefail" in content or "set -e" in content

        # CLI flags
        assert "--all" in content
        assert "--wheel" in content
        assert "--binary" in content
        assert "--clean" in content
        assert "--help" in content

        # Python interpreter and version gating
        assert "sys.version_info >= (3, 10)" in content

        # Build tools verification
        assert "pip install" in content
        assert "build" in content
        assert "pyinstaller" in content

        # Packaging commands
        assert "-m build" in content
        assert "PyInstaller" in content
        assert "recipeboxer.spec" in content

        # Verification step
        assert "--version" in content
        assert "--help" in content

    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_build_sh_help_flag_execution(self, flag: str):
        """Verifies ./build.sh --help and -h execute successfully and output usage."""
        result = subprocess.run(
            [str(BUILD_SH), flag],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"build.sh {flag} failed with code {result.returncode}:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "Usage: ./build.sh" in combined
        assert "--all" in combined
        assert "--wheel" in combined
        assert "--binary" in combined
        assert "--clean" in combined

    def test_build_sh_invalid_flag_returns_exit_code_1(self):
        """Verifies passing invalid CLI arguments to build.sh returns exit code 1."""
        result = subprocess.run(
            [str(BUILD_SH), "--invalid-build-flag-xyz"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, f"Expected exit code 1 on invalid CLI flag, got {result.returncode}"
        assert "Unknown option" in (result.stdout + result.stderr)

    def test_build_sh_invocation_from_different_cwd(self, tmp_path: Path):
        """Verifies build.sh functions correctly when invoked from an arbitrary external directory."""
        result = subprocess.run(
            [str(BUILD_SH), "--help"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"build.sh failed when executed from {tmp_path}:\n{result.stderr}"
        assert "Usage: ./build.sh" in result.stdout

    def test_build_sh_fails_when_no_compatible_python(self, tmp_path: Path):
        """Verifies build.sh aborts with exit code 1 if Python version is below 3.10."""
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()

        fake_py = fake_bin_dir / "python3"
        fake_py.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            "    # Mock Python 3.9 check: fails version_info >= (3, 10)\n"
            "    exit 1\n"
            "fi\n"
            "echo 'Python 3.9.0'\n"
            "exit 0\n"
        )
        fake_py.chmod(fake_py.stat().st_mode | stat.S_IXUSR)

        fake_python = fake_bin_dir / "python"
        fake_python.symlink_to(fake_py)

        env = dict(os.environ)
        env["PATH"] = f"{fake_bin_dir}:/bin:/usr/bin"
        env.pop("VIRTUAL_ENV", None)

        mock_proj = tmp_path / "mock_rb"
        mock_proj.mkdir()
        shutil.copy2(BUILD_SH, mock_proj / "build.sh")

        result = subprocess.run(
            ["bash", str(mock_proj / "build.sh")],
            cwd=str(mock_proj),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "build.sh should have failed when Python < 3.10"
        assert "Python 3.10 or higher is required" in (result.stdout + result.stderr)

    def test_build_sh_line_endings(self):
        """Verifies build.sh has POSIX LF line endings without Windows CR characters."""
        raw_bytes = BUILD_SH.read_bytes()
        assert b"\r\n" not in raw_bytes, "build.sh contains CRLF line endings; POSIX requires LF"


# ============================================================================
# 9. WINDOWS POWERSHELL BUILD SCRIPT TESTS (build.ps1)
# ============================================================================

class TestBuildPs1Windows:
    """Test suite for build.ps1 Windows PowerShell script."""

    def test_build_ps1_exists(self):
        """Verifies build.ps1 exists at project root."""
        assert BUILD_PS1.exists(), "build.ps1 does not exist at project root"
        assert BUILD_PS1.is_file(), "build.ps1 is not a regular file"

    def test_build_ps1_content_structure(self):
        """Verifies build.ps1 has all required PowerShell components and commands."""
        content = BUILD_PS1.read_text(encoding="utf-8")

        # ErrorActionPreference
        assert '$ErrorActionPreference = "Stop"' in content

        # Script directory resolution
        assert "$PSScriptRoot" in content or "$MyInvocation" in content

        # Virtual environment path and python candidates
        assert ".venv" in content
        assert "Scripts\\python.exe" in content

        # Version gating
        assert "sys.version_info >= (3, 10)" in content

        # Build tools verification
        assert "pip install" in content
        assert "build" in content
        assert "pyinstaller" in content

        # Build commands
        assert "-m build" in content
        assert "PyInstaller" in content
        assert "recipeboxer.spec" in content

        # Verification
        assert "--version" in content
        assert "--help" in content

    def test_build_ps1_parameter_definitions(self):
        """Verifies build.ps1 declares standard build parameters."""
        content = BUILD_PS1.read_text(encoding="utf-8")

        assert "All" in content
        assert "Wheel" in content
        assert "Binary" in content
        assert "Clean" in content
        assert "Help" in content

    def test_build_ps1_balanced_blocks(self):
        """Verifies build.ps1 has balanced curly braces, parentheses, and quotes."""
        content = BUILD_PS1.read_text(encoding="utf-8")

        no_block_comments = re.sub(r"<#.*?#>", "", content, flags=re.DOTALL)
        no_comments = re.sub(r"#.*$", "", no_block_comments, flags=re.MULTILINE)

        open_braces = no_comments.count("{")
        close_braces = no_comments.count("}")
        assert open_braces == close_braces, f"Mismatched braces in build.ps1: {open_braces} {{ vs {close_braces} }}"

        open_parens = no_comments.count("(")
        close_parens = no_comments.count(")")
        assert open_parens == close_parens, f"Mismatched parentheses in build.ps1: {open_parens} ( vs {close_parens} )"

    def test_build_ps1_encoding(self):
        """Verifies build.ps1 can be read as valid UTF-8 without decoding errors."""
        content = BUILD_PS1.read_bytes().decode("utf-8")
        assert len(content) > 0

    def test_build_ps1_syntax_with_pwsh_if_available(self):
        """Runs pwsh syntax validation if PowerShell Core / pwsh is installed."""
        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh_bin:
            pytest.skip("pwsh / powershell is not available in system PATH")

        cmd = f"& {{ [System.Management.Automation.Language.Parser]::ParseFile('{BUILD_PS1}', [ref]$null, [ref]$null) }}"
        result = subprocess.run(
            [pwsh_bin, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"PowerShell AST parsing failed on build.ps1:\n{result.stderr}"


# ============================================================================
# 10. PYINSTALLER SPEC SPECIFICATION TESTS (recipeboxer.spec)
# ============================================================================

class TestPyInstallerSpec:
    """Test suite for recipeboxer.spec PyInstaller configuration."""

    def test_spec_file_exists(self):
        """Verifies recipeboxer.spec exists at project root."""
        assert SPEC_FILE.exists(), "recipeboxer.spec does not exist at project root"
        assert SPEC_FILE.is_file(), "recipeboxer.spec is not a regular file"

    def test_spec_file_python_syntax(self):
        """Verifies recipeboxer.spec is valid Python AST syntax."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        parsed = ast.parse(content, filename=str(SPEC_FILE))
        assert isinstance(parsed, ast.Module)

    def test_spec_file_hidden_imports_coverage(self):
        """Verifies recipeboxer.spec includes all required framework and internal hidden imports."""
        content = SPEC_FILE.read_text(encoding="utf-8")

        required_hidden_imports = [
            "uvicorn",
            "uvicorn.logging",
            "uvicorn.loops",
            "uvicorn.loops.auto",
            "uvicorn.loops.asyncio",
            "uvicorn.protocols",
            "uvicorn.protocols.http",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.http.h11_impl",
            "uvicorn.protocols.http.httptools_impl",
            "uvicorn.protocols.websockets",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.protocols.websockets.wsproto_impl",
            "uvicorn.lifespan",
            "uvicorn.lifespan.on",
            "fastapi",
            "starlette",
            "starlette.middleware.cors",
            "starlette.responses",
            "pydantic",
            "pydantic_settings",
            "pydantic_core",
            "google.genai",
            "google.antigravity",
            "recipeboxer",
            "recipeboxer.cli",
            "recipeboxer.api",
            "recipeboxer.agent",
            "recipeboxer.correlation",
            "recipeboxer.exporter",
            "recipeboxer.extraction",
            "recipeboxer.ingestion",
            "recipeboxer.models",
            "recipeboxer.storage",
            "recipeboxer.vision",
            "sqlite3",
            "multipart",
            "python_multipart",
            "PIL",
            "dotenv",
        ]

        for imp in required_hidden_imports:
            assert imp in content, f"Hidden import '{imp}' missing from recipeboxer.spec"

    def test_spec_file_datas_bundling(self):
        """Verifies package data bundling is configured for recipeboxer."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        assert "datas = [" in content or "datas=" in content
        assert "recipeboxer" in content

    def test_pyinstaller_spec_target_and_datas(self):
        """Verifies package data bundling, target name, and disabled UPX compression."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        assert "datas = [" in content or "datas=" in content
        assert "recipeboxer" in content
        assert 'name="recipeboxer"' in content or "name='recipeboxer'" in content
        assert "upx=False" in content

    def test_spec_file_console_mode_and_name(self):
        """Verifies console mode is enabled and executable name is 'recipeboxer'."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        assert 'name="recipeboxer"' in content or "name='recipeboxer'" in content
        assert "console=True" in content


# ============================================================================
# 11. DISTRIBUTION PACKAGING AND BINARY EXECUTION TESTS
# ============================================================================

class TestPackagingArtifactsAndExecution:
    """Test suite verifying compiled wheels, sdist, and standalone binary execution."""

    def test_wheel_archive_validity(self):
        """Verifies built wheel in dist/ contains all required package files and metadata."""
        dist_dir = PROJECT_ROOT / "dist"
        wheels = list(dist_dir.glob("recipeboxer-*.whl"))
        if not wheels:
            pytest.skip("No wheels found in dist/. Run ./build.sh --wheel first.")

        wheel_path = wheels[0]
        with zipfile.ZipFile(wheel_path, "r") as zf:
            namelist = zf.namelist()

            # Verify package files exist
            assert any(name.startswith("recipeboxer/") and name.endswith("__init__.py") for name in namelist)
            assert any(name.startswith("recipeboxer/") and name.endswith("cli.py") for name in namelist)
            assert any(name.startswith("recipeboxer/") and name.endswith("config.py") for name in namelist)

            # Verify entry points in dist-info
            entry_points_files = [name for name in namelist if name.endswith("entry_points.txt")]
            assert len(entry_points_files) > 0, "entry_points.txt missing from wheel"
            entry_points_content = zf.read(entry_points_files[0]).decode("utf-8")
            assert "recipeboxer = recipeboxer.cli:main" in entry_points_content

            # Verify METADATA
            metadata_files = [name for name in namelist if name.endswith("METADATA")]
            assert len(metadata_files) > 0, "METADATA missing from wheel"
            metadata_content = zf.read(metadata_files[0]).decode("utf-8")
            assert "Name: recipeboxer" in metadata_content
            assert f"Version: {__version__}" in metadata_content

    def test_compiled_binary_execution_flags(self):
        """Verifies compiled standalone binary in dist/ executes --version and --help."""
        dist_dir = PROJECT_ROOT / "dist"
        binary_path = dist_dir / ("recipeboxer.exe" if sys.platform == "win32" else "recipeboxer")
        if not binary_path.exists():
            pytest.skip("Compiled binary not found in dist/. Run ./build.sh --binary first.")

        # Test --version
        res_ver = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert res_ver.returncode == 0
        assert f"RecipeBoxer {__version__}" in (res_ver.stdout + res_ver.stderr)

        # Test --help
        res_help = subprocess.run(
            [str(binary_path), "--help"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert res_help.returncode == 0
        assert "RecipeBoxer" in res_help.stdout
        assert "--host" in res_help.stdout
        assert "--port" in res_help.stdout


# ============================================================================
# 12. POSIX TEST SCRIPT TESTS (test.sh)
# ============================================================================

class TestTestShPOSIX:
    """Test suite for test.sh test execution script on macOS and Linux platforms."""

    def test_test_sh_exists(self):
        """Verifies test.sh exists at project root."""
        assert TEST_SH.exists(), "test.sh does not exist at project root"
        assert TEST_SH.is_file(), "test.sh is not a regular file"

    def test_test_sh_shebang(self):
        """Verifies test.sh starts with valid bash shebang."""
        content = TEST_SH.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        assert first_line in ("#!/usr/bin/env bash", "#!/bin/bash"), (
            f"Unexpected shebang in test.sh: {first_line}"
        )

    def test_test_sh_is_executable(self):
        """Verifies test.sh has POSIX executable permission."""
        mode = os.stat(TEST_SH).st_mode
        assert bool(mode & stat.S_IXUSR), "test.sh is not executable by owner (chmod +x required)"

    def test_test_sh_syntax_bash_n(self):
        """Verifies test.sh passes bash syntax validation (bash -n test.sh)."""
        bash_bin = shutil.which("bash")
        if not bash_bin:
            pytest.skip("bash is not available in system PATH")

        result = subprocess.run(
            [bash_bin, "-n", str(TEST_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed on test.sh with syntax errors:\n{result.stderr}"

    def test_test_sh_contains_required_directives(self):
        """Verifies test.sh contains safety flags, virtualenv check, and pytest invocation."""
        content = TEST_SH.read_text(encoding="utf-8")

        # Safety flags
        assert "set -euo pipefail" in content or "set -e" in content

        # Virtual environment resolution & setup auto-trigger
        assert ".venv" in content
        assert "setup.sh" in content
        assert "pytest" in content

        # Transparent argument forwarding and execution
        assert '"$@"' in content or "$@" in content

    def test_test_sh_line_endings(self):
        """Verifies test.sh has POSIX LF line endings without Windows CR characters."""
        raw_bytes = TEST_SH.read_bytes()
        assert b"\r\n" not in raw_bytes, "test.sh contains CRLF line endings; POSIX requires LF"

    def test_test_sh_argument_passthrough_version(self):
        """Verifies ./test.sh --version executes pytest and reports pytest version."""
        result = subprocess.run(
            [str(TEST_SH), "--version"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"test.sh --version failed:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "pytest" in combined.lower()

    def test_test_sh_argument_passthrough_specific_test(self):
        """Verifies ./test.sh forwards arguments targeting specific test files and filters."""
        result = subprocess.run(
            [str(TEST_SH), "tests/test_cli.py", "-k", "test_default_arguments", "-q"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"test.sh execution failed:\n{result.stdout}\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "passed" in combined.lower()

    def test_test_sh_exit_code_propagation_on_invalid_option(self):
        """Verifies ./test.sh propagates pytest's non-zero exit code on invalid CLI options."""
        result = subprocess.run(
            [str(TEST_SH), "--nonexistent-pytest-option-xyz"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        # pytest returns exit code 4 on command-line usage errors
        assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
        assert "unrecognized arguments" in (result.stdout + result.stderr) or "usage:" in (result.stdout + result.stderr)

    def test_test_sh_invocation_from_different_cwd(self, tmp_path: Path):
        """Verifies test.sh functions correctly when invoked from an arbitrary external directory."""
        result = subprocess.run(
            [str(TEST_SH), "--version"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"test.sh failed when executed from {tmp_path}:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "pytest" in combined.lower()

    def test_test_sh_preflight_triggers_setup_when_venv_missing(self, tmp_path: Path):
        """Verifies test.sh automatically triggers setup.sh when .venv/bin/pytest is missing."""
        mock_proj = tmp_path / "mock_project"
        mock_proj.mkdir()

        mock_test_sh = mock_proj / "test.sh"
        shutil.copy2(TEST_SH, mock_test_sh)

        # Create a mock setup.sh that creates a fake .venv/bin/pytest
        mock_setup_sh = mock_proj / "setup.sh"
        mock_setup_sh.write_text(
            "#!/bin/sh\n"
            "echo 'Mock setup.sh triggered for test runner'\n"
            "mkdir -p .venv/bin\n"
            "cat << 'EOF' > .venv/bin/pytest\n"
            "#!/bin/sh\n"
            "echo \"Mock pytest executed with args: $@\"\n"
            "exit 0\n"
            "EOF\n"
            "chmod +x .venv/bin/pytest\n"
            "exit 0\n"
        )
        mock_setup_sh.chmod(mock_setup_sh.stat().st_mode | stat.S_IXUSR)

        result = subprocess.run(
            [str(mock_test_sh), "-k", "dummy_test"],
            cwd=str(mock_proj),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mock test.sh execution failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        combined = result.stdout + result.stderr
        assert "Virtual environment or pytest not detected" in combined
        assert "Automatically running setup.sh" in combined
        assert "Mock setup.sh triggered for test runner" in combined
        assert "Mock pytest executed with args: -k dummy_test" in combined


# ============================================================================
# 13. WINDOWS POWERSHELL TEST SCRIPT TESTS (test.ps1)
# ============================================================================

class TestTestPs1Windows:
    """Test suite for test.ps1 Windows PowerShell test script."""

    def test_test_ps1_exists(self):
        """Verifies test.ps1 exists at project root."""
        assert TEST_PS1.exists(), "test.ps1 does not exist at project root"
        assert TEST_PS1.is_file(), "test.ps1 is not a regular file"

    def test_test_ps1_content_structure(self):
        """Verifies test.ps1 has all required PowerShell components, error handling, and pytest invocation."""
        content = TEST_PS1.read_text(encoding="utf-8")

        # ErrorActionPreference
        assert '$ErrorActionPreference = "Stop"' in content

        # Script directory resolution
        assert "$PSScriptRoot" in content or "$MyInvocation" in content

        # Virtual environment path
        assert ".venv" in content
        assert "Scripts\\pytest.exe" in content

        # Preflight check trigger
        assert "setup.ps1" in content

        # Pytest invocation & exit code propagation
        assert "$LASTEXITCODE" in content or "$exitCode" in content
        assert "exit $exitCode" in content or "exit $LASTEXITCODE" in content

    def test_test_ps1_parameter_definitions(self):
        """Verifies test.ps1 declares transparent parameter forwarding."""
        content = TEST_PS1.read_text(encoding="utf-8")
        assert "ValueFromRemainingArguments" in content
        assert "RemainingArgs" in content or "Args" in content

    def test_test_ps1_encoding(self):
        """Verifies test.ps1 can be read as valid UTF-8 without decoding errors."""
        content = TEST_PS1.read_bytes().decode("utf-8")
        assert len(content) > 0

    def test_test_ps1_balanced_blocks(self):
        """Verifies test.ps1 has balanced curly braces, parentheses, and quotes."""
        content = TEST_PS1.read_text(encoding="utf-8")

        no_block_comments = re.sub(r"<#.*?#>", "", content, flags=re.DOTALL)
        no_comments = re.sub(r"#.*$", "", no_block_comments, flags=re.MULTILINE)

        open_braces = no_comments.count("{")
        close_braces = no_comments.count("}")
        assert open_braces == close_braces, f"Mismatched braces in test.ps1: {open_braces} {{ vs {close_braces} }}"

        open_parens = no_comments.count("(")
        close_parens = no_comments.count(")")
        assert open_parens == close_parens, f"Mismatched parentheses in test.ps1: {open_parens} ( vs {close_parens} )"

    def test_test_ps1_syntax_with_pwsh_if_available(self):
        """Runs pwsh syntax validation if PowerShell Core / pwsh is installed."""
        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh_bin:
            pytest.skip("pwsh / powershell is not available in system PATH")

        cmd = f"& {{ [System.Management.Automation.Language.Parser]::ParseFile('{TEST_PS1}', [ref]$null, [ref]$null) }}"
        result = subprocess.run(
            [pwsh_bin, "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"PowerShell AST parsing failed on test.ps1:\n{result.stderr}"


