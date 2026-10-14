"""Command-line interface and server runner for RecipeBoxer."""

from __future__ import annotations

import argparse
import asyncio
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
    """Builds the argument parser for RecipeBoxer server CLI."""
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


def create_analyze_parser() -> argparse.ArgumentParser:
    """Builds argument parser for the 'analyze' headless subcommand (WP-E3, contracts §7)."""
    parser = argparse.ArgumentParser(
        prog="recipeboxer analyze",
        description="Run production headless digitization pipeline with auto-approve.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Input directory containing recipe card images",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Output JSON file destination",
    )
    parser.add_argument(
        "--auto-approve",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-approve HITL proposals",
    )
    parser.add_argument(
        "--profile",
        type=str,
        choices=["economy", "balanced", "best"],
        default="balanced",
        help="Model quality and cost profile",
    )
    parser.add_argument(
        "--image-sets",
        type=str,
        default=None,
        help="Path to JSON file with image sets to skip correlation grouping",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="Maximum run spending budget cap in USD (WP-B1, WP-B3)",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="Maximum number of LLM generation calls allowed (WP-B1, WP-B3)",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=None,
        help="Maximum concurrent ingestion tasks",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="info",
        help="Logging verbosity level",
    )
    return parser



def main(args: Optional[List[str]] = None) -> int:
    """Main execution entry point for RecipeBoxer."""
    # Essential for PyInstaller on Windows/macOS to prevent infinite subprocess loops
    multiprocessing.freeze_support()

    # Intercept and redirect legacy/card-generator subcommands
    cli_args = args if args is not None else sys.argv[1:]
    if cli_args and cli_args[0] in ("generate", "card-generator", "generate-cards", "cardforge"):
        print(
            "Synthetic card generation has moved to the standalone 'cardforge' package.\n"
            "Please run: python -m cardforge [options]"
        )
        return 0

    if cli_args and cli_args[0] == "analyze":
        analyze_parser = create_analyze_parser()
        parsed_analyze = analyze_parser.parse_args(args=cli_args[1:])

        if parsed_analyze.concurrency is not None:
            os.environ["RECIPEBOXER_MAX_CONCURRENCY"] = str(parsed_analyze.concurrency)

        log_level = getattr(logging, parsed_analyze.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

        from recipeboxer.headless import run_headless_analysis

        try:
            asyncio.run(
                run_headless_analysis(
                    input_dir=parsed_analyze.input,
                    out_file=parsed_analyze.out,
                    auto_approve=parsed_analyze.auto_approve,
                    profile=parsed_analyze.profile,
                    image_sets=parsed_analyze.image_sets,
                    budget_usd=parsed_analyze.budget_usd,
                    max_calls=parsed_analyze.max_calls,
                )
            )

            return 0
        except Exception as exc:
            logging.getLogger("recipeboxer.cli").error(f"Fatal error during analysis: {exc}", exc_info=True)
            return 1

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
