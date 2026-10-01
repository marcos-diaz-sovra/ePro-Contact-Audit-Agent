# ePro Contact Audit Agent

Desktop app that scrapes public Periscope/ePro purchase-order pages, reads Agency Attachments, and lists supplier contacts that are **not** already on the vendor profile.

## Install (Windows)

1. Unzip the release.
2. Double-click **`run.bat`**.

That is the whole setup. The first launch downloads a local Python runtime, the app packages, and Chromium. You do **not** need to install Python or add anything to PATH. Later launches skip setup and open the app.

If Windows shows a SmartScreen prompt, choose **More info** → **Run anyway** (the script only installs into this folder).

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
