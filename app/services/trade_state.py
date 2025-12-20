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


def build_runtime(
    symbol: str,
    *,
    market=None,
    ts_str: Optional[str] = None,
    spread_pips: Optional[float] = None,
    mode: Optional[str] = None,
    source: Optional[str] = None,
    timeframe: Optional[str] = None,
    profile: Optional[str] = None,
    price: Optional[float] = None,
) -> dict[str, Any]:
    """
    runtime dict を構築し、validate_runtime を通す（live/demo 統一の出口）。

    Parameters
    ----------
    symbol : str
        シンボル名（spread_pips 取得時に使用）
    market : module, optional
        market モジュール（spread_pips 取得時に使用）。None の場合は import する
    ts_str : str, optional
        タイムスタンプ（JST ISO形式）。None の場合は現在時刻を生成
    spread_pips : float, optional
        スプレッド（pips単位）。None の場合は market から取得、それでも無ければ 0.0
    mode : str, optional
        実行モード（例: "live", "demo", "backtest"）。v2 追加キー
    source : str, optional
        データソース（例: "mt5", "csv", "stub"）。v2 追加キー
    timeframe : str, optional
        タイムフレーム（例: "M5", "H1"）。v2 追加キー
    profile : str, optional
        プロファイル名（例: "michibiki_std"）。v2 追加キー
    price : float, optional
        現在価格。v2 追加キー

    Returns
    -------
    dict[str, Any]
        validate_runtime を通した runtime dict（schema_version=2, ts, spread_pips, open_positions, max_positions, symbol, mode, source 等を含む）

    Raises
    ------
    TypeError, ValueError
        validate_runtime で strict=True の検証に失敗した場合
    """
    from core.utils.timeutil import now_jst_iso
    from app.services.execution_stub import validate_runtime

    # 1. 基本 runtime（schema_version=2, open_positions, max_positions）
    runtime = runtime_as_dict()
    runtime["schema_version"] = 2  # v2 を返す

    # 2. ts を追加（引数優先、なければ現在時刻）
    if ts_str is None:
        ts_str = now_jst_iso()
    runtime["ts"] = ts_str

    # 3. spread_pips を追加（引数優先 → market から取得 → デフォルト値）
    if spread_pips is None:
        if market is None:
            try:
                from app.core import market as market_module
                market = market_module
            except ImportError:
                market = None

        if market is not None:
            try:
                spread_pips_val = market.spread_pips(symbol)
                spread_pips = float(spread_pips_val) if spread_pips_val is not None else 0.0
            except Exception:
                spread_pips = 0.0
        else:
            spread_pips = 0.0

    runtime["spread_pips"] = float(spread_pips)

    # 4. v2 追加キー（最低限: symbol, mode, source）
    runtime["symbol"] = symbol
    runtime["mode"] = mode if mode is not None else None
    runtime["source"] = source if source is not None else None

    # 5. v2 追加キー（取れれば追加: timeframe, profile, price）
    if timeframe is not None:
        runtime["timeframe"] = timeframe
    if profile is not None:
        runtime["profile"] = profile
    if price is not None:
        runtime["price"] = float(price)

    # 6. validate_runtime で検証（strict=True で必須キー・型チェック）
    warnings = validate_runtime(runtime, strict=True)
    # warnings があれば logger.warning で出力（ただし例外は発生させない）
    # 注意: validate_runtime が返す警告メッセージには既に [runtime_schema] プレフィックスが含まれている
    if warnings:
        from loguru import logger
        for warning in warnings:
            logger.warning(warning)  # プレフィックスは既に含まれている

    return runtime
