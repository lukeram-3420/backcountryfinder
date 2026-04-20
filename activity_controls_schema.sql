-- Activity Tracking Dashboard — schema additions.
-- Run once in the Supabase SQL editor.
--
-- Source of truth for which provider activities are visible on the frontend
-- and (for Zaui tenants) how far ahead to walk availability windows.
--
-- activity_key is a prefixed string:
--   'zaui:{numeric_id}'     — platforms with a stable upstream ID
--   'title:{title_hash_8}'  — platforms without one (WP / HTML / Squarespace)
-- One column → one unique-conflict target for UPSERT.

CREATE TABLE IF NOT EXISTS activity_controls (
  id             bigserial PRIMARY KEY,
  provider_id    text        NOT NULL,
  activity_key   text        NOT NULL,
  title          text        NOT NULL,
  upstream_id    text,
  title_hash     text,
  platform       text,
  visible        boolean     NOT NULL DEFAULT true,
  tracking_mode  text        NOT NULL DEFAULT 'immediate'
                 CHECK (tracking_mode IN ('immediate', 'extended')),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider_id, activity_key)
);

CREATE INDEX IF NOT EXISTS activity_controls_provider_idx
  ON activity_controls (provider_id);
CREATE INDEX IF NOT EXISTS activity_controls_visible_idx
  ON activity_controls (visible);

-- k/v scraper configuration, admin-editable via the Activity Tracking tab.
-- Two canonical keys: 'extended_lookahead_days' and 'immediate_lookahead_days'.
CREATE TABLE IF NOT EXISTS scraper_config (
  key        text PRIMARY KEY,
  value      text        NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO scraper_config (key, value) VALUES
  ('extended_lookahead_days',  '180'),
  ('immediate_lookahead_days', '14')
ON CONFLICT (key) DO NOTHING;

-- Admin UI + seed script read these via the anon key. Edge functions write
-- via the service role, so a simple SELECT-for-all policy is enough.
ALTER TABLE activity_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE scraper_config    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS anon_read ON activity_controls;
CREATE POLICY anon_read ON activity_controls
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS anon_read ON scraper_config;
CREATE POLICY anon_read ON scraper_config
  FOR SELECT TO anon, authenticated USING (true);
