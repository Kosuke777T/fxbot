"""
tests/test_normalize_runtime_cfg.py

_normalize_runtime_cfg() 関数の単体テスト

runtime に _sim_* キーが混入した場合の正規化を検証し、回帰を防止する。
"""
from typing import Any, Dict

import pytest

from app.services.execution_stub import _normalize_runtime_cfg


def test_normalize_sim_keys_only() -> None:
    """
    テストケース1: runtime_cfg に _sim_* のみが存在する場合
    → open_positions/pos_hold_ticks に変換され、_sim_* が消える
    """
    runtime_cfg: Dict[str, Any] = {
        "_sim_open_position": 1,
        "_sim_pos_hold_ticks": 10,
        "other_key": "value",
    }

    result = _normalize_runtime_cfg(runtime_cfg)

    # _sim_* キーが削除されていること
    assert "_sim_open_position" not in result
    assert "_sim_pos_hold_ticks" not in result

    # 標準キーに変換されていること
    assert "open_positions" in result
    assert result["open_positions"] == 1  # bool(1) → int(1)
    assert "pos_hold_ticks" in result
    assert result["pos_hold_ticks"] == 10

    # その他のキーは保持されること
    assert result["other_key"] == "value"

    # 元の辞書は変更されていないこと（副作用チェック）
    assert "_sim_open_position" in runtime_cfg
    assert "_sim_pos_hold_ticks" in runtime_cfg


def test_normalize_standard_keys_priority() -> None:
    """
    テストケース2: runtime_cfg に標準キーあり + _sim_* あり
    → 標準キー優先で _sim_* が消える
    """
    runtime_cfg: Dict[str, Any] = {
        "open_positions": 2,
        "pos_hold_ticks": 20,
        "_sim_open_position": 1,  # 無視される
        "_sim_pos_hold_ticks": 10,  # 無視される
        "other_key": "value",
    }

    result = _normalize_runtime_cfg(runtime_cfg)

    # _sim_* キーが削除されていること
    assert "_sim_open_position" not in result
    assert "_sim_pos_hold_ticks" not in result

    # 標準キーが優先されること
    assert result["open_positions"] == 2  # 標準キーの値が保持される
    assert result["pos_hold_ticks"] == 20  # 標準キーの値が保持される

    # その他のキーは保持されること
    assert result["other_key"] == "value"


def test_normalize_empty_dict() -> None:
    """
    テストケース3: runtime_cfg が空/None 相当でも落ちない
    """
    # 空の辞書
    result = _normalize_runtime_cfg({})
    assert isinstance(result, dict)
    assert len(result) == 0

    # 通常のキーのみ（_sim_* が無い場合）
    runtime_cfg: Dict[str, Any] = {
        "open_positions": 0,
        "max_positions": 1,
        "other_key": "value",
    }
    result = _normalize_runtime_cfg(runtime_cfg)
    assert result == runtime_cfg  # 変更されない
    assert "open_positions" in result
    assert "max_positions" in result
    assert "other_key" in result


def test_normalize_sim_open_position_edge_cases() -> None:
    """
    テストケース4: _sim_open_position のエッジケース
    """
    # False の場合
    result = _normalize_runtime_cfg({"_sim_open_position": False})
    assert result["open_positions"] == 0

    # 0 の場合
    result = _normalize_runtime_cfg({"_sim_open_position": 0})
    assert result["open_positions"] == 0

    # None の場合
    result = _normalize_runtime_cfg({"_sim_open_position": None})
    assert result["open_positions"] == 0

    # 正の数値の場合
    result = _normalize_runtime_cfg({"_sim_open_position": 5})
    assert result["open_positions"] == 1  # bool(5) → True → int(True) → 1


def test_normalize_sim_pos_hold_ticks_edge_cases() -> None:
    """
    テストケース5: _sim_pos_hold_ticks のエッジケース
    """
    # 正の整数
    result = _normalize_runtime_cfg({"_sim_pos_hold_ticks": 10})
    assert result["pos_hold_ticks"] == 10

    # 0
    result = _normalize_runtime_cfg({"_sim_pos_hold_ticks": 0})
    assert result["pos_hold_ticks"] == 0

    # None
    result = _normalize_runtime_cfg({"_sim_pos_hold_ticks": None})
    assert result["pos_hold_ticks"] is None

    # 文字列の数値（変換可能）
    result = _normalize_runtime_cfg({"_sim_pos_hold_ticks": "20"})
    assert result["pos_hold_ticks"] == 20

    # 変換不可な値（例外が発生せず None になる）
    result = _normalize_runtime_cfg({"_sim_pos_hold_ticks": "invalid"})
    assert result["pos_hold_ticks"] is None


def test_normalize_mixed_scenarios() -> None:
    """
    テストケース6: 複数のシナリオを組み合わせたテスト
    """
    # _sim_open_position のみ、標準キーなし
    result = _normalize_runtime_cfg({
        "_sim_open_position": 1,
        "max_positions": 1,
    })
    assert "open_positions" in result
    assert result["open_positions"] == 1
    assert "_sim_open_position" not in result
    assert "max_positions" in result

    # _sim_pos_hold_ticks のみ、標準キーなし
    result = _normalize_runtime_cfg({
        "_sim_pos_hold_ticks": 15,
        "spread_pips": 0.05,
    })
    assert "pos_hold_ticks" in result
    assert result["pos_hold_ticks"] == 15
    assert "_sim_pos_hold_ticks" not in result
    assert "spread_pips" in result

    # 両方の _sim_* キーがあり、標準キーも両方存在
    result = _normalize_runtime_cfg({
        "open_positions": 3,
        "pos_hold_ticks": 30,
        "_sim_open_position": 1,
        "_sim_pos_hold_ticks": 10,
    })
    assert result["open_positions"] == 3  # 標準キー優先
    assert result["pos_hold_ticks"] == 30  # 標準キー優先
    assert "_sim_open_position" not in result
    assert "_sim_pos_hold_ticks" not in result

