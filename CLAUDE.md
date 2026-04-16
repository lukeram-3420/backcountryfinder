# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working with Claude Code

### Always read CLAUDE.md first
Every Claude Code prompt should begin by reading CLAUDE.md in full before making any changes. This ensures all conventions, column rules, naming standards, and stack context are respected in every edit.

### Keeping project knowledge in sync
Whenever CLAUDE.md is updated, output the full contents of the updated CLAUDE.md at the end of your response so it can be copied directly into the claude.ai project knowledge base.

### UX conventions
- Every tab in admin.html has a "How to use this tab" collapsible help section at the top
- Whenever a UX change is made to any tab — new button, new section, new behaviour — the "How to use this tab" text for that tab must be updated to reflect the change in the same commit
- This applies to all tabs: Providers, Activity Mappings, Location Mappings, Summary Review, Flags, Audit Log, Pipeline, Settings
- After any successful write action in the admin panel (save, approve, reject, clear, regenerate, whitelist, add mapping, mark as expected), the actioned row must be immediately removed from the UI. The user should never have to re-action something they have already actioned. Rows only reappear after the next data refresh or page reload.

### Claude Code behaviour
- Never ask for confirmation before making changes when following a prompt
- Never pause mid-task to check in — complete the full prompt end to end
- Only ask questions if something is genuinely ambiguous and cannot be inferred from CLAUDE.md or the existing code
- If in doubt, make the most conservative safe change and note it in the commit message

## Project Overview

BackcountryFinder is a backcountry experience aggregator that scrapes outdoor activity listings (skiing, climbing, hiking, etc.) from multiple Canadian guide companies and booking platforms, storing them in Supabase and displaying them on a static frontend. Live at backcountryfinder.com.

### Frontend Architecture

The public site is a **single static file — [index.html](index.html)** — with vanilla JS/CSS, no build step, no framework. Routing is client-side page-switching via a single `showPage(name)` function; there is no hash routing and no separate HTML files. The server path is always `/` (query params are used for deep links — see below).

**Four pages**, each a sibling `<div class="page" id="page-{name}">` block toggled by `.active` class via `showPage()` ([index.html:1262](index.html#L1262)). Only one is visible at a time.

| Page | `#page-…` id | Trigger | Purpose |
|------|--------------|---------|---------|
| Search (default) | `page-search` | Logo click, `nav-search`, `mnav-search`, page load | Main course grid with filter bar |
| My List | `page-saved` | `nav-saved` / `mnav-saved` | User's localStorage-saved courses |
| Providers | `page-providers` | `nav-providers` / `mnav-providers` | Grid of all active providers with logo / rating / activity tags |
| About | `page-about` | `nav-about` / `mnav-about` | Static copy, no data |

Two nav components render the same four entries: **topnav** (desktop, `<nav class="topnav">` at [index.html:410](index.html#L410)) and **mobile-nav** (bottom tab bar, `<nav class="mobile-nav">` at [index.html:592](index.html#L592)). `showPage` toggles the `.active` class on both nav sets simultaneously.

**Shared-list deep link:** URLs like `/?shared=id1,id2,id3` trigger a green banner (`#shared-banner`) prompting the visitor to save those courses to their list. Parsed via `URLSearchParams` in `getSharedIds()` ([index.html:1301](index.html#L1301)).

**Provider deep link:** URLs like `/?provider={provider_id}` pre-apply a provider filter to the search grid and show a dismissable chip (`#provider-filter-chip`). Parsed in `initProviderFilter()` ([index.html:1229](index.html#L1229)).

**Modals & overlays** (toggled by CSS `.active`, not page-switched):
- **Notify modal** (`#notify-modal`) — "Notify me" signup for sold-out courses → inserts into `notifications` table.
- **Email-list modal** (`#email-list-modal`) — email a copy of the user's saved list → calls `send-saved-list` edge function.
- **Provider modal** (`#provider-modal`) — two-tab form ("suggest a provider" / "get listed") → inserts into `provider_submissions` and calls `notify-submission` edge function.
- **Book toast** (`#book-toast`) — transient bottom-right email capture when clicking "book now" on a card; writes to `email_signups`.
- **Micro-toast** (`#micro-toast`) — transient small confirmation for save / share actions.

**Key UI components:**
- **Course card** — built by `buildCard(c)` ([index.html:1035](index.html#L1035)). Used in the Search grid, My List grid, and the shared-list preview inside the Email-list modal.
- **Provider card** — built inline in `loadProviders()` ([index.html:1544](index.html#L1544)). Shows logo (or text fallback), star rating (links to Google reviews when `google_place_id` present), website link, activity tags derived from the `provider_activities` view.
- **Filter bar** — three controls at the top of `#page-search`: `#search-activity`, `#search-location`, `#search-date`. Activity → location dropdown dependency is wired through `updateLocationsForActivity(activity)` ([index.html:1016](index.html#L1016) query). Each control change calls `debouncedSearch()` → `fetchCourses()`.
- **Save/share controls** (My List toolbar) — clear list, email my list, share list (popover with copy-link / WhatsApp / SMS / email buttons).

**The six Supabase queries covered by the `flagged=not.is.true&auto_flagged=not.is.true` rule** (see "Frontend filter rule" below):

| # | Section | Location | Table / filter |
|---|---------|----------|----------------|
| 1 | Main listing (Search grid) | [index.html:896](index.html#L896) `fetchCourses()` | `courses?select=*,providers(...)` + filters + paginated |
| 2 | Activity dropdown | [index.html:973](index.html#L973) `loadActivitiesDropdown()` | `courses?select=activity_canonical&active=eq.true` |
| 3 | Location dropdown (activity-scoped) | [index.html:1016](index.html#L1016) `updateLocationsForActivity()` | `courses?select=location_canonical&activity_canonical=eq.{…}` |
| 4 | Saved courses | [index.html:1169](index.html#L1169) `renderSaved()` | `courses?select=*,providers(...)&or=(id.eq.…)` |
| 5 | Shared-list preview in banner | [index.html:1380](index.html#L1380) `renderSharedBannerPreview()` | `courses?select=*,providers(name)&or=(id.eq.…)` |
| 6 | Shared-list preview in Email modal | [index.html:1454](index.html#L1454) `populateEmailListPreview()` | `courses?select=*,providers(name,rating)&or=(id.eq.…)` |

Additional reads that do **not** need the flagged filter (no course visibility concern):
- `activity_labels` ([index.html:768](index.html#L768)) — canonical slug → display-label map for filter dropdowns and provider tags.
- `location_mappings` ([index.html:993](index.html#L993)) — baseline location dropdown options when no activity filter is active.
- `providers` ([index.html:1240](index.html#L1240)) — resolve `?provider=` deep-link label.
- `providers` + `provider_activities` ([index.html:1549](index.html#L1549), [index.html:1558](index.html#L1558)) — Providers page grid.

**Writes from the frontend** (all direct REST with anon key — no edge function for these):
- `click_events` ([index.html:736](index.html#L736)) — book-now click telemetry.
- `email_signups` ([index.html:1479](index.html#L1479)) — toast / modal email capture.
- `provider_submissions` ([index.html:1510, 1531](index.html#L1510-L1531)) — suggest/get-listed form submits.

Writes that trigger server-side work go through the edge functions documented elsewhere in this file (`notify-report`, `notify-submission`, `send-saved-list`, `unsubscribe-notification`, `notify-signup-confirmation`).

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
- **Stable ID format (V2 — active):** `{provider_id}-{date_sort}-{title_hash}` or `{provider_id}-flex-{title_hash}` (title_hash = first 8 chars of md5(title.strip().lower())). No activity segment. See V2 section below.
- **Stable ID format (V1 — legacy, still in DB):** `{provider_id}-{activity}-{date_sort}-{title_hash}` (title_hash = first 6 chars of md5(title)). V1 rows have `activity_canonical` populated; V2 rows have `activity_canonical = NULL`.
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

### Column existence rule
Scrapers must never reference columns in upsert payloads, SELECT queries, or PATCH calls that are not defined in the Database Schema section of this file. Before writing any database interaction code, Claude Code must verify the column exists in the schema defined here. If a column is needed that does not exist in the schema, stop and explicitly tell the user — never assume the column exists or write code that depends on it without confirmation. Never add ALTER TABLE statements to migration files or print them as suggestions without flagging this to the user first.

### Mapping tables are admin-write-only
Scrapers must never write directly to `activity_mappings` or `location_mappings`. When Claude Haiku classifies a new activity or normalises a new location in `scraper_utils.py`, the suggestion is queued to `pending_mappings` or `pending_location_mappings` for review in the admin panel. The approved mapping is only inserted into the live mapping table when an admin clicks Approve. This prevents scrapers from silently polluting the canonical mapping tables with LLM-generated guesses.

**All scrapers must import `normalise_location` from `scraper_utils`** — it returns a single canonical string and internally queues unknown locations to `pending_location_mappings`. Never define a local `normalise_location` returning a `(canonical, is_new, add_mapping)` tuple; that legacy signature was removed from `scraper.py`, `scraper_altus.py`, `scraper_cwms.py`, and `scraper_summit.py`, and the paired `sb_insert("location_mappings", ...)` call sites were deleted.

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

**Default provider state is `active=false`** — every new `providers` INSERT must use `active=false` so the row stays out of the live frontend until data has been validated. Flip to `active=true` via the admin Providers tab toggle once warnings/auto-flags are clear.

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
| `generate_summaries_batch` | `(courses: list, provider_id: str = None) -> dict` | Batch-generate two-field summaries (Phase 1 V2): `display_summary` (user-facing) + `search_document` (Algolia keywords). Input: list of `{id, title, description, provider, activity}`. Returns `{course_id: display_summary_text}` (backward-compatible). Internally upserts both fields to `course_summaries` table. `provider_id` param used for upsert; falls back to each course dict's `provider_id` key. Processes in batches of 12 with single retry on failure. |

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
- **Haiku batching**: `generate_summaries_batch` processes 12 courses per Claude call with single retry on failure. Internally upserts both `display_summary` and `search_document` to `course_summaries` table (Phase 1 V2). Return value is backward-compatible `{course_id: summary_text}`.
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
6. Writes email-only warnings to `validator_warnings` (deletes existing rows for the provider first, then inserts fresh). No email is sent — the admin Flags tab replaces the old email report.

**7 checks — AUTO-HIDE vs EMAIL ONLY:**

| Check | AUTO-HIDE (sets `auto_flagged=true`) | EMAIL ONLY |
|-------|--------------------------------------|------------|
| 1. Summary quality | Contradicts activity, duplicate summary bleed | Empty/null summary |
| 2. Activity mapping | Null activity, title/activity mismatch | — |
| 3. Price sanity | Zero or negative price | Null price (when peers have prices), >5x median outlier (skipped for titles matching `validator_price_exceptions`) |
| 4. Date sanity | Past date with `active=true` | >2 years in the future |
| 5. Availability | — | Null avail, all-sold warning |
| 6. Duplicates | All but first occurrence of same title+date (titles in `validator_whitelist` are skipped) | — |
| 7. Course count | — | >30% drop vs last run |

**Exceptions:** "Ski Mountaineering" titles accept either skiing or mountaineering. Price outliers skip courses with "Logan", "Expedition", or "Traverse" in the title.

### Validator priority stack
Admin decisions always take precedence over automated validator rules.
The validator checks admin decisions first in this order before running
any keyword or automated checks:

1. `validator_suppressions` — explicit admin "ignore this" decision.
   If a suppression matches (provider_id + title_contains + flag_reason),
   skip the check entirely. Highest priority.
2. `activity_mappings` — explicit admin activity assignment. If a mapping
   matches the course title AND the mapped activity equals the course's
   current activity, skip the mismatch check. Admin mapping trumps all
   keyword detection.
3. `validator_price_exceptions` — explicit admin "this price is correct"
   decision. Skip the outlier check for matching titles.
4. `validator_whitelist` — explicit admin "this duplicate is intentional"
   decision. Skip the duplicate check for matching titles.

The validator is a safety net for unreviewed courses only. Once an admin
has made any explicit decision about a course, the validator must respect
it permanently. Keyword rules and automated checks only fire when no admin
decision exists for that course and check type.

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
1. **Providers** — stats row (providers / courses / auto-hidden / user flags), provider table with active toggle, last run, course count, status badge, per-provider "Run" and "Validate" buttons (Validate calls `admin-trigger-scraper` with `workflow_id='validate-provider.yml'` + `inputs={provider_id}`), and "Run all" button. Column headers are clickable to sort (Provider / Last run / Courses / Status), default is alphabetical by name.
2. **Activity Mappings** — pending + approved activity mappings with inline Edit and Delete. Header has an **"Add mapping"** button that opens an inline form (Title contains + Activity dropdown sourced from `activity_labels`) and POSTs directly to `/rest/v1/activity_mappings` with the authenticated session token. Approved rows edit `title_contains` + activity dropdown (fetched dynamically from `activity_labels`). Course counts are on-demand via a "Load counts" button — one `countRows()` query per mapping using `title=ilike.*{title_contains}*`, results cached for the session. Column headers (title_contains / Activity / Courses / Created) are clickable to sort ascending/descending; Courses is only sortable after counts are loaded. Default is alphabetical by `title_contains`.
3. **Location Mappings** — pending + approved location mappings with inline Edit and Delete. Header has an **"Add mapping"** button that opens an inline form (Location raw + Location canonical text inputs) and POSTs directly to `/rest/v1/location_mappings` with the authenticated session token. Approved rows edit both `location_raw` and `location_canonical`. Course counts are on-demand via a "Load counts" button — one `countRows()` query per unique `location_canonical`, results cached for the session. Column headers (Raw / Canonical / Courses / Created) are clickable to sort ascending/descending; Courses is only sortable after counts are loaded. Default is alphabetical by `location_raw`.

### Sortable headers (shared pattern)
Three tables (Providers, Activity Mappings, Location Mappings) use a shared sort helper in `admin.html` (`cmpValues`, `sortIndicator`, `sortableHeader`, `toggleSortState`). Clicking a header toggles asc/desc on that column or switches to a new column (asc first). Nulls always sink to the bottom regardless of direction. Text sorts via `.toLowerCase().localeCompare()`. Numeric sorts cast via `Number(...)`.
4. **Summary Review** — all `course_summaries` rows where `approved=false`. Two fields per row: **Card description** (editable textarea, maps to `courses.summary`) and **Search document** (read-only textarea, maps to `courses.search_document`, Algolia keywords only). Approve / Reject / Regenerate buttons per row. Regenerate re-runs Haiku to produce both fields fresh.
5. **Flags** — Stats row (User reports / Auto-hidden / Warnings). Header buttons: "Reload flags" (re-runs `loadFlagsTab`), "Re-validate all ↗" (loops `admin-trigger-scraper` over all active providers with 500ms spacing), "Copy fixable flags prompt" (bundles wrong_price, wrong_date, bad_description, sold_out flags for Claude Code). User reports section (only `button_broken` and `other` get a Mark resolved button). Validator auto-flags section is **grouped by `(title, flag_reason)`** so 20 identical mismatch rows collapse to one row with an occurrences badge. Each group offers a root-cause fix action based on the reason: `activity mismatch:` / `null activity` → **Add mapping** (inline form, pre-fills title + suggested activity, calls `admin-approve-mapping` then bulk-clears the group's auto-flags); `summary mismatch:` / `summary bleed` → **Regenerate** (calls `admin-regenerate-summary` per course id then bulk-clears); `invalid price:` → **Mark as expected** (bulk-clear with note that next run may re-flag unless a code-level exception is added); A third **Warnings** subsection below auto-flags surfaces the `validator_warnings` table (email-only issues persisted by `validate_provider.py`): grouped by `(title, check_type)`, actions per type — `price_outlier` → Mark as expected (opens inline form for `title_contains`, scope, reason → writes a permanent row to `validator_price_exceptions` and deletes the warning rows; future runs skip the outlier check for matching titles); `summary_empty` → Regenerate (loops `admin-regenerate-summary` then deletes); `null_price` / `null_avail` / `future_date` → View (opens booking URL); `count_drop` → View provider (switches to Providers tab); `all_sold` → informational only. `duplicate:` → **Diagnose** (calls `admin-diagnose-duplicate` which sends the rows to Claude Haiku and returns `{verdict, reason, claude_code_prompt}`). Whitelist verdict → **Whitelist** button records one row per provider in `validator_whitelist` then bulk-clears. Fix-scraper verdict → **Copy fix prompt ↗** copies the Claude Code instruction to clipboard. Haiku failure falls back to a "Diagnosis unavailable" note. `validator_whitelist` is not yet consumed by `validate_provider.py` — that is a future wiring step. **Clear all** is always present as a secondary option and loops `admin-clear-auto-flag` over every course id in the group.
6. **Audit Log** — last 100 rows of `admin_log` with search filter.
7. **Pipeline** — Provider onboarding tracker backed by `provider_pipeline` table. Header has an **"Add provider"** button that opens an inline URL-only form: `admin-analyse-provider` runs Haiku web_search + Google Places lookup, slugifies the returned name, then POSTs to `provider_pipeline` (status='candidate', `id` = slug). Each non-live row (candidate/scouted/scraper_built) has a **"Copy prompt ↗"** button that copies a Claude Code instruction to the clipboard for building the scraper. **Client-side hide of already-live providers:** on every tab load, `loadPipelineTab` fetches active providers alongside pipeline rows and builds `activeProviderKeys = {domains, names}`. `renderPipelineTable` hides any pipeline row whose normalised website domain or lowercase name is in those sets. Domain comparison uses `domainOf()` which normalises via lowercase → strip `https?://` → strip `www.` → strip trailing `/`. No PATCH writes happen during display — the pipeline's own `status` column is not updated by the UI's filter logic; the status PATCH only fires via the inline Edit form. Excludes `status='skip'` from display. Columns: Name (linked to website), Location, **Rating** (`★ X.X (N)` / `★ —` / `—`), Platform, Complexity, Status (coloured badge: candidate=grey, scouted=blue, scraper_built=yellow, live=green, skip=faded), Priority (1/2/3), Notes (truncated to ~60 chars with full-text tooltip), Edit + Copy prompt. Inline edit lets you change status/platform/priority/notes plus the Google enrichment fields (`google_place_id`, `rating`, `review_count`). Name/Platform/Status/Priority headers are sortable. Pipeline `id` is a text slug — onclick handlers must quote it (`editPipelineRow('${id}')`) or it will be evaluated as a global variable.
8. **Settings** — CRUD for `activity_labels` (canonical activity slugs + display labels), used as the source of truth for the dropdowns in Activity Mappings. Also a static reference for the canonical location format (`City, Province`).

### Admin-facing tables (create in Supabase if not already)
- `admin_log` — `id bigserial, user_email text, action text, detail jsonb, created_at timestamptz default now()`
- `pending_mappings` — pending activity mapping suggestions (columns: `id, course_title, title_contains, provider_id, description, suggested_activity, reviewed bool, created_at`)
- `pending_location_mappings` — pending location mapping suggestions (columns: `id, location_raw, suggested_canonical, reviewed bool, created_at`)
- `course_summaries` — unique on `(provider_id, title)`. Columns: `id, provider_id, title, course_id, summary, description_hash, approved bool, approved_at, pending_reason, created_at`
- `validator_price_exceptions` — persistent price-outlier exceptions populated from the Flags tab Warnings "Mark as expected" inline form. Columns: `id bigserial, title_contains text not null, provider_id text, reason text, created_at timestamptz default now()`. A row means: if a course title contains `title_contains` (case-insensitive substring) and matches the scope (`provider_id` or null = global), skip the >5x median price outlier warning. **Consumed by `validate_provider.py`'s Check 3 and `write_warnings()`** — outlier warnings matching an exception are never written to `validator_warnings`. Zero/negative price auto-hides ignore this table.
- `validator_warnings` — persists email-only validator issues (replaces the old email report). Columns: `id bigserial, provider_id text not null, course_id text, title text, check_type text not null, reason text not null, run_at timestamptz default now()`. `check_type` is one of: `price_outlier`, `null_price`, `null_avail`, `all_sold`, `future_date`, `count_drop`, `summary_empty`. `validate_provider.py` deletes all rows for the provider at the start of each run then writes fresh warnings at the end. Consumed by the Flags tab Warnings subsection in admin.
- `validator_whitelist` — records duplicate-flag groups that admin marked as safe to whitelist. Columns: `id bigserial, title text not null, provider_id text, reason text, created_at timestamptz default now()`. Populated by the Flags tab's Whitelist action. **Consumed by `validate_provider.py`'s duplicate check**: titles matching a whitelist entry (title + provider_id, or title + null provider_id for global whitelist) are skipped by Check 6 and never auto-flagged as duplicates.

### Admin edge functions (deployed via deploy-functions.yml)
All live in `supabase/functions/admin-*/index.ts`. Every one verifies the JWT, checks `user.email === 'luke@backcountryfinder.com'`, executes, then writes a row to `admin_log`.

| Function | Purpose |
|----------|---------|
| `admin-approve-mapping` | Insert into `activity_mappings`, mark `pending_mappings.reviewed=true` |
| `admin-reject-mapping` | Mark `pending_mappings.reviewed=true` |
| `admin-update-mapping` | Update `activity_mappings.activity` by id |
| `admin-delete-mapping` | Delete an `activity_mappings` row by id (does not touch `courses`) |
| `admin-approve-location` | Insert into `location_mappings`, mark `pending_location_mappings.reviewed=true` |
| `admin-reject-location` | Mark `pending_location_mappings.reviewed=true` |
| `admin-update-location` | Update `location_mappings.location_raw` + `location_canonical` by id |
| `admin-delete-location` | Delete a `location_mappings` row by id (does not touch `courses`) |
| `admin-approve-summary` | Approve `course_summaries` row, patch all matching `courses.summary` + `courses.search_document`, clear any user flags |
| `admin-reject-summary` | Set `course_summaries.approved=false` |
| `admin-regenerate-summary` | Call Claude Haiku for fresh two-field summary (`display_summary` + `search_document`), write both to `course_summaries` with `approved=false, pending_reason='regenerated'` |
| `admin-resolve-flag` | Clear user flag — only for `button_broken` / `other` reasons (400 otherwise) |
| `admin-clear-auto-flag` | Clear `auto_flagged` + `flag_reason` |
| `admin-toggle-provider` | Set `providers.active` and cascade to that provider's `courses.active`. Toggle OFF sets all courses to `active=false`. Toggle ON only restores courses where `avail != 'sold'` — preserves sold-out and notify-me courses. |
| `admin-diagnose-duplicate` | Sends a duplicate-flag group's rows to Claude Haiku and returns `{verdict: 'whitelist'|'fix_scraper', reason, claude_code_prompt}`. Used by Flags tab Diagnose button. |
| `admin-analyse-provider` | Accepts `{url}`, calls Claude Haiku with `web_search` tool to derive `{name, location, platform, complexity, priority, notes}`, then enriches with Google Places `{google_place_id, rating, review_count}`. Used by Pipeline tab "Add provider" form. Falls back to URL-derived defaults on Haiku failure. **Places result passes three validation checks before being accepted** (else all three Places fields are nulled): (1) name similarity ≥ 0.4 between Haiku-derived name and Places-returned name (alphanumeric-only char overlap), (2) `user_ratings_total` ≤ 2000, (3) `place_id` not already assigned to a different `provider_pipeline` row. Each rejection logs a reason. |
| `admin-trigger-scraper` | Call GitHub Actions `workflow_dispatches` — requires `GITHUB_TOKEN` secret in Supabase Edge Functions settings. Accepts `{workflow_id, inputs?}`; `inputs` is forwarded to `workflow_dispatch` (used for `validate-provider.yml` which requires `provider_id`). |

### Related one-offs
- `bootstrap_summaries.py` — deleted. Was a one-time migration that seeded `course_summaries` from existing `courses.summary` values. No longer needed.
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
| search_document | text | V2 — Algolia search field, never shown to users |
| currency | text | ISO 4217, default 'CAD' |
| lat | numeric | Google Places enriched latitude |
| lng | numeric | Google Places enriched longitude |
| booking_mode | text | 'instant' / 'request' / 'custom', default 'instant' |
| cancellation_policy | text | scraped cancellation policy text |
| cancellation_policy_hash | text | hash for change detection |
| policy_updated_at | timestamptz | when policy last changed |
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
| country | text | ISO 3166-1 alpha-2, default 'CA' |
| description | text | Haiku-generated, admin approved |
| certifications | text | e.g. 'ACMG, IFMGA' |
| booking_platform | text | e.g. 'rezdy', 'checkfront', 'zaui', 'woocommerce' |

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

### course_availability_log (sacred, append-only)
| column | type | notes |
|---|---|---|
| id | bigserial | primary key |
| course_id | text | not null — references courses.id |
| provider_id | text | not null |
| title_hash | text | not null — groups all dates for same course title |
| date_sort | date | not null — which specific session this tracks |
| spots_remaining | integer | null if provider doesn't give count |
| avail | text | open/low/critical/sold/delisted |
| scraped_at | timestamptz | not null, default now() |
| event_type | text | not null — 'update' / 'delisted' / 'relisted' |

Indexed on `(provider_id)` and `(title_hash, date_sort)`. Append only when values change. **Never truncate, delete, or run cleanup operations.** See sacred-data rule below.

### course_price_log (sacred, append-only)
| column | type | notes |
|---|---|---|
| id | bigserial | primary key |
| provider_id | text | not null |
| title_hash | text | not null |
| date_sort | date | nullable — null means applies to all dates for this title |
| price | integer | not null, in local currency |
| currency | text | not null, ISO 4217, default 'CAD' |
| logged_at | timestamptz | not null, default now() |

Indexed on `(provider_id)` and `(title_hash)`. Append only when price changes. **Never truncate, delete, or run cleanup.**

### provider_email_preferences
| column | type | notes |
|---|---|---|
| id | bigserial | primary key |
| provider_id | text | not null, unique |
| intelligence_emails | boolean | default true |
| delisting_alerts | boolean | default true |
| contact_email | text | |
| unsubscribed_at | timestamptz | |
| updated_at | timestamptz | |

### Intelligence logging tables — append only
`course_availability_log` and `course_price_log` are sacred append-only tables that form the historical intelligence asset of the platform. Never truncate, delete rows from, or run cleanup operations on these tables under any circumstances. New rows are added only when values change. These tables are permanently excluded from all maintenance and cleanup operations.

## V2 Migration — Implemented Changes

V2 is an incremental migration on the live system. V1 and V2 coexist in the same database. The V1 frontend continues working throughout the transition. Changes below are already shipped and active.

### V2 stable ID format
All 14 standalone scrapers now emit V2 IDs via `stable_id_v2()` in `scraper_utils.py`:
```
{provider_id}-{date_sort}-{title_hash_8}     # dated courses
{provider_id}-flex-{title_hash_8}             # flexible-dates / custom / private
```
- No activity segment. Platform-agnostic. Three segments, always three.
- `title_hash_8` = `md5(title.strip().lower())[:8]` via the `title_hash()` function.
- `title_hash()` is the SINGLE source of truth for title hashing — used by `stable_id_v2`, log functions, and future Algolia objectIDs. Never compute an inline md5 of titles elsewhere.
- The old V1 `stable_id()` function still exists in `scraper_utils.py` but is no longer called by any scraper.

### V1/V2 row coexistence
- V2 rows write `activity_canonical = None`. This makes them invisible to the V1 frontend, which filters on `activity_canonical=eq.{value}`.
- V1 rows from previous scraper runs persist in the DB with `activity_canonical` populated.
- Both V1 and V2 rows coexist. The V1 frontend sees only V1 rows. V2 rows accumulate silently.
- **On cutover day:** `DELETE FROM courses WHERE activity_canonical IS NOT NULL` removes all V1 rows cleanly.

### Intelligence logging (V2 Phase 2 — active)
Every scraper calls these after upsert:
- `log_availability_change(course)` — appends to `course_availability_log` only when `spots_remaining` or `avail` differs from the last logged value. Queries by `(provider_id, title_hash, date_sort)` — ID-format-agnostic.
- `log_price_change(course)` — appends to `course_price_log` only when `price` differs from the last logged value. Queries by `(provider_id, title_hash, date_sort)`.
- Both are safe to call on every run — they no-op when values haven't changed.
- Both use `title_hash()` for grouping, NOT `course_id` — so log continuity is preserved across the V1→V2 ID format change.
- **2026-04-16:** Both log tables were purged (only contained 1 test run with V1 IDs from 2 providers). All data from this point forward uses V2 stable IDs exclusively.

### V2 schema additions (live in Supabase)
New columns on `courses`: `search_document`, `currency` (default 'CAD'), `lat`, `lng`, `booking_mode` (default 'instant'), `cancellation_policy`, `cancellation_policy_hash`, `policy_updated_at`.
New columns on `providers`: `country` (default 'CA'), `description`, `certifications`, `booking_platform`.
New columns on `course_summaries`: `search_document`, `title_hash`.
New tables: `course_availability_log`, `course_price_log`, `provider_email_preferences`.
All existing V1 courses backfilled with `currency='CAD'`. All existing providers backfilled with `country='CA'`.

### V2 Phase 1 — Haiku two-field generation (implemented)
`generate_summaries_batch()` now produces two fields per course title:
- `display_summary`: 2 sentences for course card (user-facing, admin-editable)
- `search_document`: keyword-rich text for Algolia (read-only, never shown to users)

**Backward-compatible return**: callers still receive `{course_id: summary_text}`. Both fields are upserted to `course_summaries` internally. `admin-approve-summary` copies both to `courses.summary` + `courses.search_document`. `admin-regenerate-summary` uses the two-field prompt. Summary Review tab shows both fields (card description editable, search document read-only).

No backfill needed — V1 rows are deleted on cutover, and all new scraper runs generate both fields. Algolia (Phase 3) goes live after cutover, so there is no consumer for `search_document` on pre-cutover rows.

### V2 phases remaining (not yet implemented)
- **Phase 3:** Algolia index bootstrap (geo-enrichment, record push, synonym config)
- **Phase 4:** V2 frontend (Algolia InstantSearch replaces Supabase dropdown queries)
- **Phase 5:** Velocity signal calculation (fill rate, price trend — needs 4+ weeks of log data)
- **Phase 6:** Validator simplification (4 checks, admin tabs removed)
- **Phase 7:** Drop V1 columns + tables after cutover

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
