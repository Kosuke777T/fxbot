"""
Ops履歴のnext_actionに基づくUI表示ルール定数

GUI側でnext_action.priorityをもとにボタン表示・色・優先度を決定するための定数表。
reasonは説明表示にのみ使用し、UIの分岐には使わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


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


# ==============================
# T-43-5: Condition Mining evidence (display-only)
# ==============================
def extract_condition_mining_evidence(next_action: Optional[dict]) -> dict:
    """
    Display-only helper:
    Extract condition mining evidence attached at:
      next_action.params.evidence.condition_mining

    This must NEVER affect next_action decision logic; GUI uses it only for rendering.
    """
    try:
        if not isinstance(next_action, dict):
            return {}
        params = next_action.get("params") or {}
        if not isinstance(params, dict):
            return {}
        ev = params.get("evidence") or {}
        if not isinstance(ev, dict):
            return {}
        cm = ev.get("condition_mining") or {}
        return cm if isinstance(cm, dict) else {}
    except Exception:
        return {}


def _fmt_support(sup: Any) -> str:
    if isinstance(sup, dict):
        r = sup.get("recent")
        p = sup.get("past")
        if isinstance(r, int) and isinstance(p, int):
            return f"support r={r} p={p}"
        return "support (dict)"
    if isinstance(sup, int):
        return f"support={sup}"
    return "support (n/a)"


def format_condition_mining_evidence_text(next_action: Optional[dict], top_n: int = 3) -> dict:
    """
    Display-only formatter for Ops View.

    Returns:
      {
        "has": bool,
        "summary": str,
        "top_lines": list[str],
        "warnings": list[str],
      }
    """
    cm = extract_condition_mining_evidence(next_action)
    if not cm:
        return {"has": False, "summary": "CM: (no evidence)", "top_lines": [], "warnings": []}

    adoption = cm.get("adoption") if isinstance(cm.get("adoption"), dict) else {}
    status = adoption.get("status") if isinstance(adoption, dict) else None
    notes = adoption.get("notes") if isinstance(adoption, dict) else None
    warnings: list[str] = [str(x) for x in notes] if isinstance(notes, list) else []

    top_candidates = cm.get("top_candidates")
    if not isinstance(top_candidates, list):
        top_candidates = []

    # pick representative candidate for 3 flags: prefer adopted.id, else top[0]
    rep = None
    try:
        if isinstance(adoption, dict) and isinstance(adoption.get("adopted"), dict):
            aid = adoption["adopted"].get("id")
            if isinstance(aid, str) and aid:
                for c in top_candidates:
                    if isinstance(c, dict) and c.get("id") == aid:
                        rep = c
                        break
    except Exception:
        rep = None
    if rep is None and top_candidates and isinstance(top_candidates[0], dict):
        rep = top_candidates[0]

    degr = rep.get("degradation") if isinstance(rep, dict) else None
    conf = rep.get("condition_confidence") if isinstance(rep, dict) else None
    sup = rep.get("support") if isinstance(rep, dict) else None
    sup_s = _fmt_support(sup)

    summary = f"CM: adoption={status or 'none'} | degr={degr} | conf={conf} | {sup_s}"

    lines: list[str] = []
    n = int(top_n) if isinstance(top_n, int) else 3
    if n < 0:
        n = 0
    for i, c in enumerate(top_candidates[:n], start=1):
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        desc = c.get("description")
        cconf = c.get("condition_confidence")
        cdegr = c.get("degradation")
        csup = _fmt_support(c.get("support"))
        lines.append(f"{i}) {cid} | {desc} | conf={cconf} degr={cdegr} | {csup}")

    return {"has": True, "summary": summary, "top_lines": lines, "warnings": warnings}


def build_condition_mining_evidence_strings(
    next_action: Optional[dict],
    *,
    top_n: int = 3,
    max_top_lines: int = 5,
    max_warn_lines: int = 5,
) -> dict:
    """
    UI helper (display-only):
    Wrap format_condition_mining_evidence_text() and return final strings used by UI.

    Returns:
      {
        "has": bool,
        "summary": str,
        "top_lines": list[str],
        "warnings": list[str],
        "body": str,       # summary + optional top_lines
        "warn_body": str,  # "CM warnings:\n- ..."
      }
    """
    base = format_condition_mining_evidence_text(next_action, top_n=top_n) or {}
    try:
        has = bool(base.get("has"))
    except Exception:
        has = False

    summary = str(base.get("summary") or "CM: (n/a)")

    top_lines = base.get("top_lines") or []
    if not isinstance(top_lines, list):
        top_lines = []
    top_lines_s: list[str] = [str(x) for x in top_lines]

    warnings = base.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    warnings_s: list[str] = [str(x) for x in warnings]

    try:
        m = int(max_top_lines)
    except Exception:
        m = 5
    if m < 0:
        m = 0

    body = summary
    if top_lines_s and m > 0:
        body = body + "\n" + "\n".join(top_lines_s[:m])

    try:
        mw = int(max_warn_lines)
    except Exception:
        mw = 5
    if mw < 0:
        mw = 0

    warn_body = ""
    if warnings_s and mw > 0:
        warn_body = "CM warnings:\n- " + "\n- ".join(warnings_s[:mw])

    return {
        "has": has,
        "summary": summary,
        "top_lines": top_lines_s,
        "warnings": warnings_s,
        "body": body,
        "warn_body": warn_body,
    }

