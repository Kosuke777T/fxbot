"""
プロファイル設定の保存・読み込みサービス

config/profiles.json に複数プロファイル設定を保存し、
cron/GUI/手動実行で同一の設定ソースを参照できるようにする。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

from loguru import logger


def _project_root() -> Path:
    """
    services 層から見たプロジェクトルートを推定する。

    app/services/profiles_store.py → app/ → プロジェクトルート
    """
    return Path(__file__).resolve().parents[2]


def _get_config_path() -> Path:
    """config/profiles.json のパスを返す。"""
    return _project_root() / "config" / "profiles.json"


def load_profiles(symbol: str = "USDJPY-") -> List[str]:
    """
    保存済みプロファイル設定を読み込む。

    Args:
        symbol: シンボル（現時点では "USDJPY-" 固定）

    Returns:
        プロファイル名のリスト。ファイルが無い場合は ["michibiki_std"] を返す。
    """
    cfg_path = _get_config_path()

    if not cfg_path.exists():
        logger.debug("profiles.json not found, using default: ['michibiki_std']")
        return ["michibiki_std"]

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        profiles = data.get("profiles", [])

        # 正規化: 重複削除、空要素除去
        profiles = [p.strip() for p in profiles if p and p.strip()]
        profiles = list(dict.fromkeys(profiles))  # 順序を保ったまま重複削除

        if not profiles:
            logger.debug("profiles.json has empty profiles, using default: ['michibiki_std']")
            return ["michibiki_std"]

        logger.debug("loaded profiles from %s: %s", cfg_path, profiles)
        return profiles
    except Exception as e:
        logger.exception("failed to load profiles from %s: %s", cfg_path, e)
        return ["michibiki_std"]


def save_profiles(profiles: List[str], symbol: str = "USDJPY-") -> None:
    """
    プロファイル設定を保存する。

    Args:
        profiles: プロファイル名のリスト
        symbol: シンボル（現時点では "USDJPY-" 固定）

    Raises:
        Exception: ファイル書き込みに失敗した場合
    """
    cfg_path = _get_config_path()

    # 正規化: 重複削除、空要素除去
    normalized = [p.strip() for p in profiles if p and p.strip()]
    normalized = list(dict.fromkeys(normalized))  # 順序を保ったまま重複削除

    if not normalized:
        normalized = ["michibiki_std"]

    # ディレクトリが無ければ作成
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    # atomic write: 一時ファイルに書き込んでから置換
    data = {
        "symbol": symbol,
        "profiles": normalized,
        "updated_at": datetime.now().isoformat(),
    }
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    # --- create temp file safely on Windows ---
    fd, tmp_name = tempfile.mkstemp(
        dir=str(cfg_path.parent),
        prefix="profiles_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)

    try:
        # fd を使って確実に書き込み→クローズ
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)

        # ここで初めて replace（fdはwithで閉じられている）
        os.replace(str(tmp_path), str(cfg_path))

        logger.info("saved profiles to %s: %s", cfg_path, normalized)
    except Exception as e:
        # 失敗時は一時ファイルを削除
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise Exception(f"failed to save profiles to {cfg_path}: {e}") from e
    finally:
        # replace 成功時は tmp_path はもう無いが、念のため
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
