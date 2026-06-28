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
$logDir = "C:\Users\sipatra\.investagent-logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir ("refresh_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
try { Start-Transcript -Path $logFile -Force | Out-Null } catch {}
$buildFailed = $false
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
if ($LASTEXITCODE -ne 0) {
    $buildFailed = $true
    Write-Host "[$(Get-Date -Format u)] BUILD FAILED (exit $LASTEXITCODE). Skipping publish; will email a failure notice."
} else {
    New-Item -ItemType File -Path (Join-Path $build ".nojekyll") -Force | Out-Null  # serve _-prefixed paths
    Write-Host "[$(Get-Date -Format u)] Build complete -> $build"
}

if ($Serve -and -not $buildFailed) {
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
        Start-Process -FilePath $py -ArgumentList @("-m", "http.server", "$Port", "--directory", "`"$build`"") -WindowStyle Hidden
        Write-Host "Serving locally at http://localhost:$Port/"
    }
}

$published = $false
if ($NoPush -or $buildFailed) {
    Write-Host "Skipping publish ($(if ($buildFailed) {'build failed'} else {'-NoPush'}))."
} else {
    # git writes normal progress to stderr; with ErrorActionPreference=Stop that aborts the
    # script. Switch to Continue for the publish and gate on $LASTEXITCODE explicitly.
    $ErrorActionPreference = "Continue"
    try {
        $pub = "C:\Users\sipatra\.investagent-ghpages"
        $credFile = "C:/Users/sipatra/.investagent.git-credentials"
        $remoteUrl = "https://github.com/sidhartha-patra/indian-market-investment-agent.git"
        $lsr = git ls-remote --heads $remoteUrl $Branch 2>$null
        $remoteHasBranch = -not [string]::IsNullOrWhiteSpace($lsr)

        if (-not (Test-Path (Join-Path $pub ".git"))) {
            Remove-Item $pub -Recurse -Force -ErrorAction SilentlyContinue
            if ($remoteHasBranch) {
                git -c credential.helper="store --file=$credFile" clone --branch $Branch --single-branch $remoteUrl $pub 2>&1 | Out-Null
            } else {
                git -c credential.helper="store --file=$credFile" clone $remoteUrl $pub 2>&1 | Out-Null
                Push-Location $pub; git checkout --orphan $Branch 2>&1 | Out-Null; git rm -rf . 2>&1 | Out-Null; Pop-Location
            }
        }
        if (-not (Test-Path (Join-Path $pub ".git"))) {
            Write-Host "Publish clone failed; skipping publish."
        } else {
            Push-Location $pub
            git config credential.helper "store --file=$credFile" 2>&1 | Out-Null
            git config user.name "sidhartha-patra" 2>&1 | Out-Null
            git config user.email "15684919+sidhartha-patra@users.noreply.github.com" 2>&1 | Out-Null
            if ($remoteHasBranch) {
                git fetch origin $Branch 2>&1 | Out-Null
                git checkout $Branch 2>&1 | Out-Null
                git reset --hard "origin/$Branch" 2>&1 | Out-Null
            }
            # Replace published content with the fresh build (keep .git).
            Get-ChildItem -Force . | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
            Copy-Item -Path (Join-Path $build "*") -Destination . -Recurse -Force
            git add -A 2>&1 | Out-Null
            if ([string]::IsNullOrWhiteSpace((git status --porcelain))) {
                Write-Host "No changes to publish."; $published = $true
            } else {
                git commit -m "site: automated refresh $stamp" --quiet 2>&1 | Out-Null
                git push origin $Branch 2>&1 | Write-Host
                if ($LASTEXITCODE -eq 0) { $published = $true; Write-Host "[$(Get-Date -Format u)] Published to '$Branch'." }
                else { Write-Host "Push failed (exit $LASTEXITCODE)." }
            }
            Pop-Location
        }
    } catch {
        Write-Host "Publish step error (non-fatal): $_"
    }
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
        $statusLine = if ($buildFailed) { "BUILD FAILED - see log $logFile" } elseif ($NoPush) { "Built locally (not published)." } elseif ($published) { "Published to GitHub Pages." } else { "Built, but publish FAILED - check the log." }
        $flag = if ($buildFailed) { "[FAILED] " } else { "" }
        $subject = "${flag}Indian Stock Agent - refresh $(Get-Date -Format 'yyyy-MM-dd HH:mm') ($($stats.buy) buy / $($stats.sell) sell)"
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
Write-Host "[$(Get-Date -Format u)] === refresh_and_publish complete (log: $logFile) ==="
try { Stop-Transcript | Out-Null } catch {}
if ($buildFailed) { exit 1 }
