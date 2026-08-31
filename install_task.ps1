# install_task.ps1
# Registers a Windows Task Scheduler job that runs the JLaw scanner daily at 07:30.
#
# Run ONCE as Administrator:
#   & "C:\Users\lauki\WorkBuddy\2026-08-25-14-58-48\jlaw_scanner\install_task.ps1"
#
# NOTE: Task names in Task Scheduler may NOT contain : * ? " < > |
#       (that is what caused "HRESULT 0x80070057 - The parameter is incorrect").
#       So we use "JLawDailyScanner1000" (no colon).

$ErrorActionPreference = "Stop"

$here     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$taskName = "JLawDailyScanner1000"
$bat      = Join-Path $here "run_daily.bat"

if (-not (Test-Path $bat)) {
    Write-Host "ERROR: $bat not found. Aborting." -ForegroundColor Red
    exit 1
}

# --- remove any older / duplicate tasks from earlier attempts (colon names never registered, but be safe) ---
@("JLaw Daily Scanner 07:00", "JLaw Daily Scanner 09:45", "JLaw Daily Scanner 10:00", "JLawDailyScanner1000") |
    ForEach-Object {
        try { Unregister-ScheduledTask -TaskName $_ -Confirm:$false -ErrorAction SilentlyContinue } catch { }
    }

try {
    $action    = New-ScheduledTaskAction -Execute $bat
    $trigger   = New-ScheduledTaskTrigger -Daily -At "07:30"
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

    $t    = Get-ScheduledTask -TaskName $taskName
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host ""
    Write-Host "Created task: $taskName" -ForegroundColor Green
    Write-Host "State:       $($t.State)"
    Write-Host "Next run:    $($info.NextRunTime)"
    Write-Host ""
    Write-Host "Test now:    right-click the task in taskschd.msc -> Run"
    Write-Host "Review:      taskschd.msc"
} catch {
    Write-Host "REGISTER FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Task name used: $taskName" -ForegroundColor Yellow
    exit 1
}
