param(
  [string]$Symbol = "USDJPY-",
  [string]$OutJson = "logs/condition_mining_ops_snapshot.json",
  # Step2-F: optional overrides (defaults kept if not specified)
  [Nullable[int]]$Tail = $null,
  [Nullable[int]]$RecentMinutes = $null,
  [Nullable[int]]$PastMinutes = $null,
  [Nullable[int]]$Offset = $null
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

$tailVal   = if ($Tail -ne $null) { $Tail } else { "None" }
$recentVal = if ($RecentMinutes -ne $null) { $RecentMinutes } else { "None" }
$pastVal   = if ($PastMinutes -ne $null) { $PastMinutes } else { "None" }
$offsetVal = if ($Offset -ne $null) { $Offset } else { "None" }

Write-Host ("[cm_smoke] tail={0} symbol={1} recent_minutes={2} past_minutes={3} offset={4}" -f $tailVal,$Symbol,$recentVal,$pastVal,$offsetVal) -ForegroundColor DarkCyan

Set-Content -Path $py -Encoding UTF8 -Value @"
import json
from app.services.condition_mining_facade import get_condition_mining_ops_snapshot

symbol = r"$Symbol"
tail = $tailVal
recent = $recentVal
past = $pastVal
offset = $offsetVal

kwargs = {}
if tail is not None:
    kwargs["max_decisions"] = int(tail)
if recent is not None:
    kwargs["recent_minutes"] = int(recent)
if past is not None:
    kwargs["past_minutes"] = int(past)
if offset is not None:
    kwargs["past_offset_minutes"] = int(offset)

out = get_condition_mining_ops_snapshot(symbol, **kwargs)

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

# --- T-43-4: TOP10 candidates view (description must be present) ---
cands = out.get("candidates") or out.get("condition_candidates") or []
if not isinstance(cands, list):
    cands = []
top = cands[:10]
print("top_candidates_n=", len(top))
missing_desc = 0
for i, c in enumerate(top, start=1):
    if not isinstance(c, dict):
        continue
    cond = c.get("condition") if isinstance(c.get("condition"), dict) else {}
    cid = c.get("id") or cond.get("id")
    desc = c.get("description") or cond.get("description") or ""
    if not isinstance(desc, str) or not desc.strip():
        missing_desc += 1
    support = c.get("support")
    score = c.get("weight", c.get("score"))
    conf = c.get("condition_confidence")
    degr = c.get("degradation")
    print(f"{i:02d}. {cid} | {desc} | support={support} score={score} conf={conf} degr={degr}")

if top and missing_desc:
    raise SystemExit(f"[NG] TOP10 description missing: {missing_desc}/{len(top)}")
print("[OK] TOP10 description present")
print("# --- /T-43-4 ---")

out_path = r"$OutJson"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("[OK] wrote:", out_path)
"@

& $pythonExe -X utf8 $py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
