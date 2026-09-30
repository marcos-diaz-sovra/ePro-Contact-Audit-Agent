"""Batch orchestrator: one contract at a time, then write reports."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Callable

import pandas as pd

from epro.attachments import combined_text
from epro.browser import (
    ensure_browser_installed,
    new_context,
    restart_browser,
    scrape_contract_contact,
    shutdown_browser,
)
from epro.checkpoint import append_audit, clear_progress, completed_keys, load_audits, row_key
from epro.contacts import extract_contacts
from epro.match import ERROR, compare
from epro.models import Contact, ContractAudit
from epro.report import write_outputs, write_workbook
from epro.urls import build_urls, parse_contract_url
from epro.config import default_site, resolve_state

LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

RECYCLE_EVERY = 40
PARTIAL_XLSX = "contact_audit_in_progress.xlsx"


class BatchStopped(Exception):
    """Raised when the GUI/CLI asks the batch to stop between contracts."""

ID_COLUMNS = [
    "contract_id",
    "contract id",
    "docid",
    "doc_id",
    "po number",
    "po_number",
    "po",
    "contract number",
    "contract #",
    "contract#",
    "url",
    "po url",
    "po_url",
    "procurement url",
    "contract url",
]


def read_input(path: Path) -> tuple[pd.DataFrame, str]:
    """Load CSV/Excel and return (dataframe, id_column_name)."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    elif suffix in {".csv", ".tsv"}:
        sep = "\t" if suffix == ".tsv" else ","
        df = pd.read_csv(path, sep=sep)
    else:
        raise ValueError(f"Unsupported input type: {path.suffix} (use .xlsx, .xls, or .csv)")
    if df.empty:
        raise ValueError(f"Input file has no rows: {path}")
    id_col = _find_id_column(df)
    return df, id_col


def _row_url(row: dict, id_col: str | None = None) -> str:
    """First http(s) value in the row, preferring the ID column."""
    if id_col:
        raw = str(row.get(id_col) or "").strip()
        if raw.startswith("http"):
            return raw
    for value in row.values():
        text = str(value or "").strip()
        if text.startswith("http"):
            return text
    return ""


def _urls_for_row(state: dict, contract_id: str, row: dict, id_col: str | None):
    parsed = parse_contract_url(_row_url(row, id_col) or contract_id)
    if parsed:
        return parsed
    return build_urls(state, contract_id)


def _site_from_rows(rows: list[dict], id_col: str) -> dict:
    for row in rows:
        url = _row_url(row, id_col)
        if url:
            return resolve_state(base_url=url)
    return default_site()


def extra_columns(df: pd.DataFrame, id_col: str) -> list[str]:
    return [c for c in df.columns if c != id_col]


async def run_batch(
    *,
    state: dict | None,
    input_path: Path,
    output_dir: Path,
    extract_mode: str = "hybrid",
    limit: int | None = None,
    delay: float = 1.5,
    api_key: str | None = None,
    timeout: int = 30,
    log: LogFn | None = None,
    should_stop: StopFn | None = None,
    resume: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Process each contract ID and write Excel + JSONL. Returns output paths."""
    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _stopped() -> bool:
        return bool(should_stop and should_stop())

    df, id_col = read_input(input_path)
    rows = df.to_dict(orient="records")
    if limit is not None:
        rows = rows[: max(0, int(limit))]

    site = state or _site_from_rows(rows, id_col)
    site_url = str(site.get("base_url") or "")
    contract_timeout = max(90, int(timeout) * 4)
    _log(f"Loaded {len(rows)} contract(s) from {input_path.name}")
    _log(f"ePro site: {site_url} (.{site.get('ext')})")
    _log(f"Extract mode: {extract_mode}")
    ensure_browser_installed(log=_log)

    done = completed_keys(output_dir, input_path) if resume else set()
    audits: list[ContractAudit] = load_audits(output_dir, input_path) if resume else []
    if resume and audits:
        _log(f"Resuming: {len(audits)} already saved, {max(0, len(rows) - len(done))} remaining")
    elif not resume:
        clear_progress(output_dir, input_path)

    partial_xlsx = output_dir / PARTIAL_XLSX
    processed_since_recycle = 0
    stopped = False

    try:
        await shutdown_browser()
        for i, row in enumerate(rows, start=1):
            if _stopped():
                stopped = True
                _log("Stop requested. Progress saved — click Resume to continue.")
                break

            cid = str(row.get(id_col) or "").strip()
            extra = {k: ("" if pd.isna(v) else v) for k, v in row.items() if k != id_col}
            key = row_key(i, cid if cid.lower() != "nan" else "")
            if progress:
                progress(i, len(rows), cid)
            if key in done:
                continue

            _log(f"[{i}/{len(rows)}] {cid or '(missing id)'}")
            if not cid or cid.lower() == "nan":
                audit = ContractAudit(contract_id="", extras=extra, match_status=ERROR, error="Missing contract ID")
                audit.notes.append("Missing contract ID")
            else:
                try:
                    audit = await _process_contract_guarded(
                        state=site,
                        contract_id=cid,
                        extras=extra,
                        extract_mode=extract_mode,
                        api_key=api_key,
                        timeout=timeout,
                        contract_timeout=contract_timeout,
                        log=_log,
                        row=row,
                        id_col=id_col,
                        should_stop=_stopped,
                    )
                except BatchStopped:
                    stopped = True
                    _log("Stop requested during scrape. That contract will retry on Resume.")
                    break
                except Exception as e:
                    audit = ContractAudit(
                        contract_id=cid,
                        extras=extra,
                        match_status=ERROR,
                        error=str(e),
                    )
                    audit.notes.append(str(e))
                    _log(f"  ERROR: {e}")
                    try:
                        await restart_browser()
                        processed_since_recycle = 0
                    except Exception:
                        pass

            compare(audit)
            _log(f"  {audit.match_status}" + (f" - {audit.notes[-1]}" if audit.notes else ""))
            for c in audit.new_contacts:
                _log(f"    NEW: {c.name or '-'} | {c.phone or '-'} | {c.email or '-'} ({c.source})")
            audits.append(audit)
            append_audit(
                output_dir,
                input_path,
                index=i,
                total=len(rows),
                audit=audit,
                extract_mode=extract_mode,
                site_url=site_url,
            )
            done.add(key)
            processed_since_recycle += 1
            if len(audits) % 5 == 0 or i == len(rows):
                try:
                    write_workbook(audits, partial_xlsx)
                except Exception as e:
                    _log(f"  (could not refresh in-progress workbook: {e})")

            if processed_since_recycle >= RECYCLE_EVERY:
                _log("  Recycling browser to keep the long run stable")
                try:
                    await restart_browser()
                except Exception as e:
                    _log(f"  Browser recycle failed: {e}")
                processed_since_recycle = 0

            if i < len(rows) and delay > 0:
                remaining = float(delay)
                while remaining > 0 and not _stopped():
                    step = min(0.2, remaining)
                    await asyncio.sleep(step)
                    remaining -= step
    finally:
        await shutdown_browser()

    if not audits:
        if stopped:
            _log("Stopped before any contract finished.")
            return {
                "xlsx": output_dir / PARTIAL_XLSX,
                "log": output_dir / PARTIAL_XLSX,
                "stopped": True,
                "completed": 0,
                "total": len(rows),
            }
        raise ValueError("No contracts were processed")

    if stopped:
        try:
            write_workbook(audits, partial_xlsx)
        except Exception:
            pass
        paths = write_outputs(audits, output_dir, stem=None)
        _log(f"Saved {len(audits)}/{len(rows)} contracts. Workbook: {paths['xlsx']}")
        _log("Click Resume to continue from the next unfinished contract.")
        paths["stopped"] = True
        paths["completed"] = len(audits)
        paths["total"] = len(rows)
        return paths

    paths = write_outputs(audits, output_dir)
    try:
        partial_xlsx.unlink(missing_ok=True)
    except OSError:
        pass
    clear_progress(output_dir, input_path)
    _log(f"Wrote {paths['xlsx']}")
    _log(f"Wrote {paths['log']}")
    paths["stopped"] = False
    paths["completed"] = len(audits)
    paths["total"] = len(rows)
    return paths


async def _process_contract_guarded(
    *,
    contract_timeout: int,
    should_stop: StopFn,
    **kwargs,
) -> ContractAudit:
    """Run one contract with a hard timeout and cooperative cancel."""
    task = asyncio.create_task(process_contract(**kwargs))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + contract_timeout
    while True:
        if should_stop():
            await _cancel_task(task)
            raise BatchStopped()
        remaining = deadline - loop.time()
        if remaining <= 0:
            await _cancel_task(task)
            raise TimeoutError(f"Timed out after {contract_timeout}s")
        done, _ = await asyncio.wait({task}, timeout=min(1.0, remaining))
        if task in done:
            exc = task.exception() if not task.cancelled() else None
            if task.cancelled():
                raise BatchStopped()
            if exc:
                raise exc
            return task.result()


async def _cancel_task(task: asyncio.Task) -> None:
    if task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=8)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass


async def process_contract(
    *,
    state: dict,
    contract_id: str,
    extras: dict | None = None,
    extract_mode: str = "hybrid",
    api_key: str | None = None,
    timeout: int = 30,
    log: LogFn | None = None,
    row: dict | None = None,
    id_col: str | None = None,
) -> ContractAudit:
    """Scrape one contract page + attachments and extract contacts (no compare)."""
    urls = _urls_for_row(state, contract_id, row or {}, id_col)
    contract_id = urls.contract_id
    site = dict(state)
    site["base_url"] = urls.base_url
    audit = ContractAudit(
        contract_id=contract_id,
        extras=extras or {},
        po_url=urls.po_url,
        attachments_url=urls.attachments_url,
    )
    context, page = await new_context(block_heavy=False)
    dest = Path(tempfile.mkdtemp(prefix=f"epro_{_safe(contract_id)}_"))
    if log:
        log(f"  {urls.po_url}")
    try:
        vendor, contact, files, err, att_err = await scrape_contract_contact(
            page, urls.po_url, site, timeout=timeout, dest_dir=dest
        )
        audit.vendor_name = vendor
        audit.contract_contact = contact
        if err:
            audit.notes.append(err)
            if contact.is_empty():
                audit.error = err
        if att_err:
            audit.notes.append(att_err)
            if not files and not audit.error:
                audit.error = att_err

        audit.attachment_files = [f.filename for f in files if f.filename]
        for f in files:
            for n in f.notes:
                if n.startswith("skipped file type"):
                    continue
                audit.notes.append(f"{f.filename}: {n}")

        # Per-file extract so source filenames stick; fall back to combined text.
        contacts: list[Contact] = []
        for f in files:
            if not f.text.strip():
                continue
            contacts.extend(
                extract_contacts(f.text, mode=extract_mode, source=f.filename, api_key=api_key)
            )
        if not contacts:
            blob = combined_text(files)
            if blob:
                contacts = extract_contacts(blob, mode=extract_mode, source="attachments", api_key=api_key)
        audit.attachment_contacts = _dedupe_contacts(contacts)
        if log:
            log(
                f"  contract contact: {contact.name or '-'} | {contact.phone or '-'} | {contact.email or '-'}"
            )
            names = ", ".join(f.filename for f in files if f.filename) or "none"
            log(f"  agency attachments: {names}")
            log(f"  extracted contacts: {len(audit.attachment_contacts)}")
        return audit
    finally:
        try:
            await context.close()
        except Exception:
            pass


def _find_id_column(df: pd.DataFrame) -> str:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for candidate in ID_COLUMNS:
        if candidate in lower:
            return lower[candidate]
    # Single-column sheet: treat it as IDs.
    if len(df.columns) == 1:
        return df.columns[0]
    raise ValueError(
        "Could not find a contract ID column. Use one of: "
        + ", ".join(ID_COLUMNS)
        + f". Columns present: {list(df.columns)}"
    )


def _dedupe_contacts(contacts: list[Contact]) -> list[Contact]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Contact] = []
    for c in contacts:
        key = (c.name.strip().lower(), c.phone, c.email.strip().lower())
        if key in seen or c.is_empty():
            continue
        seen.add(key)
        out.append(c)
    return out


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:40]
