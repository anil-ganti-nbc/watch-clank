# Install or update the EXPERIMENTAL WatchClank-GCentral and
# WatchClank-Plus9Time scheduled tasks (Sprint 7 source expansion).
# Own tasks, own locks, own collector_ids -- independently disableable from
# each other, CASIOBLOG, and Casio production.
#
# Cadence:
#   G-Central: 45 min (same reasoning as CASIOBLOG -- cheap WordPress RSS,
#   real ~hourly updatePeriod, several posts/week).
#   Plus9Time: 360 min (6h) -- real posting cadence observed during Sprint 7
#   research is roughly weekly, not hourly; a 45min check would be pure
#   waste against a source that rarely changes. 6h matches the existing
#   product-observation cadence and is still frequent enough to catch same-
#   day posts.
#
# Usage:
#   .\scripts\install_windows_gcentral_plus9time_tasks.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled_experimental.ps1"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_powershell.ps1"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Lanes = @(
    @{ Name = "gcentral";  Task = "WatchClank-GCentral";  Interval = 45 },
    @{ Name = "plus9time"; Task = "WatchClank-Plus9Time"; Interval = 360 }
)

Write-Host "=== Watch Clank G-Central / Plus9Time task installer ==="
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
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -Lane $($Lane.Name)" `
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
Write-Host "=== Verification: trigger gcentral once and confirm a new collector_runs row ==="
$BeforeCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Start-ScheduledTask -TaskName "WatchClank-GCentral"

$Deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 5
    $State = (Get-ScheduledTask -TaskName "WatchClank-GCentral").State
    Write-Host "  state=$State"
    if ($State -ne "Running") { break }
} while ((Get-Date) -lt $Deadline)
Start-Sleep -Seconds 3

$AfterCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Write-Host "collector_runs: before=$BeforeCount after=$AfterCount"

if ($AfterCount -le $BeforeCount) {
    Write-Host "FAILURE: no new collector_runs row after triggering WatchClank-GCentral."
    $AllOk = $false
}

Write-Host ""
if ($AllOk) {
    Write-Host "SUCCESS: WatchClank-GCentral and WatchClank-Plus9Time registered; GCentral verified live."
    exit 0
} else {
    Write-Host "One or more steps failed - see output above."
    exit 1
}
