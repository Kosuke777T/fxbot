# app/services/mt5_selftest.py
from __future__ import annotations

from typing import Any, List, Tuple
import traceback
import subprocess
import sys
import os  # ★ 追加
from pathlib import Path

from app.core import mt5_client
from app.services import mt5_account_store  # ★ 追加


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
