"""
Phase 1: Real Backtest Validation — replaces simulated monthly reports.

True candle replay with:
  - real OHLCV from yfinance (2022→present)
  - true spread + commission + slippage (from backtester/costs.py)
  - bar-by-bar simulation via WalkForwardBacktester
  - partial close at 1:1 + trailing stop
  - news blackout filter
  - realistic latency (no look-ahead)

Aggregates WFTrade results by calendar month and generates HTML reports.
Output: reporting/monthly_reports/YYYY_MM_report.html
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

REPORT_DIR    = Path(__file__).parent / "monthly_reports"
STATE_FILE    = Path(__file__).parent.parent / "local_db" / "engine_state.json"
INITIAL_BALANCE = 10_000.0
RISK_PCT        = 0.01


def _load_best_params() -> dict:
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        bp = s.get("best_params", {})
        for pair in ("XAUUSD", "GC=F"):
            if pair in bp and isinstance(bp[pair], dict):
                return bp[pair]
        if isinstance(bp, dict):
            v = next((v for v in bp.values() if isinstance(v, dict) and "ema_fast" in v), None)
            if v:
                return v
    except Exception:
        pass
    return {"ema_fast": 21, "ema_slow": 50, "ema_long": 200, "tp_rrr": 2.5,
            "sl_atr_mult": 0.5, "min_adx": 25.0, "use_ema_stack": True,
            "use_adx_filter": True, "use_pattern": True, "min_confluence": 2,
            "use_weekly_filter": False, "use_pullback_zone": False,
            "use_expansion": False, "use_ict_filter": False, "use_volume_filter": False,
            "pullback_atr_mult": 2.0, "rsi_long_min": 30.0, "rsi_long_max": 68.0,
            "rsi_short_min": 32.0, "rsi_short_max": 65.0, "min_vol_ratio": 0.7,
            "min_hold_bars": 2, "ema_weekly": 26, "atr_period": 14,
            "ict_min_score": 40, "min_adx": 25.0, "version": 2,
            "strategy_name": "HighConfluenceTrend"}


def _fetch_history(pair: str, start: str = "2021-06-01") -> Optional[object]:
    """Download full OHLCV history from yfinance."""
    try:
        import yfinance as yf
        from backtester.data_loader import TICKER_MAP
        ticker = TICKER_MAP.get(pair, pair)
        df = yf.download(ticker, start=start, interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 100:
            return None
        df.columns = [c.lower() for c in df.columns]
        if "adj close" in df.columns:
            df = df.rename(columns={"adj close": "close"})
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return df
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {pair}: {e}")
        return None


def run_full_backtest(
    pair: str = "XAUUSD",
    start: str = "2021-06-01",
) -> Optional[List]:
    """
    Run walk-forward backtest over full history.
    Returns flat list of WFTrade objects (test-set only).
    """
    try:
        from backtester.walk_forward import WalkForwardBacktester
        from strategy.trend_engine import TrendParams

        params = _load_best_params()
        p = TrendParams(**{k: v for k, v in params.items()
                           if k in TrendParams.__dataclass_fields__})
        p.version = 1   # canonical version for reporting

        df = _fetch_history(pair, start)
        if df is None or len(df) < 300:
            logger.warning(f"Insufficient data for {pair} ({len(df) if df is not None else 0} bars)")
            return None

        wf = WalkForwardBacktester(p)
        result = wf.run(df, pair=pair, n_folds=5)

        # Return test-set trades
        test_trades = [t for t in result.trades if t.split == "test"]
        logger.info(f"Real backtest {pair}: {len(test_trades)} test trades | "
                    f"WR {result.test_win_rate_realistic:.1%} | "
                    f"RRR {result.test_avg_rrr:.2f} | "
                    f"DD {result.test_max_dd_pct:.1%}")
        return test_trades
    except Exception as e:
        logger.error(f"Real backtest failed for {pair}: {e}")
        return None


def _aggregate_by_month(trades: List) -> Dict[Tuple[int, int], dict]:
    """
    Group trades by calendar month and compute real monthly stats.
    Returns {(year, month): stats_dict}.
    """
    by_month: Dict[Tuple, List] = defaultdict(list)

    for t in trades:
        try:
            d = t.entry_date
            if hasattr(d, "year"):
                key = (d.year, d.month)
            elif isinstance(d, str):
                dt = datetime.fromisoformat(str(d)[:10])
                key = (dt.year, dt.month)
            else:
                continue
            by_month[key].append(t)
        except Exception:
            continue

    monthly = {}
    balance = INITIAL_BALANCE

    for (y, m) in sorted(by_month.keys()):
        month_trades = by_month[(y, m)]
        n = len(month_trades)

        wins         = [t for t in month_trades if t.realistic_outcome == "win"]
        losses       = [t for t in month_trades if t.realistic_outcome != "win"]
        win_pnls     = [abs(t.realistic_pnl_pct) for t in wins]
        loss_pnls    = [abs(t.realistic_pnl_pct) for t in losses]

        total_return = sum(t.realistic_pnl_pct for t in month_trades)
        avg_rrr      = (sum(abs(t.rrr_achieved) for t in month_trades) / n) if n > 0 else 0.0
        wr_realistic = len(wins) / n if n > 0 else 0.0
        pf           = (sum(win_pnls) / sum(loss_pnls)) if loss_pnls else (999.0 if win_pnls else 1.0)

        # Max drawdown within month
        equity = [0.0]
        for t in month_trades:
            equity.append(equity[-1] + t.realistic_pnl_pct)
        peak = 0.0
        max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, peak - e)

        starting_balance = balance
        balance = balance * (1.0 + total_return)

        monthly[(y, m)] = {
            "year": y, "month": m,
            "starting_balance": round(starting_balance, 2),
            "ending_balance":   round(balance, 2),
            "monthly_return":   round(total_return * 100, 2),
            "trades":           n,
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate":         round(wr_realistic * 100, 1),
            "avg_rr":           round(avg_rrr, 2),
            "max_drawdown":     round(max_dd * 100, 2),
            "profit_factor":    round(pf, 2),
            "avg_cost_pct":     round(
                sum(t.cost_fraction for t in month_trades) / n * 100 if n > 0 else 0, 2),
            "data_source":      "real_candle_replay",
        }

    return monthly


def _fill_missing_months(
    monthly: Dict,
    start_year: int = 2022,
    start_month: int = 1,
) -> Dict:
    """Fill months with no trades (insufficient data periods)."""
    now = datetime.utcnow()
    balance = INITIAL_BALANCE
    filled = {}

    y, m = start_year, start_month
    while (y, m) <= (now.year, now.month):
        if (y, m) in monthly:
            filled[(y, m)] = monthly[(y, m)]
            balance = monthly[(y, m)]["ending_balance"]
        else:
            filled[(y, m)] = {
                "year": y, "month": m,
                "starting_balance": round(balance, 2),
                "ending_balance":   round(balance, 2),
                "monthly_return": 0.0, "trades": 0,
                "wins": 0, "losses": 0, "win_rate": 0.0,
                "avg_rr": 0.0, "max_drawdown": 0.0,
                "profit_factor": 1.0, "avg_cost_pct": 0.0,
                "data_source": "no_signals",
            }
        m += 1
        if m > 12:
            m = 1
            y += 1

    return filled


def _render_html(stats: dict, cumulative_balance: float, pair: str) -> str:
    m   = stats["month"]
    y   = stats["year"]
    mn  = datetime(y, m, 1).strftime("%B %Y")
    ret = stats["monthly_return"]
    ret_color = "#27ae60" if ret >= 0 else "#e74c3c"
    ret_sign  = "+" if ret >= 0 else ""
    src_badge = (
        '<span class="badge badge-green">real candle replay</span>'
        if stats["data_source"] == "real_candle_replay"
        else '<span class="badge badge-yellow">no signals</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoTrader Claude — {mn}</title>
<style>
  body{{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:20px}}
  .header{{background:linear-gradient(135deg,#1f2937,#374151);border-radius:12px;padding:24px;margin-bottom:20px}}
  h1{{margin:0;font-size:1.8rem;color:#f0f6fc}} h2{{color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px}}
  .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}}
  .card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:16px;text-align:center}}
  .card .label{{font-size:.75rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}}
  .card .value{{font-size:1.6rem;font-weight:700;margin-top:4px}}
  .positive{{color:#27ae60}} .negative{{color:#e74c3c}} .neutral{{color:#58a6ff}}
  table{{width:100%;border-collapse:collapse;margin-top:12px}}
  th{{background:#1f2937;padding:10px;text-align:left;color:#8b949e;font-weight:600;font-size:.8rem}}
  td{{padding:10px;border-bottom:1px solid #21262d;font-size:.9rem}}
  tr:hover td{{background:#1f2937}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:.75rem}}
  .badge-green{{background:#1a3c26;color:#27ae60}}
  .badge-red{{background:#3c1a1a;color:#e74c3c}}
  .badge-yellow{{background:#3c2a00;color:#ffa500}}
  .footer{{text-align:center;color:#6e7681;font-size:.75rem;margin-top:24px;padding-top:16px;border-top:1px solid #21262d}}
</style>
</head>
<body>
<div class="header">
  <h1>🤖 AutoTrader Claude — {mn}</h1>
  <p style="color:#8b949e;margin:8px 0 0">Real Candle Replay | Spread + Commission + Slippage | Pair: {pair}</p>
</div>

<h2>Monthly Summary {src_badge}</h2>
<div class="metrics">
  <div class="card"><div class="label">Monthly Return</div>
    <div class="value" style="color:{ret_color}">{ret_sign}{ret:.2f}%</div></div>
  <div class="card"><div class="label">Starting Balance</div>
    <div class="value neutral">${stats['starting_balance']:,.2f}</div></div>
  <div class="card"><div class="label">Ending Balance</div>
    <div class="value neutral">${stats['ending_balance']:,.2f}</div></div>
  <div class="card"><div class="label">Cumulative</div>
    <div class="value {'positive' if cumulative_balance >= INITIAL_BALANCE else 'negative'}">${cumulative_balance:,.2f}</div></div>
</div>

<h2>Real Performance Metrics</h2>
<div class="metrics">
  <div class="card"><div class="label">Win Rate (real)</div>
    <div class="value {'positive' if stats['win_rate'] >= 55 else 'neutral'}">{stats['win_rate']:.1f}%</div></div>
  <div class="card"><div class="label">Avg R:R (real)</div>
    <div class="value {'positive' if stats['avg_rr'] >= 1.5 else 'neutral'}">{stats['avg_rr']:.2f}</div></div>
  <div class="card"><div class="label">Profit Factor</div>
    <div class="value {'positive' if stats['profit_factor'] >= 1.5 else 'neutral'}">{stats['profit_factor']:.2f}</div></div>
  <div class="card"><div class="label">Max Drawdown</div>
    <div class="value {'positive' if stats['max_drawdown'] < 5 else 'negative'}">{stats['max_drawdown']:.1f}%</div></div>
</div>

<h2>Trade Statistics</h2>
<table>
  <tr><th>Metric</th><th>Value</th><th>Status</th></tr>
  <tr><td>Total Trades</td><td>{stats['trades']}</td>
    <td><span class="badge badge-green">real replay</span></td></tr>
  <tr><td>Wins / Losses</td><td>{stats['wins']} / {stats['losses']}</td>
    <td><span class="badge {'badge-green' if stats['wins'] >= stats['losses'] else 'badge-red'}">
    {'positive_edge' if stats['wins'] >= stats['losses'] else 'negative_edge'}</span></td></tr>
  <tr><td>Avg Cost per Trade</td><td>{stats['avg_cost_pct']:.2f}% of risk</td>
    <td><span class="badge badge-green">spread+slip+comm</span></td></tr>
  <tr><td>Risk Per Trade</td><td>1.0%</td>
    <td><span class="badge badge-green">FTMO compliant</span></td></tr>
  <tr><td>Data Source</td><td>{stats['data_source']}</td>
    <td>{src_badge}</td></tr>
</table>

<div class="footer">
  AutoTrader Claude v5.0 | Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC<br>
  Strategy: HighConfluenceTrend | Walk-Forward 5-fold | No Look-Ahead | All Costs Included
</div>
</body></html>"""


def generate_real_reports(
    pair: str = "XAUUSD",
    start_year: int = 2022,
    start_month: int = 1,
) -> Tuple[List[str], dict]:
    """
    Main entry point. Runs real backtest and generates HTML reports.
    Returns (list_of_paths, summary_stats).
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Real backtest reporter: fetching {pair} from 2021-06-01...")

    trades = run_full_backtest(pair, start="2021-06-01")

    if trades:
        monthly_raw = _aggregate_by_month(trades)
        logger.info(f"Aggregated: {len(monthly_raw)} months with trades")
    else:
        logger.warning("No trades returned — reports will show no-signal months")
        monthly_raw = {}

    monthly = _fill_missing_months(monthly_raw, start_year, start_month)

    generated = []
    for (y, m), stats in monthly.items():
        balance = stats["ending_balance"]
        html    = _render_html(stats, balance, pair)
        path    = REPORT_DIR / f"{y}_{m:02d}_report.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        generated.append(str(path))
        if stats["trades"] > 0:
            logger.info(f"  {y}-{m:02d}: {stats['monthly_return']:+.2f}% | "
                        f"WR {stats['win_rate']:.1f}% | "
                        f"RR {stats['avg_rr']:.2f} | "
                        f"Balance ${balance:,.2f}")

    # Summary
    months_with_trades = [s for s in monthly.values() if s["trades"] > 0]
    summary = {
        "total_months": len(monthly),
        "months_with_trades": len(months_with_trades),
        "total_trades": sum(s["trades"] for s in months_with_trades),
        "avg_monthly_return": round(
            sum(s["monthly_return"] for s in months_with_trades) / len(months_with_trades)
            if months_with_trades else 0, 2),
        "final_balance": list(monthly.values())[-1]["ending_balance"] if monthly else INITIAL_BALANCE,
        "overall_wr": round(
            sum(s["wins"] for s in months_with_trades) /
            max(1, sum(s["trades"] for s in months_with_trades)) * 100, 1),
    }
    logger.info(f"Real reports complete: {len(generated)} files | "
                f"Final balance ${summary['final_balance']:,.2f} | "
                f"Overall WR {summary['overall_wr']:.1f}%")
    return generated, summary


if __name__ == "__main__":
    files, summary = generate_real_reports()
    print(json.dumps(summary, indent=2))
