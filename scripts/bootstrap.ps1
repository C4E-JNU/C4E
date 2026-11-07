param(
    [string]$AssetUrl = "https://github.com/C4E-JNU/C4E/releases/latest/download/C4EAI-Portable.zip"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "python\python.exe"

if (Test-Path -LiteralPath $python) {
    Write-Host "Portable runtime already present."
    exit 0
}

$zip = Join-Path $env:TEMP "C4EAI-Portable.zip"
$downloadOk = $false

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host "Downloading: $AssetUrl"
    Invoke-WebRequest -Uri $AssetUrl -OutFile $zip -UseBasicParsing -Headers @{ "User-Agent" = "C4EAI-Bootstrap" }
    $size = (Get-Item -LiteralPath $zip).Length
    if ($size -lt 50MB) {
        throw "Downloaded file is too small ($([math]::Round($size / 1MB, 1)) MB)."
    }

    Write-Host "Extracting runtime..."
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($tar) {
        & $tar.Source -xf $zip -C $root
        if ($LASTEXITCODE -ne 0) { throw "tar exited with code $LASTEXITCODE." }
    } else {
        Expand-Archive -LiteralPath $zip -DestinationPath $root -Force
    }

    if (-not (Test-Path -LiteralPath $python)) {
        throw "python\python.exe was not found after extraction."
    }
    $downloadOk = $true
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Write-Host "Portable runtime is ready."
}
catch {
    Write-Warning "Portable runtime download failed: $($_.Exception.Message)"
    Remove-Item -LiteralPath (Join-Path $root "python") -Recurse -Force -ErrorAction SilentlyContinue
}

if ($downloadOk) { exit 0 }

Write-Host ""
Write-Host "Falling back to a local Python environment..."
$sysPython = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $sysPython) {
    Write-Host ""
    Write-Host "[ERROR] Python was not found on this computer."
    Write-Host "Install Python 3.10+ from https://www.python.org/downloads/ (enable 'Add python.exe to PATH'),"
    Write-Host "or download C4EAI-Portable.zip from the GitHub Releases page and extract it here."
    exit 1
}

try {
    $version = & $sysPython.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    $parts = $version.Trim().Split(".")
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
        throw "Python 3.10+ is required, found $version."
    }

    $venv = Join-Path $root ".venv"
    $venvPython = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating .venv..."
        & $sysPython.Source -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed." }
    }

    Write-Host "Installing dependencies (this may take several minutes)..."
    & $venvPython -m pip install --upgrade pip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
    & $venvPython -m pip install -r (Join-Path $root "backend\requirements.txt") -i https://pypi.tuna.tsinghua.edu.cn/simple --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "dependency installation failed." }

    Write-Host "Local environment is ready."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)"
    Write-Host "You can also download C4EAI-Portable.zip from the GitHub Releases page and extract it here."
    exit 1
}
