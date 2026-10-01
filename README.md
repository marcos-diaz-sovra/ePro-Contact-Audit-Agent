# ePro Contact Audit Agent

Desktop app that scrapes public Periscope/ePro purchase-order pages, reads Agency Attachments, and lists supplier contacts that are **not** already on the vendor profile.

## Requirements

- Windows 10/11
- [Python 3.11+](https://www.python.org/downloads/) (check **Add python.exe to PATH** during install)

## Quick start

1. Unzip the release.
2. Double-click `run.bat`. First launch installs packages and Chromium (~120 MB).
3. Choose a CSV or Excel file of contract IDs (column `contract_id`, `Contract ID`, `PO Number`, or similar). Full public PO URLs also work.
4. Leave **ePro site** as `https://oregonbuys.gov` unless you are testing another ePro origin.
5. Click **Run**. Use **Stop** / **Resume** if a long batch stalls.

Outputs land in `outputs\`:

- `contact_audit_YYYYMMDD_HHMMSS.xlsx` — All_Contracts + Discrepancies
- `contact_audit_in_progress.xlsx` — refreshed while a run is still going

Hybrid / LLM extract modes need an Anthropic API key. Regex mode does not.

## CLI

```bat
.venv\Scripts\python.exe main.py --input contracts.xlsx --extract regex
.venv\Scripts\python.exe main.py --input contracts.xlsx --resume
```
