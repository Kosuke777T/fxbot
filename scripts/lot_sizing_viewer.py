from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QAbstractItemView  # PyQt6 enums are scoped under the class


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "decisions"


def load_latest_decisions() -> pd.DataFrame:
    if not LOG_DIR.exists():
        print(f"[warn] LOG_DIR not found: {LOG_DIR}")
        return pd.DataFrame()

    files = sorted(LOG_DIR.glob("decisions_*.jsonl"))
    if not files:
        print("[warn] no decisions_*.jsonl found")
        return pd.DataFrame()

    latest = files[-1]
    print(f"[info] using latest decision log: {latest}")

    rows: list[dict] = []
    for line in latest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue

        lot_info = rec.get("lot_info") or {}

        row = {
            # ts_jst があればそれを使い、なければ ts を使う
            "ts": rec.get("ts_jst") or rec.get("ts"),
            "symbol": rec.get("symbol"),
            "decision": rec.get("decision"),
            "lot": rec.get("lot"),
            # risk_pct は lot_info 優先（トップレベルにあればフォールバック）
            "risk_pct": lot_info.get("risk_pct", rec.get("risk_pct")),
            "atr": lot_info.get("atr"),
            "equity": lot_info.get("equity"),
            "per_trade_risk_pct": lot_info.get("per_trade_risk_pct"),
            "est_month_vol": lot_info.get("est_monthly_volatility_pct"),
            "est_max_month_dd": lot_info.get("est_max_monthly_dd_pct"),
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # 時系列順に並べる（ts があれば）
    if "ts" in df.columns:
        df = df.sort_values("ts")
    return df


class LotSizingViewer(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LotSizingResult Viewer")

        self.table = QtWidgets.QTableWidget(self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.summary_label = QtWidgets.QLabel(self)
        self.reload_button = QtWidgets.QPushButton("最新ログを再読み込み", self)
        self.reload_button.clicked.connect(self.reload)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table)
        layout.addWidget(self.reload_button)

        self.resize(900, 600)
        self.reload()

    def reload(self) -> None:
        df = load_latest_decisions()
        if df.empty:
            self.summary_label.setText("決定ログが見つかりません。dryrun か live を1回回してください。")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return

        # テーブルに反映
        cols = list(df.columns)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(df))

        for r, (_, row) in enumerate(df.iterrows()):
            for c, col in enumerate(cols):
                val = row[col]
                text = "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
                item = QtWidgets.QTableWidgetItem(text)
                self.table.setItem(r, c, item)

        self.table.resizeColumnsToContents()

        # サマリー：平均ロット、平均リスクなど
        avg_lot = df["lot"].mean() if "lot" in df.columns else float("nan")
        avg_risk_pct = df["risk_pct"].mean() if "risk_pct" in df.columns else float("nan")
        avg_est_dd = df["est_max_month_dd"].mean() if "est_max_month_dd" in df.columns else float("nan")

        self.summary_label.setText(
            f"件数={len(df)} | 平均 lot={avg_lot:.5f} | "
            f"平均 risk_pct={avg_risk_pct:.4%} | "
            f"推定最大月次DDの平均={avg_est_dd:.2%}"
        )


def main() -> None:
    import sys

    app = QtWidgets.QApplication(sys.argv)
    viewer = LotSizingViewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
