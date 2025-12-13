from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


# =========================
#  エディション能力定義
# =========================


@dataclass(frozen=True)
class EditionCapability:
    """
    各エディションごとの「できる・できない」をまとめた能力値。
    - demo_only         : デモ口座のみ可か
    - lot_limit         : 1トレードあたりロット上限 (None なら制限なし)
    - scheduler_jobs_max: JobScheduler で登録できるジョブ数の上限 (None なら制限なし)
    - diagnosis_level   : 'none' / 'basic' / 'full'
    - ranking_send      : 成績ランキングへの送信を許可するか
    - filter_level      : 'none' / 'simple' / 'full'
    - shap_limit        : SHAPで表示できる上限特徴量数 (None なら制限なし)
    - fi_limit          : Feature Importanceで表示できる上限特徴量数 (None なら制限なし)
    - profile_multi     : 複数プロファイル運用を許可するか
    - profile_auto_switch: 調子の良いプロファイルへの自動切替を許可するか
    """

    demo_only: bool
    lot_limit: Optional[float]
    scheduler_jobs_max: Optional[int]
    diagnosis_level: str
    ranking_send: bool
    filter_level: str
    shap_limit: Optional[int]
    fi_limit: Optional[int]
    profile_multi: bool
    profile_auto_switch: bool


# エディションごとの能力テーブル
_EDITION_CAPS: Dict[str, EditionCapability] = {
    "FREE": EditionCapability(
        demo_only=True,
        lot_limit=0.03,
        scheduler_jobs_max=0,
        diagnosis_level="none",
        ranking_send=False,
        filter_level="none",
        shap_limit=0,
        fi_limit=0,
        profile_multi=False,
        profile_auto_switch=False,
    ),
    "BASIC": EditionCapability(
        demo_only=False,
        lot_limit=0.1,
        scheduler_jobs_max=1,
        diagnosis_level="none",
        ranking_send=True,
        filter_level="none",
        shap_limit=0,
        fi_limit=0,
        profile_multi=False,
        profile_auto_switch=False,
    ),
    "PRO": EditionCapability(
        demo_only=False,
        lot_limit=None,
        scheduler_jobs_max=1,
        diagnosis_level="basic",
        ranking_send=True,
        filter_level="simple",
        shap_limit=3,
        fi_limit=20,
        profile_multi=True,
        profile_auto_switch=False,
    ),
    "EXPERT": EditionCapability(
        demo_only=False,
        lot_limit=None,
        scheduler_jobs_max=5,
        diagnosis_level="full",
        ranking_send=True,
        filter_level="full",
        shap_limit=20,
        fi_limit=None,
        profile_multi=True,
        profile_auto_switch=True,
    ),
    "MASTER": EditionCapability(
        # MASTER は「Expert の全部＋制限なし＆開発者向け」
        demo_only=False,
        lot_limit=None,
        scheduler_jobs_max=None,  # None = 制限なし
        diagnosis_level="full",
        ranking_send=True,
        filter_level="full",
        shap_limit=None,  # None = 制限なし
        fi_limit=None,
        profile_multi=True,
        profile_auto_switch=True,
    ),
}


DEFAULT_EDITION_NAME = "MASTER"
_EDITION_CONFIG_PATH = Path("configs") / "edition.yaml"


# =========================
#  edition.yaml ローダー
# =========================


def _load_edition_yaml() -> Dict[str, Any]:
    """
    configs/edition.yaml を読み込んで dict を返す。
    読み込みエラー時や形式不正時は空 dict を返す。
    """
    if not _EDITION_CONFIG_PATH.exists():
        return {}

    try:
        data = yaml.safe_load(_EDITION_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        # ログを吐くと import 時にうるさいので、ここでは黙ってフォールバック
        return {}

    if not isinstance(data, dict):
        return {}

    return data


@lru_cache(maxsize=1)
def get_current_edition_name() -> str:
    """
    edition.yaml の `edition` キーから現在のエディション名を取得。
    無効 or 未設定なら DEFAULT_EDITION_NAME を返す。
    """
    data = _load_edition_yaml()
    raw = str(data.get("edition", "")).strip().upper()
    if raw in _EDITION_CAPS:
        return raw
    return DEFAULT_EDITION_NAME


def get_capability(name: Optional[str] = None) -> EditionCapability:
    """
    エディション名から EditionCapability を取得。
    name が None の場合は現在のエディションを使う。
    """
    if name is None:
        name = get_current_edition_name()

    key = name.strip().upper()
    cap = _EDITION_CAPS.get(key)
    if cap is None:
        raise KeyError(f"Unknown edition: {name!r}")

    return cap


# =========================
#  EditionGuard 本体
# =========================


class EditionGuard:
    """
    アプリ全体から利用する「現在のエディション情報」のフロントエンド。

    - name: 'FREE' / 'BASIC' / 'PRO' / 'EXPERT' / 'MASTER'
    - cap : EditionCapability インスタンス
    """

    def __init__(self, name: Optional[str] = None) -> None:
        if name is None:
            name = get_current_edition_name()
        self.name: str = name.strip().upper()
        self.cap: EditionCapability = get_capability(self.name)

    # よく使いそうなショートカットをいくつか用意しておく

    @property
    def demo_only(self) -> bool:
        return self.cap.demo_only

    @property
    def lot_limit(self) -> Optional[float]:
        return self.cap.lot_limit

    @property
    def scheduler_jobs_max(self) -> Optional[int]:
        return self.cap.scheduler_jobs_max

    @property
    def diagnosis_level(self) -> str:
        return self.cap.diagnosis_level

    @property
    def filter_level(self) -> str:
        return self.cap.filter_level

    @property
    def shap_limit(self) -> Optional[int]:
        return self.cap.shap_limit

    @property
    def fi_limit(self) -> Optional[int]:
        return self.cap.fi_limit

    @property
    def profile_multi(self) -> bool:
        return self.cap.profile_multi

    @property
    def profile_auto_switch(self) -> bool:
        return self.cap.profile_auto_switch


@lru_cache(maxsize=1)
def get_guard() -> EditionGuard:
    """
    アプリ全体から使うためのシングルトン EditionGuard。
    """
    return EditionGuard()


# =========================
#  手動テスト用エントリポイント
# =========================


def _print_capabilities_table() -> None:
    for name in ["FREE", "BASIC", "PRO", "EXPERT", "MASTER"]:
        print(f"=== {name} ===")
        print(_EDITION_CAPS[name])


def _print_current() -> None:
    print("--- current from edition.yaml ---")
    print("edition_name:", get_current_edition_name())
    print("capability  :", get_capability())
    guard = get_guard()
    print("guard.name  :", guard.name)
    print("guard.cap   :", guard.cap)


if __name__ == "__main__":
    _print_capabilities_table()
    _print_current()
