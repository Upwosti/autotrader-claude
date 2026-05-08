# Supabase Setup Guide for AutoTrader Claude

## Step 1 — Create a Supabase Project

1. Go to https://supabase.com and sign in (or sign up)
2. Click **"New project"**
3. Choose your organisation, enter:
   - **Name**: `autotrader-claude`
   - **Database Password**: (save this — you won't need it directly but keep it)
   - **Region**: pick the closest to your VPS
4. Click **"Create new project"** and wait ~2 minutes for provisioning

---

## Step 2 — Get Your API Keys

1. In your project dashboard, click **Settings → API**
2. Copy:
   - **Project URL** → `https://xxxxx.supabase.co`
   - **anon / public key** → long JWT string starting with `eyJ...`

---

## Step 3 — Create the Tables

1. In your project dashboard, click **SQL Editor → New query**
2. Paste the full contents of `database/schema.sql`
3. Click **Run** (green button)
4. You should see: *"Success. No rows returned."*
5. Verify tables in **Table Editor** — you should see:
   - `strategy_versions`
   - `backtest_runs`
   - `trades`
   - `evolution_log`
   - `alerts_log`
   - `milestone_reports`
   - `version_snapshots`
   - `system_state`

---

## Step 4 — Configure Environment Variables

Edit `C:\AutoTraderClaude\autotrader_claude\.env`:

```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

## Step 5 — Test Connection

```powershell
cd C:\AutoTraderClaude\autotrader_claude
py -c "
import sys; sys.path.insert(0,'.')
from database.supabase_client import SupabaseClient
db = SupabaseClient()
print('Online:', db.online)
print('Version:', db.get_current_version())
print('Trades:', db.get_total_trades())
"
```

Expected output:
```
Supabase connected
Online: True
Version: 1
Trades: 0
```

---

## Step 6 — Row Level Security (Optional but Recommended)

By default Supabase tables use RLS disabled for `anon` key access.  
For a private project just using the service role key is fine.  
If you switch to the `service_role` key (Settings → API → service_role):

```env
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...  # service_role key
```

The service role key bypasses RLS — recommended for server-side automation.

---

## Fallback: Local JSON Mode

If Supabase is not configured or unavailable, all data is automatically saved to:
```
C:\AutoTraderClaude\local_db\
  ├── strategy_versions.json
  ├── backtest_runs.json
  ├── trades.json
  ├── evolution_log.json
  ├── version_snapshots.json
  └── system_state.json
```

No configuration needed — the system detects missing credentials and falls back automatically.
