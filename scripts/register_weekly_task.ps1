param(
  [string]$TaskName = "FXBot_WeeklyRetrain",
  [string]$PythonExe = "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\python.exe",
  [string]$ProjectDir = "C:\fxbot",
  [string]$StartTime = "03:05",   # JST
  [ValidateSet("Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday")]
  [string]$DayOfWeek = "Sunday"
)

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts/walkforward_retrain.py" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $StartTime
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopOnIdleEnd
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Weekly walk-forward retrain & promote if metrics pass"
Write-Host "Registered task:" $TaskName
