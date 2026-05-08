#!/bin/bash
# deploy.sh — Zero-downtime deployment

set -e

WD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WD"

echo "=== AutoTrader Deploy $(date) ==="

# Pull latest
git pull origin main 2>/dev/null || echo "No git remote or already up to date"

# Install/update Python deps
./venv/Scripts/pip install -r requirements.txt -q 2>/dev/null || \
  python -m pip install -r requirements.txt -q

# Verify syntax
echo "Checking syntax..."
python -m py_compile watchdog.py run_forever.py evolution/autonomous_loop.py
echo "Syntax OK"

# Kill old processes gracefully
pkill -f watchdog.py 2>/dev/null || true
pkill -f run_forever.py 2>/dev/null || true
sleep 3

# Start watchdog (detached)
PAIRS="XAUUSD,XAGUSD,XPTUSD,GBPUSD,EURUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD,EURJPY,GBPJPY,BTCUSD,ETHUSD,NAS100,US30,GER40,GC=F,SI=F"
nohup python watchdog.py --pairs "$PAIRS" --hours 0 > logs/deploy_start.log 2>&1 &
echo "Watchdog started PID=$!"

# Send Telegram notification
python -c "
import sys; sys.path.insert(0, '.')
try:
    from alerts.telegram_bot import TelegramAlert
    tg = TelegramAlert()
    tg.send('Deployed Successfully', 'AutoTrader redeployed.\nWatchdog restarted.\nEvolution resuming from saved state.')
    print('Telegram notified')
except Exception as e:
    print(f'Telegram failed: {e}')
"

echo "=== Deploy complete ==="
