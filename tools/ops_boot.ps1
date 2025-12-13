# tools/ops_boot.ps1
# 運用起動 共通エントリ（判定→起動→最小監視/ログ）
# PS7 前提

param(
  [string]$Symbol = "USDJPY-",

  # ops_start 側に渡す（存在しない引数でもPS側では通るが、ops_startがparamで受けない場合は外してOK）
  [int]$Dry = 0,
  [int]$CloseNow = 1,

  # リトライ/監視
  [int]$RetrySec = 300,     # 市場待ち時の再判定間隔
  [int]$WatchSec = 10,      # 本体プロセス監視間隔

  # 1回だけ判定して終了（CI/動作確認用）
  [switch]$Once,

  # 本体起動（デフォルト：GUI本体）
  [string]$PythonExe = "python",
  [string]$MainModule = "app.gui.main",
  [string[]]$MainArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---- paths
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logsDir = Join-Path $root "logs\ops"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$today = Get-Date -Format "yyyyMMdd"
$logFile = Join-Path $logsDir ("ops_boot_{0}.jsonl" -f $today)

function Write-JsonlLine([hashtable]$obj) {
  $json = ($obj | ConvertTo-Json -Compress -Depth 10)
  Add-Content -Path $logFile -Value $json -Encoding UTF8
}

function NowIso() {
  return (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fffK")
}

# Ctrl+C でもログに残す
$script:mainProc = $null
$null = Register-EngineEvent -SourceIdentifier ConsoleBreak -Action {
  try {
    Write-JsonlLine @{
      ts = NowIso
      type = "ops_boot"
      symbol = $using:Symbol
      event = "interrupt"
      pid = if ($script:mainProc) { $script:mainProc.Id } else { $null }
    }
  } catch {}
}

Write-JsonlLine @{
  ts = NowIso
  type = "ops_boot"
  symbol = $Symbol
  event = "start"
  retry_sec = $RetrySec
  watch_sec = $WatchSec
  once = [bool]$Once
}

while ($true) {

  # ---- 判定：ops_start
  $opsStart = Join-Path $PSScriptRoot "ops_start.ps1"
  if (-not (Test-Path $opsStart)) {
    Write-JsonlLine @{
      ts = NowIso
      type = "ops_boot"
      symbol = $Symbol
      event = "ops_start_missing"
      path = $opsStart
    }
    exit 30
  }

  # ここは「既存ops_startのparamに合わせて調整可能」
  $out = & $opsStart -Symbol $Symbol -Dry $Dry -CloseNow $CloseNow 2>$null
  $rc = $LASTEXITCODE

  $parsed = $null
  try {
    if ($out) {
      $text = if ($out -is [System.Array]) { ($out -join "") } else { [string]$out }
      $parsed = $text | ConvertFrom-Json -ErrorAction Stop
    }
  } catch {
    $parsed = $null
  }

  Write-JsonlLine @{
    ts = NowIso
    type = "ops_boot"
    symbol = $Symbol
    event = "ops_start_result"
    rc = $rc
    status = if ($parsed) { $parsed.status } else { $null }
    error_code = if ($parsed -and $parsed.smoke -and $parsed.smoke.error) { $parsed.smoke.error.code } else { $null }
    message = if ($parsed -and $parsed.smoke -and $parsed.smoke.error) { $parsed.smoke.error.message } else { $null }
  }

  if ($Once) {
    exit $rc
  }

  switch ($rc) {

    0 {
      # ---- 起動（本体）
      $args = @("-m", $MainModule) + $MainArgs

      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "launch_main"
        python = $PythonExe
        module = $MainModule
        args = $MainArgs
      }

      $script:mainProc = Start-Process -FilePath $PythonExe -ArgumentList $args -PassThru -WindowStyle Normal

      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "main_started"
        pid = $script:mainProc.Id
      }

      # ---- 監視（最小）
      while ($true) {
        Start-Sleep -Seconds $WatchSec

        $p = Get-Process -Id $script:mainProc.Id -ErrorAction SilentlyContinue
        if (-not $p) {
          Write-JsonlLine @{
            ts = NowIso
            type = "ops_boot"
            symbol = $Symbol
            event = "main_exited"
            pid = $script:mainProc.Id
          }
          $script:mainProc = $null
          break
        }

        # heartbeat（重くしない）
        Write-JsonlLine @{
          ts = NowIso
          type = "ops_boot"
          symbol = $Symbol
          event = "heartbeat"
          pid = $script:mainProc.Id
        }
      }

      # 本体が落ちたら「再判定」に戻る（市場状態や設定を再チェック）
      continue
    }

    10 {
      # 市場待ち：定期再判定
      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "market_wait"
        sleep_sec = $RetrySec
      }
      Start-Sleep -Seconds $RetrySec
      continue
    }

    20 {
      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "config_invalid"
        rc = $rc
      }
      exit 20
    }

    default {
      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "abnormal"
        rc = $rc
      }
      exit 30
    }
  }
}

