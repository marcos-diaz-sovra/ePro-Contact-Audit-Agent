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
                page_text = _page_text_without_strikethrough(page)
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
        chunks: list[str] = []
        for paragraph in doc.paragraphs:
            text = _docx_paragraph_text(paragraph)
            if text.strip():
                chunks.append(text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        text = _docx_paragraph_text(paragraph)
                        if text.strip():
                            chunks.append(text)
        return "\n".join(chunks)[:_MAX_CHARS]
    except Exception:
        return ""


def _docx_paragraph_text(paragraph) -> str:
    """Keep replacement text and drop runs that Word has struck through."""
    parts: list[str] = []
    for run in paragraph.runs:
        font = run.font
        if font.strike or font.double_strike:
            continue
        parts.append(run.text or "")
    return "".join(parts)


def _page_text_without_strikethrough(page) -> str:
    """Drop PDF characters covered by a strikethrough line or StrikeOut annotation."""
    boxes = _strike_boxes(page)
    if not boxes:
        return page.extract_text() or ""

    def keep(obj) -> bool:
        if obj.get("object_type") != "char":
            return True
        return not _char_struck(obj, boxes)

    try:
        return page.filter(keep).extract_text() or ""
    except Exception:
        return page.extract_text() or ""


def _strike_boxes(page) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    height = float(getattr(page, "height", 0) or 0)
    for annot in page.annots or []:
        subtype = str((annot.get("data") or {}).get("Subtype") or "")
        if "Strike" not in subtype:
            continue
        boxes.extend(_annot_strike_boxes(annot, height))

    chars = page.chars or []
    for obj in list(page.lines or []) + list(page.rects or []):
        x0, x1 = obj.get("x0"), obj.get("x1")
        top, bottom = obj.get("top"), obj.get("bottom")
        if None in (x0, x1, top, bottom):
            continue
        if abs(x1 - x0) < 8 or abs(bottom - top) > 2.5:
            continue
        mid = (float(top) + float(bottom)) / 2
        if not any(_line_crosses_char(float(x0), float(x1), mid, char) for char in chars):
            continue
        boxes.append((min(float(x0), float(x1)), max(float(x0), float(x1)), mid - 3.5, mid + 3.5))
    return boxes


def _annot_strike_boxes(annot: dict, page_height: float) -> list[tuple[float, float, float, float]]:
    """Use StrikeOut quad points so a padded annotation box does not cover the replacement."""
    quads = (annot.get("data") or {}).get("QuadPoints") or []
    try:
        nums = [float(n) for n in quads]
    except (TypeError, ValueError):
        nums = []
    boxes: list[tuple[float, float, float, float]] = []
    if page_height and len(nums) >= 8:
        for i in range(0, len(nums) - 7, 8):
            xs = nums[i:i + 8:2]
            ys = nums[i + 1:i + 8:2]
            tops = [page_height - y for y in ys]
            boxes.append((min(xs), max(xs), min(tops), max(tops)))
        return boxes
    return [(float(annot["x0"]), float(annot["x1"]), float(annot["top"]), float(annot["bottom"]))]


def _line_crosses_char(x0: float, x1: float, mid: float, char: dict) -> bool:
    height = abs(float(char["bottom"]) - float(char["top"])) or 1
    center_y = (float(char["top"]) + float(char["bottom"])) / 2
    if abs(mid - center_y) > height * 0.3:
        return False
    overlap = min(x1, float(char["x1"])) - max(x0, float(char["x0"]))
    return overlap >= max(1, (float(char["x1"]) - float(char["x0"])) * 0.5)


def _char_struck(char: dict, boxes: list[tuple[float, float, float, float]]) -> bool:
    center_x = (float(char["x0"]) + float(char["x1"])) / 2
    center_y = (float(char["top"]) + float(char["bottom"])) / 2
    for x0, x1, top, bottom in boxes:
        if min(x0, x1) - 0.4 <= center_x <= max(x0, x1) + 0.4 and min(top, bottom) - 0.4 <= center_y <= max(top, bottom) + 0.4:
            return True
    return False


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
