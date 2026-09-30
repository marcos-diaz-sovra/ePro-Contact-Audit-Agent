"""List, download, and extract text from ePro contract attachments."""

from __future__ import annotations

import tempfile
from pathlib import Path

from epro.browser import click_download_file, download_with_context
from epro.models import AttachmentFile

_TEXT_EXTS = {".pdf", ".docx", ".doc", ".txt", ".rtf"}
_MAX_PDF_PAGES = 20
_MAX_CHARS = 10_000


async def collect_attachments(
    page,
    attachments_url: str,
    dest_dir: Path | None = None,
    timeout: int = 30,
    links: list[dict] | None = None,
) -> tuple[list[AttachmentFile], str]:
    """Download Agency Attachments from the PO page and extract text."""
    err = ""
    if not links:
        return [], "No Agency Attachments listed on the contract page"

    if dest_dir is None:
        dest_dir = Path(tempfile.mkdtemp(prefix="epro_attach_"))
    else:
        dest_dir.mkdir(parents=True, exist_ok=True)

    files: list[AttachmentFile] = []
    for i, link in enumerate(links):
        href = link["href"]
        filename = link.get("filename") or link.get("text") or f"attachment_{i}"
        dest = dest_dir / _unique_name(dest_dir, filename)
        if "downloadfile" in href.lower():
            ok, dl_err, dest = await click_download_file(page, href, dest, timeout=max(timeout, 60))
        else:
            ok, dl_err, dest = await download_with_context(page, href, dest)
        item = AttachmentFile(filename=dest.name if dest.exists() else filename, url=href)
        if not ok:
            item.notes.append(dl_err or "download failed")
            files.append(item)
            continue
        item.path = str(dest)
        item.filename = dest.name
        text, notes = extract_text(dest)
        item.text = text
        item.notes.extend(notes)
        files.append(item)
    return files, "" if files or not err else err


def extract_text(file_path: Path) -> tuple[str, list[str]]:
    """Extract text from a PDF or DOCX. Flags OCR_NEEDED when a PDF has no text."""
    suffix = file_path.suffix.lower()
    notes: list[str] = []
    if suffix == ".pdf":
        text, empty_pages = _extract_pdf(file_path)
        if not text.strip() and empty_pages:
            notes.append("OCR_NEEDED")
        return text, notes
    if suffix == ".docx":
        return _extract_docx(file_path), notes
    if suffix == ".doc":
        notes.append("unsupported .doc (convert to .docx)")
        return "", notes
    if suffix in {".txt", ".rtf"}:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")[:_MAX_CHARS], notes
        except Exception as e:
            notes.append(str(e))
            return "", notes
    if suffix not in _TEXT_EXTS:
        notes.append(f"skipped file type {suffix or '(none)'}")
        return "", notes
    return "", notes


def combined_text(files: list[AttachmentFile]) -> str:
    """Join extracted attachment text, tagged by filename, for contact extractors."""
    parts = []
    for f in files:
        if f.text.strip():
            parts.append(f"--- {f.filename} ---\n{f.text}")
    return "\n\n".join(parts)


def _extract_pdf(path: Path) -> tuple[str, bool]:
    try:
        import pdfplumber
    except ImportError:
        return "", True
    text_parts: list[str] = []
    empty_pages = False
    try:
        with pdfplumber.open(path) as pdf:
            pages = pdf.pages[:_MAX_PDF_PAGES]
            if not pages:
                return "", True
            for page in pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(page_text)
                else:
                    empty_pages = True
    except Exception:
        return "", True
    text = "\n".join(text_parts)[:_MAX_CHARS]
    return text, empty_pages and not text.strip()


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return text[:_MAX_CHARS]
    except Exception:
        return ""


def _unique_name(dest_dir: Path, filename: str) -> str:
    safe = "".join(c if c.isalnum() or c in " .-_()" else "_" for c in filename).strip() or "attachment"
    candidate = dest_dir / safe
    if not candidate.exists():
        return safe
    stem, suffix = Path(safe).stem, Path(safe).suffix
    i = 1
    while True:
        name = f"{stem}_{i}{suffix}"
        if not (dest_dir / name).exists():
            return name
        i += 1
