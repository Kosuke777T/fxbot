# tools/analyze_virtualbt_entry.ps1
# VirtualBT 取引頻度分析ツールのラッパー（PowerShell 7前提）

param(
    [string]$Root = "",
    [string]$Decisions = "",
    [string]$Thresholds = "",
    [string]$FilterLevels = "",
    [int]$Limit = 0,
    [string]$Csv = ""
)

$ErrorActionPreference = "Stop"

# プロジェクトルートを取得
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Python 実行
$PythonArgs = @()

if ($Root) {
    $PythonArgs += "--root", $Root
} else {
    $PythonArgs += "--root", $ProjectRoot
}

if ($Decisions) {
    $PythonArgs += "--decisions", $Decisions
}

if ($Thresholds) {
    $PythonArgs += "--thresholds", $Thresholds
}

if ($FilterLevels) {
    $PythonArgs += "--filter-levels", $FilterLevels
}

if ($Limit -gt 0) {
    $PythonArgs += "--limit", $Limit
}

if ($Csv) {
    $PythonArgs += "--csv", $Csv
}

$ScriptPath = Join-Path $ScriptDir "analyze_virtualbt_entry.py"

Write-Host "[INFO] 実行: python $ScriptPath $($PythonArgs -join ' ')" -ForegroundColor Cyan

python $ScriptPath @PythonArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 実行失敗 (exit_code=$LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}
