# scripts/compare_live_backtest.ps1
# Live vs Backtest Decision Log Comparison Script

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
    Write-Host "python version : $pyVer"
} catch {
    Write-Host "python version : FAILED TO QUERY"
}
Write-Host "==========================="
Write-Host ""

Write-Host "=== Running Decision Comparison ==="
python -X utf8 tools\decision_compare.py `
    --decisions-glob "logs/decisions/decisions_*.jsonl" `
    --backtest-glob "logs/backtest/**/decisions.jsonl" `
    --out-json reports/decision_compare.json `
    --out-md reports/decision_compare.md

if ($LASTEXITCODE -ne 0) {
    throw "decision_compare.py failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "=== Generated Reports ==="
Write-Host "JSON: reports/decision_compare.json"
Write-Host "Markdown: reports/decision_compare.md"
Write-Host ""
