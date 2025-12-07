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

    def analyze(self, profile: str = "std", start=None, end=None) -> dict:
        """
        診断AI v0:
        - time_of_day_stats: 時間帯ごとの成績
        - winning_conditions: 全体成績＋勝ちやすい時間帯（v0は時間帯ベースのみ）
        """
        # --- 日付パラメータを date 型に正規化 ---
        def _to_date(value):
            if value is None:
                return None
            if isinstance(value, date) and not isinstance(value, datetime):
                return value
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value).date()
                except ValueError:
                    return None
            return None

        start_date = _to_date(start)
        end_date = _to_date(end)

        # --- ログ読み込み＆集計 ---
        records = self._load_decision_records(start=start_date, end=end_date)
        time_of_day_stats = self._compute_time_of_day_stats(records)
        winning_conditions = self._compute_winning_conditions(records, time_of_day_stats)
        dd_pre_signal = self._compute_dd_pre_signal(records)

        # --- 結果整形 ---
        return {
            "profile": profile,
            "range": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None,
            },
            "time_of_day_stats": time_of_day_stats,
            "volatility_stats": {},      # ここは後のSTEPで実装
            "winning_conditions": winning_conditions,
            "dd_pre_signal": dd_pre_signal,
            "anomalies": [],
        }

    def _load_decision_records(
        self,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        decisions_*.jsonl を読み込んで、期間でフィルタリングしたレコードを返す。
        """
        # start と end を date 型に正規化
        start_date = None
        end_date = None

        # start を date 型に正規化
        if isinstance(start, str):
            try:
                start_date = datetime.fromisoformat(start).date()
            except ValueError:
                start_date = None
        elif isinstance(start, datetime):
            start_date = start.date()
        elif isinstance(start, date):
            start_date = start

        # end を date 型に正規化
        if isinstance(end, str):
            try:
                end_date = datetime.fromisoformat(end).date()
            except ValueError:
                end_date = None
        elif isinstance(end, datetime):
            end_date = end.date()
        elif isinstance(end, date):
            end_date = end

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
                if start_date is not None and ts.date() < start_date:
                    continue
                if end_date is not None and ts.date() > end_date:
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

    def _compute_winning_conditions(self, records: List[Dict[str, Any]], time_of_day_stats: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        """
        全体の勝率と PF、時間帯ベースの「勝ちやすい条件」をまとめる v0 ロジック。

        Parameters
        ----------
        records: List[Dict[str, Any]]
            _load_decision_records() が返すレコードのリスト
        time_of_day_stats: Dict[int, Dict[str, Any]]
            _compute_time_of_day_stats() の戻り値

        Returns
        -------
        Dict[str, Any]
            {
                "total_trades": int,
                "global_win_rate": float,
                "global_pf": float | None,
                "min_trades_per_bucket": int,
                "best_hours": List[Dict[str, Any]]
            }
        """
        if not records:
            return {}

        # --- 全体サマリ ---
        total_trades = 0
        wins = 0
        losses = 0
        profit_sum = 0.0
        loss_sum = 0.0

        for r in records:
            pl = float(r.get("pl", 0.0))
            total_trades += 1
            if pl > 0:
                wins += 1
                profit_sum += pl
            elif pl < 0:
                losses += 1
                loss_sum += abs(pl)
            # pl == 0 はどちらにもカウントしない

        global_win_rate = wins / total_trades if total_trades > 0 else 0.0
        global_pf = (profit_sum / loss_sum) if loss_sum > 0 else None

        # --- 時間帯ごとのうち、成績が良いものを抽出 ---
        # 件数が少なすぎる時間帯は除外
        min_trades_per_bucket = 5

        hour_entries: List[Dict[str, Any]] = []

        for hour, s in time_of_day_stats.items():
            trades = s.get("trades", 0)
            if trades < min_trades_per_bucket:
                continue
            win_rate = s.get("win_rate", 0.0)
            pf = s.get("pf")

            hour_entries.append({
                "hour": int(hour),
                "trades": trades,
                "win_rate": win_rate,
                "pf": pf,
            })

        # 勝率 → PF の順でソートして上位3件くらいを best_hours とする
        hour_entries.sort(
            key=lambda x: (
                x["win_rate"],
                x["pf"] if x["pf"] is not None else 0.0
            ),
            reverse=True
        )
        best_hours = hour_entries[:3]

        return {
            "total_trades": total_trades,
            "global_win_rate": global_win_rate,
            "global_pf": global_pf,
            "min_trades_per_bucket": min_trades_per_bucket,
            "best_hours": best_hours,
        }

    def _compute_dd_pre_signal(self, decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        decisions.jsonl（バックテスト or 実運用ログ）を元に
        DD直前に見られる特徴を簡易的に抽出する v0 ロジック。

        Parameters
        ----------
        decisions: List[Dict[str, Any]]
            _load_decision_records() が返すレコードのリスト

        Returns
        -------
        Dict[str, Any]
            {
                "loss_streak": int,
                "common_hours": List[int],
                "avg_atr": float | None,
                "avg_volatility": float | None,
                "notes": str
            }
        """
        if not decisions:
            return {
                "loss_streak": 0,
                "common_hours": [],
                "avg_atr": None,
                "avg_volatility": None,
                "notes": "データが無いため分析できません。",
            }

        loss_streak = 0
        max_streak = 0
        hours: List[int] = []
        atr_list: List[float] = []
        vol_list: List[float] = []

        for d in decisions:
            # pl < 0 を損失として扱う（result == -1 の代わり）
            pl = d.get("pl", 0.0)
            if pl < 0:
                loss_streak += 1
                max_streak = max(max_streak, loss_streak)

                # 時間帯は dd_pre_signal v0 の代表指標
                ts = d.get("timestamp")
                if ts and isinstance(ts, datetime):
                    hours.append(ts.hour)

                # ATR / ボラティリティ がログにある場合は集計
                # meta フィールドから取得を試みる
                meta = d.get("meta", {})
                if isinstance(meta, str):
                    try:
                        import json
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}

                atr = meta.get("atr") or d.get("atr")
                if atr is not None:
                    try:
                        atr_list.append(float(atr))
                    except (ValueError, TypeError):
                        pass

                volatility = meta.get("volatility") or d.get("volatility")
                if volatility is not None:
                    try:
                        vol_list.append(float(volatility))
                    except (ValueError, TypeError):
                        pass
            else:
                loss_streak = 0

        return {
            "loss_streak": max_streak,
            "common_hours": sorted(list(set(hours))),
            "avg_atr": sum(atr_list) / len(atr_list) if atr_list else None,
            "avg_volatility": sum(vol_list) / len(vol_list) if vol_list else None,
            "notes": "v0簡易ロジック：本番版では勝率推移・トレンド崩壊なども解析予定",
        }


# シングルトン的に使うためのヘルパー
_diagnosis_service: Optional[DiagnosisService] = None


def get_diagnosis_service() -> DiagnosisService:
    global _diagnosis_service
    if _diagnosis_service is None:
        _diagnosis_service = DiagnosisService()
    return _diagnosis_service
