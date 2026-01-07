from __future__ import annotations

from typing import Any

# order_params schema の固定アンカー（互換性のため必ず残す）
ORDER_PARAMS_SCHEMA_VERSION = 1


def ensure_order_params_schema(
    order_params: dict,
    *,
    pair: str | None = None,
    symbol: str | None = None,
    mode: str | None = None,
) -> dict:
    """
    order_params の schema を “追加のみ” で固定する。

    禁止事項:
    - 既存キーの rename / delete / 意味変更は禁止
    - 既存キーがある場合は絶対に上書きしない（setdefault のみ）

    方針:
    - 返り値は shallow copy（入力 dict を破壊しない）
    - schema_version を必ず付与（将来拡張のアンカー）
    - pair/symbol/mode は補助キーとして追加（既存に無ければ）
      - pair が未指定かつ symbol がある場合は pair=symbol を入れる（追加のみ）
    """
    if not isinstance(order_params, dict):
        return {}

    out = dict(order_params)

    # 固定アンカー
    out.setdefault("schema_version", int(ORDER_PARAMS_SCHEMA_VERSION))

    # 補助キー（既存に無ければ追加のみ）
    if symbol is not None:
        try:
            if isinstance(symbol, str):
                out.setdefault("symbol", symbol)
        except Exception:
            pass

    if pair is not None:
        try:
            if isinstance(pair, str):
                out.setdefault("pair", pair)
        except Exception:
            pass
    else:
        # pair が無く symbol があるなら補助的に入れる（renameではなく追加）
        try:
            sym = out.get("symbol")
            if isinstance(sym, str) and sym:
                out.setdefault("pair", sym)
        except Exception:
            pass

    if mode is not None:
        try:
            if isinstance(mode, str):
                out.setdefault("mode", mode)
        except Exception:
            pass

    return out



