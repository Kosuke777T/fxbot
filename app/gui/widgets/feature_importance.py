# app/gui/widgets/feature_importance.py
from __future__ import annotations
from typing import Any, Dict, Optional, cast
import json
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QHeaderView
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

class FeatureImportanceWidget(QtWidgets.QWidget):
    def __init__(self, ai_service, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.ai_service = ai_service
        self._df_cache: Optional[pd.DataFrame] = None
        self._alias: Dict[str, str] = self._load_alias()

        self.modelCombo = QtWidgets.QComboBox()
        self.methodCombo = QtWidgets.QComboBox()
        self.methodCombo.addItems(["gain", "split"])
        self.topSpin = QtWidgets.QSpinBox()
        self.topSpin.setRange(3, 100)
        self.topSpin.setValue(20)
        self.refreshBtn = QtWidgets.QPushButton("更新")

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("Model"))
        ctrl.addWidget(self.modelCombo, 1)
        ctrl.addWidget(QtWidgets.QLabel("Method"))
        ctrl.addWidget(self.methodCombo)
        ctrl.addWidget(QtWidgets.QLabel("TopN"))
        ctrl.addWidget(self.topSpin)
        ctrl.addWidget(self.refreshBtn)

        self.fig = Figure(figsize=(6, 4))
        self.canvas = FigureCanvas(self.fig)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["feature", "importance(%)", "model"])
        header: Optional[QHeaderView] = self.table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        # データなしラベル
        self.fi_empty_label = QtWidgets.QLabel("データがありません")
        self.fi_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fi_empty_label.setStyleSheet("color: gray; font-size: 12pt; padding: 20px;")
        self.fi_empty_label.hide()

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(ctrl)
        lay.addWidget(self.canvas, 2)
        lay.addWidget(self.fi_empty_label)
        lay.addWidget(self.table, 1)

        self.refreshBtn.clicked.connect(self.refresh)
        self.methodCombo.currentTextChanged.connect(self.refresh)
        self.topSpin.valueChanged.connect(self.refresh)
        self.modelCombo.currentTextChanged.connect(self._plot_current)

        self.refresh()

    def refresh(self):
        method = self.methodCombo.currentText()
        top_n = int(self.topSpin.value())
        try:
            df = self.ai_service.get_feature_importance(method=method, top_n=top_n)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "FI取得エラー", str(e))
            return

        if df is None or df.empty:
            self._df_cache = None
            self.modelCombo.clear()
            self._render_empty()
            return

        df = df.copy()
        if "feature" in df.columns and self._alias:
            df["feature"] = df["feature"].map(lambda x: self._alias.get(str(x), str(x)))

        self._df_cache = df
        models = sorted(df["model"].unique().tolist())
        prev = self.modelCombo.currentText()
        self.modelCombo.blockSignals(True)
        self.modelCombo.clear()
        self.modelCombo.addItems(models)
        self.modelCombo.blockSignals(False)
        if prev in models:
            self.modelCombo.setCurrentIndex(models.index(prev))
        self._plot_current()
        self._fill_table(df)

    def _plot_current(self):
        df = self._df_cache
        if df is None or df.empty:
            self._render_empty()
            return
        model = self.modelCombo.currentText()
        sub = df[df["model"] == model].sort_values("importance", ascending=True)
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.barh(sub["feature"], sub["importance"])
        ax.set_xlabel("importance (%)")
        ax.set_title(f"Feature Importance - {model} ({self.methodCombo.currentText()})")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _fill_table(self, df: pd.DataFrame):
        # データなし（fi_level=0 or 失敗時）
        if df is None or df.empty:
            self.table.hide()
            self.fi_empty_label.show()
            return
        else:
            self.fi_empty_label.hide()
            self.table.show()

        rows = list(df.reset_index(drop=True).itertuples(index=False))
        self.table.setRowCount(len(rows))
        
        # importance の最大値を取得（色付け用）
        max_importance = df["importance"].max() if len(df) > 0 else 1.0
        
        for r, row in enumerate(rows):
            importance_val = float(cast(Any, row.importance))
            
            # テーブルアイテムを作成
            item_feature = QtWidgets.QTableWidgetItem(str(row.feature))
            item_importance = QtWidgets.QTableWidgetItem(f"{importance_val:.2f}")
            item_model = QtWidgets.QTableWidgetItem(str(row.model))
            
            # importance に応じて背景色を変える
            if max_importance > 0:
                ratio = importance_val / max_importance
                color_value = int(255 - ratio * 155)
                item_importance.setBackground(QColor(255, color_value, color_value))
            
            self.table.setItem(r, 0, item_feature)
            self.table.setItem(r, 1, item_importance)
            self.table.setItem(r, 2, item_model)
        
        # カラム幅調整
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 100)
        header = self.table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)

    def _render_empty(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
        self.canvas.draw_idle()
        self.table.setRowCount(0)

    def _load_alias(self) -> Dict[str, str]:
        try:
            root = Path(__file__).resolve().parents[3]
            path = root / "config" / "feature_alias.json"
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}
