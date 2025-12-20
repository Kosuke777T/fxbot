# ================================
# Runtime schema smoke (Agent-safe)
# venv があれば使う／なければ続行
# ================================

$ErrorActionPreference = "Stop"

Write-Host "=== Runtime Schema Validation Smoke Tests (Agent) ==="

# ---- venv (optional) ----
$venvPy = ".\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host "[info] using venv python: $venvPy"
    $PY = $venvPy
} else {
    Write-Warning "[warn] .venv not found. Using system python."
    $PY = "python"
}

# ---- 実際に使われる python を可視化 ----
Write-Host "[info] python command:" (Get-Command $PY).Source
$pyInfo = & $PY -c "import sys; print('[info] sys.executable:', sys.executable); print('[info] sys.version:', sys.version.split()[0])"
Write-Host $pyInfo

# ---- 1) Demo smoke ----
Write-Host "[1/4] Demo runtime schema validation..."
$out = & $PY -X utf8 scripts/demo_run_stub.py 2>&1
$hits = $out | Select-String -Pattern "\[runtime_schema\]" -SimpleMatch
if ($hits) {
    throw "Demo smoke failed: runtime_schema warnings detected"
}
Write-Host "  PASSED"

# ---- 2a) Live negative test (should FAIL) ----
Write-Host "[2a/4] Live runtime schema validation (negative test)..."
$env:SMOKE_NEGATIVE = "1"
$code = 0
$out = & $PY -X utf8 tools/live_runtime_smoke.py --inject-runtime-warn 2>&1
$code = $LASTEXITCODE
Remove-Item Env:SMOKE_NEGATIVE -ErrorAction SilentlyContinue

if ($code -ne 2) {
    throw "Negative test should fail with exit 2, got $code"
}
Write-Host "  PASSED: negative test failed as expected (exit 2)"

# ---- 2b) Live normal test ----
Write-Host "[2b/4] Live runtime schema validation (normal)..."
$out = & $PY -X utf8 tools/live_runtime_smoke.py 2>&1
$hits = $out | Select-String -Pattern "\[runtime_schema\]" -SimpleMatch
if ($hits) {
    throw "Live smoke failed: runtime_schema warnings detected"
}
Write-Host "  PASSED"

# ---- 3) decisions log check ----
Write-Host "[3/4] Checking decisions.jsonl for deprecated keys..."
$hits = Select-String `
    -Path logs\decisions\decisions_USDJPY.jsonl `
    -Pattern '"_sim_|"runtime_open_positions"|"runtime_max_positions"|"sim_pos_hold_ticks"' `
    -SimpleMatch
if ($hits) {
    throw "Deprecated keys found in decisions log"
}
Write-Host "  PASSED"

Write-Host "=== All smoke tests PASSED ==="
