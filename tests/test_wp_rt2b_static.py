"""Unit tests for FIND-01 (WP-RT2b): Static Anti-Cheat AST and Double Sniffing Invariants.

Verifies that production code does not:
1. Sniff test doubles by class name (e.g. `endswith("Mock")` or `__name__.endswith("Mock")`).
2. Sniff test doubles by attribute (e.g. `hasattr(..., "mock")`).
3. Contain `MockVerifierAdapter` in production modules.
4. Import `unittest.mock` or `pytest` in production code.
5. Alter execution flow based on mock class names in extractor.py, semantic.py, or orchestrator.py.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import pytest


def get_production_py_files() -> list[Path]:
    """Returns all production python files under recipeboxer/, excluding fingerprint.py (owned by RT2a)."""
    pkg_dir = Path("recipeboxer")
    py_files = [
        p for p in pkg_dir.rglob("*.py")
        if p.name != "fingerprint.py"  # owned concurrently by WP-RT2a
    ]
    assert len(py_files) > 10, "Found too few production python files"
    return py_files


def test_no_mock_string_sniffing():
    """Verify production code does not check for 'Mock' suffix or 'mock' attribute in execution logic."""
    for py_file in get_production_py_files():
        content = py_file.read_text(encoding="utf-8")
        
        # 1. No endswith("Mock") or endswith('Mock')
        assert 'endswith("Mock")' not in content, f"{py_file} contains endswith('Mock')"
        assert "endswith('Mock')" not in content, f"{py_file} contains endswith('Mock')"

        # 2. No hasattr(..., "mock")
        match = re.search(r"hasattr\s*\([^,]+,\s*['\"]mock['\"]", content, re.IGNORECASE)
        assert match is None, f"Found hasattr(..., 'mock') in {py_file}"

        # 3. No MockVerifierAdapter
        assert "class MockVerifierAdapter" not in content, f"{py_file} defines MockVerifierAdapter"


def test_no_test_framework_imports_in_production():
    """Verify production modules do not import unittest.mock or pytest."""
    for py_file in get_production_py_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "pytest", f"{py_file} imports pytest"
                    assert not alias.name.startswith("unittest.mock"), f"{py_file} imports unittest.mock"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module != "pytest", f"{py_file} imports from pytest"
                    assert not node.module.startswith("unittest.mock"), f"{py_file} imports from unittest.mock"


def test_ast_scan_for_double_sniffing_patterns():
    """Detailed AST walk checking Call and Compare nodes for mock detection."""
    for py_file in get_production_py_files():
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            # Check for calls like type(x).__name__.endswith("Mock")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "endswith":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value == "Mock":
                            pytest.fail(f"AST detected endswith('Mock') call in {py_file}:{node.lineno}")
                if node.func.attr == "hasattr":
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        if str(node.args[1].value).lower() == "mock":
                            pytest.fail(f"AST detected hasattr(..., 'mock') call in {py_file}:{node.lineno}")


def test_owned_modules_clean_llm_delegation():
    """Verify that extractor.py and semantic.py use clean protocol / LLMClient delegation."""
    extractor_code = Path("recipeboxer/extraction/extractor.py").read_text(encoding="utf-8")
    assert 'type(res).__name__.endswith("Mock")' not in extractor_code
    assert 'isinstance(client, LLMClient)' in extractor_code

    semantic_code = Path("recipeboxer/correlation/semantic.py").read_text(encoding="utf-8")
    assert 'endswith("Mock")' not in semantic_code
    assert 'isinstance(client, LLMClient)' in semantic_code
