#!/usr/bin/env python3
"""Executable script for running RecipeBoxer evaluation benchmarks (WP-E3)."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure workspace root is in sys.path when executed directly
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from evals.harness.run import main

if __name__ == "__main__":
    sys.exit(main())
