"""Persist batch progress so a run can stop and resume from the same spreadsheet."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from epro.models import ContractAudit


def progress_dir(output_dir: Path, input_path: Path) -> Path:
    key = hashlib.sha1(str(input_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(output_dir) / ".progress" / key


def meta_path(output_dir: Path, input_path: Path) -> Path:
    return progress_dir(output_dir, input_path) / "meta.json"


def audits_path(output_dir: Path, input_path: Path) -> Path:
    return progress_dir(output_dir, input_path) / "audits.jsonl"


def row_key(index: int, contract_id: str) -> str:
    return f"{index}:{(contract_id or '').strip()}"


def load_meta(output_dir: Path, input_path: Path) -> dict | None:
    path = meta_path(output_dir, input_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def has_progress(output_dir: Path, input_path: Path) -> bool:
    meta = load_meta(output_dir, input_path)
    if not meta:
        return False
    return int(meta.get("completed") or 0) > 0 and audits_path(output_dir, input_path).is_file()


def completed_keys(output_dir: Path, input_path: Path) -> set[str]:
    meta = load_meta(output_dir, input_path) or {}
    keys = meta.get("completed_keys") or []
    return {str(k) for k in keys}


def load_audits(output_dir: Path, input_path: Path) -> list[ContractAudit]:
    path = audits_path(output_dir, input_path)
    if not path.is_file():
        return []
    audits: list[ContractAudit] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                audits.append(ContractAudit.from_log_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return audits


def append_audit(
    output_dir: Path,
    input_path: Path,
    *,
    index: int,
    total: int,
    audit: ContractAudit,
    extract_mode: str = "",
    site_url: str = "",
) -> None:
    folder = progress_dir(output_dir, input_path)
    folder.mkdir(parents=True, exist_ok=True)
    with audits_path(output_dir, input_path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit.as_log_dict(), ensure_ascii=False) + "\n")

    meta = load_meta(output_dir, input_path) or {}
    keys = [str(k) for k in meta.get("completed_keys") or []]
    key = row_key(index, audit.contract_id)
    if key not in keys:
        keys.append(key)
    meta.update({
        "input_path": str(input_path.resolve()),
        "total": total,
        "completed": len(keys),
        "completed_keys": keys,
        "extract_mode": extract_mode or meta.get("extract_mode") or "",
        "site_url": site_url or meta.get("site_url") or "",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_contract_id": audit.contract_id,
    })
    meta_path(output_dir, input_path).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def clear_progress(output_dir: Path, input_path: Path) -> None:
    folder = progress_dir(output_dir, input_path)
    for path in (audits_path(output_dir, input_path), meta_path(output_dir, input_path)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass
