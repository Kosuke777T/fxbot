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
    QApplication,
    QPlainTextEdit,
)
from PyQt6.QtCore import Qt

import joblib
import pandas as pd
from loguru import logger

from app.services.ai_service import AISvc
from app.services.recent_kpi import compute_recent_kpi_from_decisions
from app.gui.widgets.feature_importance import FeatureImportanceWidget
from app.gui.widgets.shap_bar import ShapBarWidget
from app.gui.widgets.kpi_dashboard import KPIDashboardWidget
from app.gui.widgets.diagnosis_ai_widget import DiagnosisAIWidget
from app.gui.widgets.model_info_widget import ModelInfoWidget
from app.core.strategy_profile import get_profile
from app.services import edition_guard
from app.services.diagnosis_service import get_diagnosis_service
from app.services.aisvc_loader import get_last_model_health


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

        # === 2段目タブ（AI内部タブ）の色 + 角丸スタイル ===
        self.tab_widget.setStyleSheet("""
QTabBar::tab {
    background: #F0F0F0;
    padding: 6px 12px;
    border: 1px solid #CCCCCC;
    border-top-left-radius: 4px;      /* ← 角丸 */
    border-top-right-radius: 4px;     /* ← 角丸 */
}

QTabBar::tab:selected {
    background: #D7EEFF;
    border: 1px solid #A0C8E8;
}

QTabBar::tab:hover {
    background: #E5F4FF;
}
""")

        # --- モデル指標タブ ---
        self.tab_model_info = QWidget(self.tab_widget)
        model_info_layout = QVBoxLayout(self.tab_model_info)

        # モデル指標ウィジェット
        self.model_info_widget = ModelInfoWidget(self.tab_model_info)
        model_info_layout.addWidget(self.model_info_widget)

        # --- モデル健全性（詳細）折りたたみグループ ---
        self.health_detail_group = QGroupBox("モデル健全性（詳細）", self.tab_model_info)
        self.health_detail_group.setCheckable(True)
        self.health_detail_group.setChecked(False)  # デフォルトは折りたたみ
        health_detail_layout = QVBoxLayout(self.health_detail_group)

        # 詳細情報表示用テキストエリア（read-only）
        self.health_detail_text = QPlainTextEdit(self.health_detail_group)
        self.health_detail_text.setReadOnly(True)
        self.health_detail_text.setMaximumHeight(200)
        health_detail_layout.addWidget(self.health_detail_text)

        # コピーボタン
        btn_copy_layout = QHBoxLayout()
        btn_copy_layout.addStretch()
        self.btn_copy_health = QPushButton("コピー", self.health_detail_group)
        self.btn_copy_health.clicked.connect(self._on_copy_health_clicked)
        btn_copy_layout.addWidget(self.btn_copy_health)
        health_detail_layout.addLayout(btn_copy_layout)

        model_info_layout.addWidget(self.health_detail_group)
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

        self.tab_widget.addTab(self.tab_kpi, "AI・KPI")
        # KPIダッシュボードは初期化時に自動的にrefreshされる

        # EditionGuard を利用して表示レベルを取得
        from app.services.edition_guard import get_capability

        self.fi_level = get_capability("fi_level") or 0
        self.shap_level = get_capability("shap_level") or 0

        # --- FI / SHAP 表示制御（CapabilitySet 版） ---
        fi_level = self.fi_level
        shap_level = self.shap_level

        self.tab_fi = QWidget(self.tab_widget)
        fi_layout = QVBoxLayout(self.tab_fi)
        self.feature_importance = FeatureImportanceWidget(self.ai_service, self.tab_fi)
        fi_layout.addWidget(self.feature_importance)
        self.tab_widget.addTab(self.tab_fi, "Feature Importance")

        self.tab_shap = QWidget(self.tab_widget)
        shap_layout = QVBoxLayout(self.tab_shap)

        self.shap_group = QGroupBox("SHAP Global Importance", self.tab_shap)
        shap_group_layout = QVBoxLayout(self.shap_group)

        self.shap_widget = ShapBarWidget(self.ai_service, self.shap_group)
        shap_group_layout.addWidget(self.shap_widget)

        shap_layout.addWidget(self.shap_group)
        shap_layout.addStretch(1)

        self.tab_widget.addTab(self.tab_shap, "SHAP")

        # 診断AIタブ（Pro以上）
        from app.services.edition_guard import get_capability
        if (get_capability("shap_level") or 0) >= 1:
            self.diagnosis_tab = DiagnosisAIWidget(self.tab_widget)
            self.tab_widget.addTab(self.diagnosis_tab, "診断AI")

            # v0: 暫定的に固定プロファイル "std" を使用して診断実行
            diag_svc = get_diagnosis_service()
            diag_result = diag_svc.analyze(profile="std")
            self.diagnosis_tab.update_data(diag_result)

        self.btn_refresh_kpi.clicked.connect(self.refresh_kpi)
        # Recent Trades KPI
        self.refresh_kpi()

        # モデル指標は ModelInfoWidget 内で自動的に初期化される

        # モデル健全性（詳細）の表示を更新
        self._update_health_detail()

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
        """モデル指標ウィジェットを再読込する。"""
        if hasattr(self, "model_info_widget"):
            self.model_info_widget.reload()

    def _update_health_detail(self) -> None:
        """
        モデル健全性（詳細）の表示を更新する。
        get_last_model_health() から結果を取得して整形表示。
        """
        try:
            health_result = get_last_model_health()
            if health_result is None:
                self.health_detail_text.setPlainText("(健全性チェック未実行)")
                return

            # meta情報を整形
            meta = health_result.get("meta", {})
            stable = health_result.get("stable", False)
            score = health_result.get("score", 0.0)
            reasons = health_result.get("reasons", [])

            # 表示用テキストを構築
            lines = []
            lines.append(f"stable: {stable}")
            lines.append(f"score: {score:.1f}")
            if reasons:
                lines.append(f"reasons: {', '.join(str(r) for r in reasons)}")
            else:
                lines.append("reasons: (none)")

            lines.append("")  # 空行
            lines.append("--- meta ---")

            # model_path
            model_path = meta.get("model_path", None)
            if model_path:
                lines.append(f"model_path: {model_path}")
            else:
                lines.append("model_path: (n/a)")

            # trained_at
            trained_at = meta.get("trained_at", None)
            if trained_at:
                lines.append(f"trained_at: {trained_at}")
            else:
                # active_model.json から直接取得を試みる
                try:
                    from app.services.ai_service import load_active_model_meta
                    active_meta = load_active_model_meta()
                    if active_meta:
                        trained_at_alt = active_meta.get("trained_at") or active_meta.get("version")
                        if trained_at_alt:
                            lines.append(f"trained_at: {trained_at_alt}")
                        else:
                            lines.append("trained_at: (n/a)")
                    else:
                        lines.append("trained_at: (n/a)")
                except Exception:
                    lines.append("trained_at: (n/a)")

            # scaler_path
            scaler_path = meta.get("scaler_path", None)
            if scaler_path:
                lines.append(f"scaler_path: {scaler_path}")
            else:
                lines.append("scaler_path: (n/a)")

            # expected_features_count
            expected_features_count = meta.get("expected_features_count", None)
            if expected_features_count is not None:
                lines.append(f"expected_features_count: {expected_features_count}")

            # best_threshold（active_model.json から取得を試みる）
            try:
                from app.services.ai_service import load_active_model_meta
                active_meta = load_active_model_meta()
                if active_meta:
                    best_threshold = active_meta.get("best_threshold")
                    if best_threshold is not None:
                        lines.append(f"best_threshold: {best_threshold}")
            except Exception:
                pass

            # その他のmeta情報
            other_keys = set(meta.keys()) - {
                "model_path",
                "trained_at",
                "scaler_path",
                "expected_features_count",
                "active_model_path",
            }
            if other_keys:
                lines.append("")  # 空行
                lines.append("--- その他 ---")
                for key in sorted(other_keys):
                    value = meta.get(key)
                    lines.append(f"{key}: {value}")

            text = "\n".join(lines)
            self.health_detail_text.setPlainText(text)

        except Exception as e:
            # 例外は握る（表示失敗でもアプリは継続）
            self.health_detail_text.setPlainText(f"(表示エラー: {type(e).__name__})")

    def _on_copy_health_clicked(self) -> None:
        """コピーボタン押下時：健全性詳細をクリップボードにコピー。"""
        try:
            text = self.health_detail_text.toPlainText()
            if text:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                # 簡易フィードバック（ボタンテキストを一時変更）
                original_text = self.btn_copy_health.text()
                self.btn_copy_health.setText("コピーしました")
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(2000, lambda: self.btn_copy_health.setText(original_text))
        except Exception:
            # コピー失敗でもアプリは継続
            pass
