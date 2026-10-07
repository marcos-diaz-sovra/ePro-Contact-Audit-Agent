"""Resolve writable app dir vs bundled resources (source vs frozen .exe)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def app_root() -> Path:
    """Folder that contains the .exe or the .app.

    Windows: the folder that contains eProContactAudit.exe.
    macOS .app: the folder that contains eProContactAudit.app, not Contents/MacOS.
    That folder is read-only when macOS App Translocation is active.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
            return exe.parents[3]
        return exe.parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Folder that holds bundled files such as config/states.yaml."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def support_dir() -> Path:
    """Per-user folder that is writable even when the app itself is not."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "ePro Contact Audit Agent"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local) / "ePro Contact Audit Agent"
    else:
        base = Path.home() / ".epro-contact-audit"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _can_write(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Where outputs and the Playwright browser cache are written."""
    root = app_root()
    if _can_write(root):
        return root
    return support_dir()


def bundled_browser_dir() -> Path | None:
    """Chromium shipped inside the app, if this build includes it."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
            candidates.append(exe.parent.parent / "Resources" / "ms-playwright")
    candidates.append(app_root() / "ms-playwright")
    for path in candidates:
        try:
            if path.is_dir() and any(path.glob("chromium*")):
                return path
        except OSError:
            continue
    return None


def seed_browsers(dest: Path) -> bool:
    """Copy bundled Chromium into a writable cache. Returns True if Chromium is there."""
    if any(dest.glob("chromium*")):
        return True
    source = bundled_browser_dir()
    if source is None:
        return False
    if source.resolve() == dest.resolve():
        return True
    dest.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in {"__dirlock", ".write_test"}:
            continue
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
    return any(dest.glob("chromium*"))


def prepare_runtime() -> Path:
    """chdir to a writable folder and point Playwright at a writable browser cache."""
    root = data_dir()
    os.chdir(root)
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "ms-playwright")
    return root
