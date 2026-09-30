"""Write the enriched workbook, discrepancy sheet, and JSONL run log."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from epro.models import ContractAudit

ALL_COLUMNS = [
    "Contract ID",
    "Vendor",
    "Contract Phone",
    "Contract Email",
    "Attachment Files",
    "New Contact Count",
    "Match Status",
    "Notes",
    "PO URL",
]

DISC_COLUMNS = [
    "Contract ID",
    "Vendor",
    "Source File",
    "New Contact Name",
    "New Contact Phone",
    "New Contact Email",
    "PO URL",
]


def write_outputs(
    audits: list[ContractAudit],
    output_dir: Path,
    extra_columns: list[str] | None = None,
    *,
    stem: str | None = None,
    write_log: bool = True,
) -> dict[str, Path]:
    """Write Excel (All_Contracts + Discrepancies) and optional JSONL run log."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = stem or datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = output_dir / f"contact_audit_{stamp}.xlsx"
    log_path = output_dir / f"run_log_{stamp}.jsonl"
    write_workbook(audits, xlsx_path)
    if write_log:
        with log_path.open("w", encoding="utf-8") as fh:
            for audit in audits:
                fh.write(json.dumps(audit.as_log_dict(), ensure_ascii=False) + "\n")
    return {"xlsx": xlsx_path, "log": log_path}


def write_workbook(audits: list[ContractAudit], xlsx_path: Path) -> Path:
    """Write (or overwrite) the All_Contracts + Discrepancies workbook."""
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    all_df = pd.DataFrame([_all_row(a) for a in audits], columns=ALL_COLUMNS)
    disc_df = pd.DataFrame(_discrepancy_rows(audits), columns=DISC_COLUMNS)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        all_df.to_excel(writer, sheet_name="All_Contracts", index=False)
        disc_df.to_excel(writer, sheet_name="Discrepancies", index=False)
    return xlsx_path


def _all_row(audit: ContractAudit) -> dict:
    return {
        "Contract ID": audit.contract_id,
        "Vendor": audit.vendor_name,
        "Contract Phone": audit.contract_contact.phone,
        "Contract Email": audit.contract_contact.email,
        "Attachment Files": "; ".join(audit.attachment_files),
        "New Contact Count": len(audit.new_contacts),
        "Match Status": audit.match_status,
        "Notes": "; ".join(audit.notes),
        "PO URL": audit.po_url,
    }


def _discrepancy_rows(audits: list[ContractAudit]) -> list[dict]:
    rows: list[dict] = []
    for audit in audits:
        for contact in audit.new_contacts:
            rows.append({
                "Contract ID": audit.contract_id,
                "Vendor": audit.vendor_name,
                "Source File": contact.source,
                "New Contact Name": contact.name,
                "New Contact Phone": contact.phone,
                "New Contact Email": contact.email,
                "PO URL": audit.po_url,
            })
    return rows
