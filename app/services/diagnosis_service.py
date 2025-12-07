# app/services/diagnosis_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional


@dataclass
class DiagnosisParams:
    """診断AIの入力条件（必要になれば拡張する）"""

    profile: str
    start: Optional[date] = None
    end: Optional[date] = None


class DiagnosisService:
    """
    診断AIサービス（v0）

    将来的には以下の情報源を使って分析を行う想定：
      - backtests/{profile}/monthly_returns.csv
      - logs/decisions_*.jsonl
    ここでは API 形だけを整えて、ダミーの結果を返す。
    """

    def analyze(
        self,
        profile: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        診断AIのメインAPI。

        仕様で定義されたキーを必ず返す：
          - time_of_day_stats
          - volatility_stats
          - winning_conditions
          - dd_pre_signal
          - anomalies
        """
        params = DiagnosisParams(profile=profile, start=start, end=end)

        # v0 ではまだロジックを入れず、空の構造だけ返す
        result: Dict[str, Any] = {
            "profile": params.profile,
            "range": {
                "start": params.start.isoformat() if params.start else None,
                "end": params.end.isoformat() if params.end else None,
            },
            "time_of_day_stats": {},
            "volatility_stats": {},
            "winning_conditions": {},
            "dd_pre_signal": {},
            "anomalies": [],
        }
        return result


# シングルトン的に使うためのヘルパー
_diagnosis_service: Optional[DiagnosisService] = None


def get_diagnosis_service() -> DiagnosisService:
    global _diagnosis_service
    if _diagnosis_service is None:
        _diagnosis_service = DiagnosisService()
    return _diagnosis_service
