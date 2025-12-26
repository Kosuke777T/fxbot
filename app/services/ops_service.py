"""
Ops実行サービス（内部用。public APIにしない）

tools/ops_start.ps1 を実行して結果を返す。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger


class OpsService:
    """Ops実行サービス（内部用）"""

    def __init__(self) -> None:
        # プロジェクトルートを推定（.../app/services/ から2つ上）
        self.project_root = Path(__file__).resolve().parents[2]
        self.ops_start_script = self.project_root / "tools" / "ops_start.ps1"
        # 実行開始時刻を保持（履歴追記用）
        self._started_at: Optional[str] = None

    def run_ops_start(
        self,
        *,
        symbol: str = "USDJPY-",
        dry: bool = False,
        close_now: bool = True,
        profile: Optional[str] = None,
        profiles: Optional[list[str]] = None,
    ) -> dict:
        """
        tools/ops_start.ps1 を実行して結果を返す。

        Args:
            symbol: シンボル（例: "USDJPY-"）
            dry: ドライランフラグ
            close_now: CloseNow フラグ
            profile: 単一プロファイル名（profiles と同時指定不可）
            profiles: 複数プロファイル名のリスト（profile と同時指定不可）

        Returns:
            JSONパース成功時:
                ops_start.ps1 の最終JSONをトップレベルに展開し、
                "meta" キーに追加情報（returncode, stdout, stderr, stdout_tail）を格納。
                例: {"status": "...", "step": "...", "meta": {"returncode": 0, ...}}

            JSONパース失敗時:
                {
                    "ok": False,
                    "result": None,
                    "error": {"code": "...", "message": "..."},
                    "stdout": str,
                    "stderr": str,
                    "returncode": int,
                    "stdout_tail": str,
                }
        """
        try:
            # PowerShell 7 (pwsh) のパスを取得
            try:
                pwsh_result = subprocess.run(
                    ["pwsh", "-Command", "exit 0"],
                    capture_output=True,
                    timeout=5,
                )
                pwsh_cmd = "pwsh"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                # pwsh が見つからない場合は powershell を試す
                try:
                    powershell_result = subprocess.run(
                        ["powershell", "-Command", "exit 0"],
                        capture_output=True,
                        timeout=5,
                    )
                    pwsh_cmd = "powershell"
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    return {
                        "ok": False,
                        "result": None,
                        "error": {
                            "code": "PWSH_NOT_FOUND",
                            "message": "PowerShell (pwsh or powershell) not found",
                        },
                        "stdout": "",
                        "stderr": "",
                        "returncode": -1,
                        "stdout_tail": "",
                    }

            # 引数リストを構築
            arg_list = [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.ops_start_script),
                "-Symbol",
                symbol,
                "-Dry",
                "1" if dry else "0",
                "-CloseNow",
                "1" if close_now else "0",
            ]

            # profile または profiles を追加
            if profile and profiles:
                return {
                    "ok": False,
                    "result": None,
                    "error": {
                        "code": "INVALID_ARGS",
                        "message": "profile and profiles cannot be specified at the same time",
                    },
                    "stdout": "",
                    "stderr": "",
                    "returncode": -1,
                    "stdout_tail": "",
                }

            if profile:
                # 単一プロファイルの場合、-Profiles で配列として渡す（ops_start.ps1 側で Count=1 として処理）
                arg_list.extend(["-Profiles", profile])
            elif profiles:
                # 複数プロファイルの場合、カンマ区切り文字列として1つの引数として渡す
                profiles_str = ",".join([p.strip() for p in profiles if p.strip()])
                arg_list.extend(["-Profiles", profiles_str])

            # 実行開始時刻を記録
            self._started_at = datetime.now(timezone.utc).isoformat()

            # 実行コマンドラインを構築
            cmd_list = [pwsh_cmd] + arg_list
            cmd_str = " ".join(cmd_list)
            logger.debug(f"Running ops_start.ps1: {cmd_str}")

            result = subprocess.run(
                cmd_list,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            returncode = result.returncode

            # stdout から最後のJSON行を抽出
            stdout_lines = stdout.strip().splitlines()
            stdout_tail = stdout_lines[-1] if stdout_lines else ""

            # JSONパースを試みる
            parsed_result = None
            parse_error = None
            if stdout_tail:
                try:
                    parsed_result = json.loads(stdout_tail)
                except json.JSONDecodeError as e:
                    parse_error = {
                        "code": "JSON_PARSE_ERROR",
                        "message": str(e),
                        "line": stdout_tail[:200],  # 先頭200文字のみ
                    }

            # JSONが取れた場合: トップレベルに展開して meta に追加情報を格納
            if parsed_result is not None:
                result = dict(parsed_result)
                result.setdefault("meta", {})
                result["meta"].update({
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_tail": stdout_tail,
                })

                # 履歴に追記（dry/cmd/close_nowを含める）
                self._append_to_history(result, symbol, profile, profiles, dry, cmd_str, close_now)

                return result

            # JSONが取れなかった場合: 安全dictを返す
            error_info = parse_error or {
                "code": "EXECUTION_FAILED",
                "message": f"ops_start.ps1 returned {returncode}",
            }
            error_result = {
                "ok": False,
                "result": None,
                "error": error_info,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "stdout_tail": stdout_tail,
            }

            # 履歴に追記（エラー時も、dry/cmd/close_nowを含める）
            self._append_to_history(error_result, symbol, profile, profiles, dry, cmd_str, close_now)

            return error_result

        except Exception as e:
            logger.exception("ops_start execution failed: %s", e)
            error_result = {
                "ok": False,
                "result": None,
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message": str(e),
                },
                "stdout": "",
                "stderr": "",
                "returncode": -1,
                "stdout_tail": "",
            }

            # 履歴に追記（例外時も、ただしクラッシュさせない）
            try:
                # 例外時はcmd_strが無いので空文字列を渡す
                self._append_to_history(error_result, symbol, profile, profiles, dry, "", close_now)
            except Exception as hist_e:
                logger.error(f"Failed to append to history: {hist_e}")

            return error_result

    def _append_to_history(
        self,
        result: dict,
        symbol: str,
        profile: Optional[str],
        profiles: Optional[list[str]],
        dry: bool,
        cmd_str: str,
        close_now: bool,
    ) -> None:
        """
        履歴に追記する。

        Args:
            result: 実行結果
            symbol: シンボル
            profile: 単一プロファイル名
            profiles: 複数プロファイル名のリスト
            dry: ドライランフラグ
            cmd_str: 実行コマンド文字列
            close_now: CloseNowフラグ
        """
        try:
            from app.services.ops_history_service import get_ops_history_service

            # プロファイルリストを構築
            profile_list = []
            if profiles:
                profile_list = [str(p) for p in profiles if p]
            elif profile:
                profile_list = [str(profile)]

            # 履歴レコードを構築
            hist_rec = {
                "symbol": symbol,
                "profiles": profile_list,
                "started_at": self._started_at or datetime.now(timezone.utc).isoformat(),
                "ok": result.get("ok", False),
                "model_path": result.get("model_path") or result.get("modelPath"),
                "dry": dry,  # dryフラグを追加
                "close_now": close_now,  # close_nowフラグを追加
            }
            # step: unknown fallback しない（値があるときだけ入れる）
            step = result.get("step") or result.get("status")
            if step:
                hist_rec["step"] = step

            # cmd_strがあれば追加
            if cmd_str:
                hist_rec["cmd"] = cmd_str

            # その他のキーも保存（meta など）
            for key in ["status", "result", "error", "meta"]:
                if key in result and key not in hist_rec:
                    hist_rec[key] = result[key]

            # 履歴サービスに追記
            history_service = get_ops_history_service()
            history_service.append_ops_result(hist_rec)
        except Exception as e:
            logger.error(f"Failed to append to history: {e}")
            # クラッシュさせない


# シングルトンインスタンス
_ops_service: Optional[OpsService] = None


def get_ops_service() -> OpsService:
    """OpsService のシングルトンインスタンスを返す。"""
    global _ops_service
    if _ops_service is None:
        _ops_service = OpsService()
    return _ops_service
