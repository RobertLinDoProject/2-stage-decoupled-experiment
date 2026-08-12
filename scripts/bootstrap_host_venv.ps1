[CmdletBinding()]
param(
    [string]$PythonCommand = "python",
    [string]$VenvPath = ".venv",
    [switch]$InstallDev
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $root $VenvPath
$python = Join-Path $venv "Scripts\python.exe"
$backend = Join-Path $root "backend"

function Assert-Python312 {
    param([string]$Executable)
    $version = & $Executable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot execute Python at '$Executable'. Install Python 3.12 or newer."
    }
    $parts = [version]($version | Select-Object -Last 1)
    if ($parts -lt [version]'3.12') {
        throw "Python 3.12 or newer is required; detected $parts."
    }
}

if (-not (Get-Command $PythonCommand -ErrorAction SilentlyContinue)) {
    throw "Python command '$PythonCommand' was not found. Install Python 3.12 or newer and retry."
}

Assert-Python312 $PythonCommand

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    & $PythonCommand -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment at '$venv'." }
}

Assert-Python312 $python

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to bootstrap pip in '$venv'." }

$packageSpec = if ($InstallDev) { Join-Path $backend ".[dev]" } else { Join-Path $backend "." }
& $python -m pip install $packageSpec
if ($LASTEXITCODE -ne 0) { throw "Failed to install backend dependencies into '$venv'." }

$manifest = [ordered]@{
    schema_version = "host_python_venv_manifest_v1"
    created_at_utc = [DateTime]::UtcNow.ToString("o")
    python_executable = $python
    python_version = (& $python --version 2>&1).ToString().Trim()
    package = "backend"
    install_mode = if ($InstallDev) { "editable_dev" } else { "editable_runtime" }
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $root "host_venv_manifest.json") -Encoding UTF8

Write-Output "Host virtual environment ready: $venv"
Write-Output "Use: & '$python' -m unittest discover backend/src/two_stage/tests"
