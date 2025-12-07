# app/services/diagnosis_service.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.decision_log import _iter_jsonl, _get_decision_log_dir


@dataclass
class DiagnosisParams:
    """診断AIの入力条件（必要になれば拡張する）"""

    profile: str
    start: Optional[date] = None
    end: Optional[date] = None


class DiagnosisService:
    """
    診断AIサービス（v0）

    将来的には以下の情報源を使って分析を行う想定：
      - backtests/{profile}/monthly_returns.csv
      - logs/decisions_*.jsonl
    ここでは API 形だけを整えて、ダミーの結果を返す。
    """

    def analyze(
        self,
        profile: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        診断AIのメインAPI。

        仕様で定義されたキーを必ず返す：
          - time_of_day_stats
          - volatility_stats
          - winning_conditions
          - dd_pre_signal
          - anomalies
        """
        params = DiagnosisParams(profile=profile, start=start, end=end)

        # decisions_*.jsonl を読み込む
        records = self._load_decision_records(start=start, end=end)

        # 時間帯 × 勝率統計を計算
        time_of_day_stats = self._compute_time_of_day_stats(records)

        result: Dict[str, Any] = {
            "profile": params.profile,
            "range": {
                "start": params.start.isoformat() if params.start else None,
                "end": params.end.isoformat() if params.end else None,
            },
            "time_of_day_stats": time_of_day_stats,
            "volatility_stats": {},
            "winning_conditions": {},
            "dd_pre_signal": {},
            "anomalies": [],
        }
        return result

    def _load_decision_records(
        self,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        decisions_*.jsonl を読み込んで、期間でフィルタリングしたレコードを返す。
        """
        log_dir = _get_decision_log_dir()
        decision_files = sorted(log_dir.glob("decisions_*.jsonl"))

        if not decision_files:
            return []

        records: List[Dict[str, Any]] = []
        for file_path in decision_files:
            for entry in _iter_jsonl(file_path):
                # タイムスタンプを取得
                ts_str = entry.get("ts_jst") or entry.get("ts") or entry.get("timestamp")
                if not ts_str:
                    continue

                try:
                    if isinstance(ts_str, str):
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        # tzinfo を削除して naive datetime に
                        if ts.tzinfo is not None:
                            ts = ts.replace(tzinfo=None)
                    else:
                        continue
                except Exception:
                    continue

                # 日付フィルタリング
                if start:
                    if ts.date() < start:
                        continue
                if end:
                    if ts.date() > end:
                        continue

                # pnl または pl フィールドを取得
                pnl = entry.get("pnl") or entry.get("pl")
                if pnl is None:
                    continue

                try:
                    pnl_val = float(pnl)
                except (ValueError, TypeError):
                    continue

                # レコードに timestamp と pl を追加
                record = entry.copy()
                record["timestamp"] = ts
                record["pl"] = pnl_val
                records.append(record)

        return records

    def _compute_time_of_day_stats(self, records: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """
        時間帯ごとの勝率・PF・件数を計算する。

        Returns
        -------
        Dict[int, Dict[str, Any]]
            key: hour (0-23)
            value: {
                "trades": int,
                "win_rate": float,
                "pf": float | None
            }
        """
        stats: Dict[int, Dict[str, int | float]] = {}

        for r in records:
            ts = r.get("timestamp")
            if not ts:
                continue

            hour = ts.hour

            if hour not in stats:
                stats[hour] = {
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "profit_sum": 0.0,
                    "loss_sum": 0.0,
                }

            s = stats[hour]
            s["trades"] += 1

            pl = r.get("pl", 0.0)
            if pl > 0:
                s["wins"] += 1
                s["profit_sum"] += pl
            else:
                s["losses"] += 1
                s["loss_sum"] += abs(pl)

        # 集計結果を整形（勝率・PF）
        result: Dict[int, Dict[str, Any]] = {}
        for hour, s in stats.items():
            win_rate = s["wins"] / s["trades"] if s["trades"] > 0 else 0.0
            pf = (s["profit_sum"] / s["loss_sum"]) if s["loss_sum"] > 0 else None

            result[hour] = {
                "trades": s["trades"],
                "win_rate": win_rate,
                "pf": pf,
            }

        return result


# シングルトン的に使うためのヘルパー
_diagnosis_service: Optional[DiagnosisService] = None


def get_diagnosis_service() -> DiagnosisService:
    global _diagnosis_service
    if _diagnosis_service is None:
        _diagnosis_service = DiagnosisService()
    return _diagnosis_service
