"""
Realistic trading costs: spread, slippage, commission, overnight swap.
All values sourced from user specifications.
"""

from typing import Dict

# Spread in pips (as specified by user)
SPREAD_PIPS: Dict[str, float] = {
    # Metals
    "XAUUSD": 0.30,  "GC=F": 0.30,
    "XAGUSD": 0.35,  "SI=F": 0.35,
    "XPTUSD": 0.50,
    # Major forex
    "GBPUSD": 0.8,   "EURUSD": 0.6,
    "USDJPY": 0.7,   "USDCHF": 0.9,
    "AUDUSD": 0.9,   "NZDUSD": 1.0,
    "USDCAD": 1.0,
    # Cross forex
    "EURJPY": 1.2,   "GBPJPY": 1.5,
    # Crypto
    "BTCUSD": 15.0,  "ETHUSD": 8.0,
    "BTC-USD": 15.0, "ETH-USD": 8.0,
    # Indices
    "NAS100": 1.0,   "US30": 2.0,   "GER40": 1.5,
    "NQ=F":   1.0,   "YM=F": 2.0,   "FDAX":  1.5,
    # Reference
    "DXY":    0.0,   "DX-Y.NYB": 0.0,
}

# 1 pip in price terms per instrument
PIP_SIZE: Dict[str, float] = {
    "XAUUSD": 0.10,  "GC=F": 0.10,
    "XAGUSD": 0.01,  "SI=F": 0.01,
    "XPTUSD": 0.01,
    "GBPUSD": 0.0001, "EURUSD": 0.0001,
    "USDJPY": 0.01,   "USDCHF": 0.0001,
    "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001,
    "EURJPY": 0.01,   "GBPJPY": 0.01,
    "BTCUSD": 1.0,    "ETHUSD": 0.10,
    "BTC-USD": 1.0,   "ETH-USD": 0.10,
    "NAS100": 1.0,    "US30": 1.0,    "GER40": 1.0,
    "NQ=F":   1.0,    "YM=F": 1.0,    "FDAX":  1.0,
    "DXY":    0.01,   "DX-Y.NYB": 0.01,
}

SLIPPAGE_PIPS: float = 1.0          # per entry OR exit (D1 orders fill at next open, 1 pip realistic)
COMMISSION_USD_PER_LOT: float = 7.0  # round-trip

# Lot sizes (units per standard lot)
LOT_UNITS: Dict[str, float] = {
    "XAUUSD": 100.0,   "GC=F": 100.0,
    "XAGUSD": 5000.0,  "SI=F": 5000.0,
    "XPTUSD": 100.0,
    "GBPUSD": 100_000.0, "EURUSD": 100_000.0,
    "USDJPY": 100_000.0, "USDCHF": 100_000.0,
    "AUDUSD": 100_000.0, "NZDUSD": 100_000.0,
    "USDCAD": 100_000.0,
    "EURJPY": 100_000.0, "GBPJPY": 100_000.0,
    "BTCUSD": 1.0,     "ETHUSD": 1.0,
    "BTC-USD": 1.0,    "ETH-USD": 1.0,
    "NAS100": 1.0,     "US30": 1.0,    "GER40": 1.0,
    "NQ=F":   1.0,     "YM=F": 1.0,    "FDAX":  1.0,
    "DXY": 1.0,        "DX-Y.NYB": 1.0,
}

# Overnight swap in USD per standard lot per calendar night (negative = cost)
OVERNIGHT_SWAP_USD: Dict[str, float] = {
    "XAUUSD": -5.0,  "GC=F": -5.0,
    "XAGUSD": -3.0,  "SI=F": -3.0,
    "BTCUSD": -10.0, "ETHUSD": -5.0,
}

# Assumed account size and risk per trade (for commission normalization)
_ACCOUNT_SIZE = 10_000.0
_RISK_PCT      = 0.01            # 1% risk per trade


def get_round_trip_cost_fraction(
    pair: str,
    risk_price_distance: float,
    hold_bars: int = 1,
) -> float:
    """
    Returns total cost as fraction of the risk amount (1.0 = 100% of risk).

    Includes:
      - Entry spread (half-spread) + entry slippage
      - Exit spread (half-spread) + exit slippage
      - Commission (round-trip)
      - Overnight swap × hold_bars
    """
    if risk_price_distance <= 0:
        return 0.05   # safe fallback

    pip   = PIP_SIZE.get(pair, 0.0001)
    units = LOT_UNITS.get(pair, 100_000.0)

    # Spread + slippage as absolute price per unit
    spread_price    = SPREAD_PIPS.get(pair, 1.0) * pip
    slippage_price  = SLIPPAGE_PIPS * pip

    # Round-trip: entry (half-spread + slip) + exit (half-spread + slip)
    rt_price = spread_price + 2.0 * slippage_price   # total adverse move in price

    # Commission: $7/lot round-trip → expressed as price per unit
    risk_usd     = _ACCOUNT_SIZE * _RISK_PCT
    position_lots = risk_usd / (risk_price_distance * units) if units > 0 else 0.0001
    commission_price = (COMMISSION_USD_PER_LOT * position_lots) / (position_lots * units + 1e-9)

    # Overnight swap: per bar held
    swap_usd_per_lot = OVERNIGHT_SWAP_USD.get(pair, 0.0)
    swap_price = abs(swap_usd_per_lot * position_lots * hold_bars) / (position_lots * units + 1e-9)

    total_adverse_price = rt_price + commission_price + swap_price
    cost_fraction = total_adverse_price / risk_price_distance
    return min(cost_fraction, 0.40)   # cap at 40% of risk


def adjust_entry_for_costs(pair: str, entry: float, direction: str) -> float:
    """Return adjusted entry price after spread + slippage at entry."""
    pip  = PIP_SIZE.get(pair, 0.0001)
    cost = (SPREAD_PIPS.get(pair, 1.0) / 2 + SLIPPAGE_PIPS) * pip
    return entry + cost if direction == "long" else entry - cost


def adjust_exit_for_costs(pair: str, exit_price: float, direction: str) -> float:
    """Return adjusted exit price after spread + slippage at exit."""
    pip  = PIP_SIZE.get(pair, 0.0001)
    cost = (SPREAD_PIPS.get(pair, 1.0) / 2 + SLIPPAGE_PIPS) * pip
    return exit_price - cost if direction == "long" else exit_price + cost
