# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working with Claude Code

### Always read CLAUDE.md first
Every Claude Code prompt should begin by reading CLAUDE.md in full before making any changes. This ensures all conventions, column rules, naming standards, and stack context are respected in every edit.

### Keeping project knowledge in sync
Whenever CLAUDE.md is updated, output the full contents of the updated CLAUDE.md at the end of your response so it can be copied directly into the claude.ai project knowledge base.

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
- **Stable ID format:** `{provider_id}-{activity}-{date_sort}-{title_hash}` (title_hash = first 6 chars of md5(title))
- **Availability (`avail`) values:** `open`, `low`, `critical`, `sold` — sold courses set `active=false`
- **Activity canonical values** (use exactly these): `skiing`, `climbing`, `mountaineering`, `hiking`, `biking`, `fishing`, `heli`, `cat`, `huts`, `guided`, `glissading`, `rappelling`, `snowshoeing`, `via_ferrata`, `avalanche_safety`
- **Location canonical format:** `"City, Province"` e.g. `"Canmore, AB"` — for ranges use `"Area Name, BC"` e.g. `"Rogers Pass, BC"`
- **Playwright scrapers** get their own standalone file (e.g. `scraper_yamnuska.py`), never added to `scraper.py`
- **GitHub Actions workflows:** `.github/workflows/scraper-{id}.yml`

## Scraper conventions

### Columns scrapers never touch
The following columns on the courses table are never written by any scraper under any circumstances:
- `flagged`, `flagged_reason`, `flagged_note` — user reports via `notify-report` edge function
- `auto_flagged`, `flag_reason` — validator auto-hide via `validate_provider.py`

Scrapers must never include any of these in any upsert payload.

### Mapping tables are admin-write-only
Scrapers no longer write directly to `activity_mappings` or `location_mappings`. When Claude Haiku classifies a new activity or normalises a new location in `scraper_utils.py`, the suggestion is queued to `pending_mappings` or `pending_location_mappings` for review in the admin panel. The approved mapping is only inserted into the live mapping table when an admin clicks Approve. This prevents scrapers from silently polluting the canonical mapping tables with LLM-generated guesses.

### Two-flag system
| Column set | Written by | Purpose |
|------------|-----------|---------|
| `flagged` + `flagged_reason` + `flagged_note` | `notify-report` edge function (user reports) | User-submitted issue reports. Auto-cleared by `validate_provider.py` when the issue is resolved. |
| `auto_flagged` + `flag_reason` | `validate_provider.py` only | Validator auto-hide for bad data. Reset to `false` at the start of every validation run. |

### Auto-clear rules for user flags
The validator clears user report flags (`flagged=false`) when:
- `wrong_price` → price is now present, positive, and not >5x median
- `wrong_date` → date_sort is valid and in the future
- `sold_out` → avail is not 'open' (confirms the sold-out state)
- `bad_description` → summary is present and passes contradiction checks
- `button_broken` → **never auto-cleared**, manual resolution only
- `other` → **never auto-cleared**, manual resolution only

### Frontend filter rule
All 6 courses queries in `index.html` must include both filters:
```
flagged=not.is.true&auto_flagged=not.is.true
```
This applies to: main listing, activity dropdown, location dropdown, saved courses, shared courses (2 queries).

## Architecture

### Scraping Pipeline

Each provider has a dedicated scraping function in `scraper.py` (or a standalone file for Playwright-based providers). The flow is:

1. **Fetch** listings from provider (REST API, HTML scraping, or Playwright for JS-rendered pages)
2. **Normalize** activity type via three-tier resolution: mapping tables → Claude classification → regex fallback
3. **Normalize** location via `location_mappings` table + Google Places API + Claude
4. **Generate** stable IDs: `{provider_id}-{activity}-{date_sort}-{title_hash}`
5. **Upsert** to Supabase `courses` table
6. **Validate** via `validate_provider.py` (auto-hide bad rows, auto-clear resolved user flags)

Key helper functions in `scraper.py`: `sb_get()`, `sb_upsert()`, `resolve_activity()`, `normalise_location()`, `parse_date_sort()`, `spots_to_avail()`.

### Provider-Specific Scrapers

| Provider | Platform | Standalone file | Legacy handler |
|----------|----------|-----------------|----------------|
| Altus | Rezdy API + WordPress | `scraper_altus.py` | `scraper.py --provider altus` |
| MSAA | Rezdy API | `scraper_msaa.py` | `scraper.py --provider msaa` |
| Canada West Mountain School | WooCommerce | `scraper_cwms.py` | `scraper.py --provider cwms` |
| Summit Mountain Guides | The Events Calendar | `scraper_summit.py` | `scraper.py --provider summit` |
| Island Alpine Guides | Custom Rails | `scraper_iag.py` | `scraper.py --provider iag` |
| Hike Vancouver Island | Custom Rails | `scraper_hvi.py` | `scraper.py --provider hvi` |
| Squamish Rock Guides | Custom WordPress | `scraper_srg.py` | `scraper.py --provider srg` |
| Skaha Rock Adventures | Static HTML | `scraper_skaha_rock_adventures.py` | `scraper.py --provider skaha-rock-adventures` |
| Yamnuska | JS-rendered WordPress | `scraper_yamnuska.py` (Playwright) | — |
| Alpine Air Adventures | Checkfront API v3.0 | `scraper_aaa.py` | — |
| Alpine Air Adventures (details) | WordPress | `scraper_aaa_details.py` | — |
| Black Sheep Adventure | Custom WordPress | `scraper_bsa.py` | — |
| Jasper Hikes & Tours | Squarespace | `scraper_jht.py` | — |

### Supabase Edge Functions

Five Deno TypeScript handlers in `supabase/functions/`:
- **send-saved-list** — emails a user's saved courses
- **notify-submission** — handles "Get Listed" and "Suggest Provider" form submissions
- **unsubscribe-notification** — one-click unsubscribe
- **notify-signup-confirmation** — course watchlist signup confirmation
- **notify-report** — user course report: inserts to `reports` table, sets `flagged=true` + `flagged_reason` + `flagged_note` on the course

All functions use inline HTML email templates, CORS headers, and `verify_jwt = false`. Auto-deployed via `deploy-functions.yml` on push to `supabase/functions/**`.

### Data Model

Availability: `open` (5+) → `low` (1-4) → `critical` (1-2) → `sold` (0, sets `active=false`).

Key Supabase tables: `courses` (listings), `activity_mappings` / `location_mappings` (normalization rules), `activity_labels` (Claude-learned classifications), `location_flags` (unresolved locations for review), `notifications` (watchlist subscriptions), `reports` (user course reports), `scraper_run_log` (course count per provider per run).

## Adding a New Provider

Follow `add-provider-instructions.md` for the full onboarding process. Always reference that file when adding a provider.

## Scraper architecture

### Overview — two parallel systems

Two scraper systems exist side by side:

1. **`scraper.py`** — the original monolith. Contains all provider scraping functions inline. Supports `--provider <id>` to run a single provider. Left untouched as a working fallback.
2. **`scraper_{id}.py`** — the new per-provider pattern. Each file imports shared utilities from `scraper_utils.py` and contains only provider-specific config + HTML parsing logic. All new providers going forward should use this pattern.

Both systems produce identical output (rows upserted to the `courses` table with the same schema).

### scraper_utils.py public API

#### Supabase

| Function | Signature | Description |
|----------|-----------|-------------|
| `sb_get` | `(table: str, params: dict = None) -> list` | GET rows from a Supabase table. `params` is a dict of query-string filters. |
| `sb_upsert` | `(table: str, rows: list) -> None` | POST rows with `Prefer: resolution=merge-duplicates`. |
| `sb_insert` | `(table: str, data: dict) -> None` | INSERT a single row (no upsert). Silently ignores conflicts. |
| `sb_patch` | `(table: str, filter_params: str, payload: dict) -> None` | PATCH rows matching `filter_params` (e.g. `'id=eq.abc'`). |

#### Google Places

| Function | Signature | Description |
|----------|-----------|-------------|
| `find_place_id` | `(query: str) -> Optional[str]` | Find a Google Place ID by text query. Returns `place_id` or `None`. |
| `get_place_details` | `(place_id: str) -> dict` | Get `rating` and `review_count` from Google Places. Returns `{}` on failure. |
| `update_provider_ratings` | `(provider_id: str) -> None` | Look up / refresh Google Places rating for a single provider. |

#### Location

| Function | Signature | Description |
|----------|-----------|-------------|
| `load_location_mappings` | `() -> dict` | Load `location_mappings` table → `{raw_lower: canonical}`. |
| `normalise_location` | `(raw: str, mappings: dict) -> Optional[str]` | Three-tier resolution: exact match → substring → Claude → return raw. Claude suggestions go to `pending_location_mappings` for admin review — scrapers never write directly to `location_mappings`. |

#### Activity

| Function | Signature | Description |
|----------|-----------|-------------|
| `load_activity_mappings` | `() -> list` | Load `activity_mappings` table → `[(title_contains_lower, activity)]`, sorted longest-first. |
| `load_activity_labels` | `() -> dict` | Load `activity_labels` table → `{activity: label}`. |
| `detect_activity` | `(title: str, description: str = "") -> str` | Keyword-based activity detection fallback. Returns canonical activity string. |
| `resolve_activity` | `(title: str, description: str, mappings: list) -> str` | Three-tier: mapping table → Claude classification → keyword fallback. Claude classifications go to `pending_mappings` for admin review — scrapers never write directly to `activity_mappings`. |
| `build_badge` | `(activity: str, duration_days, activity_labels: dict = None) -> str` | Build badge string like `"Mountaineering · 3 days"`. |

#### Claude AI

| Function | Signature | Description |
|----------|-----------|-------------|
| `claude_classify` | `(prompt: str, max_tokens: int = 256) -> dict` | Call Claude Haiku, return parsed JSON. Returns `{}` on failure. |
| `generate_summaries_batch` | `(courses: list) -> dict` | Batch-generate 2-sentence summaries. Input: list of `{id, title, description, provider, activity}`. Returns `{course_id: summary_text}`. Processes in batches of 12 with single retry on failure. Deduplication by title is the caller's responsibility. |

#### Dates & IDs

| Function | Signature | Description |
|----------|-----------|-------------|
| `parse_date_sort` | `(date_str: str) -> Optional[str]` | Extract `YYYY-MM-DD` from various date string formats. |
| `is_future` | `(date_sort: Optional[str]) -> bool` | Returns `True` if date is today or later (or unparseable). |
| `stable_id` | `(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str` | Generate stable ID: `{provider}-{activity}-{date}-{title_hash}` or hash fallback. |

#### Availability & URLs

| Function | Signature | Description |
|----------|-----------|-------------|
| `spots_to_avail` | `(spots: Optional[int]) -> str` | Convert spots_remaining → `open`/`low`/`critical`/`sold`. |
| `append_utm` | `(url: str) -> str` | Append UTM tracking params if not already present. |

#### Email

| Function | Signature | Description |
|----------|-----------|-------------|
| `send_email` | `(subject: str, html: str, to: str = None) -> None` | Send email via Resend API. Defaults to `NOTIFY_EMAIL`. |
| `send_scraper_summary` | `(provider_name: str, count: int, ok: bool = True) -> None` | No-op (automated scraper emails are currently disabled). |

#### Two-pass scraping

| Function | Signature | Description |
|----------|-----------|-------------|
| `fetch_detail_pages` | `(urls: list, parse_fn: Callable, delay: float = 0.5, headers: dict = None) -> list` | Fetch each URL, call `parse_fn(url, html_text) -> list[dict]`, collect all rows. Handles errors per-page. |

#### Important notes

- **Playwright is never imported at the top level** of `scraper_utils.py`. Any scraper that needs Playwright (e.g. Yamnuska) imports it in its own file.
- **Haiku batching**: `generate_summaries_batch` processes 12 courses per Claude call with 0.5s delay between batches. Failed batches retry once after 3s.
- **Environment variables**: `SUPABASE_URL`, `SUPABASE_KEY`, `RESEND_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY` are read from env at module load and available as module-level constants.

### Two-pass scraping pattern

Some providers show a listing page with course names/URLs but no date or price detail. These need a **two-pass** approach:

1. **Listing pass**: Scrape listing page(s) to collect individual course/trip URLs.
2. **Detail pass**: Fetch each course URL and parse dates, prices, availability.

**Reference implementation**: `scraper_aaa.py` (listing) + `scraper_aaa_details.py` (detail enrichment).

The `fetch_detail_pages(urls, parse_fn)` helper in `scraper_utils.py` handles the detail pass loop with error handling and rate limiting. Usage:

```python
from scraper_utils import fetch_detail_pages

def parse_course_page(url, html):
    soup = BeautifulSoup(html, "html.parser")
    # ... parse title, dates, price ...
    return [row_dict, ...]

rows = fetch_detail_pages(course_urls, parse_course_page, delay=0.5)
```

### validate_provider.py

Post-scrape validation script. Runs after any provider scraper completes. Read-only except for flagging.

**Usage:** `python validate_provider.py <provider_id>`

**Behaviour:**
1. Resets all `auto_flagged` rows for this provider (clean slate)
2. Fetches all courses for the provider
3. Runs 7 checks (see below)
4. Auto-clears resolved user report flags
5. Logs course count to `scraper_run_log`

**7 checks — AUTO-HIDE vs EMAIL ONLY:**

| Check | AUTO-HIDE (sets `auto_flagged=true`) | EMAIL ONLY |
|-------|--------------------------------------|------------|
| 1. Summary quality | Contradicts activity, duplicate summary bleed | Empty/null summary |
| 2. Activity mapping | Null activity, title/activity mismatch | — |
| 3. Price sanity | Zero or negative price | Null price (when peers have prices), >5x median outlier |
| 4. Date sanity | Past date with `active=true` | >2 years in the future |
| 5. Availability | — | Null avail, all-sold warning |
| 6. Duplicates | All but first occurrence of same title+date | — |
| 7. Course count | — | >30% drop vs last run |

**Exceptions:** "Ski Mountaineering" titles accept either skiing or mountaineering. Price outliers skip courses with "Logan", "Expedition", or "Traverse" in the title.

### How to add a new provider — checklist

1. **Create `scraper_{id}.py`** importing from `scraper_utils`:
   - Provider config dict at the top (id, name, website, location)
   - Provider-specific activity/location maps if needed
   - HTML parsing functions specific to the provider's website
   - `main()` function that: updates ratings → loads mappings → scrapes → resolves activities/locations → generates summaries → deduplicates → upserts
   - `if __name__ == "__main__": main()`

2. **Create `.github/workflows/scraper-{id}.yml`** with `workflow_dispatch` trigger only:
   - Standard pip install (`requests beautifulsoup4 anthropic`)
   - Add Playwright cache + install steps ONLY if the provider needs a headless browser
   - Set all 5 secret env vars
   - Add a final `Validate` step: `python validate_provider.py {id}` with `continue-on-error: true`

3. **Add a named step to `scraper-all.yml`**:
   ```yaml
   - name: Provider Name
     run: python scraper_{id}.py
     continue-on-error: true
   - name: Validate Provider Name
     run: python validate_provider.py {id}
     continue-on-error: true
   ```

4. **Run Supabase SQL**:
   - INSERT provider row into `providers` table
   - INSERT any known `location_mappings` for the provider's locations
   - INSERT any known `activity_mappings` for the provider's course titles

5. **Use two-pass pattern** if the listing page doesn't contain full detail data (dates, prices). See `scraper_aaa.py` + `scraper_aaa_details.py` as reference.

## GitHub Actions workflow structure

### Individual provider workflows
One file per provider at `.github/workflows/scraper-{id}.yml`. All use `workflow_dispatch` trigger only (manual). Each runs the standalone scraper then validate_provider.py.

### Master workflow — scraper-all.yml
- **Triggers:** `schedule` (cron `0 */6 * * *` — every 6 hours) + `workflow_dispatch`
- Installs all dependencies including Playwright + Chromium
- One named step per provider with `continue-on-error: true`
- A `Validate {Provider}` step after each scraper step

### Validate workflow — validate-provider.yml
- **Trigger:** `workflow_dispatch` with required `provider_id` input
- Runs: `python validate_provider.py ${{ github.event.inputs.provider_id }}`

### Deploy workflow — deploy-functions.yml
- **Trigger:** push to `supabase/functions/**`
- Deploys all edge functions with `--no-verify-jwt`

### Secrets used by all workflows
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `RESEND_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`

## Admin page

- **URL:** `backcountryfinder.com/admin` (static `admin.html` at repo root)
- **Auth:** Supabase Auth, email + password. Only `luke@backcountryfinder.com` is allowed — any other account is auto-signed-out. Auto-logout after 30 minutes of inactivity.
- **All writes go through edge functions** with `Authorization: Bearer {session.access_token}`. The edge function verifies the JWT and checks admin email before touching any table.
- **Reads** use the Supabase publishable (anon) key directly.
- **Security:** `<meta name="robots" content="noindex, nofollow">` + `robots.txt` has `Disallow: /admin`.

### Tabs
1. **Providers** — stats row (providers / courses / auto-hidden / user flags), provider table with active toggle, last run, course count, status badge, per-provider "Run" button, and "Run all" button.
2. **Activity Mappings** — pending + approved activity mappings with inline Edit. Approved rows can be edited in place (title_contains + activity dropdown fetched dynamically from `activity_labels`).
3. **Location Mappings** — pending + approved location mappings with inline Edit. Approved rows edit both `location_raw` and `location_canonical` in place.
4. **Summary Review** — all `course_summaries` rows where `approved=false`. Approve / Reject / Regenerate buttons per row.
5. **Flags** — "Copy fixable flags prompt" button (bundles wrong_price, wrong_date, bad_description, sold_out flags for Claude Code). User reports section (only `button_broken` and `other` get a Mark resolved button). Validator auto-flags section with Clear flag button.
6. **Audit Log** — last 100 rows of `admin_log` with search filter.
7. **Settings** — CRUD for `activity_labels` (canonical activity slugs + display labels), used as the source of truth for the dropdowns in Activity Mappings. Also a static reference for the canonical location format (`City, Province`).

### Admin-facing tables (create in Supabase if not already)
- `admin_log` — `id bigserial, user_email text, action text, detail jsonb, created_at timestamptz default now()`
- `pending_mappings` — pending activity mapping suggestions (columns: `id, course_title, title_contains, provider_id, description, suggested_activity, reviewed bool, created_at`)
- `pending_location_mappings` — pending location mapping suggestions (columns: `id, location_raw, suggested_canonical, reviewed bool, created_at`)
- `course_summaries` — unique on `(provider_id, title)`. Columns: `id, provider_id, title, course_id, summary, description_hash, approved bool, approved_at, pending_reason, created_at`

### Admin edge functions (deployed via deploy-functions.yml)
All live in `supabase/functions/admin-*/index.ts`. Every one verifies the JWT, checks `user.email === 'luke@backcountryfinder.com'`, executes, then writes a row to `admin_log`.

| Function | Purpose |
|----------|---------|
| `admin-approve-mapping` | Insert into `activity_mappings`, mark `pending_mappings.reviewed=true` |
| `admin-reject-mapping` | Mark `pending_mappings.reviewed=true` |
| `admin-update-mapping` | Update `activity_mappings.activity` by id |
| `admin-approve-location` | Insert into `location_mappings`, mark `pending_location_mappings.reviewed=true` |
| `admin-reject-location` | Mark `pending_location_mappings.reviewed=true` |
| `admin-update-location` | Update `location_mappings.location_raw` + `location_canonical` by id |
| `admin-delete-location` | Delete a `location_mappings` row by id (does not touch `courses`) |
| `admin-approve-summary` | Approve `course_summaries` row, patch all matching `courses.summary`, clear any user flags |
| `admin-reject-summary` | Set `course_summaries.approved=false` |
| `admin-regenerate-summary` | Call Claude Haiku for fresh summary, write to `course_summaries` with `approved=false, pending_reason='regenerated'` |
| `admin-resolve-flag` | Clear user flag — only for `button_broken` / `other` reasons (400 otherwise) |
| `admin-clear-auto-flag` | Clear `auto_flagged` + `flag_reason` |
| `admin-toggle-provider` | Set `providers.active` |
| `admin-trigger-scraper` | Call GitHub Actions `workflow_dispatches` — requires `GITHUB_TOKEN` secret in Supabase Edge Functions settings |

### Related one-offs
- `bootstrap_summaries.py` — one-time migration that seeded `course_summaries` from existing `courses.summary` values. Already run; file can be deleted.
- `course_summaries` dedup: unique constraint on `(provider_id, title)`; `description_hash` tracks when the underlying description changes so a stale approved summary can be flagged for review.

### Two-flag system reminder
- `flagged` + `flagged_reason` + `flagged_note` → user reports (set by `notify-report`, cleared by admin actions or validator auto-clear rules)
- `auto_flagged` + `flag_reason` → validator only (set + reset by `validate_provider.py`)
- Scrapers never touch either set.

## Filter behaviour

### Activity → location dependency
When the activity filter changes, the location dropdown is rebuilt to only show locations where that activity has active unflagged courses. This is handled by `updateLocationsForActivity(activity)` in `index.html`. When activity is reset to all, `loadLocationsDropdown()` is called to restore all locations. If the previously selected location is not available in the new activity's locations, the location filter is reset to all.

### Empty states
- No filters + 0 results → maintenance state: '🏔 Updating course listings / Check back in 45 minutes' with pulsing scraper status pill. Shows when courses table is empty and no filters are active.
- Filters applied + 0 results → standard empty state: 'no experiences found / Try adjusting your filters'. Existing behaviour unchanged.

## Known gotchas

### Supabase pagination
**RULE: Never use JavaScript `.length` on Supabase query results to count rows — this is always wrong because of pagination.** ALWAYS use `Prefer: count=exact` with `limit=0` for any count, total, or aggregate number shown in the UI. Use the `countRows()` helper in `admin.html` as the reference implementation. This applies to both `admin.html` and `index.html`. If you see `.length` used on a Supabase result anywhere, it is a bug.

All Supabase queries default to 1000 rows. For queries that need all rows use explicit `Range: 0-49999` headers. Never rely on default pagination for correctness — if a feature shows wrong counts or missing data, check pagination first.

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
| id | text | stable ID: `{provider_id}-{activity}-{date_sort}-{title_hash}` or hash fallback |
| title | text | |
| provider_id | text | |
| badge | text | |
| activity | text | canonical: `skiing` `climbing` `mountaineering` `hiking` `biking` `fishing` `heli` `cat` `huts` `guided` `glissading` `rappelling` `snowshoeing` `via_ferrata` `avalanche_safety` |
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
| flagged | boolean | user report flag — set by notify-report edge function |
| flagged_reason | text | user report reason code |
| flagged_note | text | user report free-text note |
| auto_flagged | boolean | validator flag — set by validate_provider.py |
| flag_reason | text | validator flag reason |

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

### scraper_run_log
| column | type | notes |
|---|---|---|
| id | bigint | auto-generated identity |
| provider_id | text | not null |
| run_at | timestamptz | default now() |
| course_count | int | not null |

Used by `validate_provider.py` to track course count per provider per run. A >30% drop between runs triggers a critical warning in the validation report.

### One-time setup SQL

Run these once in Supabase SQL editor for a fresh setup:

```sql
CREATE TABLE IF NOT EXISTS scraper_run_log (
  id bigint generated always as identity primary key,
  provider_id text not null,
  run_at timestamptz default now(),
  course_count int not null
);

ALTER TABLE courses ADD COLUMN IF NOT EXISTS auto_flagged boolean default false;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS flag_reason text;
```

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
