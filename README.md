# ePro Contact Audit Agent

Desktop app that scrapes public Periscope/ePro purchase-order pages, reads Agency Attachments, and lists supplier contacts that are **not** already on the vendor profile.

## Windows

Unzip **eProContactAudit** and double-click **`eProContactAudit.exe`**. Keep the whole folder together; the `.exe` will not run if you copy it out by itself. Chromium is already included. Results go to `outputs` next to the `.exe`.

## macOS

macOS does not run `.exe` files. Unzip **eProContactAudit-macos** and double-click **`eProContactAudit.app`**.

The first time macOS blocks an unsigned app: right-click **eProContactAudit.app**, choose **Open**, then **Open** again.

Downloads are opened from a read-only folder. The app then saves the browser and results here:

`~/Library/Application Support/ePro Contact Audit Agent/outputs`

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

## Updates

The packaged app checks GitHub on startup. When a newer release exists, an **Update** button appears. It downloads the build for this computer, restarts, and replaces the installed app. Saved audit progress in `outputs` is left in place.

Each release must include a zip whose name contains `windows` or `macos`.

Hybrid / LLM extract modes need an Anthropic API key. Regex mode does not.

## CLI (after the first GUI launch)

```bat
.venv\Scripts\python.exe main.py --input contracts.xlsx --extract regex
.venv\Scripts\python.exe main.py --input contracts.xlsx --resume
```
