"""Command-line interface and server runner for RecipeBoxer."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
from typing import List, Optional

import uvicorn

from recipeboxer import __version__
from recipeboxer.api.app import create_app
from recipeboxer.config import get_settings


def create_parser() -> argparse.ArgumentParser:
    """Builds the argument parser for RecipeBoxer CLI."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="recipeboxer",
        description="RecipeBoxer: Intelligent recipe card digitization and HITL correlation engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-V",
        "-v",
        "--version",
        action="version",
        version=f"RecipeBoxer {__version__}",
        help="Show application version and exit",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("RECIPEBOXER_API_HOST", settings.api_host),
        help="Host address to bind the FastAPI server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RECIPEBOXER_API_PORT", settings.api_port)),
        help="Port to bind the FastAPI server",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=int(os.environ.get("RECIPEBOXER_MAX_CONCURRENCY", settings.max_concurrency)),
        help="Default maximum concurrent ingestion tasks",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="info",
        help="Logging verbosity level for server output",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload for development (ignored in frozen standalone binary)",
    )
    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Main execution entry point for RecipeBoxer."""
    # Essential for PyInstaller on Windows/macOS to prevent infinite subprocess loops
    multiprocessing.freeze_support()

    parser = create_parser()
    parsed_args = parser.parse_args(args=args)

    if parsed_args.concurrency is not None:
        os.environ["RECIPEBOXER_MAX_CONCURRENCY"] = str(parsed_args.concurrency)

    # Configure logging
    log_level = getattr(logging, parsed_args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("recipeboxer.cli")
    logger.info(f"Starting RecipeBoxer v{__version__} on http://{parsed_args.host}:{parsed_args.port}")

    # Build FastAPI application instance
    app = create_app()

    # In frozen PyInstaller mode, uvicorn must run the app object directly
    is_frozen = getattr(sys, "frozen", False)
    use_reload = parsed_args.reload and not is_frozen

    try:
        uvicorn.run(
            app,
            host=parsed_args.host,
            port=parsed_args.port,
            log_level=parsed_args.log_level.lower(),
            reload=use_reload,
            lifespan="on",
        )
        return 0
    except KeyboardInterrupt:
        logger.info("RecipeBoxer server stopped by user interrupt.")
        return 0
    except Exception as exc:
        logger.error(f"Fatal error starting RecipeBoxer server: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
