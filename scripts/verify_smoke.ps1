param(
  [ValidateSet("smoke","strict","live")]
  [string]$Mode = "smoke",
  [int]$Ticks = 600,
  [int]$DtMs = 50,
  [double]$Base = 150.20,
  [double]$Spread = 0.5,
  [double]$AtrPct = 0.00050
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSCommandPath
Set-Location (Join-Path $root "..")

$python = "python"

function Invoke-Py {
  param([string]$ArgsLine)
  & $python -c $ArgsLine
}

function PyMod {
  param(
    [string]$Module,
    [string[]]$Arguments
  )
  & $python -m $Module @Arguments
}

Write-Host "== Step 1: Compile =="
PyMod "compileall" @("app","core","scripts") | Out-Null
Write-Host "OK: compile"

Write-Host "== Step 2: Runtime mode check =="
$live = Invoke-Py "from core.utils.runtime import is_live; print('LIVE' if is_live() else 'DRYRUN')"
Write-Host "Runtime:" $live

Write-Host "== Step 3: Show key filters =="
Invoke-Py "from core.config import cfg; f=cfg.get('filters',{}); hy=f.get('atr_hysteresis',{}); print('min_atr_pct=', f.get('min_atr_pct'), 'enable_min=', hy.get('enable_min_pct'), 'disable_min=', hy.get('disable_min_pct')); entry=cfg.get('entry',{}); print('prob_threshold=', entry.get('prob_threshold')); print('session.allow_hours_jst=', cfg.get('session',{}).get('allow_hours_jst'))"

Write-Host "== Step 4: Ensure logs dir =="
$logDir = "logs\decisions"
if (!(Test-Path $logDir)) {
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
}

Write-Host "== Step 5: Launch GUI =="
Start-Process -WindowStyle Minimized powershell -ArgumentList "-NoLogo -NoExit -Command `"$python -m app.gui.main`""
Start-Sleep -Seconds 2

Write-Host "== Step 6: Dryrun / Replay =="
$extra = @()
switch ($Mode) {
  "smoke"  { $extra = @("--atr-open") }
  "strict" { $extra = @() }
  "live"   { $extra = @() }
}

$argsList = @("--sim") + $extra + @(
  "--n", "$Ticks",
  "--dt", "$DtMs",
  "--base", "$Base",
  "--spread", "$Spread",
  "--atrpct", "$AtrPct"
)
Write-Host "Running: python -m scripts.dryrun_smoke $($argsList -join ' ')"
PyMod "scripts.dryrun_smoke" $argsList

Write-Host "== Step 7: Log scan =="
$decisionLog = Get-ChildItem -Path $logDir -Filter "decisions_*.jsonl" | Sort-Object LastWriteTime | Select-Object -Last 1
if ($null -eq $decisionLog) {
  Write-Warning "No decisions_*.jsonl found."
  $entryCount = 0
} else {
  $entryCount = (Select-String -Path $decisionLog.FullName -Pattern '"decision": "ENTRY"' | Measure-Object).Count
}
$trailFlags = Get-Content "logs\app.log" -ErrorAction SilentlyContinue | Select-String "\[TRAIL\]\[(DRYRUN|OK|NG)\]"
$trailCount = ($trailFlags | Measure-Object).Count

Write-Host "ENTRY count =" $entryCount
Write-Host "TRAIL events =" $trailCount

Write-Host "== Step 8: PASS/FAIL =="
$pass = $false
if ($Mode -eq "smoke") {
  if ($trailCount -ge 1) { $pass = $true }
} else {
  if ($entryCount -ge 1 -or $trailCount -ge 1) { $pass = $true }
}

if ($pass) {
  Write-Host "RESULT: PASS" -ForegroundColor Green
  exit 0
} else {
  Write-Host "RESULT: FAIL" -ForegroundColor Red
  Write-Host "Hints:"
  Write-Host " - smokeモードでは --atr-open が有効か確認してください"
  Write-Host " - [TRAIL] ログが出力されているかを確認してください"
  Write-Host " - strict モードでは atrpct を調整してゲートを跨ぐか検討してください"
  exit 1
}
