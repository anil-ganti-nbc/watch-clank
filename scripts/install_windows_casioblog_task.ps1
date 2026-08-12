# Install or update the EXPERIMENTAL WatchClank-Casioblog scheduled task.
# Deliberately its own installer/task, separate from the four brand lanes
# (install_windows_experimental_tasks.ps1) and from Casio production, so
# CASIOBLOG can be disabled independently without touching anything else.
#
# Cadence: 45 minutes. Justification — CASIOBLOG is a standard WordPress RSS
# feed (cheap GET, <20 items, no auth/anti-bot concerns) with a real
# updateFrequency of ~hourly and several posts/week in practice, so 45
# minutes gives multiple checks per typical posting window without hammering
# the source (32 requests/day) — well inside the 30-60 min range called for
# in the sprint brief for a cheap RSS feed.
#
# Usage:
#   .\scripts\install_windows_casioblog_task.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled_experimental.ps1"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_powershell.ps1"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$TaskName = "WatchClank-Casioblog"
$IntervalMinutes = 45

Write-Host "=== Watch Clank CASIOBLOG task installer ==="
Write-Host "Repository: $RepoRoot"
Write-Host "Identity:   $CurrentIdentity"
Write-Host "Cadence:    every $IntervalMinutes minutes"
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

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`" -Lane casioblog" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
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

$AllOk = $Task.Settings.Enabled
if (-not $AllOk) {
    Write-Host "FAILURE: $TaskName registered but not enabled."
}

Write-Host ""
Write-Host "=== Verification: trigger once and confirm a new collector_runs row ==="
$BeforeCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Start-ScheduledTask -TaskName $TaskName

$Deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 5
    $State = (Get-ScheduledTask -TaskName $TaskName).State
    Write-Host "  state=$State"
    if ($State -ne "Running") { break }
} while ((Get-Date) -lt $Deadline)
Start-Sleep -Seconds 3

$AfterCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Write-Host "collector_runs: before=$BeforeCount after=$AfterCount"

if ($AfterCount -le $BeforeCount) {
    Write-Host "FAILURE: no new collector_runs row after triggering $TaskName."
    $AllOk = $false
}

Write-Host ""
if ($AllOk) {
    Write-Host "SUCCESS: WatchClank-Casioblog registered and verified live."
    exit 0
} else {
    Write-Host "One or more steps failed - see output above."
    exit 1
}
