"""
Ops履歴のnext_actionに基づくUI表示ルール定数

GUI側でnext_action.priorityをもとにボタン表示・色・優先度を決定するための定数表。
reasonは説明表示にのみ使用し、UIの分岐には使わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionUiSpec:
    """next_actionのUI表示仕様"""
    visible: bool  # ボタン/ラベルを表示するか
    priority: int  # 表示優先度（大きいほど優先、0=非表示）
    label: str  # 表示ラベル（ボタンテキスト等）
    style: str  # CSSスタイル文字列
    tooltip_prefix: str  # ツールチップのプレフィックス（reasonと結合して使用）


# next_action.kind ごとのUI仕様
ACTION_UI_SPECS: dict[str, ActionUiSpec] = {
    "PROMOTE": ActionUiSpec(
        visible=True,
        priority=300,
        label="実行（推奨）",
        style="background-color: #4CAF50; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold;",
        tooltip_prefix="実行（推奨）: ",
    ),
    "PROMOTE_DRY_TO_RUN": ActionUiSpec(
        visible=True,
        priority=300,
        label="実行（推奨）",
        style="background-color: #4CAF50; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold;",
        tooltip_prefix="実行（推奨）: ",
    ),
    "RETRY": ActionUiSpec(
        visible=True,
        priority=200,
        label="再実行",
        style="background-color: #FF9800; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold;",
        tooltip_prefix="再実行: ",
    ),
    "NONE": ActionUiSpec(
        visible=False,
        priority=0,
        label="",
        style="",
        tooltip_prefix="",
    ),
}

# 安全なデフォルト（未知のkind用）
_DEFAULT_UI_SPEC = ActionUiSpec(
    visible=False,
    priority=0,
    label="",
    style="",
    tooltip_prefix="",
)


def ui_for_next_action(next_action: Optional[dict]) -> ActionUiSpec:
    """
    next_actionからUI仕様を取得する。

    Args:
        next_action: next_action dict（{"kind":"...", "reason":"...", "params":{}}）

    Returns:
        ActionUiSpec: UI表示仕様（未知のkindの場合は安全なデフォルト）
    """
    if not next_action:
        return _DEFAULT_UI_SPEC

    kind = (next_action.get("kind") or "").upper()
    return ACTION_UI_SPECS.get(kind, _DEFAULT_UI_SPEC)


def format_action_hint_text(next_action: Optional[dict]) -> str:
    """
    next_actionから行動ヒントの表示テキストを生成する。

    Args:
        next_action: next_action dict

    Returns:
        表示テキスト（空文字列の場合は非表示）
    """
    if not next_action:
        return ""

    spec = ui_for_next_action(next_action)
    if not spec.visible:
        return ""

    reason = next_action.get("reason", "")
    if reason:
        return f"行動ヒント：{spec.label}（{reason}）"
    else:
        return f"行動ヒント：{spec.label}"


def get_action_priority(next_action: Optional[dict]) -> int:
    """
    next_actionからpriorityを取得する（ソート用）。

    Args:
        next_action: next_action dict

    Returns:
        priority値（0=非表示、大きいほど優先）
    """
    if not next_action:
        return 0

    spec = ui_for_next_action(next_action)
    return spec.priority if spec.visible else 0

