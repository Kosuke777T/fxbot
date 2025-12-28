param(
  [string]$Symbol = "USDJPY-",
  [string]$OutJson = "logs/condition_mining_ops_snapshot.json"
)

$ErrorActionPreference = "Stop"

# このスクリプトの場所からプロジェクトルートへ移動（D:\fxbot を想定）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
Set-Location -Path $repoRoot

# import app を確実に通す（Temp 配下 .py 実行で sys.path がズレるため）
$env:PYTHONPATH = "$repoRoot;$($env:PYTHONPATH)"

# venv の python を優先（無ければ PATH の python）
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) { $pythonExe = "python" }

# logs/ が無ければ作る
$logsDir = Split-Path $OutJson -Parent
if ($logsDir -and !(Test-Path $logsDir)) {
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
}

# Python を一時ファイルに書いて実行（Here-String 地雷回避）
$py = Join-Path $env:TEMP "cm_ops_snapshot_smoke.py"

Set-Content -Path $py -Encoding UTF8 -Value @"
import json
from app.services.condition_mining_facade import get_condition_mining_ops_snapshot

symbol = r"$Symbol"
out = get_condition_mining_ops_snapshot(symbol)

required = ["warnings","ops_cards_first","evidence","evidence_kind","evidence_src","symbol"]
missing = [k for k in required if k not in out]
if missing:
    raise SystemExit(f"[NG] missing keys: {missing}")

if not isinstance(out.get("warnings"), list):
    raise SystemExit("[NG] warnings is not list")
if not isinstance(out.get("ops_cards_first"), list):
    raise SystemExit("[NG] ops_cards_first is not list")

print("[OK] snapshot keys:", required)
print("symbol=", out.get("symbol"))
print("warnings=", out.get("warnings"))

cards = out.get("ops_cards_first") or []
if cards:
    c0 = cards[0] or {}
    print("ops_cards_first_n=", len(cards))
    print("title=", c0.get("title"))
    print("summary=", c0.get("summary"))
else:
    print("ops_cards_first_n= 0")

print("evidence_kind=", out.get("evidence_kind"))
print("evidence_src=", out.get("evidence_src"))

out_path = r"$OutJson"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("[OK] wrote:", out_path)
"@

& $pythonExe -X utf8 $py
