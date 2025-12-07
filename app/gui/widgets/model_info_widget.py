# app/gui/widgets/model_info_widget.py
from __future__ import annotations

from PyQt6.QtWidgets import QGroupBox, QFormLayout, QLabel

from app.services.ai_service import get_model_metrics


def get_model_info():
    """get_model_metrics() のエイリアス（後方互換性のため）"""
    return get_model_metrics()


class ModelInfoWidget(QGroupBox):
    """モデル指標表示ウィジェット"""

    def __init__(self, parent=None):
        super().__init__("モデル指標", parent)

        # コンストラクタで get_model_info() を呼び出して保持
        self._model_info = get_model_info()

        # フォームレイアウト
        form = QFormLayout(self)

        # ラベル作成
        self.label_name_value = QLabel("-")
        self.label_version_value = QLabel("-")
        self.label_file_value = QLabel("-")
        self.label_threshold_value = QLabel("-")
        self.label_logloss_value = QLabel("-")
        self.label_auc_value = QLabel("-")
        self.label_trained_at_value = QLabel("-")

        # フォームに追加
        form.addRow("モデル名", self.label_name_value)
        form.addRow("バージョン", self.label_version_value)
        form.addRow("ファイル", self.label_file_value)
        form.addRow("しきい値", self.label_threshold_value)
        form.addRow("Logloss", self.label_logloss_value)
        form.addRow("AUC", self.label_auc_value)
        form.addRow("最終更新", self.label_trained_at_value)

        # 初期表示を反映
        self.update_view()

    def update_view(self) -> None:
        """ラベル更新用メソッド"""
        info = self._model_info or {}

        self.label_name_value.setText(str(info.get("model_name") or "-"))
        self.label_version_value.setText(str(info.get("version") or "-"))
        self.label_file_value.setText(str(info.get("file") or "-"))

        bt = info.get("best_threshold")
        self.label_threshold_value.setText(
            f"{bt:.3f}" if isinstance(bt, (int, float)) else "-"
        )

        logloss = info.get("logloss")
        self.label_logloss_value.setText(
            f"{logloss:.4f}" if isinstance(logloss, (int, float)) else "-"
        )

        auc = info.get("auc")
        self.label_auc_value.setText(
            f"{auc:.4f}" if isinstance(auc, (int, float)) else "-"
        )

        trained = info.get("trained_at")
        self.label_trained_at_value.setText(str(trained or "-"))

    def reload(self) -> None:
        """モデル情報を再読込して表示を更新"""
        self._model_info = get_model_info()
        self.update_view()

