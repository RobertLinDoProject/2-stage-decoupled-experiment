[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $root "portable_packages"
} elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $root $OutputDirectory
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$packageName = "decoupled_2_stage_runtime_$stamp"
$stagingRoot = Join-Path $OutputDirectory $packageName
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

$excludedDirectoryNames = @(
    ".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".pnpm-store", "node_modules", "dist", "coverage", "playwright-report", "test-results",
    "storage", "artifacts", "prototype_data", "docx_render_qa", "._tmp_topology0803",
    "portable_packages", "build"
)

function Get-RobocopyExcludedPaths {
    param([string]$Source)

    @(
        Get-ChildItem -LiteralPath $Source -Directory -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object {
                $isExcludedName = $excludedDirectoryNames -contains $_.Name -or $_.Name -like "*.egg-info"
                if (-not $isExcludedName) {
                    return $false
                }

                # Do not pass nested directories below an already excluded
                # tree (especially node_modules) to robocopy. This keeps the
                # command line bounded on Windows while preserving the same
                # exclusion semantics.
                $ancestor = $_.Parent
                while ($null -ne $ancestor -and $ancestor.FullName -ne $Source) {
                    if ($excludedDirectoryNames -contains $ancestor.Name -or $ancestor.Name -like "*.egg-info") {
                        return $false
                    }
                    $ancestor = $ancestor.Parent
                }
                return $true
            } |
            ForEach-Object { $_.FullName }
    )
}

function Copy-PortableTree {
    param([string]$RelativePath)

    $source = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required directory is missing: $RelativePath"
    }
    $destination = Join-Path $stagingRoot $RelativePath
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $excludedPaths = Get-RobocopyExcludedPaths -Source $source
    & robocopy $source $destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP `
        /XF ".env" "*.log" "*.pyc" "*.tsbuildinfo" `
        /XD $excludedPaths | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Failed to copy $RelativePath; robocopy exit code $LASTEXITCODE"
    }
}

foreach ($directory in @("backend", "frontend", "contracts", "configs", "Data", "scripts")) {
    Copy-PortableTree -RelativePath $directory
}

# Ship only an empty writable storage skeleton. Existing Run artifacts are never copied.
$storageDestination = Join-Path $stagingRoot "storage"
New-Item -ItemType Directory -Path (Join-Path $storageDestination "tmp") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $storageDestination "published\runs") -Force | Out-Null
$storageReadme = Join-Path $root "storage\README.md"
if (Test-Path -LiteralPath $storageReadme -PathType Leaf) {
    Copy-Item -LiteralPath $storageReadme -Destination (Join-Path $storageDestination "README.md") -Force
}
Set-Content -LiteralPath (Join-Path $storageDestination "tmp\.gitkeep") -Value "" -Encoding ASCII
Set-Content -LiteralPath (Join-Path $storageDestination "published\runs\.gitkeep") -Value "" -Encoding ASCII

$rootFiles = @(
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    "LICENSE",
    "Makefile",
    "README.md"
)
$rootFiles += Get-ChildItem -LiteralPath $root -File -Force |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".md", ".pdf", ".docx", ".pptx") } |
    ForEach-Object { $_.Name }

foreach ($fileName in ($rootFiles | Sort-Object -Unique)) {
    $source = Join-Path $root $fileName
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $stagingRoot $fileName) -Force
    }
}

$manifestPath = Join-Path $stagingRoot "portable_manifest.json"
$fileRecords = @(
    Get-ChildItem -LiteralPath $stagingRoot -File -Recurse -Force |
        Where-Object { $_.FullName -ne $manifestPath } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($stagingRoot.Length).TrimStart("\", "/").Replace("\", "/")
            [ordered]@{
                path = $relative
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)
$dataRecords = @($fileRecords | Where-Object { $_.path -like "Data/*" })
$manifest = [ordered]@{
    schema_version = "portable_runtime_manifest_v1"
    package_name = $packageName
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    source_policy = "formal runtime and A/B input data only; empty writable storage skeleton included; no prior Run artifacts or dependency caches"
    ollama = [ordered]@{
        provider = "ollama"
        model = "mistral:7b-instruct-v0.3-q4_K_M"
        endpoint = "http://host.docker.internal:11434/api/chat"
        prompt_template_version = "m6_ollama_action_v1"
    }
    file_count = $fileRecords.Count
    input_data_file_count = $dataRecords.Count
    input_data_files = $dataRecords
    files = $fileRecords
}
$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$zipPath = $null
if (-not $SkipZip) {
    $zipPath = Join-Path $OutputDirectory "$packageName.zip"
    Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
}

$summary = [ordered]@{
    package_directory = $stagingRoot
    package_zip = $zipPath
    file_count = $fileRecords.Count
    input_data_file_count = $dataRecords.Count
    manifest = $manifestPath
}
if ($zipPath) {
    $summary.zip_sha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
$summary | ConvertTo-Json -Depth 4
