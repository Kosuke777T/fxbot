# app/services/decision_log.py
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

import pandas as pd
import fxbot_path

# プロジェクトルート = app/services/ から 2 つ上
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _PROJECT_ROOT / "logs"
@dataclass
class DecisionRecord:
    """
    decisions_*.jsonl の 1 行を、GUI や KPI 計算から使いやすい形に薄くラップしたもの。

    必要に応じてフィールドは増やせるようにしておく。
    """
    ts_jst: str
    symbol: str
    action: str
    side: Optional[str]
    reason: Optional[str]
    meta: Optional[str]
    blocked: Optional[str]
    raw: dict[str, Any]


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """
    JSONL ファイルを 1 行ずつ dict として返すジェネレータ。壊れた行はスキップ。

    encoding/errors/行フィルタ/型チェックを堅牢化。
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, start=1):
                # 行フィルタ: 空行・空白のみ行をスキップ
                line = line.strip()
                if not line:
                    continue

                # JSON解析
                try:
                    obj = json.loads(line)
                    # schema normalize (ts_jst -> timestamp)
                    if isinstance(obj, dict) and ('timestamp' not in obj) and ('ts_jst' in obj):
                        obj['timestamp'] = obj.get('ts_jst')

                except (json.JSONDecodeError, ValueError) as e:
                    # 壊れた1行があっても全体は止めない（ログは出さない）
                    continue

                # 型チェック: dict でない場合はスキップ
                if not isinstance(obj, dict):
                    continue

                yield obj
    except FileNotFoundError:
        return
    except (UnicodeDecodeError, IOError, OSError):
        # ファイル読み込みエラー（encoding/IO）は静かに終了
        return


def _extract_decision_record(j: dict[str, Any]) -> DecisionRecord:
    """
    JSON を DecisionRecord に薄く変換する（GUI/KPI でよく使う形式）。
    """
    ts = str(j.get("ts_jst") or j.get("ts") or "")
    symbol = str(j.get("symbol") or "")

    decision_raw = j.get("decision")
    if isinstance(decision_raw, dict):
        decision = decision_raw
    elif isinstance(decision_raw, str):
        decision = {"action": decision_raw}
    else:
        decision = {}

    action = str(decision.get("action") or "")

    inner_dec_raw = decision.get("dec")
    if isinstance(inner_dec_raw, dict):
        inner_dec = inner_dec_raw
    else:
        inner_dec = {}

    side = inner_dec.get("side") or decision.get("side")
    if side is not None:
        side = str(side)

    meta = j.get("meta")
    if meta is None:
        if isinstance(decision_raw, dict):
            meta = decision_raw.get("meta")
        elif isinstance(decision_raw, str):
            meta = decision_raw
    if meta is not None:
        meta = str(meta)

    reason = decision.get("reason")
    if reason is not None:
        reason = str(reason)

    filters_raw = j.get("filters") or {}
    filters = filters_raw if isinstance(filters_raw, dict) else {}
    blocked = filters.get("blocked")
    # blocked が None のとき blocked_reason も参照する
    if blocked is None:
        blocked = filters.get("blocked_reason")
    if blocked is not None:
        blocked = str(blocked)

    return DecisionRecord(
        ts_jst=ts,
        symbol=symbol,
        action=action,
        side=side,
        reason=reason,
        meta=meta,
        blocked=blocked,
        raw=j,
    )


def _find_first_numeric_by_keys(
    container: Any,
    key_candidates: tuple[str, ...],
) -> float | None:
    """
    任意にネストした dict/list 構造の中から、
    指定したキー名のいずれかに対応する「最初の数値」を返す。
    見つからなければ None。
    """
    if isinstance(container, Mapping):
        for k in key_candidates:
            if k in container and container[k] is not None:
                try:
                    return float(container[k])
                except (TypeError, ValueError):
                    pass
        for value in container.values():
            val = _find_first_numeric_by_keys(value, key_candidates)
            if val is not None:
                return val
    elif isinstance(container, Sequence) and not isinstance(container, (str, bytes, bytearray)):
        for item in container:
            val = _find_first_numeric_by_keys(item, key_candidates)
            if val is not None:
                return val
    return None


def _ensure_pnl_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    decisions_* の生ログ DataFrame に「pnl 列」が無ければ、
    exit_plan / decision_detail / ai / meta などの dict の中から
    それっぽいキー名を再帰的に探し、最初に見つかった数値を pnl とする。
    見つからない行は NaN のまま。
    """
    if "pnl" in df.columns:
        return df

    KEY_CANDIDATES: tuple[str, ...] = (
        "pnl",
        "profit",
        "pl_jpy",
        "pl",
        "pips",
    )

    TARGET_COLS: tuple[str, ...] = (
        "exit_plan",
        "decision_detail",
        "ai",
        "meta",
    )

    def _row_pnl(row: pd.Series) -> float | None:
        for col_name in TARGET_COLS:
            if col_name not in row:
                continue
            container = row[col_name]
            val = _find_first_numeric_by_keys(container, KEY_CANDIDATES)
            if val is not None:
                return val
        return None

    df = df.copy()
    df["pnl"] = df.apply(_row_pnl, axis=1)
    return df


def _get_decision_log_dir() -> Path:
    """
    決定ログのルートディレクトリを返す。

    例: <project_root>/logs
    """
    root = fxbot_path.get_project_root()
    return root / "logs"
def load_recent_decisions(limit: int | None = None) -> pd.DataFrame:
    """
    decisions_*.jsonl から最新の N レコードを pandas.DataFrame で読み込む。
    """
    log_dir = _get_decision_log_dir()
    files = sorted(log_dir.glob("decisions_*.jsonl"))
    if not files:
        return pd.DataFrame()

    df_list: list[pd.DataFrame] = []
    for f in files:
        try:
            df_list.append(pd.read_json(f, lines=True))
        except Exception:
            continue

    if not df_list:
        return pd.DataFrame()

    df = pd.concat(df_list, ignore_index=True)

    if "ts_jst" in df.columns:
        df = df.sort_values("ts_jst", ascending=False)
    elif "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)

    if limit is not None and limit > 0:
        df = df.head(limit)

    df = _ensure_pnl_column(df)

    return df.reset_index(drop=True)

