"""Playwright browser session, labelled-field scrape, and in-context downloads."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

from epro.models import Contact
from epro.config import DEFAULT_LABELS

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

_HEADERS = {
    "User-Agent": _BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Labelled table cells: a cell whose text ends with ":" is a label and the
# next cell holds its value. Also pick up <label> + for/next-sibling pairs.
_SCRAPE_LABELLED_JS = """() => {
    const map = {};
    const cells = Array.from(document.querySelectorAll('td, th'));
    for (let i = 0; i < cells.length; i++) {
        const t = (cells[i].innerText || '').replace(/\\s+/g, ' ').trim();
        if (t.endsWith(':') && t.length > 1 && t.length < 80) {
            const label = t.slice(0, -1).trim();
            const next = cells[i + 1]
                ? (cells[i + 1].innerText || '').replace(/\\s+/g, ' ').trim()
                : '';
            if (next && !next.endsWith(':') && !map[label]) map[label] = next;
        }
    }
    document.querySelectorAll('label').forEach(lab => {
        const label = (lab.innerText || '').replace(/[:\\s]+$/, '').trim();
        if (!label) return;
        let val = '';
        const forId = lab.getAttribute('for');
        if (forId) {
            const el = document.getElementById(forId);
            if (el) val = (el.value || el.innerText || '').trim();
        }
        if (!val && lab.nextElementSibling) {
            const sib = lab.nextElementSibling;
            val = (sib.value || sib.innerText || '').replace(/\\s+/g, ' ').trim();
        }
        if (val && !map[label]) map[label] = val;
    });
    return map;
}"""

_LIST_LINKS_JS = """() => {
    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
        href: a.href,
        text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim(),
        title: a.getAttribute('title') || '',
    }));
}"""

# Vendor: hyperlink under Primary Vendor Information & PO Terms.
_VENDOR_LINK_JS = """() => {
    const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
    const cells = Array.from(document.querySelectorAll('td, th'));
    for (let i = 0; i < cells.length; i++) {
        const t = norm(cells[i].innerText).replace(/:$/, '');
        if (!/^vendor$/i.test(t)) continue;
        const next = cells[i + 1];
        if (!next) continue;
        const a = next.querySelector('a');
        if (!a) {
            return { text: norm(next.innerText), href: '', onclick: '', vendorId: '' };
        }
        const onclick = a.getAttribute('onclick') || '';
        const m = onclick.match(/viewExternalVendorProfile\\('([^']+)'\\)/);
        return {
            text: norm(a.innerText || a.textContent),
            href: a.href || '',
            onclick,
            vendorId: m ? m[1] : '',
        };
    }
    return null;
}"""

# File links in the cell after the "Agency Attachments" label on the PO page.
_AGENCY_ATTACHMENTS_JS = """() => {
    const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
    const files = [];
    const cells = Array.from(document.querySelectorAll('td, th'));
    for (let i = 0; i < cells.length; i++) {
        const t = norm(cells[i].innerText).replace(/:$/, '');
        if (!/^agency attachments$/i.test(t)) continue;
        const next = cells[i + 1] || cells[i];
        next.querySelectorAll('a[href]').forEach(a => {
            files.push({
                href: a.href,
                text: norm(a.innerText || a.textContent),
                title: a.getAttribute('title') || '',
            });
        });
        break;
    }
    return files;
}"""

_VENDOR_ID_PREFIX = re.compile(r"^(V\d+)\s*[-–]\s*(.+)$", re.IGNORECASE)

# Government-buyer fields on the PO page — never treat these as vendor contact.
_IGNORE_LABELS = {
    "contact instructions",
    "purchaser",
    "buyer",
    "entered by",
    "organization",
    "department",
}

_DOWNLOAD_HINT = re.compile(
    r"(attachment|download|viewdocument|viewdoc|documentdownload|"
    r"\.(pdf|docx?|xlsx?|txt|rtf)(\?|$))",
    re.IGNORECASE,
)
_SKIP_HREF = re.compile(
    r"^(javascript:|#|mailto:|tel:)|poSummary\.|login\.|logout\.|"
    r"attachments\.sd[ao]|css|javascript\.js",
    re.IGNORECASE,
)
_FILE_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|txt|rtf)(?:$|\?)",
    re.IGNORECASE,
)

_pw = None
_browser = None
_lock = asyncio.Lock()


def _playwright_cache_dir() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env not in ("0", ""):
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _chromium_installed() -> bool:
    cache = _playwright_cache_dir()
    if not cache.exists():
        return False
    return any(p.name.startswith("chromium") for p in cache.iterdir())


def ensure_browser_installed(log=None) -> None:
    """Install Playwright Chromium on first use. Idempotent."""
    from epro.paths import app_root

    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(app_root() / "ms-playwright")
    if _chromium_installed():
        return
    if log:
        log("Setting up browser (one-time download, ~120 MB)…")
    Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        from playwright._impl._driver import compute_driver_executable

        driver = compute_driver_executable()
        if isinstance(driver, (tuple, list)):
            cmd = [str(part) for part in driver] + ["install", "chromium"]
        else:
            cmd = [str(driver), "install", "chromium"]
    else:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as e:
        raise RuntimeError(
            "Could not install Playwright Chromium. Re-download the release zip."
        ) from e
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError("Playwright Chromium install failed:\n" + "\n".join(tail))
    if log:
        log("Browser setup complete.")


async def init_browser():
    """Lazily start Playwright and launch headless Chromium."""
    global _pw, _browser
    if _browser is not None:
        return _browser
    async with _lock:
        if _browser is None:
            from playwright.async_api import async_playwright

            _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
    return _browser


async def shutdown_browser() -> None:
    """Close Chromium. Safe to call from a fresh event loop after a killed run."""
    global _pw, _browser
    browser, pw = _browser, _pw
    _browser = None
    _pw = None
    if browser is not None:
        try:
            await asyncio.wait_for(browser.close(), timeout=8)
        except Exception:
            pass
    if pw is not None:
        try:
            await asyncio.wait_for(pw.stop(), timeout=8)
        except Exception:
            pass


async def restart_browser() -> None:
    """Drop the current Chromium process and launch a new one."""
    await shutdown_browser()
    await init_browser()


async def new_context(block_heavy: bool = True):
    """Create an isolated context + page. Caller must close the context."""
    from playwright.async_api import BrowserContext, Page

    browser = await init_browser()
    context = await browser.new_context(
        user_agent=_BROWSER_USER_AGENT,
        viewport={"width": 1366, "height": 900},
        accept_downloads=True,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    if block_heavy:
        async def _route(route):
            if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()
        await context.route("**/*", _route)
    page = await context.new_page()
    context.set_default_timeout(45_000)
    context.set_default_navigation_timeout(45_000)
    return context, page


def pick_field(fields: dict, candidates: list[str]) -> str:
    """First non-empty value whose label exactly matches a candidate (case-insensitive)."""
    lower = {
        str(k).strip().lower(): str(v or "").strip()
        for k, v in fields.items()
        if str(k).strip().lower() not in _IGNORE_LABELS
    }
    for c in candidates:
        val = lower.get(c.strip().lower())
        if val:
            return val
    return ""


def contact_from_fields(fields: dict, labels: dict | None = None) -> tuple[str, Contact]:
    """Map a labelled-field dict onto vendor name + Contact using state labels."""
    labels = labels or DEFAULT_LABELS
    vendor = pick_field(fields, labels.get("vendor") or DEFAULT_LABELS["vendor"])
    contact = Contact(
        name=pick_field(fields, labels.get("name") or DEFAULT_LABELS["name"]),
        phone=pick_field(fields, labels.get("phone") or DEFAULT_LABELS["phone"]),
        email=pick_field(fields, labels.get("email") or DEFAULT_LABELS["email"]),
        source="contract",
    )
    return vendor, contact


async def scrape_labelled_fields(page, timeout: int = 30) -> dict:
    """Return {label: value} from the current page. Retries once if empty."""
    try:
        await page.wait_for_load_state("load", timeout=timeout * 1000)
    except Exception:
        pass
    fields = await page.evaluate(_SCRAPE_LABELLED_JS)
    if not fields:
        await page.wait_for_timeout(1000)
        fields = await page.evaluate(_SCRAPE_LABELLED_JS)
    return fields or {}


async def goto(page, url: str, timeout: int = 30) -> tuple[int, str]:
    """Navigate and return (http_status, error). Status is 0 if unknown."""
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        return (response.status if response else 0), ""
    except Exception as e:
        msg = str(e).split("Call log:")[0].strip().replace("\n", " ")
        return 0, msg


async def scrape_contract_contact(page, po_url: str, state: dict, timeout: int = 30, dest_dir: Path | None = None) -> tuple[str, Contact, list, str, str]:
    """Load the PO summary, download Agency Attachments, then vendor contact.

    Vendor name/phone/email come from the Vendor: hyperlink profile — never
    from Contact Instructions / Purchaser (government buyer).

    Returns (vendor_name, contact, attachment_files, error, attachment_error).
    """
    labels = state.get("labels") or DEFAULT_LABELS
    mode = (state.get("contact_mode") or "vendor_profile").lower()
    status, nav_err = await goto(page, po_url, timeout=timeout)
    empty = Contact(source="contract")
    if nav_err:
        return "", empty, [], nav_err, ""
    if status in (404, 410):
        return "", empty, [], f"PO page HTTP {status}", ""
    if status and status >= 400:
        return "", empty, [], f"PO page HTTP {status}", ""

    try:
        await page.wait_for_load_state("load", timeout=timeout * 1000)
    except Exception:
        pass

    vendor_link = await _read_vendor_link(page)
    vendor = _vendor_name_from_link(vendor_link)
    vendor_id = (vendor_link or {}).get("vendorId") or ""
    if not vendor_id and vendor_link:
        vendor_id = _vendor_id_from_text(vendor_link.get("text") or "")
    agency_links = await list_agency_attachment_links(page)
    files: list = []
    att_err = ""
    if dest_dir is not None:
        from epro.attachments import collect_attachments

        files, att_err = await collect_attachments(
            page, "", dest_dir=dest_dir, timeout=timeout, links=agency_links
        )

    fields = await scrape_labelled_fields(page, timeout=timeout)
    if not vendor:
        vendor, _ = contact_from_fields(fields, labels)
        vendor = _clean_vendor_name(vendor)

    contact = Contact(source="contract")
    if mode == "vendor_profile":
        profile_href = (vendor_link or {}).get("href") or ""
        profile_fields, profile_err = await _scrape_vendor_profile(
            page,
            po_url,
            timeout,
            vendor_id=vendor_id or None,
            profile_href=profile_href,
        )
        if profile_fields:
            p_vendor, p_contact = contact_from_fields(profile_fields, labels)
            vendor = _clean_vendor_name(p_vendor) or vendor
            contact = p_contact
        elif profile_err:
            return vendor, contact, files, profile_err, att_err
    else:
        _, contact = contact_from_fields(fields, labels)

    return vendor, contact, files, "", att_err


async def _read_vendor_link(page) -> dict | None:
    try:
        return await page.evaluate(_VENDOR_LINK_JS)
    except Exception:
        return None


async def list_agency_attachment_links(page) -> list[dict]:
    """File links under Header Information → Agency Attachments on the PO page."""
    try:
        raw = await page.evaluate(_AGENCY_ATTACHMENTS_JS) or []
    except Exception:
        return []
    files: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        href = (item.get("href") or "").strip()
        if not href or href in seen:
            continue
        low = href.lower()
        if low.startswith(("mailto:", "tel:", "#")):
            continue
        if low.startswith("javascript:") and "downloadfile" not in low:
            continue
        seen.add(href)
        filename = _filename_from_link(href, item.get("text") or "", item.get("title") or "")
        files.append({"href": href, "text": item.get("text") or "", "filename": filename})
    return files


def _vendor_name_from_link(link: dict | None) -> str:
    if not link:
        return ""
    return _clean_vendor_name(link.get("text") or "")


def _clean_vendor_name(raw: str) -> str:
    text = (raw or "").strip()
    match = _VENDOR_ID_PREFIX.match(text)
    if match:
        return match.group(2).strip()
    return text


def _vendor_id_from_text(raw: str) -> str:
    match = _VENDOR_ID_PREFIX.match((raw or "").strip())
    return match.group(1) if match else ""


async def _scrape_vendor_profile(
    page,
    procurement_url: str,
    timeout: int,
    vendor_id: str | None = None,
    profile_href: str = "",
) -> tuple[dict, str]:
    """Follow the Vendor: hyperlink / viewExternalVendorProfile pop-up."""
    if profile_href and "vendorProfileOrgInfo" in profile_href and not profile_href.lower().startswith("javascript:"):
        profile_url = profile_href
    else:
        if not vendor_id:
            html = await page.content()
            match = re.search(r"viewExternalVendorProfile\('([^']+)'\)", html)
            if not match:
                return {}, "No Vendor hyperlink on the PO page"
            vendor_id = match.group(1)
        parsed = urlparse(procurement_url)
        ctx = parsed.path.split("/external/")[0] if "/external/" in parsed.path else "/bso"
        ext = "sda" if ".sda" in procurement_url else "sdo"
        profile_url = (
            f"{parsed.scheme}://{parsed.netloc}{ctx}"
            f"/external/vendor/vendorProfileOrgInfo.{ext}?external=true&vendorId={vendor_id}"
        )
    status, nav_err = await goto(page, profile_url, timeout=timeout)
    if nav_err:
        return {}, nav_err
    if status and status >= 400:
        return {}, f"Vendor profile HTTP {status}"
    fields = await scrape_labelled_fields(page, timeout=timeout)
    return fields, "" if fields else "Vendor profile had no labelled fields"


async def list_attachment_links(page, attachments_url: str, timeout: int = 30) -> tuple[list[dict], str]:
    """Return [{href, text, filename}] for downloadable files on the Attachments tab."""
    status, nav_err = await goto(page, attachments_url, timeout=timeout)
    if nav_err:
        return [], nav_err
    if status in (404, 410):
        return [], f"Attachments tab HTTP {status}"
    if status and status >= 400:
        return [], f"Attachments tab HTTP {status}"
    try:
        await page.wait_for_load_state("load", timeout=timeout * 1000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    raw_links = await page.evaluate(_LIST_LINKS_JS)
    # Frames (some ePro deployments nest the file list)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            extra = await frame.evaluate(_LIST_LINKS_JS)
            raw_links.extend(extra)
        except Exception:
            continue

    seen: set[str] = set()
    files: list[dict] = []
    for item in raw_links:
        href = (item.get("href") or "").strip()
        if not href or href in seen:
            continue
        if _SKIP_HREF.search(href):
            continue
        if not _DOWNLOAD_HINT.search(href) and not _DOWNLOAD_HINT.search(item.get("text") or ""):
            continue
        seen.add(href)
        filename = _filename_from_link(href, item.get("text") or "", item.get("title") or "")
        files.append({"href": href, "text": item.get("text") or "", "filename": filename})
    return files, ""


async def download_with_context(page, url: str, dest: Path, timeout: int = 60) -> tuple[bool, str, Path]:
    """Download ``url`` using the page's cookie jar; fall back to requests."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = await page.request.get(url, timeout=timeout * 1000)
        if response.ok:
            body = await response.body()
            if _looks_like_html(body):
                return False, "Download returned HTML (likely a login or error page)", dest
            dest.write_bytes(body)
            dest = _maybe_rename_from_header(dest, response.headers.get("content-disposition", ""))
            return True, "", dest
        status = response.status
    except Exception as e:
        status = 0
        last_err = str(e)
    else:
        last_err = f"HTTP {status}"

    try:
        import requests

        resp = requests.get(url, headers=_HEADERS, timeout=timeout, stream=True, verify=True)
        resp.raise_for_status()
        content = resp.content
        if _looks_like_html(content):
            return False, f"requests fallback returned HTML ({last_err})", dest
        dest.write_bytes(content)
        dest = _maybe_rename_from_header(dest, resp.headers.get("content-disposition", ""))
        return True, "", dest
    except Exception as e:
        return False, f"{last_err}; requests fallback: {e}", dest


_DOWNLOAD_FILE_RE = re.compile(r"downloadFile\('(\d+)'\)", re.IGNORECASE)


async def click_download_file(page, href: str, dest: Path, timeout: int = 60) -> tuple[bool, str, Path]:
    """Download a Periscope Agency Attachment via javascript:downloadFile('id')."""
    match = _DOWNLOAD_FILE_RE.search(href or "")
    if not match:
        return False, "Not a downloadFile() link", dest
    file_id = match.group(1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with page.expect_download(timeout=timeout * 1000) as pending:
            await page.evaluate(f"downloadFile('{file_id}')")
        download = await pending.value
        suggested = (download.suggested_filename or "").strip()
        if suggested:
            dest = dest.with_name(_safe_filename(suggested, dest.parent))
        await download.save_as(str(dest))
        return True, "", dest
    except Exception as e:
        return False, str(e).split("Call log:")[0].strip(), dest


def _safe_filename(name: str, dest_dir: Path) -> str:
    safe = "".join(c if c.isalnum() or c in " .-_()" else "_" for c in name).strip() or "attachment"
    if not (dest_dir / safe).exists():
        return safe
    stem, suffix = Path(safe).stem, Path(safe).suffix
    i = 1
    while (dest_dir / f"{stem}_{i}{suffix}").exists():
        i += 1
    return f"{stem}_{i}{suffix}"


def _looks_like_html(body: bytes) -> bool:
    head = body[:200].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _maybe_rename_from_header(dest: Path, disposition: str) -> Path:
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition, re.IGNORECASE)
    if not match:
        return dest
    name = Path(unquote(match.group(1))).name
    if not name or name == dest.name:
        return dest
    target = dest.with_name(name)
    try:
        dest.rename(target)
        return target
    except OSError:
        return dest


def _filename_from_link(href: str, text: str, title: str) -> str:
    for candidate in (text, title):
        name = Path(candidate.strip()).name
        if name and "." in name and _FILE_EXT.search(name):
            return name
    parsed = urlparse(href)
    path_name = unquote(Path(parsed.path).name)
    if path_name and "." in path_name:
        return path_name
    # Query-string fileName=...
    qs = unquote(parsed.query)
    m = re.search(r"(?:fileName|filename|docName|name)=([^&]+)", qs, re.IGNORECASE)
    if m:
        name = Path(m.group(1)).name
        if name:
            return name
    slug = re.sub(r"[^\w.\-]+", "_", (text or "attachment").strip())[:80] or "attachment"
    return slug
