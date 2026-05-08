"""
AutoTrader Claude - Central Configuration
All parameters live here. Evolution layer reads and writes back to this module.
"""

import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_env_path, override=True)

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
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME: str = os.getenv("GITHUB_USERNAME", "")
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "autotrader-claude")

# ─── MT5 ─────────────────────────────────────────────────────────────────────
MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
MT5_SERVER: str = os.getenv("MT5_SERVER", "")

# ─── TRADING PAIRS ───────────────────────────────────────────────────────────
PAIRS: List[str] = ["XAUUSD", "BTCUSD", "GBPUSD", "EURUSD"]
ALL_PAIRS: List[str] = PAIRS          # alias used across modules
PRIMARY_PAIR: str = "XAUUSD"
SECONDARY_PAIRS: List[str] = ["GBPUSD", "EURUSD", "BTCUSD"]
DXY_SYMBOL: str = "DXY"

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
ONE_TRADE_AT_A_TIME: bool = True
MAX_DAILY_LOSS_PCT: float = DAILY_LOSS_LIMIT_PCT  # alias for ftmo_guardian
MAX_TOTAL_DD_PCT: float = MAX_DRAWDOWN_PCT        # alias for ftmo_guardian

# ─── STRATEGY PARAMETERS (evolvable) ─────────────────────────────────────────
@dataclass
class StrategyParams:
    # Liquidity
    liquidity_sweep_lookback: int = 20          # bars to look back for highs/lows
    liquidity_min_touches: int = 1              # min touches before valid level
    liquidity_sweep_wick_pct: float = 0.3       # wick must be >= 30% of candle range

    # BOS
    bos_confirmation: str = "candle_close"      # "candle_close" | "wick"
    bos_lookback: int = 10                      # bars to look back for structure

    # FVG
    fvg_min_size_pips: float = 5.0             # minimum FVG size in pips
    fvg_max_age_bars: int = 50                  # FVG expires after N bars
    fvg_fill_threshold_pct: float = 0.5         # ignore if >50% filled

    # Confidence
    confidence_threshold: float = 5.0           # minimum score to take trade (out of 10)

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

    # Risk
    risk_pct: float = 1.0                         # % of account risked per trade

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
    "liquidity_sweep_lookback": (10, 50, 5),       # (min, max, step)
    "liquidity_min_touches": (1, 4, 1),
    "liquidity_sweep_wick_pct": (0.1, 0.6, 0.1),
    "bos_confirmation": ["candle_close", "wick"],
    "bos_lookback": (5, 20, 5),
    "fvg_min_size_pips": (2.0, 15.0, 1.0),
    "fvg_max_age_bars": (20, 100, 10),
    "fvg_fill_threshold_pct": (0.3, 0.8, 0.1),
    "confidence_threshold": [5.0, 6.0, 7.0, 8.0],
    "min_rrr": [2.0, 2.5, 3.0, 4.0],
    "use_london": [True, False],
    "use_ny": [True, False],
    "risk_pct": (0.5, 2.0, 0.5),
}

# ─── BACKTESTER ───────────────────────────────────────────────────────────────
BACKTEST_INITIAL_CAPITAL: float = 10_000.0
BACKTEST_COMMISSION_PCT: float = 0.05        # 0.05% per side
BACKTEST_SLIPPAGE_PIPS: float = 1.0
DATA_DIR: str = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\data"

# ─── DASHBOARD ────────────────────────────────────────────────────────────────
DASHBOARD_HOST: str = "0.0.0.0"
DASHBOARD_PORT: int = 5000
DASHBOARD_DEBUG: bool = False

# ─── CLAUDE MODEL ─────────────────────────────────────────────────────────────
CLAUDE_MODEL: str = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS: int = 4096

# ─── PATHS ───────────────────────────────────────────────────────────────────
BASE_DIR: str = r"C:\Users\Administrator\Desktop\AutoTraderClaude"
MODELS_DIR: str = r"C:\Users\Administrator\Desktop\AutoTraderClaude\models"
REPORTS_DIR: str = r"C:\Users\Administrator\Desktop\AutoTraderClaude\evolution\reports"

# ─── EVOLUTION ────────────────────────────────────────────────────────────────
MAX_EVOLUTION_ITERS: int = 50

# ─── LOGGING ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = "INFO"
LOG_FILE: str = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\logs\\autotrader.log"

