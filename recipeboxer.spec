# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for RecipeBoxer standalone binary.

Builds a single-file console executable for macOS (Darwin Mach-O), Linux (ELF),
and Windows (.exe) with all required hidden imports and package assets.
"""

from __future__ import annotations

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Comprehensive hidden imports for dynamic imports and frameworks
hidden_imports = [
    # Uvicorn server core, loops, protocols, lifespan
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # FastAPI & Starlette routing, middleware, responses
    "fastapi",
    "fastapi.routing",
    "fastapi.responses",
    "fastapi.exceptions",
    "starlette",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.responses",
    "starlette.routing",
    "starlette.status",
    "starlette.types",
    # Pydantic V2 and settings
    "pydantic",
    "pydantic_settings",
    "pydantic_core",
    # Google GenAI / Antigravity SDKs
    "google.genai",
    "google.antigravity",
    # RecipeBoxer internal package modules
    "recipeboxer",
    "recipeboxer.config",
    "recipeboxer.cli",
    "recipeboxer.api",
    "recipeboxer.api.app",
    "recipeboxer.api.routes",
    "recipeboxer.agent",
    "recipeboxer.agent.orchestrator",
    "recipeboxer.agent.session",
    "recipeboxer.correlation",
    "recipeboxer.correlation.optimizer",
    "recipeboxer.correlation.semantic",
    "recipeboxer.exporter",
    "recipeboxer.exporter.plan_to_eat",
    "recipeboxer.extraction",
    "recipeboxer.extraction.extractor",
    "recipeboxer.ingestion",
    "recipeboxer.ingestion.runner",
    "recipeboxer.ingestion.scanner",
    "recipeboxer.models",
    "recipeboxer.models.domain",
    "recipeboxer.models.correlation",
    "recipeboxer.models.hitl",
    "recipeboxer.models.pipeline",
    "recipeboxer.models.recipe",
    "recipeboxer.storage",
    "recipeboxer.storage.cache",
    "recipeboxer.vision",
    "recipeboxer.vision.fingerprint",
    "recipeboxer.vision.heuristics",
    # Database, multipart, imaging, environment & networking
    "sqlite3",
    "email_validator",
    "multipart",
    "python_multipart",
    "httpx",
    "httpcore",
    "PIL",
    "PIL.Image",
    "dotenv",
]

# Dynamically collect submodules for key packages
for package_name in ["recipeboxer", "uvicorn", "starlette", "fastapi"]:
    try:
        collected = collect_submodules(package_name)
        hidden_imports.extend(collected)
    except Exception:
        pass

hidden_imports = sorted(list(set(hidden_imports)))

# Bundle package data
datas = [
    ("recipeboxer", "recipeboxer"),
]

a = Analysis(
    ["recipeboxer/cli.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "notebook",
        "scipy",
        "IPython",
        "pytest",
        "_pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="recipeboxer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
