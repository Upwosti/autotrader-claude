"""
Confidence Score Calculator — rates setups 0–10 using the full ICT confluence
model (liquidity sweep, BOS/MSS, PD array quality, kill zone, HTF bias,
displacement, plus double-purge / SMT / CRT-TBS / IRL-ERL / key-liquidity
bonuses, minus spread / news / DXY penalties).
"""

from dataclasses import dataclass
from typing import Optional
from config import StrategyParams
from strategy.liquidity import LiquiditySweep
from strategy.bos import BOS
from strategy.fvg import FVG


# Per-array quality contribution (out of 2.0 max).
PD_ARRAY_QUALITY = {
    "OB": 2.0, "BB": 2.0, "MB": 2.0, "PB": 2.0,
    "IFVG": 1.5, "FVG": 1.5, "IRB": 1.5, "LV": 1.5, "BPR": 1.5,
    "RB": 1.0, "VI": 1.0,
}


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
        pd_array_kind: Optional[str] = None,
        double_purge: bool = False,
        smt_divergence: bool = False,
        crt_tbs: bool = False,
        irl_erl_aligned: bool = False,
        key_liquidity_dol: bool = False,
    ) -> SetupScore:
        breakdown = {}
        score = 0.0

        # 1. Liquidity sweep confirmed (+2)
        if sweep and sweep.confirmed:
            breakdown["liquidity_sweep"] = 2.0
            score += 2.0
        else:
            breakdown["liquidity_sweep"] = 0.0

        # 2. BOS / MSS confirmed (+2)
        if bos:
            breakdown["bos"] = 2.0
            score += 2.0
        else:
            breakdown["bos"] = 0.0

        # 3. PD Array quality (variable). Prefer the explicit PD array kind;
        #    fall back to a valid FVG.
        if pd_array_kind and pd_array_kind in PD_ARRAY_QUALITY:
            pts = PD_ARRAY_QUALITY[pd_array_kind]
            breakdown["pd_array"] = pts
            score += pts
        elif fvg and fvg.valid:
            breakdown["pd_array"] = 1.5
            score += 1.5
        else:
            breakdown["pd_array"] = 0.0

        # 4. Kill zone (+1.5)
        if in_kill_zone:
            breakdown["kill_zone"] = 1.5
            score += 1.5
        else:
            breakdown["kill_zone"] = 0.0

        # 5. HTF bias aligned (+1.5)
        if higher_tf_bias_aligned:
            breakdown["htf_bias"] = 1.5
            score += 1.5
        else:
            breakdown["htf_bias"] = 0.0

        # 6. Displacement present (+1)
        if displacement_present:
            breakdown["displacement"] = 1.0
            score += 1.0
        else:
            breakdown["displacement"] = 0.0

        # 7. Strong wick quality (+0.5)
        if sweep and sweep.wick_pct >= 0.5:
            breakdown["strong_wick"] = 0.5
            score += 0.5
        else:
            breakdown["strong_wick"] = 0.0

        # ─── Bonuses ──────────────────────────────────────────────────────
        if double_purge:
            breakdown["double_purge"] = 1.0
            score += 1.0
        if smt_divergence:
            breakdown["smt"] = 1.0
            score += 1.0
        if crt_tbs:
            breakdown["crt_tbs"] = 0.5
            score += 0.5
        if irl_erl_aligned:
            breakdown["irl_erl"] = 0.5
            score += 0.5
        if key_liquidity_dol:
            breakdown["key_liquidity"] = 0.5
            score += 0.5

        # ─── Deductions ───────────────────────────────────────────────────
        if not spread_ok:
            score -= 1.0
            breakdown["spread_penalty"] = -1.0
        else:
            breakdown["spread_penalty"] = 0.0

        if not news_clear:
            score -= 1.0
            breakdown["news_penalty"] = -1.0
        else:
            breakdown["news_penalty"] = 0.0

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
