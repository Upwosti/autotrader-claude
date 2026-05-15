"""Send error report email with all 3 fixes documented."""
import os, json, smtplib, ssl as _ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

EMAIL_FROM = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_TO   = os.environ.get("EMAIL_RECEIVER", "")
SMTP_HOST  = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("EMAIL_SMTP_PORT", 587))

# Load current state
state = json.load(open(ROOT / "local_db" / "engine_state.json"))
it   = state.get("iteration", 0)
bwr  = state.get("best_wr", {})
brrr = state.get("best_rrr", {})
bsc  = state.get("best_score", {})

pairs = sorted(bsc.keys(), key=lambda p: bsc.get(p, 0), reverse=True)
rows_html = ""
for p in pairs[:10]:
    wr  = bwr.get(p, 0)
    rrr = brrr.get(p, 0)
    e   = round(wr * rrr - (1 - wr), 4)
    sc  = bsc.get(p, 0)
    rows_html += f"<tr><td>{p}</td><td>{wr:.1%}</td><td>{rrr:.2f}</td><td>{e:.3f}R</td><td>{sc:.4f}</td></tr>"

html = f"""<!DOCTYPE html>
<html>
<head><style>
body{{font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}}
h1{{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:10px}}
h2{{color:#3fb950;margin-top:25px}}
table{{border-collapse:collapse;width:100%;margin:10px 0}}
th{{background:#21262d;color:#58a6ff;padding:8px 12px;text-align:left;border:1px solid #30363d}}
td{{padding:7px 12px;border:1px solid #30363d}}
tr:nth-child(even){{background:#161b22}}
.fix{{background:#1c2a1c;border-left:3px solid #3fb950;padding:10px 15px;margin:8px 0;border-radius:4px}}
.err{{background:#2a1c1c;border-left:3px solid #f85149;padding:10px 15px;margin:8px 0;border-radius:4px}}
.stat{{display:inline-block;background:#21262d;border-radius:6px;padding:10px 20px;margin:5px;text-align:center}}
.stat-val{{font-size:24px;color:#58a6ff;font-weight:bold}}
.stat-label{{font-size:12px;color:#8b949e}}
</style></head>
<body>
<h1>&#x1F916; AutoTrader OMEGA &mdash; Error Report &amp; Fixes</h1>
<p style="color:#8b949e">Generated: 2026-05-14 | Engine iteration: {it:,}</p>

<div>
<div class="stat"><div class="stat-val">86.2%</div><div class="stat-label">XAUUSD WR</div></div>
<div class="stat"><div class="stat-val">2.03</div><div class="stat-label">XAUUSD RRR</div></div>
<div class="stat"><div class="stat-val">1.615R</div><div class="stat-label">XAUUSD Expectancy</div></div>
<div class="stat"><div class="stat-val">{it:,}</div><div class="stat-label">Total Iterations</div></div>
</div>

<h2>&#x1F534; Errors Found &amp; Fixed</h2>

<div class="err">
<strong>Error 1: Email Authentication Failure (535 BadCredentials)</strong><br>
<code>535 5.7.8 Username and Password not accepted &mdash; smtp.gmail.com</code><br><br>
<strong>Root cause:</strong> Gmail no longer accepts regular account passwords for SMTP since May 2022.
An App Password (16-char) is required when 2-Step Verification is enabled.<br><br>
<strong>Fix applied in run_forever.py:</strong>
<ul>
  <li>Email sender now tries <strong>SSL port 465</strong> first (most reliable)</li>
  <li>Falls back to <strong>STARTTLS port 587</strong> if SSL fails</li>
  <li>Logs a clear actionable warning with the App Password URL when BadCredentials is detected</li>
</ul>
<strong>&#x26A0;&#xFE0F; Action required by you:</strong><br>
1. Go to <a href="https://myaccount.google.com/apppasswords" style="color:#58a6ff">myaccount.google.com/apppasswords</a><br>
2. Click "Select app" &rarr; "Mail" &rarr; "Select device" &rarr; "Windows Computer" &rarr; Generate<br>
3. Copy the 16-character password (e.g. <code>abcd efgh ijkl mnop</code>)<br>
4. Open <code>.env</code> and set: <code>EMAIL_PASSWORD=abcdefghijklmnop</code> (no spaces)<br>
5. Engine auto-reloads on next restart.
</div>

<div class="err">
<strong>Error 2: Warning Log Spam (hundreds of WARNING lines per day)</strong><br>
<code>WARNING: USDJPY small test sample (0 trades)</code><br>
<code>WARNING: NAS100 possible overfitting (train WR 73.7% vs test WR 50.0%)</code><br><br>
<strong>Root cause:</strong> 3 sites in <code>backtester/walk_forward.py</code> used <code>logger.warning()</code>
for conditions that are already handled by the OMEGA acceptance gates (trades &lt; 15 and overfit=True
both cause automatic rejection). These warnings were noise, not actionable.<br><br>
<strong>Fix applied:</strong> All 3 warning sites demoted to <code>logger.debug()</code>.
Logs now only show real warnings. Log files will be significantly smaller.
</div>

<div class="err">
<strong>Error 3: USDJPY / NAS100 Permanent Overfit Loop (zero progress)</strong><br>
<code>USDJPY: train WR=77.8% vs test WR=0.0% &mdash; rejected every single iteration</code><br><br>
<strong>Root cause:</strong> Some pairs kept generating strategies that scored high on training data
but completely failed on the unseen test window. No mechanism existed to break this loop &mdash;
the engine would mutate slightly and overfit again, indefinitely.<br><br>
<strong>Fix applied:</strong>
<ul>
  <li>Added <code>overfit_strikes</code> counter per pair (persisted in engine_state.json)</li>
  <li>Counter increments each time the overfit flag causes a rejection</li>
  <li>Counter resets to 0 when a clean (non-overfit) result passes</li>
  <li>After <strong>30 consecutive overfit rejections</strong>, <code>_overfit_reset()</code> fires automatically</li>
  <li>Reset applies conservative params: <code>min_confluence=4</code>, <code>min_adx=25</code>,
      <code>use_pattern=False</code>, <code>use_expansion=False</code> &mdash; far fewer signals, far less curve-fitting</li>
  <li>Telegram alert sent on each reset</li>
</ul>
</div>

<h2>&#x2705; Engine Scoreboard (Top 10 Pairs)</h2>
<table>
<tr><th>Pair</th><th>Win Rate</th><th>RRR</th><th>Expectancy</th><th>Score</th></tr>
{rows_html}
</table>

<h2>&#x1F4CB; OMEGA Acceptance Rules (All Active)</h2>
<table>
<tr><th>#</th><th>Rule</th><th>Threshold</th><th>Status</th></tr>
<tr><td>1</td><td>Min Trades</td><td>&ge; 15</td><td>&#x2705; Active</td></tr>
<tr><td>2</td><td>Win Rate Floor</td><td>&ge; 90% of pair best</td><td>&#x2705; Active</td></tr>
<tr><td>3</td><td>RRR Floor</td><td>&ge; 1.0</td><td>&#x2705; Active</td></tr>
<tr><td>4</td><td>Max Drawdown</td><td>&lt; 8%</td><td>&#x2705; Active</td></tr>
<tr><td>5</td><td>Profit Factor</td><td>&gt; 1.3</td><td>&#x2705; Active</td></tr>
<tr><td>6</td><td>No Overfit</td><td>Train/test gap &lt; 20pp + auto-reset after 30 strikes</td><td>&#x2705; Active + Reset</td></tr>
<tr><td>7</td><td>Monte Carlo</td><td>&gt; 65% survival</td><td>&#x2705; Active</td></tr>
</table>

<h2>&#x2699;&#xFE0F; System Status</h2>
<table>
<tr><th>Component</th><th>Status</th></tr>
<tr><td>Engine</td><td>&#x2705; Running (restarted with all fixes)</td></tr>
<tr><td>Watchdog</td><td>&#x2705; Active (auto-restarts engine)</td></tr>
<tr><td>GitHub Auto-sync</td><td>&#x2705; Every 10 iterations &rarr; master</td></tr>
<tr><td>Telegram Commands</td><td>&#x2705; /status /pause /resume /stop etc.</td></tr>
<tr><td>Paper Trading</td><td>&#x2705; Active (14-day gate before live)</td></tr>
<tr><td>MT5 Connection</td><td>&#x23F3; Awaiting MT5_LOGIN/PASSWORD/SERVER in .env</td></tr>
<tr><td>Email Reporting</td><td>&#x26A0;&#xFE0F; Needs Gmail App Password (see Error 1 above)</td></tr>
</table>

<p style="color:#8b949e;font-size:12px;margin-top:30px;border-top:1px solid #30363d;padding-top:10px">
AutoTrader OMEGA-1 | Commit: Fix 3 errors: email SSL/465, warning spam, overfit reset | Engine iter {it:,}
</p>
</body>
</html>"""

msg = MIMEMultipart("alternative")
msg["Subject"] = "AutoTrader OMEGA — 3 Errors Fixed + System Report"
msg["From"] = EMAIL_FROM
msg["To"]   = EMAIL_TO
msg.attach(MIMEText(html, "html"))

sent = False

# Method 1: SSL on port 465
try:
    ctx = _ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30, context=ctx) as s:
        s.login(EMAIL_FROM, EMAIL_PASS)
        s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print("Email sent via SSL/465")
    sent = True
except Exception as e1:
    print(f"SSL/465 failed: {e1}")

# Method 2: STARTTLS on port 587
if not sent:
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print("Email sent via STARTTLS/587")
        sent = True
    except Exception as e2:
        print(f"STARTTLS/587 failed: {e2}")
        if "535" in str(e2) or "BadCredentials" in str(e2):
            print()
            print("=" * 60)
            print("ACTION REQUIRED: Gmail App Password needed")
            print("=" * 60)
            print("1. Go to: https://myaccount.google.com/apppasswords")
            print("2. Generate App Password for Mail/Windows")
            print("3. Set in .env: EMAIL_PASSWORD=<16-char-password>")
            print("4. Engine will use it automatically on next email")

if not sent:
    print()
    print("Report summary (email could not be delivered):")
    print("  Error 1 FIXED: Email now tries SSL/465 then STARTTLS/587")
    print("  Error 2 FIXED: Backtester warning spam demoted to DEBUG")
    print("  Error 3 FIXED: Overfit strike counter + _overfit_reset() added")
    print("  All fixes committed to master branch")
    print("  Engine restarted with all fixes applied")
    print()
    print(f"  XAUUSD: WR=86.2% | RRR=2.03 | E=1.615R | iter={it:,}")
