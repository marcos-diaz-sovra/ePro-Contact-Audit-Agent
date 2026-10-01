# First-launch bootstrap: no Python install required.
# Double-click run.bat. Subsequent launches skip setup and open the app.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root ".playwright"

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Marker = Join-Path $Root ".venv\.setup-complete"
$UvDir = Join-Path $Root ".tools"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Get-Uv {
    $uv = Join-Path $UvDir "uv.exe"
    if (Test-Path $uv) { return $uv }
    Write-Step "Downloading the setup helper (one-time)..."
    New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
    $zip = Join-Path $env:TEMP "uv-windows.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $UvDir -Force
    $found = Get-ChildItem -Path $UvDir -Recurse -Filter "uv.exe" | Select-Object -First 1
    if (-not $found) { throw "Could not download the setup helper (uv.exe)." }
    if ($found.DirectoryName -ne $UvDir) {
        Copy-Item $found.FullName (Join-Path $UvDir "uv.exe") -Force
    }
    return (Join-Path $UvDir "uv.exe")
}

function Test-AppImport([string]$PythonExe) {
    & $PythonExe -c "import PySide6, playwright, pandas, yaml, openpyxl" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-Chromium {
    $dir = $env:PLAYWRIGHT_BROWSERS_PATH
    if (-not (Test-Path $dir)) { return $false }
    return [bool](Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "chromium*" })
}

function Install-App {
    Write-Step "First-time setup — this usually takes a few minutes, then the app opens."
    Write-Host "You do not need to install Python yourself."

    $uv = Get-Uv
    Write-Step "Installing a local Python runtime..."
    & $uv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw "Could not install Python." }

    Write-Step "Creating the app environment..."
    & $uv venv (Join-Path $Root ".venv") --python 3.12
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv." }

    Write-Step "Installing application packages..."
    & $uv pip install --python $VenvPython -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Package install failed." }

    Write-Step "Installing the browser used to open public contract pages (~120 MB, one-time)..."
    New-Item -ItemType Directory -Force -Path $env:PLAYWRIGHT_BROWSERS_PATH | Out-Null
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Browser install failed." }

    "ok" | Set-Content -Path $Marker -Encoding ascii
}

try {
    $needsSetup = -not (Test-Path $VenvPython) -or -not (Test-Path $Marker) -or -not (Test-AppImport $VenvPython)
    if ($needsSetup) {
        Install-App
    } elseif (-not (Test-Chromium)) {
        Write-Step "Installing the browser used to open public contract pages..."
        New-Item -ItemType Directory -Force -Path $env:PLAYWRIGHT_BROWSERS_PATH | Out-Null
        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Browser install failed." }
    }

    Write-Step "Starting ePro Contact Audit Agent..."
    & $VenvPython (Join-Path $Root "gui.py")
    if ($LASTEXITCODE -ne 0) { throw "The app exited with an error." }
}
catch {
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "If you are on the corporate network, confirm GitHub/python.org are allowed, then try again." -ForegroundColor Yellow
    exit 1
}
