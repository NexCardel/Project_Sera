# Registers the "Amas Sera\Updater" scheduled task (run by the installer, elevated).
#
# The task runs sera_update_agent.ps1 as SYSTEM at boot and every 30 minutes. Logged-on users
# may start it (read + execute on the task): the app does that right after it closes itself for
# an update, so the install begins at once instead of on the next 30-minute tick. Starting it
# runs only the agent script in Program Files, which users cannot modify.
#
# An install launched by the task itself re-runs this script; the definition is replaced only
# when TaskSchema changes, so the running agent is never torn down mid-install.

param([Parameter(Mandatory = $true)][string]$AppDir)

$ErrorActionPreference = "Stop"
$TaskPath = "\Amas Sera\"
$TaskName = "Updater"
$TaskSchema = "amas-sera-updater/1"

# Updater state: SYSTEM and admins write, users only read (the agent runs installers from here).
$state = Join-Path $env:ProgramData "AmasSera\updater"
New-Item -ItemType Directory -Force -Path $state | Out-Null
& icacls.exe $state /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null

$existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description.Contains("[$TaskSchema]") -and
    $existing.Actions[0].Arguments -like "*$AppDir*") {
    exit 0
}

$script = Join-Path $AppDir "updater\sera_update_agent.ps1"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$script`""
$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = "PT1M"
# Daily trigger repeating every 30 minutes for 24 hours = every 30 minutes, on every Windows 10 build.
$every = New-ScheduledTaskTrigger -Daily -At "00:00"
$every.Repetition = (New-ScheduledTaskTrigger -Once -At "00:00" -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-18" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Trigger @($boot, $every) `
    -Settings $settings -Principal $principal -Force `
    -Description "Installs Amas Sera updates silently. [$TaskSchema]" | Out-Null

# Owner/admins/SYSTEM full control; Authenticated Users read + execute (may start it, not change it).
$svc = New-Object -ComObject "Schedule.Service"
$svc.Connect()
$task = $svc.GetFolder($TaskPath.TrimEnd("\")).GetTask($TaskName)
$task.SetSecurityDescriptor("D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;GRGX;;;AU)", 0)
