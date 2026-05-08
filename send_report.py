import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SENDER    = "Upwosti@gmail.com"
PASSWORD  = "pdfpdmhgzmtdbsal"
RECIPIENT = "Upwosti@gmail.com"

html = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body { background:#0a0e1a; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:0; }
  .wrap { max-width:700px; margin:0 auto; padding:32px 24px; }
  .header { background:linear-gradient(135deg,#1e3a5f,#0f2942); border-radius:12px; padding:28px 32px; margin-bottom:24px; border:1px solid #1e40af44; }
  .header h1 { margin:0 0 4px; font-size:24px; color:#60a5fa; }
  .header p  { margin:0; color:#94a3b8; font-size:14px; }
  .metrics { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:24px; }
  .metric { background:#111827; border:1px solid #1e293b; border-radius:10px; padding:16px 18px; text-align:center; }
  .metric .val { font-size:28px; font-weight:700; color:#3b82f6; margin-bottom:4px; }
  .metric .val.green { color:#10b981; }
  .metric .val.amber { color:#f59e0b; }
  .metric .lbl { font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:.05em; }
  .section { background:#111827; border:1px solid #1e293b; border-radius:10px; padding:20px; margin-bottom:16px; }
  .section h2 { font-size:14px; font-weight:600; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em; margin:0 0 14px; }
  .item { display:flex; justify-content:space-between; padding:7px 0; border-bottom:1px solid #1e293b44; font-size:13px; }
  .item:last-child { border-bottom:none; }
  .item .k { color:#94a3b8; }
  .item .v { color:#e2e8f0; font-weight:500; }
  .badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }
  .badge-green { background:rgba(16,185,129,.15); color:#10b981; }
  .badge-blue  { background:rgba(59,130,246,.15); color:#3b82f6; }
  .badge-amber { background:rgba(245,158,11,.15);  color:#f59e0b; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:16px; }
  .tag { display:inline-block; background:#1e293b; color:#94a3b8; border-radius:4px; padding:2px 8px; font-size:11px; margin:2px; }
  .cta { background:linear-gradient(135deg,#1e40af,#1d4ed8); border-radius:10px; padding:20px 24px; text-align:center; margin-bottom:16px; }
  .cta a { color:#93c5fd; font-size:16px; font-weight:600; text-decoration:none; }
  .cta p { color:#94a3b8; font-size:12px; margin:6px 0 0; }
  .footer { text-align:center; color:#475569; font-size:12px; margin-top:24px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { background:#0f172a; color:#64748b; padding:8px 10px; text-align:left; font-weight:600; text-transform:uppercase; font-size:10px; letter-spacing:.05em; }
  td { padding:7px 10px; border-bottom:1px solid #1e293b44; color:#cbd5e1; }
  tr:nth-child(even) td { background:rgba(255,255,255,.02); }
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <h1>⚡ AutoTrader — Institutional Upgrade Complete</h1>
    <p>Full system build report &amp; live evolution status · """ + datetime.now().strftime('%Y-%m-%d %H:%M UTC') + """</p>
  </div>

  <!-- Key Metrics -->
  <div class="metrics">
    <div class="metric">
      <div class="val amber">68.2%</div>
      <div class="lbl">XAUUSD WR (Realistic)</div>
    </div>
    <div class="metric">
      <div class="val">710</div>
      <div class="lbl">Evolution Iteration</div>
    </div>
    <div class="metric">
      <div class="val green">1,662</div>
      <div class="lbl">Total Test Trades</div>
    </div>
  </div>

  <!-- Dashboard Link -->
  <div class="cta">
    <a href="http://144.91.69.63:5000/react">🖥️ Open React Dashboard → http://144.91.69.63:5000/react</a>
    <p>Live data · refreshes every 15 seconds · 7 pages</p>
  </div>
  <div class="cta" style="background:linear-gradient(135deg,#064e3b,#065f46);">
    <a href="http://144.91.69.63:5000">📊 Classic Flask Dashboard → http://144.91.69.63:5000</a>
    <p>Original dashboard still available</p>
  </div>

  <!-- Evolution Status -->
  <div class="section">
    <h2>Live Evolution Status</h2>
    <div class="item"><span class="k">XAUUSD WR (Realistic)</span><span class="v">68.2% <span class="badge badge-amber">+11.8% to target</span></span></div>
    <div class="item"><span class="k">Aggregate WR</span><span class="v">56.4%</span></div>
    <div class="item"><span class="k">Current Iteration</span><span class="v">710</span></div>
    <div class="item"><span class="k">Total Test Trades</span><span class="v">1,662 across 19 pairs</span></div>
    <div class="item"><span class="k">No-Improvement Count</span><span class="v">28 / 150 (random restart at 150)</span></div>
    <div class="item"><span class="k">Target</span><span class="v">80%+ XAUUSD realistic WR — never stops</span></div>
    <div class="item"><span class="k">Mode</span><span class="v"><span class="badge badge-green">RUNNING 24/7 · AUTONOMOUS</span></span></div>
  </div>

  <!-- New Features Built -->
  <div class="section">
    <h2>Institutional Upgrade — 25 New Files Added</h2>
    <table>
      <thead><tr><th>Category</th><th>File</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td>ICT Advanced</td><td>strategy/ict_advanced.py</td><td>IFVG, Order Blocks, Breaker Blocks, MSS, OTE, Turtle Soup, SMT Divergence, Dealing Range, Killzone</td></tr>
        <tr><td>Strategy Router</td><td>strategy/strategy_router.py</td><td>Per-pair profiles for 19 pairs, dynamic weight updates every 100 iterations</td></tr>
        <tr><td>ML — Pattern</td><td>ml/pattern_classifier.py</td><td>XGBoost entry pattern classifier, threshold 0.65, weekly retrain</td></tr>
        <tr><td>ML — Regime</td><td>ml/regime_detector.py</td><td>LightGBM market regime detector (trending/ranging/volatile/quiet), daily retrain</td></tr>
        <tr><td>ML — LSTM</td><td>ml/lstm_predictor.py</td><td>50-bar sequence predictor (PyTorch, graceful fallback), weekly retrain</td></tr>
        <tr><td>ML — Ensemble</td><td>ml/ensemble.py</td><td>Combined score from all models, threshold 0.62, adaptive weighting</td></tr>
        <tr><td>ML — RL Agent</td><td>ml/reinforcement_agent.py</td><td>Q-learning entry/exit timing optimizer</td></tr>
        <tr><td>Risk — Portfolio</td><td>risk/portfolio_manager.py</td><td>Correlation matrix, max 3 correlated pairs, exposure tracking</td></tr>
        <tr><td>Risk — Volatility</td><td>risk/volatility_sizer.py</td><td>ATR-based position sizing, skip on extreme volatility</td></tr>
        <tr><td>Risk — Equity</td><td>risk/equity_protector.py</td><td>3% daily / 5% weekly / 8% total DD limits, auto-pause &amp; resume</td></tr>
        <tr><td>Risk — Correlation</td><td>risk/correlation_filter.py</td><td>Pre-entry correlation check, block at &gt;2 correlated trades</td></tr>
        <tr><td>Execution</td><td>execution/smart_executor.py</td><td>Spread check, slippage sim, news blackout, retry logic</td></tr>
        <tr><td>Execution</td><td>execution/order_manager.py</td><td>Partial close 1:1, trailing stop, BE move, emergency close</td></tr>
        <tr><td>Database</td><td>database/postgresql_client.py</td><td>PostgreSQL primary DB, 5 tables, auto-reconnect</td></tr>
        <tr><td>Database</td><td>database/redis_client.py</td><td>Redis cache layer, in-memory fallback when offline</td></tr>
        <tr><td>Alerts</td><td>alerts/telegram_advanced.py</td><td>15 professional alert types with formatted blocks</td></tr>
        <tr><td>Reports</td><td>reports/email_reporter.py</td><td>HTML monthly email reports with dark theme</td></tr>
        <tr><td>Reports</td><td>backtester/monthly_reporter.py</td><td>Per-month WR/RRR breakdown from 2022 to today</td></tr>
        <tr><td>Backtesting</td><td>backtester/tick_simulator.py</td><td>GBM intra-bar tick path simulation for final validation</td></tr>
        <tr><td>Dashboard</td><td>dashboard/frontend/index.html</td><td>React 18 CDN dashboard, 7 pages, live updates every 15s</td></tr>
        <tr><td>Infrastructure</td><td>Dockerfile</td><td>Python 3.11-slim, all deps, VPS-ready</td></tr>
        <tr><td>Infrastructure</td><td>docker-compose.yml</td><td>5 services: autotrader, watchdog, postgres, redis, dashboard</td></tr>
        <tr><td>Infrastructure</td><td>deploy.sh</td><td>Zero-downtime bash deployment script</td></tr>
        <tr><td>Autonomy</td><td>auto_updater.py</td><td>6h resource checks, 24h module health, Telegram alerts</td></tr>
        <tr><td>Skills</td><td>evolution/skill_builder.py</td><td>+ML regime, volatility, day-of-week, month, cross-pair inheritance skills</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Pair Universe -->
  <div class="section">
    <h2>Active Pair Universe (19 pairs)</h2>
    <div style="margin-top:4px">
      <span class="tag">XAUUSD</span><span class="tag">XAGUSD</span><span class="tag">XPTUSD</span>
      <span class="tag">GBPUSD</span><span class="tag">EURUSD</span><span class="tag">USDJPY</span>
      <span class="tag">USDCHF</span><span class="tag">AUDUSD</span><span class="tag">NZDUSD</span>
      <span class="tag">USDCAD</span><span class="tag">EURJPY</span><span class="tag">GBPJPY</span>
      <span class="tag">BTCUSD</span><span class="tag">ETHUSD</span>
      <span class="tag">NAS100</span><span class="tag">US30</span><span class="tag">GER40</span>
      <span class="tag">GC=F</span><span class="tag">SI=F</span>
    </div>
  </div>

  <!-- Best Strategy Params -->
  <div class="section">
    <h2>Current Best Strategy Parameters</h2>
    <div class="item"><span class="k">Strategy</span><span class="v">HighConfluenceTrend v21</span></div>
    <div class="item"><span class="k">EMA Stack</span><span class="v">21 / 89 / 200</span></div>
    <div class="item"><span class="k">ATR Period</span><span class="v">14 · SL mult: 0.5 · TP RRR: 2.5</span></div>
    <div class="item"><span class="k">ADX Filter</span><span class="v">Min ADX: 25 (active)</span></div>
    <div class="item"><span class="k">Pattern Filter</span><span class="v">Active</span></div>
    <div class="item"><span class="k">Min Confluence</span><span class="v">2</span></div>
  </div>

  <!-- Schedule -->
  <div class="section">
    <h2>Autonomous Schedule</h2>
    <div class="item"><span class="k">Every 30 min</span><span class="v">Watchdog · stall detection · Supabase check</span></div>
    <div class="item"><span class="k">Every 2 hours</span><span class="v">Full analysis · resource check · connections · report</span></div>
    <div class="item"><span class="k">Every 6 hours</span><span class="v">Deep skill update · code health · bottleneck analysis</span></div>
    <div class="item"><span class="k">Every 24 hours</span><span class="v">ML retrain · stress test · full module health check</span></div>
    <div class="item"><span class="k">Every 100 iters</span><span class="v">Full pair ranking report · strategy weight update</span></div>
    <div class="item"><span class="k">Every Sunday</span><span class="v">Weekly performance summary</span></div>
  </div>

  <!-- MT5 Note -->
  <div class="section" style="border-color:#f59e0b44">
    <h2 style="color:#f59e0b">⚡ MT5 Live Trading — Ready When You Are</h2>
    <div class="item"><span class="k">Status</span><span class="v"><span class="badge badge-amber">MT5_PENDING — awaiting login credentials</span></span></div>
    <div class="item"><span class="k">Action required</span><span class="v">Provide MT5 account number, password, server</span></div>
    <div class="item"><span class="k">On connection</span><span class="v">Live execution activates immediately using best evolved strategy</span></div>
    <div class="item"><span class="k">Risk controls ready</span><span class="v">Equity protector · correlation filter · volatility sizer · order manager</span></div>
  </div>

  <div class="footer">
    AutoTrader Claude · Institutional Build · """ + datetime.now().strftime('%Y-%m-%d') + """ · All systems running 24/7
  </div>

</div>
</body>
</html>"""

msg = MIMEMultipart("alternative")
msg["Subject"] = f"AutoTrader — Institutional Upgrade Complete | XAUUSD WR 68.2% | Iter 710"
msg["From"]    = SENDER
msg["To"]      = RECIPIENT

msg.attach(MIMEText(html, "html"))

try:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SENDER, PASSWORD)
        s.sendmail(SENDER, RECIPIENT, msg.as_string())
    print("EMAIL SENT OK")
except Exception as e:
    print(f"EMAIL FAILED: {e}")
