param(
    [string]$PythonPath = 'C:\Users\Administrator\anaconda3\pythonw.exe',
    [int]$Port = 9092
)
$ErrorActionPreference = 'Stop'
$serviceRoot = Split-Path -Parent $PSScriptRoot
$serviceLauncher = Join-Path $PSScriptRoot 'run-transformers.pyw'
$serviceName = "LabelStudio-Transformers-$Port"
$serviceUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Anaconda base pythonw.exe was not found.' }
$existingTask = Get-ScheduledTask -TaskName $serviceName -ErrorAction SilentlyContinue
if ($existingTask -and ($existingTask.Actions.Arguments -notlike "*$serviceLauncher*")) {
    throw 'A different task already uses this name; refusing to overwrite it.'
}
$serviceAction = New-ScheduledTaskAction -Execute $PythonPath -Argument ('-X utf8 "{0}" --port {1}' -f $serviceLauncher, $Port) -WorkingDirectory $serviceRoot
$serviceTrigger = New-ScheduledTaskTrigger -AtLogOn -User $serviceUser
$servicePrincipal = New-ScheduledTaskPrincipal -UserId $serviceUser -LogonType Interactive -RunLevel Highest
$serviceSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $serviceName -Action $serviceAction -Trigger $serviceTrigger -Principal $servicePrincipal -Settings $serviceSettings -Description 'Local Label Studio Transformers inference, using the existing Anaconda base.' -Force | Out-Null
Write-Output "Registered $serviceName for user logon. Stop any manually started instance before starting this task."
