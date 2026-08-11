# Shared helper: cap a single log file's size before it's appended to.
# The Python side (app/core/logging.py) already uses RotatingFileHandler
# for its own log; this covers the PowerShell wrapper/python-output logs
# (scheduled-*.log), which just Out-File -Append forever otherwise.
# Deliberately simple: one rotation, one backup — this is not a log
# analysis platform, just a cap so a wrapper log can't grow unbounded.

function Invoke-LogRotate {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$MaxBytes = 5MB
    )
    if (Test-Path $Path) {
        $size = (Get-Item $Path).Length
        if ($size -gt $MaxBytes) {
            $backup = "$Path.1"
            Remove-Item -Path $backup -ErrorAction SilentlyContinue
            Rename-Item -Path $Path -NewName (Split-Path -Leaf $backup) -ErrorAction SilentlyContinue
        }
    }
}
