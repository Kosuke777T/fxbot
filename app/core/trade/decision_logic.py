# app/core/trade/decision_logic.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Side = Literal["BUY", "SELL"]


@dataclass
class SignalDecision:
    side: Optional[Side]
    prob_buy: float
    prob_sell: float
    confidence: float
    best_threshold: float
    pass_threshold: bool
    reason: str  # "no_prob", "threshold_ng", "threshold_ok", "tie"

    def to_decision_detail(
        self,
        action: str,
        ai_margin: float = 0.03,
        cooldown_sec: Optional[int] = None,
        blocked_reason: Optional[str] = None,
    ) -> dict:
        """
        decision_detail 辞書を生成する。

        Parameters
        ----------
        action : str
            "ENTRY" / "BLOCKED" / "HOLD" / "SKIP" など
        ai_margin : float, optional
            AI判定のマージン（デフォルト: 0.03）
        cooldown_sec : int, optional
            クールダウン秒数（デフォルト: None）
        blocked_reason : str, optional
            ブロック理由（デフォルト: None）

        Returns
        -------
        dict
            decision_detail 辞書
        """
        return {
            "action": action,
            "side": self.side,
            "prob_buy": float(self.prob_buy),
            "prob_sell": float(self.prob_sell),
            "threshold": float(self.best_threshold),
            "ai_margin": float(ai_margin),
            "cooldown_sec": int(cooldown_sec) if cooldown_sec is not None else None,
            "blocked_reason": blocked_reason,
        }


def decide_signal(
    prob_buy: Optional[float],
    prob_sell: Optional[float],
    best_threshold: float,
) -> SignalDecision:
    """
    best_threshold に基づくシグナル判定を共通化する関数。

    - BUY/SELL のどちらが優勢か
    - 優勢側の確信度が best_threshold を超えているか
    """
    # どちらかが None → そもそもシグナルなし
    if prob_buy is None or prob_sell is None:
        return SignalDecision(
            side=None,
            prob_buy=float(prob_buy or 0.0),
            prob_sell=float(prob_sell or 0.0),
            confidence=0.0,
            best_threshold=float(best_threshold),
            pass_threshold=False,
            reason="no_prob",
        )

    pb = float(prob_buy)
    ps = float(prob_sell)

    if pb > ps:
        side: Optional[Side] = "BUY"
        confidence = pb
    elif ps > pb:
        side = "SELL"
        confidence = ps
    else:
        # 完全な引き分け
        return SignalDecision(
            side=None,
            prob_buy=pb,
            prob_sell=ps,
            confidence=pb,
            best_threshold=float(best_threshold),
            pass_threshold=False,
            reason="tie",
        )

    pass_threshold = confidence >= float(best_threshold)
    if not pass_threshold:
        side_result: Optional[Side] = None
        reason = "threshold_ng"
    else:
        side_result = side
        reason = "threshold_ok"

    return SignalDecision(
        side=side_result,
        prob_buy=pb,
        prob_sell=ps,
        confidence=confidence,
        best_threshold=float(best_threshold),
        pass_threshold=pass_threshold,
        reason=reason,
    )

