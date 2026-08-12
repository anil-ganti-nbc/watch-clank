# Install or update the four EXPERIMENTAL Watch Clank scheduled tasks
# (citizen_news, seiko_jp_news, citizen_products, seiko_products) alongside
# the existing WatchClank-CasioJapan task. Same interactive-logon model, no
# stored credentials. Each task is independently named/registered so any one
# can be disabled without affecting the others or Casio.
#
# Usage:
#   .\scripts\install_windows_experimental_tasks.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled_experimental.ps1"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_powershell.ps1"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

# Lane -> (TaskName, interval minutes)
$Lanes = @(
    @{ Name = "citizen-news";     Task = "WatchClank-CitizenNews";     Interval = 90 },
    @{ Name = "seiko-news";       Task = "WatchClank-SeikoNews";       Interval = 90 },
    @{ Name = "citizen-products"; Task = "WatchClank-CitizenProducts"; Interval = 360 },
    @{ Name = "seiko-products";   Task = "WatchClank-SeikoProducts";   Interval = 360 }
)

Write-Host "=== Watch Clank EXPERIMENTAL task installer ==="
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

# Same migration gate as the Casio installer (informational — schema_check
# in run_pipeline.py refuses at runtime anyway if this drifts later).
$DbRev = $null
try {
    $DbRev = & $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT version_num FROM alembic_version')).scalar())" 2>&1
} catch { $DbRev = $null }
Write-Host "Database rev: $DbRev"

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
Write-Host "=== Verification: trigger citizen-news once and confirm a new collector_runs row ==="
$BeforeCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Start-ScheduledTask -TaskName "WatchClank-CitizenNews"

$Deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 5
    $State = (Get-ScheduledTask -TaskName "WatchClank-CitizenNews").State
    Write-Host "  state=$State"
    if ($State -ne "Running") { break }
} while ((Get-Date) -lt $Deadline)
Start-Sleep -Seconds 3

$AfterCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL'); c=e.connect(); print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
Write-Host "collector_runs: before=$BeforeCount after=$AfterCount"

if ($AfterCount -le $BeforeCount) {
    Write-Host "FAILURE: no new collector_runs row after triggering WatchClank-CitizenNews."
    $AllOk = $false
}

Write-Host ""
if ($AllOk) {
    Write-Host "SUCCESS: all four experimental tasks registered and citizen-news verified live."
    exit 0
} else {
    Write-Host "One or more steps failed - see output above."
    exit 1
}
