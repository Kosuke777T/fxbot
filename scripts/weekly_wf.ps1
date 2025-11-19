# scripts/weekly_wf.ps1
param(
  [double]$MinAuc = 0.55
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSCommandPath)

# 1) Compile
python -m compileall app core scripts | Out-Null

# 2) Train (WF)
python -m scripts.walkforward_train --csv *.csv --weeks-train 156 --weeks-valid 12 --min-auc $MinAuc

# 3) Swap to live (if new READY exists, it will be the latest)
python -m scripts.swap_model

# 4) Smoke (strict) to ensure it runs
powershell -ExecutionPolicy Bypass -File scripts\verify_smoke.ps1 -Mode strict -Ticks 300 -DtMs 20 -AtrPct 0.00080

Write-Host "[WEEKLY] done"
