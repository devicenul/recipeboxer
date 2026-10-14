"""Bootstrap launcher injecting runtime audit hook for System Under Test (SUT) (WP-E3).

Enforces black-box evaluation isolation (INV-AC1, INV-AC5):
- Installs sys.addaudithook checking all file open events.
- Immediately terminates with exit code 86 and emits CHEAT_DETECTED upon any attempt
  to open evals/, tests/fixtures, *.gt.enc, *.enc, manifest.json outside cwd, or age keys.
- Executes `recipeboxer` via CLI entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _install_anti_cheat_audit_hook() -> None:
    """Installs sys.addaudithook blocking unauthorized access to ground truth and cheating vectors (INV-AC1, INV-AC5)."""
    cwd = os.path.abspath(os.getcwd())

    def _is_forbidden_path(raw_path: object) -> tuple[bool, str]:
        if raw_path is None or isinstance(raw_path, int):
            return False, ""
        if isinstance(raw_path, bytes):
            path_str = raw_path.decode("utf-8", errors="replace")
        else:
            path_str = str(raw_path)

        candidates = [path_str]
        try:
            candidates.append(os.path.realpath(path_str))
        except Exception:
            pass

        # If wildcards exist, also inspect the base dir realpath
        if "*" in path_str or "?" in path_str:
            try:
                base_dir = path_str.split("*")[0].split("?")[0]
                if base_dir:
                    candidates.append(os.path.realpath(base_dir))
            except Exception:
                pass

        for p in candidates:
            normalized = p.replace("\\", "/")
            norm_lower = normalized.lower()

            # 1. Any file or dir under evals/ (or evals dir itself)
            if "/evals/" in normalized or normalized.startswith("evals/") or normalized == "evals" or normalized.endswith("/evals"):
                return True, f"Unauthorized access to evals path: {path_str}"

            # 2. Any file under tests/fixtures or sample_cards
            if "/tests/fixtures" in normalized or normalized.startswith("tests/fixtures") or "fixtures/sample_cards" in normalized:
                return True, f"Unauthorized access to test fixtures: {path_str}"

            # 3. Encrypted vault files (*.gt.enc, *.enc)
            if norm_lower.endswith(".enc") or ".gt.enc" in norm_lower:
                return True, f"Unauthorized access to encrypted vault payload: {path_str}"

            # 4. Age identity / key files
            if "age.key" in norm_lower or norm_lower.endswith(".key") or "age_key" in norm_lower:
                return True, f"Unauthorized access to age identity key: {path_str}"

            # 5. manifest.json outside active working directory (cwd)
            if norm_lower.endswith("manifest.json"):
                try:
                    resolved = os.path.abspath(p)
                    common = os.path.commonpath([cwd, resolved])
                    if common != cwd:
                        return True, f"Unauthorized access to external manifest: {path_str}"
                except Exception:
                    return True, f"Unauthorized access to external manifest: {path_str}"

        return False, ""

    def _audit_hook(event: str, args: tuple) -> None:
        # 1. Package / module imports (evals, cardforge, tests)
        if event == "import":
            if args:
                mod_name = str(args[0])
                mod_root = mod_name.split(".")[0]
                if mod_root in ("evals", "cardforge", "tests"):
                    sys.stderr.write(f"CHEAT_DETECTED: Unauthorized import of {mod_name}\n")
                    sys.stderr.flush()
                    os._exit(86)
            return

        # 2. Subprocess and command execution
        if (
            event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn")
            or event.startswith("subprocess.")
            or event.startswith("os.exec")
        ):
            sys.stderr.write(f"CHEAT_DETECTED: Unauthorized process execution ({event})\n")
            sys.stderr.flush()
            os._exit(86)

        # 3. Path inspection and traversal events
        if (
            event in ("open", "os.listdir", "os.scandir", "os.walk", "sqlite3.connect")
            or event.startswith("glob.glob")
        ):
            if args:
                forbidden, reason = _is_forbidden_path(args[0])
                if forbidden:
                    sys.stderr.write(f"CHEAT_DETECTED: {reason}\n")
                    sys.stderr.flush()
                    os._exit(86)
            return

        # 4. File copy (check both source and destination)
        if event == "shutil.copyfile":
            for arg in args[:2]:
                forbidden, reason = _is_forbidden_path(arg)
                if forbidden:
                    sys.stderr.write(f"CHEAT_DETECTED: {reason}\n")
                    sys.stderr.flush()
                    os._exit(86)
            return

    sys.addaudithook(_audit_hook)


def main() -> None:
    """Installs audit hook and delegates execution to recipeboxer.cli."""
    # Remove sut_boot directory from sys.path so Python import machinery does not search evals/
    sut_dir = str(Path(__file__).resolve().parent)
    while sut_dir in sys.path:
        sys.path.remove(sut_dir)

    _install_anti_cheat_audit_hook()

    # Delegate execution to recipeboxer
    from recipeboxer.cli import main as rb_main
    sys.exit(rb_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
