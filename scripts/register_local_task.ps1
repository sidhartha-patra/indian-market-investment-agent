# Registers a Windows Scheduled Task that refreshes the LOCAL all-stocks site twice a
# day (default 08:30 + 18:00 local time / IST). Run once:
#   powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1
# Remove with:
#   Unregister-ScheduledTask -TaskName "IndianStockSite-Refresh" -Confirm:$false
param(
    [string]$TaskName = "IndianStockSite-Refresh",
    [string]$Time1 = "08:30",
    [string]$Time2 = "18:00"
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "refresh_local_site.ps1"
if (-not (Test-Path $script)) { throw "refresh_local_site.ps1 not found next to this script." }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $Time1),
    (New-ScheduledTaskTrigger -Daily -At $Time2)
)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' at $Time1 and $Time2 (local time)."
Write-Host "Run now with: Start-ScheduledTask -TaskName '$TaskName'"
