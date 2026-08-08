# Remove only the Watch Clank scheduled task. Leaves all project data intact.
#
# Does NOT delete: database, logs, snapshots, virtual environment, or config.
#
# Usage:
#   .\scripts\remove_windows_task.ps1

$ErrorActionPreference = "Stop"
$TaskName = "WatchClank-CasioJapan"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' does not exist. Nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task: $TaskName"
Write-Host "Project data (database, logs, snapshots, .venv) was left untouched."
exit 0
