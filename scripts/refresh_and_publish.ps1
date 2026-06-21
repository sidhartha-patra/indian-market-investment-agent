# Builds the FULL site (AI Buy/Sell/Hold + Search + movers + Nifty 50) EXHAUSTIVELY using
# your local `gh` login for FREE GitHub Models (no PAT, no CI limits, runs as long as it needs),
# then auto-commits & pushes the built site to the `gh-pages` branch so GitHub Pages serves it.
#
# Designed for a twice-daily Scheduled Task (see register_local_task.ps1), also runnable by hand:
#   powershell -ExecutionPolicy Bypass -File scripts\refresh_and_publish.ps1 -RecTop 300
#
# State (the AI analysis cache, data\ai_cache) stays on THIS machine and is reused every run,
# so coverage accumulates and only changed stocks are re-analysed. Only the finished static
# site is pushed to GitHub.
#
# > Legal: universe metrics use the TradingView scanner (personal-research ToS). Publishing is
# > your call as the repo owner; every page keeps the educational / not-advice disclaimer.
param(
    [int]$RecTop = 300,                 # how many stocks the AI Buy/Sell/Hold analyses (exhaustive)
    [string]$Source = "hybrid",        # universe display source (hybrid = TV-ranked, Yahoo-shown)
    [int]$Top = 50,                     # home-page "top picks from the whole market"
    [string]$Branch = "gh-pages",      # branch GitHub Pages serves from
    [string]$MoversSource = "yfinance",# 'both'/'auto' adds Screener depth (personal use)
    [string]$Email = "sipatra@microsoft.com",  # where the "done" notification goes
    [string]$PagesUrl = "https://sidhartha-patra.github.io/indian-market-investment-agent/",
    [switch]$NoEmail,                   # skip the completion email
    [switch]$NoPush,                    # build only, skip the git push (dry run)
    [switch]$NoMl,                      # skip ML forecasts (faster)
    [switch]$Serve,                     # also serve the build locally on -Port
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Set-Location $repo
$stamp = Get-Date -Format u
Write-Host "[$stamp] === refresh_and_publish (RecTop=$RecTop, source=$Source) ==="

# --- Free AI: hand the build your gh login token so GitHub Models works with no PAT --------
try {
    $tok = (gh auth token) 2>$null
    if ($tok) { $env:GITHUB_TOKEN = $tok; Write-Host "AI: GitHub Models via gh login (free)." }
    else { Write-Host "AI: no gh token; the build will use the deterministic analyst." }
} catch { Write-Host "AI: gh not available; deterministic analyst will be used." }
# (Set $env:ANTHROPIC_API_KEY before running to use Claude Opus instead.)

# --- Build the full site into a clean staging dir -----------------------------------------
$build = Join-Path $repo ".site-build"
if (Test-Path $build) { Remove-Item -Recurse -Force $build }

$buildArgs = @(
    "-m", "scripts.build_all_stocks",
    "--source", $Source, "--mode", "full", "--top", "$Top",
    "--with-movers", "--movers-source", $MoversSource, "--movers-top", "10",
    "--with-nifty50",
    "--with-recommendations", "--rec-top", "$RecTop"
)
if ($NoMl) { $buildArgs += "--no-ml" }
$buildArgs += @("--out", $build)

Write-Host "[$(Get-Date -Format u)] Building (this can take a while - exhaustive AI + ML)..."
& $py @buildArgs
if ($LASTEXITCODE -ne 0) { throw "Build failed (exit $LASTEXITCODE)." }
New-Item -ItemType File -Path (Join-Path $build ".nojekyll") -Force | Out-Null  # serve _-prefixed paths
Write-Host "[$(Get-Date -Format u)] Build complete -> $build"

if ($Serve) {
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        Start-Process -FilePath $py -ArgumentList @("-m", "http.server", "$Port", "--directory", "`"$build`"") -WindowStyle Hidden
        Write-Host "Serving locally at http://localhost:$Port/"
    }
}

$published = $false
if ($NoPush) {
    Write-Host "-NoPush set; skipping publish."
} else {
    # --- Publish: mirror the build into a `gh-pages` worktree and push -------------------------
    $pub = Join-Path $repo ".gh-pages-publish"
    git fetch origin --quiet 2>$null
    $remoteHasBranch = (git ls-remote --heads origin $Branch) -ne $null -and (git ls-remote --heads origin $Branch) -ne ""

    if (-not (Test-Path (Join-Path $pub ".git"))) {
        git worktree prune 2>$null | Out-Null
        if ($remoteHasBranch) {
            git worktree add $pub $Branch
        } else {
            git worktree add --detach $pub
            Push-Location $pub
            git checkout --orphan $Branch
            git reset --hard 2>$null | Out-Null
            Pop-Location
        }
    } else {
        Push-Location $pub
        if ($remoteHasBranch) { git checkout $Branch 2>$null; git pull --ff-only origin $Branch 2>$null }
        Pop-Location
    }

    # Replace published content with the fresh build (keep .git).
    Get-ChildItem -Force $pub | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
    Copy-Item -Path (Join-Path $build "*") -Destination $pub -Recurse -Force

    Push-Location $pub
    git add -A
    if ((git status --porcelain).Length -eq 0) {
        Write-Host "No changes to publish."
        $published = $true
    } else {
        git -c user.name="sidhartha-patra" -c user.email="15684919+sidhartha-patra@users.noreply.github.com" `
            commit -m "site: automated refresh $stamp" --quiet
        git push origin $Branch
        if ($LASTEXITCODE -eq 0) { $published = $true; Write-Host "[$(Get-Date -Format u)] Published to '$Branch'." }
        else { Write-Host "Push failed (exit $LASTEXITCODE)." }
    }
    Pop-Location
    Write-Host "Ensure Settings -> Pages -> Source = 'Deploy from a branch' -> '$Branch' / root."
}

# --- Completion email via the local mail MCP (same one your other agents use) --------------
if (-not $NoEmail) {
    try {
        $stats = @{ universe = "?"; ai = "?"; buy = "?"; hold = "?"; sell = "?" }
        $recJson = Join-Path $build "data\recommendations.json"
        if (Test-Path $recJson) {
            $r = Get-Content $recJson -Raw | ConvertFrom-Json
            $stats.universe = $r.universe_count; $stats.ai = $r.ai_analysed
            $stats.buy = $r.buckets.BUY.Count; $stats.hold = $r.buckets.HOLD.Count; $stats.sell = $r.buckets.SELL.Count
        }
        $statusLine = if ($NoPush) { "Built locally (not published)." } elseif ($published) { "Published to GitHub Pages." } else { "Built, but publish FAILED - check the log." }
        $subject = "Indian Stock Agent - refresh $(Get-Date -Format 'yyyy-MM-dd HH:mm') ($($stats.buy) buy / $($stats.sell) sell)"
        $html = @"
<div style='font-family:Segoe UI,Arial,sans-serif;max-width:640px'>
<h2 style='margin:0 0 6px'>Indian Market Investment Agent - daily refresh</h2>
<p style='color:#555'>$statusLine &nbsp;|&nbsp; $stamp</p>
<table style='border-collapse:collapse;font-size:14px'>
<tr><td style='padding:3px 10px'>Stocks analysed</td><td><b>$($stats.universe)</b> (AI: $($stats.ai))</td></tr>
<tr><td style='padding:3px 10px'>Top Buy / Hold / Sell</td><td><b style='color:#2e7d32'>$($stats.buy)</b> / $($stats.hold) / <b style='color:#c62828'>$($stats.sell)</b></td></tr>
</table>
<p><a href='$PagesUrl'>Open the site</a> &nbsp;|&nbsp;
<a href='${PagesUrl}recommendations.html'>Buy / Sell / Hold</a> &nbsp;|&nbsp;
<a href='${PagesUrl}search.html'>Search a stock</a></p>
<p style='color:#999;font-size:12px'>Educational model output, not investment advice. Auto-generated by refresh_and_publish.ps1.</p>
</div>
"@
        $tmp = Join-Path $env:TEMP "invest_agent_email_$(Get-Random).html"
        Set-Content -Path $tmp -Value $html -Encoding UTF8
        Write-Host "[$(Get-Date -Format u)] Emailing $Email via local mail MCP..."
        node (Join-Path $PSScriptRoot "notify_email.mjs") $Email $subject $tmp 2>&1 | Write-Host
        Remove-Item $tmp -ErrorAction SilentlyContinue
    } catch {
        Write-Host "Email step failed (non-fatal): $_"
    }
}
Write-Host "[$(Get-Date -Format u)] === refresh_and_publish complete ==="
