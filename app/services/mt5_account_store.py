# app/services/mt5_account_store.py
from __future__ import annotations

from typing import Dict, Any, Optional
from pathlib import Path
import json
import os

from loguru import logger

import fxbot_path

# 設定ファイルのパス: <project_root>/config/mt5_accounts.json
_CONFIG_DIR = fxbot_path.get_project_root() / "config"
_CONFIG_FILE = _CONFIG_DIR / "mt5_accounts.json"


def _default_config() -> Dict[str, Any]:
    """設定ファイルが存在しない場合の初期値。"""
    return {
        "active_profile": "",
        "profiles": {},  # name -> {login, password, server}
    }


def load_config() -> Dict[str, Any]:
    """JSON 設定ファイルを読み込んで dict を返す。"""
    try:
        if not _CONFIG_FILE.exists():
            return _default_config()
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"[mt5_account_store] config is not dict: {_CONFIG_FILE}")
            return _default_config()
        # 必須キーのフォールバック
        data.setdefault("active_profile", "")
        data.setdefault("profiles", {})
        if not isinstance(data["profiles"], dict):
            data["profiles"] = {}
        return data
    except Exception as e:
        logger.error(f"[mt5_account_store] failed to load config: {e}")
        return _default_config()


def save_config(cfg: Dict[str, Any]) -> None:
    """設定ファイルを保存する。"""
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with _CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info(f"[mt5_account_store] saved config: {_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"[mt5_account_store] failed to save config: {e}")


def get_profile(name: str) -> Optional[Dict[str, Any]]:
    """プロファイル名から設定を取得する。存在しなければ None。"""
    cfg = load_config()
    profiles = cfg.get("profiles", {})
    if not isinstance(profiles, dict):
        return None
    acc = profiles.get(name)
    if isinstance(acc, dict):
        return acc
    return None


def upsert_profile(name: str, *, login: int, password: str, server: str) -> None:
    """プロファイルを追加または更新する。"""
    cfg = load_config()
    profiles = cfg.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        cfg["profiles"] = profiles

    profiles[name] = {
        "login": int(login),
        "password": str(password),
        "server": str(server),
    }
    save_config(cfg)


def set_active_profile(name: str, *, apply_env: bool = True) -> None:
    """アクティブプロファイルを変更する。

    apply_env=True のとき、os.environ の MT5_LOGIN/PASSWORD/SERVER も更新。
    """
    cfg = load_config()
    profiles = cfg.get("profiles", {})
    if not isinstance(profiles, dict) or name not in profiles:
        logger.warning(f"[mt5_account_store] profile {name!r} not found; active_profile not changed")
        return

    cfg["active_profile"] = name
    save_config(cfg)

    if apply_env:
        acc = profiles[name]
        os.environ["MT5_LOGIN"] = str(acc.get("login", ""))
        os.environ["MT5_PASSWORD"] = str(acc.get("password", ""))
        os.environ["MT5_SERVER"] = str(acc.get("server", ""))
        logger.info(f"[mt5_account_store] applied env for profile {name!r}")


def get_active_profile_name() -> str:
    cfg = load_config()
    active = cfg.get("active_profile")
    return str(active or "")


# ==============================
# Condition Mining window (profile-scoped)
# ==============================
def get_condition_mining_window(profile: Optional[str] = None) -> Dict[str, Any]:
    """
    profile 別 Condition Mining window 設定を取得する。
    無ければデフォルトを返す（保存はしない）。
    """
    DEFAULT = {
        "recent_minutes": 30,
        "past_minutes": 30,
        "past_offset_minutes": 24 * 60,
    }

    cfg = load_config()
    profiles = cfg.get("profiles", {}) or {}

    # NOTE:
    # - ここで早期 return すると cfg['override'] が適用されない（profile未発見/未設定時に不整合）。
    # - 成功条件: 「profile が見つからない場合でも cfg['override'] は必ず最後に適用」。
    out = dict(DEFAULT)

    name = profile or cfg.get("active_profile") or ""
    p = profiles.get(name) if isinstance(profiles, dict) else None
    if not isinstance(p, dict):
        return dict(DEFAULT)

    win = p.get("condition_mining_window")
    if not isinstance(win, dict):
        return dict(DEFAULT)

    out = dict(DEFAULT)
    for k in DEFAULT:
        if k in win:
            try:
                out[k] = int(win[k])
            except Exception:
                pass

    # --- apply config-level override (v5.2) ---
    ov = cfg.get('override')
    if isinstance(ov, dict):
        for k in ('recent_minutes', 'past_minutes', 'past_offset_minutes'):
            if k in ov and ov.get(k) is not None:
                try:
                    out[k] = int(ov.get(k))
                except Exception:
                    pass
    # --- end override ---

    return out

def set_condition_mining_window(patch: Dict[str, Any], profile: Optional[str] = None) -> Dict[str, Any]:
    """
    profile 別 Condition Mining window 設定を更新する。
    patch は部分更新可。
    """
    cfg = load_config()
    profiles = cfg.setdefault("profiles", {})

    name = profile or cfg.get("active_profile") or ""
    if not name:
        raise ValueError("no active profile for condition_mining_window")

    p = profiles.setdefault(name, {})
    if not isinstance(p, dict):
        p = {}
        profiles[name] = p

    win = p.setdefault("condition_mining_window", {})
    if not isinstance(win, dict):
        win = {}
        p["condition_mining_window"] = win

    for k in ["recent_minutes", "past_minutes", "past_offset_minutes"]:
        if k in patch:
            try:
                win[k] = int(patch[k])
            except Exception:
                pass

    save_config(cfg)
    return get_condition_mining_window(profile=name)
