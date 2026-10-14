"""Static and runtime isolation tests enforcing independence between cardforge and recipeboxer.

CardForge must never import RecipeBoxer, and RecipeBoxer production code
must never import CardForge.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CARDFORGE_ROOT = REPO_ROOT / "cardforge"
RECIPEBOXER_ROOT = REPO_ROOT / "recipeboxer"


def _extract_imported_modules(py_file: Path) -> set[str]:
    """Parses a Python file and returns the set of all top-level imported module names."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except SyntaxError:
        return set()

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    return imported


def test_cardforge_source_never_imports_recipeboxer():
    """Verify that no file under cardforge/ imports recipeboxer statically."""
    py_files = list(CARDFORGE_ROOT.glob("*.py"))
    assert len(py_files) > 0, "No Python source files found in cardforge root"

    violations = []
    for py_file in py_files:
        imported = _extract_imported_modules(py_file)
        if "recipeboxer" in imported:
            violations.append(str(py_file.relative_to(REPO_ROOT)))

    assert not violations, (
        f"CardForge must NOT import recipeboxer, but found imports in: {violations}"
    )


def test_cardforge_runtime_import_cleanliness():
    """Verify that importing cardforge does not trigger an import of recipeboxer at runtime."""
    cmd = [
        sys.executable,
        "-c",
        "import sys, cardforge; assert 'recipeboxer' not in sys.modules, 'recipeboxer was loaded by cardforge'",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Runtime import isolation failed: {res.stderr}"


def test_recipeboxer_production_never_imports_cardforge():
    """Verify that recipeboxer production code never imports cardforge.

    (Excludes recipeboxer/evals which is being decoupled under Phase 2 by WP-C6).
    """
    violations = []
    for py_file in RECIPEBOXER_ROOT.rglob("*.py"):
        rel_path = py_file.relative_to(RECIPEBOXER_ROOT)
        # recipeboxer/evals is decoupled separately under WP-C6
        if str(rel_path).startswith("evals"):
            continue
        imported = _extract_imported_modules(py_file)
        if "cardforge" in imported:
            violations.append(str(py_file.relative_to(REPO_ROOT)))

    assert not violations, (
        f"RecipeBoxer production code must NOT import cardforge, but found imports in: {violations}"
    )
