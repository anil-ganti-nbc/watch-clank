# Validate all Watch Clank PowerShell scripts with the PowerShell language parser.
# Exits nonzero if any syntax errors are found.
#
# Usage:
#   .\scripts\validate_powershell.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path

$Targets = @(
    (Join-Path $ScriptDir "install_windows_task.ps1"),
    (Join-Path $ScriptDir "install_windows_experimental_tasks.ps1"),
    (Join-Path $ScriptDir "run_scheduled.ps1"),
    (Join-Path $ScriptDir "run_scheduled_experimental.ps1"),
    (Join-Path $ScriptDir "status_windows_task.ps1"),
    (Join-Path $ScriptDir "remove_windows_task.ps1"),
    (Join-Path $ScriptDir "validate_powershell.ps1")
)

$Failed = 0

foreach ($Path in $Targets) {
    if (-not (Test-Path $Path)) {
        Write-Host "MISSING: $Path"
        $Failed++
        continue
    }

    $Tokens = $null
    $Errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $Path,
        [ref]$Tokens,
        [ref]$Errors
    )

    if ($Errors -and $Errors.Count -gt 0) {
        Write-Host "FAIL: $Path"
        foreach ($err in $Errors) {
            Write-Host "  Line $($err.Extent.StartLineNumber): $($err.Message)"
        }
        $Failed++
    } else {
        Write-Host "OK:   $Path"
    }
}

if ($Failed -gt 0) {
    Write-Host ""
    Write-Host "PowerShell validation failed: $Failed file(s) with errors."
    exit 1
}

Write-Host ""
Write-Host "All PowerShell scripts parsed successfully."
exit 0
