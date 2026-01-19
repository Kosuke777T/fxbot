# app/services/ohlcv_update_service.py
"""
OHLCV CSV自動更新サービス（GUI起動中にMT5最新まで追記＋推論実行）
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import MetaTrader5 as mt5
import pandas as pd
from loguru import logger

from app.core.symbol_map import resolve_symbol
from app.services import data_guard
from app.services.ai_service import get_ai_service
from app.services.execution_stub import _write_decision_log


def _jst_from_mt5_epoch(series) -> pd.Series:
    """MT5の 'time' (Unix秒, UTC) を JST の naive datetime に変換"""
    s = pd.to_datetime(series, unit="s", utc=True)
    if isinstance(s, pd.DatetimeIndex):
        return s.tz_convert("Asia/Tokyo").tz_localize(None)
    else:
        return s.dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)


def ensure_ohlcv_uptodate(symbol: str = "USDJPY-", timeframe: str = "M5") -> Dict[str, Any]:
    """
    OHLCV CSVをMT5最新まで更新し、新規行に対して推論を実行してdecisions.jsonlに保存する。

    Parameters
    ----------
    symbol : str
        MT5シンボル（例: "USDJPY-"）
    timeframe : str
        タイムフレーム（例: "M5"）

    Returns
    -------
    dict
        {
            "ok": bool,
            "symbol": str,
            "timeframe": str,
            "csv_tail_before": Optional[str],  # JST naive datetime文字列
            "csv_tail_after": Optional[str],
            "mt5_latest": Optional[str],
            "append_rows": int,
            "infer_rows": int,
            "error": Optional[str],
        }
    """
    symbol_tag = str(symbol or "USDJPY").rstrip("-").upper().strip()
    tf = str(timeframe or "M5").upper().strip()

    result: Dict[str, Any] = {
        "ok": False,
        "symbol": symbol,
        "timeframe": tf,
        "csv_tail_before": None,
        "csv_tail_after": None,
        "mt5_latest": None,
        "append_rows": 0,
        "infer_rows": 0,
        "error": None,
    }

    try:
        # 観測: 関数呼び出し確認（必須）
        csv_path = data_guard.csv_path(symbol_tag=symbol_tag, timeframe=tf, layout="per-symbol")
        csv_path_abs = csv_path.resolve()
        now_jst = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
        logger.info(
            "[ohlcv][m5][update][tick] job=ohlcv_m5_auto_update symbol={} tf={} csv_path={} cwd={} exe={}",
            symbol,
            tf,
            str(csv_path_abs),
            str(Path.cwd()),
            str(sys.executable),
        )

        # 1) CSV末尾tsを読む（更新前）
        csv_tail_before: Optional[pd.Timestamp] = None
        if csv_path.exists():
            try:
                df_csv = pd.read_csv(csv_path, parse_dates=["time"])
                if not df_csv.empty and "time" in df_csv.columns:
                    csv_tail_before = df_csv["time"].max()
                    result["csv_tail_before"] = str(csv_tail_before)
            except Exception as e:
                logger.warning(f"[ohlcv][m5][update] failed to read CSV tail: {e}")

        # 2) MT5最新M5 tsを取得
        mt5_latest: Optional[pd.Timestamp] = None
        try:
            if not mt5.initialize():
                err = mt5.last_error()
                raise RuntimeError(f"MT5 initialize failed: {err}")
            resolved = resolve_symbol(symbol)
            tf_map = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
            tf_const = tf_map.get(tf, mt5.TIMEFRAME_M5)
            # 修正: copy_rates_from_pos を使用（最新取得の正規ルート）
            rates = mt5.copy_rates_from_pos(resolved, tf_const, 0, 1)
            if rates and len(rates) > 0:
                df_mt5 = pd.DataFrame(rates)
                df_mt5["time"] = _jst_from_mt5_epoch(df_mt5["time"])
                mt5_latest = df_mt5["time"].iloc[-1]
                result["mt5_latest"] = str(mt5_latest)
            mt5.shutdown()
        except Exception as e:
            logger.error(f"[ohlcv][m5][update] MT5 fetch failed: {e}")
            result["error"] = f"mt5_fetch_failed: {e}"
            return result

        if mt5_latest is None:
            result["error"] = "mt5_latest_is_none"
            return result

        # 3) data_guard.ensure_data() でOHLC CSVを最新化
        # 修正: end_date を MT5最新の日付に設定（当日を含むため翌日にする）
        mt5_latest_date = mt5_latest.date()
        end_date_str = (pd.Timestamp(mt5_latest_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        start_date_str = "2020-11-01"  # 既存デフォルト

        logger.info(
            "[ohlcv][m5][update] ensure_data start: symbol_tag={} tf={} start={} end={} env=laptop layout=per-symbol",
            symbol_tag,
            tf,
            start_date_str,
            end_date_str,
        )

        try:
            data_guard.ensure_data(
                symbol_tag=symbol_tag,
                timeframe=tf,
                start_date=start_date_str,
                end_date=end_date_str,
                env="laptop",  # 環境判定は既存ロジックに任せる
                layout="per-symbol",
            )
            logger.info(
                "[ohlcv][m5][update] ensure_data done: csv_path={} exists={}",
                str(csv_path_abs),
                csv_path_abs.exists(),
            )
        except Exception as e:
            logger.error(f"[ohlcv][m5][update] ensure_data failed: {e}")
            result["error"] = f"ensure_data_failed: {e}"
            return result

        # 4) 更新後のCSV末尾tsを読む
        csv_tail_after: Optional[pd.Timestamp] = None
        append_rows = 0
        if csv_path_abs.exists():
            try:
                df_csv_after = pd.read_csv(csv_path_abs, parse_dates=["time"])
                if not df_csv_after.empty and "time" in df_csv_after.columns:
                    csv_tail_after = df_csv_after["time"].max()
                    result["csv_tail_after"] = str(csv_tail_after)
                    if csv_tail_before is not None:
                        # 更新前後の差から追加行数を算出
                        df_new = df_csv_after[df_csv_after["time"] > csv_tail_before]
                        append_rows = len(df_new)
                    else:
                        # 更新前が無い場合は全体行数（初回実行想定）
                        append_rows = len(df_csv_after)
                    logger.info(
                        "[ohlcv][m5][update] csv_tail_after={} append_rows={}",
                        str(csv_tail_after),
                        append_rows,
                    )
            except Exception as e:
                logger.warning(f"[ohlcv][m5][update] failed to read CSV after update: {e}")

        result["append_rows"] = append_rows

        # 5) append_rows > 0 のときのみ、新規行に対して推論実行
        infer_rows = 0
        if append_rows > 0 and csv_path_abs.exists():
            try:
                # CSV全体を読み込んで特徴量生成（rolling計算に過去データが必要）
                df_csv_after = pd.read_csv(csv_path_abs, parse_dates=["time"])
                df_csv_after = df_csv_after.sort_values("time").reset_index(drop=True)

                # 新規行のインデックス範囲を特定
                if csv_tail_before is not None:
                    new_mask = df_csv_after["time"] > csv_tail_before
                    new_indices = df_csv_after[new_mask].index.tolist()
                else:
                    # 初回実行時は末尾100行を対象（過去データが必要なため）
                    new_indices = df_csv_after.tail(min(100, len(df_csv_after))).index.tolist()

                if not new_indices:
                    result["infer_rows"] = 0
                else:
                    # 特徴量生成（既存ロジックを再利用）
                    from app.strategies.ai_strategy import build_features

                    # 特徴量生成に必要な列を確認
                    required_cols = ["time", "open", "high", "low", "close"]
                    if not all(col in df_csv_after.columns for col in required_cols):
                        logger.warning("[lgbm][m5][infer] CSV missing required columns")
                        result["infer_rows"] = 0
                    else:
                        # 特徴量生成（過去データを含む範囲で計算）
                        feat_params = {"feature_recipe": "ohlcv_tech_v1"}
                        try:
                            df_feat = build_features(df_csv_after[required_cols], feat_params)
                        except Exception as e:
                            logger.warning(f"[lgbm][m5][infer] feature generation failed: {e}")
                            result["infer_rows"] = 0
                        else:
                            # 新規行分だけ推論実行
                            ai_service = get_ai_service()
                            first_ts: Optional[str] = None
                            last_ts: Optional[str] = None

                            for idx in new_indices:
                                if idx >= len(df_feat):
                                    continue
                                try:
                                    row_feat = df_feat.iloc[idx]
                                    row_ohlc = df_csv_after.iloc[idx]

                                    # 特徴量をdictに変換（AISvc.predict用）
                                    feat_dict = row_feat.drop("time").to_dict()
                                    # NaNを0で埋める
                                    feat_dict = {k: (float(v) if pd.notna(v) else 0.0) for k, v in feat_dict.items()}

                                    # 推論実行
                                    prob_out = ai_service.predict(feat_dict, no_metrics=True)
                                    if not prob_out:
                                        continue

                                    # decisions.jsonl に保存（既存形式に合わせる）
                                    ts_jst = row_ohlc["time"]
                                    if isinstance(ts_jst, pd.Timestamp):
                                        ts_jst_str = ts_jst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
                                    else:
                                        ts_jst_str = str(ts_jst)

                                    if first_ts is None:
                                        first_ts = ts_jst_str
                                    last_ts = ts_jst_str

                                    p_buy = float(getattr(prob_out, "p_buy", 0.0))
                                    p_sell = float(getattr(prob_out, "p_sell", 0.0))

                                    record = {
                                        "ts_jst": ts_jst_str,
                                        "timestamp": ts_jst_str,
                                        "type": "decision",
                                        "symbol": symbol,
                                        "strategy": "LightGBM_clf",
                                        "prob_buy": p_buy,
                                        "prob_sell": p_sell,
                                        "filter_pass": False,  # 簡易版ではFalse
                                        "filter_reasons": ["ohlcv_auto_update"],
                                        "decision": "SKIP",  # 簡易版ではSKIP
                                        "decision_detail": {
                                            "action": "SKIP",
                                            "side": "BUY" if p_buy > p_sell else "SELL",
                                            "ai_margin": p_buy - p_sell,
                                        },
                                    }
                                    _write_decision_log(symbol, record)
                                    infer_rows += 1
                                except Exception as e:
                                    logger.warning(f"[lgbm][m5][infer] failed for idx {idx}: {e}")
                                    continue

                            result["infer_rows"] = infer_rows
                            if first_ts and last_ts:
                                logger.info(
                                    "[lgbm][m5][infer] symbol={} rows={} first_ts={} last_ts={}",
                                    symbol,
                                    infer_rows,
                                    first_ts,
                                    last_ts,
                                )
            except Exception as e:
                logger.error(f"[lgbm][m5][infer] failed: {e}")
                result["error"] = f"infer_failed: {e}"

        # 6) INFOログ（必須）
        logger.info(
            "[ohlcv][m5][update] symbol={} csv_tail={} mt5_last={} append_rows={}",
            symbol,
            result["csv_tail_after"] or "none",
            result["mt5_latest"] or "none",
            append_rows,
        )

        result["ok"] = True
        return result

    except Exception as e:
        logger.error(f"[ohlcv][m5][update] failed: {e}")
        result["error"] = str(e)
        return result


def main() -> None:
    """
    手動実行用エントリポイント（観測・デバッグ用）
    python -m app.services.ohlcv_update_service で実行可能
    """
    import sys

    symbol = "USDJPY-"
    timeframe = "M5"
    if len(sys.argv) > 1:
        symbol = sys.argv[1]
    if len(sys.argv) > 2:
        timeframe = sys.argv[2]

    result = ensure_ohlcv_uptodate(symbol=symbol, timeframe=timeframe)
    if result.get("ok"):
        print(f"OK: append_rows={result.get('append_rows')} infer_rows={result.get('infer_rows')}")
        sys.exit(0)
    else:
        print(f"FAILED: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
