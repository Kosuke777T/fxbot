# app/gui/widgets/shap_bar.py
from __future__ import annotations

from typing import Dict, Optional
import json
from pathlib import Path

import pandas as pd
from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QHeaderView
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.services.ai_service import AISvc


class ShapBarWidget(QtWidgets.QWidget):
    """
    AISvc.get_shap_top_features() の結果を棒グラフ＋テーブルで表示するウィジェット。
    """

    def __init__(self, ai_service: AISvc, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.ai_service = ai_service
        self._df_cache: Optional[pd.DataFrame] = None
        self._alias: Dict[str, str] = self._load_alias()

        self.spin_top_n = QtWidgets.QSpinBox()
        self.spin_top_n.setRange(1, 200)
        self.spin_top_n.setValue(20)

        self.spin_cache_sec = QtWidgets.QSpinBox()
        self.spin_cache_sec.setRange(0, 24 * 3600)
        self.spin_cache_sec.setSingleStep(60)
        self.spin_cache_sec.setValue(300)

        self.btn_refresh = QtWidgets.QPushButton("Recalc SHAP")

        ctrl_layout = QtWidgets.QHBoxLayout()
        ctrl_layout.addWidget(QtWidgets.QLabel("Top N:"))
        ctrl_layout.addWidget(self.spin_top_n)
        ctrl_layout.addSpacing(12)
        ctrl_layout.addWidget(QtWidgets.QLabel("Cache TTL (sec):"))
        ctrl_layout.addWidget(self.spin_cache_sec)
        ctrl_layout.addStretch(1)
        ctrl_layout.addWidget(self.btn_refresh)

        self.lbl_top1 = QtWidgets.QLabel("-")
        self.lbl_top2 = QtWidgets.QLabel("-")
        self.lbl_top3 = QtWidgets.QLabel("-")
        for lbl in (self.lbl_top1, self.lbl_top2, self.lbl_top3):
            lbl.setWordWrap(True)

        top_box = QtWidgets.QGroupBox("Top 3 features")
        top_layout = QtWidgets.QVBoxLayout(top_box)
        top_layout.addWidget(self.lbl_top1)
        top_layout.addWidget(self.lbl_top2)
        top_layout.addWidget(self.lbl_top3)

        self.figure = Figure(figsize=(5, 3))
        self.canvas = FigureCanvas(self.figure)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["rank", "feature", "mean|SHAP|", "model"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(ctrl_layout)
        layout.addWidget(top_box)
        layout.addWidget(self.canvas, 2)
        layout.addWidget(self.table, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self.spin_top_n.valueChanged.connect(self.refresh)
        self.spin_cache_sec.valueChanged.connect(self.refresh)

        self.refresh()

    def refresh(self, force: bool = False) -> None:
        """
        AISvc.get_shap_top_features() を呼び出して再描画する。
        """
        from loguru import logger

        top_n = int(self.spin_top_n.value())
        cache_sec = 0 if force else int(self.spin_cache_sec.value())

        try:
            df = self.ai_service.get_shap_top_features(
                top_n=top_n,
                cache_sec=cache_sec,
            )
        except Exception as e:
            logger.exception("failed to compute SHAP top features: %s", e)
            self._render_empty(f"SHAP error: {e}")
            return

        if df is None or df.empty:
            self._render_empty("No SHAP data.")
            return

        self._df_cache = df
        self._plot(df)
        self._fill_table(df)
        self._update_top3(df)

    def _plot(self, df: pd.DataFrame) -> None:
        """
        SHAPグローバル重要度の水平棒グラフを描画。
        """
        df_plot = df.copy()
        df_plot = df_plot.sort_values("mean_abs_shap", ascending=True)

        features_raw = df_plot["feature"].astype(str).tolist()
        features = [self._alias.get(f, f) for f in features_raw]
        values = pd.to_numeric(df_plot["mean_abs_shap"], errors="coerce").fillna(0.0).tolist()

        self.figure.clear()
        ax = self.figure.add_subplot(111)

        y_pos = range(len(features))
        ax.barh(y_pos, values)
        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(features)

        ax.set_xlabel("mean |SHAP value|")
        ax.set_title("Global SHAP Feature Importance")

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _fill_table(self, df: pd.DataFrame) -> None:
        """
        テーブルに SHAP 順位を表示。
        """
        df = df.reset_index(drop=True)
        self.table.setRowCount(len(df))

        for row_idx, row in df.iterrows():
            rank = str(row.get("rank", row_idx + 1))
            feat = str(row.get("feature", ""))
            mean_abs = float(row.get("mean_abs_shap", 0.0))
            model = str(row.get("model", ""))

            items = [
                QtWidgets.QTableWidgetItem(rank),
                QtWidgets.QTableWidgetItem(feat),
                QtWidgets.QTableWidgetItem(f"{mean_abs:.6f}"),
                QtWidgets.QTableWidgetItem(model),
            ]

            for col_idx, item in enumerate(items):
                self.table.setItem(row_idx, col_idx, item)

        self.table.resizeColumnsToContents()

    def _update_top3(self, df: Optional[pd.DataFrame]) -> None:
        """
        上位3特徴量の簡易サマリをラベルに表示。
        """
        if df is None or df.empty:
            self.lbl_top1.setText("-")
            self.lbl_top2.setText("-")
            self.lbl_top3.setText("-")
            return

        if "rank" in df.columns:
            df_sorted = df.sort_values("rank", ascending=True)
        else:
            df_sorted = df.sort_values("mean_abs_shap", ascending=False)

        top3 = df_sorted.head(3).reset_index(drop=True)

        labels = [self.lbl_top1, self.lbl_top2, self.lbl_top3]
        for idx in range(3):
            if idx < len(top3):
                row = top3.iloc[idx]
                raw_name = str(row.get("feature", ""))
                alias = self._alias.get(raw_name, raw_name)
                mean_abs = float(row.get("mean_abs_shap", 0.0))
                model = str(row.get("model", ""))
                text = f"{idx + 1}. {alias} (|SHAP|={mean_abs:.4f})"
                if model:
                    text += f"  [{model}]"
                labels[idx].setText(text)
            else:
                labels[idx].setText("-")

    def _render_empty(self, message: str) -> None:
        """
        データが無い or エラー時の簡単な表示。
        """
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.axis("off")
        self.canvas.draw_idle()
        self.table.setRowCount(0)
        self._update_top3(None)

    def _load_alias(self) -> Dict[str, str]:
        """
        configs/feature_alias.json から feature 名のエイリアスを読み出す。
        FeatureImportanceWidget でも使えるように共通処理を流用。
        """
        try:
            root = Path(__file__).resolve().parents[3]
            path = root / "configs" / "feature_alias.json"
            if not path.exists():
                return {}
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            print(f"[ShapBarWidget] _load_alias failed: {e!r}")
        return {}
