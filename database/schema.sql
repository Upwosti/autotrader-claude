-- AutoTrader Claude — Supabase Schema
-- Run this in the Supabase SQL editor to initialise all tables.

-- ─── STRATEGY VERSIONS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_versions (
    id                      BIGSERIAL PRIMARY KEY,
    version                 INTEGER NOT NULL UNIQUE,
    params                  JSONB NOT NULL,
    win_rate                FLOAT,
    avg_rrr                 FLOAT,
    max_drawdown            FLOAT,
    total_trades            INTEGER DEFAULT 0,
    profitable_trades       INTEGER DEFAULT 0,
    notes                   TEXT,
    is_best                 BOOLEAN DEFAULT FALSE,
    overfitting_flag        BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_versions_version ON strategy_versions(version);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_is_best ON strategy_versions(is_best);

-- ─── BACKTEST RUNS ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_runs (
    id                      BIGSERIAL PRIMARY KEY,
    strategy_version        INTEGER REFERENCES strategy_versions(version),
    pair                    VARCHAR(20) NOT NULL,
    timeframe               VARCHAR(10) NOT NULL,
    start_date              DATE NOT NULL,
    end_date                DATE NOT NULL,
    initial_capital         FLOAT NOT NULL,
    final_capital           FLOAT,
    total_return_pct        FLOAT,
    win_rate                FLOAT,
    total_trades            INTEGER,
    winning_trades          INTEGER,
    losing_trades           INTEGER,
    avg_rrr                 FLOAT,
    max_drawdown_pct        FLOAT,
    sharpe_ratio            FLOAT,
    sortino_ratio           FLOAT,
    profit_factor           FLOAT,
    report_path             TEXT,
    overfitting_flag        BOOLEAN DEFAULT FALSE,
    small_sample_flag       BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_version ON backtest_runs(strategy_version);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_pair ON backtest_runs(pair);

-- ─── INDIVIDUAL TRADES ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id                      BIGSERIAL PRIMARY KEY,
    strategy_version        INTEGER REFERENCES strategy_versions(version),
    backtest_run_id         BIGINT REFERENCES backtest_runs(id),
    pair                    VARCHAR(20) NOT NULL,
    direction               VARCHAR(5) NOT NULL,   -- 'long' | 'short'
    entry_time              TIMESTAMPTZ NOT NULL,
    exit_time               TIMESTAMPTZ,
    entry_price             FLOAT NOT NULL,
    exit_price              FLOAT,
    stop_loss               FLOAT NOT NULL,
    take_profit             FLOAT NOT NULL,
    risk_pct                FLOAT NOT NULL,
    rrr_achieved            FLOAT,
    pnl_pips                FLOAT,
    pnl_pct                 FLOAT,
    outcome                 VARCHAR(10),            -- 'win' | 'loss' | 'open' | 'break_even'
    session                 VARCHAR(20),            -- 'london' | 'ny' | 'asia'
    confidence_score        FLOAT,
    liquidity_swept         BOOLEAN DEFAULT FALSE,
    bos_confirmed           BOOLEAN DEFAULT FALSE,
    fvg_used                BOOLEAN DEFAULT FALSE,
    displacement_present    BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_version ON trades(strategy_version);
CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);
CREATE INDEX IF NOT EXISTS idx_trades_outcome ON trades(outcome);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);

-- ─── EVOLUTION LOG ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evolution_log (
    id                      BIGSERIAL PRIMARY KEY,
    iteration               INTEGER NOT NULL,
    from_version            INTEGER,
    to_version              INTEGER,
    change_type             VARCHAR(50),  -- 'mutation' | 'revert' | 'initial'
    param_changed           VARCHAR(100),
    old_value               TEXT,
    new_value               TEXT,
    win_rate_before         FLOAT,
    win_rate_after          FLOAT,
    decision                VARCHAR(20),  -- 'kept' | 'reverted'
    reasoning               TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evolution_log_iteration ON evolution_log(iteration);

-- ─── ALERTS LOG ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts_log (
    id                      BIGSERIAL PRIMARY KEY,
    alert_type              VARCHAR(50) NOT NULL,
    channel                 VARCHAR(20) NOT NULL,  -- 'telegram' | 'email'
    subject                 TEXT,
    body                    TEXT,
    sent_at                 TIMESTAMPTZ DEFAULT NOW(),
    success                 BOOLEAN DEFAULT TRUE,
    error_msg               TEXT
);

-- ─── MILESTONE REPORTS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS milestone_reports (
    id                      BIGSERIAL PRIMARY KEY,
    report_type             VARCHAR(20) NOT NULL,  -- 'mini' | 'evolution' | 'final'
    trade_count             INTEGER NOT NULL,
    strategy_version        INTEGER REFERENCES strategy_versions(version),
    win_rate                FLOAT,
    avg_rrr                 FLOAT,
    max_drawdown_pct        FLOAT,
    total_return_pct        FLOAT,
    best_pair               VARCHAR(20),
    best_session            VARCHAR(20),
    notes                   TEXT,
    report_json             JSONB,
    report_path             TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ─── VERSION SNAPSHOTS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS version_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    iteration               INTEGER NOT NULL,
    strategy_version        INTEGER,
    params_json             JSONB,
    win_rate                FLOAT,
    avg_rrr                 FLOAT,
    max_drawdown            FLOAT,
    total_trades            INTEGER,
    total_return_pct        FLOAT,
    overfitting_flag        BOOLEAN DEFAULT FALSE,
    snapshotted_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_version_snapshots_iteration ON version_snapshots(iteration);

-- ─── SYSTEM STATE ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_state (
    id                      BIGSERIAL PRIMARY KEY,
    key                     VARCHAR(100) UNIQUE NOT NULL,
    value                   TEXT NOT NULL,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Seed initial state
INSERT INTO system_state (key, value) VALUES
    ('current_version', '1'),
    ('total_trades', '0'),
    ('current_iteration', '1'),
    ('status', 'initialised')
ON CONFLICT (key) DO NOTHING;
