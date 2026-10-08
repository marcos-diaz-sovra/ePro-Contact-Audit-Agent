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
$BuiltExe = Join-Path $Dist "eProContactAudit.exe"
if (-not (Test-Path $BuiltExe)) {
    throw "Build finished but eProContactAudit.exe was not found in $Dist"
}
$AppExe = Join-Path $Dist "eProContactAudit-app.exe"
if (Test-Path $AppExe) { Remove-Item $AppExe -Force }
Rename-Item $BuiltExe "eProContactAudit-app.exe"
$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $Csc /nologo /target:winexe /r:System.Windows.Forms.dll "/out:$BuiltExe" (Join-Path $Root "packaging\windows_launcher.cs")
if ($LASTEXITCODE -ne 0) { throw "Windows launcher compile failed" }

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
Right-click the zip, choose Extract All, then double-click eProContactAudit.exe.

Do not run the app from inside the zip. Windows leaves the rest of the files behind and shows "Failed to load Python DLL".

Keep this whole folder together. Do not move the .exe out by itself.
The app checks GitHub when it opens. When a newer version exists, click Update.
"@
Set-Content -Path (Join-Path $Dist "README.txt") -Value $Readme -Encoding UTF8

Write-Host ""
Write-Host "Done: $Dist\eProContactAudit.exe" -ForegroundColor Green
