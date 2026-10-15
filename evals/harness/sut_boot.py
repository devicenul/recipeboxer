"""Bootstrap launcher injecting runtime audit hook for System Under Test (SUT) (WP-E3, WP-RT3FIX).

Enforces black-box evaluation isolation (INV-AC1, INV-AC5):
- Installs sys.addaudithook checking file open / directory listing / glob / sqlite / copy events.
- Immediately terminates with exit code 86 and emits CHEAT_DETECTED upon any attempt to touch:
  * evals/, cardforge/, tests/fixtures, *.gt.enc, *.enc, manifest.json outside cwd, age keys (WP-E3);
  * GT-adjacent plaintext caches by name anywhere (``/t3gt/``, ``/t3_cache/``, ``/gutenberg_cache/``,
    ``/t2_corpus_``, eval run dirs ``/honest_v*``, ``/bench_out``, red-team scratch ``/redteam*``) and agent
    artifact dirs (``/.gemini/``) (RT3-01);
  * ``$HOME/.cache/projects`` of the current HOME and any configured deny root (the harness passes the
    *real* ``$HOME/.cache/projects``, the real ``~/.config/recipeboxer`` identity dir, ``~/.gemini`` and the
    agent artifact dir) except the interpreter prefix, the pycache prefix and the recipeboxer package dir
    (RT3-01).
- Strict mode (enabled by the harness): every path must additionally lie inside a read/write allowlist
  (interpreter prefixes / site-packages, pycache prefix, the recipeboxer package dir, cwd, the input /
  output dir, the private sandbox HOME/TMPDIR, and system dirs needed by the interpreter: /dev,
  /proc (self only for per-process entries), /sys, /etc, /usr/share, /usr/lib*, /lib*). Directory listing
  (not opening files) is additionally allowed for the exact ``sys.path`` entries (import machinery).
- Executes `recipeboxer` via CLI entry point.

Configuration is passed by the harness in the ``RB_SUT_SANDBOX`` environment variable (JSON:
``{"strict": bool, "allow": [...], "deny": [...]}``); it is removed from ``os.environ`` before the SUT runs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence

SANDBOX_ENV = "RB_SUT_SANDBOX"

# Name patterns denied anywhere on the filesystem (normalized, forward slashes).
DENY_SEGMENTS = (
    "/.gemini/",
    "/t3gt/",
    "/t3_cache/",
    "/gutenberg_cache/",
    "/t2_corpus_",
    "/honest_v",
    "/bench_out",
    "/redteam",
    "/cardforge/",
)

SYSTEM_READ_ROOTS = (
    "/dev",
    "/sys",
    "/etc",
    "/usr/share",
    "/usr/lib",
    "/usr/lib64",
    "/usr/local/lib",
    "/usr/local/share",
    "/lib",
    "/lib64",
    "/run/systemd/resolve",
)


def _norm(p: str) -> str:
    return os.path.normpath(os.path.abspath(p)).replace("\\", "/")


def _under(path: str, root: str) -> bool:
    if not root:
        return False
    if path == root:
        return True
    return path.startswith(root.rstrip("/") + "/")


def _package_dir() -> Optional[str]:
    try:
        import importlib.util

        spec = importlib.util.find_spec("recipeboxer")
        if spec is not None and spec.submodule_search_locations:
            return _norm(list(spec.submodule_search_locations)[0])
    except Exception:
        pass
    return None


def load_sandbox_config(env: Optional[dict] = None) -> dict:
    """Reads (and removes) the harness sandbox config from the environment."""
    e = os.environ if env is None else env
    raw = e.pop(SANDBOX_ENV, None)
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _install_anti_cheat_audit_hook(
    strict: bool = False,
    allow_roots: Sequence[str] = (),
    deny_roots: Sequence[str] = (),
) -> None:
    """Installs sys.addaudithook blocking unauthorized access to ground truth and cheating vectors (INV-AC1, INV-AC5)."""
    cwd = os.path.abspath(os.getcwd())
    self_file = _norm(__file__)
    real_home = os.path.expanduser("~")

    # Interpreter-owned locations (venv / stdlib / site-packages / pycache) and the SUT package itself.
    interp_roots: List[str] = []
    for p in {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}:
        if p:
            interp_roots.append(_norm(p))
    if getattr(sys, "pycache_prefix", None):
        interp_roots.append(_norm(sys.pycache_prefix))  # type: ignore[arg-type]
    pkg_dir = _package_dir()
    if pkg_dir:
        interp_roots.append(pkg_dir)

    denies: List[str] = [_norm(d) for d in deny_roots if d]
    # Always deny the project cache tree of the *current* HOME (defense in depth when HOME is not private).
    if real_home and real_home not in ("~", "/"):
        denies.append(_norm(os.path.join(real_home, ".cache", "projects")))

    allows: List[str] = []
    if strict:
        allows = interp_roots + [_norm(cwd)] + [_norm(a) for a in allow_roots if a]
        allows += list(SYSTEM_READ_ROOTS)
    listdir_ok = {_norm(p) for p in sys.path if p}
    my_pid = str(os.getpid())

    def _is_dist_metadata(path: str) -> bool:
        """Package metadata (``*.dist-info`` / ``*.egg-info``) directly inside a sys.path entry
        (read by importlib.metadata, e.g. entry points); contains no GT content."""
        for entry in listdir_ok:
            if _under(path, entry) and path != entry:
                first = path[len(entry.rstrip("/")) + 1:].split("/", 1)[0]
                if first.endswith((".dist-info", ".egg-info")):
                    return True
        return False

    def _proc_ok(path: str) -> bool:
        rest = path[len("/proc"):].lstrip("/")
        first = rest.split("/", 1)[0]
        if not first:
            return True
        if first.isdigit():
            return first == my_pid
        return True  # self, thread-self, cpuinfo, meminfo, stat, sys/...

    def _is_forbidden_path(raw_path: object, event: str = "open") -> tuple[bool, str]:
        if raw_path is None or isinstance(raw_path, int):
            return False, ""
        if isinstance(raw_path, bytes):
            path_str = raw_path.decode("utf-8", errors="replace")
        else:
            try:
                path_str = os.fspath(raw_path)  # type: ignore[arg-type]
                if isinstance(path_str, bytes):
                    path_str = path_str.decode("utf-8", errors="replace")
            except TypeError:
                path_str = str(raw_path)
        if path_str in ("", ":memory:"):
            return False, ""

        candidates = [path_str]
        try:
            candidates.append(os.path.realpath(path_str))
        except Exception:
            pass

        # If wildcards exist, also inspect the base dir realpath
        if "*" in path_str or "?" in path_str or "[" in path_str:
            try:
                base_dir = path_str.split("*")[0].split("?")[0].split("[")[0]
                if base_dir:
                    candidates.append(os.path.realpath(base_dir))
                    candidates.append(base_dir)
            except Exception:
                pass

        for p in candidates:
            normalized = p.replace("\\", "/")
            norm_lower = normalized.lower()
            try:
                absn = _norm(p)
            except Exception:
                absn = normalized
            if absn == self_file:
                continue  # traceback rendering of the launcher frame (no GT content)

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

            # 6./7. GT-adjacent plaintext caches / eval outputs / agent artifact dirs (by name), and the
            # configured deny roots (real $HOME/.cache/projects, identity dir, artifact dir) (RT3-01).
            # Interpreter-owned roots (venv, pycache prefix, recipeboxer package dir) are exempt; listing an
            # exact sys.path entry (import machinery) is exempt from deny roots.
            if not any(_under(absn, r) for r in interp_roots) and not _is_dist_metadata(absn):
                absn_slash = absn + "/"
                for seg in DENY_SEGMENTS:
                    if seg in absn_slash:
                        return True, f"Unauthorized access to GT-adjacent path ({seg.strip('/')}): {path_str}"
                sys_path_listing = event != "open" and absn in listdir_ok
                for d in denies:
                    if _under(absn, d) and not sys_path_listing:
                        return True, f"Unauthorized access outside SUT sandbox (deny root): {path_str}"

        # 8. Strict allowlist (harness mode): resolved path must lie in an allowed root
        if strict:
            for p in candidates[:2]:
                try:
                    absn = _norm(p)
                except Exception:
                    continue
                if absn == self_file:
                    continue
                if _under(absn, "/proc"):
                    if not _proc_ok(absn):
                        return True, f"Unauthorized access to foreign process data: {path_str}"
                    continue
                if any(_under(absn, a) for a in allows):
                    continue
                if event in ("os.listdir", "os.scandir", "os.walk") and absn in listdir_ok:
                    continue
                if _is_dist_metadata(absn):
                    continue
                return True, f"Unauthorized access outside SUT sandbox allowlist: {path_str}"

        return False, ""

    def _trip(reason: str) -> None:
        sys.stderr.write(f"CHEAT_DETECTED: {reason}\n")
        sys.stderr.flush()
        os._exit(86)

    def _audit_hook(event: str, args: tuple) -> None:
        # 1. Package / module imports (evals, cardforge, tests)
        if event == "import":
            if args:
                mod_name = str(args[0])
                mod_root = mod_name.split(".")[0]
                if mod_root in ("evals", "cardforge", "tests"):
                    _trip(f"Unauthorized import of {mod_name}")
            return

        # 2. Subprocess and command execution
        if (
            event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn")
            or event.startswith("subprocess.")
            or event.startswith("os.exec")
        ):
            _trip(f"Unauthorized process execution ({event})")

        # 3. Path inspection and traversal events
        if (
            event in ("open", "os.listdir", "os.scandir", "os.walk", "sqlite3.connect")
            or event.startswith("glob.glob")
            or event in ("pathlib.Path.glob", "pathlib.Path.rglob")
        ):
            if args:
                ev = "open" if event in ("open", "sqlite3.connect") else ("os.listdir" if "glob" in event else event)
                forbidden, reason = _is_forbidden_path(args[0], ev)
                if forbidden:
                    _trip(reason)
                # glob.glob(pattern, recursive) / glob.glob/2 (pattern, recursive, root_dir, dir_fd)
                if event.startswith("glob.glob") and len(args) >= 3 and args[2]:
                    forbidden, reason = _is_forbidden_path(args[2], "os.listdir")
                    if forbidden:
                        _trip(reason)
            return

        # 4. File copy (check both source and destination)
        if event in ("shutil.copyfile", "shutil.copytree", "shutil.move", "os.rename"):
            for arg in args[:2]:
                forbidden, reason = _is_forbidden_path(arg)
                if forbidden:
                    _trip(reason)
            return

    sys.addaudithook(_audit_hook)


def install_from_env(env: Optional[dict] = None) -> dict:
    """Installs the hook using the harness sandbox config (if any). Returns the config used."""
    cfg = load_sandbox_config(env)
    _install_anti_cheat_audit_hook(
        strict=bool(cfg.get("strict", False)),
        allow_roots=[str(x) for x in (cfg.get("allow") or [])],
        deny_roots=[str(x) for x in (cfg.get("deny") or [])],
    )
    return cfg


def main() -> None:
    """Installs audit hook and delegates execution to recipeboxer.cli."""
    # Remove sut_boot directory from sys.path so Python import machinery does not search evals/
    sut_dir = str(Path(__file__).resolve().parent)
    while sut_dir in sys.path:
        sys.path.remove(sut_dir)

    install_from_env()

    # Delegate execution to recipeboxer
    from recipeboxer.cli import main as rb_main
    sys.exit(rb_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
