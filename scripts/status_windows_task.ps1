# Show Task Scheduler + Watch Clank operational status.
#
# Usage:
#   .\scripts\status_windows_task.ps1

$ErrorActionPreference = "Continue"
$TaskName = "WatchClank-CasioJapan"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LockFile = Join-Path $RepoRoot "data\casio_japan.run.lock"
$SqlitePath = Join-Path $RepoRoot "data\watch_clank.db"
$WrapperLog = Join-Path $RepoRoot "data\logs\scheduled-wrapper.log"
$PythonLog = Join-Path $RepoRoot "data\logs\scheduled-python.log"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled.ps1"

function Format-TaskResult {
    param([int]$Code)
    $hex = ("0x{0:X8}" -f $Code)
    switch ($Code) {
        0       { $msg = "SUCCESS (0)" }
        1       { $msg = "General failure / incorrect function (1)" }
        267009  { $msg = "Task is still running (0x41301)" }
        267011  { $msg = "Task has not yet run (0x41303)" }
        267014  { $msg = "Task was terminated by user (0x41306)" }
        default { $msg = "See Task Scheduler docs" }
    }
    return ("{0} ({1}) - {2}" -f $Code, $hex, $msg)
}

Write-Host "=== Repository ==="
Write-Host "Repo root:      $RepoRoot"
Write-Host "Venv Python:    $VenvPython"
Write-Host "SQLite path:    $SqlitePath"
Write-Host "Wrapper script: $RunScript"
Write-Host "SQLite exists:  $(Test-Path $SqlitePath)"

Write-Host ""
Write-Host "=== Task Scheduler ==="
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Exists:         Yes"
    Write-Host "State:          $($task.State)"
    Write-Host "Enabled:        $($task.Settings.Enabled)"
    Write-Host "Last run:       $($info.LastRunTime)"
    Write-Host "Next run:       $($info.NextRunTime)"
    Write-Host "Last result:    $(Format-TaskResult -Code ([int]$info.LastTaskResult))"
    $actions = $task.Actions
    foreach ($a in $actions) {
        Write-Host "Execute:        $($a.Execute)"
        Write-Host "Arguments:      $($a.Arguments)"
        Write-Host "Working dir:    $($a.WorkingDirectory)"
    }
} else {
    Write-Host "Exists:         No"
}

Write-Host ""
Write-Host "=== Overlap lock ==="
if (Test-Path $LockFile) {
    Write-Host "Lock file:      PRESENT - $LockFile"
    Get-Content $LockFile -ErrorAction SilentlyContinue
} else {
    Write-Host "Lock file:      absent"
}

Write-Host ""
Write-Host "=== Logs ==="
Write-Host "Wrapper log:    $(if (Test-Path $WrapperLog) { $WrapperLog } else { 'missing' })"
Write-Host "Python log:     $(if (Test-Path $PythonLog) { $PythonLog } else { 'missing' })"
if (Test-Path $WrapperLog) {
    Write-Host "--- scheduled-wrapper.log (last 20) ---"
    Get-Content $WrapperLog -Tail 20
}
if (Test-Path $PythonLog) {
    Write-Host "--- scheduled-python.log (last 20) ---"
    Get-Content $PythonLog -Tail 20
}

Write-Host ""
Write-Host "=== Latest collector runs (SQLite) ==="
if (-not (Test-Path $VenvPython)) {
    Write-Host "venv python not found; skip DB query"
    exit 0
}

$env:PYTHONPATH = $RepoRoot
$env:DATABASE_URL = "sqlite:///$($SqlitePath -replace '\\', '/')"

& $VenvPython -c @"
from sqlalchemy import create_engine, text
import os
url = os.environ.get('DATABASE_URL')
print(f'DB URL: {url}')
engine = create_engine(url)
with engine.connect() as c:
    try:
        n = c.execute(text('SELECT COUNT(*) FROM collector_runs')).scalar()
    except Exception as e:
        print(f'ERROR reading collector_runs: {e}')
        raise SystemExit(0)
    print(f'Total collector_runs: {n}')
    if n == 0:
        print('No collector runs yet.')
    else:
        rows = c.execute(text('''
            SELECT id, status, started_at, completed_at,
                   discovered_count, fetched_count, parsed_count,
                   warning_count, failure_count, new_watch_count,
                   duration_ms, summary_metadata
            FROM collector_runs
            ORDER BY started_at DESC
            LIMIT 5
        ''')).fetchall()
        for r in rows:
            print(f'id={r[0]} status={r[1]} started={r[2]} completed={r[3]}')
            print(f'  discovered={r[4]} fetched={r[5]} parsed={r[6]} warn={r[7]} fail={r[8]} new={r[9]} duration_ms={r[10]}')
            print(f'  summary={r[11]}')
"@
