-- Hangfire Training Ltd — one-time setup SQL
-- Review and execute manually in the Supabase SQL editor.

-- Provider starts inactive for staging. Flip active in Providers
-- tab once data is clean.
INSERT INTO providers (id, name, website, location, logo_url, active)
VALUES (
  'hangfire',
  'Hangfire Training Ltd',
  'https://hangfiretraining.com',
  'Golden, BC',
  NULL,
  FALSE
);

-- Location aliases. Raw = exact string the scraper emits from title keywords.
INSERT INTO location_mappings (location_raw, location_canonical) VALUES
  ('Golden, BC',             'Golden, BC'),
  ('Revelstoke, BC',         'Revelstoke, BC'),
  ('Valemount, BC',           'Valemount, BC'),
  ('McBride, BC',             'McBride, BC'),
  ('Fernie, BC',              'Fernie, BC'),
  ('Radium Hot Springs, BC',  'Radium Hot Springs, BC'),
  ('Kimberley, BC',           'Kimberley, BC'),
  ('Kelowna, BC',             'Kelowna, BC'),
  ('Penticton, BC',           'Penticton, BC');

-- Activity mappings — all Hangfire courses are avalanche_safety.
INSERT INTO activity_mappings (title_contains, activity) VALUES
  ('ast 1',       'avalanche_safety'),
  ('ast 2',       'avalanche_safety'),
  ('ast-1',       'avalanche_safety'),
  ('ast-2',       'avalanche_safety'),
  ('sled ast',    'avalanche_safety'),
  ('ski ast',     'avalanche_safety'),
  ('avalanche',   'avalanche_safety');
