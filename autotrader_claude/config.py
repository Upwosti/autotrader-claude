"""
AutoTrader Claude - Central Configuration
All parameters live here. Evolution layer reads and writes back to this module.
"""

import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# ─── API KEYS ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVER: str = os.getenv("EMAIL_RECEIVER", "")
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))

# ─── TRADING PAIRS ───────────────────────────────────────────────────────────
PAIRS: List[str] = ["XAUUSD", "BTCUSD", "GBPUSD", "EURUSD"]
PRIMARY_PAIR: str = "XAUUSD"

# ─── TIMEFRAMES ──────────────────────────────────────────────────────────────
BIAS_TIMEFRAMES: List[str] = ["W1", "D1"]
ENTRY_TIMEFRAME: str = "H4"
CONFIRMATION_TIMEFRAME: str = "H1"

# ─── SESSION / KILL ZONES (UTC) ──────────────────────────────────────────────
LONDON_KILL_ZONE: Dict[str, int] = {"start": 7, "end": 10}    # 07:00–10:00 UTC
NY_KILL_ZONE: Dict[str, int] = {"start": 13, "end": 16}        # 13:00–16:00 UTC
NEWS_BLACKOUT_MINUTES: int = 10                                  # skip 10 min before high-impact news

# ─── RISK MANAGEMENT ─────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT: float = 1.0          # % of account per trade
DAILY_LOSS_LIMIT_PCT: float = 2.0        # max daily drawdown %
MAX_DRAWDOWN_PCT: float = 5.0            # hard stop drawdown %
MAX_SPREAD_PIPS: Dict[str, float] = {
    "XAUUSD": 0.5,
    "BTCUSD": 25.0,
    "GBPUSD": 0.8,
    "EURUSD": 0.6,
}
MAX_OPEN_TRADES: int = 1                  # one trade at a time across all pairs

# ─── STRATEGY PARAMETERS (evolvable) ─────────────────────────────────────────
@dataclass
class StrategyParams:
    # Liquidity
    liquidity_sweep_lookback: int = 20          # bars to look back for highs/lows
    liquidity_min_touches: int = 2              # min touches before valid level
    liquidity_sweep_wick_pct: float = 0.3       # wick must be >= 30% of candle range

    # BOS
    bos_confirmation: str = "candle_close"      # "candle_close" | "wick"
    bos_lookback: int = 10                      # bars to look back for structure

    # FVG
    fvg_min_size_pips: float = 5.0             # minimum FVG size in pips
    fvg_max_age_bars: int = 50                  # FVG expires after N bars
    fvg_fill_threshold_pct: float = 0.5         # ignore if >50% filled

    # Confidence
    confidence_threshold: float = 7.0           # minimum score to take trade (out of 10)

    # RRR
    min_rrr: float = 3.0                        # minimum risk:reward ratio

    # Kill zones (hours UTC)
    london_start: int = 7
    london_end: int = 10
    ny_start: int = 13
    ny_end: int = 16

    # Session filter
    use_london: bool = True
    use_ny: bool = True
    use_asia: bool = False

    # ─── New PD Array parameters ──────────────────────────────────────────
    # Order Block
    ob_lookback: int = 30                       # bars to scan for OBs
    ob_impulse_factor: float = 1.5              # impulse range vs avg range

    # Propulsion / Mitigation / Breaker
    pb_mt_tolerance: float = 0.5                # 50% midpoint tolerance
    structure_swing_lookback: int = 5           # swing pivot half-window

    # Rejection Block
    rb_min_wick_pct: float = 0.5                # wick must be >= % of range

    # Immediate Rebalance / Volume Imbalance / Liquidity Void
    irb_tolerance_pct: float = 0.1              # pullback proximity tolerance

    # Key liquidity
    equal_level_tolerance_pips: float = 5.0     # pip tolerance for equal H/L
    key_liquidity_lookback: int = 100           # bars to scan for key levels

    # IRL/ERL
    irl_erl_proximity_pips: float = 10.0        # "near" a level threshold

    # SMT
    smt_lookback: int = 20                      # bars to compare swings

    # Double purge
    double_purge_lookback: int = 50             # bars to scan for double purge

    # CRT / TBS
    crt_range_factor: float = 1.3               # CRT candle vs avg range
    tbs_min_bodies_inside: int = 2              # min confirmation bodies inside CRT

    # Consolidation filter
    adr_atr_min_ratio: float = 0.3              # skip if ADR < 30% of ATR(20)
    atr_period: int = 20

    # Evolution metadata
    version: int = 1
    notes: str = "Initial parameters"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyParams":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# Default active params — evolution layer swaps this out
ACTIVE_PARAMS: StrategyParams = StrategyParams()

# ─── EVOLUTION SETTINGS ──────────────────────────────────────────────────────
EVOLUTION_MIN_TRADES: int = 100             # min trades before comparing
EVOLUTION_SUMMARY_EVERY: int = 30           # summary after N iterations
FINAL_REPORT_TRADES: int = 10_000           # final report threshold
MINI_REPORT_TRADES: int = 100
EVOLUTION_REPORT_TRADES: int = 1_000

# Parameter mutation ranges (used by optimizer)
PARAM_RANGES: Dict[str, Any] = {
    # CRT/TBS signal parameters (most impactful for module performance)
    "crt_range_factor": (0.8, 2.5, 0.1),          # CRT candle size vs avg range
    "tbs_min_bodies_inside": [1, 2, 3],            # Bodies inside CRT for TBS confirm
    "equal_level_tolerance_pips": (2.0, 10.0, 1.0),
    # ICT signal parameters
    "liquidity_sweep_lookback": (10, 50, 5),
    "liquidity_min_touches": (1, 4, 1),
    "liquidity_sweep_wick_pct": (0.1, 0.6, 0.1),
    "bos_confirmation": ["candle_close", "wick"],
    "bos_lookback": (5, 20, 5),
    "fvg_min_size_pips": (2.0, 15.0, 1.0),
    "fvg_max_age_bars": (20, 100, 10),
    "fvg_fill_threshold_pct": (0.3, 0.8, 0.1),
    "confidence_threshold": [6.0, 7.0, 8.0],
    "min_rrr": [2.0, 3.0, 4.0],
    "use_london": [True, False],
    "use_ny": [True, False],
}

# ─── BACKTESTER ───────────────────────────────────────────────────────────────
BACKTEST_INITIAL_CAPITAL: float = 10_000.0
BACKTEST_COMMISSION_PCT: float = 0.05        # 0.05% per side
BACKTEST_SLIPPAGE_PIPS: float = 1.0
DATA_DIR: str = os.path.join(os.path.expanduser("~"), "AutoTraderClaude", "data")

# ─── DASHBOARD ────────────────────────────────────────────────────────────────
DASHBOARD_HOST: str = "0.0.0.0"
DASHBOARD_PORT: int = 5000
DASHBOARD_DEBUG: bool = False

# ─── CLAUDE MODEL ─────────────────────────────────────────────────────────────
CLAUDE_MODEL: str = "claude-opus-4-8"
CLAUDE_MAX_TOKENS: int = 8192

# ─── SMT CORRELATION PAIRS ────────────────────────────────────────────────────
# Positively correlated pairs (used for SMT divergence detection).
# Each value is a dict with the correlated partner and the correlation sign.
SMT_CORRELATION_PAIRS: Dict[str, Dict[str, Any]] = {
    "EURUSD": {"partner": "GBPUSD", "correlation": "positive"},
    "GBPUSD": {"partner": "EURUSD", "correlation": "positive"},
    "AUDUSD": {"partner": "NZDUSD", "correlation": "positive"},
    "NZDUSD": {"partner": "AUDUSD", "correlation": "positive"},
    "XAUUSD": {"partner": "XAGUSD", "correlation": "positive"},   # Gold / Silver
    "XAGUSD": {"partner": "XAUUSD", "correlation": "positive"},
    "BTCUSD": {"partner": "ETHUSD", "correlation": "positive"},
    "ETHUSD": {"partner": "BTCUSD", "correlation": "positive"},
}

# DXY is negatively correlated with these (a divergence vs DXY is a tell).
DXY_NEGATIVE_PAIRS: List[str] = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "XAUUSD"]

# ─── LOGGING ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = "INFO"
LOG_FILE: str = os.path.join(os.path.expanduser("~"), "AutoTraderClaude", "logs", "autotrader.log")
