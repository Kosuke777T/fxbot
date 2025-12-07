# app/gui/ai_tab.py
from __future__ import annotations
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
)

import joblib
import pandas as pd
from loguru import logger

from app.services.ai_service import AISvc, get_model_metrics
from app.services.recent_kpi import compute_recent_kpi_from_decisions
from app.gui.widgets.feature_importance import FeatureImportanceWidget
from app.gui.widgets.shap_bar import ShapBarWidget
from app.gui.widgets.kpi_dashboard import KPIDashboardWidget
from app.core.strategy_profile import get_profile
from app.services import edition_guard


class AITab(QWidget):
    def __init__(self, ai_service: AISvc | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ai_service = ai_service or AISvc()
        self.profile = get_profile()

        try:
            p = Path("models/LightGBM_clf.pkl")
            if p.exists():
                obj = joblib.load(p)
                model = obj.get("model", obj) if isinstance(obj, dict) else obj
                self.ai_service.models.setdefault("lgbm_cls", model)
        except Exception as e:
            print(f"[AITab] model autoload skipped: {e}")

        main_layout = QVBoxLayout(self)
        
        # タブウィジェットをメインレイアウトに追加（モデル指標はタブ内に移動）
        self.tab_widget = QTabWidget(self)
        main_layout.addWidget(self.tab_widget, 1)

        # --- モデル指標タブ ---
        self.tab_model_info = QWidget(self.tab_widget)
        model_info_layout = QVBoxLayout(self.tab_model_info)
        
        # モデル指標パネル
        self.model_group = QGroupBox("モデル指標", self.tab_model_info)
        model_form = QFormLayout(self.model_group)

        # 基本情報
        self.lbl_model_name = QLabel("-")
        self.lbl_model_version = QLabel("-")

        # 評価指標
        self.lbl_model_logloss = QLabel("-")
        self.lbl_model_auc = QLabel("-")

        # 追加情報（ファイル名・しきい値・最終更新）
        self.lbl_model_file = QLabel("-")
        self.lbl_model_threshold = QLabel("-")
        self.lbl_model_updated = QLabel("-")

        model_form.addRow("モデル名", self.lbl_model_name)
        model_form.addRow("バージョン", self.lbl_model_version)
        model_form.addRow("ファイル", self.lbl_model_file)
        model_form.addRow("しきい値", self.lbl_model_threshold)
        model_form.addRow("Logloss", self.lbl_model_logloss)
        model_form.addRow("AUC", self.lbl_model_auc)
        model_form.addRow("最終更新", self.lbl_model_updated)

        model_info_layout.addWidget(self.model_group)
        model_info_layout.addStretch(1)
        
        self.tab_model_info.setLayout(model_info_layout)
        self.tab_widget.addTab(self.tab_model_info, "モデル指標")

        self.tab_kpi = QWidget(self.tab_widget)
        kpi_layout = QVBoxLayout(self.tab_kpi)

        # 正式KPIダッシュボード（T-10 STEP2）
        profile_name = self.profile.name if hasattr(self.profile, "name") else "std"
        self.kpi_dashboard = KPIDashboardWidget(profile=profile_name, parent=self.tab_kpi)
        kpi_layout.addWidget(self.kpi_dashboard, 1)

        self.recent_kpi_group = QGroupBox("Recent Trades KPI", self.tab_kpi)
        kpi_form = QFormLayout(self.recent_kpi_group)

        self.spin_recent_n = QSpinBox(self.recent_kpi_group)
        self.spin_recent_n.setRange(1, 100000)
        self.spin_recent_n.setValue(100)

        self.lbl_n_trades = QLabel("-", self.recent_kpi_group)
        self.lbl_win_rate = QLabel("-", self.recent_kpi_group)
        self.lbl_profit_factor = QLabel("-", self.recent_kpi_group)
        self.lbl_max_drawdown = QLabel("0.0", self.recent_kpi_group)
        self.lbl_max_dd_ratio = QLabel("N/A", self.recent_kpi_group)
        self.lbl_best_streaks = QLabel("0 / 0", self.recent_kpi_group)

        self.btn_refresh_kpi = QPushButton("Refresh", self.recent_kpi_group)

        kpi_form.addRow("Window (N trades)", self.spin_recent_n)
        kpi_form.addRow("Total trades", self.lbl_n_trades)
        kpi_form.addRow("Win rate", self.lbl_win_rate)
        kpi_form.addRow("Profit factor", self.lbl_profit_factor)
        kpi_form.addRow("Max drawdown", self.lbl_max_drawdown)
        kpi_form.addRow("Max DD ratio", self.lbl_max_dd_ratio)
        kpi_form.addRow("Best win / loss streak", self.lbl_best_streaks)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_refresh_kpi)
        kpi_form.addRow(btn_row)

        kpi_layout.addWidget(self.recent_kpi_group)
        kpi_layout.addStretch(1)

        self.tab_widget.addTab(self.tab_kpi, "KPI")
        # KPIダッシュボードは初期化時に自動的にrefreshされる

        # --- FI / SHAP 表示制御（CapabilitySet 版） ---
        fi_level = edition_guard.get_capability("fi_level") or 0
        shap_level = edition_guard.get_capability("shap_level") or 0

        if fi_level > 0:
            self.tab_fi = QWidget(self.tab_widget)
            fi_layout = QVBoxLayout(self.tab_fi)
            self.feature_importance = FeatureImportanceWidget(self.ai_service, self.tab_fi)
            fi_layout.addWidget(self.feature_importance)
            self.tab_widget.addTab(self.tab_fi, "Feature Importance")

        if shap_level > 0:
            self.tab_shap = QWidget(self.tab_widget)
            shap_layout = QVBoxLayout(self.tab_shap)

            self.shap_group = QGroupBox("SHAP Global Importance", self.tab_shap)
            shap_group_layout = QVBoxLayout(self.shap_group)

            self.shap_widget = ShapBarWidget(self.ai_service, self.shap_group)
            shap_group_layout.addWidget(self.shap_widget)

            shap_layout.addWidget(self.shap_group)
            shap_layout.addStretch(1)

            self.tab_widget.addTab(self.tab_shap, "SHAP")

        self.btn_refresh_kpi.clicked.connect(self.refresh_kpi)
        # Recent Trades KPI
        self.refresh_kpi()

        # モデル指標
        self.refresh_model_metrics()

    def refresh_kpi(self) -> None:
        """
        recent_kpi.compute_recent_kpi_from_decisions を呼び出し、
        ラベルに KPI を表示する。
        """
        limit = self.spin_recent_n.value()

        try:
            result = compute_recent_kpi_from_decisions(
                limit=limit,
                starting_equity=100000.0,
            )
        except Exception as e:
            self.lbl_n_trades.setText("Error")
            self.lbl_win_rate.setText("Error")
            self.lbl_profit_factor.setText("Error")
            self.lbl_max_drawdown.setText("Error")
            self.lbl_max_dd_ratio.setText("Error")
            self.lbl_best_streaks.setText("Error")

            print(f"[AITab] refresh_kpi error: {e!r}")
            return

        self.lbl_n_trades.setText(str(result.n_trades))

        if result.win_rate is not None:
            self.lbl_win_rate.setText(f"{result.win_rate * 100:.1f} %")
        else:
            self.lbl_win_rate.setText("N/A")

        if result.profit_factor is not None:
            self.lbl_profit_factor.setText(f"{result.profit_factor:.2f}")
        else:
            self.lbl_profit_factor.setText("N/A")

        self.lbl_max_drawdown.setText(f"{result.max_drawdown:.1f}")

        if result.max_drawdown_ratio is not None:
            self.lbl_max_dd_ratio.setText(f"{result.max_drawdown_ratio * 100:.2f} %")
        else:
            self.lbl_max_dd_ratio.setText("N/A")

        self.lbl_best_streaks.setText(
            f"{result.best_win_streak} / {result.best_loss_streak}"
        )


    def refresh_model_metrics(self) -> None:
        """サービス層からモデル指標を取得してラベルに反映する。"""
        try:
            m = get_model_metrics()
        except Exception as e:
            print(f"[AITab] get_model_metrics failed: {e!r}")
            # エラー時は全部まとめて Error 表示
            self.lbl_model_name.setText("Error")
            self.lbl_model_version.setText("Error")
            self.lbl_model_file.setText("Error")
            self.lbl_model_threshold.setText("Error")
            self.lbl_model_logloss.setText("Error")
            self.lbl_model_auc.setText("Error")
            self.lbl_model_updated.setText("Error")
            return

        # --- テキスト整形 ----------------------------------------
        model_name = m.get("model_name") or "-"
        version = m.get("version") or "-"

        # file / model_file のどちらかに入っている想定
        file_name = m.get("file") or m.get("model_file") or "-"

        logloss = m.get("logloss")
        auc = m.get("auc")
        best_threshold = m.get("best_threshold")

        # updated_at がなければ created_at_utc を fallback
        updated_at = m.get("updated_at") or m.get("created_at_utc") or "-"

        # --- ラベル反映 -------------------------------------------
        self.lbl_model_name.setText(str(model_name))
        self.lbl_model_version.setText(str(version))
        self.lbl_model_file.setText(str(file_name))

        if isinstance(best_threshold, (int, float)):
            self.lbl_model_threshold.setText(f"{best_threshold:.3f}")
        else:
            self.lbl_model_threshold.setText("-")

        if isinstance(logloss, (int, float)):
            self.lbl_model_logloss.setText(f"{logloss:.4f}")
        else:
            self.lbl_model_logloss.setText("-")

        if isinstance(auc, (int, float)):
            self.lbl_model_auc.setText(f"{auc:.4f}")
        else:
            self.lbl_model_auc.setText("-")

        self.lbl_model_updated.setText(str(updated_at))
