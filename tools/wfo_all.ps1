# tools/wfo_all.ps1
# Walk-Forward Optimization (WFO) を全プロファイルに対して一括実行するツール
#
# 使い方:
#   # 全プロファイルでWFO実行（既定値使用）
#   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\wfo_all.ps1
#
#   # 特定プロファイルのみ実行
#   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\wfo_all.ps1 -Profiles "michibiki_std"
#
#   # カスタムパラメータ指定
#   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\wfo_all.ps1 `
#     -Symbol "USDJPY-" -Timeframe "M5" `
#     -StartDate "2024-01-01" -EndDate "2024-12-31" `
#     -Capital 100000 -TrainRatio 0.7 `
#     -OutRoot "logs\wfo_all"
#
# 確認コマンド（最小スモーク）:
#   # 1プロファイル、短期間で実行
#   pwsh -NoProfile -ExecutionPolicy Bypass -File tools\wfo_all.ps1 `
#     -Profiles "michibiki_std" `
#     -StartDate "2024-12-01" -EndDate "2024-12-31" `
#     -OutRoot "logs\wfo_all_smoke"
#
#   # 成果物確認
#   python -X utf8 tools\list_wfo_reports.py
#
#   # Python import チェック
#   python -X utf8 -c "from app.core.strategy_profile import list_profiles; from tools.backtest_run import run_wfo; print('OK')"

param(
    [string]$Symbol = "USDJPY-",
    [string]$Timeframe = "M5",
    [string[]]$Profiles = @(),  # 空の場合は全プロファイル
    [string]$StartDate = $null,
    [string]$EndDate = $null,
    [string]$Start = $null,  # エイリアス: -StartDate の代替
    [string]$End = $null,    # エイリアス: -EndDate の代替
    [double]$Capital = 100000.0,
    [double]$TrainRatio = 0.7,
    [string]$OutRoot = "logs\wfo_all",
    [int]$MaxRuns = 0,  # 0=無制限
    [switch]$ContinueOnError = $false
)

# エイリアス処理: -Start / -End を -StartDate / -EndDate にマッピング
if ($Start -and -not $StartDate) {
    $StartDate = $Start
}
if ($End -and -not $EndDate) {
    $EndDate = $End
}

$ErrorActionPreference = "Stop"

# プロジェクトルートに移動
$root = Split-Path -Parent $PSCommandPath
Set-Location (Join-Path $root "..")

$python = "python"

Write-Host "=== WFO All Profiles Runner ===" -ForegroundColor Cyan
Write-Host ""

# Python環境確認
Write-Host "=== Python Environment ===" -ForegroundColor Yellow
try {
    $pyExe = python -c "import sys; print(sys.executable)" 2>&1
    $pyVer = python -c "import sys; print(sys.version.replace('\n',' '))" 2>&1
    Write-Host "python executable : $pyExe"
    Write-Host "python version    : $pyVer"
} catch {
    Write-Host "ERROR: Python環境の確認に失敗しました" -ForegroundColor Red
    throw
}
Write-Host "===========================" -ForegroundColor Yellow
Write-Host ""

# プロファイル一覧取得
Write-Host "=== Profile List ===" -ForegroundColor Yellow
$profileListScript = @"
import sys
from pathlib import Path
import os
# python -c では __file__ が使えないので、カレントディレクトリから取得
PROJECT_ROOT = Path(os.getcwd()).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.core.strategy_profile import list_profiles
profiles = list_profiles()
for name in sorted(profiles.keys()):
    print(name)
"@

$allProfiles = @()
try {
    $profileOutput = python -X utf8 -c $profileListScript 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: プロファイル一覧の取得に失敗しました" -ForegroundColor Red
        $profileOutput | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        throw "Failed to list profiles"
    }
    $allProfiles = $profileOutput | Where-Object { $_ -match '^\w+' } | ForEach-Object { $_.Trim() }
    Write-Host "利用可能なプロファイル: $($allProfiles -join ', ')"
} catch {
    Write-Host "ERROR: プロファイル一覧の取得に失敗しました: $_" -ForegroundColor Red
    throw
}

# 実行対象プロファイルを決定
$targetProfiles = @()
if ($Profiles.Count -eq 0) {
    $targetProfiles = $allProfiles
    Write-Host "実行対象: 全プロファイル ($($targetProfiles.Count)件)"
} else {
    foreach ($p in $Profiles) {
        if ($allProfiles -contains $p) {
            $targetProfiles += $p
        } else {
            Write-Host "WARN: 未知のプロファイル '$p' をスキップします" -ForegroundColor Yellow
        }
    }
    Write-Host "実行対象: 指定プロファイル ($($targetProfiles.Count)件): $($targetProfiles -join ', ')"
}

if ($targetProfiles.Count -eq 0) {
    Write-Host "ERROR: 実行対象のプロファイルがありません" -ForegroundColor Red
    exit 1
}

# MaxRuns制限
if ($MaxRuns -gt 0 -and $targetProfiles.Count -gt $MaxRuns) {
    Write-Host "INFO: MaxRuns=$MaxRuns により、最初の $MaxRuns プロファイルのみ実行します" -ForegroundColor Yellow
    $targetProfiles = $targetProfiles[0..($MaxRuns-1)]
}

Write-Host "===========================" -ForegroundColor Yellow
Write-Host ""

# データCSV探索（プロファイルごとに異なるシンボル/タイムフレームに対応）
Write-Host "=== Data CSV Search ===" -ForegroundColor Yellow
$csvFindScript = @"
import sys
from pathlib import Path
import os
# python -c では __file__ が使えないので、カレントディレクトリから取得
PROJECT_ROOT = Path(os.getcwd()).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import fxbot_path
from app.core.strategy_profile import get_profile

def find_csv_for_profile(profile_name, default_symbol, default_timeframe):
    try:
        profile = get_profile(profile_name)
        symbol_tag = profile.symbol.rstrip('-')
        timeframe = profile.timeframe
    except:
        # プロファイル取得失敗時は既定値を使用
        symbol_tag = default_symbol.rstrip('-')
        timeframe = default_timeframe
    
    csv_path = fxbot_path.get_ohlcv_csv_path(symbol_tag, timeframe, layout='per-symbol')
    if csv_path.exists():
        return str(csv_path)
    # flat layout も試す
    csv_path = fxbot_path.get_ohlcv_csv_path(symbol_tag, timeframe, layout='flat')
    if csv_path.exists():
        return str(csv_path)
    return None

# 最初のプロファイルでCSVを探索（全プロファイル共通の場合は1回だけ）
default_symbol = '$Symbol'
default_timeframe = '$Timeframe'
csv_path = find_csv_for_profile('$($targetProfiles[0])', default_symbol, default_timeframe)
if csv_path:
    print(csv_path)
else:
    sys.exit(1)
"@

$dataCsv = $null
try {
    $csvOutput = python -X utf8 -c $csvFindScript 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: データCSVが見つかりませんでした (Symbol=$Symbol, Timeframe=$Timeframe)" -ForegroundColor Red
        $csvOutput | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        throw "Data CSV not found"
    }
    $dataCsv = ($csvOutput | Where-Object { $_ -match '\.csv$' } | Select-Object -First 1).Trim()
    if (-not $dataCsv -or -not (Test-Path $dataCsv)) {
        throw "CSV path is invalid: $dataCsv"
    }
    Write-Host "データCSV: $dataCsv"
} catch {
    Write-Host "ERROR: データCSVの探索に失敗しました: $_" -ForegroundColor Red
    throw
}
Write-Host "===========================" -ForegroundColor Yellow
Write-Host ""

# 実行結果を記録する配列
$results = @()

# 各プロファイルに対してWFO実行
Write-Host "=== WFO Execution ===" -ForegroundColor Cyan
Write-Host ""

$outRootPath = [System.IO.Path]::GetFullPath($OutRoot)
New-Item -ItemType Directory -Force -Path $outRootPath | Out-Null

$profileIndex = 0
foreach ($profile in $targetProfiles) {
    $profileIndex++
    Write-Host "[$profileIndex/$($targetProfiles.Count)] Processing profile: $profile" -ForegroundColor Yellow
    
    # 出力ディレクトリを一意化（日時+profile）
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDirName = "${timestamp}_${profile}"
    $outDir = Join-Path $outRootPath $outDirName
    
    # WFO実行
    $wfoScript = @"
import sys
from pathlib import Path
import os
# python -c では __file__ が使えないので、カレントディレクトリから取得
PROJECT_ROOT = Path(os.getcwd()).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from tools.backtest_run import run_wfo
data_csv = Path(r'$dataCsv')
out_dir = Path(r'$outDir')
start = '$StartDate' if '$StartDate' else None
end = '$EndDate' if '$EndDate' else None
capital = $Capital
train_ratio = $TrainRatio
try:
    result = run_wfo(data_csv, start, end, capital, out_dir, train_ratio=train_ratio)
    print(f'SUCCESS: {result}')
    sys.exit(0)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
"@

    $success = $false
    $errorMsg = $null
    
    try {
        $wfoOutput = python -X utf8 -c $wfoScript 2>&1
        $exitCode = $LASTEXITCODE
        
        if ($exitCode -eq 0) {
            $success = $true
            Write-Host "  ✓ SUCCESS: $profile" -ForegroundColor Green
            $wfoOutput | Where-Object { $_ -match '^\[wfo\]|^SUCCESS:' } | ForEach-Object {
                Write-Host "    $_" -ForegroundColor Gray
            }
        } else {
            $errorMsg = "Exit code: $exitCode"
            Write-Host "  ✗ FAILED: $profile" -ForegroundColor Red
            $wfoOutput | ForEach-Object {
                Write-Host "    $_" -ForegroundColor Red
            }
        }
    } catch {
        $errorMsg = $_.Exception.Message
        Write-Host "  ✗ EXCEPTION: $profile - $errorMsg" -ForegroundColor Red
    }
    
    # 成果物検査
    $artifacts = @{}
    if ($success -and (Test-Path $outDir)) {
        # metrics_wfo.json
        $metricsJson = Join-Path $outDir "metrics_wfo.json"
        if (Test-Path $metricsJson) {
            $artifacts["metrics_wfo.json"] = $true
        } else {
            $artifacts["metrics_wfo.json"] = $false
        }
        
        # equity_curve.csv
        $equityCsv = Join-Path $outDir "equity_curve.csv"
        if (Test-Path $equityCsv) {
            $artifacts["equity_curve.csv"] = $true
        } else {
            $artifacts["equity_curve.csv"] = $false
        }
        
        # equity_train.csv, equity_test.csv
        $equityTrain = Join-Path $outDir "equity_train.csv"
        $equityTest = Join-Path $outDir "equity_test.csv"
        if (Test-Path $equityTrain) { $artifacts["equity_train.csv"] = $true } else { $artifacts["equity_train.csv"] = $false }
        if (Test-Path $equityTest) { $artifacts["equity_test.csv"] = $true } else { $artifacts["equity_test.csv"] = $false }
        
        # monthly_returns.csv
        $monthlyCsv = Join-Path $outDir "monthly_returns.csv"
        if (Test-Path $monthlyCsv) {
            $artifacts["monthly_returns.csv"] = $true
        } else {
            $artifacts["monthly_returns.csv"] = $false
        }
        
        $missing = $artifacts.Keys | Where-Object { -not $artifacts[$_] }
        if ($missing.Count -gt 0) {
            Write-Host "  WARN: 成果物が不足しています: $($missing -join ', ')" -ForegroundColor Yellow
        } else {
            Write-Host "  ✓ 成果物確認OK" -ForegroundColor Green
        }
    }
    
    # 結果を記録
    $results += [PSCustomObject]@{
        Profile = $profile
        Success = $success
        OutDir = $outDir
        ErrorMsg = $errorMsg
        Artifacts = $artifacts
    }
    
    # エラー時はContinueOnErrorフラグを確認
    if (-not $success -and -not $ContinueOnError) {
        Write-Host ""
        Write-Host "ERROR: プロファイル '$profile' の実行に失敗しました（ContinueOnError=false のため中断）" -ForegroundColor Red
        break
    }
    
    Write-Host ""
}

# 実行結果サマリー
Write-Host "=== Execution Summary ===" -ForegroundColor Cyan
Write-Host ""

$successCount = ($results | Where-Object { $_.Success }).Count
$failCount = ($results | Where-Object { -not $_.Success }).Count

Write-Host "成功: $successCount / 失敗: $failCount / 合計: $($results.Count)" -ForegroundColor $(if ($failCount -eq 0) { "Green" } else { "Yellow" })
Write-Host ""

# 成功したプロファイル
if ($successCount -gt 0) {
    Write-Host "✓ 成功したプロファイル:" -ForegroundColor Green
    foreach ($r in $results | Where-Object { $_.Success }) {
        Write-Host "  - $($r.Profile): $($r.OutDir)" -ForegroundColor Green
    }
    Write-Host ""
}

# 失敗したプロファイル
if ($failCount -gt 0) {
    Write-Host "✗ 失敗したプロファイル:" -ForegroundColor Red
    foreach ($r in $results | Where-Object { -not $_.Success }) {
        Write-Host "  - $($r.Profile): $($r.ErrorMsg)" -ForegroundColor Red
        Write-Host "    OutDir: $($r.OutDir)" -ForegroundColor Gray
    }
    Write-Host ""
}

# 終了コード
if ($failCount -gt 0) {
    Write-Host "=== WFO All: 一部失敗 ===" -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "=== WFO All: 全成功 ===" -ForegroundColor Green
    exit 0
}

