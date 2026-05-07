# AutoTrader Claude v1.1

An evolutionary ICT (Inner Circle Trader) strategy system that backtests, mutates, and optimises trading parameters automatically using Claude AI for intelligent analysis.

---

## Architecture

```
autotrader_claude/
├── config.py                  # All params, env vars, constants
├── main.py                    # CLI entry point
├── strategy/
│   ├── liquidity.py           # Liquidity sweep detection
│   ├── bos.py                 # Break of Structure detection
│   ├── fvg.py                 # Fair Value Gap detection
│   ├── confidence.py          # Signal confidence scorer (0–10)
│   ├── ict_engine.py          # Signal orchestrator
│   └── evolution.py           # Parameter mutation engine
├── backtester/
│   ├── data_loader.py         # CSV / yfinance / synthetic data
│   ├── engine.py              # Walk-forward backtest engine
│   └── report.py              # Markdown + JSON report generator
├── database/
│   ├── supabase_client.py     # Supabase + local JSON fallback
│   ├── logger.py              # Structured logging to DB
│   └── schema.sql             # SQL schema for Supabase
├── evolution/
│   ├── optimizer.py           # Main evolution loop
│   ├── analyzer.py            # Claude API result explainer
│   └── versioning.py          # Snapshot / restore versions
├── alerts/
│   ├── telegram_bot.py        # Telegram alert sender
│   └── email_alert.py         # SMTP email alert sender
├── reports/
│   ├── mini_report.py         # Every 100 trades
│   ├── evolution_report.py    # Every 1,000 trades
│   └── final_report.py        # Final 10,000 trade report
├── dashboard/
│   └── app.py                 # Flask web dashboard
└── tests/
    ├── test_strategy.py
    ├── test_backtester.py
    └── test_database.py
```

---

## Quick Start

### 1. Install dependencies

```powershell
cd C:\AutoTraderClaude\autotrader_claude
pip install -r requirements.txt
```

### 2. Configure environment

```powershell
copy .env.example .env
# Edit .env with your Supabase, Anthropic, Telegram, and email credentials
```

### 3. Set up Supabase (optional)

Run `database/schema.sql` in the Supabase SQL editor. If Supabase is not configured, the system automatically falls back to local JSON files in `C:\AutoTraderClaude\local_db\`.

### 4. Run a single backtest

```powershell
python main.py backtest --pair XAUUSD
```

### 5. Run the evolution loop

```powershell
python main.py evolve --iterations 100 --pairs XAUUSD
```

### 6. Start the dashboard

```powershell
python main.py dashboard --port 5000
# Open http://localhost:5000 in your browser
```

### 7. Run tests

```powershell
python main.py test
```

---

## ICT Strategy Rules

| Component | Rule |
|-----------|------|
| **Liquidity Sweep** | Price wicks beyond equal highs/lows and returns. Wick must be ≥ 30% of total candle range. |
| **Break of Structure** | Candle close (or wick) beyond the last swing high/low in sweep direction. Displacement candle ≥ 1.5× average range. |
| **Fair Value Gap** | 3-candle imbalance. Minimum size configurable (default 5 pips). Entry at 50% fill. |
| **Kill Zones** | London: 07:00–10:00 UTC. New York: 13:00–16:00 UTC. Bonus if in kill zone. |
| **Confidence** | Score 0–10. Minimum threshold: 7.0 (configurable). |
| **RRR** | Minimum 3:1 risk-reward ratio (configurable). |

---

## Evolution Parameters

| Parameter | Default | Range |
|-----------|---------|-------|
| `liquidity_sweep_lookback` | 20 | 10–50 |
| `liquidity_min_touches` | 2 | 2–5 |
| `liquidity_sweep_wick_pct` | 0.3 | 0.2–0.7 |
| `fvg_min_size_pips` | 5.0 | 2.0–15.0 |
| `confidence_threshold` | 7.0 | 5.0–9.0 |
| `min_rrr` | 3.0 | 2.0–5.0 |
| `bos_confirmation` | candle_close | candle_close / wick |

---

## Milestone Reports

| Milestone | Report |
|-----------|--------|
| Every 100 trades | Mini report (session breakdown, recent perf) |
| Every 1,000 trades | Evolution report (parameter analysis, top versions) |
| 10,000 trades | Final report (comprehensive, JSON + Markdown) |

Reports are saved to `C:\AutoTraderClaude\reports_output\`.

---

## Alerts

Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and SMTP credentials in `.env` to receive alerts for:
- Baseline backtest complete
- New best strategy found
- Overfitting warning
- Trade milestones (100 / 1,000 / 10,000)

---

## Risk Limits

- **Max daily drawdown**: 4%
- **Max total drawdown**: 10%
- **Default risk per trade**: 1%
- **Max open trades**: 3

---

## Data Sources

1. CSV files in `C:\AutoTraderClaude\data\` (e.g. `XAUUSD_H1.csv`)
2. yfinance (automatic download if CSV not found)
3. Synthetic OHLCV generation (fallback, realistic trend simulation)

---

*AutoTrader Claude — Evolutionary ICT Strategy System*
