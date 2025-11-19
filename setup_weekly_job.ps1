param(
  [string]$TaskName   = "FXBot_WeeklyRetrain",
  [string]$PythonExe  = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe",
  [string]$ProjectDir = "$env:USERPROFILE\OneDrive\fxbot",
  [ValidateSet("Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday")]
  [string]$DayOfWeek  = "Saturday",
  [string]$StartTime  = "03:05",
  [switch]$RunHighest = $true
)

$ErrorActionPreference = "Stop"

$Py = Resolve-Path -LiteralPath $PythonExe
$ScriptPath = Join-Path $ProjectDir "scripts\weekly_retrain.py"
if (!(Test-Path -LiteralPath $ScriptPath)) { throw "not found: $ScriptPath" }

$h,$m = $StartTime.Split(":")
$at = [DateTime]::Today.AddHours([int]$h).AddMinutes([int]$m)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $at

$action  = New-ScheduledTaskAction -Execute $Py -Argument "`"$ScriptPath`""

$principal = New-ScheduledTaskPrincipal -UserId $env:UserName -RunLevel ($(if ($RunHighest) {"Highest"} else {"Limited"}))

try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
  -Description "Weekly retrain job for fxbot (LightGBM/XGB WFO + promote)"

Write-Host "`n[OK] Task '$TaskName' registered for $DayOfWeek $StartTime (JST)."
Write-Host "Python: $Py"
Write-Host "Script: $ScriptPath"
