"""
tools/list_wfo_reports.py

Walkforward / 再学習レポート (logs/retrain/report_*.json) の一覧を表示するツール。

目的:
- WFO レポートファイルの「場所」と「最低限の中身」を確認する
- GUI から参照するときの前提を揃える
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import fxbot_path
except ImportError:
    fxbot_path = None


@dataclass
class WFOReportSummary:
    path: Path
    id: str
    created_at: datetime | None
    symbol: str | None
    timeframe: str | None
    label_horizon: int | None
    pf: float | None
    max_dd: float | None
    sharpe: float | None
    win_rate: float | None

    @classmethod
    def from_json(cls, path: Path, data: dict[str, Any]) -> WFOReportSummary:
        # 1) ID
        rid = str(data.get("id") or path.stem.replace("report_", ""))

        # 2) created_at
        created_raw = data.get("created_at")
        created_at: datetime | None = None
        if isinstance(created_raw, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    created_at = datetime.strptime(created_raw, fmt)
                    break
                except ValueError:
                    continue

        # 3) top-level keys
        symbol = data.get("symbol")
        timeframe = data.get("timeframe")
        label_horizon = data.get("label_horizon")

        # 4) metrics
        metrics = data.get("metrics") or {}
        pf = metrics.get("pf")
        max_dd = metrics.get("max_dd")
        sharpe = metrics.get("sharpe")
        win_rate = metrics.get("win_rate")

        return cls(
            path=path,
            id=rid,
            created_at=created_at,
            symbol=symbol,
            timeframe=timeframe,
            label_horizon=label_horizon,
            pf=pf,
            max_dd=max_dd,
            sharpe=sharpe,
            win_rate=win_rate,
        )


def get_project_root() -> Path:
    """fxbot_path があればそれを使い、なければカレントから推測。"""
    if fxbot_path is not None and hasattr(fxbot_path, "get_project_root"):
        return Path(fxbot_path.get_project_root())
    # フォールバック：このファイルの親の親をルートとみなす
    return Path(__file__).resolve().parents[1]


def find_wfo_reports(root: Path | None = None) -> list[WFOReportSummary]:
    if root is None:
        root = get_project_root()

    logs_retrain = root / "logs" / "retrain"
    if not logs_retrain.exists():
        print(f"[WARN] logs/retrain/ が見つかりません: {logs_retrain}")
        return []

    summaries: list[WFOReportSummary] = []
    for path in sorted(logs_retrain.glob("report_*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            summary = WFOReportSummary.from_json(path, data)
            summaries.append(summary)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {path} の読み込みに失敗しました: {e}")

    return summaries


def print_table(reports: list[WFOReportSummary]) -> None:
    if not reports:
        print("[INFO] WFO レポートが見つかりませんでした。")
        return

    # ヘッダ
    header = [
        "idx",
        "id",
        "created_at",
        "symbol",
        "tf",
        "horizon",
        "PF",
        "MaxDD",
        "WinRate",
        "path",
    ]
    print("\t".join(header))

    for idx, r in enumerate(reports, start=1):
        created_str = (
            r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "-"
        )
        row = [
            str(idx),
            r.id,
            created_str,
            r.symbol or "-",
            r.timeframe or "-",
            str(r.label_horizon) if r.label_horizon is not None else "-",
            f"{r.pf:.3f}" if isinstance(r.pf, (int, float)) else "-",
            f"{r.max_dd:.3f}" if isinstance(r.max_dd, (int, float)) else "-",
            f"{r.win_rate:.3f}" if isinstance(r.win_rate, (int, float)) else "-",
            str(r.path.relative_to(get_project_root())),
        ]
        print("\t".join(row))


def main() -> None:
    root = get_project_root()
    print(f"[INFO] project_root = {root}")
    reports = find_wfo_reports(root)
    print_table(reports)


if __name__ == "__main__":
    main()
