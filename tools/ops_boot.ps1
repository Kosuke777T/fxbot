# tools/ops_boot.ps1
# 運用起動 共通エントリ（判定→起動→最小監視/ログ）
# PS7 前提

param(
  [string]$Symbol = "USDJPY-",

  # ops_start 側に渡す（存在しない引数でもPS側では通るが、ops_startがparamで受けない場合は外してOK）
  [int]$Dry = 0,
  [int]$CloseNow = 1,

  # リトライ/監視
  [int]$RetrySec = 300,     # 市場待ち時の再判定間隔（未使用、error.code で決定）
  [int]$WatchSec = 10,      # 本体プロセス監視間隔

  # 1回だけ判定して終了（CI/動作確認用）
  [switch]$Once,

  # ループ実行（デフォルト: 1回のみ）
  [switch]$Loop,

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

# ---- ログ関数（Mutex ブロックより前に定義）
function NowIso() {
  return (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fffK")
}

function Write-JsonlLine([hashtable]$obj) {
  $json = ($obj | ConvertTo-Json -Compress -Depth 10)
  Add-Content -Path $logFile -Value $json -Encoding UTF8
}

# ---- 二重起動防止（Mutex）
$mutexName = "Global\fxbot_ops_boot_$Symbol"
$mutex = $null
try {
  $mutex = New-Object System.Threading.Mutex($false, $mutexName)
  if (-not $mutex.WaitOne(0)) {
    # 既に起動中（ログは1回だけ）
    $logObj = @{
      ts = NowIso
      type = "ops_boot"
      symbol = $Symbol
      event = "already_running"
      mutex_name = $mutexName
    }
    if (-not (Get-Command Write-JsonlLine -ErrorAction SilentlyContinue)) {
      $line = ($logObj | ConvertTo-Json -Compress -Depth 10)
      Add-Content -Path $logFile -Value $line -Encoding UTF8
    } else {
      Write-JsonlLine $logObj
    }
    # exit 前に Mutex を解放
    try {
      $mutex.ReleaseMutex()
      $mutex.Dispose()
    } catch {}
    exit 0
  }
} catch {
  $logObj = @{
    ts = NowIso
    type = "ops_boot"
    symbol = $Symbol
    event = "mutex_error"
    error = $_.Exception.Message
  }
  if (-not (Get-Command Write-JsonlLine -ErrorAction SilentlyContinue)) {
    $line = ($logObj | ConvertTo-Json -Compress -Depth 10)
    Add-Content -Path $logFile -Value $line -Encoding UTF8
  } else {
    Write-JsonlLine $logObj
  }
  # exit 前に Mutex を解放（取得できた場合）
  if ($mutex) {
    try {
      $mutex.ReleaseMutex()
      $mutex.Dispose()
    } catch {}
  }
  exit 30
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
  loop = [bool]$Loop
}

$retryCount = 0
$maxRetries = if ($Loop) { [int]::MaxValue } else { 1 }

while ($retryCount -lt $maxRetries) {
  $retryCount++

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
  # Start-Process で stdout/stderr をファイルへ保存
  $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $stdoutFile = Join-Path $logsDir ("ops_start_{0}_{1}_stdout.log" -f $Symbol, $timestamp)
  $stderrFile = Join-Path $logsDir ("ops_start_{0}_{1}_stderr.log" -f $Symbol, $timestamp)

  # PowerShell 7 (pwsh) のパスを取得
  try {
    $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
  } catch {
    Write-JsonlLine @{
      ts = NowIso
      type = "ops_boot"
      symbol = $Symbol
      event = "pwsh_not_found"
      error = "pwsh command not found"
    }
    exit 30
  }

  Write-JsonlLine @{
    ts = NowIso
    type = "ops_boot"
    symbol = $Symbol
    event = "start_process"
    stdout_path = $stdoutFile
    stderr_path = $stderrFile
    pwsh_path = $pwsh
  }

  $proc = Start-Process -FilePath $pwsh `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $opsStart, "-Symbol", $Symbol, "-Dry", $Dry, "-CloseNow", $CloseNow) `
    -RedirectStandardOutput $stdoutFile `
    -RedirectStandardError $stderrFile `
    -Wait -PassThru -NoNewWindow

  $rc = $proc.ExitCode

  # stdout の最初の1行を JSON としてパース
  $parsed = $null
  $stdoutFirstLine = ""
  $stderrHead = ""
  try {
    if (Test-Path $stdoutFile) {
      $stdoutContent = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
      if ($stdoutContent) {
        $lines = $stdoutContent -split "`r?`n"
        if ($lines.Count -gt 0) {
          $stdoutFirstLine = $lines[0].Trim()
          if ($stdoutFirstLine) {
            $parsed = $stdoutFirstLine | ConvertFrom-Json -ErrorAction Stop
          }
        }
      }
    }
  } catch {
    $parsed = $null
  }

  # stderr の先頭最大 2000 文字を取得
  try {
    if (Test-Path $stderrFile) {
      $stderrContent = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
      if ($stderrContent) {
        $stderrHead = $stderrContent.Substring(0, [Math]::Min(2000, $stderrContent.Length))
      }
    }
  } catch {
    $stderrHead = ""
  }

  Write-JsonlLine @{
    ts = NowIso
    type = "ops_boot"
    symbol = $Symbol
    event = "ops_start_result"
    rc = $rc
    stdout_path = $stdoutFile
    stderr_path = $stderrFile
    stderr_head = $stderrHead
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
      # error.code で sleep 秒を決定
      $sleepSec = 900  # MARKET_CLOSED のデフォルト
      if ($parsed -and $parsed.smoke -and $parsed.smoke.error) {
        $errorCode = $parsed.smoke.error.code
        if ($errorCode -eq "MARKET_CLOSED") {
          $sleepSec = 900
        } elseif ($errorCode -eq "TRADE_DISABLED") {
          $sleepSec = 3600
        } else {
          $sleepSec = 60
        }
      }

      Write-JsonlLine @{
        ts = NowIso
        type = "ops_boot"
        symbol = $Symbol
        event = "market_wait"
        sleep_sec = $sleepSec
        error_code = if ($parsed -and $parsed.smoke -and $parsed.smoke.error) { $parsed.smoke.error.code } else { $null }
      }

      if ($Loop) {
        Start-Sleep -Seconds $sleepSec
        continue
      } else {
        exit $rc
      }
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
        error_code = if ($parsed -and $parsed.smoke -and $parsed.smoke.error) { $parsed.smoke.error.code } else { $null }
      }
      exit 30
    }
  }
} finally {
  # Mutex を解放（早期 exit 時も確実に解放）
  if ($mutex) {
    try {
      $mutex.ReleaseMutex()
    } catch {
      # 既に解放済みなどは無視
    }
    try {
      $mutex.Dispose()
    } catch {
      # 既に破棄済みなどは無視
    }
  }
}

