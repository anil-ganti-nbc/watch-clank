# Install the four approved EXPERIMENTAL publication RSS lanes. Each task has
# its own collector id and run lock, so it can be disabled independently.
#
# Cadence: Monochrome and Fratello publish multiple times per day (45 min);
# Deployant and WatchTime have a lower observed current cadence (90 min).

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoRoot "scripts\run_scheduled_experimental.ps1"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Lanes = @(
    @{ Name = "monochrome"; Task = "WatchClank-Monochrome"; Interval = 45 },
    @{ Name = "deployant"; Task = "WatchClank-Deployant"; Interval = 90 },
    @{ Name = "fratello"; Task = "WatchClank-Fratello"; Interval = 45 },
    @{ Name = "watchtime"; Task = "WatchClank-WatchTime"; Interval = 90 }
)

if (-not (Test-Path $VenvPython)) { throw "Virtual environment Python not found at $VenvPython" }
if (-not (Test-Path $RunScript)) { throw "Missing scheduled wrapper at $RunScript" }

foreach ($Lane in $Lanes) {
    $TaskName = $Lane.Task
    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`" -Lane $($Lane.Name)" `
        -WorkingDirectory $RepoRoot
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes $Lane.Interval) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -RestartCount 0
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentIdentity -LogonType Interactive -RunLevel Limited

    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal | Out-Null
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if (-not $Task.Settings.Enabled) { throw "$TaskName registered but is not enabled" }
    Write-Host "${TaskName}: Ready/Enabled, every $($Lane.Interval) minutes"
}
