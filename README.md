# ePro Contact Audit Agent

Desktop app that scrapes public Periscope/ePro purchase-order pages, reads Agency Attachments, and lists supplier contacts that are **not** already on the vendor profile.

## Windows app

Unzip **eProContactAudit** and double-click **`eProContactAudit.exe`**. Keep the whole folder together; the `.exe` will not run if you copy it out by itself.

The first release zip already includes Chromium. Results go to `outputs` next to the `.exe`.

## Source install (developers)

1. Unzip the source release, or clone the repo.
2. Double-click **`run.bat`**.

That first launch downloads a local Python runtime, packages, and Chromium into the folder. You do **not** need to install Python or add anything to PATH. Later launches skip setup and open the app.

## Use

1. Choose a CSV or Excel file of contract IDs (`contract_id`, `Contract ID`, `PO Number`, or similar). Full public PO URLs also work.
2. Leave **ePro site** as `https://oregonbuys.gov` unless you are testing another origin.
3. Click **Run**. Use **Stop** / **Resume** if a long batch stalls.

Results are written to `outputs\`:

- `contact_audit_YYYYMMDD_HHMMSS.xlsx` — All_Contracts + Discrepancies
- `contact_audit_in_progress.xlsx` — refreshed while a run is still going

Hybrid / LLM extract modes need an Anthropic API key. Regex mode does not.

## CLI (after the first GUI launch)

```bat
.venv\Scripts\python.exe main.py --input contracts.xlsx --extract regex
.venv\Scripts\python.exe main.py --input contracts.xlsx --resume
```
