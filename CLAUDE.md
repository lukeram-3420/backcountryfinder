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
- This applies to all tabs: Providers, Location Mappings, Summary Review, Flags, Audit Log, Pipeline, Settings.
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
- **Course card** — built by `buildCard(c)` in [js/cards.js](js/cards.js). Used in the Search grid, My List grid, and the shared-list preview inside the Email-list modal. No activity/badge render — hero image is `c.image_url` with a single `FALLBACK_IMG` constant (defined in [index.html](index.html)) when missing.
- **Provider card** — built in `loadProviders()` in [js/providers.js](js/providers.js). Shows logo (or text fallback), star rating (links to Google reviews when `google_place_id` present), website link. No activity tags.
- **Filter bar** — active controls on `#page-search` are the Algolia searchbox and `#search-date`. Activity and location dropdowns were removed in V2 Phase 4 — free-text search against `search_document` covers both.
- **Save/share controls** (My List toolbar) — clear list, email my list, share list (popover with copy-link / WhatsApp / SMS / email buttons).

**Supabase queries covered by the `flagged=not.is.true&auto_flagged=not.is.true` rule** (see "Frontend filter rule" below):

| # | Section | Location | Table / filter |
|---|---------|----------|----------------|
| 1 | Saved courses | [js/saved.js](js/saved.js) `renderSaved()` | `courses?select=*,providers(...)&or=(id.eq.…)` |
| 2 | Shared-list preview in banner | [js/saved.js](js/saved.js) `renderSharedBannerPreview()` | `courses?select=*,providers(name)&or=(id.eq.…)` |
| 3 | Shared-list preview in Email modal | [js/saved.js](js/saved.js) `populateEmailListPreview()` | `courses?select=*,providers(name,rating)&or=(id.eq.…)` |

The main Search grid is served by Algolia (`courses_v2` index) — it applies its own filters at sync time in [algolia_sync.py](algolia_sync.py) (`active=eq.true&flagged=not.is.true&auto_flagged=not.is.true&activity_canonical=is.null`), so the live frontend only needs the flagged filter on the three direct Supabase reads above.

Additional reads that do **not** need the flagged filter (no course visibility concern):
- `providers` — resolve `?provider=` deep-link label.
- `providers` — Providers page grid in [js/providers.js](js/providers.js).

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
- **AI Classification:** Claude Haiku for location normalisation when the mapping table misses; also generates two-field course summaries (`display_summary` + `search_document`) in batches, deduplicated by title. Activity classification has been retired — scrapers no longer emit an activity field (see V2 notes below).
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
- **Activity:** retired. Scrapers no longer populate `activity`, `activity_raw`, `badge`, or `badge_canonical` on V2 rows. The four columns stay on the schema until V2 Phase 7 cutover, then drop. The Algolia index exposes no `activity` facet — free-text search on `search_document` handles activity-style queries via synonyms (skiing/backcountry skiing/ski touring/splitboarding etc.).
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

### Location mapping policy — Haiku-live-write on structural confidence
`normalise_location` in `scraper_utils.py` resolves an unknown location through four tiers:
1. Exact match in the in-memory `location_mappings` dict → return canonical.
2. Substring match → return canonical.
3. **Claude Haiku with structural validation** — Haiku is prompted for `{"city": "...", "province": "XX"}` JSON. The response is accepted ONLY if:
   - `city` is a non-empty string containing no comma, AND
   - `province` matches `^[A-Z]{2}$` (a 2-letter uppercase code: BC/AB/ON/QC/CA/NY/WA/etc. — scales past Canada).

   On a structural match, the scraper writes `{location_raw, location_canonical}` directly to the `location_mappings` table (LIVE — no admin approval required) and returns the composed `"City, XX"` canonical. The admin Location Mappings tab sees this appear in the approved list on next load.
4. **Fallback (Haiku unconfident / malformed / API error / no API key)** — queue to `pending_location_mappings` with a null `suggested_canonical` and return `None`. The admin fixes these by hand in the pending queue.

**This is a targeted deviation from the old "mapping tables are admin-write-only" rule.** It applies to location only. Activity mappings are retired entirely — scrapers no longer resolve activity. The structural guard (`^[A-Z]{2}$`) is the confidence proxy: Haiku either produces something parseable into `City, XX` or it doesn't, no model-self-reported confidence mush.

**All scrapers must import `normalise_location` from `scraper_utils`** — it returns `Optional[str]` and internally queues unknowns to `pending_location_mappings`. Never define a local `normalise_location` returning a `(canonical, is_new, add_mapping)` tuple; that legacy signature was removed from `scraper.py`, `scraper_altus.py`, `scraper_cwms.py`, and `scraper_summit.py`, and the paired `sb_insert("location_mappings", ...)` call sites were deleted.

### Never pass `location_canonical: None` to a courses upsert
When `normalise_location()` returns `None`, the scraper MUST OMIT the `location_canonical` key from the upsert payload entirely. Do NOT include `"location_canonical": None`. Reason: Supabase's `Prefer: resolution=merge-duplicates` treats an explicit null as "overwrite existing value with null." On a re-scrape, a transient Haiku failure would then silently destroy a previously-resolved canonical. Omitting the key preserves whatever is already in the DB. Pattern every scraper uses:

```python
loc_canonical = normalise_location(loc_raw, mappings)
row = {..., "location_raw": loc_raw}
if loc_canonical is not None:
    row["location_canonical"] = loc_canonical
processed.append(row)
```

This applies to every new course row built by any `scraper_{id}.py` that calls `normalise_location`. Scrapers that derive location from a hardcoded provider default (`scraper_srg`, `scraper_skaha_rock_adventures`, `scraper_aaa`, `scraper_bsa`, `scraper_jht`) do not call `normalise_location` and always have a non-null canonical, so the guard is structurally unnecessary there.

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
- `bad_description` → **never auto-cleared** (Initiative 3). Every user report routes to the Summary Review tab for explicit admin acknowledgement.
- `button_broken` → **never auto-cleared**, manual resolution only
- `other` → **never auto-cleared**, manual resolution only

### Frontend filter rule
All direct Supabase courses queries in the `/js/` modules must include both filters:
```
flagged=not.is.true&auto_flagged=not.is.true
```
This applies to: saved courses, shared-list banner preview, shared-list email modal preview. The main Search grid is served by Algolia and inherits the filter at sync time.

### Title-based exclusion
Scrapers may define a module-level `EXCLUDE_TITLES` list of lowercased title strings to filter non-course products (subscription clubs, merchandise, gift cards, membership products, digital downloads, Thinkific subscriptions) from a provider's catalog. Apply the check as `title.lower().strip() in EXCLUDE_TITLES` in the source parsing function, before any detail-page fetch or further processing. Filter at both pass 1 (listing) and pass 2 (detail) for two-pass scrapers.

Reference: `scraper_altus.py` has `EXCLUDE_TITLES = ["altus mtn club", "altus mountain club"]` to skip the Thinkific subscription product, which was appearing with wrong price ($225) and a fabricated date.

### Date extraction must be scoped (required)
Scrapers that parse course dates from provider HTML pages must scope regex matching to schedule-like containers. Never run date regexes against the entire `soup.get_text()` — doing so pulls stray dates from footers, copyright notices, testimonials, Thinkific membership terms, "last updated" timestamps, and unrelated blog content, producing fabricated course dates (e.g. Altus MTN Club was assigned a fake Aug 20 2026 date before this rule was enforced).

Required scoping heuristic:
- Only extract dates from elements whose `class` or `id` matches the regex `schedule|dates|upcoming|session|availability|calendar` (case-insensitive)
- Or from siblings following an `h2/h3/h4` whose text matches the same pattern
- If no schedule container is found, treat the course as `custom_dates=True` (flex-date row)
- This rule applies to BOTH Pass 1 detail-page checks (Rezdy/Checkfront/etc.) AND Pass 2 WordPress/HTML schedule parsing

Reference implementation: `extract_schedule_text(soup)` in `scraper_altus.py` — replicate in other scrapers that parse schedules from HTML. The optional-year fallback in `parse_wp_dates` (defaults to current year, bumps to next year if past) amplifies this bug if unscoped, so the scoping rule is a hard requirement, not a nice-to-have.

## Architecture

### Scraping Pipeline

Each standalone scraper (`scraper_{id}.py`) follows the same flow:

1. **Fetch** listings from provider (REST API, HTML scraping, or Playwright for JS-rendered pages)
2. **Normalise** location via `location_mappings` table → Claude (suggestions queued to `pending_location_mappings` for admin review)
3. **Generate** stable IDs: `{provider_id}-{date_sort}-{title_hash}` (V2 — no activity segment)
4. **Upsert** to Supabase `courses` table (no `activity`, `activity_raw`, `badge`, or `badge_canonical` fields)
5. **Generate summaries** via `generate_summaries_batch` → Haiku produces `display_summary` + `search_document`
6. **Log intelligence** via `log_availability_change` + `log_price_change` (append-only on change)
7. **Validate** via `validate_provider.py` (auto-hide bad rows, auto-clear resolved user flags)

Key helper functions exposed by `scraper_utils.py`: `sb_get()`, `sb_upsert()`, `normalise_location()`, `parse_date_sort()`, `spots_to_avail()`, `stable_id_v2()`, `title_hash()`, `generate_summaries_batch()`, `log_availability_change()`, `log_price_change()`.

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
| Vancouver Mountain Guides | Zaui booking API (`vanmtnguides.zaui.net`) | `scraper_vanmtnguides.py` (+ `scraper_zaui_utils.py` helper) — **grouped scraper**, see below | — |
| Vibe Backcountry | FareHarbor External API v1 (`fareharbor.com/api/external/v1/companies/vibebackcountry`) | `scraper_vibe_backcountry.py` — first FareHarbor adapter | — |

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

Key Supabase tables: `courses` (listings), `location_mappings` (location normalisation rules), `pending_location_mappings` (Haiku suggestions awaiting admin review), `location_flags` (unresolved locations for review), `notifications` (watchlist subscriptions), `reports` (user course reports), `scraper_run_log` (course count per provider per run). Legacy tables `activity_mappings`, `pending_mappings`, `activity_labels` persist for now but are no longer written or read by any running code; they drop at V2 Phase 7 cutover.

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
| `sb_upsert` | `(table: str, rows: list) -> None` | POST rows with `Prefer: resolution=merge-duplicates`. Internally groups rows by keyset and POSTs one request per group — PostgREST (PGRST102) rejects bulk payloads with differing keysets, and scrapers intentionally omit `location_canonical` when `normalise_location()` returns None (see "Never pass `location_canonical: None`" rule below). Callers never need to pre-sort or pad rows. |
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
| `normalise_location` | `(raw: str, mappings: dict) -> Optional[str]` | Four-tier resolution: exact match → substring → Haiku with structural validation → None. Haiku responses matching `{"city": "...", "province": "XX"}` with province `^[A-Z]{2}$` are written directly to `location_mappings` (live) and returned. Malformed/null Haiku responses and API failures queue to `pending_location_mappings` and return `None`. Callers must omit `location_canonical` from the upsert payload when this returns `None` — see the caller contract in the Location mapping policy section. |

#### Claude AI

| Function | Signature | Description |
|----------|-----------|-------------|
| `claude_classify` | `(prompt: str, max_tokens: int = 256) -> dict` | Call Claude Haiku, return parsed JSON. Returns `{}` on failure. |
| `generate_summaries_batch` | `(courses: list, provider_id: str = None) -> dict` | Batch-generate two-field summaries (Phase 1 V2): `display_summary` (user-facing) + `search_document` (Algolia keywords). Input: list of `{id, title, description, provider}`. Returns `{course_id: {"summary": str, "search_document": str}}`. Internally upserts both fields to `course_summaries` table. `provider_id` param used for upsert; falls back to each course dict's `provider_id` key. Processes in batches of 12 with single retry on failure. |

#### Dates & IDs

| Function | Signature | Description |
|----------|-----------|-------------|
| `parse_date_sort` | `(date_str: str) -> Optional[str]` | Extract `YYYY-MM-DD` from various date string formats. |
| `is_future` | `(date_sort: Optional[str]) -> bool` | Returns `True` if date is today or later (or unparseable). |
| `title_hash` | `(title: str) -> str` | Stable 8-char md5 hash of stripped lowercase title. Single source of truth for title hashing across `stable_id_v2`, log functions, and Algolia objectIDs. |
| `stable_id_v2` | `(provider_id: str, date_sort: Optional[str], title: str) -> str` | V2 stable course ID: `{provider}-{date}-{title_hash_8}` or `{provider}-flex-{title_hash_8}`. No activity segment. |

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
- **Haiku batching**: `generate_summaries_batch` processes 12 courses per Claude call with single retry on failure. Internally upserts both `display_summary` and `search_document` to `course_summaries` table (Phase 1 V2). Returns `{course_id: {"summary": str, "search_document": str}}`. All 14 scrapers write both fields to the courses upsert payload — `search_document` goes live immediately at scrape time, no admin approval needed.
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

### Hybrid platform pattern (booking system + own website)

Many providers split their catalogue between a **booking platform** (Rezdy /
Checkfront / Zaui) for transactional, dated products and their **own marketing
website** (WordPress / Squarespace) for inquiry-based programs that don't have
fixed dates published. A single scraper covers both with two passes.

**When to use it:** if the booking-platform storefront returns suspiciously few
products relative to the provider's stated offerings (e.g. a guide service
advertising rock + ice + alpine + AST + ski touring but the Rezdy storefront
only lists 11 ski-focused items), assume the rest is on the marketing website
and add Pass 2.

**Reference implementations:**
- `scraper_altus.py` — Rezdy + WordPress (altusmountainguides.com). Pass 2
  crawls listing pages (`/mountaineering-courses`, `/climbing-courses`,
  `/climbing-trips`) and follows links to detail pages.
- `scraper_cloud_nine_guides.py` — Rezdy + Squarespace (cloudnineguides.com).
  Pass 2 uses a hardcoded `WEBSITE_PROGRAM_URLS` list because Squarespace
  exposes no clean nav-discoverable listing pages — every program lives at
  a top-level slug with no /category/ prefix.

**Pass 2 dedup contract:** before emitting a website row, check if Pass 1
already produced a row with the same title (or title containing a known
overlap keyword from a `PASS2_TITLE_SKIP_KEYWORDS` list). Pass 1 wins because
it has real dates and availability; Pass 2 is the inquiry-only safety net.

**Squarespace-specific notes:**
- No standard listing-page hierarchy — `/category/` URLs don't exist
- No discoverable sitemap.xml in many cases — use Google web-search probe
  (`site:provider.com`) to enumerate program URLs at build time
- Program slugs are arbitrary (e.g. `/c9g-day-rock-climbing-experience`,
  `/wicked-wanda-wi4-65m`) — never try to derive them from a URL pattern
- Hardcode the discovered URL list at module-level. Re-probe annually or
  when the provider adds a new program; the list will rarely churn.

**WordPress-specific notes:**
- Listing pages exist at predictable category URLs (e.g. `/courses`,
  `/trips`, `/mountaineering-courses`) — discoverable via nav or sitemap
- Detail pages have h1 + description paragraphs + price text
- Apply the **CLAUDE.md hard date-scoping rule** to any HTML date
  extraction: only run regex against containers whose class/id matches
  `schedule|dates|upcoming|session|availability|calendar`, never against
  `soup.get_text()`

**URL drift detection (for hardcoded URL lists):**
Scrapers with hardcoded program URL lists (`scraper_yamnuska.py`,
`scraper_cloud_nine_guides.py`) call `detect_url_drift()` from
`scraper_utils.py` at the end of `main()`. The helper fetches the provider
homepage, extracts every `<a href>` that matches a per-scraper
`url_pattern` regex (and doesn't match `exclude_pattern`), compares to the
known URL set, and INSERTs any new findings into `provider_url_drift`
(idempotent via unique constraint on `(provider_id, url)`). Findings
surface in the admin Pipeline tab → URL drift section for review.
Auto-discovery scrapers (Rezdy / Checkfront / Zaui APIs, WordPress nav
crawlers) don't need this — they pick up new programs automatically.

```python
from scraper_utils import detect_url_drift

detect_url_drift(
    provider_id="yamnuska",
    homepage_url="https://yamnuska.com",
    known_urls=set(PROVIDER["courses"]),
    url_pattern=re.compile(r"yamnuska\.com/(avalanche-courses|mountaineering|...)/[^?#]+"),
    exclude_pattern=re.compile(r"/(about|contact|cart|wp-content)"),
)
```

### Grouped scraper pattern

Some providers (Zaui, Checkfront variants) expose so many activity IDs that a single run would exceed GitHub Actions step timeouts or burn through Claude summary budget in one go. These use a **grouped** scraper pattern: the activity list is deterministically partitioned into N interleaved groups (typically 4), and each run processes only one group via `--group N` (0-indexed). Full catalog coverage requires N runs back-to-back.

**Reference implementation:** `scraper_vanmtnguides.py` + `scraper_zaui_utils.py` — Vancouver Mountain Guides (Zaui API, `vanmtnguides.zaui.net`). Activities are split into 4 interleaved groups (0/1/2/3) by index modulo. Invoked as `python scraper_vanmtnguides.py --group 0` etc.

**Current production state:** `scraper-all.yml` runs VMG with `--group 0` only — groups 1/2/3 are not yet wired in. This means:
- Each VMG run upserts ~25% of the full provider catalog
- `scraper_run_log.course_count` for VMG reflects one group per run, not the full catalog
- The Providers tab will **always** show a yellow `⚠ ~50%+ drop ↗` badge for VMG (Initiative 7's >30% drop detection compares last two `scraper_run_log` rows, which are non-overlapping group partitions) — this is expected, not a real data quality issue
- **VMG drop badges should be ignored** until `scraper-all.yml` is updated to run all 4 groups sequentially (e.g. 4 named steps `scraper_vanmtnguides.py --group 0/1/2/3` followed by a single `validate_provider.py vanmtnguides`)

The 30% drop detection is structurally incompatible with grouped scrapers — any provider that ships as grouped will emit the same false positive until all groups run in one scheduled window. When wiring a new grouped scraper, either run all groups in sequence within `scraper-all.yml` or explicitly exclude the provider from count-drop signal detection.

### validate_provider.py

Post-scrape validation script. Runs after any provider scraper completes. Read-only except for flagging.

**Usage:** `python validate_provider.py <provider_id>`

**Behaviour:**
1. Resets all `auto_flagged` rows for this provider (clean slate)
2. Fetches all courses for the provider
3. Runs 6 checks (see below — activity mapping check retired)
4. Auto-clears resolved user report flags
5. Logs course count to `scraper_run_log`
6. Writes email-only warnings to `validator_warnings` (deletes existing rows for the provider first, then inserts fresh). No email is sent — the admin Flags tab replaces the old email report.

**5 checks — AUTO-HIDE vs EMAIL ONLY:**

| Check | AUTO-HIDE (sets `auto_flagged=true`) | EMAIL ONLY |
|-------|--------------------------------------|------------|
| 1. Summary quality | Duplicate summary bleed (identical text across different titles — second occurrence auto-hidden with `flag_reason='summary_bleed'`). Null summary is auto-filled inline via `generate_summaries_batch()` with a title-only seed; courses that still have no summary after backfill surface in the Summary Review tab but are **not** auto-hidden | — |
| 2. Price sanity | Zero or negative price → `flag_reason='invalid_price'`. Auto-hide on first detection. Courses with a `course_price_log` row 24+ hours old get `flag_reason` upgraded to `invalid_price_escalated`, which the Flags tab Price escalations section renders with provider-email copy (Initiative 4) | — (null_price and price_outlier warnings retired in Initiative 4 — permanently removed, no median comparison anywhere) |
| 3. Date sanity | Past date with `active=true` → `flag_reason='past_date'`; >2 years in the future → `flag_reason='future_date'`. Both auto-hide on first detection. Courses with a `course_availability_log` row 24+ hours old get their `flag_reason` upgraded to the `_escalated` suffix (`past_date_escalated` / `future_date_escalated`) which the Flags tab Date escalations section renders with provider-email copy. `custom_dates=true` and `date_sort IS NULL` are a HARD skip for both branches | — (future_date warning retired in Initiative 5 — replaced by auto-hide + escalation) |
| 4. Availability | — | Null avail, all-sold warning |
| 5. Duplicates | All but first occurrence of same title+date. No whitelist layer (retired in Initiative 6 — duplicates are always scraper bugs, resolution is to fix the scraper). Admin suppression still respected | — |

**Course count check (retired from validator in Initiative 7):** the >30% course-count-drop signal is now computed client-side on the Providers tab from `scraper_run_log`. Validator no longer writes `count_drop` rows to `validator_warnings`.

**Exceptions:** Summary bleed skips any group whose `(provider_id, md5(summary))` is present in `validator_summary_exceptions` (admin-reviewed). The inline summary backfill is framed as a safety net — title-only seed is acceptable because scrapers strip the real description before upsert. Price check has no exception layer — the old `validator_price_exceptions` table and hardcoded Logan/Expedition/Traverse skip list were retired in Initiative 4 (the zero/negative condition has no legitimate exceptions). Duplicate check has no exception layer either — the old `validator_whitelist` load was retired in Initiative 6 (duplicates are always scraper bugs); only `validator_suppressions` can still short-circuit a group via the admin "Clear all" button.

### Validator priority stack
Admin decisions always take precedence over automated validator rules.
The validator checks admin decisions first in this order before running
any keyword or automated checks:

1. `validator_suppressions` — explicit admin "ignore this" decision.
   Two match modes. Title-scoped (the default / legacy): matches on
   `(provider_id, title_contains substring, flag_reason category)` — used
   by duplicate / summary flows. Course-id-scoped (Initiative 5, extended
   in Initiative 4): if the suppression row's `course_id` column is set,
   matching requires exact `(course_id, flag_reason category)` — used by
   the Flags tab "Clear escalation" button on both date and price
   escalations so a stale course_id can be retired precisely without
   over-suppressing other rows sharing the same title. Highest priority.
2. `validator_summary_exceptions` — admin-saved summary text via the
   Summary Review tab. Keyed on `(provider_id, md5(summary))`. If any
   course's current summary hashes to an exception row, skip the bleed
   check for the whole collision group. One admin save clears both
   sides of a bleed pair on the next validate run.

Two layers only. The validator is a safety net for unreviewed courses only.
Once an admin has made any explicit decision about a course, the validator
must respect it permanently. Automated checks only fire when no admin
decision exists for that course and check type. (The legacy
`activity_mappings` branch was removed alongside the activity-mismatch
check in Initiative 1; the `validator_price_exceptions` branch was removed
in Initiative 4 when the outlier check itself was deleted; the
`validator_whitelist` branch was removed in Initiative 6 when the duplicate
check was simplified to a pure scraper-signal — all three tables persist
in Supabase, orphaned, drop at V2 Phase 7.)

### How to add a new provider — checklist

1. **Create `scraper_{id}.py`** importing from `scraper_utils`:
   - Provider config dict at the top (id, name, website, location)
   - Provider-specific location map if needed
   - HTML parsing functions specific to the provider's website
   - `main()` function that: updates ratings → loads location mappings → scrapes → normalises locations → generates summaries → deduplicates → upserts → logs availability + price
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
   - INSERT provider row into `providers` table (with `active=false`)
   - INSERT any known `location_mappings` for the provider's locations

5. **Use two-pass pattern** if the listing page doesn't contain full detail data (dates, prices). See `scraper_aaa.py` + `scraper_aaa_details.py` as reference.

## GitHub Actions workflow structure

### Individual provider workflows
One file per provider at `.github/workflows/scraper-{id}.yml`. All use `workflow_dispatch` trigger only (manual). Each runs the standalone scraper then validate_provider.py.

### Master workflow — scraper-all.yml
- **Triggers:** `schedule` (cron `0 */6 * * *` — every 6 hours) + `workflow_dispatch`
- Installs all dependencies including Playwright + Chromium + `algoliasearch`
- One named step per provider with `continue-on-error: true`
- A `Validate {Provider}` step after each scraper step
- Final step: `python algolia_sync.py --skip-settings` — syncs all V2 courses to Algolia after every run
- Uses 7 secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `RESEND_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`, `ALGOLIA_APP_ID`, `ALGOLIA_ADMIN_KEY`

### Validate workflow — validate-provider.yml
- **Trigger:** `workflow_dispatch` with required `provider_id` input
- Runs: `python validate_provider.py ${{ github.event.inputs.provider_id }}`

### Deploy workflow — deploy-functions.yml
- **Trigger:** push to `supabase/functions/**`
- Deploys all edge functions with `--no-verify-jwt`

### Discovery workflow — discover-providers.yml
- **Triggers:** `schedule` (cron `0 6 * * 0` — every Sunday 06:00 UTC) + `workflow_dispatch`
- Runs: `refresh_discovery_cloud.py` then `discover_providers.py`
- Dependencies: `requests` only (no beautifulsoup4/playwright needed)
- Uses 4 secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`

### Refresh cloud workflow — refresh-cloud.yml
- **Trigger:** `workflow_dispatch` only (manual)
- Runs: `python refresh_discovery_cloud.py`
- Dependencies: `requests` only
- Uses 2 secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`

### discover_providers.py

Automated provider discovery script. Searches the web for Canadian backcountry guide companies using terms from `discovery_cloud` table, applies tiered cost controls, learns skip patterns from pipeline history, and inserts candidates to `provider_pipeline`.

**Usage:** `python discover_providers.py` or `python discover_providers.py --dry-run`

**Flags:** `--max-queries N` (default 100) caps search phase queries. `--max-candidates N` (default 50) caps analysis phase candidates.

**Tiered cost controls:**
| Tier | When | Cost | What |
|------|------|------|------|
| 1 | Post-search | Free | `SKIP_DOMAINS` (social media, aggregators, travel platforms) + `SKIP_URL_KEYWORDS` (checked against domain/URL only, never provider name) |
| 2 | Post-search | Free | Skip pattern learning from `provider_pipeline` rows with `status='skip'` — domains + keywords extracted from notes (2+ skip rows must mention same keyword, min 4 chars) |
| 3 | Search phase | ~$0.001/query | Haiku web_search calls, capped by `--max-queries` |
| 4 | Analysis phase | ~$0.005/candidate | Full Haiku analysis + Google Places, capped by `--max-candidates`. Normal-priority candidates sorted first; low-priority (review_count < 5) fills remaining slots |

**Flow:**
1. Load active terms from `discovery_cloud` table
2. Load known domains from `providers`, `provider_pipeline`, `provider_submissions`
3. Load skip patterns from `provider_pipeline` (status='skip')
4. Generate + cap search queries (activity terms x location terms)
5. Search phase: Haiku web_search → Tier 1 filter → Tier 2 filter → collect candidates
6. Analysis phase: sort by priority (normal first, low-review last) → analyse top N (Haiku for name/location/complexity/priority/notes + **deterministic platform detection** via `detect_platform()` — fetches homepage HTML and signature-matches against `PLATFORM_SIGNATURES`, overrides Haiku's platform guess when a match is found) → Google Places (null-safe review_count) → insert to pipeline
7. Increment `hit_count`/`skip_count` on contributing `discovery_cloud` terms
8. Stamp `last_used_at` on all cloud terms that generated queries
9. Log per-tier cost breakdown

**Null-safe review_count:** `review_count < 5` is a soft signal (low priority), not a hard skip. `review_count is None` (Places API failure) → keep candidate at normal priority.

**Pipeline columns used by discovery:**
- `discovered_by` (text) — `'manual'` (default/null for admin-added) or `'auto'` (script-found)
- `discovery_query` (text) — which search query found this provider (debugging)

**Rate limiting:** ~1.5s between search queries, ~2s between analysis calls.

**Cost:** Target under $0.50/week with default caps (100 queries + 50 candidates).

### refresh_discovery_cloud.py

Builds the `discovery_cloud` table from live course and provider data. Runs before `discover_providers.py` in the weekly cron.

**Usage:** `python refresh_discovery_cloud.py` or `python refresh_discovery_cloud.py --dry-run`

**Flow:**
1. Load all active course titles + provider_ids from `courses`
2. Load provider locations from `providers` + `location_mappings`
3. Extract activity bigrams from course titles (must appear across 2+ providers)
4. Extract high-signal single keywords (activity nouns across 3+ providers)
5. Extract location terms (provinces from provider locations + base regions)
6. Upsert `auto` terms to `discovery_cloud` — never overwrites `manual` entries or `active=false` admin decisions

**Stopword filtering:** Filler bigrams ("day 1", "per person", "full day") and common stop words are excluded. The script's `STOP_BIGRAMS` and `STOP_WORDS` sets handle this.

**Search surface grows automatically:** As new providers and courses are added, the refresh script discovers new bigrams and location terms. Manual terms added via the admin Settings tab are preserved and never overwritten.

### Algolia sync workflow — sync-algolia.yml
- **Trigger:** `workflow_dispatch` only (manual)
- Runs: `python algolia_sync.py`
- Dependencies: `requests algoliasearch`
- Uses 4 secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ALGOLIA_APP_ID`, `ALGOLIA_ADMIN_KEY`
- Hardcoded: `ALGOLIA_INDEX_NAME=courses_v2`

### algolia_sync.py

Pushes V2 courses from Supabase to Algolia index. Reads all active, non-flagged V2 courses with provider join, maps to Algolia records, configures index settings, and pushes via `save_objects` (upsert by objectID). Idempotent — safe to re-run.

**Usage:** `python algolia_sync.py` or `python algolia_sync.py --dry-run` or `python algolia_sync.py --skip-settings`

**Flags:** `--dry-run` (log records, no push), `--skip-settings` (skip index config, just push records)

**Env vars:** `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ALGOLIA_APP_ID`, `ALGOLIA_ADMIN_KEY`, `ALGOLIA_INDEX_NAME` (default: `courses_v2`)

**Supabase query:** `courses?active=eq.true&flagged=not.is.true&auto_flagged=not.is.true&activity_canonical=is.null` with provider join.

**Algolia record schema:** `objectID` (courses.id), `title`, `search_document`, `summary`, `activity`, `location_canonical`, `location_raw`, `date_sort` (unix timestamp), `date_display`, `duration_days`, `price`, `currency`, `avail`, `badge`, `image_url`, `booking_url`, `custom_dates`, `provider_id`, `provider_name`, `provider_rating`, `provider_logo_url`.

**Index settings:** Searchable attributes (ordered): `title`, `search_document`, `provider_name`, `location_canonical`. Facets: `activity`, `location_canonical`, `provider_name`, `avail`. Custom ranking: `asc(date_sort)`. Flex-date courses use far-future timestamp (2100-01-01) to sort to end.

**Synonyms:** skiing/backcountry skiing/ski touring/splitboarding, climbing/rock climbing/sport climbing/trad climbing, hiking/backpacking/trekking, mountaineering/alpine climbing/glacier travel, avalanche safety/AST/AST 1/AST 2/avy, BC/British Columbia, AB/Alberta.

### Secrets used by all workflows
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `RESEND_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`, `ALGOLIA_APP_ID`, `ALGOLIA_ADMIN_KEY`

## Admin page

- **URL:** `backcountryfinder.com/admin` (static `admin.html` at repo root)
- **Auth:** Supabase Auth, email + password. Only `luke@backcountryfinder.com` is allowed — any other account is auto-signed-out. Auto-logout after 30 minutes of inactivity.
- **All writes go through edge functions** with `Authorization: Bearer {session.access_token}`. The edge function verifies the JWT and checks admin email before touching any table.
- **Reads** use the Supabase publishable (anon) key directly.
- **Security:** `<meta name="robots" content="noindex, nofollow">` + `robots.txt` has `Disallow: /admin`.

### Tabs
1. **Providers** — stats row (providers / courses / auto-hidden / user flags), provider table with **Scraper** column (three-state classifier: green platform-name badge like `rezdy` / `zaui` / `checkfront` / `woocommerce` / `squarespace` when the provider uses reusable adapter code we can clone for similar providers; amber `bespoke` badge when the scraper is hand-written for that specific site and not reusable — covers custom HTML, Rails, custom WordPress, Playwright-specific parsers, and any site whose `booking_platform` is `'wordpress'`, `'unknown'`, `'custom'`, or null; em-dash when no `scraper_run_log` row exists yet. Reusable set is the hardcoded `REUSABLE_PLATFORMS` const in admin.html — `wordpress` is deliberately excluded because each WP site needs its own parser. When adding a new reusable platform adapter, update both `REUSABLE_PLATFORMS` and `PLATFORM_REFERENCE` in `copyPipelinePrompt`), active toggle, last run, course count, status badge, per-provider "Run" and "Validate" buttons (Validate calls `admin-trigger-scraper` with `workflow_id='validate-provider.yml'` + `inputs={provider_id}`), and "Run all" button. The status cell also surfaces the **Initiative 7 count-drop signal** — when the last two `scraper_run_log` rows for a provider show a course count drop of >30%, a yellow `⚠ {pct}% drop ↗` badge renders alongside the normal status badge, linking directly to that provider's GitHub Actions workflow (`actions/workflows/scraper-{id}.yml`). Computed client-side in `renderProvidersTable()` from the `prevRun` / `lastRun` data attached in `loadProvidersTab()` — no server-side write, no `validator_warnings` row. Clears automatically on the next healthy scrape. Column headers are clickable to sort (Provider / Scraper / Last run / Courses / Status), default is alphabetical by name.
2. **Location Mappings** — pending + approved location mappings with inline Edit and Delete. Header has an **"Add mapping"** button that opens an inline form (Location raw + Location canonical text inputs) and POSTs directly to `/rest/v1/location_mappings` with the authenticated session token. Approved rows edit both `location_raw` and `location_canonical`. Course counts are on-demand via a "Load counts" button — one `countRows()` query per unique `location_canonical`, results cached for the session. Column headers (Raw / Canonical / Courses / Created) are clickable to sort ascending/descending; Courses is only sortable after counts are loaded. Default is alphabetical by `location_raw`.

### Sortable headers (shared pattern)
Two tables (Providers, Location Mappings) use a shared sort helper in `admin.html` (`cmpValues`, `sortIndicator`, `sortableHeader`, `toggleSortState`). Clicking a header toggles asc/desc on that column or switches to a new column (asc first). Nulls always sink to the bottom regardless of direction. Text sorts via `.toLowerCase().localeCompare()`. Numeric sorts cast via `Number(...)`.
3. **Summary Review** — exception inbox (Initiative 3). Three row sources merged client-side: (a) `auto_flagged=true` + `flag_reason LIKE 'summary_bleed%'` — the validator detected identical summary text across different titles and auto-hid the second occurrence, (b) `flagged=true` + `flagged_reason='bad_description'` — user reports, (c) `summary IS NULL` + `active=true` — generation failures (the validator's inline `generate_summaries_batch()` backfill could not produce text). Two fields per row: **Card description** (editable textarea, maps to `courses.summary`) and **Search document** (read-only textarea, maps to `courses.search_document`). Two buttons: **Save** calls `admin-save-summary` edge function which writes the text, clears `auto_flagged` and/or `flagged` on the course, and inserts a `(provider_id, md5(summary), reason)` row into `validator_summary_exceptions` so the validator skips this summary text on future runs. **Regenerate** calls `admin-regenerate-summary` with the title as the description seed (scrapers strip description before upsert) and populates the textarea — does not save; admin must click Save to commit. The old `approved=false` queue from `course_summaries` is bypassed; scraper-generated summaries go live immediately via direct `courses.summary` writes at scrape time. The legacy `admin-approve-summary` / `admin-reject-summary` edge functions still exist but are not called by the current UI.
4. **Flags** — Stats row (User reports / Auto-hidden / Warnings). Header buttons: "Reload flags" (re-runs `loadFlagsTab`), "Re-validate all ↗" (loops `admin-trigger-scraper` over all active providers with 500ms spacing), "Copy fixable flags prompt" (bundles wrong_price, wrong_date, bad_description, sold_out flags for Claude Code). User reports section (only `button_broken` and `other` get a Mark resolved button — `bad_description` is handled in Summary Review, `wrong_date`/`wrong_price`/`sold_out` auto-clear via validator when the issue resolves). Validator auto-flags section is **grouped by `(title, flag_reason)`** so identical rows collapse to one row with an occurrences badge. `summary_bleed`, pre-escalation `past_date` / `future_date` / `invalid_price`, and all `*_escalated` auto-flags are filtered out of this section's fetch — they live in Summary Review or the escalation sub-sections below. **Duplicate groups are read-only (Initiative 6)** — each row shows one `scraper_{provider_id}.py ↗` button per provider_id linking directly to the offending scraper file on GitHub (`https://github.com/lukeram-3420/backcountryfinder/blob/main/scraper_{provider_id}.py`), plus a Clear all button that writes a title-scoped suppression as a last resort. No Diagnose or Whitelist button — the resolution is always "open the scraper, fix the iteration logic, re-run." A **Date escalations** sub-section (Initiative 5) renders courses with `flag_reason IN ('past_date_escalated','future_date_escalated')` — per row: booking URL, copyable provider-email body, Clear (writes course-id-scoped suppression). A **Price escalations** sub-section (Initiative 4) renders `flag_reason='invalid_price_escalated'` — same card layout: current price, booking URL, copyable email body, Clear. A **Warnings** sub-section surfaces the `validator_warnings` table (email-only issues persisted by `validate_provider.py`): grouped by `(title, check_type)`, actions per type — `null_avail` → View (opens booking URL); `all_sold` → informational only. The Warnings section no longer surfaces price rows (Initiative 4 retired `price_outlier`/`null_price`) nor count-drop rows (Initiative 7 moved `count_drop` to the Providers tab as a yellow badge). Remaining `validator_warnings` check_types are `null_avail` + `all_sold`.
5. **Audit Log** — last 100 rows of `admin_log` with search filter.
6. **Pipeline** — three stacked sections sharing one tab. **Top: Discovery Cloud** — two lists (activity terms + location terms) that drive the weekly automated provider discovery search queries. Each term shows a weight bar, quality indicator (X found / Y skipped — warning at >80% skip rate with 5+ total), last-used date, and a single **Remove** button (toggles to **Restore** once removed). Remove is a soft-delete — it sets `discovery_cloud.active = false` rather than DELETEing the row. This matters because `refresh_discovery_cloud.py` preserves `active=false` on every run, so a removed term stays blocked permanently instead of being re-added next Sunday; a hard DELETE would re-appear on the next refresh. Manual **Add term** POSTs `{term, type, weight: 1, source: 'manual', active: true}` directly to `/rest/v1/discovery_cloud` with the authenticated session token. Populated by `refresh_discovery_cloud.py`, consumed by `discover_providers.py`. **Middle: Provider pipeline** — onboarding tracker backed by `provider_pipeline` table. Header has an **"Add provider"** button that opens an inline URL-only form: `admin-analyse-provider` runs Haiku web_search + Google Places lookup, slugifies the returned name, then POSTs to `provider_pipeline` (status='candidate', `id` = slug). Each non-live row (candidate/scouted/scraper_built) has a **"Copy prompt ↗"** button that copies a Claude Code instruction to the clipboard for building the scraper. **Client-side hide of already-live providers:** on every tab load, `loadPipelineTab` fetches active providers alongside pipeline rows and builds `activeProviderKeys = {domains, names}`. `renderPipelineTable` hides any pipeline row whose normalised website domain or lowercase name is in those sets. Domain comparison uses `domainOf()` which normalises via lowercase → strip `https?://` → strip `www.` → strip trailing `/`. No PATCH writes happen during display — the pipeline's own `status` column is not updated by the UI's filter logic; the status PATCH only fires via the inline Edit form. Excludes `status='skip'` from display. Columns: Name (linked to website), Location, **Rating** (`★ X.X (N)` / `★ —` / `—`), Platform, Complexity, Status (coloured badge: candidate=grey, scouted=blue, scraper_built=yellow, live=green, skip=faded), Priority (1/2/3), Notes (truncated to ~60 chars with full-text tooltip), Edit + Copy prompt. Inline edit lets you change status/platform/priority/notes plus the Google enrichment fields (`google_place_id`, `rating`, `review_count`). Name/Platform/Status/Priority headers are sortable. Pipeline `id` is a text slug — onclick handlers must quote it (`editPipelineRow('${id}')`) or it will be evaluated as a global variable. **Bottom: URL drift** — surfaces program URLs detected on a provider homepage that aren't in the scraper's hardcoded list. Only populated for scrapers with hardcoded URL lists (currently `scraper_yamnuska.py` and `scraper_cloud_nine_guides.py` — see `detect_url_drift()` in `scraper_utils.py`). Rows grouped by provider_id, each with **Add** (copies the URL with paste instructions for the scraper file, marks `reviewed=true, action='added'`) and **Reject** (`reviewed=true, action='rejected'`) buttons. Reviewed rows stay in the table but don't re-surface; the unique constraint on `(provider_id, url)` makes re-detection idempotent.
7. **Settings** — Static reference for the canonical location format (`City, Province`). Discovery Cloud UI moved to the Pipeline tab.

### Admin-facing tables (create in Supabase if not already)
- `admin_log` — `id bigserial, user_email text, action text, detail jsonb, created_at timestamptz default now()`
- `pending_mappings` — retired; scraper-side Claude activity classification no longer runs, so this table receives no new rows. Drops at V2 Phase 7.
- `pending_location_mappings` — pending location mapping suggestions (columns: `id, location_raw, suggested_canonical, reviewed bool, created_at`)
- `course_summaries` — unique on `(provider_id, title)`. Columns: `id, provider_id, title, course_id, summary, description_hash, approved bool, approved_at, pending_reason, created_at`
- `validator_price_exceptions` — **orphaned in Initiative 4**, drops at V2 Phase 7. The outlier check that consumed this table was deleted entirely; the table still exists in Supabase but no code reads or writes it. Don't add code that references it.
- `validator_warnings` — persists email-only validator issues (replaces the old email report). Columns: `id bigserial, provider_id text not null, course_id text, title text, check_type text not null, reason text not null, run_at timestamptz default now()`. `check_type` is one of: `null_avail`, `all_sold`. `validate_provider.py` deletes all rows for the provider at the start of each run then writes fresh warnings at the end. Consumed by the Flags tab Warnings subsection in admin. (`summary_empty` retired in Initiative 3; `future_date` retired in Initiative 5; `null_price` and `price_outlier` retired in Initiative 4; `count_drop` retired in Initiative 7 — count-drop surfaces as a client-side yellow badge on the Providers tab now. Retirements are replaced by active flows in the Summary Review tab, the Flags-tab escalation sub-sections, and the Providers-tab scraper-health signal respectively.)
- `validator_whitelist` — **orphaned in Initiative 6**, drops at V2 Phase 7. The duplicate check that consumed this table now auto-hides without any whitelist layer (duplicates are always scraper bugs — resolution is to fix the scraper, not whitelist the title). The table still exists in Supabase but no code reads or writes it. Don't add code that references it.
- `validator_suppressions` — explicit admin "ignore this" entries. Columns: `id bigserial, provider_id text, title_contains text, course_id text, flag_reason text not null, created_at timestamptz default now()`. `course_id` was added in Initiative 5 as a nullable column. Title-scoped rows (course_id IS NULL) match on `title_contains` substring + `flag_reason` category — populated by the Flags tab's "Clear all" action on auto-flag groups. Course-id-scoped rows (course_id set) match on exact `course_id + flag_reason` category — populated by the Flags tab's "Clear (suppress)" button on Date escalations (Initiative 5) and Price escalations (Initiative 4). **Consumed by `validate_provider.py`'s priority stack**: `is_suppressed()` checks both modes on every flag evaluation.
- `validator_summary_exceptions` — admin-reviewed summary text exceptions from the Summary Review tab (Initiative 3). Columns: `id bigserial primary key, provider_id text not null, summary_hash text not null, course_id text, reason text not null check (reason in ('summary_bleed','bad_description','generation_failed')), saved_at timestamptz default now(), unique (provider_id, summary_hash)`. Populated by `admin-save-summary` edge function on admin Save. **Consumed by `validate_provider.py` Check 1**: bleed detection skips any group whose `(provider_id, md5(summary_text))` is in this table — one admin save clears the whole collision group on the next run. Does NOT apply to the empty-summary backfill (that's idempotent by nature).
- `discovery_cloud` — search terms for automated provider discovery. Columns: `id bigserial, term text not null, type text not null ('activity'/'location'), weight integer default 1, active boolean default true, source text ('auto'/'manual'), last_used_at timestamptz, hit_count integer default 0, skip_count integer default 0, created_at timestamptz default now()`. Unique index on `(lower(term), type)`. Populated by `refresh_discovery_cloud.py`, consumed by `discover_providers.py`. Admin-editable in Settings tab.
- `provider_url_drift` — homepage-probe findings for scrapers with hardcoded URL lists (yamnuska, cloud-nine-guides). Columns: `id bigserial primary key, provider_id text not null, url text not null, link_text text, detected_at timestamptz default now(), reviewed boolean default false, action text, unique (provider_id, url)`. Populated by `detect_url_drift()` in `scraper_utils.py` at the end of each scraper run. Admin reviews unreviewed rows in the Pipeline tab → URL drift section: **Add** copies the URL with paste instructions and marks `action='added'`; **Reject** marks `action='rejected'`. The unique constraint means re-detection of an already-recorded URL is a no-op (idempotent).

### Admin edge functions (deployed via deploy-functions.yml)
All live in `supabase/functions/admin-*/index.ts`. Every one verifies the JWT, checks `user.email === 'luke@backcountryfinder.com'`, executes, then writes a row to `admin_log`.

| Function | Purpose |
|----------|---------|
| `admin-approve-location` | Insert into `location_mappings`, mark `pending_location_mappings.reviewed=true` |
| `admin-reject-location` | Mark `pending_location_mappings.reviewed=true` |
| `admin-update-location` | Update `location_mappings.location_raw` + `location_canonical` by id |
| `admin-delete-location` | Delete a `location_mappings` row by id (does not touch `courses`) |
| `admin-approve-summary` | **Legacy** — still deployed for backward compat, not called by the current UI. Originally approved a `course_summaries` row and patched matching `courses`. Retires at V2 Phase 7. |
| `admin-reject-summary` | **Legacy** — still deployed, not called by the current UI. Retires at V2 Phase 7. |
| `admin-regenerate-summary` | Call Claude Haiku for fresh two-field summary (`display_summary` + `search_document`). Used by the Summary Review tab's Regenerate button; also used by the Flags tab historically (Initiative 3 removed the Flags-tab caller, only Summary Review calls it now). Returns the fresh text without committing — caller must Save. |
| `admin-save-summary` | **Initiative 3** — the single Save path from the Summary Review tab. Input: `{course_id, summary, search_document?, reason}`. Computes `md5(summary.trim())` server-side. Patches `courses` with the new text, clears `auto_flagged/flag_reason/flagged/flagged_reason/flagged_note` on the course, and inserts a row into `validator_summary_exceptions` keyed on `(provider_id, summary_hash)`. Idempotent — unique-key conflicts are treated as success. Writes `admin_log`. |
| `admin-resolve-flag` | Clear user flag — only for `button_broken` / `other` reasons (400 otherwise) |
| `admin-clear-auto-flag` | Clear `auto_flagged` + `flag_reason` |
| `admin-toggle-provider` | Set `providers.active` and cascade to that provider's `courses.active`. Toggle OFF sets all courses to `active=false`. Toggle ON only restores courses where `avail != 'sold'` — preserves sold-out and notify-me courses. On ON it also flips matching `provider_pipeline` rows to `status='live'` (matched by normalised domain or lowercase name, same logic as the client-side hide in admin.html; skips rows already `live` or `skip`). On OFF it intentionally does NOT revert pipeline rows — once a provider has been onboarded it stays "live" in the pipeline even if temporarily disabled, so stale candidates don't resurface on the next weekly discover run. The admin UI's `toggleProvider` handler additionally dispatches `sync-algolia.yml` via `admin-trigger-scraper` after every successful toggle (in either direction) so `courses_v2` reflects the change within ~30 seconds instead of waiting for the next 6-hour `scraper-all.yml` tick. Failure of the sync dispatch is logged to the toast but does not fail the toggle. |
| `admin-analyse-provider` | Accepts `{url}`, calls Claude Haiku with `web_search` tool to derive `{name, location, platform, complexity, priority, notes}`, then enriches with Google Places `{google_place_id, rating, review_count}`. Used by Pipeline tab "Add provider" form. Falls back to URL-derived defaults on Haiku failure. **Platform is detected deterministically**, not trusted from Haiku: `detectPlatform(url)` fetches the homepage and signature-matches against `PLATFORM_SIGNATURES` (rezdy/checkfront/zaui/fareharbor/bokun/peek/thinkific/shopify/wix/squarespace/woocommerce/wordpress). First match wins; Haiku's platform guess is used only when detection returns `unknown`. The signature table mirrors `discover_providers.py` and `admin-detect-platform` — keep all three in sync when adding a platform. **Places result passes three validation checks before being accepted** (else all three Places fields are nulled): (1) name similarity ≥ 0.4 between Haiku-derived name and Places-returned name (alphanumeric-only char overlap), (2) `user_ratings_total` ≤ 2000, (3) `place_id` not already assigned to a different `provider_pipeline` row. Each rejection logs a reason. |
| `admin-detect-platform` | Accepts `{table, id, url}` where `table` is `'providers'` or `'provider_pipeline'`. Fetches the URL, matches against the same `PLATFORM_SIGNATURES` table used by `admin-analyse-provider`, PATCHes the target row's platform column (`providers.booking_platform` or `provider_pipeline.platform` — indirection in `PLATFORM_COLUMN` const), logs to `admin_log` with the matched evidence pattern. Returns `{platform, evidence}`. Writes the result even on `unknown` so the UI reflects "we tried and nothing matched" rather than leaving a stale value. Wired to the inline **Detect** button on the Pipeline tab Platform cell. The `providers` table path is supported but not currently surfaced in the UI — kept for future use and for any scripted backfill that wants to route through the edge function rather than direct REST. |
| `admin-trigger-scraper` | Call GitHub Actions `workflow_dispatches` — requires `GITHUB_TOKEN` secret in Supabase Edge Functions settings. Accepts `{workflow_id, inputs?}`; `inputs` is forwarded to `workflow_dispatch` (used for `validate-provider.yml` which requires `provider_id`). |

### Related one-offs
- `bootstrap_summaries.py` — deleted. Was a one-time migration that seeded `course_summaries` from existing `courses.summary` values. No longer needed.
- `course_summaries` dedup: unique constraint on `(provider_id, title)`; `description_hash` tracks when the underlying description changes so a stale approved summary can be flagged for review.
- `backfill_platforms.py` — one-shot backfill that imports `detect_platform` from `discover_providers.py` and walks `providers` + `provider_pipeline`, PATCHing rows whose platform column is null / empty / `'unknown'` / `'custom'`. Column indirection (`providers.booking_platform` vs `provider_pipeline.platform`) lives in `PLATFORM_COLUMN`. Safe to re-run — rows already resolved to a concrete platform are skipped. Supports `--dry-run` and `--table {providers,provider_pipeline,both}`. Needs `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`.

### Two-flag system reminder
- `flagged` + `flagged_reason` + `flagged_note` → user reports (set by `notify-report`, cleared by admin actions or validator auto-clear rules)
- `auto_flagged` + `flag_reason` → validator only (set + reset by `validate_provider.py`)
- Scrapers never touch either set.

## Filter behaviour

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
| id | text | V2 stable ID: `{provider_id}-{date_sort}-{title_hash}` or `{provider_id}-flex-{title_hash}`. V1 legacy: `{provider_id}-{activity}-{date_sort}-{title_hash}` |
| title | text | |
| provider_id | text | |
| badge | text | **Deprecated** — no longer written. Drops at V2 Phase 7. |
| activity | text | **Deprecated** — no longer written. Drops at V2 Phase 7. |
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
| activity_raw | text | **Deprecated** — no longer written. Drops at V2 Phase 7. |
| activity_canonical | text | V1 distinguishing column; V2 rows set it to NULL. Drops at Phase 7. |
| badge_canonical | text | **Deprecated** — no longer written. Drops at V2 Phase 7. |
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

### activity_mappings (retired)
Legacy table from the V1 activity-classification pipeline. No longer read or
written by any running code. Drops at V2 Phase 7 cutover alongside
`pending_mappings` and `activity_labels`.

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
| bad_data | boolean | default false — set to true by `log_price_change` when `price <= 0` (Initiative 4). Lets Phase 5 velocity-signal consumers filter out polluting zero/negative rows without re-deriving the condition at read time. |

Indexed on `(provider_id)` and `(title_hash)`. Append only when price changes. **Never truncate, delete, or run cleanup.** Consumed by `validate_provider.py`'s `load_price_escalation_candidates(provider_id)` (Initiative 4) which returns V2 course_ids reconstructed from `(provider_id, date_sort, title_hash)` for rows 24+ hours old — these upgrade zero/negative-priced courses to `invalid_price_escalated` in the Flags tab Price escalations sub-section.

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

### discovery_cloud
| column | type | notes |
|---|---|---|
| id | bigserial | primary key |
| term | text | not null — the search term (e.g. "backcountry skiing", "British Columbia") |
| type | text | not null — `activity` or `location` |
| weight | integer | default 1 — how many providers use this term |
| active | boolean | default true — admin toggle, inactive terms skipped by discovery |
| source | text | `auto` (refresh script) or `manual` (admin-added) |
| last_used_at | timestamptz | stamped by discover_providers.py when the term generates a search query |
| hit_count | integer | default 0 — cumulative: candidates from this term that passed to analysis |
| skip_count | integer | default 0 — cumulative: candidates from this term filtered before analysis |
| created_at | timestamptz | default now() |

Unique index on `(lower(term), type)`. Populated by `refresh_discovery_cloud.py`, consumed by `discover_providers.py`. Admin-editable in the Settings tab of admin.html. Quality warning shown in admin when `skip_count > 80% of (hit_count + skip_count)` and total >= 5.

### Intelligence logging tables — append only
`course_availability_log` and `course_price_log` are sacred append-only tables that form the historical intelligence asset of the platform. Never truncate, delete rows from, or run cleanup operations on these tables under any circumstances. New rows are added only when values change. These tables are permanently excluded from all maintenance and cleanup operations.

## V2 Migration — Implemented Changes

V2 is an incremental migration on the live system. V1 and V2 coexist in the same database. The V1 frontend continues working throughout the transition. Changes below are already shipped and active.

### V2 phase status
| Phase | Name | Status |
|-------|------|--------|
| 0 | Schema additions | Complete |
| 1 | Haiku two-field summaries (`display_summary` + `search_document`) | Complete |
| 2 | Intelligence logging (`course_availability_log`, `course_price_log`) | Complete |
| — | V2 stable ID migration (all 14 scrapers) | Complete |
| 3 | Algolia index bootstrap | Complete |
| 4 | V2 frontend (Algolia InstantSearch) | Complete |
| 4.5 | `index.html` modularisation | Complete |
| — | Activity mapping elimination (Initiative 1 of data quality mission) | Complete — scrapers, validator, Algolia, frontend, admin tab, 4 edge functions, docs all retired |
| — | Location mapping refinement (Initiative 2 of data quality mission) | Complete — Haiku-live-write on structural `{city, province}` match, `None`-return guard across all 9 normalise_location callers, pending queue now holds only real unknowns |
| — | Summary Review redesign (Initiative 3 of data quality mission) | Complete — Summary Review tab is now an exception inbox (bleed / user flag / generation failed). Validator backfills null summaries inline. `summary_empty` warnings retired. `bad_description` auto-clear retired. New `admin-save-summary` edge function and `validator_summary_exceptions` table |
| — | Date sanity provider loop (Initiative 5 of data quality mission) | Complete — both past-date-active and far-future (>2 yr) now auto-hide on first detection with `flag_reason` `past_date` / `future_date`. After 24-hour confirmation via `course_availability_log` the reason upgrades to `past_date_escalated` / `future_date_escalated` and surfaces in the Flags tab Date escalations section with copyable provider email. Admin Clear writes a course-id-scoped suppression (new nullable column on `validator_suppressions`) so the zombie never re-escalates. `custom_dates` is a hard skip. `future_date` retired from `validator_warnings` |
| — | Price sanity provider loop (Initiative 4 of data quality mission) | Complete — zero/negative price auto-hides on first detection with `flag_reason='invalid_price'`. After 24-hour confirmation via `course_price_log` the reason upgrades to `invalid_price_escalated` and surfaces in the Flags tab Price escalations section with copyable provider email. Admin Clear writes a course-id-scoped suppression reusing Initiative 5's mechanic. Null-price and >5x-median outlier checks deleted entirely (no replacement, permanent). Hardcoded Logan/Expedition/Traverse skip list and `validator_price_exceptions` reads retired. `course_price_log.bad_data` column added (set at write time when `price <= 0`). `null_price` and `price_outlier` retired from `validator_warnings` |
| — | Duplicate detection simplification (Initiative 6 of data quality mission) | Complete — duplicate check collapsed to a pure scraper-signal. `validator_whitelist` load + lookup removed from `validate_provider.py`; `admin-diagnose-duplicate` edge function deleted along with its `deploy-functions.yml` step. Flags-tab duplicate rows now render read-only with one `scraper_{provider_id}.py ↗` link per provider_id in the group; `Clear all` remains as a last-resort title-scoped suppression. Priority stack now 2 layers (suppressions → summary exceptions). `validator_whitelist` table orphaned-pending-Phase 7 |
| — | Course count drop → Providers tab (Initiative 7 of data quality mission) | Complete — `check_course_count()` deleted from `validate_provider.py`. Providers tab now computes the >30% drop client-side from the last two `scraper_run_log` rows per provider on every tab load and renders a yellow `⚠ {pct}% drop ↗` badge linking to the GitHub Actions workflow. No server-side write, no `validator_warnings` row. `validator_warnings` types now narrow to `null_avail` + `all_sold` |
| 5 | Velocity signals (fill rate, price trend) | Not started — needs 4+ weeks of log data |
| 6 | Validator simplification | Partially done — activity and price checks simplified; date check is active-loop; remaining cleanup pending |
| 7 | Drop V1 columns + tables post-cutover | Not started |

### V2 stable ID format
All 14 standalone scrapers now emit V2 IDs via `stable_id_v2()` in `scraper_utils.py`:
```
{provider_id}-{date_sort}-{title_hash_8}     # dated courses
{provider_id}-flex-{title_hash_8}             # flexible-dates / custom / private
```
- No activity segment. Platform-agnostic. Three segments, always three.
- `title_hash_8` = `md5(title.strip().lower())[:8]` via the `title_hash()` function.
- `title_hash()` is the SINGLE source of truth for title hashing — used by `stable_id_v2`, log functions, and future Algolia objectIDs. Never compute an inline md5 of titles elsewhere.
- The old V1 `stable_id()` function has been deleted from `scraper_utils.py`. Local copies still exist in a few scraper files (scraper.py legacy monolith, plus dead references in scraper_hangfire/bsa/jht) but nothing calls them.

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

**All 14 scrapers consolidated**: every scraper imports `generate_summaries_batch` from `scraper_utils.py`. Local copies in altus, cwms, summit, hangfire were deleted. Single-course `generate_summary` in bsa and jht was replaced with the batch pattern. Some scrapers (altus, cwms, summit, hangfire) still have local copies of other helpers (`claude_classify`, `parse_date_sort`, etc.) — lower priority consolidation.

**Return format**: `{course_id: {"summary": str, "search_document": str}}`. Both fields are upserted to `course_summaries` internally. All 14 scrapers write both `summary` and `search_document` directly to the `courses` table at scrape time — `search_document` goes live immediately without admin approval. `admin-approve-summary` still copies the `display_summary` to `courses.summary` (for admin edits). `admin-regenerate-summary` uses the two-field prompt. Summary Review tab shows both fields (card description editable, search document read-only).

No backfill needed — V1 rows are deleted on cutover, and all new scraper runs generate both fields. Algolia (Phase 3) goes live after cutover, so there is no consumer for `search_document` on pre-cutover rows.

### V2 Phase 3 — Algolia index bootstrap (implemented)
`algolia_sync.py` pushes V2 courses to Algolia index `courses_v2`. Uses `replace_all_objects` for atomic full replacement — stale records are automatically removed. Configured with searchable attributes, facets, custom ranking, and activity/location synonyms (synonyms retained as free-text relevance boosters even after the activity facet was removed). Runs automatically after every `scraper-all.yml` run (every 6 hours) with `--skip-settings`. Also available as standalone `sync-algolia.yml` workflow for manual triggers or settings reconfiguration.

### V2 Phase 4 — V2 frontend (implemented)
Algolia InstantSearch is live in `index.html` and replaces the Supabase-backed search stack on the Search page:
- **Search box** wired via `connectSearchBox`
- **Activity + Location dropdowns** fully removed — free-text Algolia search on `search_document` covers both. The Location synonyms + provider-name searchable attrs + date numericFilter handle the filter use cases the dropdowns used to serve.
- **Date filter** converted to a unix-timestamp `numericFilter` against `date_sort`
- **Provider deep link** (`?provider=`) applies as an Algolia `facetFilters` constraint rather than a Supabase `eq.` filter
- Old Supabase search functions are commented out (not deleted) as a fallback reference until V1 cutover
- `courses_v2` is the single source of truth for the search grid, synced every 6 hours by `scraper-all.yml`'s final step and on-demand via `sync-algolia.yml`
- **"Search by Algolia" attribution** — required by the Algolia Build (free) plan terms. Rendered as a static `#algolia-attribution` block inside `#page-search`, positioned directly below the infinite-hits `#load-more-wrap` ("show more" button). Pure markup + CSS, no JS — persists across empty / populated result states. Only visible on the Search tab (scoped via the existing `.page.active` toggle). Inline SVG links to `https://www.algolia.com/` per brand guidelines. If the site ever moves to the Algolia Grow plan, this attribution can be removed.

### Algolia Insights (event tracking)
Event tracking is wired so the Analytics tab in the Algolia dashboard accumulates CTR + conversion data per query, and so the dataset is ready for future personalisation / dynamic re-ranking.

**Setup** (in `/js/search.js`):
- `search-insights` CDN script loaded in `<head>` before deferred modules → exposes `window.aa`
- `initAlgoliaInsights()` in `/js/search.js` calls `aa('init', ...)` + sets a persistent anonymous `userToken` (UUID stored in `localStorage` as `bcf_algolia_user`) so returning visitors are recognised across sessions
- `instantsearch({ insights: true })` middleware auto-fires `viewedObjectIDsAfterSearch` when results render, and decorates each hit with `__queryID` + `__position` which `mapHit()` propagates onto the course object as `_queryID` / `_position`

**Events fired:** the funnel is deliberately tight — Search → Book Now (or Notify Me for flex/sold courses). Save-to-list is not tracked; it's engagement, not conversion intent, and adding it to the dataset dilutes the signal Algolia uses for ranking and personalisation.

| Event | Trigger | Algolia call | Name |
|-------|---------|--------------|------|
| View | Results render | auto (middleware) | `Hits Viewed` |
| Conversion | User clicks Book Now | `trackAlgoliaConversion(id, queryID, 'Course Booking Initiated')` in [js/cards.js](js/cards.js) `buildCard` onclick | `Course Booking Initiated` |
| Conversion | User submits Notify Me form | `trackAlgoliaConversion(_notifyCourseId, _notifyQueryID, 'Notify Me Signed Up')` in [js/ui.js](js/ui.js) `submitNotify` | `Notify Me Signed Up` |

**Helpers** live in `/js/search.js`: `trackAlgoliaClick(objectID, queryID, position, eventName)` and `trackAlgoliaConversion(objectID, queryID, eventName)`. Both no-op silently if `aa` isn't loaded or the objectID is missing — they never block UI or throw. `trackAlgoliaClick` is defined but currently unused; kept as the symmetric half of the helper pair for when a new engagement signal is introduced.

**Overlap with Supabase `click_events`:** the Book Now click currently fires BOTH `logClick()` (Supabase) and `trackAlgoliaConversion()` (Algolia). Double-instrumented by design during V2 transition — Phase 5 velocity-signals work will pick whichever source is more reliable and deprecate the other.

**Rules for future changes:**
- Keep the event surface narrow. Only fire an Algolia event when the action is booking intent (Book Now, Notify Me, future "Contact Provider" etc.). Engagement signals (save, share, hover) stay out of Algolia.
- `_queryID` + `_position` must be threaded through any new onclick/handler that acts on a course — read from the `currentCourses` entry, or serialise into the onclick payload like Book Now does
- Never depend on `aa` being defined — always guard with `if (typeof aa !== 'function') return;` inside the helper (already done)

### V2 Phase 4.5 — `index.html` modularisation (implemented)

JS extracted into `/js/` modules. Classic script tags, not ES modules — functions stay global, no import/export, no build step:

| File | Contents |
|------|----------|
| `/js/cards.js`     | `buildCard()`, `mapHit()`, `renderCards()`, `utmUrl()` |
| `/js/saved.js`     | saved-list primitives (`getSaved`/`setSaved`/`isSaved`), `toggleSave`, `renderSaved`, shared-list (`getSharedIds`, `initSharedCourses`, `saveSharedCourses`, `dismissSharedBanner`), share popover (`buildSharePopoverHTML`, `positionPopover`, `toggleSavedShare`, `closeAllPopovers`, `copyShareLink`, `nativeShare`), `clearMyList`, `openEmailListModal`, `submitEmailList` |
| `/js/ui.js`        | `showPage`, `logClick`, notify / email / provider modals, toast + micro-toast, skeleton/loading utilities, `addRemoveReadyListeners`, report strip (`openReport`, `selectChip`, `closeReport`, `resetReport`, `submitReport`, `reportObserver`), `initUI()` (logo hover + tagline animation) |
| `/js/providers.js` | `loadProviders()` (providers page grid) |
| `/js/search.js`    | Algolia InstantSearch (`searchClient`, `search`, `customSearchBox`, `customInfiniteHits`, `customConfigure`), date/provider filters (`updateDateChip`, `clearDateFilter`, `applyConfigFilters`), provider deep-link helpers (`setProviderFilter`, `clearProviderFilter`, `initProviderFilter`), `debouncedSearch` legacy stub, commented-out V1 Supabase query blocks, `initSearch()` |

`index.html` now contains only: HTML structure, CSS, constants (`SUPABASE_URL`, `SUPABASE_KEY`, `ALGOLIA_APP_ID`, `FALLBACK_IMG` etc.), shared state (`currentFilters`, `currentCourses`, etc.), and a ~5-line `DOMContentLoaded` init sequence that calls `initSearch()`, `initUI()`, `initSharedCourses()`.

**Rules for future changes:**
- All new JS goes into the relevant `/js/` module — never back into `index.html` inline
- Script tags in `<head>` use `defer` and load in order: `cards.js`, `saved.js`, `ui.js`, `providers.js`, `search.js`. `defer` means modules execute *after* HTML parsing (including the body `<script>` that defines constants) but before `DOMContentLoaded`, so top-level code in a module can reference body-script constants (`ALGOLIA_APP_ID`, `SUPABASE_URL`, etc.) safely — no need to defer instantiation into `initXxx()` functions for ordering reasons
- Shared mutable state (`currentCourses`, `currentFilters`, `totalCount` etc.) lives as top-level `let` in `index.html`; modules read/write by name
- Credentials/URLs (`SUPABASE_URL`, `ALGOLIA_APP_ID` etc.) stay in `index.html`; modules reference them as globals
- Never drop `defer` from a module tag without migrating any body-script dependencies into `<head>` first — it is the load-order guarantee that keeps this pattern stable

### Card redesign (scheduled post-Phase 4.5)
New card design discussed and approved. Claude Code to build against `/js/cards.js` only. No other files touched during card redesign.

### V2 phases remaining (not yet implemented)
- **Phase 5:** Velocity signal calculation (fill rate, price trend — needs 4+ weeks of log data)
- **Phase 6:** Validator simplification (remaining admin-tab retirements; activity check already removed)
- **Phase 7:** Drop V1 columns + tables after cutover (includes `activity`, `activity_raw`, `activity_canonical`, `badge`, `badge_canonical` from `courses` and the `activity_mappings`, `pending_mappings`, `activity_labels` tables)

### Data quality mission (parallel track)
See [data_quality_initiatives.md](data_quality_initiatives.md) for the initiative plan.
- **Initiative 1 — Activity mapping elimination:** fully complete.
- **Initiative 2 — Location mapping refinement:** fully complete.
- **Initiative 3 — Summary Review tab redesign:** fully complete. Tab is an exception inbox (bleed / user flag / generation failed). Validator backfills null summaries inline using a title-only Haiku seed (safety net). `bad_description` user reports no longer auto-clear. New `admin-save-summary` edge function commits admin edits + writes `(provider_id, md5(summary))` to `validator_summary_exceptions`. Bleed check consults the exception table so one admin save clears the whole collision group on the next run.
- **Initiative 5 — Date sanity provider loop:** fully complete. Past-date-active and far-future (>2 yr) both auto-hide on first detection and escalate to the Flags tab's Date escalations section 24 hours after the first `course_availability_log` entry. Escalation surfaces with booking URL + copyable provider email body; admin Clear writes a course-id-scoped suppression (new nullable column on `validator_suppressions`) so zombies from V2-id-drifting date corrections never re-escalate. `future_date` retired from `validator_warnings`. `custom_dates=true` / `date_sort IS NULL` is a hard skip.
- **Initiative 4 — Price sanity provider loop:** fully complete. Zero/negative price auto-hides on first detection with `flag_reason='invalid_price'`; a `course_price_log` row 24+ hours old upgrades the reason to `invalid_price_escalated` and surfaces the course in the Flags tab's Price escalations section with copyable provider email. Admin Clear reuses Initiative 5's course-id-scoped suppression mechanic. Null-price and >5x-median outlier checks are deleted permanently — no replacement, no median comparison anywhere in the codebase. Hardcoded `("Logan","Expedition","Traverse")` skip list and `validator_price_exceptions` reads are gone. New column `course_price_log.bad_data` (set at write time by `log_price_change` when `price <= 0`) protects Phase 5 velocity-signal consumers from zero-priced-row pollution without read-time filter logic. `wrong_price` user-flag auto-clear simplified to `price > 0`.
- **Initiative 6 — Duplicate detection simplification:** fully complete. Admin decision path stripped from duplicate handling: `validator_whitelist` load + lookup deleted, `admin-diagnose-duplicate` edge function deleted (also removed from `deploy-functions.yml`), Diagnose/Whitelist buttons removed from the Flags tab. Auto-hide stays. Duplicate rows render read-only with one `scraper_{provider_id}.py ↗` GitHub link per provider_id; the only manual action is `Clear all` which writes a title-scoped suppression as a last resort. Priority stack collapsed to 2 layers (suppressions → summary exceptions). `validator_whitelist` table orphaned, drops at V2 Phase 7.
- **Initiative 7 — Course count drop → Providers tab:** fully complete. `check_course_count()` deleted from `validate_provider.py`; the Providers tab now fetches the last two `scraper_run_log` rows per provider on load (via `fetchRunLog` + `lastRunByProvider` / `prevRunByProvider` maps) and renders a yellow `⚠ {pct}% drop ↗` badge on the status cell when the drop exceeds 30%. The badge links directly to that provider's GitHub Actions scraper workflow. No server-side write; the signal recomputes on every tab load and clears automatically on the next healthy run. `validator_warnings` types narrowed to `null_avail` + `all_sold`. No new tables, no Supabase SQL.

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
