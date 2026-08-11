# Single finite EXPERIMENTAL lane run for Task Scheduler / manual use.
# Mirrors run_scheduled.ps1's contract exactly (same exit codes, same
# log-writing pattern) but targets one of the four experimental lanes
# instead of Casio, via its own log files so it never interleaves with
# Casio's scheduled-wrapper.log/scheduled-python.log. Each lane has its own
# isolated run lock (see app/services/run_lock.py) so running this
# alongside run_scheduled.ps1, or alongside another experimental lane,
# cannot collide or contaminate Casio's production state.
#
# Exit codes (from Python, preserved by this wrapper):
#   0 = SUCCESS, PARTIAL, ZERO_ITEMS, BLOCKED, SKIPPED_OVERLAP (nonfatal)
#   1 = pipeline failure (FAILED)
#   2 = setup / configuration / fatal exception
#
# Usage:
#   .\scripts\run_scheduled_experimental.ps1 -Lane citizen-news
#   .\scripts\run_scheduled_experimental.ps1 -Lane seiko-news
#   .\scripts\run_scheduled_experimental.ps1 -Lane citizen-products
#   .\scripts\run_scheduled_experimental.ps1 -Lane seiko-products
#   .\scripts\run_scheduled_experimental.ps1 -Lane casioblog

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("citizen-news", "seiko-news", "citizen-products", "seiko-products", "casioblog")]
    [string]$Lane
)

$ErrorActionPreference = "Stop"

$LaneArgs = @{
    "citizen-news"      = @("--experimental-brand", "citizen")
    "seiko-news"        = @("--experimental-brand", "seiko")
    "citizen-products"  = @("--experimental-product", "citizen")
    "seiko-products"    = @("--experimental-product", "seiko")
    "casioblog"         = @("--experimental-specialist", "casioblog")
}
$PipelineArgs = $LaneArgs[$Lane]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "data\logs"
$WrapperLog = Join-Path $LogDir "scheduled-experimental-$Lane-wrapper.log"
$PythonLog = Join-Path $LogDir "scheduled-experimental-$Lane-python.log"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"

function Write-WrapperLog {
    param([Parameter(Mandatory = $true)][string]$Message)
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

. (Join-Path $ScriptDir "lib_log_rotate.ps1")
Invoke-LogRotate -Path $WrapperLog
Invoke-LogRotate -Path $PythonLog

Write-WrapperLog "START pid=$PID repo=$RepoRoot lane=$Lane"
Write-WrapperLog "DB_PATH=$SqlitePath"
Write-WrapperLog "VENV_PYTHON=$VenvPython"

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
    Write-WrapperLog "INVOKING: $VenvPython -m scripts.run_pipeline $($PipelineArgs -join ' ')"

    $output = & $VenvPython -m scripts.run_pipeline @PipelineArgs 2>&1
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
