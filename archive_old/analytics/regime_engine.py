"""
OMEGA Regime Engine — public alias for market_regime_engine.
Import from here for consistent naming across the codebase.
"""
from analytics.market_regime_engine import (
    classify_regime,
    RegimeState,
    _compute_regime,
    _ema,
    _compute_adx,
)

__all__ = ["classify_regime", "RegimeState"]
