"""
Result analysis — uses Claude API to explain evolution decisions.
Falls back to rule-based explanations when API is unavailable.
"""

from loguru import logger

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from config import ANTHROPIC_API_KEY


class ResultAnalyzer:
    """Generates human-readable explanations for evolution decisions."""

    def __init__(self):
        self.client = None
        if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
            try:
                self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                logger.info("ResultAnalyzer: Claude API connected")
            except Exception as e:
                logger.warning(f"ResultAnalyzer: Claude API init failed: {e}")

    def explain_change(
        self,
        param_name: str,
        old_val,
        new_val,
        wr_before: float,
        wr_after: float,
        kept: bool,
    ) -> str:
        """Return a short explanation of why a mutation was kept or reverted."""
        if self.client:
            return self._claude_explain(param_name, old_val, new_val, wr_before, wr_after, kept)
        return self._rule_explain(param_name, old_val, new_val, wr_before, wr_after, kept)

    def _claude_explain(self, param_name, old_val, new_val, wr_before, wr_after, kept) -> str:
        decision = "kept" if kept else "reverted"
        wr_delta = wr_after - wr_before
        prompt = (
            f"An ICT trading strategy evolution step changed '{param_name}' "
            f"from {old_val} to {new_val}. "
            f"Win rate went from {wr_before:.1%} to {wr_after:.1%} "
            f"(delta: {wr_delta:+.1%}). Decision: {decision}. "
            f"In one sentence, explain why this result makes sense for an ICT strategy."
        )
        try:
            msg = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Claude explain failed: {e}")
            return self._rule_explain(param_name, old_val, new_val, wr_before, wr_after, kept)

    def _rule_explain(self, param_name, old_val, new_val, wr_before, wr_after, kept) -> str:
        wr_delta = wr_after - wr_before
        direction = "increased" if wr_delta > 0 else "decreased"
        decision = "kept" if kept else "reverted"

        param_context = {
            "liquidity_sweep_lookback": "longer lookback captures stronger levels",
            "liquidity_min_touches": "more touches means stronger liquidity level",
            "liquidity_sweep_wick_pct": "higher wick quality filters noise",
            "fvg_min_size_pips": "larger FVGs indicate stronger imbalances",
            "confidence_threshold": "higher threshold filters lower-quality setups",
            "min_rrr": "higher RRR improves expectancy per trade",
            "bos_confirmation": "confirmation method affects entry timing",
        }

        context = param_context.get(param_name, "parameter affects signal quality")
        return (
            f"Changing {param_name} from {old_val} to {new_val} {direction} win rate "
            f"by {abs(wr_delta):.1%} ({context}); change {decision}."
        )
