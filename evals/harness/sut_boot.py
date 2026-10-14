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
    """Installs sys.addaudithook blocking unauthorized access to ground truth."""
    cwd = os.path.abspath(os.getcwd())

    def _audit_hook(event: str, args: tuple) -> None:
        if event == "open":
            if not args:
                return
            raw_path = args[0]
            if isinstance(raw_path, int):
                return
            if isinstance(raw_path, bytes):
                path_str = raw_path.decode("utf-8", errors="replace")
            else:
                path_str = str(raw_path)

            normalized = path_str.replace("\\", "/")
            norm_lower = normalized.lower()

            # 1. Any file under evals/ (except standard python path lookups before hook)
            if "/evals/" in normalized or normalized.startswith("evals/") or normalized == "evals":
                sys.stderr.write(f"CHEAT_DETECTED: Unauthorized open of evals path: {path_str}\n")
                sys.stderr.flush()
                os._exit(86)

            # 2. Any file under tests/fixtures
            if "/tests/fixtures" in normalized or normalized.startswith("tests/fixtures") or "fixtures/sample_cards" in normalized:
                sys.stderr.write(f"CHEAT_DETECTED: Unauthorized open of test fixtures: {path_str}\n")
                sys.stderr.flush()
                os._exit(86)

            # 3. Encrypted vault files (*.gt.enc, *.enc)
            if norm_lower.endswith(".enc") or ".gt.enc" in norm_lower:
                sys.stderr.write(f"CHEAT_DETECTED: Unauthorized open of encrypted vault payload: {path_str}\n")
                sys.stderr.flush()
                os._exit(86)

            # 4. Age identity / key files
            if "age.key" in norm_lower or norm_lower.endswith(".key") or "age_key" in norm_lower:
                sys.stderr.write(f"CHEAT_DETECTED: Unauthorized open of age identity key: {path_str}\n")
                sys.stderr.flush()
                os._exit(86)

            # 5. manifest.json outside the active working directory (cwd)
            if norm_lower.endswith("manifest.json"):
                try:
                    resolved = os.path.abspath(path_str)
                    common = os.path.commonpath([cwd, resolved])
                    if common != cwd:
                        sys.stderr.write(
                            f"CHEAT_DETECTED: Unauthorized open of external manifest: {path_str}\n"
                        )
                        sys.stderr.flush()
                        os._exit(86)
                except Exception:
                    sys.stderr.write(
                        f"CHEAT_DETECTED: Unauthorized open of external manifest: {path_str}\n"
                    )
                    sys.stderr.flush()
                    os._exit(86)

    sys.addaudithook(_audit_hook)


def main() -> None:
    """Installs audit hook and delegates execution to recipeboxer.cli."""
    _install_anti_cheat_audit_hook()

    # Delegate execution to recipeboxer
    from recipeboxer.cli import main as rb_main
    sys.exit(rb_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
