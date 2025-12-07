# app/services/edition_guard.py
# v5.1 CapabilitySet 準拠 EditionGuard

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilitySet:
    """
    ミチビキ v5 / v5.1 の CapabilitySet 定義。

    fi_level:
      0 = 表示なし
      1 = Top3
      2 = Top20
      3 = 全件

    shap_level: fi_level と同様の段階

    scheduler_level:
      0 = 表示のみ
      1 = 週1再学習（固定）
      2 = 1ジョブのみ（再学習 or 診断）
      3 = 複数ジョブ・連鎖

    filter_level:
      0 = フィルタなし
      1 = Basic（時間帯のみ）
      2 = Pro（時間帯＋ATRオンオフ）
      3 = Expert（ATR/ボラ/トレンド/連敗回避/自動プロファイル切替）

    ranking_level:
      0 = 閲覧のみ
      1 = Basic ランキング送信
      2 = Pro ランキング送信（リターンのみ）
      3 = Expert 複合スコア＋自動送信
    """

    fi_level: int = 0
    shap_level: int = 0
    scheduler_level: int = 0
    filter_level: int = 0
    ranking_level: int = 0
    edition: str = "free"


# 仕様書 v5 の表に基づくエディション別 CapabilitySet
_CAPABILITY_TABLE: Dict[str, CapabilitySet] = {
    "free": CapabilitySet(
        fi_level=0,
        shap_level=0,
        scheduler_level=0,
        filter_level=0,
        ranking_level=0,
        edition="free",
    ),
    "basic": CapabilitySet(
        fi_level=0,
        shap_level=0,
        scheduler_level=1,
        filter_level=1,
        ranking_level=1,
        edition="basic",
    ),
    "pro": CapabilitySet(
        fi_level=2,   # FI20
        shap_level=1, # SHAP3
        scheduler_level=2,
        filter_level=2,
        ranking_level=2,
        edition="pro",
    ),
    "expert": CapabilitySet(
        fi_level=3,
        shap_level=3,
        scheduler_level=3,
        filter_level=3,
        ranking_level=3,
        edition="expert",
    ),
    "master": CapabilitySet(
        fi_level=3,
        shap_level=3,
        scheduler_level=3,
        filter_level=3,
        ranking_level=3,
        edition="master",
    ),
}


def _project_root() -> Path:
    """
    services 層から見たプロジェクトルートを推定する。

    app/services/edition_guard.py → app/ → プロジェクトルート
    """
    return Path(__file__).resolve().parents[2]


def _load_edition_from_config() -> Optional[str]:
    """
    config/edition.json から edition を読み込む。

    形式:
      {
        "edition": "basic"
      }
    """
    cfg_path = _project_root() / "config" / "edition.json"
    if not cfg_path.exists():
        return None

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        edition = str(data.get("edition", "")).strip().lower()
        return edition or None
    except Exception:
        logger.exception("failed to load edition from %s", cfg_path)
        return None


def _detect_edition() -> str:
    """
    実際に使用する edition 名を決定する。

    優先順位:
      1. 環境変数 FXBOT_EDITION
      2. 環境変数 EDITION
      3. config/edition.json
      4. デフォルト: "free"
    """
    env_edition = (
        os.getenv("FXBOT_EDITION")
        or os.getenv("EDITION")
        or ""
    ).strip().lower()

    if env_edition:
        edition = env_edition
    else:
        cfg_edition = _load_edition_from_config()
        edition = cfg_edition or "free"

    if edition not in _CAPABILITY_TABLE:
        logger.warning(
            "unknown edition '%s' detected. fallback to 'free'", edition
        )
        edition = "free"

    return edition


class EditionGuard:
    """
    v5.1 EditionGuard 本体。

    - CapabilitySet の取得
    - Edition に応じた各種フラグの判定
    """

    def __init__(self, edition: Optional[str] = None) -> None:
        if edition is None:
            edition = _detect_edition()

        edition = edition.lower()
        if edition not in _CAPABILITY_TABLE:
            logger.warning("unknown edition '%s'. fallback to 'free'", edition)
            edition = "free"

        self._edition = edition
        self._caps = _CAPABILITY_TABLE[edition]

        logger.debug("EditionGuard initialized with edition=%s", edition)

    @property
    def edition(self) -> str:
        return self._edition

    @property
    def capabilities(self) -> CapabilitySet:
        return self._caps

    # ---- Capability API（仕様書 11.1） ----

    def get_capability(self, name: str) -> Any:
        """
        任意の Capability 値を取得する。
        不正な name の場合は None を返す。
        """
        if not hasattr(self._caps, name):
            logger.warning("unknown capability name: %s", name)
            return None
        return getattr(self._caps, name)

    def allow_real_account(self) -> bool:
        """
        実口座トレードを許可するか。

        仕様上明記はないが、常識的な安全策として:
          - free/basic: デモ専用
          - pro/expert/master: 実口座 OK
        """
        return self._edition in ("pro", "expert", "master")

    def scheduler_limit(self) -> int:
        """
        スケジューラのジョブ数上限を返す。

        大雑把に:
          scheduler_level 0 → 0
          scheduler_level 1 → 1（固定週1）
          scheduler_level 2 → 1（任意ジョブ）
          scheduler_level 3 → 10（複数ジョブ・連鎖想定）
        """
        lvl = self._caps.scheduler_level
        if lvl <= 0:
            return 0
        if lvl == 1:
            return 1
        if lvl == 2:
            return 1
        # Expert / Master は実質「無制限」扱いだが、
        # 実装上は安全のため上限を設ける
        return 10

    def filter_level(self) -> int:
        return self._caps.filter_level

    def ranking_level(self) -> int:
        return self._caps.ranking_level


# ---- モジュールレベルのシングルトン API ----

@lru_cache(maxsize=1)
def _default_guard() -> EditionGuard:
    """
    デフォルト EditionGuard をキャッシュして返す。
    """
    return EditionGuard()


def get_capability(name: str) -> Any:
    """
    GUI/Service から直接呼ばれる想定のショートカット関数。
    """
    return _default_guard().get_capability(name)


def allow_real_account() -> bool:
    return _default_guard().allow_real_account()


def scheduler_limit() -> int:
    return _default_guard().scheduler_limit()


def filter_level() -> int:
    return _default_guard().filter_level()


def ranking_level() -> int:
    return _default_guard().ranking_level()


def current_edition() -> str:
    """
    旧 API が get_current_edition() などだった場合の互換用。
    """
    return _default_guard().edition

