[CmdletBinding()]
param(
    [switch]$StartServices,
    [string]$ApiBaseUrl = "http://localhost:8000/api/v1",
    [string]$ExpectedModel = "mistral:7b-instruct-v0.3-q4_K_M"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$checks = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Message)
    $checks.Add([pscustomobject]@{ Check = $Name; Status = if ($Passed) { "PASS" } else { "FAIL" }; Message = $Message })
}

function Get-EnvValue {
    param([string]$Name)
    $line = Get-Content -LiteralPath (Join-Path $root ".env") -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^$([regex]::Escape($Name))=(.*)$" } |
        Select-Object -First 1
    if ($null -eq $line) { return $null }
    return ([regex]::Match($line, "^$([regex]::Escape($Name))=(.*)$")).Groups[1].Value.Trim()
}

$ollamaExecutable = $null
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if ($null -ne $ollamaCommand) {
    $ollamaExecutable = $ollamaCommand.Source
} else {
    $ollamaCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    $ollamaExecutable = $ollamaCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}

try {
    $ollamaVersionResponse = Invoke-RestMethod "http://127.0.0.1:11434/api/version" -TimeoutSec 5
    $ollamaTagsResponse = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $ollamaModelNames = @($ollamaTagsResponse.models | ForEach-Object { [string]$_.name })
    $ollamaServiceAvailable = $true
    $ollamaServiceMessage = "Ollama HTTP service responded (version $($ollamaVersionResponse.version))"
} catch {
    $ollamaModelNames = @()
    $ollamaServiceAvailable = $false
    $ollamaServiceMessage = "Local Ollama HTTP service unavailable: $($_.Exception.Message)"
}

Add-Check "Docker command" ([bool](Get-Command docker -ErrorAction SilentlyContinue)) "docker executable is available"
Add-Check "Ollama command" (($null -ne $ollamaExecutable) -or $ollamaServiceAvailable) $(if ($null -ne $ollamaExecutable) { "ollama executable is available at $ollamaExecutable" } elseif ($ollamaServiceAvailable) { "Ollama service is reachable even though CLI is not in PATH" } else { "ollama executable is not in PATH or a standard install location" })
Add-Check "Ollama service" $ollamaServiceAvailable $ollamaServiceMessage
Add-Check "Ollama model" ($ollamaServiceAvailable -and ($ollamaModelNames -contains $ExpectedModel)) "Model is present in local Ollama /api/tags"
Add-Check "Formal Data" (Test-Path -LiteralPath (Join-Path $root "Data") -PathType Container) "Data directory exists"
Add-Check "Docker Compose file" (Test-Path -LiteralPath (Join-Path $root "docker-compose.yml") -PathType Leaf) "docker-compose.yml exists"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvVersion = (& $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1 | Select-Object -Last 1).ToString().Trim()
    $venvVersionOk = $LASTEXITCODE -eq 0 -and $venvVersion -match "^3\.(1[2-9]|[2-9][0-9])$"
    Add-Check "Host Python venv" $venvVersionOk "Using .venv\Scripts\python.exe ($venvVersion)"
} else {
    Add-Check "Host Python venv" $false "Missing .venv\Scripts\python.exe; run .\scripts\bootstrap_host_venv.ps1"
}

$envPath = Join-Path $root ".env"
if (Test-Path -LiteralPath $envPath -PathType Leaf) {
    Add-Check "GAI execution mode" ((Get-EnvValue "GAI_EXECUTION_MODE") -eq "live") "GAI_EXECUTION_MODE=live"
    Add-Check "GAI provider" ((Get-EnvValue "GAI_PROVIDER_NAME") -eq "ollama") "GAI_PROVIDER_NAME=ollama"
    Add-Check "GAI model" ((Get-EnvValue "GAI_PROVIDER_MODEL") -eq $ExpectedModel) "Configured model is $ExpectedModel"
    Add-Check "GAI endpoint" ((Get-EnvValue "GAI_PROVIDER_ENDPOINT") -match "host\.docker\.internal:11434/api/chat") "Docker API reaches host Ollama through host.docker.internal"
} else {
    Add-Check ".env" $false ".env is missing; create it from .env.example"
}

$manifestPath = Join-Path $root "portable_manifest.json"
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $manifestFailures = @()
        foreach ($record in $manifest.files) {
            $filePath = Join-Path $root ($record.path -replace "/", "\")
            if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
                $manifestFailures += "$($record.path): missing"
                continue
            }
            $actualHash = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualHash -ne [string]$record.sha256) {
                $manifestFailures += "$($record.path): checksum mismatch"
            }
        }
        $manifestMessage = if ($manifestFailures.Count -eq 0) { "All packaged files match" } else { $manifestFailures -join "; " }
        Add-Check "Portable manifest" ($manifestFailures.Count -eq 0) $manifestMessage
    } catch {
        Add-Check "Portable manifest" $false "Manifest could not be verified: $($_.Exception.Message)"
    }
}

if ($StartServices) {
    Push-Location $root
    try {
        docker compose up -d --build
        Add-Check "Docker services" ($LASTEXITCODE -eq 0) "docker compose up completed"
    } finally {
        Pop-Location
    }
}

try {
    $health = Invoke-RestMethod "$ApiBaseUrl/health" -TimeoutSec 10
    Add-Check "API health" ($health.status -eq "ok") "API health endpoint responded"
} catch {
    Add-Check "API health" $false "API is not reachable: $($_.Exception.Message)"
}

try {
    $gai = Invoke-RestMethod "$ApiBaseUrl/decoupled-2-stage-experiment/gai/preflight" -TimeoutSec 15
    Add-Check "GAI provider preflight" ($gai.status -eq "PASSED") ([string]$gai.message)
} catch {
    Add-Check "GAI provider preflight" $false "Preflight request failed: $($_.Exception.Message)"
}

$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object Status -eq "FAIL")
if ($failed.Count -gt 0) {
    Write-Error "Portable environment validation failed: $($failed.Count) check(s)."
    exit 1
}
Write-Output "Portable environment validation passed. No GAI action was generated by this script."
