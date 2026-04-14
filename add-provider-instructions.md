# Adding a New Provider

End-to-end checklist for onboarding a new outdoor adventure provider into BackcountryFinder. Always read this file plus `CLAUDE.md` before starting.

## 1. Pipeline

The provider should already exist in `provider_pipeline` via the admin Pipeline tab "Add provider" form. If not, add it there first — Claude Haiku auto-fills name, location, platform, complexity, priority, and Google Places rating.

## 2. Build the scraper

- New file `scraper_{id}.py` following the `scraper_yamnuska.py` (Playwright) or `scraper_aaa.py` (REST API) pattern.
- Import shared utilities from `scraper_utils`. Never define a local `normalise_location` or write to `activity_mappings` / `location_mappings` directly.
- Two-pass via `fetch_detail_pages` if the listing page lacks dates/prices.

## 3. GitHub Actions workflow

`.github/workflows/scraper-{id}.yml` with `workflow_dispatch` only. Final step runs `python validate_provider.py {id}` with `continue-on-error: true`.

Add a corresponding step to `scraper-all.yml`.

## 4. Supabase SQL

Run in the Supabase SQL editor:

```sql
-- Provider starts inactive for staging. Flip active in Providers
-- tab once data is clean.
INSERT INTO providers (id, name, website, location, logo_url, active)
VALUES ('{id}', '{name}', '{website}', '{location}', null, false);

-- Known location aliases (raw → canonical)
INSERT INTO location_mappings (location_raw, location_canonical) VALUES
  ('Squamish', 'Squamish, BC'),
  ('Sea to Sky', 'Squamish, BC');

-- Known title → activity mappings
INSERT INTO activity_mappings (title_contains, activity) VALUES
  ('crevasse rescue', 'avalanche_safety');
```

## 5. First run

1. Trigger `scraper-{id}.yml` from the Actions tab (or admin Providers → Run).
2. Run `validate_provider.py {id}` (or admin Providers → Validate).
3. Inspect Flags tab Warnings + Auto-flags. Fix mappings, add price exceptions, regenerate summaries.
4. When clean, flip `providers.active = true` via the admin Providers toggle. Pipeline auto-syncs to `live`.
