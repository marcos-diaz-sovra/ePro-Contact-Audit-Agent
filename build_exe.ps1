# Build dist\eProContactAudit\eProContactAudit.exe (onedir) plus bundled Chromium.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    throw "Missing .venv. Run run.bat once, or: py -3 -m venv .venv"
}

Write-Host "Installing PyInstaller..." -ForegroundColor Cyan
& $Py -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }

Write-Host "Building .exe..." -ForegroundColor Cyan
& $Py -m PyInstaller --noconfirm --clean eProContactAudit.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Dist = Join-Path $Root "dist\eProContactAudit"
if (-not (Test-Path (Join-Path $Dist "eProContactAudit.exe"))) {
    throw "Build finished but eProContactAudit.exe was not found in $Dist"
}

Write-Host "Bundling Chromium next to the .exe..." -ForegroundColor Cyan
$Browsers = Join-Path $Dist "ms-playwright"
$TempBrowsers = Join-Path $env:TEMP "epro-ms-playwright"
if (Test-Path $TempBrowsers) { Remove-Item $TempBrowsers -Recurse -Force }
New-Item -ItemType Directory -Force -Path $TempBrowsers | Out-Null
$env:PLAYWRIGHT_BROWSERS_PATH = $TempBrowsers
& $Py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }
if (Test-Path $Browsers) { Remove-Item $Browsers -Recurse -Force }
Copy-Item -Recurse $TempBrowsers $Browsers

$Readme = @"
ePro Contact Audit Agent
========================
Double-click eProContactAudit.exe.

Keep this whole folder together — do not move the .exe out by itself.
Outputs are written to an outputs folder next to the .exe.
"@
Set-Content -Path (Join-Path $Dist "README.txt") -Value $Readme -Encoding UTF8

Write-Host ""
Write-Host "Done: $Dist\eProContactAudit.exe" -ForegroundColor Green
