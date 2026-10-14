"""CLI execution entry point for cardforge (`python -m cardforge`)."""

from __future__ import annotations

import sys
from cardforge.generator import main

if __name__ == "__main__":
    sys.exit(main())
