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


# priority定数（services層と整合）
PRIORITY_PROMOTE = 300
PRIORITY_RETRY = 200
PRIORITY_NONE = 0

# priority → UI仕様のマッピング（1箇所に集約）
def _ui_spec_from_priority(priority: int, kind: Optional[str] = None) -> ActionUiSpec:
    """
    priorityからUI仕様を決定する（重複排除・1箇所集約）。

    Args:
        priority: priority値（300=PROMOTE, 200=RETRY, 0=NONE）
        kind: kind値（label文言決定用、分岐には使わない）

    Returns:
        ActionUiSpec: UI表示仕様
    """
    if priority >= PRIORITY_PROMOTE:
        # PROMOTE系（priority >= 300）：最強の強調（緑・太字）
        label = "実行（推奨）" if kind and "PROMOTE" in kind.upper() else "実行（推奨）"
        return ActionUiSpec(
            visible=True,
            priority=PRIORITY_PROMOTE,
            label=label,
            style="background-color: #4CAF50; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold;",
            tooltip_prefix="実行（推奨）: ",
        )
    elif priority >= PRIORITY_RETRY:
        # RETRY系（priority >= 200）：中強調（PROMOTEより弱いが、NONEより目立つ）
        label = "再実行" if kind and "RETRY" in kind.upper() else "再実行"
        return ActionUiSpec(
            visible=True,
            priority=PRIORITY_RETRY,
            label=label,
            style="background-color: #FF9800; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold;",
            tooltip_prefix="再実行: ",
        )
    else:
        # NONE系（priority < 200）：非表示
        return ActionUiSpec(
            visible=False,
            priority=PRIORITY_NONE,
            label="",
            style="",
            tooltip_prefix="",
        )


# next_action.kind ごとのUI仕様（後方互換・label文言決定用、分岐には使わない）
ACTION_UI_SPECS: dict[str, ActionUiSpec] = {
    "PROMOTE": _ui_spec_from_priority(PRIORITY_PROMOTE, "PROMOTE"),
    "PROMOTE_DRY_TO_RUN": _ui_spec_from_priority(PRIORITY_PROMOTE, "PROMOTE_DRY_TO_RUN"),
    "RETRY": _ui_spec_from_priority(PRIORITY_RETRY, "RETRY"),
    "NONE": _ui_spec_from_priority(PRIORITY_NONE, "NONE"),
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
    next_actionからUI仕様を取得する（priority優先）。

    Args:
        next_action: next_action dict（{"kind":"...", "priority":int, "reason":"...", "params":{}}）

    Returns:
        ActionUiSpec: UI表示仕様（priority優先、kindはlabel文言決定用のみ）
    """
    if not next_action:
        return _DEFAULT_UI_SPEC

    # priorityを優先（services層で必ず付与される）
    priority = next_action.get("priority")
    if priority is not None:
        kind = next_action.get("kind")
        return _ui_spec_from_priority(priority, kind)

    # フォールバック：kindから推定（後方互換、通常は使われない）
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
    next_actionからpriorityを取得する（ソート用、priority優先）。

    Args:
        next_action: next_action dict（{"priority":int, "kind":"..."}）

    Returns:
        priority値（0=非表示、大きいほど優先）
    """
    if not next_action:
        return 0

    # priorityを直接読む（services層で必ず付与される）
    priority = next_action.get("priority")
    if priority is not None:
        return priority

    # フォールバック：UI仕様から取得（後方互換、通常は使われない）
    spec = ui_for_next_action(next_action)
    return spec.priority if spec.visible else 0

