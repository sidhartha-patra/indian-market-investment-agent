# Rebuilds the LOCAL all-stocks site (TradingView source, personal research) and makes
# sure a static web server is serving it. Designed to be run by Task Scheduler twice a
# day (see register_local_task.ps1), but also runnable by hand.
#
# NOTE: TradingView data is for PERSONAL research only — do NOT publish this output.
param(
    [string]$Out = (Join-Path $env:USERPROFILE "stock-site-preview"),
    [int]$Limit = 1500,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Set-Location $repo
Write-Host "[$(Get-Date -Format u)] Rebuilding local site -> $Out (limit $Limit)"
& $py -m scripts.build_all_stocks --source tradingview --limit $Limit --out $Out

# http.server reads files from disk per request, so a running server auto-serves the
# freshly rebuilt pages — we only (re)start one if nothing is listening on $Port.
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Start-Process -FilePath $py `
        -ArgumentList @("-m", "http.server", "$Port", "--directory", "`"$Out`"") `
        -WindowStyle Hidden
    Write-Host "Started server on http://localhost:$Port/"
} else {
    Write-Host "Server already running on http://localhost:$Port/ (serving refreshed files)"
}
