from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import csv

# BacktestRun が出力する標準パス
# backtests/{profile}/monthly_returns.csv を読む
BACKTEST_ROOT = Path("backtests")

# KPI 仕様の「月3%」目標値
TARGET_MONTHLY_RETURN = 0.03

def _bands_from_timeline(timeline_rows, equity_times):
    """
    Step2-18:
    next_action_timeline.csv の「変化点(time, kind, reason)」から
    GUI向けの bands（start/end, kind, reason）を構築する。

    timeline_rows: list[dict] with keys time, kind, reason
    equity_times : pandas.Series or list-like of datetime/str
    """
    try:
        import pandas as pd
    except Exception:
        pd = None  # type: ignore

    if not timeline_rows:
        return []

    # equity_times の min/max を終端として使う（最後の帯を閉じるため）
    try:
        if pd is not None:
            ts = pd.to_datetime(equity_times, errors='coerce')
            ts = ts.dropna()
            if len(ts) == 0:
                end_ts = None
            else:
                end_ts = ts.max()
        else:
            end_ts = None
    except Exception:
        end_ts = None

    # timeline を時刻でソート
    def _to_dt(x):
        if pd is None:
            return str(x)
        return pd.to_datetime(x, errors='coerce')

    rows = []
    for r in timeline_rows:
        t = r.get('time', '')
        k = r.get('kind', '')
        reason = r.get('reason', '') or ''
        rows.append({'time': t, 'kind': k, 'reason': reason})

    if pd is not None:
        rows.sort(key=lambda r: (_to_dt(r['time']) if _to_dt(r['time']) is not None else pd.Timestamp.min))
    else:
        rows.sort(key=lambda r: r['time'])

    bands = []
    for i, r in enumerate(rows):
        start = r['time']
        kind = r['kind']
        reason = r.get('reason', '') or ''

        if i + 1 < len(rows):
            end = rows[i+1]['time']
        else:
            # 最終帯：equity の最終時刻まで伸ばす（取れなければ start のまま）
            end = str(end_ts) if end_ts is not None else start

        bands.append({
            'start': start,
            'end': end,
            'kind': kind,
            'reason': reason,
        })

    return bands
def _try_load_next_action_timeline(run_dir: Path):
    """
    Step2-18: core/backtest が出力する run/next_action_timeline.csv を最優先で読む。
    無ければ None を返し、従来フォールバックに回す。
    """
    p = run_dir / 'next_action_timeline.csv'
    if not p.exists():
        return None
    rows = []
    try:
        with p.open('r', encoding='utf-8', newline='') as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append({
                    'time': row.get('time',''),
                    'kind': row.get('kind',''),
                    'reason': row.get('reason',''),
                })
        return rows
    except Exception:
        return None
class KpiMonthlyRecord:
    year_month: str
    return_pct: float
    max_dd_pct: float
    total_trades: int
    pf: float


@dataclass
class KpiDashboard:
    profile: str
    has_backtest: bool
    current_month: Optional[str]
    current_month_return: float
    current_month_progress: float  # 0.0〜2.0（=0〜200%）でクリップ
    monthly: List[KpiMonthlyRecord]


class KPIService:
    """バックテスト結果を元に KPI ダッシュボード用のデータを作るサービス."""

    def __init__(
        self,
        backtest_root: Optional[Path] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        """
        backtest_root:
            - 明示的に backtests ルートディレクトリを指定したい場合に使用
            - 例: KPIService(backtest_root=Path("backtests"))

        base_dir:
            - 旧仕様互換用。
            - 例: KPIService(base_dir=Path(".")) のような呼び出しをサポートする。
            - base_dir が指定された場合は base_dir / "backtests" を backtest_root とみなす。
        """
        if backtest_root is not None:
            self.backtest_root = Path(backtest_root)
        elif base_dir is not None:
            self.backtest_root = Path(base_dir) / "backtests"
        else:
            self.backtest_root = BACKTEST_ROOT
        # monthly_returns の簡易キャッシュ（必要なら）
        self._monthly_cache: dict[str, pd.DataFrame] = {}

    # 仕様書で書いてある標準メソッド名
    # load_backtest_kpi_summary(profile) -> dict
    def load_backtest_kpi_summary(self, profile: str) -> Dict[str, Any]:
        """
        バックテストKPIサマリを読み込む（仕様書 v5.1 準拠）。

        Parameters
        ----------
        profile : str
            プロファイル名（例: "michibiki_std"）

        Returns
        -------
        Dict[str, Any]
            {
                "profile": str,
                "has_backtest": bool,
                "current_month": str | None,
                "current_month_return": float,
                "current_month_progress": float,
                "monthly": List[Dict],
                # 追加統計（過去12ヶ月）
                "avg_return_pct": float,
                "max_dd_pct": float,
                "win_rate": float,
                "avg_pf": float,
            }
        """
        try:
            dashboard = self.compute_monthly_dashboard(profile)

            # 過去12ヶ月の統計を計算
            monthly_list = dashboard.monthly
            avg_return_pct = 0.0
            max_dd_pct = 0.0
            win_rate = 0.0
            avg_pf = 0.0

            if monthly_list:
                returns = [m.return_pct for m in monthly_list]
                dds = [m.max_dd_pct for m in monthly_list]
                pfs = [m.pf for m in monthly_list if m.pf > 0]

                avg_return_pct = sum(returns) / len(returns) if returns else 0.0
                max_dd_pct = min(dds) if dds else 0.0
                win_rate = sum(1 for r in returns if r > 0) / len(returns) if returns else 0.0
                avg_pf = sum(pfs) / len(pfs) if pfs else 0.0

            # GUI から扱いやすいように dict 化
            return {

                "profile": dashboard.profile,
                "has_backtest": dashboard.has_backtest,
                "current_month": dashboard.current_month,
                "current_month_return": dashboard.current_month_return,
                "current_month_progress": dashboard.current_month_progress,
                "monthly": [
                    {
                        "year_month": m.year_month,
                        "return_pct": m.return_pct,
                        "max_dd_pct": m.max_dd_pct,
                        "total_trades": m.total_trades,
                        "pf": m.pf,
                    }
                    for m in monthly_list
                ],
                # 追加統計（過去12ヶ月）
                "avg_return_pct": avg_return_pct,
                "max_dd_pct": max_dd_pct,
                "win_rate": win_rate,
                "avg_pf": avg_pf,
            }
        except Exception as e:
            # 例外はすべて握り、安全な dict を返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] load_backtest_kpi_summary error: {e}")
            return {
                "profile": profile,
                "has_backtest": False,
                "current_month": None,
                "current_month_return": 0.0,
                "current_month_progress": 0.0,
                "monthly": [],
                "avg_return_pct": 0.0,
                "max_dd_pct": 0.0,
                "win_rate": 0.0,
                "avg_pf": 0.0,
            }

    # 仕様書 v5 の「compute_monthly_dashboard(profile)」実体
    def compute_monthly_dashboard(self, profile: str) -> KpiDashboard:
        """
        月次ダッシュボードデータを計算する（仕様書 v5.1 準拠）。

        Parameters
        ----------
        profile : str
            プロファイル名

        Returns
        -------
        KpiDashboard
            ダッシュボードデータ（例外時は空データを返す）
        """
        try:
            df = self._load_monthly_returns(profile)

            if df.empty:
                # まだバックテストしていない場合
                return KpiDashboard(
                    profile=profile,
                    has_backtest=False,
                    current_month=None,
                    current_month_return=0.0,
                    current_month_progress=0.0,
                    monthly=[],
                )

            # 直近 12 ヶ月だけを KPI の対象にする
            df_12 = df.tail(12).copy()

            # 現在の年月キー（例: 2025-12）
            now = datetime.now()
            ym_now = f"{now.year:04d}-{now.month:02d}"

            row_now = df_12.loc[df_12["year_month"] == ym_now]
            if row_now.empty:
                # 今月分がまだ無ければ、最後の行を「最新」と見なす
                row = df_12.iloc[-1]
            else:
                row = row_now.iloc[0]

            current_month = str(row["year_month"])
            current_return = float(row["return_pct"])
            progress = self.compute_target_progress(current_return)

            monthly_records = [
                KpiMonthlyRecord(
                    year_month=str(r["year_month"]),
                    return_pct=float(r["return_pct"]),
                    max_dd_pct=float(r["max_dd_pct"]),
                    total_trades=int(r["total_trades"]),
                    pf=float(r["pf"]),
                )
                for _, r in df_12.iterrows()
            ]

            return KpiDashboard(
                profile=profile,
                has_backtest=True,
                current_month=current_month,
                current_month_return=current_return,
                current_month_progress=progress,
                monthly=monthly_records,
            )
        except Exception as e:
            # 例外はすべて握り、安全なデータを返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] compute_monthly_dashboard error: {e}")
            return KpiDashboard(
                profile=profile,
                has_backtest=False,
                current_month=None,
                current_month_return=0.0,
                current_month_progress=0.0,
                monthly=[],
            )

    def compute_target_progress(
        self,
        return_pct: float,
        target: float = TARGET_MONTHLY_RETURN,
    ) -> float:
        """
        月3%に対する進捗率（0.0〜2.0=0〜200%）を返す。

        Parameters
        ----------
        return_pct : float
            月次リターン（小数形式、例: 0.03 = 3%）
        target : float, optional
            目標リターン（デフォルト: 0.03 = 3%）

        Returns
        -------
        float
            進捗率（0.0〜2.0）。None や NaN の場合は 0.0 を返す。
        """
        try:
            # None や NaN の場合は 0.0 を返す
            if return_pct is None:
                return 0.0

            import math
            if math.isnan(return_pct):
                return 0.0

            return_pct = float(return_pct)
            target = float(target)

            if target <= 0:
                return 0.0

            raw = return_pct / target
            # 仕様上 0〜200% を想定しているので 0.0〜2.0 にクリップ
            # （ゲージ側で ×100 してパーセント表示）
            return max(0.0, min(2.0, raw))
        except (TypeError, ValueError, ZeroDivisionError):
            # 例外はすべて握り、安全な値を返す（仕様書 v5.1 のポリシー）
            return 0.0

    def compute_trade_stats(self, profile: str) -> dict:
        """
        バックテスト or 実運用のトレード結果から
        勝率・PF・平均RRなどを算出する。

        現段階ではバックテスト側の
        backtests/{profile}/trades.csv を主な情報源とする。
        将来、実運用ログ（decisionsや専用トレードログ）が
        整備されたらここに統合する。
        """
        try:
            import math

            trades_path = self.backtest_root / profile / "trades.csv"
            if not trades_path.exists():
                # トレード情報が無い場合は安全なデフォルト
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                }

            df = pd.read_csv(trades_path)

            # 必須カラムチェック
            if "pnl" not in df.columns:
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                    "error": "trades.csv に pnl 列がありません",
                }

            # RR 列は任意
            rr_col = "rr" if "rr" in df.columns else None

            # NaN を落とす（安全側）
            df = df.copy()
            df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
            df = df.dropna(subset=["pnl"])

            if df.empty:
                return {
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "avg_rr": 0.0,
                    "total_trades": 0,
                }

            wins = df[df["pnl"] > 0]["pnl"]
            losses = df[df["pnl"] < 0]["pnl"]

            total_trades = len(df)
            win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

            if len(losses) > 0:
                pf = wins.sum() / abs(losses.sum()) if abs(losses.sum()) > 0 else math.inf
            else:
                # 負けが一度もない場合は PF を大きな値で扱う
                pf = math.inf

            if rr_col is not None:
                df_rr = pd.to_numeric(df[rr_col], errors="coerce").dropna()
                avg_rr = float(df_rr.mean()) if not df_rr.empty else 0.0
            else:
                avg_rr = 0.0

            return {
                "win_rate": float(win_rate),
                "pf": float(pf),
                "avg_rr": float(avg_rr),
                "total_trades": int(total_trades),
            }

        except Exception as e:
            # GUIには例外を渡さず、安全なデフォルト＋エラー文字列だけ返す
            return {
                "win_rate": 0.0,
                "pf": 0.0,
                "avg_rr": 0.0,
                "total_trades": 0,
                "error": str(e),
            }

    # --- 内部関数 ---

    def _load_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        BacktestRun が出力した monthly_returns.csv を読み込む。

        Parameters
        ----------
        profile : str
            プロファイル名

        Returns
        -------
        pd.DataFrame
            monthly_returns.csv の内容。ファイルが存在しない場合は空 DataFrame。
            例外時も空 DataFrame を返す。
        """
        try:
            csv_path = self.backtest_root / profile / "monthly_returns.csv"

            if not csv_path.exists():
                # まだバックテストしていない場合は空 DataFrame
                return pd.DataFrame(
                    columns=[
                        "year_month",
                        "return_pct",
                        "max_dd_pct",
                        "total_trades",
                        "pf",
                    ]
                )

            df = pd.read_csv(csv_path)

            # 型を一応そろえておく（念のため）
            df["year_month"] = df["year_month"].astype(str)
            df["return_pct"] = pd.to_numeric(df["return_pct"], errors="coerce").fillna(0.0).astype(float)
            df["max_dd_pct"] = pd.to_numeric(df["max_dd_pct"], errors="coerce").fillna(0.0).astype(float)
            df["total_trades"] = pd.to_numeric(df["total_trades"], errors="coerce").fillna(0).astype(int)
            df["pf"] = pd.to_numeric(df["pf"], errors="coerce").fillna(0.0).astype(float)

            return df
        except Exception as e:
            # 例外はすべて握り、空 DataFrame を返す（仕様書 v5.1 のポリシー）
            print(f"[KPIService] _load_monthly_returns error: {e}")
            return pd.DataFrame(
                columns=[
                    "year_month",
                    "return_pct",
                    "max_dd_pct",
                    "total_trades",
                    "pf",
                ]
            )

    def load_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        指定プロファイルの monthly_returns.csv を読み込んで返す。
        必須フォーマット:
          year_month, return_pct, max_dd_pct, total_trades, pf
        """
        return self._load_monthly_returns(profile)

    def refresh_monthly_returns(self, profile: str) -> pd.DataFrame:
        """
        BacktestRun が monthly_returns.csv を更新した後に呼び出す前提。
        キャッシュを捨てて最新の monthly_returns を返す。
        """
        # キャッシュを使っている場合は破棄
        if hasattr(self, "_monthly_cache"):
            self._monthly_cache.pop(profile, None)

        df = self.load_monthly_returns(profile)

        # ここで KPI 用の派生データを更新してもよい
        # （例：self._kpi_summary[profile] = self._build_kpi_summary(df) など）

        return df


    def load_equity_curve_with_action_bands(self, profile: str, symbol: str = "USDJPY-") -> dict:
        """バックテスト資産曲線(equity_curve)と、HOLD/BLOCKED帯（背景用）を返す。
        - GUIは描画のみ（生成ロジックを持たない）
        - ログ直読みは禁止のため、servicesがファイル読取→最小整形して返す
        戻り:
          {
            "equity": [{"time": "...", "equity": 100000.0}, ...],
            "bands": [{"start": "...", "end": "...", "kind": "HOLD|BLOCKED", "reason": "..."}, ...],
            "source": {"equity_curve_csv": "...", "decisions_jsonl": "...|None"},
            "counts": {"HOLD": n, "BLOCKED": n, "total": n},
          }
        """
        from pathlib import Path
        import json
        import re

        # 1) 最新の equity_curve.csv を探索（logs/backtest 配下を優先）
        root = Path("logs/backtest") / symbol
        if not root.exists():
            return {"equity": [], "bands": [], "source": {"equity_curve_csv": None, "decisions_jsonl": None},
                    "counts": {"HOLD": 0, "BLOCKED": 0, "total": 0}, "warnings": ["backtest_root_not_found"]}

        equity_paths = sorted(root.glob("**/equity_curve.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not equity_paths:
            return {"equity": [], "bands": [], "source": {"equity_curve_csv": None, "decisions_jsonl": None},
                    "counts": {"HOLD": 0, "BLOCKED": 0, "total": 0}, "warnings": ["equity_curve_not_found"]}

        equity_csv = equity_paths[0]
        df_eq = pd.read_csv(equity_csv)
        # time/timestamp 揺れ吸収（仕様書: time）
        if "timestamp" not in df_eq.columns and "time" in df_eq.columns:
            df_eq = df_eq.rename(columns={"time": "timestamp"})
        if "timestamp" not in df_eq.columns or "equity" not in df_eq.columns:
            return {"equity": [], "bands": [], "source": {"equity_curve_csv": str(equity_csv), "decisions_jsonl": None},
                    "counts": {"HOLD": 0, "BLOCKED": 0, "total": 0}, "warnings": ["equity_curve_columns_invalid"]}

        df_eq["timestamp"] = pd.to_datetime(df_eq["timestamp"], errors="coerce")
        df_eq = df_eq.dropna(subset=["timestamp"]).sort_values("timestamp")

        # --- Step2-18: try loading next_action_timeline.csv first ---
        timeline_data = _try_load_next_action_timeline(equity_csv.parent)
        bands = []
        dec_path = None

        # Step2-18: timeline -> bands (authoritative)
        if timeline_data:
            try:
                bands = _bands_from_timeline(timeline_data, df_eq['timestamp'])
            except Exception:
                bands = []

        # timeline が無い場合は従来の decisions フォールバックを使用
        if not timeline_data:
            # 2) decisions*.jsonl を探索（同ディレクトリ → 無ければ logs/decisions_*.jsonl にフォールバック）
            ddir = equity_csv.parent
            dec_paths = sorted(ddir.glob("decisions*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
            dec_path = dec_paths[0] if dec_paths else None

            # fallback: 実行系が出すグローバル decisions ログを参照（ただし equity の期間に近いものを選ぶ）
            if dec_path is None:
                glb = Path("logs")
                glb_paths = list(glb.glob("decisions_*.jsonl"))
                if glb_paths:
                    # equity の end 日付に最も近い（基本は end 以前） decisions を採用
                    eq_end = df_eq["timestamp"].max()
                    def _date_from_name(path: Path):
                        m = re.search(r"decisions_(\d{4}-\d{2}-\d{2})\.jsonl$", path.name)
                        return m.group(1) if m else None
                    scored = []
                    for fp in glb_paths:
                        ds = _date_from_name(fp)
                        if not ds:
                            continue
                        try:
                            d = pd.to_datetime(ds, errors="coerce")
                            if pd.isna(d):
                                continue
                            # score: end 以前の decisions だけ採用（期間外=未来ログはバックテスト帯に使わない）
                            delta = (eq_end.normalize() - d).days
                            if delta >= 0:
                                score = abs(delta)
                                scored.append((score, fp))
                        except Exception:
                            continue
                    if scored:
                        scored.sort(key=lambda x: x[0])
                        dec_path = scored[0][1]
                    else:
                        # end 以前の decisions が無い（または日付抽出不可）場合は採用しない
                        dec_path = None

            bands = []
            rows = []  # Step2-18: ensure defined for fallback
            if dec_path is not None:
                rows = []
                with dec_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue
            if rows:

                df_dec = pd.DataFrame(rows)

                # --- schema normalize (decisions jsonl) ---
                # v5.2 decisions は timestamp/reason ではなく ts_jst / filter_reasons の場合がある
                if "timestamp" not in df_dec.columns and "ts_jst" in df_dec.columns:
                    df_dec = df_dec.rename(columns={"ts_jst": "timestamp"})
                # timestamp カラムのタイムゾーンを削除（equity と型を合わせる）
                if "timestamp" in df_dec.columns:
                    df_dec["timestamp"] = pd.to_datetime(df_dec["timestamp"], errors="coerce")
                    # タイムゾーン付きの場合は削除
                    if df_dec["timestamp"].dtype.tz is not None:
                        df_dec["timestamp"] = df_dec["timestamp"].dt.tz_localize(None)
                if "reason" not in df_dec.columns and "filter_reasons" in df_dec.columns:
                    def _one_line_reason(x):
                        if x is None:
                            return None
                        # list / dict / str を想定して tooltip 向けに潰す
                        try:
                            if isinstance(x, list):
                                txt = "; ".join([str(i) for i in x if i is not None])
                            elif isinstance(x, dict):
                                txt = "; ".join([f"{k}={v}" for k,v in x.items()])
                            else:
                                txt = str(x)
                        except Exception:
                            txt = str(x)
                        txt = txt.replace("\n", " ").strip()
                        # 長すぎるのは1行に収める
                        return txt[:200] if len(txt) > 200 else txt
                    df_dec["reason"] = df_dec["filter_reasons"].apply(_one_line_reason)
                # --- end normalize ---

                # timestampキーの揺れ吸収
                if "timestamp" not in df_dec.columns and "time" in df_dec.columns:
                    df_dec = df_dec.rename(columns={"time": "timestamp"})
                if "timestamp" in df_dec.columns:
                    df_dec["timestamp"] = pd.to_datetime(df_dec["timestamp"], errors="coerce")
                    df_dec = df_dec.dropna(subset=["timestamp"]).sort_values("timestamp")

                    # equityの各timestampに直近のdecisionをasof結合
                    df_m = pd.merge_asof(
                        df_eq[["timestamp"]].copy(),
                        df_dec[["timestamp", "filter_pass", "reason"]].copy(),
                        on="timestamp",
                        direction="backward",
                    )

                    def _classify(fp, reason):
                        # 暫定ルール（T-43-3 Step2-16の前提に合わせる）
                        if fp is False:
                            return "BLOCKED"
                        r = (reason or "")
                        r = str(r)
                        # "入らない"系は HOLD とみなす（暫定）
                        if ("threshold" in r and "pass" not in r) or ("below" in r) or (r in ("no_signal","hold","wait")):
                            return "HOLD"
                        return None

                    states = []
                    for _, row in df_m.iterrows():
                        states.append((_classify(row.get("filter_pass"), row.get("reason")), row.get("reason")))

                    # 連続区間に圧縮（kindが同じ間をまとめる）
                    cur_kind = None
                    cur_reason = None
                    cur_start = None
                    prev_ts = None

                    ts_list = df_eq["timestamp"].tolist()
                    for i, ts in enumerate(ts_list):
                        kind, reason = states[i]
                        if kind != cur_kind:
                            # close previous
                            if cur_kind in ("HOLD","BLOCKED") and cur_start is not None and prev_ts is not None:
                                bands.append({
                                    "start": cur_start.isoformat(),
                                    "end": prev_ts.isoformat(),
                                    "kind": cur_kind,
                                    "reason": (str(cur_reason) if cur_reason is not None else None),
                                })
                            # open new
                            cur_kind = kind
                            cur_reason = reason
                            cur_start = ts if kind in ("HOLD","BLOCKED") else None
                        prev_ts = ts

                    # close tail
                    if cur_kind in ("HOLD","BLOCKED") and cur_start is not None and prev_ts is not None:
                        bands.append({
                            "start": cur_start.isoformat(),
                            "end": prev_ts.isoformat(),
                            "kind": cur_kind,
                            "reason": (str(cur_reason) if cur_reason is not None else None),
                        })

        equity = [{"time": t.isoformat(), "equity": float(v)} for t, v in zip(df_eq["timestamp"], df_eq["equity"])]

        counts = {"HOLD": 0, "BLOCKED": 0, "total": len(bands)}
        for b in bands:
            k = b.get("kind")
            if k in counts:
                counts[k] += 1

        # Step2-18: timeline がある場合、decisions の欠損警告は出さない
        warnings = ([] if dec_path is not None else ["decisions_jsonl_not_found"])
        try:
            if 'timeline_data' in locals() and timeline_data and isinstance(warnings, list):
                warnings = [w for w in warnings if w != 'decisions_jsonl_not_found']
        except Exception:
            pass

        # Step2-18: recompute counts from bands
        try:
            _c_hold = sum(1 for b in bands if b.get('kind') == 'HOLD') if isinstance(bands, list) else 0
            _c_blk  = sum(1 for b in bands if b.get('kind') == 'BLOCKED') if isinstance(bands, list) else 0
            counts = {'HOLD': int(_c_hold), 'BLOCKED': int(_c_blk), 'total': int(_c_hold + _c_blk)}
        except Exception:
            pass

        return {
            "equity": equity,
            "bands": bands,
            "source": {"equity_curve_csv": str(equity_csv), "decisions_jsonl": (str(dec_path) if dec_path else None)},
            "counts": counts,
            "warnings": warnings,
        }
