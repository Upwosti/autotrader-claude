# AutoTrader Claude

Automated trading bot powered by Claude AI, using ICT (Inner Circle Trader) strategy with evolutionary parameter optimization.

## Setup
- Python 3.11
- pandas, numpy, flask, anthropic, supabase
- Telegram notifications via python-telegram-bot
- GitHub integration via PyGithub

## Structure
- `autotrader_claude/main.py` - Entry point (evolve / backtest / dashboard / test)
- `autotrader_claude/strategy/` - ICT signal generation
- `autotrader_claude/backtester/` - Walk-forward backtesting engine
- `autotrader_claude/evolution/` - Evolutionary parameter optimization
- `autotrader_claude/database/` - Supabase + local JSON fallback
- `autotrader_claude/alerts/` - Telegram and email notifications
- `autotrader_claude/dashboard/` - Flask web dashboard
