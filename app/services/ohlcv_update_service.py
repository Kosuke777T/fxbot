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
from app.services.ai_service import get_ai_service, load_active_model_meta
from app.services.execution_stub import _write_decision_log


def _jst_from_mt5_epoch(series) -> pd.Series:
    """MT5の 'time' (Unix秒, UTC) を JST の naive datetime に変換"""
    s = pd.to_datetime(series, unit="s", utc=True)
    if isinstance(s, pd.DatetimeIndex):
        return s.tz_convert("Asia/Tokyo").tz_localize(None)
    else:
        return s.dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)


def _obs_prob_df(
    tag: str,
    df: pd.DataFrame | None,
    *,
    time_col: str = "time",
    prob_col: str = "prob_buy",
    n: int = 2000,
) -> None:
    """
    観測ログ専用（挙動変更なし）。
    - df を変更しない（必要ならコピーして集計）
    - 空/列なし/型不正でも落とさない
    """
    try:
        import math

        if df is None:
            logger.info("[lgbm_proba][{}] df=None", tag)
            return

        if not isinstance(df, pd.DataFrame) or df.empty:
            cols = list(df.columns) if isinstance(df, pd.DataFrame) else None
            logger.info("[lgbm_proba][{}] empty_or_not_df columns={}", tag, cols)
            return

        cols = list(df.columns)
        if time_col not in cols or prob_col not in cols:
            logger.info(
                "[lgbm_proba][{}] missing_cols time_col={} prob_col={} columns={}",
                tag,
                time_col,
                prob_col,
                cols,
            )
            return

        n2 = int(max(10, min(int(n), 5000)))
        sub = df[[time_col, prob_col]].tail(n2).copy()

        # time/prob を安全に整形（失敗は握る）
        try:
            sub[time_col] = pd.to_datetime(sub[time_col], errors="coerce")
            sub = sub.dropna(subset=[time_col])
        except Exception:
            pass
        try:
            sub[prob_col] = pd.to_numeric(sub[prob_col], errors="coerce")
        except Exception:
            pass

        # Δt top
        dt_top = None
        try:
            dt = sub[time_col].sort_values().diff()
            dt_sec = dt.dt.total_seconds()
            vc = dt_sec.value_counts().head(10)
            dt_top = {str(k): int(v) for k, v in vc.items()}
        except Exception:
            dt_top = None

        # run_max（連続同値の最長）
        run_max = None
        try:
            ss = pd.Series(
                [
                    float(x) if (x is not None and math.isfinite(float(x))) else None
                    for x in sub[prob_col].tolist()
                ]
            )
            grp = (ss != ss.shift()).cumsum()
            run_max = int(grp.value_counts().max()) if len(grp) > 0 else None
        except Exception:
            run_max = None

        # 文字列表現のユニーク数（保存時丸め疑いの切り分け）
        nunique_str6 = None
        try:
            nunique_str6 = int(sub[prob_col].map(lambda x: f"{float(x):.6f}" if pd.notna(x) else "NA").nunique())
        except Exception:
            nunique_str6 = None

        head = list(zip(sub[time_col].head(3).tolist(), sub[prob_col].head(3).tolist()))
        tail = list(zip(sub[time_col].tail(3).tolist(), sub[prob_col].tail(3).tolist()))

        logger.info(
            "[lgbm_proba][{}] len={} nunique_prob={} nunique_time={} run_max={} nunique_prob_str6={} head={} tail={} dt_top={}",
            tag,
            int(len(sub)),
            int(sub[prob_col].nunique(dropna=True)),
            int(sub[time_col].nunique(dropna=True)),
            run_max,
            nunique_str6,
            head,
            tail,
            dt_top,
        )
    except Exception as e:
        logger.warning("[lgbm_proba][{}] obs_failed: {}", tag, e)


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


def ensure_lgbm_proba_uptodate(
    symbol: str = "USDJPY-",
    timeframe: str = "M5",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> None:
    """
    M5 OHLC CSVの自動追記に追随して、LightGBMの推論値を「未推論分だけ」計算し、
    proba CSVに自動追記する。

    Parameters
    ----------
    symbol : str
        MT5シンボル（例: "USDJPY-"）
    timeframe : str
        タイムフレーム（例: "M5"）
    start_time : datetime, optional
        推論対象の開始時刻（指定時はこの範囲を埋める）
    end_time : datetime, optional
        推論対象の終了時刻（指定時はこの範囲を埋める）

    Notes
    -----
    - proba CSV: data/<symbol>/lgbm/<symbol>_M5_proba.csv
    - 既存行は上書きしない（重複チェック: (time, model_id) の組み合わせ）
    - model_id列を含める（active_model.jsonから取得）
    - 推論できない行はスキップ（WARNINGログのみ）
    - start_time/end_time が指定された場合、その範囲の未推論分を埋める
    """
    symbol_tag = str(symbol or "USDJPY").rstrip("-").upper().strip()
    tf = str(timeframe or "M5").upper().strip()

    try:
        # 1) M5 CSVのパスを取得
        ohlc_csv_path = data_guard.csv_path(symbol_tag=symbol_tag, timeframe=tf, layout="per-symbol")
        if not ohlc_csv_path.exists():
            logger.warning(f"[lgbm] OHLC CSV not found: {ohlc_csv_path}")
            return

        # 2) M5 CSVを読み込む
        df_ohlc = pd.read_csv(ohlc_csv_path, parse_dates=["time"])
        if df_ohlc.empty or "time" not in df_ohlc.columns:
            logger.warning(f"[lgbm] OHLC CSV is empty or missing time column: {ohlc_csv_path}")
            return

        df_ohlc = df_ohlc.sort_values("time").reset_index(drop=True)
        t_ohlc_last = df_ohlc["time"].max()

        # 3) proba CSVのパスを取得
        proba_dir = ohlc_csv_path.parent.parent / "lgbm"
        proba_dir.mkdir(parents=True, exist_ok=True)
        proba_csv_path = proba_dir / f"{symbol_tag}_{tf}_proba.csv"

        # 4) model_idを関数冒頭で1回だけ確定（active_model.jsonから）
        model_id: Optional[str] = None
        try:
            meta = load_active_model_meta()
            model_path = meta.get("model_path")
            if not model_path:
                file = meta.get("file")
                if file:
                    model_path = f"models/{file}"
            if model_path:
                # ファイル名からmodel_idを生成（拡張子を除く）
                from pathlib import Path
                model_id = Path(model_path).stem
            else:
                # フォールバック: active_model.jsonのfileフィールド
                file = meta.get("file")
                if file:
                    from pathlib import Path
                    model_id = Path(file).stem
                else:
                    model_id = "unknown"
        except Exception as e:
            logger.warning(f"[lgbm] failed to get model_id: {e}")
            model_id = "unknown"

        # 5) proba CSVの既存データを読み込み（世代管理: (time, model_id) の組み合わせ）
        t_proba_last: Optional[pd.Timestamp] = None
        existing_keys: set[tuple[pd.Timestamp, str]] = set()

        if proba_csv_path.exists():
            try:
                df_proba = pd.read_csv(proba_csv_path, parse_dates=["time"])
                if not df_proba.empty and "time" in df_proba.columns:
                    # ★ 読み取り直後に必ずtimeで昇順ソート（mergesort: 安定ソート）
                    df_proba = df_proba.sort_values("time", kind="mergesort").reset_index(drop=True)

                    # 現在のmodel_idの行だけから t_proba_last を取得（max(time)で最新判定）
                    if "model_id" in df_proba.columns:
                        df_current_model = df_proba[df_proba["model_id"] == model_id]
                        if not df_current_model.empty:
                            t_proba_last = df_current_model["time"].max()
                    else:
                        # model_id列がない場合は全体から取得（後方互換）
                        t_proba_last = df_proba["time"].max()

                    # 既存の (time, model_id) の組み合わせを記録（ソート済みdfから）
                    if "model_id" in df_proba.columns:
                        for _, row in df_proba.iterrows():
                            existing_keys.add((row["time"], str(row["model_id"])))
                    else:
                        # model_id列がない場合は time のみ（後方互換）
                        for _, row in df_proba.iterrows():
                            existing_keys.add((row["time"], "unknown"))
            except Exception as e:
                logger.warning(f"[lgbm] failed to read proba CSV: {e}")

        # 6) 未推論のM5行を抽出（model_idは関数冒頭で確定済み）
        if start_time is not None and end_time is not None:
            # 範囲指定時: start_time から end_time の範囲で、既存の (time, model_id) が存在しない行
            range_mask = (df_ohlc["time"] >= pd.Timestamp(start_time)) & (df_ohlc["time"] <= pd.Timestamp(end_time))
            df_range = df_ohlc[range_mask].copy()
            # 既存の (time, model_id) の組み合わせを除外
            missing_mask = df_range.apply(
                lambda row: (row["time"], model_id) not in existing_keys, axis=1
            )
            df_missing = df_range[missing_mask].copy()
        elif start_time is not None:
            # start_time 以降で、既存の (time, model_id) が存在しない行
            range_mask = df_ohlc["time"] >= pd.Timestamp(start_time)
            df_range = df_ohlc[range_mask].copy()
            missing_mask = df_range.apply(
                lambda row: (row["time"], model_id) not in existing_keys, axis=1
            )
            df_missing = df_range[missing_mask].copy()
        elif end_time is not None:
            # end_time 以前で、既存の (time, model_id) が存在しない行
            range_mask = df_ohlc["time"] <= pd.Timestamp(end_time)
            df_range = df_ohlc[range_mask].copy()
            missing_mask = df_range.apply(
                lambda row: (row["time"], model_id) not in existing_keys, axis=1
            )
            df_missing = df_range[missing_mask].copy()
        else:
            # 従来通り: 未来方向の未推論分
            if t_proba_last is None:
                # 初回実行: 末尾100行を対象（過去データが必要なため）
                missing_mask = df_ohlc.index >= max(0, len(df_ohlc) - 100)
            else:
                # 通常: t_proba_lastより後の行で、既存の (time, model_id) が存在しない行
                future_mask = df_ohlc["time"] > t_proba_last
                df_future = df_ohlc[future_mask].copy()
                missing_mask = df_future.apply(
                    lambda row: (row["time"], model_id) not in existing_keys, axis=1
                )
                df_missing = df_future[missing_mask].copy()
                # missing_mask は df_future のインデックスなので、df_ohlc のインデックスに変換
                missing_mask = df_ohlc.index.isin(df_future[missing_mask].index)
                df_missing = df_ohlc[missing_mask].copy()

        missing_count = len(df_missing)

        if missing_count == 0:
            # 未推論分なし
            return

        # 7) 特徴量生成（過去データを含む範囲で計算）
        from app.strategies.ai_strategy import build_features

        # active_model.json の expected_features に vol_chg があるため、
        # tick_volume / real_volume が存在する場合は特徴量生成へ渡す（無ければ従来通り）
        required_cols = ["time", "open", "high", "low", "close"]
        for opt in ["tick_volume", "real_volume"]:
            if opt in df_ohlc.columns:
                required_cols.append(opt)

        if not all(col in df_ohlc.columns for col in required_cols):
            logger.warning("[lgbm] CSV missing required columns")
            return

        try:
            df_feat = build_features(df_ohlc[required_cols], {"feature_recipe": "ohlcv_tech_v1"})
        except Exception as e:
            logger.warning(f"[lgbm] feature generation failed: {e}")
            return

        # --- 観測ログ（features作成直後） ---
        # - 欠損率（上位）/行ハッシュ（先頭3/末尾3）で「同一入力連発」や「欠損→0埋め由来」を切り分ける
        try:
            meta_for_obs = None
            try:
                meta_for_obs = load_active_model_meta()
            except Exception:
                meta_for_obs = None
            exp_feats = None
            try:
                if isinstance(meta_for_obs, dict):
                    exp_feats = (
                        meta_for_obs.get("expected_features")
                        or meta_for_obs.get("feature_order")
                        or meta_for_obs.get("features")
                    )
                if not isinstance(exp_feats, list):
                    exp_feats = None
            except Exception:
                exp_feats = None

            # 未推論対象の time（上限n=2000）
            ts_list = df_missing["time"].tolist()
            ts_list = ts_list[-2000:] if len(ts_list) > 2000 else ts_list

            df_feat_indexed = df_feat.set_index("time", drop=False)
            X_new = df_feat_indexed.loc[ts_list]
            # loc が単一行だと Series になることがあるので DataFrame化
            if isinstance(X_new, pd.Series):
                X_new = X_new.to_frame().T
            # time列は除外
            if "time" in X_new.columns:
                X_body = X_new.drop(columns=["time"])
            else:
                X_body = X_new

            # 欠損率 top
            na_top = None
            try:
                na_rate = X_body.isna().mean().sort_values(ascending=False).head(10)
                na_top = {str(k): float(v) for k, v in na_rate.items()}
            except Exception:
                na_top = None

            # expected_features 整合（存在/欠落の観測）
            exp_meta = None
            exp_missing = None
            exp_present = None
            try:
                if isinstance(exp_feats, list):
                    exp_meta = [str(x) for x in exp_feats if isinstance(x, str)]
                    cols_set = set([str(c) for c in X_body.columns])
                    exp_present = [c for c in exp_meta if c in cols_set]
                    exp_missing = [c for c in exp_meta if c not in cols_set]
            except Exception:
                exp_meta = None
                exp_missing = None
                exp_present = None

            # 行ハッシュ（軽量：round(6)して連結→sha1）
            import hashlib
            import math

            def _row_sig(row: pd.Series) -> str:
                vals: list[str] = []
                for v in row.tolist():
                    try:
                        fv = float(v)
                        if not math.isfinite(fv):
                            vals.append("NA")
                        else:
                            vals.append(f"{fv:.6f}")
                    except Exception:
                        vals.append("NA")
                s = "|".join(vals).encode("utf-8")
                return hashlib.sha1(s).hexdigest()[:12]

            head_hash = None
            tail_hash = None
            try:
                head_hash = [_row_sig(X_body.iloc[i]) for i in range(min(3, len(X_body)))]
                tail_hash = [_row_sig(X_body.iloc[i]) for i in range(max(0, len(X_body) - 3), len(X_body))]
            except Exception:
                head_hash = None
                tail_hash = None

            logger.info(
                "[lgbm_proba][features] X_new shape={} cols={} na_top={} expected_n={} expected_present_n={} expected_missing_n={} missing_examples={} head_hash={} tail_hash={}",
                tuple(getattr(X_body, "shape", (None, None))),
                int(len(getattr(X_body, "columns", []))),
                na_top,
                int(len(exp_meta)) if isinstance(exp_meta, list) else None,
                int(len(exp_present)) if isinstance(exp_present, list) else None,
                int(len(exp_missing)) if isinstance(exp_missing, list) else None,
                (exp_missing[:10] if isinstance(exp_missing, list) else None),
                head_hash,
                tail_hash,
            )

            # expected_features に揃えた入力ベクトルが「実質同一」になってないか（先頭3/末尾3だけ）
            try:
                if isinstance(exp_meta, list) and exp_meta:
                    def _vec_sig(row: pd.Series) -> str:
                        vals: list[str] = []
                        for k in exp_meta:
                            try:
                                v = row.get(k, 0.0)
                                fv = float(v)
                                vals.append(f"{fv:.6f}" if math.isfinite(fv) else "NA")
                            except Exception:
                                vals.append("NA")
                        return hashlib.sha1("|".join(vals).encode("utf-8")).hexdigest()[:12]

                    head_vec = []
                    for i in range(min(3, len(X_body))):
                        head_vec.append(_vec_sig(X_body.iloc[i]))
                    tail_vec = []
                    for i in range(max(0, len(X_body) - 3), len(X_body)):
                        tail_vec.append(_vec_sig(X_body.iloc[i]))
                    logger.info(
                        "[lgbm_proba][features] vec_sig (expected_features) head={} tail={}",
                        head_vec,
                        tail_vec,
                    )
            except Exception as e:
                logger.warning("[lgbm_proba][features] vec_sig failed: {}", e)
        except Exception as e:
            logger.warning("[lgbm_proba][features] failed: {}", e)

        # 8) 未推論分だけ推論実行
        ai_service = get_ai_service()
        rows_to_append: list[dict[str, Any]] = []
        appended_count = 0

        # df_featをtime列でインデックス化（dropnaで行数が減る可能性があるため）
        df_feat_indexed = df_feat.set_index("time", drop=False)

        for ohlc_idx, row_ohlc in df_missing.iterrows():
            # 重複チェック（既にproba CSVに存在する (time, model_id) はスキップ）
            ts = row_ohlc["time"]
            if (ts, model_id) in existing_keys:
                continue

            # time列でマッチング（dropnaで行数が減る可能性があるため）
            if ts not in df_feat_indexed.index:
                logger.warning(f"[lgbm] time not found in features: time={ts}")
                continue

            try:
                row_feat = df_feat_indexed.loc[ts]

                # 特徴量をdictに変換（AISvc.predict用）
                feat_dict = row_feat.drop("time").to_dict()
                # NaNを0で埋める
                feat_dict = {k: (float(v) if pd.notna(v) else 0.0) for k, v in feat_dict.items()}

                # 推論実行
                prob_out = ai_service.predict(feat_dict, no_metrics=True)
                if not prob_out:
                    logger.warning(f"[lgbm] predict returned None for time={ts}")
                    continue

                p_buy = float(getattr(prob_out, "p_buy", 0.0))
                p_sell = float(getattr(prob_out, "p_sell", 0.0))

                # 追記行を構築
                row_dict = {
                    "time": ts,
                    "prob_buy": p_buy,
                    "prob_sell": p_sell,
                    "model_id": model_id,
                }
                rows_to_append.append(row_dict)
                existing_keys.add((ts, model_id))  # 重複チェック用に追加
                appended_count += 1

            except Exception as e:
                logger.warning(f"[lgbm] failed for time={ts}: {e}")
                continue

        # 9) proba CSVに追記
        if rows_to_append:
            df_new = pd.DataFrame(rows_to_append)
            # --- 観測ログ（書き込み前: prewrite） ---
            try:
                _obs_prob_df("prewrite", df_new, time_col="time", prob_col="prob_buy", n=2000)
                # dtype観測
                try:
                    logger.info(
                        "[lgbm_proba][prewrite] dtypes prob_buy={} prob_sell={} model_id={}",
                        str(df_new.get("prob_buy").dtype) if "prob_buy" in df_new.columns else None,
                        str(df_new.get("prob_sell").dtype) if "prob_sell" in df_new.columns else None,
                        str(df_new.get("model_id").dtype) if "model_id" in df_new.columns else None,
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.warning("[lgbm_proba][prewrite] failed: {}", e)

            # CSVが存在する場合は追記、存在しない場合は新規作成
            if proba_csv_path.exists():
                # --- 観測ログ（保存直前: write） ---
                logger.info(
                    "[lgbm_proba][write] path={} mode=a header=false index=false date_format={} float_format=None round_used=None",
                    str(proba_csv_path),
                    "%Y-%m-%d %H:%M:%S",
                )
                df_new.to_csv(proba_csv_path, mode="a", header=False, index=False, date_format="%Y-%m-%d %H:%M:%S")
            else:
                logger.info(
                    "[lgbm_proba][write] path={} mode=w header=true index=false date_format={} float_format=None round_used=None",
                    str(proba_csv_path),
                    "%Y-%m-%d %H:%M:%S",
                )
                df_new.to_csv(proba_csv_path, mode="w", header=True, index=False, date_format="%Y-%m-%d %H:%M:%S")

        # 10) 必須ログ
        range_start_str = start_time.strftime("%Y-%m-%d %H:%M:%S") if start_time else "none"
        range_end_str = end_time.strftime("%Y-%m-%d %H:%M:%S") if end_time else "none"
        logger.info(
            "[lgbm] symbol={} tf={} model_id={} range_start={} range_end={} missing={} appended={}",
            symbol,
            tf,
            model_id,
            range_start_str,
            range_end_str,
            missing_count,
            appended_count,
        )

    except Exception as e:
        logger.error(f"[lgbm] ensure_lgbm_proba_uptodate failed: {e}")


if __name__ == "__main__":
    main()
