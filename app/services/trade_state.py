from dataclasses import dataclass, asdict
from typing import Any, Optional

@dataclass
class TradeSettings:
    trading_enabled: bool = False
    threshold_buy: float = 0.60
    threshold_sell: float = 0.60
    prob_threshold: float = 0.60
    side_bias: str = "auto"
    tp_pips: int = 15
    sl_pips: int = 10

# シングルトン的にプロセス内共有
_settings = TradeSettings()



@dataclass
class TradeRuntime:
    # schema_version: runtime の標準キー定義のバージョン（将来の拡張時に互換性チェック用）
    schema_version: int = 1

    # 標準キー: ポジション管理
    open_positions: int = 0  # 現在のオープンポジション数（0以上）
    max_positions: int = 1  # 最大ポジション数（1以上）

    # 将来の拡張用（最小限）
    # inflight_orders: int = 0  # 注文中数（将来追加予定、今は未使用）

    # 従来のフィールド（後方互換性のため残す）
    last_ticket: Optional[int] = None
    last_side: Optional[str] = None
    last_symbol: Optional[str] = None


_runtime_state = TradeRuntime()


def get_runtime() -> TradeRuntime:
    return _runtime_state


def update_runtime(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        if hasattr(_runtime_state, k):
            setattr(_runtime_state, k, v)

def get_settings() -> TradeSettings:
    return _settings

def update(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        if hasattr(_settings, k):
            setattr(_settings, k, v)

def as_dict() -> dict[str, Any]:
    """TradeSettings を dict に変換（従来のAPI）"""
    return asdict(_settings)


def runtime_as_dict(rt: Optional["TradeRuntime"] = None) -> dict[str, Any]:
    """
    TradeRuntime を dict に変換し、必須キー・型を検証・矯正する（出口での統一処理）。

    戻り値は標準3キー固定（schema_version, open_positions, max_positions）。
    TradeRuntime に他のフィールド（last_ticket, last_side, last_symbol 等）があっても、ログの標準 runtime には含めない。

    Parameters
    ----------
    rt : TradeRuntime | None, optional
        TradeRuntime インスタンス（省略時は現在のシングルトン _runtime_state を使用）

    Returns
    -------
    dict[str, Any]
        検証・矯正済みの runtime dict（schema_version, open_positions, max_positions のみ）
    """
    # rt を省略したら現在のruntimeを取る
    if rt is None:
        rt = _runtime_state

    # 標準3キーのみを明示的に返す（last_* などの追加フィールドは含めない）
    # 検証・矯正: "落とさず矯正"が基本（GUI運用で落ちると嫌なので）

    # schema_version が無い／intでない → 1 を入れる
    try:
        schema_ver = int(getattr(rt, "schema_version", 1) or 1)
        if schema_ver < 1:
            schema_ver = 1
    except (ValueError, TypeError):
        schema_ver = 1

    # open_positions が無い／intでない → 0 に丸める
    try:
        open_pos = int(getattr(rt, "open_positions", 0) or 0)
        if open_pos < 0:
            open_pos = 0
    except (ValueError, TypeError):
        open_pos = 0

    # max_positions が無い／intでない／0以下 → 1 に丸める
    try:
        max_pos = int(getattr(rt, "max_positions", 1) or 1)
        if max_pos < 1:
            max_pos = 1
    except (ValueError, TypeError):
        max_pos = 1

    return {
        "schema_version": schema_ver,
        "open_positions": open_pos,
        "max_positions": max_pos,
    }


# （任意）呼び出し名のブレを吸収する alias（関数は増やさない）
as_runtime_dict = runtime_as_dict
