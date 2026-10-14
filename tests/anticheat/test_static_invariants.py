"""Static anti-cheat tests enforcing invariants INV-AC1..INV-AC3.

These tests enforce architectural boundaries and anti-cheat invariants against
the production package `recipeboxer/`:

- INV-AC1 (test_no_answer_key_paths): Production code must never reference test
  fixtures, answer keys, manifest paths, or eval environment variables.
- INV-AC2 (test_no_filename_semantics): Production code in core pipeline stages
  (vision, correlation, extraction, ai, agent, ingestion) must not infer card
  roles, continuation semantics, typography/modality, or recipe identity from
  filename substrings, regex patterns, or category code tokens.
- INV-AC3 (test_no_fixture_content): Production code must not hardcode corpus
  recipe titles, distinctive ingredient lines, recipe IDs, or fixture SHA-256
  digests.
- INV-AC4 (test_no_cardforge_or_evals_import): Production code must not import
  cardforge, evals, or tests packages.

Heuristic Design for test_no_filename_semantics:
To eliminate false positives while strictly catching all cheats, the AST analysis
flags:
1. Regex patterns passed to re.search/match/compile/findall/finditer containing:
   - Card part/number patterns: `card\\s*\\d`, `part\\s*\\d`, `pt\\s*\\d`
   - Side alternatives: `front|back`
2. Binary comparisons and membership tests (`in`, `not in`, `==`, `!=`) where:
   - One operand is a semantic token (`front`, `back`, `card1`..`card9`, `part1`..`part9`,
     `pt1`..`pt9`, category codes `vhc`, `clp`, `dmg`, `mcs`, `sta`, `typ`, or `_vhc_` style,
     or known recipe cheat tokens like `cinnamon`, `lemon`, `fudge`, `pecan`, `unruled`,
     `stain`, `clipping`, `typewriter`).
   - The other operand evaluates against variables representing filenames or card identifiers
     (variable or attribute names containing `fname`, `fn1`, `fn2`, `card_id`, `filename`,
     `name`, `path`, `stem`, `c_str`, `all_names`, `clean_cid`, `s1`, `s2`, or `s` for
     modality/category normalizers).
3. Legitimate Non-Cheats (Safelist / Exemptions):
   - Role enums and role string normalizers (e.g. `CardRole.FRONT_ONLY`, `s in ("FRONT_ONLY", ...)`
     in `heuristics.py:457` where `s` is normalizing enum values).
   - Multimodal prompt template instructions explaining card concepts to LLMs
     (e.g. `SEMANTIC_VERIFICATION_PROMPT` in `semantic.py`, prompt strings in `extractor.py`).
   - Default role identifiers and generic safe tokens in `gemini.py`'s
     `sanitize_card_id()` (`clean.lower() in ("card_front", "card_back", ...)`).
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

# Repository root: /storage/emulated/10/AntiGravity/Workspaces/recipeboxer
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE_ROOT = REPO_ROOT / "recipeboxer"

# Temporary exclusions list:
# Production scope excludes static assets, and temporarily excludes card_generator and
# evals which move to separate top-level packages in Phase 1 / Phase 2.
TEMPORARY_EXCLUSIONS = {
    # TODO(WP-V2): card_generator and tools move to top-level cardforge/ project
    "recipeboxer/tools/card_generator.py",
    "recipeboxer/tools/__init__.py",
    # TODO(WP-E3): evals package moves out of recipeboxer to top-level evals/
    "recipeboxer/evals",
    # Static frontend web assets
    "recipeboxer/api/static",
}

# Source files with legitimate usage of manifest.json for .recipeboxer project archives
# or static web manifests:
# - storage/archive.py: Reads/writes project archive manifest.json
# - models/archive.py: Defines ProjectArchiveManifest schema model
# - agent/orchestrator.py: Validates/reads manifest.json from uploaded .recipeboxer archives
# - api/app.py: Serves web app manifest.json / static assets
LEGITIMATE_MANIFEST_FILES = {
    "recipeboxer/storage/archive.py",
    "recipeboxer/models/archive.py",
    "recipeboxer/agent/orchestrator.py",
    "recipeboxer/api/app.py",
}

# Scoped packages for filename semantics checks:
FILENAME_SEMANTICS_PACKAGES = [
    "recipeboxer/vision",
    "recipeboxer/correlation",
    "recipeboxer/extraction",
    "recipeboxer/ai",
    "recipeboxer/agent",
    "recipeboxer/ingestion",
]

# Filename variable name substrings indicating card IDs, paths, or names:
FILENAME_VAR_SUBSTRINGS = {
    "fname",
    "fn1",
    "fn2",
    "card_id",
    "filename",
    "name",
    "path",
    "stem",
    "c_str",
    "all_names",
    "clean_cid",
    "c_path",
    "s1",
    "s2",
}

# Semantic tokens used in filename cheats:
SEMANTIC_TOKENS: Set[str] = {"front", "back"}
for i in range(1, 10):
    SEMANTIC_TOKENS.add(f"card{i}")
    SEMANTIC_TOKENS.add(f"part{i}")
    SEMANTIC_TOKENS.add(f"pt{i}")

CATEGORY_CODES: Set[str] = {"vhc", "clp", "dmg", "mcs", "sta", "typ"}
for code in CATEGORY_CODES:
    SEMANTIC_TOKENS.add(code)
    SEMANTIC_TOKENS.add(f"_{code}_")

# Known recipe cheat tokens checked against filename strings in production:
CHEAT_FILENAME_KEYWORDS: Set[str] = {
    "cinnamon",
    "lemon",
    "fudge",
    "pecan",
    "unruled",
    "stain",
    "clipping",
    "newspaper",
    "newsprint",
    "typewriter",
    "typewritten",
    "typed",
    "mixed",
    "stained",
    "damaged",
}
SEMANTIC_TOKENS.update(CHEAT_FILENAME_KEYWORDS)

# Regex patterns matching filename sequencing or side tokens:
REGEX_CHEAT_PATTERNS = [
    re.compile(r"card\s*([0-9]|\[0-9|\\[0-9]|\()", re.I),
    re.compile(r"part\s*([0-9]|\[0-9|\\[0-9]|\()", re.I),
    re.compile(r"pt\s*([0-9]|\[0-9|\\[0-9]|\()", re.I),
    re.compile(r"(front\|back|back\|front)", re.I),
]

# Common generic pantry staples that should not trigger false positives if they appear
# in normalizer examples, docstrings, or prompt guidelines:
GENERIC_PANTRY_STAPLES = {
    "1 tsp baking soda",
    "2 cups all-purpose flour",
    "1/2 cup granulated sugar",
    "1 tsp vanilla extract",
    "1/2 cup warm water",
}


def is_excluded(path: Path) -> bool:
    """Checks if a file path matches TEMPORARY_EXCLUSIONS."""
    rel_str = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    for excl in TEMPORARY_EXCLUSIONS:
        if rel_str == excl or rel_str.startswith(excl + "/"):
            return True
    return False


def get_scoped_python_files(package_dirs: Optional[List[str]] = None) -> List[Path]:
    """Retrieves all non-excluded Python files under package_dirs or recipeboxer/."""
    if package_dirs is None:
        target_dirs = [PACKAGE_ROOT]
    else:
        target_dirs = [REPO_ROOT / p for p in package_dirs]

    files: List[Path] = []
    for d in target_dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.py")):
            if not is_excluded(p):
                files.append(p)
    return files


def _extract_string_constants_from_node(node: ast.AST) -> Set[str]:
    """Extracts lowercase stripped string constants from an AST expression node."""
    tokens: Set[str] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        tokens.add(node.value.strip().lower())
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                tokens.add(elt.value.strip().lower())
    return tokens


def _extract_varnames_from_node(node: ast.AST) -> Set[str]:
    """Extracts variable and attribute names from an AST expression node."""
    names: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id.lower())
        elif isinstance(n, ast.Attribute):
            names.add(n.attr.lower())
    return names


@pytest.mark.xfail(
    strict=True,
    reason="Cheat present; removed in Phase 2 WP-C1..C6 - flip to hard pass then",
)
def test_no_answer_key_paths() -> None:
    """Enforces INV-AC1: No answer key paths or manifest lookups in production code.

    No source file in scoped packages may contain string literals or path joins referencing:
    - 'tests/fixtures'
    - 'sample_cards'
    - 'manifest.json' (except legitimate archive / static app files)
    - 'ground_truth'
    - 'RB_EVAL'
    - Path join chains with 'tests'
    """
    forbidden_tokens = ["tests/fixtures", "sample_cards", "ground_truth", "RB_EVAL"]
    violations: List[Tuple[str, int, str]] = []

    for file_path in get_scoped_python_files():
        rel_str = str(file_path.relative_to(REPO_ROOT)).replace("\\", "/")
        with open(file_path, "r", encoding="utf-8") as fp:
            source = fp.read()

        try:
            tree = ast.parse(source, filename=rel_str)
        except SyntaxError as exc:
            violations.append((rel_str, exc.lineno or 0, f"SyntaxError parsing file: {exc}"))
            continue

        # 1. AST Inspection: inspect string constants and Path join chains
        for node in ast.walk(tree):
            # Check string constants
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                for tok in forbidden_tokens:
                    if tok in val:
                        violations.append((rel_str, node.lineno, f"Contains forbidden string token {tok!r}"))
                if "manifest.json" in val and rel_str not in LEGITIMATE_MANIFEST_FILES:
                    violations.append((rel_str, node.lineno, "References 'manifest.json' outside legitimate archive files"))

            # Check Path / 'tests' join chains (e.g. path / "tests")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and side.value == "tests":
                        violations.append((rel_str, node.lineno, "Path / join chain contains 'tests' segment"))

            # Check os.path.join(..., 'tests', ...) or Path(..., 'tests', ...)
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id
                if func_name in ("join", "Path"):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value == "tests":
                            violations.append((rel_str, node.lineno, f"Call to {func_name} contains 'tests' argument"))

        # 2. Source text regex fallback:
        # Fallback regex scan over source text to catch any references in comments, raw strings, or dynamic formatting.
        regex_patterns = [
            (re.compile(r"tests/fixtures"), "tests/fixtures"),
            (re.compile(r"sample_cards"), "sample_cards"),
            (re.compile(r"RB_EVAL"), "RB_EVAL"),
            (re.compile(r"(?:/|\bjoin\s*\([^)]*)\s*['\"]tests['\"]"), "path join with 'tests'"),
        ]
        for lno, line in enumerate(source.splitlines(), 1):
            for pattern, label in regex_patterns:
                if pattern.search(line):
                    # Only append if not already flagged on this line to avoid duplicate noise
                    if not any(v[0] == rel_str and v[1] == lno for v in violations):
                        violations.append((rel_str, lno, f"Source text regex matched {label}"))
            if "manifest.json" in line and rel_str not in LEGITIMATE_MANIFEST_FILES:
                if not any(v[0] == rel_str and v[1] == lno for v in violations):
                    violations.append((rel_str, lno, "Source text references 'manifest.json'"))

    # Format actionable error message listing offending file:line items
    lines = [f"{rel}:{lno}: {reason}" for rel, lno, reason in sorted(set(violations))]
    assert not violations, f"Anti-cheat violation inventory for INV-AC1 ({len(violations)} hits):\n" + "\n".join(lines)


@pytest.mark.xfail(
    strict=True,
    reason="Cheat present; removed in Phase 2 WP-C1..C6 - flip to hard pass then",
)
def test_no_filename_semantics() -> None:
    """Enforces INV-AC2: No filename semantics or category code inference in pipeline.

    In recipeboxer/{vision,correlation,extraction,ai,agent,ingestion}:
    - No comparisons or membership tests against filename tokens ('front', 'back', 'card1'..'card9',
      'part1'..'part9', 'pt1'..'pt9', category codes 'vhc', 'clp', 'dmg', 'mcs', 'sta', 'typ',
      or recipe keywords).
    - No regex patterns inspecting filenames for card numbers, parts, or front/back roles.
    - Avoids false positives on enum members, prompt text, and sanitizer default parameters.
    """
    violations: List[Tuple[str, int, str]] = []

    for file_path in get_scoped_python_files(FILENAME_SEMANTICS_PACKAGES):
        rel_str = str(file_path.relative_to(REPO_ROOT)).replace("\\", "/")
        with open(file_path, "r", encoding="utf-8") as fp:
            source = fp.read()

        try:
            tree = ast.parse(source, filename=rel_str)
        except SyntaxError as exc:
            violations.append((rel_str, exc.lineno or 0, f"SyntaxError: {exc}"))
            continue

        for node in ast.walk(tree):
            # 1. Regex calls: re.search, re.match, re.compile, etc.
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id
                if func_name in ("search", "match", "compile", "findall", "finditer"):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        pattern_str = node.args[0].value
                        for rpat in REGEX_CHEAT_PATTERNS:
                            if rpat.search(pattern_str):
                                violations.append(
                                    (rel_str, node.lineno, f"Regex pattern {pattern_str!r} inspects card/part/side numbers")
                                )
                                break

            # 2. Comparisons and membership tests
            if isinstance(node, ast.Compare):
                # Safelist: sanitize_card_id in recipeboxer/ai/gemini.py line checking generic fallback role IDs
                if rel_str == "recipeboxer/ai/gemini.py" and node.lineno in (42, 43):
                    continue

                left = node.left
                for op, right in zip(node.ops, node.comparators):
                    left_tokens = _extract_string_constants_from_node(left)
                    right_tokens = _extract_string_constants_from_node(right)
                    left_vars = _extract_varnames_from_node(left)
                    right_vars = _extract_varnames_from_node(right)

                    # Check category code checks (e.g. 'CLP' in s, s in ('VHC', 'MCS'), '_vhc_' in s.lower())
                    cat_left = left_tokens.intersection(CATEGORY_CODES | {f"_{c}_" for c in CATEGORY_CODES})
                    cat_right = right_tokens.intersection(CATEGORY_CODES | {f"_{c}_" for c in CATEGORY_CODES})
                    if cat_left and ("s" in right_vars or any(any(sub in v for sub in FILENAME_VAR_SUBSTRINGS) for v in right_vars)):
                        violations.append(
                            (rel_str, node.lineno, f"Category code token {cat_left} checked in comparison: {ast.unparse(node)}")
                        )
                        continue
                    if cat_right and ("s" in left_vars or any(any(sub in v for sub in FILENAME_VAR_SUBSTRINGS) for v in left_vars)):
                        violations.append(
                            (rel_str, node.lineno, f"Category code token {cat_right} checked in comparison: {ast.unparse(node)}")
                        )
                        continue

                    # Check semantic / recipe tokens vs filename variables
                    tok_left = left_tokens.intersection(SEMANTIC_TOKENS)
                    tok_right = right_tokens.intersection(SEMANTIC_TOKENS)

                    right_is_fn = any(any(sub in v for sub in FILENAME_VAR_SUBSTRINGS) for v in right_vars)
                    left_is_fn = any(any(sub in v for sub in FILENAME_VAR_SUBSTRINGS) for v in left_vars)

                    if tok_left and right_is_fn:
                        violations.append(
                            (rel_str, node.lineno, f"Semantic token {tok_left} tested against filename var {right_vars}: {ast.unparse(node)}")
                        )
                    elif tok_right and left_is_fn:
                        violations.append(
                            (rel_str, node.lineno, f"Filename var {left_vars} tested against semantic token {tok_right}: {ast.unparse(node)}")
                        )

    lines = [f"{rel}:{lno}: {reason}" for rel, lno, reason in sorted(set(violations))]
    assert not violations, f"Anti-cheat violation inventory for INV-AC2 ({len(violations)} hits):\n" + "\n".join(lines)


@pytest.mark.xfail(
    strict=True,
    reason="Cheat present; removed in Phase 2 WP-C1..C6 - flip to hard pass then",
)
def test_no_fixture_content() -> None:
    """Enforces INV-AC3: No fixture recipe content, titles, or hashes in production code.

    Builds a deny-list dynamically at test time from tests/fixtures/sample_cards/manifest.json:
    - Every ground_truth_recipe title (case-insensitive substring)
    - Distinctive multi-word ingredient lines (>= 4 words) (exact case-insensitive substring)
    - Recipe IDs (case-insensitive substring)
    - SHA-256 fixture hashes (case-insensitive substring)

    NOTE (Phase 1): In Phase 1 and beyond, the deny-list will additionally be generated
    by the scorer directly from the decrypted ground-truth vault in a score-tier test.
    """
    manifest_path = REPO_ROOT / "tests" / "fixtures" / "sample_cards" / "manifest.json"
    assert manifest_path.exists(), f"Sample cards manifest not found at {manifest_path}"

    with open(manifest_path, "r", encoding="utf-8") as fp:
        manifest_data = json.load(fp)

    titles: Set[str] = set()
    recipe_ids: Set[str] = set()
    sha_hashes: Set[str] = set()
    ingredient_lines: Set[str] = set()

    for card in manifest_data.get("cards", []):
        sha = card.get("sha256_hash")
        if sha:
            sha_hashes.add(sha.strip().lower())

        gt = card.get("ground_truth_recipe") or {}
        title = gt.get("title")
        if title:
            titles.add(title.strip())

        rid = gt.get("recipe_id") or card.get("recipe_cluster", {}).get("recipe_id")
        if rid:
            recipe_ids.add(rid.strip())

        for ing in gt.get("ingredients", []):
            cleaned = " ".join(ing.strip().split())
            words = cleaned.split()
            # Distinctive multi-word ingredients (>= 4 words, excluding universal pantry staples)
            if len(words) >= 4 and cleaned.lower() not in GENERIC_PANTRY_STAPLES:
                ingredient_lines.add(cleaned)

    violations: List[Tuple[str, int, str]] = []

    for file_path in get_scoped_python_files():
        rel_str = str(file_path.relative_to(REPO_ROOT)).replace("\\", "/")
        with open(file_path, "r", encoding="utf-8") as fp:
            for lno, line in enumerate(fp, 1):
                line_lower = line.lower()
                for sha in sha_hashes:
                    if sha in line_lower:
                        violations.append((rel_str, lno, f"Contains corpus fixture SHA-256 hash: {sha}"))
                for title in titles:
                    if title.lower() in line_lower:
                        violations.append((rel_str, lno, f"Contains ground-truth recipe title: {title!r}"))
                for rid in recipe_ids:
                    if rid.lower() in line_lower:
                        violations.append((rel_str, lno, f"Contains ground-truth recipe_id: {rid!r}"))
                for ing in ingredient_lines:
                    if ing.lower() in line_lower:
                        violations.append((rel_str, lno, f"Contains ground-truth ingredient line: {ing!r}"))

    lines = [f"{rel}:{lno}: {reason}" for rel, lno, reason in sorted(set(violations))]
    assert not violations, f"Anti-cheat violation inventory for INV-AC3 ({len(violations)} hits):\n" + "\n".join(lines)


def test_no_cardforge_or_evals_import() -> None:
    """Enforces INV-AC4: Production modules must never import cardforge, evals, or tests.

    Production packages under recipeboxer/ must not import:
    - cardforge (separate synthetic card generation project)
    - evals (evaluation harness and scoring framework)
    - tests (test suites, fixtures, test utilities)
    - recipeboxer.evals (internal evals package being moved out, except allowed in cli.py)
    """
    forbidden_roots = {"cardforge", "evals", "tests"}
    allowed_recipeboxer_evals_importers = {"recipeboxer/cli.py"}
    violations: List[Tuple[str, int, str]] = []

    for file_path in get_scoped_python_files():
        rel_str = str(file_path.relative_to(REPO_ROOT)).replace("\\", "/")
        with open(file_path, "r", encoding="utf-8") as fp:
            source = fp.read()

        try:
            tree = ast.parse(source, filename=rel_str)
        except SyntaxError as exc:
            violations.append((rel_str, exc.lineno or 0, f"SyntaxError: {exc}"))
            continue

        for node in ast.walk(tree):
            imported_names: List[Tuple[str, int]] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imported_names.append((mod, node.lineno))

            for name, lineno in imported_names:
                parts = name.split(".")
                root_pkg = parts[0]
                if root_pkg in forbidden_roots:
                    violations.append((rel_str, lineno, f"Forbidden import of package {name!r}"))
                elif name == "recipeboxer.evals" or name.startswith("recipeboxer.evals."):
                    if rel_str not in allowed_recipeboxer_evals_importers:
                        violations.append(
                            (rel_str, lineno, f"Forbidden import of 'recipeboxer.evals' in production module {rel_str}")
                        )

    lines = [f"{rel}:{lno}: {reason}" for rel, lno, reason in sorted(set(violations))]
    assert not violations, f"Forbidden imports detected ({len(violations)} hits):\n" + "\n".join(lines)
