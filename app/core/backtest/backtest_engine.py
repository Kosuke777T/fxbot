# app/core/backtest/backtest_engine.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.backtest.simulated_execution import SimulatedExecution
from app.core.trade.decision_logic import decide_signal
from app.core.filter.strategy_filter_engine import StrategyFilterEngine
from app.services.ai_service import AISvc, get_model_metrics
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
        self.filter_engine = StrategyFilterEngine()

        # 連敗カウンタ（バックテスト中に動的に更新）
        self.consecutive_losses = 0

        # decisions.jsonl の記録用
        self.decisions: List[Dict[str, Any]] = []

        # best_threshold を取得（active_model.jsonから）
        try:
            model_metrics = get_model_metrics()
            self.best_threshold = float(model_metrics.get("best_threshold", 0.52))
        except Exception:
            self.best_threshold = 0.52  # フォールバック

    def _normalize_filter_ctx(self, filters_ctx: dict | None) -> dict:
        """
        Backtest 用 filters_ctx を v5.1 仕様に揃える:
        - None を {} に置き換え
        - filter_reasons を必ず list に正規化
        """
        if filters_ctx is None:
            filters_ctx = {}
        else:
            filters_ctx = dict(filters_ctx)

        reasons = filters_ctx.get("filter_reasons")

        if reasons is None:
            reasons_list: list[str] = []
        elif isinstance(reasons, str):
            reasons_list = [reasons]
        else:
            # list, tuple, set などを list にする
            reasons_list = list(reasons)

        filters_ctx["filter_reasons"] = reasons_list
        return filters_ctx

    def run(
        self,
        df: pd.DataFrame,
        out_dir: Path,
        symbol: str = "USDJPY-",
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

        # デバッグカウンタを初期化
        debug_counters = {
            "n_signal_buy": 0,
            "n_signal_sell": 0,
            "n_filter_pass": 0,
            "n_filter_fail": 0,
            "n_entries": 0,
            "n_exits": 0,
            "n_entry_attempts": 0,  # エントリー試行回数（任意）
            "filter_fail_reason": None,  # 最初の1件の失敗理由
            "filter_fail_reason_count": 0,  # 同じ理由の出現回数（optional）
            "entry_block_reason": None,  # 最初の1件のエントリーブロック理由
        }

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
            # filter_level を entry_context に追加
            entry_context["filter_level"] = self.filter_level
            filter_pass, filter_reasons = self.filter_engine.evaluate(entry_context, filter_level=self.filter_level)

            # 決定を構築
            decision = self._build_decision(
                ai_out=ai_out,
                filter_pass=filter_pass,
                filter_reasons=filter_reasons,
                entry_context=entry_context,
            )

            # シグナルカウンタを更新
            signal_side = decision.get("signal", {}).get("side")
            if signal_side == "BUY":
                debug_counters["n_signal_buy"] += 1
            elif signal_side == "SELL":
                debug_counters["n_signal_sell"] += 1

            # フィルタカウンタを更新
            if filter_pass:
                debug_counters["n_filter_pass"] += 1
            else:
                debug_counters["n_filter_fail"] += 1
                # 最初の1件の失敗理由を記録（ログ爆発を防ぐ）
                if debug_counters["n_filter_fail"] == 1:
                    # filter_reasons が空でない場合は最初の理由を、空の場合は "unknown" を記録
                    if filter_reasons and len(filter_reasons) > 0:
                        debug_counters["filter_fail_reason"] = str(filter_reasons[0])
                    else:
                        debug_counters["filter_fail_reason"] = "unknown"

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
                        # エグジットカウンタを更新
                        debug_counters["n_exits"] += 1
                        # 連敗カウンタを更新
                        if closed_trade.pnl < 0:
                            self.consecutive_losses += 1
                        else:
                            self.consecutive_losses = 0

            # filter_pass = True の場合のみ SimulatedExecution に渡す
            # エントリー試行カウンタを更新
            debug_counters["n_entry_attempts"] += 1

            # 既存ポジション保有中の場合はブロック
            if self.executor._open_position is not None:
                if debug_counters["entry_block_reason"] is None:
                    debug_counters["entry_block_reason"] = "already_in_position"
                continue

            # decision.action が "ENTRY" でない、または side が None の場合はブロック
            action = decision.get("action")
            side = decision.get("side")
            if action != "ENTRY" or side is None:
                if debug_counters["entry_block_reason"] is None:
                    if action != "ENTRY":
                        debug_counters["entry_block_reason"] = f"action_not_entry:{action}"
                    elif side is None:
                        signal_side = decision.get("signal", {}).get("side")
                        if signal_side is None:
                            debug_counters["entry_block_reason"] = "signal_none"
                        else:
                            debug_counters["entry_block_reason"] = f"side_none:signal={signal_side}"
                continue

            # BUY/SELL のチェック（後方互換のため）
            if side not in ("BUY", "SELL"):
                if debug_counters["entry_block_reason"] is None:
                    debug_counters["entry_block_reason"] = f"invalid_side:{side}"
                continue

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
            # エントリーカウンタを更新
            debug_counters["n_entries"] += 1

        # 最終バーで強制クローズ
        if self.executor._open_position is not None:
            final_price = float(df_features.iloc[-1]["close"])
            final_timestamp = pd.Timestamp(df_features.iloc[-1]["time"])
            # force_close_all()は戻り値がないので、close_position()を直接呼ぶ
            closed_trade = self.executor.close_position(final_price, final_timestamp)
            if closed_trade:
                debug_counters["n_exits"] += 1

        # 出力ファイルを生成
        print(f"[BacktestEngine] Generating output files...", flush=True)
        result = self._generate_outputs(df_features, out_dir, symbol)

        # トレード数をカウント
        trades_df = self.executor.get_trades_df()
        debug_counters["n_trades"] = len(trades_df)

        # 追加カウンタを計算
        debug_counters["n_bars"] = len(df_features)
        debug_counters["n_signals"] = debug_counters["n_signal_buy"] + debug_counters["n_signal_sell"]

        # デバッグカウンタを結果に追加
        result["debug_counters"] = debug_counters

        return result

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
        prob_buy = getattr(ai_out, "p_buy", None)
        prob_sell = getattr(ai_out, "p_sell", None)

        # decide_signal を使用してシグナル判定
        signal = decide_signal(
            prob_buy=prob_buy,
            prob_sell=prob_sell,
            best_threshold=self.best_threshold,
        )

        action = "SKIP"
        side = None

        if filter_pass and signal.side:
            action = "ENTRY"
            side = signal.side

        return {
            "action": action,
            "side": side,
            "filter_pass": filter_pass,
            "filter_reasons": filter_reasons,
            "signal": {
                "side": signal.side,
                "confidence": signal.confidence,
                "best_threshold": signal.best_threshold,
                "pass_threshold": signal.pass_threshold,
                "reason": signal.reason,
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

        # filters_ctx を構築して正規化
        filters_ctx = {
            **entry_context,
            "filter_level": self.filter_level,
            "filter_reasons": decision.get("filter_reasons", []),
        }
        filters_ctx = self._normalize_filter_ctx(filters_ctx)

        # signal情報を取得
        signal_info = decision.get("signal", {})

        # decision_context を構築（判断材料を分離）
        decision_context = {
            "ai": {
                "prob_buy": round(prob_buy, 6),
                "prob_sell": round(prob_sell, 6),
                "model_name": getattr(ai_out, "model_name", "unknown"),
                "threshold": self.best_threshold,
            },
            "filters": {
                "filter_pass": decision.get("filter_pass"),
                "filter_reasons": filters_ctx.get("filter_reasons", []),
                "spread": filters_ctx.get("spread"),
                "adx": filters_ctx.get("adx"),
                "min_adx": filters_ctx.get("min_adx"),
                "atr_pct": filters_ctx.get("atr_pct"),
                "volatility": filters_ctx.get("volatility"),
                "filter_level": filters_ctx.get("filter_level"),
            },
            "decision": {
                "action": decision.get("action", "SKIP"),
                "side": decision.get("side"),
                "reason": decision.get("reason"),
                "blocked_reason": None,  # backtest では通常 None
            },
            "meta": meta,
        }

        # runtime を構築（環境状態のみ）
        from app.services import trade_state
        from core.utils.timeutil import now_jst_iso
        runtime = trade_state.build_runtime(
            symbol,
            ts_str=now_jst_iso(),  # backtest では現在時刻を使用
            spread_pips=filters_ctx.get("spread", 0.0),
            mode="backtest",
            source="backtest",
            timeframe=None,  # backtest では timeframe は未設定
            profile=self.profile,
        )

        return {
            "ts_jst": ts_jst,
            "type": "decision",
            "symbol": symbol,
            "strategy": getattr(ai_out, "model_name", "unknown"),
            "prob_buy": round(prob_buy, 6),  # 後方互換のため残す
            "prob_sell": round(prob_sell, 6),  # 後方互換のため残す
            "filter_pass": decision.get("filter_pass"),  # 後方互換のため残す
            "filter_reasons": filters_ctx.get("filter_reasons", []),  # 後方互換のため残す
            "filters": filters_ctx,  # 後方互換のため残す
            "meta": meta,  # 後方互換のため残す
            "decision": decision.get("action", "SKIP"),  # 後方互換のため残す
            "decision_detail": {  # 後方互換のため残す
                "action": decision.get("action", "SKIP"),
                "side": decision.get("side"),
                "signal": signal_info,
                "filter_pass": decision.get("filter_pass"),
                "filter_reasons": filters_ctx.get("filter_reasons", []),
            },
            "decision_context": decision_context,  # 新規追加：判断材料を分離
            "runtime": runtime,  # 新規追加：環境状態のみ
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

    def _validate_outputs(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        出力ファイルの検証を行う。

        Parameters
        ----------
        outputs : dict
            出力ファイルのパスを含む辞書

        Returns
        -------
        dict
            {"ok": bool, "errors": list[str]}
        """
        errors: List[str] = []

        # 必須ファイルの存在確認
        required_files = ["equity_curve", "trades", "monthly_returns", "decisions"]
        for key in required_files:
            file_path = outputs.get(key)
            if file_path is None:
                errors.append(f"Missing output key: {key}")
                continue

            path = Path(file_path) if not isinstance(file_path, Path) else file_path
            if not path.exists():
                errors.append(f"Output file does not exist: {path}")

        # equity_curve.csv の内容検証（空でない、必須列がある）
        equity_path = outputs.get("equity_curve")
        if equity_path:
            try:
                path = Path(equity_path) if not isinstance(equity_path, Path) else equity_path
                if path.exists():
                    df = pd.read_csv(path)
                    if df.empty:
                        errors.append(f"equity_curve.csv is empty: {path}")
                    elif "time" not in df.columns or "equity" not in df.columns:
                        errors.append(f"equity_curve.csv missing required columns (time, equity): {path}")
            except Exception as e:
                errors.append(f"Failed to validate equity_curve.csv: {e}")

        # monthly_returns.csv 必須列チェック
        monthly_returns_path = outputs.get("monthly_returns")
        if monthly_returns_path:
            mr = Path(monthly_returns_path) if not isinstance(monthly_returns_path, Path) else monthly_returns_path
            if not mr.exists():
                errors.append(f"missing: {mr}")
            else:
                try:
                    df = pd.read_csv(mr)
                    need = ["year_month", "return_pct", "max_dd_pct", "total_trades", "pf"]
                    miss = [c for c in need if c not in df.columns]
                    if miss:
                        errors.append(f"monthly_returns missing columns: {miss}")
                    if len(df) == 0:
                        errors.append("monthly_returns is empty")
                except Exception as e:
                    errors.append(f"failed to read monthly_returns: {e!r}")

        # decisions.jsonl が最低1行dictとして読めるか
        decisions_path = outputs.get("decisions")
        if decisions_path:
            dj = Path(decisions_path) if not isinstance(decisions_path, Path) else decisions_path
            if not dj.exists():
                errors.append(f"missing: {dj}")
            else:
                ok_any = False
                try:
                    with dj.open("r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line or not line.startswith("{"):
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            if isinstance(obj, dict):
                                ok_any = True
                                break
                    if not ok_any:
                        errors.append("decisions.jsonl has no readable JSON dict line")
                except Exception as e:
                    errors.append(f"failed to read decisions.jsonl: {e!r}")

        return {
            "ok": len(errors) == 0,
            "errors": errors,
        }

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
        print(f"[BacktestEngine] _generate_outputs symbol(arg)={symbol!r}")
        if self.decisions:
            print(f"[BacktestEngine] decisions[0] type={type(self.decisions[0])} keys={list(self.decisions[0].keys())[:5] if isinstance(self.decisions[0], dict) else 'N/A'}")
        # decisions.jsonl の最終整形：symbol は run() 引数を絶対優先（運用ログと整合させる）
        # （生成側が USDJPY を入れても成果物は USDJPY- に統一される）
        for rec in self.decisions:
            if isinstance(rec, dict):
                rec["symbol"] = symbol
        if self.decisions and isinstance(self.decisions[0], dict):
            print(f"[BacktestEngine] decisions[0].symbol(after)={self.decisions[0].get('symbol')!r}")
        with open(decisions_jsonl, "w", encoding="utf-8") as f:
            for decision in self.decisions:
                normalized = self._normalize_for_json_recursive(decision)
                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        print(f"[BacktestEngine] Wrote {decisions_jsonl}", flush=True)

        # --- 集約 decisions.jsonl を更新（M5直下） ---
        # 期間dir配下の decisions.jsonl が正なので、それを M5直下へ上書きして整合性を保つ
        agg_decisions_jsonl = out_dir.parent / "decisions.jsonl"
        try:
            with open(agg_decisions_jsonl, "w", encoding="utf-8") as f:
                for decision in self.decisions:
                    normalized = self._normalize_for_json_recursive(decision)
                    f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            print(f"[BacktestEngine] Wrote {agg_decisions_jsonl}", flush=True)
        except Exception as e:
            print(f"[BacktestEngine][warn] could not update aggregate decisions.jsonl: {e!r}", flush=True)

        result = {
            "equity_curve": equity_csv,
            "trades": trades_csv,
            "monthly_returns": monthly_csv,
            "decisions": decisions_jsonl,
        }

        # 出力ファイルの検証
        validation_result = self._validate_outputs(result)
        result["output_ok"] = validation_result["ok"]
        result["output_errors"] = validation_result["errors"]

        return result

