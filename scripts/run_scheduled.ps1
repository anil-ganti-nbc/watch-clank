# Single finite Casio Japan pipeline run for Task Scheduler / manual use.
# Acquires overlap lock via Python, writes logs, exits with meaningful code.
#
# Exit codes (from Python, preserved by this wrapper):
#   0 = SUCCESS, PARTIAL, ZERO_ITEMS, BLOCKED, SKIPPED_OVERLAP (nonfatal)
#   1 = pipeline failure (FAILED)
#   2 = setup / configuration / fatal exception
#   3 = migration / database failure (reserved)
#
# Usage:
#   .\scripts\run_scheduled.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "data\logs"
$WrapperLog = Join-Path $LogDir "scheduled-wrapper.log"
$PythonLog = Join-Path $LogDir "scheduled-python.log"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"

function Write-WrapperLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )
    $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
    $line = "$ts $Message"
    try {
        $line | Out-File -FilePath $WrapperLog -Append -Encoding utf8
    } catch {
        Write-Host "WARN: could not write wrapper log: $_"
    }
    Write-Host $line
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

Write-WrapperLog "START pid=$PID repo=$RepoRoot"
Write-WrapperLog "DB_PATH=$SqlitePath"
Write-WrapperLog "VENV_PYTHON=$VenvPython"
Write-WrapperLog "PWD_BEFORE=$PWD"

if (-not (Test-Path $VenvPython)) {
    Write-WrapperLog "ERROR: venv python missing at $VenvPython"
    exit 2
}

$env:PYTHONPATH = $RepoRoot
$env:DATABASE_URL = "sqlite:///$($SqlitePath -replace '\\', '/')"

$code = 2
Push-Location $RepoRoot
try {
    Write-WrapperLog "CWD=$PWD"
    Write-WrapperLog "INVOKING: $VenvPython -m scripts.run_pipeline --scheduled"

    $output = & $VenvPython -m scripts.run_pipeline --scheduled 2>&1
    if ($null -eq $LASTEXITCODE) {
        if ($?) { $code = 0 } else { $code = 1 }
    } else {
        $code = [int]$LASTEXITCODE
    }

    if ($null -ne $output) {
        $output | ForEach-Object { "$_" } | Out-File -FilePath $PythonLog -Append -Encoding utf8
        $output | ForEach-Object { Write-Host $_ }
    }
} catch {
    Write-WrapperLog "EXCEPTION: $_"
    $code = 2
} finally {
    Pop-Location
}

Write-WrapperLog "END exit_code=$code"
exit $code
