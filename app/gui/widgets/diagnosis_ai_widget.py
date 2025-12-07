# app/gui/widgets/diagnosis_ai_widget.py
import json
from typing import Any
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QTextEdit

from app.gui.widgets.future_scenario_widget import FutureScenarioWidget


class DiagnosisAIWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)

        # 来週のシナリオウィジェットを上部に追加
        self.future_widget = FutureScenarioWidget(self)
        main_layout.addWidget(self.future_widget)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setStyleSheet("""
/* --- 診断AI内タブ（2段目） --- */
QTabBar::tab {
    background: #F6F6F6;        /* 非選択タブ：薄めのグレー（標準UIと似た色） */
    border: 1px solid #CCCCCC;
    padding: 6px 12px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

/* 選択タブ（薄い水色） */
QTabBar::tab:selected {
    background: #D7EEFF;
    border: 1px solid #A0C8E8;
}

/* ホバー */
QTabBar::tab:hover {
    background: #E9F5FF;
}
""")
        main_layout.addWidget(self.tab_widget, 1)

        # 各タブの QTextEdit をメンバ変数として保持
        self.time_tab_edit = QTextEdit(self)
        self.time_tab_edit.setReadOnly(True)
        self.tab_widget.addTab(self.time_tab_edit, "時間帯 × 相場タイプ")

        self.win_tab_edit = QTextEdit(self)
        self.win_tab_edit.setReadOnly(True)
        self.tab_widget.addTab(self.win_tab_edit, "勝率が高い条件")

        self.dd_tab_edit = QTextEdit(self)
        self.dd_tab_edit.setReadOnly(True)
        self.tab_widget.addTab(self.dd_tab_edit, "DD直前の特徴")

        self.anomaly_tab_edit = QTextEdit(self)
        self.anomaly_tab_edit.setReadOnly(True)
        self.tab_widget.addTab(self.anomaly_tab_edit, "異常点の検出")

    def update_data(self, data: Any) -> None:
        """診断結果を各タブに反映する"""
        if not isinstance(data, dict):
            msg = "データがありません。"
            self.time_tab_edit.setText(msg)
            self.win_tab_edit.setText(msg)
            self.dd_tab_edit.setText(msg)
            self.anomaly_tab_edit.setText(msg)
            self.future_widget.update_data(None)
            return

        # 来週シナリオ
        self.future_widget.update_data(data.get("future_scenario"))

        self._update_time_of_day_tab(data.get("time_of_day_stats"))
        self._update_winning_tab(data.get("winning_conditions"))
        self._update_dd_tab(data.get("dd_pre_signal"))
        self._update_anomaly_tab(data.get("anomalies"))

    def _update_time_of_day_tab(self, stats: Any) -> None:
        if not stats:
            self.time_tab_edit.setText("データがありません。")
            return

        # 代表的な構造を想定しつつ、なければ JSON 表示にフォールバック
        bins = None
        if isinstance(stats, dict):
            bins = stats.get("bins")

        if isinstance(bins, list) and bins:
            lines: list[str] = []
            for b in bins:
                hour = b.get("hour") or b.get("range") or "?"
                trades = b.get("trades") or b.get("n") or 0
                win_rate = b.get("win_rate") or b.get("winrate")
                avg_pl = b.get("avg_pl") or b.get("avg_pips")

                line = f"{hour}: 件数={trades}"
                if isinstance(win_rate, (int, float)):
                    line += f", 勝率={win_rate*100:.1f}%"
                if isinstance(avg_pl, (int, float)):
                    line += f", 平均損益={avg_pl:.1f}"
                lines.append(line)

            self.time_tab_edit.setText("\n".join(lines))
        else:
            # dict の場合は時間帯ごとの統計を整形
            if isinstance(stats, dict):
                lines: list[str] = []
                for hour, info in sorted(stats.items()):
                    if isinstance(info, dict):
                        trades = info.get("trades") or info.get("n") or 0
                        win_rate = info.get("win_rate") or info.get("winrate")
                        pf = info.get("pf")
                        
                        line = f"{hour}時: 件数={trades}"
                        if isinstance(win_rate, (int, float)):
                            line += f", 勝率={win_rate*100:.1f}%"
                        if isinstance(pf, (int, float)):
                            line += f", PF={pf:.2f}"
                        lines.append(line)
                
                if lines:
                    self.time_tab_edit.setText("\n".join(lines))
                    return

            # ここまでで決まらなければ JSON 表示
            try:
                self.time_tab_edit.setText(
                    json.dumps(stats, ensure_ascii=False, indent=2)
                )
            except TypeError:
                self.time_tab_edit.setText(str(stats))

    def _update_winning_tab(self, data: Any) -> None:
        if not data:
            self.win_tab_edit.setText("データがありません。")
            return

        # list[dict] を想定した整形
        if isinstance(data, list):
            lines: list[str] = []
            for row in data:
                cond = row.get("condition") or row.get("label") or ""
                win_rate = row.get("win_rate")
                sample = row.get("trades") or row.get("n")

                line = cond or "(条件不明)"
                if isinstance(win_rate, (int, float)):
                    line += f" / 勝率={win_rate*100:.1f}%"
                if isinstance(sample, int):
                    line += f" / 件数={sample}"
                lines.append(line)

            self.win_tab_edit.setText("\n".join(lines))
            return

        # dict の場合は整形表示
        if isinstance(data, dict):
            lines: list[str] = []
            
            total_trades = data.get("total_trades")
            if total_trades is not None:
                lines.append(f"総トレード数: {total_trades}")
            
            global_win_rate = data.get("global_win_rate")
            if isinstance(global_win_rate, (int, float)):
                lines.append(f"全体勝率: {global_win_rate*100:.1f}%")
            
            global_pf = data.get("global_pf")
            if isinstance(global_pf, (int, float)):
                lines.append(f"全体PF: {global_pf:.2f}")
            
            best_hours = data.get("best_hours")
            if isinstance(best_hours, list) and best_hours:
                lines.append("")
                lines.append("勝率が高い時間帯:")
                for hour_info in best_hours:
                    if isinstance(hour_info, dict):
                        hour = hour_info.get("hour")
                        win_rate = hour_info.get("win_rate")
                        trades = hour_info.get("trades")
                        pf = hour_info.get("pf")
                        
                        line = f"  {hour}時"
                        if isinstance(win_rate, (int, float)):
                            line += f": 勝率={win_rate*100:.1f}%"
                        if isinstance(trades, int):
                            line += f", 件数={trades}"
                        if isinstance(pf, (int, float)):
                            line += f", PF={pf:.2f}"
                        lines.append(line)
            
            if lines:
                self.win_tab_edit.setText("\n".join(lines))
                return

        # ここまでで決まらなければ JSON 表示
        try:
            self.win_tab_edit.setText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        except TypeError:
            self.win_tab_edit.setText(str(data))

    def _update_dd_tab(self, data: Any) -> None:
        if not data:
            self.dd_tab_edit.setText("データがありません。")
            return

        if isinstance(data, dict):
            lines: list[str] = []
            
            loss_streak = data.get("loss_streak")
            if loss_streak is not None:
                lines.append(f"最大連敗数: {loss_streak}")

            avg_atr = data.get("avg_atr")
            if isinstance(avg_atr, (int, float)):
                lines.append(f"平均ATR: {avg_atr:.5f}")

            avg_vol = data.get("avg_volatility")
            if isinstance(avg_vol, (int, float)):
                lines.append(f"平均ボラティリティ: {avg_vol:.5f}")

            common_hours = data.get("common_hours")
            if common_hours:
                lines.append(f"損失が集中した時間帯: {', '.join(map(str, common_hours))}")

            worst_month = data.get("worst_month")
            if worst_month:
                lines.append(f"最悪月: {worst_month}")

            max_dd_pct = data.get("max_dd_pct")
            if isinstance(max_dd_pct, (int, float)):
                lines.append(f"最大DD: {max_dd_pct*100:.2f}%")

            trades_in_period = data.get("trades_in_period")
            if trades_in_period is not None:
                lines.append(f"該当月のトレード数: {trades_in_period}")

            winrate = data.get("winrate")
            if isinstance(winrate, (int, float)):
                lines.append(f"該当月の勝率: {winrate*100:.1f}%")

            notes = data.get("notes")
            if notes:
                lines.append("")
                lines.append(str(notes))

            if lines:
                self.dd_tab_edit.setText("\n".join(lines))
                return

        # ここまでで決まらなければ JSON 表示
        try:
            self.dd_tab_edit.setText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        except TypeError:
            self.dd_tab_edit.setText(str(data))

    def _update_anomaly_tab(self, data: Any) -> None:
        if not data:
            self.anomaly_tab_edit.setText("データがありません。")
            return

        # list[dict] を想定
        if isinstance(data, list):
            lines: list[str] = []
            for i, row in enumerate(data, start=1):
                label = row.get("label") or row.get("type") or "異常"
                ts = row.get("timestamp")
                score = row.get("score")
                line = f"[{i}] {label}"
                if ts:
                    line += f" / {ts}"
                if isinstance(score, (int, float)):
                    line += f" / スコア={score:.3f}"
                lines.append(line)

            self.anomaly_tab_edit.setText("\n".join(lines))
            return

        # ここまでで決まらなければ JSON 表示
        try:
            self.anomaly_tab_edit.setText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        except TypeError:
            self.anomaly_tab_edit.setText(str(data))

