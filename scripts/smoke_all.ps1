# scripts/smoke_all.ps1
# Runtime schema validation smoke tests (all-in-one)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSCommandPath
Set-Location (Join-Path $root "..")

$python = "python"

Write-Host "=== Python Environment ==="

# python コマンドの解決結果
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    Write-Host "python command : $($pyCmd.Source)"
} else {
    Write-Host "python command : NOT FOUND"
}

# 実際に使われる python executable
try {
    $pyExe = python -c "import sys; print(sys.executable)"
    Write-Host "python executable : $pyExe"
} catch {
    Write-Host "python executable : FAILED TO QUERY"
}

# Python バージョン
try {
    $pyVer = python -c "import sys; print(sys.version.replace('\n',' '))"
    Write-Host "python version    : $pyVer"
} catch {
    Write-Host "python version    : FAILED TO QUERY"
}

Write-Host "==========================="
Write-Host ""

Write-Host "=== Runtime Schema Validation Smoke Tests ===" -ForegroundColor Cyan
Write-Host ""

# STEP 1: Demo schema warn=0
Write-Host "[1/3] Demo runtime schema validation..." -ForegroundColor Yellow
$demoOut = python -X utf8 scripts\demo_run_stub.py 2>&1
$demoWarnings = $demoOut | Select-String -Pattern "\[runtime_schema\]" -SimpleMatch
$demoCount = ($demoWarnings | Measure-Object).Count

if ($demoCount -gt 0) {
    Write-Host "  FAILED: [runtime_schema] warnings detected ($demoCount)" -ForegroundColor Red
    $demoWarnings | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    throw "Demo runtime schema validation failed: $demoCount warnings"
}
Write-Host "  PASSED: [runtime_schema] warnings = 0" -ForegroundColor Green
Write-Host ""

# STEP 2a: Negative test (should fail)
Write-Host "[2a/4] Live runtime schema validation (negative test)..." -ForegroundColor Yellow
$env:SMOKE_NEGATIVE = "1"
try {
    $negativeOut = python -X utf8 tools\live_runtime_smoke.py --inject-runtime-warn 2>&1
    $negativeExitCode = $LASTEXITCODE

    if ($negativeExitCode -eq 2) {
        Write-Host "  PASSED: Negative test correctly failed (exit 2)" -ForegroundColor Green
    } else {
        Write-Host "  FAILED: Negative test should fail with exit 2, got $negativeExitCode" -ForegroundColor Red
        $negativeOut | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        throw "Negative test failed: should exit with code 2"
    }
} finally {
    Remove-Item Env:\SMOKE_NEGATIVE -ErrorAction SilentlyContinue
}
Write-Host ""

# STEP 2b: Live schema warn=0 (normal test)
Write-Host "[2b/4] Live runtime schema validation (normal test)..." -ForegroundColor Yellow
$liveOut = python -X utf8 tools\live_runtime_smoke.py 2>&1
$liveExitCode = $LASTEXITCODE

# [runtime_schema] 警告を検出（exit code とは独立してチェック）
$liveWarnings = $liveOut | Select-String -Pattern "\[runtime_schema\]" -SimpleMatch
$liveWarningCount = ($liveWarnings | Measure-Object).Count

if ($liveExitCode -eq 2 -or $liveWarningCount -gt 0) {
    Write-Host "  FAILED: [runtime_schema] warnings detected ($liveWarningCount)" -ForegroundColor Red
    if ($liveWarnings) {
        $liveWarnings | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    }
    throw "Live runtime schema validation failed: warnings detected"
} elseif ($liveExitCode -eq 1) {
    Write-Host "  FAILED: Exception occurred" -ForegroundColor Red
    $liveOut | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    throw "Live runtime schema validation failed: exception"
} else {
    Write-Host "  PASSED: [runtime_schema] warnings = 0" -ForegroundColor Green
}
Write-Host ""

# STEP 3: decisions.jsonl に旧キー混入 0
Write-Host "[3/4] Checking decisions.jsonl for deprecated keys..." -ForegroundColor Yellow
$logDir = "logs\decisions"
if (!(Test-Path $logDir)) {
    Write-Host "  SKIPPED: Log directory not found ($logDir)" -ForegroundColor Yellow
} else {
    $logFiles = Get-ChildItem -Path $logDir -Filter "decisions_*.jsonl" -ErrorAction SilentlyContinue
    if ($null -eq $logFiles -or $logFiles.Count -eq 0) {
        Write-Host "  SKIPPED: No decision log files found" -ForegroundColor Yellow
    } else {
        $deprecatedKeys = @()
        foreach ($file in $logFiles) {
            $fileMatches = Select-String -Path $file.FullName -Pattern '"_sim_|"runtime_open_positions"|"runtime_max_positions"|"sim_pos_hold_ticks"' -SimpleMatch -ErrorAction SilentlyContinue
            if ($fileMatches) {
                $deprecatedKeys += $fileMatches
            }
        }
        $deprecatedCount = ($deprecatedKeys | Measure-Object).Count
        
        if ($deprecatedCount -gt 0) {
            Write-Host "  FAILED: Deprecated keys detected ($deprecatedCount)" -ForegroundColor Red
            $deprecatedKeys | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
            if ($deprecatedCount -gt 10) {
                Write-Host "    ... and $($deprecatedCount - 10) more" -ForegroundColor Red
            }
            throw "Deprecated keys found in decisions.jsonl: $deprecatedCount"
        }
        Write-Host "  PASSED: No deprecated keys found" -ForegroundColor Green
    }
}
Write-Host ""

# STEP 4: Decision comparison report (optional)
$smokeCompare = $env:SMOKE_COMPARE
if ($smokeCompare -eq "1") {
    Write-Host "[4/4] Generating decision comparison report..." -ForegroundColor Yellow
    try {
        $compareOut = python -X utf8 tools\decision_compare.py `
            --decisions-glob "logs/decisions/decisions_*.jsonl" `
            --backtest-glob "logs/backtest/**/decisions.jsonl" `
            --out-json reports/decision_compare.json `
            --out-md reports/decision_compare.md 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  PASSED: Comparison report generated" -ForegroundColor Green
            Write-Host "    JSON: reports/decision_compare.json" -ForegroundColor Gray
            Write-Host "    Markdown: reports/decision_compare.md" -ForegroundColor Gray
            Write-Host ""
            Write-Host "  Report preview (first 5 lines):" -ForegroundColor Gray
            if (Test-Path "reports/decision_compare.md") {
                Get-Content "reports/decision_compare.md" -TotalCount 5 | ForEach-Object {
                    Write-Host "    $_" -ForegroundColor Gray
                }
            }
        } else {
            Write-Warning "Comparison report generation failed (exit $LASTEXITCODE)"
            $compareOut | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
        }
    } catch {
        Write-Warning "Comparison report generation failed: $_"
    }
    Write-Host ""
}

# STEP 5: Decision comparison alert check (optional, CI落とし用)
$smokeCompareFail = $env:SMOKE_COMPARE_FAIL
if ($smokeCompareFail -eq "1") {
    Write-Host "[5/5] Checking decision comparison alerts..." -ForegroundColor Yellow
    try {
        $alertOut = python -X utf8 tools\decision_compare.py `
            --decisions-glob "logs/decisions/decisions_*.jsonl" `
            --backtest-glob "logs/backtest/**/decisions.jsonl" `
            --fail-on-delta `
            --delta-entry 0.05 `
            --delta-filter-pass 0.10 `
            --delta-blocked 0.10 2>&1
        
        $alertExitCode = $LASTEXITCODE
        
        if ($alertExitCode -eq 2) {
            Write-Host "  FAILED: Threshold violations detected" -ForegroundColor Red
            $alertOut | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
            throw "Decision comparison alert check failed: threshold violations detected"
        } elseif ($alertExitCode -eq 1) {
            Write-Host "  FAILED: Exception or invalid arguments" -ForegroundColor Red
            $alertOut | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
            throw "Decision comparison alert check failed: exception"
        } else {
            Write-Host "  PASSED: All threshold checks passed" -ForegroundColor Green
        }
    } catch {
        Write-Warning "Decision comparison alert check failed: $_"
        throw
    }
    Write-Host ""
}

Write-Host "=== All smoke tests PASSED ===" -ForegroundColor Green
exit 0
