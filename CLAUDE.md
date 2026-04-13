# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BackcountryFinder is a backcountry experience aggregator that scrapes outdoor activity listings (skiing, climbing, hiking, etc.) from multiple Canadian guide companies and booking platforms, storing them in Supabase and displaying them on a static frontend. Live at backcountryfinder.com.

## Tech Stack

- **Frontend:** Static `index.html` with vanilla JS/CSS (no build step, no framework)
- **Scrapers:** Python 3.11 — `requests`, `beautifulsoup4`, `playwright` (for JS-rendered sites)
- **Database:** Supabase (PostgreSQL) — URL: `https://owzrztaguehebkatnatc.supabase.co`
- **Serverless:** Supabase Edge Functions (Deno/TypeScript)
- **AI Classification:** Claude Haiku for activity/location normalization when mapping tables fail; also generates course summaries in batches, deduplicated by title
- **Email:** Resend API
- **CI/CD:** GitHub Actions — manual-trigger scraper workflows + auto-deploy for Edge Functions

## Running Scrapers

All scrapers require these environment variables: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY`, `RESEND_API_KEY`.

```bash
pip install requests beautifulsoup4
python scraper.py              # Main scraper (Rezdy, CWMS, Summit, IAG, HVI, SRG, Skaha)
python scraper_yamnuska.py     # Requires playwright: pip install playwright && playwright install
python scraper_aaa.py          # Alpine Air Adventures (Checkfront API)
python scraper_aaa_details.py  # AAA detail enrichment
python housekeeping.py         # Claude-powered mapping table review
```

Scrapers are idempotent (upsert via `Prefer: resolution=merge-duplicates`). Safe to re-run.

In production, scrapers run via GitHub Actions with `workflow_dispatch` (manual trigger from GitHub UI). No scheduled runs.

## Conventions

- **Booking URLs** must always append `utm_source=backcountryfinder&utm_medium=referral`
- **Stable ID format:** `{provider_id}-{activity}-{date_sort}`
- **Availability (`avail`) values:** `open`, `low`, `critical`, `sold` — sold courses set `active=false`
- **Activity canonical values** (use exactly these): `skiing`, `climbing`, `mountaineering`, `hiking`, `biking`, `fishing`, `heli`, `cat`, `huts`, `guided`, `glissading`, `rappelling`, `snowshoeing`, `via_ferrata`
- **Location canonical format:** `"City, Province"` e.g. `"Canmore, AB"` — for ranges use `"Area Name, BC"` e.g. `"Rogers Pass, BC"`
- **Playwright scrapers** get their own standalone file (e.g. `scraper_yamnuska.py`), never added to `scraper.py`
- **GitHub Actions workflows:** `.github/workflows/scraper-{id}.yml`

## Architecture

### Scraping Pipeline

Each provider has a dedicated scraping function in `scraper.py` (or a standalone file for Playwright-based providers). The flow is:

1. **Fetch** listings from provider (REST API, HTML scraping, or Playwright for JS-rendered pages)
2. **Normalize** activity type via three-tier resolution: mapping tables → Claude classification → regex fallback
3. **Normalize** location via `location_mappings` table + Google Places API + Claude
4. **Generate** stable IDs: `{provider_id}-{activity}-{date_sort}`
5. **Upsert** to Supabase `courses` table

Key helper functions in `scraper.py`: `sb_get()`, `sb_upsert()`, `resolve_activity()`, `normalise_location()`, `parse_date_sort()`, `spots_to_avail()`.

### Provider-Specific Scrapers

| Provider | Platform | Handler |
|----------|----------|---------|
| Altus, MSAA | Rezdy API | `scraper.py:scrape_rezdy()` |
| Canada West Mountain School | WooCommerce | `scraper.py:scrape_cwms()` |
| Summit Mountain Guides | The Events Calendar | `scraper.py:scrape_summit()` |
| Island Alpine, Hike Vancouver Island | Custom Rails | `scraper.py:scrape_iag()`, `scrape_hvi()` |
| Squamish Rock Guides | Custom WordPress | `scraper.py:scrape_srg()` |
| Skaha Rock Adventures | Static HTML | `scraper.py:scrape_skaha()` |
| Yamnuska | JS-rendered WordPress | `scraper_yamnuska.py` (Playwright) |
| Alpine Air Adventures | Checkfront API v3.0 | `scraper_aaa.py` |

### Supabase Edge Functions

Four Deno TypeScript handlers in `supabase/functions/`:
- **send-saved-list** — emails a user's saved courses
- **notify-submission** — handles "Get Listed" and "Suggest Provider" form submissions
- **unsubscribe-notification** — one-click unsubscribe
- **notify-signup-confirmation** — course watchlist signup confirmation

All functions use inline HTML email templates, CORS headers, and `verify_jwt = false`. Auto-deployed via `deploy-functions.yml` on push to `supabase/functions/**`.

### Data Model

Availability: `open` (5+) → `low` (1-4) → `critical` (1-2) → `sold` (0, sets `active=false`).

Key Supabase tables: `courses` (listings), `activity_mappings` / `location_mappings` (normalization rules), `activity_labels` (Claude-learned classifications), `location_flags` (unresolved locations for review), `notifications` (watchlist subscriptions).

## Adding a New Provider

Follow `add-provider-instructions.md` for the full onboarding process. Always reference that file when adding a provider.

## Slash commands

### /add-scraper
A new standalone scraper file is about to be pasted. When I paste it:
1. Save it as the filename specified in the file's top docstring (e.g. `scraper_bsa.py`)
2. Confirm the filename and line count
3. Do not run it yet

### /trigger-scraper $SCRAPER_ID
Trigger a GitHub Actions workflow via the GitHub CLI:
```bash
gh workflow run scraper-$SCRAPER_ID.yml --repo lukeram-3420/backcountryfinder
gh run list --workflow=scraper-$SCRAPER_ID.yml --repo lukeram-3420/backcountryfinder --limit 3
```
## Git
When making any file changes, always commit and push automatically using:
```bash
git add -A && git commit -m "<describe what changed>" && git push
```
Never wait for manual confirmation to commit.

## Database Schema

### courses
| column | type | notes |
|---|---|---|
| id | text | stable ID: `{provider_id}-{activity}-{date_sort}` or hash fallback |
| title | text | |
| provider_id | text | |
| badge | text | |
| activity | text | canonical: `skiing` `climbing` `mountaineering` `hiking` `biking` `fishing` `heli` `cat` `huts` `guided` `glissading` `rappelling` `snowshoeing` `via_ferrata` |
| location_raw | text | raw string from site |
| location_canonical | text | `City, Province` e.g. `Squamish, BC` |
| date_display | text | human readable e.g. `March 3 (4 Days)` |
| date_sort | date | `YYYY-MM-DD` |
| duration_days | numeric | |
| price | integer | CAD |
| spots_remaining | integer | null if unknown |
| avail | text | `open` `low` `critical` `sold` |
| image_url | text | |
| booking_url | text | always append `utm_source=backcountryfinder&utm_medium=referral` |
| active | boolean | false for sold out courses |
| scraped_at | timestamptz | |
| created_at | timestamptz | |
| activity_raw | text | |
| activity_canonical | text | |
| badge_canonical | text | |
| custom_dates | boolean | |
| summary | text | 1-2 sentences, generated by Claude Haiku |

### providers
| column | type | notes |
|---|---|---|
| id | text | short slug e.g. `bsa` |
| name | text | |
| website | text | |
| location | text | `City, Province` |
| google_place_id | text | auto-discovered via Places API |
| rating | numeric | |
| review_count | integer | |
| active | boolean | |
| created_at | timestamptz | |
| logo_url | text | |

### activity_mappings
| column | type | notes |
|---|---|---|
| id | integer | auto |
| title_contains | text | keyword to match against course title |
| activity | text | canonical activity value |
| created_at | timestamptz | |

### location_mappings
| column | type | notes |
|---|---|---|
| id | integer | auto |
| location_raw | text | |
| location_canonical | text | `City, Province` |
| created_at | timestamptz | |

## Supabase Edge Function conventions

### Environment variable names — use these exactly
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`  ← not SUPABASE_SERVICE_KEY
- `SUPABASE_ANON_KEY`
- `SUPABASE_DB_URL`
- `RESEND_API_KEY`
- `ANTHROPIC_API_KEY`

### Every edge function must handle CORS
All functions are called from the browser and require CORS headers. Always include this at the top of serve():

```ts
if (req.method === 'OPTIONS') {
  return new Response('ok', {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
};
```

All return statements must include ...corsHeaders in their headers.

### Deployment
- All functions deployed via `.github/workflows/deploy-functions.yml`
- Workflow triggers on push to `supabase/functions/**`
- All functions use `--no-verify-jwt` flag
- New functions must be added as a step in deploy-functions.yml
- Secrets are set in Supabase dashboard → Edge Functions → Settings (not in GitHub secrets)
- GitHub secrets are write-only and cannot be read outside of Actions workflows

### Testing a new function
After deployment check in this order:
1. Supabase → Edge Functions — confirm function appears in list
2. Trigger from live site
3. Supabase → Edge Functions → notify-report → Logs — check for errors
4. Check target table for new row
5. Check email inbox