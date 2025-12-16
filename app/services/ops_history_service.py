"""
Ops実行履歴サービス

logs/ops/ops_result.jsonl に実行結果を蓄積し、過去結果を読み込む機能を提供する。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger


def _normalize_human_text(s: str) -> str:
    """
    表示用テキストを正規化する。

    Args:
        s: 元の文字列

    Returns:
        正規化された文字列（全角スペース→半角、連続スペース→1個、strip適用）
    """
    if not s:
        return ""
    # 全角スペースを半角へ
    s = s.replace("　", " ")
    # 連続スペースを1個へ
    s = re.sub(r" +", " ", s)
    # strip()を適用
    return s.strip()


def _is_dry_record(record: dict, cmd: object | None = None) -> bool | None:
    """
    レコードまたはコマンドからdryフラグを判定する。

    Args:
        record: Ops履歴レコード
        cmd: コマンド（str/list、オプション）

    Returns:
        True: dry runである
        False: dry runではない
        None: 判定不能
    """
    # 1) record["dry"] が存在すればそれを bool 化して返す（"1"/1/True対応）
    if "dry" in record:
        dry_val = record["dry"]
        if isinstance(dry_val, bool):
            return dry_val
        if isinstance(dry_val, (int, str)):
            # "1"/1 -> True, "0"/0 -> False
            if str(dry_val).strip() in ("1", "true", "True", "TRUE"):
                return True
            if str(dry_val).strip() in ("0", "false", "False", "FALSE"):
                return False
        # その他の値はNoneを返す（判定不能）

    # 2) cmd（str/list）から -Dry 1 / --dry 1 を検出
    if cmd is not None:
        cmd_str = ""
        if isinstance(cmd, list):
            cmd_str = " ".join(str(x) for x in cmd)
        elif isinstance(cmd, str):
            cmd_str = cmd
        else:
            return None

        # -Dry 1 または --dry 1 を検出（大文字小文字不問）
        cmd_lower = cmd_str.lower()
        # -Dry 1 または --dry 1 のパターンを検出（値が1ならTrue、0ならFalse）
        # パターン: -dry または --dry の後にスペースと0または1
        match = re.search(r'[-]+dry\s+([01])', cmd_lower, re.IGNORECASE)
        if match:
            dry_val = match.group(1)
            return dry_val == "1"

    # 3) 判定不能なら None を返す
    return None


class OpsHistoryService:
    """Ops実行履歴サービス"""

    def __init__(self) -> None:
        # プロジェクトルートを推定（.../app/services/ から2つ上）
        self.project_root = Path(__file__).resolve().parents[2]
        self.history_file = self.project_root / "logs" / "ops" / "ops_result.jsonl"
        # ディレクトリが存在しない場合は作成
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def append_ops_result(self, rec: dict) -> None:
        """
        logs/ops/ops_result.jsonl に 1行追記（UTF-8、JSONL）

        Args:
            rec: 追記するレコード。以下のキーを必ず含む:
                - symbol: str
                - profiles: list[str]（単一でも list に統一）
                - started_at: str（ISO文字列）
                - ok: bool
                - step: str
                - model_path: str|None（無ければ None）
        """
        try:
            # トップレベルを正規化
            normalized = self._normalize_record(rec)

            # JSONL形式で追記
            with self.history_file.open("a", encoding="utf-8") as f:
                json.dump(normalized, f, ensure_ascii=False)
                f.write("\n")

            logger.debug(f"Appended ops result to {self.history_file}")
        except Exception as e:
            logger.error(f"Failed to append ops result: {e}")
            # クラッシュさせない（例外はログのみ）

    def _normalize_record(self, rec: dict) -> dict:
        """
        レコードを正規化する。

        Args:
            rec: 元のレコード

        Returns:
            正規化されたレコード
        """
        normalized = dict(rec)

        # 必須キーの確認と正規化
        # symbol
        if "symbol" not in normalized:
            normalized["symbol"] = rec.get("Symbol") or rec.get("symbol") or "USDJPY-"

        # profiles: 単一でも list に統一
        if "profiles" not in normalized:
            # profile または Profiles から取得
            profile = rec.get("profile") or rec.get("Profile")
            profiles = rec.get("Profiles")
            if profiles:
                if isinstance(profiles, str):
                    profiles = [p.strip() for p in profiles.split(",") if p.strip()]
                elif isinstance(profiles, list):
                    profiles = [str(p) for p in profiles if p]
                else:
                    profiles = []
            elif profile:
                profiles = [str(profile)]
            else:
                profiles = []
            normalized["profiles"] = profiles

        # started_at: ISO文字列
        if "started_at" not in normalized:
            # 現在時刻をISO形式で設定
            now = datetime.now(timezone.utc)
            normalized["started_at"] = now.isoformat()

        # ok: bool
        if "ok" not in normalized:
            # status や result から推定
            status = rec.get("status") or rec.get("Status")
            if status and isinstance(status, str):
                normalized["ok"] = status.lower() in ("ok", "success", "completed")
            else:
                normalized["ok"] = rec.get("ok", False)

        # step: str
        if "step" not in normalized:
            normalized["step"] = str(rec.get("step") or rec.get("Step") or "unknown")

        # model_path: str|None
        if "model_path" not in normalized:
            model_path = rec.get("model_path") or rec.get("modelPath") or rec.get("ModelPath")
            normalized["model_path"] = str(model_path) if model_path else None

        return normalized

    def load_ops_history(
        self, symbol: Optional[str] = None, limit: int = 200
    ) -> list[dict]:
        """
        JSONL を末尾から最大 limit 件読み、壊れ行はスキップ。

        Args:
            symbol: シンボルでフィルタ（None の場合は全件）
            limit: 最大読み込み件数

        Returns:
            レコードのリスト（新しい順）
        """
        if not self.history_file.exists():
            return []

        records = []
        try:
            # ファイルを末尾から読み込む（効率化のため全行読み込み）
            with self.history_file.open("r", encoding="utf-8") as f:
                lines = f.readlines()

            # 末尾から逆順に処理
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue

                try:
                    rec = json.loads(line)
                    # symbol フィルタ
                    if symbol and rec.get("symbol") != symbol:
                        continue
                    # record_idを付与（読み取り時に生成）
                    if "record_id" not in rec:
                        rec["record_id"] = self._generate_record_id(rec)
                    records.append(rec)
                    if len(records) >= limit:
                        break
                except json.JSONDecodeError:
                    # 壊れ行はスキップ
                    logger.warning(f"Skipping invalid JSON line in {self.history_file}")
                    continue

        except Exception as e:
            logger.error(f"Failed to load ops history: {e}")

        return records

    def _parse_started_at(self, s: str) -> Optional[datetime]:
        """
        started_at 文字列を datetime に変換する（複数フォーマット対応）。

        Args:
            s: started_at 文字列（ISO形式、US表記など）

        Returns:
            datetime オブジェクト（パース失敗時は None）
        """
        if not s:
            return None
        s = str(s).strip()

        # 1) ISO 8601（T + timezone、7桁小数もOK）
        # Pythonのfromisoformatは "Z" が苦手なので置換
        try:
            iso = s.replace("Z", "+00:00")
            return datetime.fromisoformat(iso)
        except Exception:
            pass

        # 2) 旧：US表記 "12/16/2025 12:22:55"
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass

        # 3) 旧：もし作ってしまった想定 "%Y-%m-%d %H:%M:%S"
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass

        return None

    def _generate_record_id(self, rec: dict) -> str:
        """
        レコードから安定したrecord_idを生成する。

        Args:
            rec: レコードdict

        Returns:
            record_id（SHA1ハッシュの先頭16文字）
        """
        symbol = str(rec.get("symbol", ""))
        started_at = str(rec.get("started_at", ""))
        profiles = rec.get("profiles", [])
        profiles_str = ",".join(sorted([str(p) for p in profiles])) if isinstance(profiles, list) else str(profiles)
        step = str(rec.get("step", ""))
        ok = str(rec.get("ok", False))

        # 安定したIDを生成
        key = f"{symbol}|{started_at}|{profiles_str}|{step}|{ok}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def summarize_ops_history(self, symbol: Optional[str] = None) -> dict:
        """
        履歴を集計する。

        Args:
            symbol: シンボルでフィルタ（None の場合は全件）

        Returns:
            集計結果:
                - week_total: int（直近7日の総件数）
                - week_ok: int（直近7日の成功件数）
                - week_ok_rate: float（直近7日の成功率、0.0-1.0）
                - month_total: int（当月の総件数）
                - month_ok: int（当月の成功件数）
                - month_ok_rate: float（当月の成功率、0.0-1.0）
                - consecutive_failures: int（連続失敗回数）
                - week_model_updates: int（直近7日のモデル更新回数）
                - month_model_updates: int（当月のモデル更新回数）
                - last_model_update: dict|None（最後のモデル更新情報）
                - last: dict|None（最後の1件：started_at/ok/step/model_path/profiles/symbol）
        """
        records = self.load_ops_history(symbol=symbol, limit=1000)  # 集計用に多めに取得

        week_total = 0
        week_ok = 0
        month_total = 0
        month_ok = 0
        week_model_updates = 0
        month_model_updates = 0
        consecutive_failures = 0
        last = None
        last_model_update = None

        # 連続失敗をカウント（records は新しい順なので最初から見る）
        for idx, rec in enumerate(records):
            if not rec.get("ok", False):
                consecutive_failures += 1
            else:
                break

        for rec in records:
            started_at_str = rec.get("started_at")
            if not started_at_str:
                continue

            try:
                # started_at をパース（複数フォーマット対応）
                dt = self._parse_started_at(started_at_str)
                if dt is None:
                    # パース失敗時はそのレコードを集計対象から除外（WARNは出さない）
                    continue

                # naive/aware混在を避けるため、naiveに統一してから比較
                if dt.tzinfo is None:
                    dt_naive = dt
                else:
                    dt_naive = dt.replace(tzinfo=None)

                # 週次・月次の判定は日付のみで行う（時刻は無視）
                dt_date = dt_naive.date()
                now_date = datetime.now().date()
                week_ago_date = now_date - timedelta(days=7)
                month_start_date = datetime(now_date.year, now_date.month, 1).date()
                ok = rec.get("ok", False)
                apply_performed = rec.get("apply_performed", False)

                # 週次カウント（日付のみで判定）
                if dt_date >= week_ago_date:
                    week_total += 1
                    if ok:
                        week_ok += 1
                    if apply_performed:
                        week_model_updates += 1

                # 月次カウント（日付のみで判定）
                if dt_date >= month_start_date:
                    month_total += 1
                    if ok:
                        month_ok += 1
                    if apply_performed:
                        month_model_updates += 1

                # 最後の1件を取得（records は新しい順なので最初の有効なものが最新）
                if last is None:
                    record_id = self._generate_record_id(rec)
                    last = {
                        "record_id": record_id,
                        "started_at": started_at_str,
                        "ok": ok,
                        "step": rec.get("step", "unknown"),
                        "model_path": rec.get("model_path"),
                        "profiles": rec.get("profiles", []),
                        "symbol": rec.get("symbol", "USDJPY-"),
                    }

                # 最後のモデル更新を取得（apply_performed == True の最初のもの）
                if last_model_update is None and apply_performed:
                    record_id = self._generate_record_id(rec)
                    last_model_update = {
                        "record_id": record_id,
                        "started_at": started_at_str,
                        "model_path": rec.get("model_path"),
                        "ok": ok,
                        "step": rec.get("step", "unknown"),
                    }
            except Exception as e:
                logger.warning(f"Failed to parse started_at '{started_at_str}': {e}")
                continue

        # 成功率を計算
        week_ok_rate = (week_ok / week_total) if week_total > 0 else 0.0
        month_ok_rate = (month_ok / month_total) if month_total > 0 else 0.0

        return {
            "week_total": week_total,
            "week_ok": week_ok,
            "week_ok_rate": week_ok_rate,
            "month_total": month_total,
            "month_ok": month_ok,
            "month_ok_rate": month_ok_rate,
            "consecutive_failures": consecutive_failures,
            "week_model_updates": week_model_updates,
            "month_model_updates": month_model_updates,
            "last_model_update": last_model_update,
            "last": last,
        }


# シングルトンインスタンス
_ops_history_service: Optional[OpsHistoryService] = None


def get_ops_history_service() -> OpsHistoryService:
    """OpsHistoryService のシングルトンインスタンスを返す。"""
    global _ops_history_service
    if _ops_history_service is None:
        _ops_history_service = OpsHistoryService()
    return _ops_history_service


# トップレベル関数ラッパー（互換性のため）
def summarize_ops_history(symbol: Optional[str] = None) -> dict:
    """
    履歴を集計する（トップレベル関数ラッパー）。

    Args:
        symbol: シンボルでフィルタ（None の場合は全件）

    Returns:
        集計結果（OpsHistoryService.summarize_ops_history と同じ）
    """
    return get_ops_history_service().summarize_ops_history(symbol=symbol)


def load_ops_history(symbol: Optional[str] = None, limit: int = 200) -> list[dict]:
    """
    JSONL を末尾から最大 limit 件読み込む（トップレベル関数ラッパー）。

    Args:
        symbol: シンボルでフィルタ（None の場合は全件）
        limit: 最大読み込み件数

    Returns:
        レコードのリスト（新しい順）
    """
    return get_ops_history_service().load_ops_history(symbol=symbol, limit=limit)


def append_ops_result(rec: dict) -> None:
    """
    logs/ops/ops_result.jsonl に 1行追記（トップレベル関数ラッパー）。

    Args:
        rec: 追記するレコード
    """
    return get_ops_history_service().append_ops_result(rec)


def replay_from_record(record: dict, *, run: bool = False) -> dict:
    """
    レコードから条件を復元して再実行する。

    Args:
        record: Ops履歴レコード（load_ops_history などで取得した dict）
        run: True のときのみ実際に再実行。False ならコマンドを表示するだけ

    Returns:
        実行結果dict:
            - ok: bool（成功/失敗）
            - cmd: list[str]（実行コマンド）
            - rc: int（returncode、run=False のときは 0）
            - stdout: str（標準出力）
            - stderr: str（標準エラー出力）
            - error: dict|None（エラー情報）
    """
    import subprocess
    import sys
    import tempfile
    import json
    from pathlib import Path

    # バリデーション：record is None / not isinstance(record, dict) を弾く
    if not isinstance(record, dict) or not record:
        return {
            "ok": False,
            "rc": 1,
            "cmd": [],
            "stdout": "",
            "stderr": "",
            "error": {"code": "NO_RECORD", "message": "No record selected / record is empty"},
        }

    project_root = Path(__file__).resolve().parents[2]
    cmd = None
    rc = 0
    stdout = ""
    stderr = ""
    error = None

    try:
        # レコードを一時JSONLファイルに書き込む（1行JSONで確実に）
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".jsonl",
            delete=False,
            encoding="utf-8",
            dir=str(project_root / "logs" / "ops"),
        ) as tmp_file:
            # JSONLとして1行に固定
            line = json.dumps(record, ensure_ascii=True, separators=(",", ":"), default=str)
            tmp_file.write(line + "\n")
            tmp_file.flush()
            tmp_path = Path(tmp_file.name)

        # tools/ops_replay.py を実行（tempファイルは1行しかないのでindex指定は不要）
        cmd = [
            sys.executable,
            "-m",
            "tools.ops_replay",
            "--log",
            str(tmp_path),
        ]
        if run:
            cmd.append("--run")

        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        rc = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # record_idを生成
        record_id = get_ops_history_service()._generate_record_id(record)

        # stderrを解析（行配列と末尾取得）
        stderr_lines = stderr.splitlines() if stderr else []
        stderr_tail = stderr_lines[-5:] if len(stderr_lines) > 5 else stderr_lines
        # stderr_tailの各行を正規化
        stderr_tail = [_normalize_human_text(line) for line in stderr_tail]

        # dry判定（servicesで一貫して判定）
        # record["cmd"] を優先して渡す（テストで record["cmd"] をいじったら dry_flag に反映される）
        dry_flag = _is_dry_record(record, record.get("cmd") or cmd)

        # summaryを生成
        ok = rc == 0
        if ok:
            title = "再実行が成功しました"
            hint = "正常に完了しました。"
        else:
            title = f"再実行が失敗しました (rc={rc})"
            # エラー原因を推定
            stderr_lower = stderr.lower()
            if "market_closed" in stderr_lower or "trade_disabled" in stderr_lower:
                hint = "市場が閉まっているか、取引が無効です。"
            elif dry_flag is True:
                hint = "Dry runモードでした。実際の実行を試しますか？"
            else:
                hint = "エラーが発生しました。再試行を検討してください。"

        summary = {
            "title": _normalize_human_text(title),
            "rc": rc,
            "ok": ok,
            "stderr_lines": len(stderr_lines),
            "stderr_tail": stderr_tail,
            "hint": _normalize_human_text(hint),
        }

        # next_actionを生成（自動再実行ポリシーの下地）
        next_action = {"kind": "NONE", "reason": "", "params": {}}
        if not ok:
            stderr_lower = stderr.lower()
            # Dry runだった場合（dry_flag is True かつ rc != 0）
            if dry_flag is True:
                next_action = {
                    "kind": "PROMOTE_DRY_TO_RUN",
                    "reason": "Dry runモードでした。実際の実行を試すことができます。",
                    "params": {"dry": False},
                }
            # 市場クローズ系エラー
            elif "market_closed" in stderr_lower or "trade_disabled" in stderr_lower:
                next_action = {
                    "kind": "NONE",
                    "reason": "市場が閉まっているか、取引が無効です。",
                    "params": {},
                }
            # その他のエラー
            else:
                next_action = {
                    "kind": "RETRY",
                    "reason": "エラーが発生しました。再試行を検討してください。",
                    "params": {"max_retries": 1},
                }

        # 一時ファイルを削除
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

        return {
            "ok": ok,
            "cmd": cmd,
            "rc": rc,
            "stdout": stdout,
            "stderr": stderr,
            "stderr_full": stderr_lines,  # 折りたたみ表示用
            "error": None,
            "summary": summary,
            "next_action": next_action,
            "record_id": record_id,
            "dry": dry_flag,  # 表示/デバッグ用
        }

    except Exception as e:
        logger.exception("replay_from_record failed: %s", e)
        error = {
            "code": "REPLAY_ERROR",
            "message": str(e),
        }

        # エラー時もsummaryとnext_actionを返す
        summary = {
            "title": "再実行エラー",
            "rc": -1,
            "ok": False,
            "stderr_lines": 0,
            "stderr_tail": [],
            "hint": f"エラー: {str(e)}",
        }

        next_action = {
            "kind": "NONE",
            "reason": "システムエラーが発生しました。",
            "params": {},
        }

        return {
            "ok": False,
            "cmd": cmd or [],
            "rc": -1,
            "stdout": stdout,
            "stderr": stderr,
            "stderr_full": [],
            "error": error,
            "summary": summary,
            "next_action": next_action,
            "record_id": get_ops_history_service()._generate_record_id(record) if isinstance(record, dict) else None,
        }

