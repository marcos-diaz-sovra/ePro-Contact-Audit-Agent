"""Resolve writable app dir vs bundled resources (source vs frozen .exe)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Folder that holds the .exe (or the project root when running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Folder that holds bundled files such as config/states.yaml."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def prepare_runtime() -> Path:
    """chdir to the writable app folder and point Playwright at local Chromium."""
    root = app_root()
    os.chdir(root)
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "ms-playwright")
    return root
