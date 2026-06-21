# Registers a Windows Scheduled Task that refreshes the site twice a day
# (default 08:30 + 18:00 local time / IST). Run once from PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1            # local preview only
#   powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1 -Publish   # auto-build + push to Pages
# Remove with:
#   Unregister-ScheduledTask -TaskName "IndianStockSite-Refresh" -Confirm:$false
param(
    [string]$TaskName = "IndianStockSite-Refresh",
    [string]$Time1 = "08:30",
    [string]$Time2 = "18:00",
    [switch]$Publish,              # use refresh_and_publish.ps1 (exhaustive AI build + git push)
    [int]$RecTop = 300,            # stocks the AI Buy/Sell/Hold analyses (when -Publish)
    [switch]$NoMl,                 # skip ML forecasts (faster) when -Publish
    [int]$TimeLimitHours = 6       # max run time — exhaustive AI+ML can take hours
)

$ErrorActionPreference = "Stop"
if ($Publish) {
    $script = Join-Path $PSScriptRoot "refresh_and_publish.ps1"
    $extra = "-RecTop $RecTop" + $(if ($NoMl) { " -NoMl" } else { "" })
} else {
    $script = Join-Path $PSScriptRoot "refresh_local_site.ps1"
    $extra = ""
}
if (-not (Test-Path $script)) { throw "$script not found." }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" $extra"

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $Time1),
    (New-ScheduledTaskTrigger -Daily -At $Time2)
)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -RunOnlyIfNetworkAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours $TimeLimitHours) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered '$TaskName' at $Time1 + $Time2 (local time)."
if ($Publish) {
    Write-Host "Mode: PUBLISH — builds exhaustively (RecTop=$RecTop) and pushes the 'gh-pages' branch."
    Write-Host "Ensure: (1) git is authenticated for sidhartha-patra, (2) Settings -> Pages -> Source = 'Deploy from a branch' -> gh-pages."
} else {
    Write-Host "Mode: LOCAL PREVIEW — serves at http://localhost:8765/ (not published)."
}
Write-Host "Run now with: Start-ScheduledTask -TaskName '$TaskName'"
