-- Migration 0002: Promo codes system
-- Created: 2026-04-03
-- Adds promo_codes table (code definitions) and promo_redemptions table (usage tracking).

CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    free_months INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT NOT NULL,
    max_redemptions INTEGER DEFAULT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    free_months INTEGER NOT NULL,
    redeemed_at TEXT NOT NULL,
    UNIQUE(code, provider_id)
);

CREATE INDEX IF NOT EXISTS idx_promo_redemptions_provider
    ON promo_redemptions(provider_id);
CREATE INDEX IF NOT EXISTS idx_promo_redemptions_code
    ON promo_redemptions(code);

-- Seed the PRODUCTHUNT promo code: 3 months free, expires 2026-06-06
INSERT OR IGNORE INTO promo_codes (code, description, free_months, expires_at, max_redemptions, active, created_at)
VALUES ('PRODUCTHUNT', 'Product Hunt launch promo - 3 months zero commission', 3, '2026-06-06T23:59:59+00:00', NULL, 1, '2026-04-03T00:00:00+00:00');
