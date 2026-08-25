#!/usr/bin/env python3
"""Standalone executable script for running RecipeBoxer evaluation benchmarks."""

import sys
from pathlib import Path

# Ensure workspace root is in sys.path when executed directly
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from recipeboxer.evals.runner import main

if __name__ == "__main__":
    sys.exit(main())
