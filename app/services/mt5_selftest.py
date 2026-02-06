# app/services/mt5_selftest.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple
import traceback
import subprocess
import sys
import os  # ★ 追加
import time
import re
from pathlib import Path

from app.core import mt5_client
from app.core.mt5_client import MT5Client
from app.services import mt5_account_store  # ★ 追加
from app.services.inflight_service import make_key as inflight_make_key, mark as inflight_mark, finish as inflight_finish


# JSON安全な文字列に正規化するヘルパー
_CTRL = re.compile(r"[\x00-\x1F\x7F]")


def is_mt5_connected() -> bool:
    """
    MT5 が接続中かどうかを services 経由で返す。
    app.core.mt5_client の _client 状態を参照する。
    """
    return mt5_client.is_connected()


def connect_mt5() -> bool:
    """
    GUI の「ログイン」用。既存の接続経路（run_mt5_selftest と同様）で MT5 に接続する。
    - active_profile から環境変数を適用して mt5_client.initialize() を呼ぶ
    - 成功時 True、失敗時 False（接続は維持しない）
    """
    active = mt5_account_store.get_active_profile_name()
    if not active:
        return False
    mt5_account_store.set_active_profile(active, apply_env=True)
    try:
        mt5_client.shutdown()
    except Exception:
        pass
    return mt5_client.initialize()


def disconnect_mt5() -> None:
    """GUI の「ログアウト」用。mt5_client.shutdown() を呼ぶ。"""
    try:
        mt5_client.shutdown()
    except Exception:
        pass


def get_account_snapshot() -> Dict[str, Any]:
    """
    MT5ログイン中の口座スナップショット（read-only）。
    未接続なら {"ok": False} を返す。shutdown() は呼ばない（ログイン状態を維持するのが目的）。
    """
    if not is_mt5_connected():
        return {"ok": False}

    info = mt5_client.get_account_info()
    if not info:
        return {"ok": False}

    balance = _get_attr(info, "balance", None)
    equity = _get_attr(info, "equity", None)
    margin_free = _get_attr(info, "margin_free", None)

    positions = mt5_client.get_positions()
    n_pos = len(positions) if positions else 0

    return {
        "ok": True,
        "balance": balance,
        "equity": equity,
        "margin_free": margin_free,
        "positions": n_pos,
    }


def _json_safe_str(s: object) -> str:
    """
    JSON安全な文字列に正規化する。
    - None等も安全に処理
    - 制御文字（改行/タブ等）を除去
    """
    t = "" if s is None else str(s)
    # 制御文字は除去（改行/タブも全部落とす方がCLI用途では安全）
    t = _CTRL.sub("", t)
    return t


def _get_attr(obj: Any, name: str, default: Any = "(n/a)") -> Any:
    """
    dict / MT5 の AccountInfo のどちらでも安全に属性を取り出すヘルパー。

    scripts/selftest_mt5.py と同じ挙動にしておく。
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def run_mt5_selftest() -> Tuple[bool, str]:
    """
    MT5 自己診断を実行して、(成功フラグ, ログ文字列) を返す。

    - GUI から呼び出すことを前提に、print ではなく文字列を組み立てる
    - 例外はここでキャッチし、False とスタックトレースを返す
    """
    lines: List[str] = []
    lines.append("=== MT5 self test (GUI) ===")
    lines.append("このテストは、現在の設定タブで選択されている口座プロファイルに基づき、")
    lines.append("MT5 への接続とログイン状態を確認します。")
    lines.append("")

    try:
        # 0) アクティブプロファイルから環境変数を適用
        active = mt5_account_store.get_active_profile_name()
        lines.append(f"現在のアクティブプロファイル: {active or '(未設定)'}")
        if not active:
            lines.append("")
            lines.append("ERROR: アクティブなMT5口座プロファイルが設定されていません。")
            lines.append("設定タブで口座を保存し、「この口座に切り替え」を押してから再実行してください。")
            return False, "\n".join(lines)

        # 念のため、ここでも apply_env=True で環境変数を更新しておく
        mt5_account_store.set_active_profile(active, apply_env=True)

        # 念のため毎回クリーンな状態から始める
        try:
            mt5_client.shutdown()
        except Exception:
            # 失敗しても致命的ではないので無視
            pass

        # 1) initialize
        lines.append("[1] mt5_client.initialize() ...")
        ok = mt5_client.initialize()
        lines.append(f"    -> initialize() returned: {ok!r}")
        if not ok:
            lines.append("")
            lines.append("ERROR: MT5 の初期化に失敗しました。")
            lines.append(" - MT5 ターミナルが起動しているか？")
            lines.append(" - 設定タブで選択した口座ID / サーバー / パスワードは正しいか？")
            return False, "\n".join(lines)

        # 2) account_info
        lines.append("")
        lines.append("[2] mt5_client.get_account_info() ...")
        info = mt5_client.get_account_info()
        if not info:
            lines.append("ERROR: get_account_info() が None / False を返しました。")
            lines.append("      ログイン情報やサーバー設定を確認してください。")
            return False, "\n".join(lines)

        login = _get_attr(info, "login")
        name = _get_attr(info, "name")
        server = _get_attr(info, "server")
        balance = _get_attr(info, "balance")
        equity = _get_attr(info, "equity")
        trade_mode = _get_attr(info, "trade_mode")

        lines.append("  --- Account Info ---")
        lines.append(f"  login      : {login}")
        lines.append(f"  name       : {name}")
        lines.append(f"  server     : {server}")
        lines.append(f"  balance    : {balance}")
        lines.append(f"  equity     : {equity}")
        lines.append(f"  trade_mode : {trade_mode}")
        lines.append("  --------------------")

        # 3) positions (raw)
        lines.append("")
        lines.append("[3] mt5_client.get_positions() ...")
        positions = mt5_client.get_positions()
        n_pos = len(positions) if positions is not None else 0
        lines.append(f"    -> open positions: {n_pos}")
        if positions:
            # 先頭数件だけざっくり表示
            lines.append("    sample positions (up to 3):")
            for i, pos in enumerate(positions[:3]):
                lines.append(f"      [{i}] {pos!r}")

        # 4) positions_df (DataFrame)
        lines.append("")
        lines.append("[4] mt5_client.get_positions_df() ...")
        df = mt5_client.get_positions_df()
        if df is None:
            lines.append("    -> positions_df: None")
        else:
            try:
                shape = getattr(df, "shape", None)
                lines.append(f"    -> positions_df.shape = {shape}")
                # 行数だけ軽く表示
                lines.append(f"    -> positions_df.head():")
                lines.append(df.head(5).to_string())
            except Exception as e:
                lines.append(f"    -> positions_df の表示中にエラー: {e!r}")

        lines.append("")
        lines.append("MT5 self test completed successfully.")
        return True, "\n".join(lines)

    except Exception:
        # ここで例外を全部飲み込んで、GUI 側には文字列で返す
        lines.append("")
        lines.append("ERROR: MT5 自己診断中に例外が発生しました。")
        lines.append("")
        lines.append(traceback.format_exc())
        return False, "\n".join(lines)

    finally:
        # 毎回 shutdown しておくことで、次回テストもクリーンに実行できるようにする
        try:
            mt5_client.shutdown()
        except Exception:
            pass

def run_mt5_orderflow_selftest() -> Tuple[bool, str]:
    """
    scripts/selftest_order_flow.py をサブプロセスとして実行し、
    (成功/失敗) を返す。

    - GUI から呼び出された場合は、print ではなく GUI に表示する
    - 例外は潰してキャッチし、False とスタックトレースを返す
    """
    lines: List[str] = []
    lines.append("=== MT5 order flow self test (GUI) ===")
    lines.append(
        "このテストは、現在のアクティブMT5口座で 0.01 lot の仮想BUY をクローズ実行し、"
        "オーダーフロー結果を画面に表示を確認します。"
    )
    lines.append("")
    lines.append("※ 必ずデモ口座で実行してください。")
    lines.append("")

    try:
        # 0) アクティブプロファイルから env を準備
        active = mt5_account_store.get_active_profile_name()
        lines.append(f"現在のアクティブプロファイル: {active or '(未設定)'}")

        if not active:
            lines.append("")
            lines.append("ERROR: アクティブなMT5口座プロファイルが設定されていません。")
            lines.append("設定タブで口座を保存し、「この口座に切り替え」を押してから再実行してください。")
            return False, "\n".join(lines)

        acc = mt5_account_store.get_profile(active)
        if acc is None:
            lines.append("")
            lines.append(f"ERROR: プロファイル '{active}' の設定が見つかりません。")
            return False, "\n".join(lines)

        # GUIプロセス側でも念のため apply_env しておく
        mt5_account_store.set_active_profile(active, apply_env=True)

        # サブプロセスに渡す環境変数を構築
        env = os.environ.copy()
        env["MT5_LOGIN"] = str(acc.get("login", ""))
        env["MT5_PASSWORD"] = str(acc.get("password", ""))
        env["MT5_SERVER"] = str(acc.get("server", ""))

        # 1) プロジェクトルートを推定（.../app/services/ から2つ上）
        project_root = Path(__file__).resolve().parents[2]

        cmd = [sys.executable, "-m", "scripts.selftest_order_flow"]
        lines.append(f"[INFO] 実行コマンド: {' '.join(cmd)}")
        lines.append(f"[INFO] cwd: {project_root}")
        lines.append("")

        result = subprocess.run(
            cmd,
            cwd=project_root,
            env=env,              # ★ ここが重要：MT5_* を明示的に渡す
            capture_output=True,
            text=True,
            check=False,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        lines.append("--- stdout ---")
        if stdout:
            lines.append(stdout)
        else:
            lines.append("(出力なし)")

        if stderr:
            lines.append("")
            lines.append("--- stderr ---")
            lines.append(stderr)

        lines.append("")
        lines.append(f"[INFO] returncode = {result.returncode}")

        ok = (result.returncode == 0)
        return ok, "\n".join(lines)

    except Exception:
        # 例外は潰して返すので、False + スタックトレースを返す
        lines.append("")
        lines.append("ERROR: selftest_order_flow 実行中に例外が発生しました。")
        lines.append(traceback.format_exc())
        return False, "\n".join(lines)


def mt5_smoke(
    symbol: str = "USDJPY-",
    lot: float = 0.01,
    close_now: bool = True,
    dry: bool = False,
) -> Dict[str, Any]:
    """
    MT5 接続・テスト発注のスモークテストを実行し、結果を安全なdictで返す。

    Parameters
    ----------
    symbol : str
        テスト対象のシンボル（デフォルト: "USDJPY-"）
    lot : float
        テスト発注のロット（デフォルト: 0.01）
    close_now : bool
        True の場合、発注後に即座にクローズする（デフォルト: True）
    dry : bool
        True の場合、実際の発注は行わない（デフォルト: False）

    Returns
    -------
    Dict[str, Any]
        {
            "ok": bool,           # 全体の成功/失敗
            "step": str,          # 最後に到達したステップ名
            "details": {...},     # 各ステップの詳細情報
            "error": {...}        # エラー情報（あれば）
        }
    """
    result: Dict[str, Any] = {
        "ok": False,
        "step": "init",
        "details": {},
        "error": {},
    }

    try:
        # 0) アクティブプロファイルから環境変数を適用
        active = mt5_account_store.get_active_profile_name()
        result["details"]["active_profile"] = _json_safe_str(active or "(未設定)")
        if not active:
            result["step"] = "apply_env"
            result["error"] = {
                "code": "NO_ACTIVE_PROFILE",
                "message": _json_safe_str("アクティブなMT5口座プロファイルが設定されていません。"),
            }
            return result

        mt5_account_store.set_active_profile(active, apply_env=True)
        result["step"] = "apply_env"
        result["details"]["env_applied"] = True

        # 念のため毎回クリーンな状態から始める
        try:
            mt5_client.shutdown()
        except Exception:
            pass

        # 環境変数から MT5Client インスタンスを作成（services層から core を呼ぶのはOK）
        login_val = int(os.getenv("MT5_LOGIN", "0"))
        password_val = os.getenv("MT5_PASSWORD", "")
        server_val = os.getenv("MT5_SERVER", "")
        if not login_val or not password_val or not server_val:
            result["step"] = "create_client"
            result["error"] = {
                "code": "ENV_NOT_SET",
                "message": _json_safe_str("環境変数 MT5_LOGIN/PASSWORD/SERVER が設定されていません。"),
            }
            return result

        client = MT5Client(login=login_val, password=password_val, server=server_val)

        # 1) initialize
        ok = client.initialize()
        result["details"]["initialize"] = {"success": ok}
        if not ok:
            result["step"] = "initialize"
            result["error"] = {
                "code": "INIT_FAILED",
                "message": _json_safe_str("MT5 の初期化に失敗しました。"),
            }
            return result

        result["step"] = "initialize"
        result["details"]["initialize"]["success"] = True

        # 2) login_account
        ok = client.login_account()
        result["details"]["login"] = {"success": ok}
        if not ok:
            result["step"] = "login"
            result["error"] = {
                "code": "LOGIN_FAILED",
                "message": _json_safe_str("MT5 へのログインに失敗しました。"),
            }
            return result

        result["step"] = "login"
        result["details"]["login"]["success"] = True

        # 3) account_info
        info = mt5_client.get_account_info()
        if not info:
            result["step"] = "account_info"
            result["error"] = {
                "code": "ACCOUNT_INFO_FAILED",
                "message": _json_safe_str("get_account_info() が None を返しました。"),
            }
            return result

        login = _get_attr(info, "login")
        name = _get_attr(info, "name")
        server = _get_attr(info, "server")
        balance = _get_attr(info, "balance")
        equity = _get_attr(info, "equity")

        result["step"] = "account_info"
        result["details"]["account_info"] = {
            "login": login,
            "name": _json_safe_str(name),  # 制御文字を除去
            "server": _json_safe_str(server),  # 念のため server も正規化
            "balance": balance,
            "equity": equity,
        }

        # 4) get_tick_spec
        try:
            tick_spec = client.get_tick_spec(symbol)
            result["step"] = "get_tick_spec"
            result["details"]["tick_spec"] = {
                "symbol": _json_safe_str(symbol),  # 念のため symbol も正規化
                "tick_size": tick_spec.tick_size,
                "tick_value": tick_spec.tick_value,
            }
        except Exception as e:
            result["step"] = "get_tick_spec"
            result["error"] = {
                "code": "TICK_SPEC_FAILED",
                "message": _json_safe_str(str(e)),
            }
            return result

        # 5) (dry=False の場合のみ) 成行発注（inflight は services 層で実施）
        if not dry:
            _key = inflight_make_key(symbol)
            try:
                inflight_mark(_key)
            except Exception:
                pass
            ticket = None
            try:
                order_result = client.order_send(symbol=symbol, order_type="BUY", lot=lot)
                ticket = (order_result or (None, None, None))[0]
                result["details"]["order_send"] = {"ticket": ticket}
                if not ticket:
                    result["step"] = "order_send"
                    result["error"] = {
                        "code": "ORDER_SEND_FAILED",
                        "message": _json_safe_str("order_send() が ticket を返しませんでした。"),
                    }
                    return result
            finally:
                try:
                    inflight_finish(key=_key, ok=bool(ticket), symbol=symbol)
                except Exception:
                    pass

            result["step"] = "order_send"
            result["details"]["order_send"]["success"] = True
            result["details"]["order_send"]["symbol"] = _json_safe_str(symbol)
            result["details"]["order_send"]["lot"] = lot

            # 6) (close_now=True の場合) 即クローズ
            if close_now:
                # ポジション出現待ち（最大10秒）
                deadline = time.time() + 10.0
                position_found = False
                while time.time() < deadline:
                    import MetaTrader5 as MT5  # type: ignore[import]
                    pos = MT5.positions_get(ticket=ticket)
                    if pos:
                        position_found = True
                        break
                    time.sleep(0.5)

                if not position_found:
                    result["step"] = "wait_position"
                    result["error"] = {
                        "code": "POSITION_NOT_FOUND",
                        "message": _json_safe_str("発注後、ポジションが見つかりませんでした。"),
                    }
                    return result

                _key_close = inflight_make_key(symbol)
                try:
                    inflight_mark(_key_close, intent="CLOSE", ticket=ticket)
                except Exception:
                    pass
                ok = False
                try:
                    ok = bool(client.close_position(ticket=ticket, symbol=symbol))
                finally:
                    try:
                        inflight_finish(key=_key_close, ok=ok, symbol=symbol, intent="CLOSE", ticket=ticket)
                    except Exception:
                        pass
                result["details"]["close_position"] = {"success": ok}
                if not ok:
                    result["step"] = "close_position"
                    result["error"] = {
                        "code": "CLOSE_POSITION_FAILED",
                        "message": _json_safe_str("close_position() が失敗しました。"),
                    }
                    return result

                result["step"] = "close_position"
                result["details"]["close_position"]["success"] = True
        else:
            result["step"] = "order_send"
            result["details"]["order_send"] = {"skipped": True, "reason": "dry_run"}

        # 成功
        result["ok"] = True
        return result

    except Exception as e:
        # 例外は握り、安全なdictを返す
        result["error"] = {
            "code": "EXCEPTION",
            "message": _json_safe_str(str(e)),
            "traceback": _json_safe_str(traceback.format_exc()),
        }
        return result

    finally:
        # 毎回 shutdown しておく
        try:
            mt5_client.shutdown()
        except Exception:
            pass
