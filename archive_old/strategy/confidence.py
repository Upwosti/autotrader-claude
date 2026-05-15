"""
Confidence Score Calculator — rates setups 0–10.
Each ICT confluence factor adds to the score.
"""

from dataclasses import dataclass
from typing import Optional
from config import StrategyParams
from strategy.liquidity import LiquiditySweep
from strategy.bos import BOS
from strategy.fvg import FVG


@dataclass
class SetupScore:
    total: float
    breakdown: dict
    passed: bool
    reason: str


class ConfidenceScorer:
    """Calculates a confidence score for a potential trade setup."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def score(
        self,
        sweep: Optional[LiquiditySweep],
        bos: Optional[BOS],
        fvg: Optional[FVG],
        in_kill_zone: bool,
        higher_tf_bias_aligned: bool,
        displacement_present: bool,
        spread_ok: bool,
        news_clear: bool,
        dxy_conflict: bool = False,
        pair: str = "XAUUSD",
    ) -> SetupScore:
        breakdown = {}
        score = 0.0

        # 1. Liquidity sweep present (+2)
        if sweep and sweep.confirmed:
            pts = 2.0
            breakdown["liquidity_sweep"] = pts
            score += pts
        else:
            breakdown["liquidity_sweep"] = 0.0

        # 2. BOS confirmed (+2)
        if bos:
            pts = 2.0
            breakdown["bos"] = pts
            score += pts
        else:
            breakdown["bos"] = 0.0

        # 3. FVG present and valid (+1.5)
        if fvg and fvg.valid:
            pts = 1.5
            breakdown["fvg"] = pts
            score += pts
        else:
            breakdown["fvg"] = 0.0

        # 4. Kill zone timing (+1.5)
        if in_kill_zone:
            pts = 1.5
            breakdown["kill_zone"] = pts
            score += pts
        else:
            breakdown["kill_zone"] = 0.0

        # 5. Higher TF bias aligned (+1.5)
        if higher_tf_bias_aligned:
            pts = 1.5
            breakdown["htf_bias"] = pts
            score += pts
        else:
            breakdown["htf_bias"] = 0.0

        # 6. Displacement present (+1)
        if displacement_present:
            pts = 1.0
            breakdown["displacement"] = pts
            score += pts
        else:
            breakdown["displacement"] = 0.0

        # 7. Liquidity wick quality bonus (+0.5)
        if sweep and sweep.wick_pct >= 0.5:
            pts = 0.5
            breakdown["strong_wick"] = pts
            score += pts
        else:
            breakdown["strong_wick"] = 0.0

        # ─── Deductions ───────────────────────────────────────────────────
        # Spread filter (-1 if spread too wide)
        if not spread_ok:
            score -= 1.0
            breakdown["spread_penalty"] = -1.0
        else:
            breakdown["spread_penalty"] = 0.0

        # News proximity (-1)
        if not news_clear:
            score -= 1.0
            breakdown["news_penalty"] = -1.0
        else:
            breakdown["news_penalty"] = 0.0

        # DXY conflict for XAUUSD (-2)
        if pair == "XAUUSD" and dxy_conflict:
            score -= 2.0
            breakdown["dxy_conflict"] = -2.0
        else:
            breakdown["dxy_conflict"] = 0.0

        score = max(0.0, min(10.0, score))
        passed = score >= self.params.confidence_threshold

        reason = self._build_reason(breakdown, passed, score, self.params.confidence_threshold)
        return SetupScore(total=score, breakdown=breakdown, passed=passed, reason=reason)

    def _build_reason(self, breakdown: dict, passed: bool, score: float, threshold: float) -> str:
        positives = [k for k, v in breakdown.items() if v > 0]
        negatives = [k for k, v in breakdown.items() if v < 0]
        status = "PASS" if passed else "SKIP"
        reason = f"[{status}] Score {score:.1f}/{threshold:.0f} | +"
        reason += ",".join(positives) if positives else "none"
        if negatives:
            reason += " | -" + ",".join(negatives)
        return reason
