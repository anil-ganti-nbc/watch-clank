# Install or update the EXPERIMENTAL WatchClank-TimexNews and
# WatchClank-TimexProducts scheduled tasks (Sprint 9: Timex as fourth
# official brand). Own tasks, own locks, own collector_ids -- independently
# disableable from Casio/Citizen/Seiko and from each other.
#
# Cadence:
#   News (timex_news, Shopify Atom blog feed): 90 min -- matches the
#   existing citizen_news/seiko_jp_news cadence for a first-party official
#   news source of comparable cost.
#   Products (timex_products, Shopify products.json, ~1445 real watches
#   across 6-7 pages): 360 min (6h) -- matches the existing
#   citizen_products/seiko_products cadence for a catalogue collector of
#   comparable scale/cost.
#
# NOTE: this task deliberately does NOT pass --force-baseline. The initial
# silent Timex population (source-scoped baseline joining the already-live
# Epoch 1) was a one-time manual operation -- see HANDOFF.md's Sprint 9
# checkpoint. Ongoing scheduled runs use normal (non-baseline) semantics,
# exactly like every other source.
#
# Usage:
#   .\scripts\install_windows_timex_tasks.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled_experimental.ps1"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_powershell.ps1"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Lanes = @(
    @{ Name = "timex-news";     Task = "WatchClank-TimexNews";     Interval = 90 },
    @{ Name = "timex-products"; Task = "WatchClank-TimexProducts"; Interval = 360 }
)

Write-Host "=== Watch Clank Timex task installer ==="
Write-Host "Repository: $RepoRoot"
Write-Host "Identity:   $CurrentIdentity"
Write-Host ""

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment Python not found at $VenvPython."
    exit 2
}
if (-not (Test-Path $RunScript)) {
    Write-Error "Missing run_scheduled_experimental.ps1 at $RunScript"
    exit 2
}

if (Test-Path $ValidateScript) {
    Write-Host "Validating PowerShell scripts..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ValidateScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PowerShell validation failed. Aborting install."
        exit 2
    }
}

$env:PYTHONPATH = $RepoRoot
$env:DATABASE_URL = "sqlite:///$($SqlitePath -replace '\\', '/')"

$AllOk = $true
foreach ($Lane in $Lanes) {
    $TaskName = $Lane.Task
    $Interval = $Lane.Interval
    Write-Host ""
    Write-Host "--- $($Lane.Name) -> $TaskName (every $Interval min) ---"

    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`" -Lane $($Lane.Name)" `
        -WorkingDirectory $RepoRoot

    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes $Interval) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -RestartCount 0

    $Principal = New-ScheduledTaskPrincipal `
        -UserId $CurrentIdentity `
        -LogonType Interactive `
        -RunLevel Limited

    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal | Out-Null

    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "State: $($Task.State)  Enabled: $($Task.Settings.Enabled)  NextRun: $($Info.NextRunTime)"

    if (-not $Task.Settings.Enabled) {
        Write-Host "FAILURE: $TaskName registered but not enabled."
        $AllOk = $false
    }
}

Write-Host ""
Write-Host "=== Verification: trigger timex-news once and confirm a new collector_runs row ==="
$BeforeCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Start-ScheduledTask -TaskName "WatchClank-TimexNews"

$Deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 5
    $State = (Get-ScheduledTask -TaskName "WatchClank-TimexNews").State
    Write-Host "  state=$State"
    if ($State -ne "Running") { break }
} while ((Get-Date) -lt $Deadline)
Start-Sleep -Seconds 3

$AfterCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Write-Host "collector_runs: before=$BeforeCount after=$AfterCount"

if ($AfterCount -le $BeforeCount) {
    Write-Host "FAILURE: no new collector_runs row after triggering WatchClank-TimexNews."
    $AllOk = $false
}

Write-Host ""
if ($AllOk) {
    Write-Host "SUCCESS: WatchClank-TimexNews and WatchClank-TimexProducts registered; TimexNews verified live."
    exit 0
} else {
    Write-Host "One or more steps failed - see output above."
    exit 1
}
