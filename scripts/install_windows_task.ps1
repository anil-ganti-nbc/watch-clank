# Install or update the Watch Clank Casio Japan scheduled task.
# Requires: PowerShell 5+, permission to create scheduled tasks.
#
# Security model: runs as the installing user while logged on (Interactive).
# Unattended execution without an interactive logon would require stored
# credentials; this installer does NOT store credentials.
#
# Usage:
#   .\scripts\install_windows_task.ps1

$ErrorActionPreference = "Stop"
$TaskName = "WatchClank-CasioJapan"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled.ps1"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_powershell.ps1"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$IntervalMinutes = 90
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Write-Host "=== Watch Clank task installer ==="
Write-Host "Repository:     $RepoRoot"
Write-Host "Identity:       $CurrentIdentity"
Write-Host "Python:         $VenvPython"
Write-Host "Wrapper:        $RunScript"
Write-Host "SQLite path:    $SqlitePath"
Write-Host "Interval:       $IntervalMinutes minutes"
Write-Host "Logon model:    Interactive (while user is logged on)"
Write-Host ""

# 1. Prerequisites
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment Python not found at $VenvPython. Create .venv and install deps first."
    exit 2
}
if (-not (Test-Path $RunScript)) {
    Write-Error "Missing run_scheduled.ps1 at $RunScript"
    exit 2
}

# 2. Validate PowerShell syntax before registering anything
if (Test-Path $ValidateScript) {
    Write-Host "Validating PowerShell scripts..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ValidateScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PowerShell validation failed. Aborting install."
        exit 2
    }
} else {
    Write-Host "WARN: validate_powershell.ps1 not found; skipping syntax check."
}

# 3. Migration verification – compare alembic head to DB revision
$env:PYTHONPATH = $RepoRoot
$env:DATABASE_URL = "sqlite:///$($SqlitePath -replace '\\', '/')"

Write-Host "Checking Alembic migration state..."
$HeadOut = & $VenvPython -m alembic heads 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $HeadOut
    Write-Error "Failed to read Alembic heads."
    exit 3
}
$HeadRev = ($HeadOut | Select-String -Pattern '^\s*([0-9a-zA-Z_]+)\b' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $HeadRev) {
    # Fallback: take first token of first non-empty line
    $HeadRev = (($HeadOut | Where-Object { $_.ToString().Trim() -ne "" } | Select-Object -First 1).ToString() -split '\s+')[0]
}
Write-Host "Alembic head:   $HeadRev"

$DbRev = $null
try {
    $DbRev = & $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL');
c=e.connect();
r=c.execute(text('SELECT version_num FROM alembic_version')).scalar();
print(r if r else '')" 2>&1
    if ($LASTEXITCODE -ne 0) { $DbRev = $null }
} catch {
    $DbRev = $null
}
$DbRev = if ($DbRev) { $DbRev.ToString().Trim() } else { "" }
Write-Host "Database rev:   $(if ($DbRev) { $DbRev } else { '(none / missing)' })"

if ($DbRev -ne $HeadRev) {
    Write-Host "Database is not at head. Running: python -m alembic upgrade head"
    & $VenvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Alembic upgrade head failed."
        exit 3
    }
    $DbRev = & $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL');
c=e.connect(); print(c.execute(text('SELECT version_num FROM alembic_version')).scalar())" 2>&1
    $DbRev = $DbRev.ToString().Trim()
    Write-Host "Database rev after upgrade: $DbRev"
    if ($DbRev -ne $HeadRev) {
        Write-Error "Database still not at head after upgrade (got '$DbRev', want '$HeadRev')."
        exit 3
    }
}

# Warn if multiple SQLite files exist under the repo
$DbFiles = Get-ChildItem -Path $RepoRoot -Recurse -Filter "watch_clank.db" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.venv\\' }
if ($DbFiles -and $DbFiles.Count -gt 1) {
    Write-Host "WARN: multiple watch_clank.db files found:"
    $DbFiles | ForEach-Object { Write-Host "  $($_.FullName)" }
    Write-Host "Scheduled runs will use: $SqlitePath"
}

# 4. Capture baseline collector_runs count
$BeforeCount = 0
try {
    $BeforeCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL');
c=e.connect();
print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
} catch {
    $BeforeCount = 0
}
Write-Host "collector_runs before trigger: $BeforeCount"

# 5. Register the task (idempotent)
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $RepoRoot

# Repeat every 90 minutes for ~10 years (Task Scheduler-compatible finite duration)
$RepetitionDuration = New-TimeSpan -Days 3650
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration $RepetitionDuration

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 0

# Interactive logon: runs only while this user is logged on. No stored password.
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

Write-Host ""
Write-Host "=== Registration ==="
Write-Host "Task name:      $TaskName"
Write-Host "State:          $($Task.State)"
Write-Host "Enabled:        $($Task.Settings.Enabled)"
Write-Host "Next run:       $($Info.NextRunTime)"
Write-Host "Action:         powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`""
Write-Host "Working dir:    $RepoRoot"
Write-Host "Identity:       $CurrentIdentity"
Write-Host "Logon type:     Interactive (while logged on)"

if (-not $Task.Settings.Enabled) {
    Write-Error "Task registered but is not enabled."
    exit 1
}

# 6. Immediate verification: manual trigger must create a collector_runs row
Write-Host ""
Write-Host "=== Immediate verification (Start-ScheduledTask) ==="
Start-ScheduledTask -TaskName $TaskName

$Deadline = (Get-Date).AddMinutes(3)
$LastResult = $null
do {
    Start-Sleep -Seconds 5
    $Info = Get-ScheduledTaskInfo -TaskName $TaskName
    $LastResult = $Info.LastTaskResult
    $State = (Get-ScheduledTask -TaskName $TaskName).State
    Write-Host "  state=$State lastResult=$LastResult"
    if ($State -ne "Running") { break }
} while ((Get-Date) -lt $Deadline)

# Give the wrapper a moment to flush logs / commit
Start-Sleep -Seconds 3

$AfterCount = 0
try {
    $AfterCount = [int](& $VenvPython -c "from sqlalchemy import create_engine, text; e=create_engine(r'$env:DATABASE_URL');
c=e.connect();
print(c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar())" 2>&1)
} catch {
    $AfterCount = $BeforeCount
}
Write-Host "collector_runs after trigger:  $AfterCount"

$WrapperLog = Join-Path $RepoRoot "data\logs\scheduled-wrapper.log"
$PythonLog = Join-Path $RepoRoot "data\logs\scheduled-python.log"
Write-Host "Wrapper log exists: $(Test-Path $WrapperLog)"
Write-Host "Python log exists:  $(Test-Path $PythonLog)"
if (Test-Path $WrapperLog) {
    Write-Host "--- wrapper log (tail) ---"
    Get-Content $WrapperLog -Tail 15
}
if (Test-Path $PythonLog) {
    Write-Host "--- python log (tail) ---"
    Get-Content $PythonLog -Tail 20
}

if ($AfterCount -le $BeforeCount) {
    Write-Host ""
    Write-Host "FAILURE: No new collector_runs row after Start-ScheduledTask."
    Write-Host "LastTaskResult=$LastResult  (0 = success from Task Scheduler perspective)"
    Write-Host "Diagnose with: .\scripts\status_windows_task.ps1"
    Write-Host "And:            .\scripts\run_scheduled.ps1"
    exit 1
}

Write-Host ""
Write-Host "SUCCESS: Task installed and verified. New collector_runs row(s) recorded."
Write-Host "Note: A BLOCKED / ZERO_ITEMS / PARTIAL / FAILED status still counts as a real run."
exit 0
