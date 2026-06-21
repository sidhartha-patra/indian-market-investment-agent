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

Write-Host "[$(Get-Date -Format u)] Building (this can take a while — exhaustive AI + ML)…"
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

if ($NoPush) { Write-Host "-NoPush set; skipping publish."; return }

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
} else {
    git -c user.name="sidhartha-patra" -c user.email="15684919+sidhartha-patra@users.noreply.github.com" `
        commit -m "site: automated refresh $stamp" --quiet
    git push origin $Branch
    Write-Host "[$(Get-Date -Format u)] Published to '$Branch'. Pages will update shortly."
}
Pop-Location
Write-Host "Done. Ensure Settings -> Pages -> Source = 'Deploy from a branch' -> '$Branch' / root."
