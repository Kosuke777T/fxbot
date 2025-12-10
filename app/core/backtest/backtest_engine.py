# app/core/backtest/backtest_engine.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.backtest.simulated_execution import SimulatedExecution
from app.services.ai_service import AISvc
from app.services.filter_service import evaluate_entry
from app.services.profile_stats_service import get_profile_stats_service
from app.strategies.ai_strategy import build_features


class BacktestEngine:
    """
    v5.1 準拠のバックテストエンジン

    フロー: Strategy → FilterEngine → SimulatedExecution
    """

    def __init__(
        self,
        profile: str = "michibiki_std",
        initial_capital: float = 100000.0,
        contract_size: int = 100000,
        filter_level: int = 3,
    ):
        """
        Parameters
        ----------
        profile : str
            プロファイル名
        initial_capital : float
            初期資本
        contract_size : int
            契約サイズ（JPYペアの場合は100000）
        filter_level : int
            フィルタレベル（0=無効, 1=Basic, 2=Pro, 3=Expert）
        """
        self.profile = profile
        self.initial_capital = initial_capital
        self.contract_size = contract_size
        self.filter_level = filter_level

        self.ai_service = AISvc()
        self.executor = SimulatedExecution(initial_capital, contract_size)
        self.profile_stats_service = get_profile_stats_service()

        # 連敗カウンタ（バックテスト中に動的に更新）
        self.consecutive_losses = 0

        # decisions.jsonl の記録用
        self.decisions: List[Dict[str, Any]] = []

    def run(
        self,
        df: pd.DataFrame,
        out_dir: Path,
        symbol: str = "USDJPY",
    ) -> Dict[str, Any]:
        """
        バックテストを実行する

        Parameters
        ----------
        df : pd.DataFrame
            OHLCVデータ（time, open, high, low, close, volume を含む）
        out_dir : Path
            出力ディレクトリ
        symbol : str
            シンボル名

        Returns
        -------
        dict
            バックテスト結果（equity_curve, trades, decisions のパスなど）
        """
        # データの準備
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        # 特徴量を構築
        print(f"[BacktestEngine] Building features...", flush=True)
        df_features = build_features(df, params={})

        # 必須列の補完
        if "time" not in df_features.columns:
            df_features["time"] = df["time"]
        if "close" not in df_features.columns:
            df_features["close"] = df["close"].astype(float)

        # 各バーを処理
        print(f"[BacktestEngine] Processing {len(df_features)} bars...", flush=True)
        from tools.backtest_run import iter_with_progress
        for idx, row in iter_with_progress(df_features, step=5, use_iterrows=True):
            timestamp = pd.Timestamp(row["time"])
            price = float(row["close"])

            # 特徴量を辞書形式に変換
            features_dict = {col: float(row[col]) for col in df_features.columns if col not in ["time", "close"]}

            # Strategy.predict を呼ぶ
            ai_out = self.ai_service.predict(features_dict, no_metrics=True)

            # EntryContext を作成
            entry_context = self._build_entry_context(row, timestamp)

            # FilterEngine.evaluate を呼ぶ
            # filter_level を渡すために、直接 StrategyFilterEngine を使用
            from app.core.filter.strategy_filter_engine import StrategyFilterEngine
            filter_engine = StrategyFilterEngine()
            filter_pass, filter_reasons = filter_engine.evaluate(entry_context, filter_level=self.filter_level)

            # 決定を構築
            decision = self._build_decision(
                ai_out=ai_out,
                filter_pass=filter_pass,
                filter_reasons=filter_reasons,
                entry_context=entry_context,
            )

            # decisions.jsonl に記録
            decision_trace = self._build_decision_trace(
                timestamp=timestamp,
                symbol=symbol,
                ai_out=ai_out,
                decision=decision,
                entry_context=entry_context,
            )
            self.decisions.append(decision_trace)

            # filter_pass = False の場合は見送り
            if not filter_pass:
                continue

            # 既存ポジションのクローズ判定（SL/TP判定）
            if self.executor._open_position is not None:
                open_pos = self.executor._open_position
                should_close = False
                close_price = price

                # SL/TP判定
                if open_pos.side == "BUY":
                    if open_pos.sl is not None and price <= open_pos.sl:
                        should_close = True
                        close_price = open_pos.sl
                    elif open_pos.tp is not None and price >= open_pos.tp:
                        should_close = True
                        close_price = open_pos.tp
                else:  # SELL
                    if open_pos.sl is not None and price >= open_pos.sl:
                        should_close = True
                        close_price = open_pos.sl
                    elif open_pos.tp is not None and price <= open_pos.tp:
                        should_close = True
                        close_price = open_pos.tp

                # 簡易版：次のバーでクローズ（SL/TPが無い場合）
                if not should_close and idx < len(df_features) - 1:
                    # 次のバーでクローズ
                    should_close = True
                    close_price = float(df_features.iloc[idx + 1]["close"])

                if should_close:
                    closed_trade = self.executor.close_position(close_price, timestamp)

                    if closed_trade:
                        # 連敗カウンタを更新
                        if closed_trade.pnl < 0:
                            self.consecutive_losses += 1
                        else:
                            self.consecutive_losses = 0

            # filter_pass = True の場合のみ SimulatedExecution に渡す
            if decision.get("action") in ("BUY", "SELL"):
                side = decision["action"]
                lot = decision.get("lot", 0.1)
                atr = entry_context.get("atr")
                sl = decision.get("signal", {}).get("sl")
                tp = decision.get("signal", {}).get("tp")

                self.executor.open_position(
                    side=side,
                    price=price,
                    timestamp=timestamp,
                    lot=lot,
                    atr=atr,
                    sl=sl,
                    tp=tp,
                )

        # 最終バーで強制クローズ
        if self.executor._open_position is not None:
            final_price = float(df_features.iloc[-1]["close"])
            final_timestamp = pd.Timestamp(df_features.iloc[-1]["time"])
            self.executor.force_close_all(final_price, final_timestamp)

        # 出力ファイルを生成
        print(f"[BacktestEngine] Generating output files...", flush=True)
        return self._generate_outputs(df_features, out_dir, symbol)

    def _build_entry_context(self, row: pd.Series, timestamp: pd.Timestamp) -> Dict[str, Any]:
        """
        EntryContext を作成する

        Parameters
        ----------
        row : pd.Series
            特徴量を含む行
        timestamp : pd.Timestamp
            タイムスタンプ

        Returns
        -------
        dict
            EntryContext
        """
        # プロファイル統計を取得
        profile_stats = {}
        try:
            stats = self.profile_stats_service.get_profile_stats([self.profile])
            if self.profile in stats:
                profile_stats = stats[self.profile].to_dict()
        except Exception:
            pass

        return {
            "timestamp": timestamp,
            "atr": float(row.get("atr", 0.0)) if "atr" in row else None,
            "volatility": float(row.get("volatility", 0.0)) if "volatility" in row else None,
            "trend_strength": float(row.get("trend_strength", 0.0)) if "trend_strength" in row else None,
            "consecutive_losses": self.consecutive_losses,
            "profile_stats": profile_stats,
        }

    def _build_decision(
        self,
        ai_out: Any,
        filter_pass: bool,
        filter_reasons: List[str],
        entry_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        決定を構築する

        Parameters
        ----------
        ai_out : Any
            Strategy.predict の出力
        filter_pass : bool
            フィルタ通過フラグ
        filter_reasons : List[str]
            フィルタNGの場合の理由リスト
        entry_context : Dict[str, Any]
            EntryContext

        Returns
        -------
        dict
            決定辞書
        """
        prob_buy = getattr(ai_out, "p_buy", 0.0)
        prob_sell = getattr(ai_out, "p_sell", 0.0)

        # 簡易版：prob_buy > 0.52 なら BUY、prob_sell > 0.52 なら SELL
        # TODO: 実際の戦略ロジックに合わせて修正
        threshold = 0.52
        action = "SKIP"
        side = None

        if filter_pass:
            if prob_buy > threshold:
                action = "BUY"
                side = "BUY"
            elif prob_sell > threshold:
                action = "SELL"
                side = "SELL"

        return {
            "action": action,
            "side": side,
            "filter_pass": filter_pass,
            "filter_reasons": filter_reasons,
            "signal": {
                "side": side,
                "lot": 0.1,  # TODO: 実際のロット計算ロジックに合わせて修正
            },
        }

    def _build_decision_trace(
        self,
        timestamp: pd.Timestamp,
        symbol: str,
        ai_out: Any,
        decision: Dict[str, Any],
        entry_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        decisions.jsonl 用のトレースを構築する

        Parameters
        ----------
        timestamp : pd.Timestamp
            タイムスタンプ
        symbol : str
            シンボル名
        ai_out : Any
            Strategy.predict の出力
        decision : Dict[str, Any]
            決定辞書
        entry_context : Dict[str, Any]
            EntryContext

        Returns
        -------
        dict
            decisions.jsonl 用のレコード
        """
        prob_buy = getattr(ai_out, "p_buy", 0.0)
        prob_sell = getattr(ai_out, "p_sell", 0.0)
        meta = getattr(ai_out, "meta", {})
        if not isinstance(meta, dict):
            meta = {}

        ts_jst = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "ts_jst": ts_jst,
            "type": "decision",
            "symbol": symbol,
            "strategy": getattr(ai_out, "model_name", "unknown"),
            "prob_buy": round(prob_buy, 6),
            "prob_sell": round(prob_sell, 6),
            "filter_pass": decision.get("filter_pass"),
            "filter_reasons": decision.get("filter_reasons", []),
            "filters": {
                **entry_context,
                "filter_level": self.filter_level,
            },
            "meta": meta,
            "decision": decision.get("action", "SKIP"),
            "decision_detail": decision,
        }

    def _normalize_for_json(self, obj: Any) -> Any:
        """
        JSON シリアライズ可能な形式に変換する

        Parameters
        ----------
        obj : Any
            変換対象のオブジェクト

        Returns
        -------
        Any
            JSON 可能な形式に変換されたオブジェクト
        """
        import numpy as np
        import datetime

        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    def _normalize_for_json_recursive(self, obj: Any) -> Any:
        """
        JSON シリアライズ可能な形式に再帰的に変換する

        Parameters
        ----------
        obj : Any
            変換対象のオブジェクト（dict, list, その他）

        Returns
        -------
        Any
            JSON 可能な形式に変換されたオブジェクト
        """
        if isinstance(obj, dict):
            return {k: self._normalize_for_json_recursive(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._normalize_for_json_recursive(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self._normalize_for_json_recursive(v) for v in obj)
        return self._normalize_for_json(obj)

    def _generate_outputs(
        self,
        df_features: pd.DataFrame,
        out_dir: Path,
        symbol: str,
    ) -> Dict[str, Any]:
        """
        出力ファイルを生成する

        Parameters
        ----------
        df_features : pd.DataFrame
            特徴量データ
        out_dir : Path
            出力ディレクトリ
        symbol : str
            シンボル名

        Returns
        -------
        dict
            出力ファイルのパス
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        # トレード履歴を取得
        trades_df = self.executor.get_trades_df()

        # エクイティ曲線を生成
        timestamps = pd.to_datetime(df_features["time"])
        prices = df_features["close"].astype(float)
        equity_series = self.executor.get_equity_curve(timestamps, prices)

        equity_df = pd.DataFrame({
            "time": equity_series.index,
            "equity": equity_series.values,
        })

        # ファイル出力
        equity_csv = out_dir / "equity_curve.csv"
        equity_df.to_csv(equity_csv, index=False)
        print(f"[BacktestEngine] Wrote {equity_csv}", flush=True)

        trades_csv = out_dir / "trades.csv"
        if not trades_df.empty:
            trades_df.to_csv(trades_csv, index=False)
            print(f"[BacktestEngine] Wrote {trades_csv}", flush=True)
        else:
            # 空のCSVを作成
            pd.DataFrame(columns=["entry_time", "entry_price", "exit_time", "exit_price", "side", "lot", "pnl"]).to_csv(trades_csv, index=False)

        # monthly_returns.csv を生成
        from tools.backtest_run import compute_monthly_returns
        monthly_csv = out_dir / "monthly_returns.csv"
        compute_monthly_returns(equity_csv, monthly_csv)
        print(f"[BacktestEngine] Wrote {monthly_csv}", flush=True)

        # decisions.jsonl を出力
        decisions_jsonl = out_dir / "decisions.jsonl"
        with open(decisions_jsonl, "w", encoding="utf-8") as f:
            for decision in self.decisions:
                normalized = self._normalize_for_json_recursive(decision)
                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        print(f"[BacktestEngine] Wrote {decisions_jsonl}", flush=True)

        return {
            "equity_curve": equity_csv,
            "trades": trades_csv,
            "monthly_returns": monthly_csv,
            "decisions": decisions_jsonl,
        }

