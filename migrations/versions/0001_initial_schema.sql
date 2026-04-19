-- Migration 0001: Initial schema baseline
-- Captures the complete schema from marketplace/db.py SCHEMA_SQL as of v0.6.1 (2026-03-24)
-- This is a baseline migration — existing databases should mark it as applied
-- without executing (all tables use IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    endpoint TEXT NOT NULL,
    price_per_call REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    payment_method TEXT DEFAULT 'x402',
    free_tier_calls INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    category TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    hashed_secret TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'buyer',
    rate_limit INTEGER DEFAULT 60,
    wallet_address TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    latency_ms INTEGER DEFAULT 0,
    status_code INTEGER DEFAULT 200,
    amount_usd REAL DEFAULT 0,
    payment_method TEXT DEFAULT 'x402',
    payment_tx TEXT
);

CREATE TABLE IF NOT EXISTS settlements (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    total_amount REAL DEFAULT 0,
    platform_fee REAL DEFAULT 0,
    net_amount REAL DEFAULT 0,
    payment_tx TEXT,
    status TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_usage_buyer
    ON usage_records(buyer_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_service
    ON usage_records(service_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_services_status
    ON services(status, category);
CREATE INDEX IF NOT EXISTS idx_keys_owner
    ON api_keys(owner_id);

-- Agent Identity
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    identity_type TEXT DEFAULT 'api_key_only',
    capabilities TEXT DEFAULT '[]',
    wallet_address TEXT,
    verified INTEGER DEFAULT 0,
    reputation_score REAL DEFAULT 0.0,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agents_owner
    ON agent_identities(owner_id);
CREATE INDEX IF NOT EXISTS idx_agents_status
    ON agent_identities(status);

-- Reputation Records
CREATE TABLE IF NOT EXISTS reputation_records (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    overall_score REAL DEFAULT 0.0,
    latency_score REAL DEFAULT 0.0,
    reliability_score REAL DEFAULT 0.0,
    response_quality REAL DEFAULT 0.0,
    call_count INTEGER DEFAULT 0,
    period TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reputation_agent
    ON reputation_records(agent_id, period);
CREATE INDEX IF NOT EXISTS idx_reputation_service
    ON reputation_records(service_id, period);

-- Teams
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    description TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_teams_owner
    ON teams(owner_id);

-- Team Members
CREATE TABLE IF NOT EXISTS team_members (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT DEFAULT 'worker',
    skills TEXT DEFAULT '[]',
    joined_at TEXT NOT NULL,
    UNIQUE(team_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_members_team
    ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_members_agent
    ON team_members(agent_id);

-- Routing Rules
CREATE TABLE IF NOT EXISTS routing_rules (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    keywords TEXT DEFAULT '[]',
    target_agent_id TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routing_team
    ON routing_rules(team_id, priority);

-- Quality Gates
CREATE TABLE IF NOT EXISTS quality_gates (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    gate_order INTEGER DEFAULT 0,
    config TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gates_team
    ON quality_gates(team_id, gate_order);

-- Webhooks
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    url TEXT NOT NULL,
    events TEXT DEFAULT '[]',
    secret TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhooks_owner
    ON webhooks(owner_id);

-- Buyer Balances (pre-paid credit system)
CREATE TABLE IF NOT EXISTS balances (
    buyer_id TEXT PRIMARY KEY,
    balance REAL NOT NULL DEFAULT 0,
    total_deposited REAL NOT NULL DEFAULT 0,
    total_spent REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Deposit records (IPN-confirmed only)
CREATE TABLE IF NOT EXISTS deposits (
    id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    payment_provider TEXT NOT NULL,
    payment_id TEXT,
    payment_status TEXT DEFAULT 'pending',
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deposits_buyer
    ON deposits(buyer_id, payment_status);
CREATE INDEX IF NOT EXISTS idx_deposits_payment
    ON deposits(payment_id);

-- Founding Sellers (first 50 permanent badges)
CREATE TABLE IF NOT EXISTS founding_sellers (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL UNIQUE,
    sequence_number INTEGER NOT NULL UNIQUE,
    badge_tier TEXT DEFAULT 'founding',
    commission_rate REAL DEFAULT 0.08,
    awarded_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_founding_provider
    ON founding_sellers(provider_id);
CREATE INDEX IF NOT EXISTS idx_founding_sequence
    ON founding_sellers(sequence_number);

-- Email subscribers (download gate)
CREATE TABLE IF NOT EXISTS subscribers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'starter-kit',
    subscribed_at TEXT NOT NULL,
    confirmed INTEGER DEFAULT 0,
    drip_stage INTEGER DEFAULT 0,
    drip_next_at TEXT,
    unsubscribed INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_subscribers_email
    ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_subscribers_drip
    ON subscribers(drip_next_at, unsubscribed);

-- Agent Providers (agents registered as service providers)
CREATE TABLE IF NOT EXISTS agent_providers (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL UNIQUE,
    owner_email TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    did TEXT NOT NULL,
    declaration TEXT NOT NULL,
    status TEXT DEFAULT 'pending_review',
    reputation_score REAL DEFAULT 0.0,
    fast_track_eligible INTEGER DEFAULT 0,
    daily_tx_cap REAL DEFAULT 500.0,
    daily_tx_used REAL DEFAULT 0.0,
    daily_tx_reset_at TEXT,
    probation_ends_at TEXT,
    total_reports INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agent_providers_agent
    ON agent_providers(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_providers_owner
    ON agent_providers(owner_email);
CREATE INDEX IF NOT EXISTS idx_agent_providers_status
    ON agent_providers(status);

-- Service Reviews (automated quality checks for new listings)
CREATE TABLE IF NOT EXISTS service_reviews (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    review_type TEXT DEFAULT 'automated',
    status TEXT DEFAULT 'pending',
    endpoint_reachable INTEGER DEFAULT 0,
    response_format_valid INTEGER DEFAULT 0,
    response_time_ms INTEGER DEFAULT 0,
    malicious_check_passed INTEGER DEFAULT 0,
    error_details TEXT DEFAULT '',
    reviewer_notes TEXT DEFAULT '',
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_service
    ON service_reviews(service_id);
CREATE INDEX IF NOT EXISTS idx_reviews_status
    ON service_reviews(status, created_at);

-- Escrow Holds (payment escrow for Agent Provider transactions)
CREATE TABLE IF NOT EXISTS escrow_holds (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    buyer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USDC',
    status TEXT DEFAULT 'held',
    usage_record_id TEXT,
    held_at TEXT NOT NULL,
    release_at TEXT NOT NULL,
    released_at TEXT,
    updated_at TEXT,
    created_at TEXT NOT NULL,
    -- Dispute fields (v0.7.1)
    dispute_reason TEXT,
    dispute_category TEXT,
    dispute_timeout_at TEXT,
    resolved_at TEXT,
    resolution_outcome TEXT,
    resolution_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_escrow_provider
    ON escrow_holds(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_escrow_release
    ON escrow_holds(release_at, status);

-- Dispute Evidence (structured evidence for escrow disputes)
CREATE TABLE IF NOT EXISTS dispute_evidence (
    id TEXT PRIMARY KEY,
    hold_id TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    role TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_urls TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dispute_evidence_hold
    ON dispute_evidence(hold_id);

-- Service Reports (abuse reporting by buyers)
CREATE TABLE IF NOT EXISTS service_reports (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    details TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_service
    ON service_reports(service_id);
CREATE INDEX IF NOT EXISTS idx_reports_provider
    ON service_reports(provider_id);
