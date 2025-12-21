"""
Ops実行履歴サービス

logs/ops/ops_result.jsonl に実行結果を蓄積し、過去結果を読み込む機能を提供する。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any

from loguru import logger

from app.services.wfo_stability_service import evaluate_wfo_stability

# TTLキャッシュ（モジュールレベル）
_SUMMARY_CACHE = {"ts": 0.0, "value": None}


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


def _normalize_profiles(profiles_raw) -> list[str]:
    """
    profiles を list[str] に正規化する。

    Args:
        profiles_raw: プロファイル名（list[str], list[str]（カンマ区切り文字列含む）, str, None など）

    Returns:
        正規化されたプロファイル名のリスト
    """
    if profiles_raw is None:
        return []

    # 文字列の場合はカンマで分割
    if isinstance(profiles_raw, str):
        profiles_raw = [profiles_raw]

    # リストの場合、各要素を処理
    result = []
    for item in profiles_raw:
        if not item:
            continue
        item_str = str(item).strip()
        if not item_str:
            continue

        # カンマ区切りの場合は分割
        if "," in item_str:
            parts = item_str.split(",")
            for part in parts:
                part = part.strip()
                if part:
                    result.append(part)
        else:
            result.append(item_str)

    # 重複除外（順序保持）
    seen = set()
    normalized = []
    for p in result:
        if p not in seen:
            seen.add(p)
            normalized.append(p)

    return normalized


def _is_dry_record(record: dict, cmd: object | None = None) -> bool | None:
    """
    レコードまたはコマンドからdryフラグを判定する（dry優先）。

    Args:
        record: Ops履歴レコード
        cmd: コマンド（str/list、オプション）

    Returns:
        True: dry runである
        False: dry runではない
        None: 判定不能
    """
    # 1) record["dry"] が存在すればそれを bool 化して返す（"1"/1/True対応、優先）
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
        # その他の値は次の判定に進む

    # 2) cmd（str/list）から -Dry 1 / --dry 1 を検出（dryが無い場合のみ）
    if cmd is not None:
        cmd_str = ""
        if isinstance(cmd, list):
            cmd_str = " ".join(str(x) for x in cmd)
        elif isinstance(cmd, str):
            cmd_str = cmd
        else:
            return False  # cmdもdryも無い過去レコードはFalse

        # -Dry 1 または --dry 1 を検出（大文字小文字不問）
        cmd_lower = cmd_str.lower()
        # -Dry 1 または --dry 1 のパターンを検出（値が1ならTrue、0ならFalse）
        # パターン: -dry または --dry の後にスペースと0または1
        match = re.search(r'[-]+dry\s+([01])', cmd_lower, re.IGNORECASE)
        if match:
            dry_val = match.group(1)
            return dry_val == "1"

    # 3) record["cmd"] からも検出を試みる（record内にcmdが保存されている場合）
    if "cmd" in record:
        cmd_from_record = record["cmd"]
        if isinstance(cmd_from_record, str):
            cmd_lower = cmd_from_record.lower()
            match = re.search(r'[-]+dry\s+([01])', cmd_lower, re.IGNORECASE)
            if match:
                dry_val = match.group(1)
                return dry_val == "1"

    # 4) 判定不能（cmdもdryも無い過去レコード）は False を返す
    return False


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

        # promoted_at: ISO文字列（PROMOTE後の状態遷移用）
        if "promoted_at" in rec:
            normalized["promoted_at"] = rec.get("promoted_at")

        return normalized

    def _load_latest_wfo_inputs(self) -> Optional[dict[str, Any]]:
        """
        WFO安定性評価に必要な入力を最新の成果物から集める。
        - metrics_wfo.json（train/test統計）
        - logs/retrain/report_*.json（wfo指標入り）
        返り値は wfo_stability_service が想定する input dict に合わせる。
        """
        # 1) 最新 metrics_wfo.json（backtests と logs/backtest を優先）
        roots = [Path("backtests"), Path("logs") / "backtest"]
        metrics_candidates: list[Path] = []
        for r in roots:
            if r.exists():
                metrics_candidates.extend(r.rglob("metrics_wfo.json"))
        if not metrics_candidates:
            return None
        metrics_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        metrics_path = metrics_candidates[0]

        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None

        # 2) 最新 report_*.json（logs/retrain）
        report_dir = Path("logs") / "retrain"
        report_candidates = list(report_dir.glob("report_*.json")) if report_dir.exists() else []
        report_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        report_json = None
        if report_candidates:
            try:
                report_json = json.loads(report_candidates[0].read_text(encoding="utf-8", errors="replace"))
            except Exception:
                report_json = None

        # wfo_stability_service が欲しい形に合わせて返す
        return {
            "metrics_wfo": metrics,
            "report": report_json,
            "paths": {
                "metrics_wfo": str(metrics_path),
                "report": str(report_candidates[0]) if report_candidates else None,
            },
        }

    def _calc_next_action(self, record: dict) -> dict:
        """
        recordからnext_actionを軽量ルールで計算する（replay_from_recordを呼ばない）。

        Args:
            record: Ops履歴レコード（またはviewのraw）

        Returns:
            next_action dict（{"kind":"...", "reason":"...", "params":{}, "priority":int}）
        """
        # kind → priority マッピング（GUI側のACTION_UI_SPECSと整合）
        KIND_PRIORITY_MAP = {
            "PROMOTE": 300,
            "PROMOTE_DRY_TO_RUN": 300,  # PROMOTE系も300
            "RETRY": 200,
            "NONE": 0,
        }

        def _normalize_next_action(na: dict) -> dict:
            """next_actionにpriorityを付与（付け忘れ防止）"""
            kind = (na.get("kind") or "NONE").upper()
            priority = KIND_PRIORITY_MAP.get(kind, 0)  # 未知のkindは0
            na["priority"] = priority
            return na

        try:
            # レコードから必要な情報を取得
            step_raw = record.get("step")
            step = str(step_raw or "").lower()  # step_rawがNoneの場合は空文字列に
            ok = record.get("ok", False)
            dry = record.get("dry", False)
            promoted_at = record.get("promoted_at")
            apply_performed = record.get("apply_performed", False)

            # 軽量ルールでnext_actionを決定
            # promoted扱い: step == "promoted" が最優先、promoted_at は補助（stepが空またはpromotedの場合のみ）
            if step == "promoted":
                # stepが明確にpromoted → 適用可能（WFO安定性チェック）
                logger.debug(
                    f"[next_action] branch=promoted_step step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                wfo_inputs = self._load_latest_wfo_inputs()
                if not wfo_inputs:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_result_missing",
                        "params": {},
                    })
                out = evaluate_wfo_stability(
                    wfo_inputs.get("metrics_wfo"),
                    metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
                    report_path=wfo_inputs.get("paths", {}).get("report"),
                )
                stable = bool(out.get("stable"))
                if not stable:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_unstable",
                        "params": {"wfo": out},
                    })
                return _normalize_next_action({
                    "kind": "PROMOTE",
                    "reason": "適用可能（PROMOTED済み）",
                    "params": {},
                })
            elif promoted_at is not None and (step_raw is None or step == "" or step == "promoted"):
                # promoted_atがあり、stepがNone/空文字列/promotedの場合のみ → 適用可能（WFO安定性チェック）
                # stepが明確に別値（例: "done", "applied"）の場合は次の判定へ
                logger.debug(
                    f"[next_action] branch=promoted_at step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                wfo_inputs = self._load_latest_wfo_inputs()
                if not wfo_inputs:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_result_missing",
                        "params": {},
                    })
                out = evaluate_wfo_stability(
                    wfo_inputs.get("metrics_wfo"),
                    metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
                    report_path=wfo_inputs.get("paths", {}).get("report"),
                )
                stable = bool(out.get("stable"))
                if not stable:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_unstable",
                        "params": {"wfo": out},
                    })
                return _normalize_next_action({
                    "kind": "PROMOTE",
                    "reason": "適用可能（PROMOTED済み）",
                    "params": {},
                })
            elif step == "applied" or apply_performed:
                # 適用済み → アクションなし
                logger.debug(
                    f"[next_action] branch=applied step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                return _normalize_next_action({
                    "kind": "NONE",
                    "reason": "適用済み",
                    "params": {},
                })
            elif step in ("done", "completed", "success") and ok and dry:
                # dry run成功 → 本番反映可能（WFO安定性チェック）
                logger.debug(
                    f"[next_action] branch=dry_run_success step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                wfo_inputs = self._load_latest_wfo_inputs()
                if not wfo_inputs:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_result_missing",
                        "params": {},
                    })
                out = evaluate_wfo_stability(
                    wfo_inputs.get("metrics_wfo"),
                    metrics_path=wfo_inputs.get("paths", {}).get("metrics_wfo"),
                    report_path=wfo_inputs.get("paths", {}).get("report"),
                )
                stable = bool(out.get("stable"))
                if not stable:
                    return _normalize_next_action({
                        "kind": "NONE",
                        "reason": "wfo_unstable",
                        "params": {"wfo": out},
                    })
                return _normalize_next_action({
                    "kind": "PROMOTE",
                    "reason": "本番反映可能（dry run成功）",
                    "params": {},
                })
            elif not ok:
                # 失敗 → 再実行可能
                logger.debug(
                    f"[next_action] branch=failed step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                return _normalize_next_action({
                    "kind": "RETRY",
                    "reason": "失敗。ログ確認して再実行",
                    "params": {},
                })
            else:
                # それ以外 → アクションなし
                logger.debug(
                    f"[next_action] branch=else step_raw={repr(step_raw)} step={repr(step)} "
                    f"promoted_at={repr(promoted_at)} applied_at={repr(record.get('applied_at'))} "
                    f"apply_performed={repr(apply_performed)} ok={repr(ok)} dry={repr(dry)}"
                )
                return _normalize_next_action({
                    "kind": "NONE",
                    "reason": "",
                    "params": {},
                })
        except Exception as e:
            logger.warning(f"Failed to calculate next_action for record: {e}")
            return _normalize_next_action({"kind": "NONE", "reason": "", "params": {}})

    def _to_ops_view(self, rec: dict, prev_rec: Optional[dict] = None) -> Optional[dict]:
        """
        レコードを表示用ビューに変換する。

        Args:
            rec: Ops履歴レコード
            prev_rec: 前の履歴レコード（diff計算用、None可）

        Returns:
            表示用ビューdict:
                - record_id: str
                - phase: str（PROMOTED/APPLIED/DONE/FAILED/OTHER）
                - timeline: dict（started/promoted/applied/done）
                - headline: str
                - subline: str
                - diff: dict（前のレコードとの差分）
                - raw: dict（元のレコード、詳細表示用）
                - next_action: dict（行動ヒント、summarize_ops_history()側で付与される）
        """
        try:
            # 正規化（rawは正規化前のまま保持するため、先にprofilesを保存）
            normalized = self._normalize_record(rec)
            record_id = self._generate_record_id(normalized)

            # profilesを正規化（diff/headline用）
            profiles_raw = normalized.get("profiles", [])
            profiles_normalized = _normalize_profiles(profiles_raw)

            # phaseを決定
            step = normalized.get("step", "").lower()
            ok = normalized.get("ok", False)
            promoted_at = normalized.get("promoted_at")
            apply_performed = normalized.get("apply_performed", False)

            if step == "promoted" or promoted_at:
                phase = "PROMOTED"
            elif apply_performed:
                phase = "APPLIED"
            elif ok and step in ("done", "completed", "success"):
                phase = "DONE"
            elif not ok:
                phase = "FAILED"
            else:
                phase = "OTHER"

            # timelineを作成
            timeline = {
                "started": normalized.get("started_at"),
                "promoted": normalized.get("promoted_at"),
                "applied": normalized.get("ended_at") if apply_performed else None,
                "done": normalized.get("ended_at") if ok and not apply_performed else None,
            }

            # headline/sublineを生成（正規化済みprofilesを使用）
            symbol = normalized.get("symbol", "USDJPY-")
            profiles_str = ", ".join(profiles_normalized) if profiles_normalized else "なし"

            headline = f"{symbol} - {profiles_str}"
            if phase == "PROMOTED":
                headline += " (PROMOTED)"
            elif phase == "APPLIED":
                headline += " (APPLIED)"
            elif phase == "FAILED":
                headline += " (FAILED)"

            subline_parts = []
            if step:
                subline_parts.append(f"step: {step}")
            if not ok:
                error = normalized.get("error")
                if isinstance(error, dict):
                    error_msg = error.get("message", "")
                    if error_msg:
                        subline_parts.append(f"error: {error_msg[:50]}")
            if normalized.get("dry"):
                subline_parts.append("dry run")

            # next_action reasonを含める（replay_from_recordの結果から取得する必要があるが、ここでは簡易的に）
            subline = " | ".join(subline_parts) if subline_parts else ""

            # diffを計算（前のレコードとの差分）
            diff = {}
            if prev_rec:
                prev_normalized = self._normalize_record(prev_rec)
                # profilesは正規化済み同士で比較
                prev_profiles_raw = prev_normalized.get("profiles", [])
                prev_profiles_normalized = _normalize_profiles(prev_profiles_raw)

                # 比較対象フィールド（profilesは特別処理）
                compare_fields = ["model_path", "close_now", "dry", "cmd", "symbol"]
                for field in compare_fields:
                    current_val = normalized.get(field)
                    prev_val = prev_normalized.get(field)
                    if current_val != prev_val:
                        diff[field] = {"from": prev_val, "to": current_val}

                # profilesは正規化済み同士で比較
                if profiles_normalized != prev_profiles_normalized:
                    diff["profiles"] = {"from": prev_profiles_normalized, "to": profiles_normalized}

            return {
                "record_id": record_id,
                "phase": phase,
                "timeline": timeline,
                "headline": headline,
                "subline": subline,
                "diff": diff,
                # ソート用フィールド
                "started_at": normalized.get("started_at"),  # ISO文字列またはNone
                "ts": rec.get("ts") or normalized.get("started_at"),  # フォールバック用
                # next_actionはsummarize_ops_history()側で必要分だけ付与（パフォーマンス改善）
                # 元のレコードも保持（詳細表示用）
                "raw": normalized,
            }
        except Exception as e:
            logger.warning(f"Failed to convert record to view: {e}")
            return None

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
        # 候補ファイルを列挙（ops_result_*.jsonl と ops_start_*.jsonl）
        base_dir = self.project_root / "logs" / "ops"
        if not base_dir.exists():
            return []

        candidates = []
        candidates += list(base_dir.glob("ops_result_*.jsonl"))
        candidates += list(base_dir.glob("ops_start_*.jsonl"))
        if not candidates:
            return []

        # 更新日時でソート（最新順）
        candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)

        records = []
        try:
            # すべての候補ファイルから読み込む（最新のファイルから順に）
            for candidate_file in candidates:
                if len(records) >= limit:
                    break

                try:
                    # ファイルを末尾から読み込む（効率化のため全行読み込み）
                    with candidate_file.open("r", encoding="utf-8") as f:
                        lines = f.readlines()

                    # 末尾から逆順に処理
                    for line in reversed(lines):
                        if len(records) >= limit:
                            break

                        line = line.strip()
                        if not line:
                            continue

                        # JSONじゃない行（プレーンログ）を静かにスキップ
                        if not line.startswith("{"):
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
                        except Exception:
                            # 本当に壊れたJSON行（{... が途中で欠けてる場合）は警告
                            logger.warning(f"Skipping invalid JSON line in {candidate_file}")
                            continue
                except Exception as e:
                    logger.warning(f"Failed to read {candidate_file}: {e}")
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

    def _to_epoch(self, s: Optional[str]) -> Optional[float]:
        """
        ISO文字列またはNoneをUTC epoch秒（浮動小数）に変換する（ソート用）。

        Args:
            s: started_at/ts 文字列（ISO形式、timezone付き/無し対応）またはNone

        Returns:
            epoch秒（float）またはNone（パース失敗時）
        """
        if not s:
            return None
        s = str(s).strip()
        if not s:
            return None

        # _parse_started_at()を使ってdatetimeに変換
        dt = self._parse_started_at(s)
        if dt is None:
            return None

        # timezone付きはそのままtimestamp()、timezone無しは既存ロジックに合わせる（ローカル扱い）
        # timestamp()はUTC epochを返す（timezone awareな場合は自動変換、naiveな場合はローカル時刻として扱う）
        try:
            return dt.timestamp()
        except Exception:
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

    def summarize_ops_history(self, symbol: Optional[str] = None, cache_sec: int = 5) -> dict:
        """
        履歴を集計する。

        Args:
            symbol: シンボルでフィルタ（None の場合は全件）
            cache_sec: キャッシュ有効時間（秒、0で無効）

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
        # TTLキャッシュチェック
        now = time.time()
        if cache_sec > 0 and _SUMMARY_CACHE["value"] and now - _SUMMARY_CACHE["ts"] < cache_sec:
            return copy.deepcopy(_SUMMARY_CACHE["value"])  # 破壊防止

        # 計測開始
        t_total_start = time.perf_counter()

        # ログ読み込み
        t_load_start = time.perf_counter()
        records = self.load_ops_history(symbol=symbol, limit=1000)  # 集計用に多めに取得
        t_load_end = time.perf_counter()
        load_sec = t_load_end - t_load_start

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
                    # _normalize_record()を通して正規化（promoted_at等も含める）
                    normalized_rec = self._normalize_record(rec)
                    record_id = self._generate_record_id(normalized_rec)
                    last = {
                        "record_id": record_id,
                        "started_at": started_at_str,
                        "ok": ok,
                        "step": normalized_rec.get("step", "unknown"),
                        "model_path": normalized_rec.get("model_path"),
                        "profiles": normalized_rec.get("profiles", []),
                        "symbol": normalized_rec.get("symbol", "USDJPY-"),
                        "dry": normalized_rec.get("dry"),
                        "cmd": normalized_rec.get("cmd"),
                        "close_now": normalized_rec.get("close_now"),
                        "promoted_at": normalized_rec.get("promoted_at"),
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
        t_stats_end = time.perf_counter()
        stats_sec = t_stats_end - t_load_end

        # 表示用ビューに変換（GUIでカード表示用）
        t_view_start = time.perf_counter()
        items = []
        prev_rec = None
        for rec in records[:50]:  # 最新50件まで表示用ビュー化
            try:
                view = self._to_ops_view(rec, prev_rec)
                if view:
                    items.append(view)
                    prev_rec = rec
            except Exception as e:
                logger.warning(f"Failed to convert record to view: {e}")
                continue
        t_view_end = time.perf_counter()
        view_sec = t_view_end - t_view_start

        # next_actionを先頭N件だけ計算（パフォーマンス改善）
        t_hint_start = time.perf_counter()
        MAX_HINT_ITEMS = 30  # 先頭30件だけnext_actionを計算
        next_action_cache = {}  # メモ化用（key: record_id or cmd）

        for idx, view in enumerate(items[:MAX_HINT_ITEMS]):
            if not view:
                continue

            # キャッシュキーを決定（record_id優先、なければcmd）
            cache_key = view.get("record_id")
            if not cache_key:
                raw = view.get("raw", {})
                cache_key = raw.get("cmd") or str(raw)

            # キャッシュに無ければ計算
            if cache_key not in next_action_cache:
                try:
                    raw = view.get("raw", {})
                    next_action = self._calc_next_action(raw)
                    next_action_cache[cache_key] = next_action
                except Exception as e:
                    logger.warning(f"Failed to calculate next_action for view {idx}: {e}")
                    next_action_cache[cache_key] = {"kind": "NONE", "reason": "", "params": {}, "priority": 0}

            # viewにnext_actionを付与
            view["next_action"] = next_action_cache[cache_key]

        # 残りのitemにはNONEを設定
        for view in items[MAX_HINT_ITEMS:]:
            if view:
                view["next_action"] = {"kind": "NONE", "reason": "", "params": {}, "priority": 0}

        # last_viewを追加（items[0]が最新viewなのでそれを優先）
        last_view = items[0] if items else (self._to_ops_view(last, None) if last else None)

        # last_viewにもnext_actionを付与（items[0]に既に含まれている場合はそのまま）
        if last_view and "next_action" not in last_view:
            # last_viewがitems[0]でない場合（lastから生成した場合）は計算
            cache_key = last_view.get("record_id")
            if not cache_key:
                raw = last_view.get("raw", {})
                cache_key = raw.get("cmd") or str(raw)

            if cache_key in next_action_cache:
                last_view["next_action"] = next_action_cache[cache_key]
            else:
                try:
                    raw = last_view.get("raw", {})
                    # 軽量ルールでnext_actionを計算（replay_from_recordを呼ばない）
                    last_view["next_action"] = self._calc_next_action(raw)
                except Exception as e:
                    logger.warning(f"Failed to calculate next_action for last_view: {e}")
                    last_view["next_action"] = {"kind": "NONE", "reason": "", "params": {}, "priority": 0}
        t_hint_end = time.perf_counter()
        hint_sec = t_hint_end - t_hint_start

        # itemsをソート（priority降順、started_at降順（UTC epoch）、record_idで安定化）
        def sort_key_for_items(item: dict) -> tuple:
            """itemsのソートキー（降順、UTC epoch比較）"""
            # 1) next_action.priority（desc）
            next_action = item.get("next_action", {})
            priority = next_action.get("priority", 0)

            # 2) started_at（desc、UTC epoch比較、None/空/parse失敗ならts、tsも無ければrecord_id）
            started_at = item.get("started_at")
            started_epoch = None  # デフォルト（降順で最後に来るようにNone）

            # started_atをepochに変換
            if started_at:
                started_epoch = self._to_epoch(started_at)

            # started_atがNone/空/parse失敗時はtsを試す
            if started_epoch is None:
                ts = item.get("ts")
                if ts:
                    started_epoch = self._to_epoch(ts)

            # tsも無ければ最後に来るようにする（降順で最後 = -inf）
            if started_epoch is None:
                started_epoch = float("-inf")  # 降順で最後に来る（None相当）

            # 3) record_id（安定化のため必ず含める）
            record_id = item.get("record_id") or ""

            # 降順ソートのため、priorityとstarted_epochは負数
            # started_epochが-infの場合は+infになる（降順で最後）
            started_key = -started_epoch  # 降順のため負数（-infは+infになる）

            return (-priority, started_key, record_id)

        items_sorted = sorted(items, key=sort_key_for_items)

        t_total_end = time.perf_counter()
        total_sec = t_total_end - t_total_start

        result = {
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
            "items": items_sorted,  # GUIで使う表示用ビュー（ソート済み）
            "last_view": last_view,  # 最新の表示用ビュー（新規追加）
        }

        # 計測ログ出力
        logger.info(
            f"PERF summarize_ops_history: "
            f"load={load_sec:.4f} stats={stats_sec:.4f} view={view_sec:.4f} hint={hint_sec:.4f} total={total_sec:.4f}"
        )

        # キャッシュに保存
        _SUMMARY_CACHE["ts"] = now
        _SUMMARY_CACHE["value"] = copy.deepcopy(result)

        return result


# シングルトンインスタンス
_ops_history_service: Optional[OpsHistoryService] = None


def get_ops_history_service() -> OpsHistoryService:
    """OpsHistoryService のシングルトンインスタンスを返す。"""
    global _ops_history_service
    if _ops_history_service is None:
        _ops_history_service = OpsHistoryService()
    return _ops_history_service


# トップレベル関数ラッパー（互換性のため）
def summarize_ops_history(symbol: Optional[str] = None, cache_sec: int = 5) -> dict:
    """
    履歴を集計する（トップレベル関数ラッパー）。

    Args:
        symbol: シンボルでフィルタ（None の場合は全件）
        cache_sec: キャッシュ有効時間（秒、0で無効）

    Returns:
        集計結果（OpsHistoryService.summarize_ops_history と同じ）
    """
    return get_ops_history_service().summarize_ops_history(symbol=symbol, cache_sec=cache_sec)


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


def replay_from_record(record: dict, *, run: bool = False, overrides: dict | None = None) -> dict:
    """
    レコードから条件を復元して再実行する。

    Args:
        record: Ops履歴レコード（load_ops_history などで取得した dict）
        run: True のときのみ実際に再実行。False ならコマンドを表示するだけ
        overrides: レコードを上書きするパラメータ（例: {"dry": False}）

    Returns:
        実行結果dict:
            - ok: bool（成功/失敗）
            - cmd: list[str]（実行コマンド）
            - rc: int（returncode、run=False のときは 0）
            - stdout: str（標準出力）
            - stderr: str（標準エラー出力）
            - error: dict|None（エラー情報）
            - corr_id: str|None（相関ID）
            - source_record_id: str|None（起点レコードID）
    """
    import subprocess
    import sys
    import tempfile
    import json
    import copy
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
            "corr_id": None,
            "source_record_id": None,
        }

    # overridesがある場合、recordをマージ（recordを直接破壊しない）
    if overrides:
        record = copy.deepcopy(record)
        record.update(overrides)

    # corr_id/source_record_idのルールを確定（services側で一貫して決定）
    history_service = get_ops_history_service()
    original_record_id = record.get("record_id")
    if not original_record_id:
        original_record_id = history_service._generate_record_id(record)

    # corr_id: 元recordのcorr_idを引き継ぐ。無ければ生成して付与
    corr_id = record.get("corr_id")
    if not corr_id:
        # 元recordにcorr_idが無い場合、record_idをcorr_idとして使用
        corr_id = original_record_id

    # source_record_id: 元recordの"起点"を指す
    # 既にsource_record_idがあればそれを維持、無ければrecord_idをsourceにする
    source_record_id = record.get("source_record_id")
    if not source_record_id:
        source_record_id = original_record_id

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
        # next_actionにpriorityを付与するためのマッピング（_calc_next_actionと同一）
        KIND_PRIORITY_MAP = {
            "PROMOTE": 300,
            "PROMOTE_DRY_TO_RUN": 300,  # PROMOTE系も300
            "RETRY": 200,
            "NONE": 0,
        }

        def _normalize_next_action_priority(na: dict) -> dict:
            """next_actionにpriorityを付与（付け忘れ防止）"""
            if not na:
                return {"kind": "NONE", "reason": "", "params": {}, "priority": 0}
            kind = (na.get("kind") or "NONE").upper()
            priority = KIND_PRIORITY_MAP.get(kind, 0)  # 未知のkindは0
            na["priority"] = priority
            return na

        next_action = {"kind": "NONE", "reason": "", "params": {}}
        if not ok:
            stderr_lower = stderr.lower()
            # Dry runだった場合（dry_flag is True かつ rc != 0）
            if dry_flag is True:
                next_action = _normalize_next_action_priority({
                    "kind": "PROMOTE_DRY_TO_RUN",
                    "reason": "Dry runモードでした。実際の実行を試すことができます。",
                    "params": {"dry": False},
                })
            # 市場クローズ系エラー
            elif "market_closed" in stderr_lower or "trade_disabled" in stderr_lower:
                next_action = _normalize_next_action_priority({
                    "kind": "NONE",
                    "reason": "市場が閉まっているか、取引が無効です。",
                    "params": {},
                })
            # その他のエラー（RETRY条件を精緻化）
            else:
                # consecutive_failuresを取得
                try:
                    summary = history_service.summarize_ops_history(symbol=record.get("symbol"))
                    consecutive_failures = summary.get("consecutive_failures", 0)
                except Exception:
                    consecutive_failures = 0

                # step/error.codeでリトライ可否を判定
                step = record.get("step", "").lower()
                error_code = None
                if isinstance(record.get("error"), dict):
                    error_code = record.get("error", {}).get("code", "")

                # RETRYを抑制する条件
                retry_suppressed = False
                retry_reason = "エラーが発生しました。再試行を検討してください。"

                # dry=TrueのときはRETRYを抑制
                if dry_flag is True:
                    retry_suppressed = True
                    retry_reason = "Dry runモードのため、RETRYは推奨されません。PROMOTEを検討してください。"

                # consecutive_failuresが3回以上ならRETRYを抑制
                elif consecutive_failures >= 3:
                    retry_suppressed = True
                    retry_reason = f"連続失敗が{consecutive_failures}回のため、RETRYは推奨されません。原因を確認してください。"

                # 設定ミス系エラーはRETRY不可
                elif error_code in ("INVALID_ARGS", "CONFIG_ERROR", "PROFILE_NOT_FOUND"):
                    retry_suppressed = True
                    retry_reason = f"設定エラー({error_code})のため、RETRYは無効です。設定を確認してください。"

                # 環境起因エラー（MT5起動待ち等）はRETRY可
                elif error_code in ("PWSH_NOT_FOUND", "MT5_NOT_READY") or "mt5" in stderr_lower:
                    retry_reason = "環境起因のエラーの可能性があります。しばらく待ってから再試行してください。"

                if retry_suppressed:
                    next_action = _normalize_next_action_priority({
                        "kind": "NONE",
                        "reason": retry_reason,
                        "params": {},
                    })
                else:
                    next_action = _normalize_next_action_priority({
                        "kind": "RETRY",
                        "reason": retry_reason,
                        "params": {"max_retries": 1},
                    })

        # 一時ファイルを削除
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

        # EVENT_STOREに記録（提案ボタン経由の追跡を保証）
        try:
            from app.services.event_store import EVENT_STORE

            # overridesからactionを取得（PROMOTE/RETRY等）
            action = "REPLAY"
            if overrides:
                if overrides.get("dry") is False:
                    action = "PROMOTE"
                elif "retry" in overrides or "action" in overrides:
                    action = str(overrides.get("action", overrides.get("retry", "RETRY")))

            EVENT_STORE.add(
                kind="ops_replay",
                symbol=record.get("symbol", ""),
                reason=f"replay: action={action}, ok={ok}, rc={rc}",
                source_record_id=source_record_id,
                corr_id=corr_id,
            )
        except Exception:
            # イベント記録失敗は無視（replay自体は成功させる）
            pass

        # PROMOTE後の状態遷移：実行成功時に履歴へ保存
        promoted_at = None
        if ok and overrides and (overrides.get("dry") is False or overrides.get("action") == "PROMOTE"):
            try:
                history_service = get_ops_history_service()
                promoted_at = datetime.now(timezone.utc).isoformat()
                promoted_rec = {
                    "symbol": record.get("symbol", ""),
                    "profiles": record.get("profiles", []),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "ok": True,
                    "step": "promoted",
                    "model_path": record.get("model_path"),
                    "dry": False,  # PROMOTE後はdry=False
                    "close_now": record.get("close_now", True),
                    "promoted_at": promoted_at,
                }
                if record.get("cmd"):
                    promoted_rec["cmd"] = record.get("cmd")
                history_service.append_ops_result(promoted_rec)
            except Exception:
                # 履歴保存失敗は無視（replay自体は成功させる）
                pass

        # out/result dict が完成した後、next_actionを補完
        # next_actionにpriorityを付与（全分岐で付け忘れ防止）
        KIND_PRIORITY_MAP = {
            "PROMOTE": 300,
            "PROMOTE_DRY_TO_RUN": 300,  # PROMOTE系も300
            "RETRY": 200,
            "NONE": 0,
        }

        def _normalize_next_action_priority(na: dict) -> dict:
            """next_actionにpriorityを付与（付け忘れ防止）"""
            if not na:
                return {"kind": "NONE", "reason": "", "params": {}, "priority": 0}
            kind = (na.get("kind") or "NONE").upper()
            priority = KIND_PRIORITY_MAP.get(kind, 0)  # 未知のkindは0
            na["priority"] = priority
            return na

        # next_actionをnormalize（全分岐でpriorityが付与されることを保証）
        next_action = _normalize_next_action_priority(next_action)

        out = {
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
            "corr_id": corr_id,  # 相関ID
            "source_record_id": source_record_id,  # 起点レコードID
        }

        # PROMOTE成功時にstepとpromoted_atを返り値に追加
        if ok and overrides and (overrides.get("dry") is False or overrides.get("action") == "PROMOTE"):
            out["step"] = "promoted"
            if promoted_at:
                out["promoted_at"] = promoted_at

        # --- next_action の自動補完（GUIは next_action を表示するだけ） ---
        na = out.get("next_action") or {}
        kind = (na.get("kind") or "NONE").upper()

        if kind == "NONE":
            ok = bool(out.get("ok"))
            if not ok:
                out["next_action"] = _normalize_next_action_priority({"kind": "RETRY", "reason": "last_failed", "params": {}})
            else:
                # apply は None のことがあるので record から dry 判定する
                try:
                    is_dry = _is_dry_record(record)  # 既存（STEP33-6で追加した想定）
                except Exception:
                    is_dry = False
                if is_dry:
                    out["next_action"] = _normalize_next_action_priority({"kind": "PROMOTE", "reason": "dry_run", "params": {}})
        else:
            # 既存のnext_actionにもpriorityを付与（付け忘れ防止）
            out["next_action"] = _normalize_next_action_priority(out.get("next_action", {}))

        return out

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

        # エラー時もcorr_id/source_record_idを返す
        error_corr_id = None
        error_source_record_id = None
        if isinstance(record, dict):
            try:
                history_service = get_ops_history_service()
                original_record_id = record.get("record_id")
                if not original_record_id:
                    original_record_id = history_service._generate_record_id(record)
                error_corr_id = record.get("corr_id") or original_record_id
                error_source_record_id = record.get("source_record_id") or original_record_id
            except Exception:
                pass

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
            "corr_id": error_corr_id,
            "source_record_id": error_source_record_id,
        }

