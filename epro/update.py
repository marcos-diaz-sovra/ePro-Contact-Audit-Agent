"""Check GitHub releases and replace the installed app with a newer one.

Release assets must include the platform in the file name:
``*-windows.zip`` on Windows and ``*-macos.zip`` on macOS.
The running process cannot overwrite itself, so a helper waits for this
process to exit, copies the new files, and relaunches.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from epro import __version__
from epro.paths import support_dir

REPO = "marcos-diaz-sovra/ePro-Contact-Audit-Agent"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"eProContactAudit/{__version__}",
}


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    asset_name: str
    download_url: str
    notes: str


def version_tuple(text: str) -> tuple[int, ...]:
    raw = (text or "").strip().lstrip("vV")
    nums: list[int] = []
    for part in raw.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    return tuple(nums)


def is_newer(remote: str, local: str) -> bool:
    left, right = version_tuple(remote), version_tuple(local)
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def platform_markers() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("macos", "darwin", "mac")
    if sys.platform == "win32":
        return ("windows", "win64", "win")
    return ("linux",)


def pick_asset(assets: list[dict]) -> dict | None:
    markers = platform_markers()
    zips = [a for a in assets if str(a.get("name") or "").lower().endswith(".zip")]
    for marker in markers:
        for asset in zips:
            if marker in str(asset.get("name") or "").lower():
                return asset
    return None


def check_for_update(local_version: str | None = None, timeout: int = 15) -> UpdateOffer | None:
    """Return an offer when GitHub has a newer release for this platform."""
    local = local_version or __version__
    response = requests.get(API_URL, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    remote = str(data.get("tag_name") or "")
    if not remote or not is_newer(remote, local):
        return None
    asset = pick_asset(list(data.get("assets") or []))
    if not asset:
        return None
    url = str(asset.get("browser_download_url") or "")
    if not url:
        return None
    return UpdateOffer(
        version=remote.lstrip("vV"),
        asset_name=str(asset.get("name") or "update.zip"),
        download_url=url,
        notes=str(data.get("body") or "").strip(),
    )


def download_update(offer: UpdateOffer, dest: Path, on_progress=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(offer.download_url, headers=_HEADERS, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with dest.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done, total)
    return dest


def _app_bundle() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        return exe.parents[2]
    return None


def install_target() -> tuple[Path, str]:
    """Where the new build should be written, and how to relaunch it.

    macOS may be running a read-only App Translocation copy. In that case the
    update is installed under ~/Applications instead.
    """
    bundle = _app_bundle()
    if bundle is not None:
        protected = "AppTranslocation" in str(bundle)
        dest = Path.home() / "Applications" / bundle.name if protected else bundle
        relaunch = str(dest)
        return dest, relaunch
    if getattr(sys, "frozen", False):
        folder = Path(sys.executable).resolve().parent
        return folder, str(Path(sys.executable).resolve())
    raise RuntimeError("Updates install only into the packaged app, not a source checkout.")


def extract_payload(zip_path: Path) -> Path:
    stage = support_dir() / "update-staging"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(stage)
    return _payload_root(stage)


def _payload_root(stage: Path) -> Path:
    apps = [p for p in stage.rglob("*.app") if p.is_dir()]
    if apps:
        return apps[0]
    exes = list(stage.rglob("eProContactAudit.exe"))
    if exes:
        return exes[0].parent
    children = [p for p in stage.iterdir() if p.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return stage


def launch_swap(source: Path, dest: Path, relaunch: str) -> None:
    """Start a helper that replaces ``dest`` after this process exits."""
    helper_dir = support_dir()
    pid = os.getpid()
    if sys.platform == "win32":
        script = helper_dir / "apply-update.ps1"
        script.write_text(_WINDOWS_HELPER, encoding="utf-8")
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProcId",
                str(pid),
                "-Source",
                str(source),
                "-Dest",
                str(dest),
                "-Relaunch",
                relaunch,
            ],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        return
    script = helper_dir / "apply-update.sh"
    script.write_text(_POSIX_HELPER, encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(
        ["bash", str(script), str(pid), str(source), str(dest), relaunch],
        start_new_session=True,
        close_fds=True,
    )


_WINDOWS_HELPER = r"""
param(
    [int]$ProcId,
    [string]$Source,
    [string]$Dest,
    [string]$Relaunch
)
while (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {
    Start-Sleep -Milliseconds 400
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
& robocopy $Source $Dest /E /XD outputs .progress /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { exit $LASTEXITCODE }
Start-Process -FilePath $Relaunch
"""

_POSIX_HELPER = r"""
#!/bin/bash
set -euo pipefail
PROC_ID="$1"
SOURCE="$2"
DEST="$3"
RELAUNCH="$4"
while kill -0 "$PROC_ID" 2>/dev/null; do
    sleep 0.4
done
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$SOURCE" "$DEST"
open "$RELAUNCH"
"""
