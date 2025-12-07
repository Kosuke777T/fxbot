# app/services/diagnosis_service.py
# DiagnosisService: 診断AI v1 (T-12)
# 内部仕様書 v5.1 準拠

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.edition_guard import EditionGuard
from app.services.decision_log import _iter_jsonl, _get_decision_log_dir


class DiagnosisService:
    """
    診断AIサービス（v5.1 仕様準拠）

    - 時間帯ごとの勝率
    - ボラティリティ × 勝率
    - 勝ちやすい特徴量条件
    - DD直前の特徴
    - 連勝区間とその傾向
    - 異常点検出
    - Expertのみ：来週シナリオ生成
    """

    def __init__(self):
        self.guard = EditionGuard()

    def analyze(self, profile: str = "michibiki_std", start: Optional[str] = None, end: Optional[str] = None) -> dict:
        """
        Diagnosis AI v1 (T-12)

        Parameters
        ----------
        profile: str
            プロファイル名（デフォルト: "michibiki_std"）
        start: str | None
            開始日時（ISO形式、例: "2025-01-01T00:00:00"）
        end: str | None
            終了日時（ISO形式）

        Returns
        -------
        dict
            診断結果の辞書
        """
        # ① decisions.jsonl の読み込み
        log_dir = _get_decision_log_dir()
        
        # プロファイル名に基づいてファイルを探す
        # 実際のファイル名は decisions_USDJPY.jsonl などの形式
        decision_files = sorted(log_dir.glob("decisions_*.jsonl"))
        
        if not decision_files:
            logger.warning(f"[DiagnosisService] no decision files found in {log_dir}")
            return {"error": "no_decisions"}

        # 最新のファイルを使用（または全ファイルをマージ）
        entries: List[Dict[str, Any]] = []
        for file_path in decision_files:
            for entry in _iter_jsonl(file_path):
                # start/end でフィルタリング
                if start or end:
                    ts_str = entry.get("ts_jst") or entry.get("ts") or entry.get("timestamp")
                    if not ts_str:
                        continue
                    try:
                        if isinstance(ts_str, str):
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        else:
                            continue
                    except Exception:
                        continue
                    
                    if start:
                        try:
                            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                            if ts < start_dt:
                                continue
                        except Exception:
                            pass
                    
                    if end:
                        try:
                            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                            if ts > end_dt:
                                continue
                        except Exception:
                            pass
                
                entries.append(entry)

        if not entries:
            logger.warning("[DiagnosisService] no entries found after filtering")
            return {"error": "empty_log"}

        logger.info(f"[DiagnosisService] analyzing {len(entries)} entries")

        # 時間帯 × 勝率
        time_stats = self._compute_time_of_day(entries)

        # ボラ × 勝率
        vol_stats = self._compute_volatility(entries)

        # 勝ちやすい特徴量推定（簡易版）
        win_cond = self._compute_winning_conditions(entries)

        # DD直前の特徴
        dd_pre = self._compute_dd_pre_signal(profile, entries)

        # 異常点検出
        anomalies = self._detect_anomalies(entries)

        result = {
            "time_of_day_stats": time_stats,
            "volatility_stats": vol_stats,
            "winning_conditions": win_cond,
            "dd_pre_signal": dd_pre,
            "anomalies": anomalies,
        }

        # Expert: 来週の相場シナリオ（filter_level == 3 は Expert/Master）
        if self.guard.get_capability("filter_level") == 3:
            result["scenario_next_week"] = self._forecast_next_week(entries)

        return result

    # ====== 以下は分析用の内部関数 ======

    def _compute_time_of_day(self, entries: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """時間帯ごとの勝率を計算"""
        hourly: Dict[int, Dict[str, int]] = {}

        for e in entries:
            # タイムスタンプを取得
            ts_str = e.get("ts_jst") or e.get("ts") or e.get("timestamp")
            if not ts_str:
                continue

            try:
                if isinstance(ts_str, str):
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                else:
                    continue
            except Exception:
                continue

            hour = ts.hour
            hourly.setdefault(hour, {"wins": 0, "loss": 0, "total": 0})

            # result を判定（pnl が正なら win、負なら loss）
            pnl = e.get("pnl")
            if pnl is not None:
                try:
                    pnl_val = float(pnl)
                    if pnl_val > 0:
                        hourly[hour]["wins"] += 1
                    elif pnl_val < 0:
                        hourly[hour]["loss"] += 1
                    hourly[hour]["total"] += 1
                except (ValueError, TypeError):
                    pass
            else:
                # pnl がない場合は result フィールドを確認
                result = e.get("result")
                if result == "win":
                    hourly[hour]["wins"] += 1
                    hourly[hour]["total"] += 1
                elif result == "loss":
                    hourly[hour]["loss"] += 1
                    hourly[hour]["total"] += 1

        # winrate 計算
        result_dict: Dict[int, Dict[str, Any]] = {}
        for h, v in hourly.items():
            total = v["total"]
            if total > 0:
                result_dict[h] = {
                    "wins": v["wins"],
                    "loss": v["loss"],
                    "total": total,
                    "winrate": v["wins"] / total if total > 0 else None,
                }
            else:
                result_dict[h] = {
                    "wins": 0,
                    "loss": 0,
                    "total": 0,
                    "winrate": None,
                }

        return result_dict

    def _compute_volatility(self, entries: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        """ボラティリティ帯ごとの勝率を計算"""
        buckets: Dict[str, List[Dict[str, Any]]] = {"low": [], "mid": [], "high": []}

        for e in entries:
            # meta から volatility を取得
            meta = e.get("meta", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            vol = meta.get("volatility") or meta.get("vol") or e.get("volatility")
            if vol is None:
                continue

            try:
                vol_val = float(vol)
            except (ValueError, TypeError):
                continue

            if vol_val < 0.2:
                buckets["low"].append(e)
            elif vol_val < 0.5:
                buckets["mid"].append(e)
            else:
                buckets["high"].append(e)

        def winrate(lst: List[Dict[str, Any]]) -> Optional[float]:
            if not lst:
                return None
            wins = 0
            for x in lst:
                pnl = x.get("pnl")
                if pnl is not None:
                    try:
                        if float(pnl) > 0:
                            wins += 1
                    except (ValueError, TypeError):
                        pass
                elif x.get("result") == "win":
                    wins += 1
            return wins / len(lst) if len(lst) > 0 else None

        return {
            "low": winrate(buckets["low"]),
            "mid": winrate(buckets["mid"]),
            "high": winrate(buckets["high"]),
        }

    def _compute_winning_conditions(self, entries: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        """勝ちやすい特徴量条件を推定（簡易版）"""
        wins: List[Dict[str, Any]] = []
        losses: List[Dict[str, Any]] = []

        for e in entries:
            pnl = e.get("pnl")
            if pnl is not None:
                try:
                    if float(pnl) > 0:
                        wins.append(e)
                    elif float(pnl) < 0:
                        losses.append(e)
                except (ValueError, TypeError):
                    pass
            else:
                if e.get("result") == "win":
                    wins.append(e)
                elif e.get("result") == "loss":
                    losses.append(e)

        def median_feature(lst: List[Dict[str, Any]], key: str) -> Optional[float]:
            vals: List[float] = []
            for e in lst:
                meta = e.get("meta", {})
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                
                val = meta.get(key) or e.get(key)
                if val is not None:
                    try:
                        vals.append(float(val))
                    except (ValueError, TypeError):
                        continue
            return statistics.median(vals) if vals else None

        return {
            "atr_win_med": median_feature(wins, "atr"),
            "atr_loss_med": median_feature(losses, "atr"),
            "trend_win_med": median_feature(wins, "trend"),
            "trend_loss_med": median_feature(losses, "trend"),
            "vol_win_med": median_feature(wins, "volatility"),
            "vol_loss_med": median_feature(losses, "volatility"),
        }

    def _compute_dd_pre_signal(self, profile: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        backtests/{profile}/monthly_returns.csv を参照して
        「最大DDが発生した月」とその直前区間の特徴をざっくり出す。

        - worst_month: 一番DDのきつかった年月
        - max_dd_pct: そのときのDD
        - trades_in_period: その月のトレード数（decisions log ベース）
        - winrate: その月の勝率（result が win/loss の場合のみ）
        - avg_atr / avg_volatility / avg_trend: meta からの平均
        """
        csv_path = Path(f"backtests/{profile}/monthly_returns.csv")

        if not csv_path.exists():
            return {"error": "no_monthly_returns_csv"}

        rows = []

        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row["max_dd_pct"] = float(row.get("max_dd_pct", 0.0))
                except ValueError:
                    continue
                rows.append(row)

        if not rows:
            return {"error": "empty_monthly_returns"}

        # max_dd_pct は通常マイナスなので、一番小さい値が「最悪DD」
        worst = min(rows, key=lambda r: r["max_dd_pct"])
        worst_month = worst.get("year_month")  # "2025-11" など
        max_dd_pct = worst.get("max_dd_pct")

        # year_month -> その月の開始・終了日時を計算
        try:
            ym = worst_month or "1900-01"
            start_dt = datetime.strptime(ym + "-01", "%Y-%m-%d")
        except Exception:
            return {
                "error": "invalid_year_month",
                "raw": worst_month,
                "max_dd_pct": max_dd_pct,
            }

        if start_dt.month == 12:
            end_dt = start_dt.replace(year=start_dt.year + 1, month=1)
        else:
            end_dt = start_dt.replace(month=start_dt.month + 1)

        # 該当月の decisions を抽出
        period_entries = []
        for e in entries:
            ts_str = e.get("ts_jst") or e.get("ts") or e.get("timestamp")
            if not ts_str:
                continue
            try:
                if isinstance(ts_str, str):
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                else:
                    continue
            except Exception:
                continue

            # tzinfo（+09:00 など）が付いている場合は削って naive に揃える
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)

            if start_dt <= ts < end_dt:
                period_entries.append(e)

        if not period_entries:
            return {
                "worst_month": worst_month,
                "max_dd_pct": max_dd_pct,
                "trades_in_period": 0,
                "winrate": None,
                "avg_atr": None,
                "avg_volatility": None,
                "avg_trend": None,
            }

        # 勝率
        wins = 0
        losses = 0
        for e in period_entries:
            pnl = e.get("pnl")
            if pnl is not None:
                try:
                    if float(pnl) > 0:
                        wins += 1
                    elif float(pnl) < 0:
                        losses += 1
                except (ValueError, TypeError):
                    pass
            elif e.get("result") == "win":
                wins += 1
            elif e.get("result") == "loss":
                losses += 1

        total = wins + losses
        winrate = wins / total if total > 0 else None

        # 特徴量の平均（meta がある場合のみ）
        atr_vals: List[float] = []
        vol_vals: List[float] = []
        trend_vals: List[float] = []

        for e in period_entries:
            meta = e.get("meta", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            atr = meta.get("atr")
            if atr is not None:
                try:
                    atr_vals.append(float(atr))
                except (ValueError, TypeError):
                    pass

            vol = meta.get("volatility")
            if vol is not None:
                try:
                    vol_vals.append(float(vol))
                except (ValueError, TypeError):
                    pass

            trend = meta.get("trend") or meta.get("trend_strength")
            if trend is not None:
                try:
                    trend_vals.append(float(trend))
                except (ValueError, TypeError):
                    pass

        def _avg(lst: List[float]) -> Optional[float]:
            return sum(lst) / len(lst) if lst else None

        return {
            "worst_month": worst_month,
            "max_dd_pct": max_dd_pct,
            "trades_in_period": len(period_entries),
            "winrate": winrate,
            "avg_atr": _avg(atr_vals),
            "avg_volatility": _avg(vol_vals),
            "avg_trend": _avg(trend_vals),
        }

    def _detect_anomalies(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """異常点検出（高確率なのに負けているケースなど）"""
        outliers: List[Dict[str, Any]] = []

        for e in entries:
            # decision から確率を取得
            decision = e.get("decision", {})
            if isinstance(decision, str):
                decision = {"action": decision}

            prob_buy = decision.get("prob_buy") or e.get("prob_buy")
            prob_sell = decision.get("prob_sell") or e.get("prob_sell")
            prob = prob_buy or prob_sell

            if prob is not None:
                try:
                    prob_val = float(prob)
                    # 高確率（0.95以上）なのに負けているケースを異常点として検出
                    pnl = e.get("pnl")
                    if pnl is not None:
                        try:
                            if prob_val > 0.95 and float(pnl) < 0:
                                outliers.append(e)
                        except (ValueError, TypeError):
                            pass
                    elif e.get("result") == "loss" and prob_val > 0.95:
                        outliers.append(e)
                except (ValueError, TypeError):
                    pass

        return outliers

    def _forecast_next_week(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """来週の相場シナリオ生成（Expert限定）"""
        # 直近30エントリの傾向を分析
        recent = entries[-30:] if len(entries) >= 30 else entries

        if not recent:
            return {
                "forecast": "insufficient_data",
                "reason": "less than 30 entries available",
            }

        # 簡易的な傾向分析
        trend_strength = 0.0
        trend_count = 0

        for e in recent:
            meta = e.get("meta", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            trend = meta.get("trend") or meta.get("trend_strength")
            if trend is not None:
                try:
                    trend_strength += abs(float(trend))
                    trend_count += 1
                except (ValueError, TypeError):
                    pass

        avg_trend = trend_strength / trend_count if trend_count > 0 else 0.0

        if avg_trend > 0.5:
            return {
                "forecast": "uptrend_likely",
                "reason": f"trend_strength high (avg={avg_trend:.2f}) in last {len(recent)} entries",
            }
        elif avg_trend < -0.5:
            return {
                "forecast": "downtrend_likely",
                "reason": f"trend_strength low (avg={avg_trend:.2f}) in last {len(recent)} entries",
            }
        else:
            return {
                "forecast": "sideways_likely",
                "reason": f"trend_strength neutral (avg={avg_trend:.2f}) in last {len(recent)} entries",
            }

