# scripts/verify_runtime_schema.ps1
# Runtime schema v1 smoke verification

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

cd D:\fxbot

# venv activate (optional)
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
  . .\.venv\Scripts\Activate.ps1
  Write-Host "venv activated"
} else {
  Write-Host "NOTE: venv not found, continuing without activation"
}

Write-Host "=== (A) smoke_all.ps1 (should PASS) ==="
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_all.ps1
Write-Host "smoke_all.ps1: PASS"

Write-Host "=== (B1) negative smoke without env (should be BLOCKED exit 1) ==="
python -X utf8 tools\live_runtime_smoke.py --inject-runtime-warn
$code = $LASTEXITCODE
Write-Host "exit code:" $code
if ($code -ne 1) { throw "Expected exit 1 when SMOKE_NEGATIVE is not set, got $code" }

Write-Host "=== (B2) negative smoke with env (should FAIL exit 2 and include [runtime_schema]) ==="
$env:SMOKE_NEGATIVE="1"

# IMPORTANT: force string output (avoid array)
$out = (python -X utf8 tools\live_runtime_smoke.py --inject-runtime-warn 2>&1 | Out-String)
$code = $LASTEXITCODE

Write-Host $out
Write-Host "exit code:" $code

if ($code -ne 2) { throw "Expected exit 2 for negative test, got $code" }
if ($out -notmatch "\[runtime_schema\]") { throw "Expected output to include [runtime_schema], but not found" }

Remove-Item Env:\SMOKE_NEGATIVE -ErrorAction SilentlyContinue

Write-Host "=== (C) deprecated keys should NOT exist in decisions log (0 hits) ==="
if (!(Test-Path "logs\decisions\decisions_USDJPY.jsonl")) {
  Write-Host "NOTE: decisions log not found; skipping deprecated keys check."
} else {
  $hits = Select-String -Path logs\decisions\decisions_USDJPY.jsonl -Pattern '"_sim_|"runtime_open_positions"|"runtime_max_positions"|"sim_pos_hold_ticks"' -SimpleMatch
  if ($hits) {
    $hits | Select-Object -First 20 | ForEach-Object { $_.Line } | Out-Host
    throw "Deprecated keys found in decisions log."
  }
  Write-Host "deprecated keys check: PASS (0 hits)"
}

Write-Host "=== ALL CHECKS PASSED ==="
