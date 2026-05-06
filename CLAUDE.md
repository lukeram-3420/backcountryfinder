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
- **Filter bar** — active controls on `#page-search` are the Algolia searchbox (`#search-query`), the `#search-location` dropdown, and `#search-date`, all sitting in the search bar. The location dropdown is populated once on init by `populateLocationDropdown()` in [js/search.js](js/search.js) via a single Algolia facet query against `location_canonical` (sorted A→Z, "Anywhere" prepended, course count appended). Below the search bar a single `#sort-price` pill cycles `Price → Price ↑ → Price ↓ → Price` (`cyclePriceSort()`) and swaps the active Algolia index between the primary and the two `_price_asc` / `_price_desc` replicas. The activity dropdown was removed in V2 Phase 4 — free-text search against `search_document` covers it.
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

### Activity tracking — `activity_controls` (Initiative 8)
Per-title exclusions used to live in per-scraper `EXCLUDE_TITLES` constants. They now live in the `activity_controls` table and are admin-toggled via the Activity Tracking tab. **Every scraper** (all 23 active provider scrapers — the legacy `scraper.py` monolith and `scraper_aaa_details.py` enrichment pass are the only exceptions) upserts `(provider_id, activity_key)` for every activity it sees, then consults `visible` before any expensive work (detail-page fetch, availability walk, row emit).

`activity_key` is a unified prefixed string:
- `zaui:{upstream_id}` — Zaui tenants (stable numeric id survives title edits)
- `title:{title_hash_8}` — platforms without a stable upstream id (WordPress / Squarespace / HTML)

Scrapers use five helpers from `scraper_utils`:
- `activity_key(platform, upstream_id, title)` — builds the prefixed key
- `upsert_activity_control(provider_id, key, title, ...)` — single-row upsert (used by the 18 non-Zaui scrapers; see `_is_visible()` pattern in `scraper_altus.py` and every other title-based scraper)
- `bulk_upsert_activity_controls(rows)` — batch version used by the 5 Zaui scrapers (banff_adventures, canmore_adventures, mt_norquay, toby_creek_adventures, vanmtnguides) where N single POSTs per run would add noticeable latency
- `load_activity_controls(provider_id)` — one query per run, returns `{activity_key: {'visible': bool, 'tracking_mode': str}}`
- `load_lookahead_windows()` — reads `scraper_config`, returns `{'extended': int, 'immediate': int}` (Zaui only)

Defaults for a first-seen activity are `visible=true, tracking_mode='immediate'`. The scraper pays one normal cycle on that activity until the admin flips the toggle.

**Visible toggle cascades to `courses.active` immediately.** The `admin-toggle-activity-control` edge function PATCHes every matching course row (`provider_id + title` match) when `visible` flips — OFF hides them all; ON re-activates them except `avail='sold'` rows (mirrors `admin-toggle-provider` semantics). This makes Visible a general-purpose "hide this course on the frontend" button — usable for out-of-scope activities, broken scraper output, or anything that shouldn't surface while it's being fixed. Frontend reflects the change on the next Algolia sync (~30s if the admin separately dispatches `sync-algolia.yml`, or up to 24 hours via the scheduled sync). The Activity Tracking tab has a dedicated **Sync Algolia** button in the catalog header that dispatches `sync-algolia.yml` on demand — the per-row toggles intentionally do NOT auto-dispatch so an admin can batch 10–20 edits into a single workflow run instead of firing one per toggle. (Contrast with `admin-toggle-provider`, which is low-frequency and always auto-dispatches.)

**Zaui tracking_mode controls per-activity lookahead window.** `immediate` (default 14 days) vs `extended` (default 180 days). Configured via `scraper_config` k/v table, read by scrapers via `load_lookahead_windows()`. The admin edits the two windows at the top of the Activity Tracking tab; scrapers pick them up on next run. Non-Zaui scrapers don't consume tracking_mode — WordPress / Rezdy / Checkfront / FareHarbor / Squarespace / Rails either read a full-catalog API or crawl fixed pages, with no tunable lookahead.

**Structural filters stay as code.** `scraper_zaui_utils.is_experience_product` still filters out hotels, airport transfers, rentals (substring match on `"rental"` catches 100s of products), and category-level excludes. These are domain-invariant — hotels are never backcountry experiences regardless of admin intent — and don't fit per-row DB toggle semantics.

Migration: historical `EXCLUDE_TITLES` constants seeded into `activity_controls` via `seed_activity_controls.py` (idempotent UPSERT on `(provider_id, activity_key)`). 5 files had real entries (altus, vibe, girth_hitch, cloud_nine, bow_valley); 4 empty `EXTRA_EXCLUDE_TITLES = []` constants were deleted (canmore, banff, mt_norquay, toby_creek). The seed can also be run from GitHub Actions via the `seed-activity-controls.yml` workflow (manual trigger, optional `dry_run` input).

Reference: the old hardcoded `EXCLUDE_TITLES = ["altus mtn club", "altus mountain club"]` on `scraper_altus.py` — which blocked a Thinkific subscription product from polluting the catalog with a fabricated date — is now two `visible=false` rows in `activity_controls` keyed on `(altus, title:{hash})`.

### Date extraction must be scoped (required)
Scrapers that parse course dates from provider HTML pages must scope regex matching to schedule-like containers. Never run date regexes against the entire `soup.get_text()` — doing so pulls stray dates from footers, copyright notices, testimonials, Thinkific membership terms, "last updated" timestamps, and unrelated blog content, producing fabricated course dates (e.g. Altus MTN Club was assigned a fake Aug 20 2026 date before this rule was enforced).

Required scoping heuristic:
- Only extract dates from elements whose `class` or `id` matches the regex `schedule|dates|upcoming|session|availability|calendar` (case-insensitive)
- Or from siblings following an `h2/h3/h4` whose text matches the same pattern
- If no schedule container is found, treat the course as `custom_dates=True` (flex-date row)
- This rule applies to BOTH Pass 1 detail-page checks (Rezdy/Checkfront/etc.) AND Pass 2 WordPress/HTML schedule parsing

Reference implementation: `extract_schedule_text(soup)` in `scraper_altus.py` — replicate in other scrapers that parse schedules from HTML. The optional-year fallback in `parse_wp_dates` (defaults to current year, bumps to next year if past) amplifies this bug if unscoped, so the scoping rule is a hard requirement, not a nice-to-have.

### Variable pricing — Zaui tier policy
Zaui tenants with multi-tier pricing (adults / seniors / students / children / infants) sometimes leave the adult tier null on specific products (day passes, single-person rentals). Worse, some tenants invert the tier semantics entirely — Toby Creek labels `seniors` as "Driver (18+)" (the sellable primary) while `adults` is a passenger/pillion add-on rate. Trusting the key name at face value picks the wrong price.

All 5 Zaui scrapers resolve price via `extract_zaui_price(act)` in `scraper_zaui_utils.py` which returns `{price, tier, has_variations}`. Extraction order is deterministic (stable across runs so `course_price_log` stays apples-to-apples):

1. **`pax`-declared primary tier** — the tenant's own `act.pax` dict tells us which tier is sellable. `pax: {"seniors": {"default": 1}}` means `seniors` is the primary guest type, regardless of what the key is called. Cheapest positive primary-tier price wins.
2. `listPrice` scalar — fallback when pax is missing.
3. Adult-equivalent keys: `adults` / `adult` / `single` / `rider` / `default` / `standard` / `base`.
4. Near-adult tiers: `seniors` / `senior` / `students` / `student`.
5. `inferred_min` of remaining positive values.
6. Scalar fallbacks: `minPrice` / `fromPrice` / `basePrice` / `startingPrice`.
7. Array fallbacks: `customerTypePricing[]` / `ratePlans[]` / `rates[]`.

Variation detection (drives the `↕ Price varies` chip) is gated on the primary tier set: only counts ≥2 distinct positive prices among `pax`-declared primary tiers. If no `pax` info exists, falls back to the whole-price-dict scan.

Every Zaui row writes three fields: `price`, `price_tier` (e.g. `'seniors'` for Toby Creek's Driver tier, `'adults'` for standard tenants, `'inferred_min'` / `'scalar_minPrice'` / `'array_customerTypePricing'` for tier-less products), and `price_has_variations` (display signal only). `log_price_change()` propagates `price_tier` into `course_price_log` so Phase 5 velocity signals filter on a single tier per course.

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
| `generate_summaries_batch` | `(courses: list, provider_id: str = None) -> dict` | Batch-generate two-field summaries (Phase 1 V2): `display_summary` (user-facing) + `search_document` (Algolia keywords). Input: list of `{id, title, description, provider}`. Returns `{course_id: {"summary": str, "search_document": str}}`. Internally upserts both fields to `course_summaries` table. `provider_id` param used for upsert; falls back to each course dict's `provider_id` key. Processes in batches of 12 with single retry on failure. **Caches via `course_summaries.description_hash`** (PR #48): preflight bulk-fetches existing rows, skips Haiku for any `(provider_id, title)` whose stored hash matches `md5(description.strip())` and has a non-empty cached summary. Only cache misses go to Haiku; hits are merged into the return value. **Caller contract:** scrapers must pass `provider_id=PROVIDER["id"]` explicitly OR include `provider_id` on every per-course dict. If neither is supplied, `_upsert_course_summaries` writes rows with `provider_id=""` which (a) collide on the unique constraint with every run and (b) make the cache permanently miss because the lookup filters out empty provider_ids. PRs #49 + #53 fixed this for all 14 scrapers. |
| `_load_cached_summaries` | `(provider_ids: list) -> dict` | Private helper. Bulk-fetches `course_summaries` rows for the given providers via single PostgREST GET with `Range: 0-49999`. Returns `{(provider_id, title): {summary, search_document, description_hash}}`. Used internally by `generate_summaries_batch`'s preflight cache check. Logs and returns `{}` on failure (cache miss → fall through to Haiku). |

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
| `detect_checkfront_spot_counts` | `(item_cal: dict) -> bool` | **Per-item** probe of a Checkfront `/api/3.0/item/cal` response for one item. Returns `True` only when at least one of THIS item's values is `>1`, meaning integer interpretation is safe. Returns `False` if all values are `0`/`1` — the binary-availability flag — at which point the caller MUST set `spots_remaining=None` so `spots_to_avail()` returns `'open'`. **Do not run a global probe across the whole `cal` dict** — semantics are per item, not per tenant; mixing the two leaks "1 spot left" into binary-flag products. Used by `scraper_aaa.py` and `scraper_girth_hitch_guiding.py`. |
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

#### Activity tracking

| Function | Signature | Description |
|----------|-----------|-------------|
| `activity_key` | `(platform: str, upstream_id=None, title: str = "") -> str` | Unified prefixed dedup key. `zaui:{id}` when `upstream_id` is present, `title:{title_hash_8}` otherwise. |
| `upsert_activity_control` | `(provider_id, activity_key_, title, *, upstream_id=None, title_hash_=None, platform=None) -> None` | Idempotent single-row upsert. Writes title/upstream_id/title_hash/platform/last_seen_at. **Never writes `visible` or `tracking_mode`** — those are admin-owned and preserved by merge-duplicates. |
| `bulk_upsert_activity_controls` | `(rows: list) -> None` | Batch version. Same semantics as `upsert_activity_control` but one HTTP POST for the whole list. Used by Zaui scrapers with hundreds of activities. |
| `load_activity_controls` | `(provider_id: str) -> dict` | One query per scraper run. Returns `{activity_key: {'visible': bool, 'tracking_mode': str}}`. Missing keys default to `visible=true, tracking_mode='immediate'`. |
| `load_lookahead_windows` | `() -> dict` | Reads `scraper_config`. Returns `{'extended': int, 'immediate': int}`. Baked-in defaults (180/14) if rows missing or values unparseable. Used by Zaui scrapers to pick per-activity `fetch_unavailability` window length. |

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

**FareHarbor-specific notes (added May 2026 after API rework):**

FareHarbor stripped pricing from the entire `/api/v1/companies/.../items/...`
namespace in their 2026 rework. Pricing now lives **only** on the embed-side
endpoint `/api/embed/{shortname}/price-preview/per-day/v2/`. Three other
endpoints carry partial data, all captured via Playwright XHR interception:

| Endpoint | Carries |
|---|---|
| `/api/v1/companies/{shortname}/items/` | Catalogue (titles, IDs, descriptions). Plain HTTP, no auth. **No pricing.** |
| `/api/v1/companies/{shortname}/items/{pk}/calendar/{YYYY}/{MM}/?allow_grouped=yes&bookable_only=no` | Per-month availability dicts with `pk`, `start_at`, `capacity_remaining`. **No pricing.** Replaces the legacy `/availabilities/` endpoint. |
| `/api/v1/companies/{shortname}/items/{pk}/` | Per-item details (94 keys). **No pricing on this either** — `customer_prototypes: None`. |
| `/api/embed/{shortname}/price-preview/per-day/v2/?item_pks={pk}&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | The price source. Body shape: `{"prices": [{"date": "YYYY-MM-DD", "price": {"low": cents, "high": cents}}], "details": {currency, prices_include_taxes, ...}}`. `low` and `high` are an integer cents range across customer types. Empty `prices: []` = item has no bookable dates in the queried window. |

Implementation pattern in `scraper_vibe_backcountry.py` (the only FareHarbor
scraper as of writing — see provider table):

1. **Catalog fetch** — plain HTTP `fh_get("items/")` for the shallow listing.
2. **Playwright walk per item** — load `https://fareharbor.com/embeds/book/{shortname}/items/{pk}/calendar/{YYYY}/{MM}/?full-items=yes&flow=no&g4=yes` for each month in the lookahead window. The page's Angular widget fires both the calendar XHR (availabilities) AND the price-preview XHR (pricing) — `on_response` matches both URL patterns and stores them. The `/embeds/` page-shell URL is explicitly excluded so we don't try to JSON-parse the HTML.
3. **Recursive walker** (`_walk_for_amount`) finds the smallest amount-like value anywhere in the price-preview body. AMOUNT_KEYS includes `low`, `high`, `amount`, `total`, etc. Handles both numeric and string-formatted (`"$250.00"`) values via `_coerce_amount`. Returns the raw cents value.
4. **Cents conversion** — divide by 100 explicitly on the price-preview path. The widget displays whole dollars but the API stores cents (verified — `18550 = $185.50` for "Beginner Paddling Skills"). Don't use a magnitude-based heuristic; a $2,000+ multi-day trip would get mis-classified as $20.

**Multi-availability dedup is required.** FareHarbor exposes morning/afternoon
slots of the same product on the same day as distinct `pk` values. The V2
stable id `{provider_id}-{date_sort}-{title_hash}` collapses these to one
row, which Postgres rejects with code 21000 (`"ON CONFLICT DO UPDATE command
cannot affect row a second time"`) when both end up in one upsert batch.
Filter `seen_ids` before `sb_upsert("courses", ...)` — pattern at
`scraper_vibe_backcountry.py:457` and `scraper_altus.py:1054`. PR #55 fixed
this for Vibe; copy that pattern verbatim if onboarding another FareHarbor
provider.

**Diagnostic-first iteration history (PRs #50-#76):** the API rework took
five diagnostic-then-fix cycles to fully reverse-engineer. The pattern of
"add a `[shape]` log line on the silent-failure path, ship that, read the
output, then ship the targeted fix" is now baked into the scraper's
`on_response` handler. Future shape drift on either the calendar or
price-preview endpoint will surface in the per-item summary log without
another diagnostic-only PR cycle.

**Checkfront-specific notes (added May 2026):**

Checkfront's `/api/3.0/item/cal` endpoint occasionally returns 500 even on
otherwise valid requests, and some tenants have data-corruption ghosts that
crash the endpoint deterministically for specific products. Three defensive
layers, all in `scraper_girth_hitch_guiding.py` (replicate when adding
another Checkfront scraper):

1. **Retry-on-5xx in `cf_get`** — three attempts with exponential backoff
   (2s, 4s, 8s). 4xx still raises immediately. Soaks transient infra blips.
2. **Per-item fallback in `fetch_availability`** — if a batch of 5 items
   sustains 500s after retries, retry each item individually. The healthy
   items get through, only the broken ones get logged + skipped. Prevents
   one bad product from killing the whole run.
3. **Hardcoded skip list (`BROKEN_ITEM_IDS`)** — for items confirmed broken
   beyond retry. Girth Hitch has `{8, 14, 20, 134, 143}` set in scraper
   module top-level (PR #58). Filter `[i for i in ids if int(i) not in
   BROKEN_ITEM_IDS]` before calling `fetch_availability`. Avoids wasting
   the per-item-fallback API budget on every run.

The skip list is a workaround, not a fix. **Real resolution is on the
provider's side**: those item_ids correspond to archived / misconfigured
products in their Checkfront account whose availability rules crash the
public API. Cross-reference the IDs against the provider's Checkfront admin
and ask them to clean up. Once they confirm fixed, drop the IDs from the
set or just leave it as a no-op (the IDs will no longer be in the catalog).
Compare with the binary-flag bug at `/api/3.0/item/cal` documented above
under `detect_checkfront_spot_counts` — different failure mode, same
"upstream-data-quality issue surfaced by our scraper" story.

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

**Current production state:** `scraper-zaui.yml` runs once daily (`0 0 * * *`) and walks all 4 groups sequentially per provider in a single workflow run via `for g in 0 1 2 3; do python scraper_X.py --group $g || true; done`, then validates each provider once at the end. This means:
- A single daily run covers the full catalog for every grouped provider
- `scraper_run_log.course_count` reflects the full catalog (one row per provider per day)
- The Providers tab Initiative 7 count-drop badge clears naturally — comparisons are now apples-to-apples (full catalog vs full catalog)

When wiring a new grouped scraper, run all 4 groups in sequence inside `scraper-zaui.yml` (or `scraper-all.yml` for non-Zaui grouped scrapers) followed by a single `validate_provider.py` step. The 30% drop detection only stays accurate if every group runs inside one scheduled window before validation.

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
- **Triggers:** `schedule` (cron `0 0 * * *` — daily at 00:00 UTC) + `workflow_dispatch`
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
- **Trigger:** `workflow_dispatch` only (manual). The Sunday 06:00 UTC cron was removed 2026-04-29 — the pipeline already had enough candidates queued for triage and the weekly auto-run was generating noise faster than the admin could review.
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

### Seed activity controls workflow — seed-activity-controls.yml
- **Trigger:** `workflow_dispatch` only (manual)
- Input: `dry_run` (default `false`) — `true` prints rows without writing
- Runs: `python seed_activity_controls.py [--dry-run]`
- Dependencies: `requests`
- Uses 2 secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- Prerequisite: `activity_controls_schema.sql` must be applied in Supabase first, otherwise the seed will 404
- Idempotent — safe to re-run. Admin-flipped rows are not reverted because the script only writes the columns it owns.

### algolia_sync.py

Pushes V2 courses from Supabase to Algolia index. Reads all active, non-flagged V2 courses with provider join, maps to Algolia records, configures index settings, and pushes via `save_objects` (upsert by objectID). Idempotent — safe to re-run.

**Usage:** `python algolia_sync.py` or `python algolia_sync.py --dry-run` or `python algolia_sync.py --skip-settings`

**Flags:** `--dry-run` (log records, no push), `--skip-settings` (skip index config, just push records)

**Env vars:** `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ALGOLIA_APP_ID`, `ALGOLIA_ADMIN_KEY`, `ALGOLIA_INDEX_NAME` (default: `courses_v2`)

**Supabase query:** `courses?active=eq.true&flagged=not.is.true&auto_flagged=not.is.true&activity_canonical=is.null` with provider join.

**Algolia record schema (server-side dedup):** one record per `(provider_id, title_hash)` group — emitted by `group_courses_for_algolia()` in `algolia_sync.py`. Top-level fields: `objectID` (`{provider_id}-{title_hash}`), `id` (head session's V2 stable id, used by frontend save/share flows), `title_hash`, `title`, `search_document`, `summary`, `location_canonical`, `location_raw`, `image_url`, `duration_days`, `currency`, `custom_dates`, `booking_mode`, `price_min` (smallest positive price across sessions), `price_has_variations`, `next_date_sort` (smallest date_sort across sessions, used for sort), `max_date_sort` (largest date_sort across sessions, used for the frontend's date numericFilter — see "Date filter semantics" below), `next_date_display`, `provider_id`, `provider_name`, `provider_rating`, `provider_logo_url`. Plus a `dates[]` array, sorted ascending by `date_sort`, where each entry has: `id`, `date_sort` (unix timestamp), `date_display`, `price`, `avail`, `spots_remaining`, `booking_url`. **Course-level field resilience (PR #101):** `summary`, `search_document`, `image_url`, `location_canonical`, `location_raw`, `duration_days`, `currency`, `booking_mode` all use a `first_nonempty(items, key)` fallback inside `group_courses_for_algolia` instead of plucking from `head.get(...)` only — protects the record from an empty head row when a sibling session has the value populated (real-world cause: orphan rows from prior scrapes whose date_sort drifted, or partial Haiku batch failures that left some sessions of a title with `summary=""` while others were back-filled). `id` and `custom_dates` stay head-only (head's V2 stable id is what saved/share flows expect; `custom_dates` semantically tracks the head session). Effects: ~5× record-count reduction (~21k → ~3-5k); search grid stops indexing the same course as N records; SEO landing pages stop emitting duplicate-content cards.

**Date filter semantics (PR #103):** the search bar's date input defaults to **tomorrow** ([js/search.js](js/search.js) `initSearch()`). Filtering on `next_date_sort >= ts` (the smallest date_sort) was wrong — it excluded courses whose earliest session was today even though they had ~150 future sessions in `dates[]`. The numericFilter now reads `[["max_date_sort>=ts", "next_date_sort>=ts"]]` (nested-array OR): a record passes when AT LEAST ONE session is on/after the user's date. The OR fallback covers the deploy-gap before a sync repopulates every record with `max_date_sort` — Algolia excludes records lacking the filtered attribute, which would break the live grid for up to 24 hours otherwise. Safe to drop the fallback once a full `sync-algolia.yml` has run post-deploy. **Client-side session trim:** [js/cards.js](js/cards.js) `mapHit()` reads the user's date input via `_getUserDateTs()` and drops sessions with `date_sort < ts` from the synthetic group, then recomputes `price_min` from the visible subset. Falls back to the full set when the filter would empty the group (defensive). The card's primary row, expanded dates list, and "From $X" chip reflect what the user is looking for instead of the global session list. `mapSupabaseRow()` (saved-list / shared-list path) intentionally does NOT participate — those views show every saved date regardless of the search-page filter.

**Index settings:** Searchable attributes (ordered): `title`, `search_document`, `provider_name`, `location_canonical`. Facets: `location_canonical`, `provider_name`, `provider_id`, `booking_mode`, `avail`. Custom ranking: `asc(next_date_sort)`. Flex-date courses use far-future timestamp (2100-01-01) so they sort to the end. **Query-understanding settings (PR #96):** `queryLanguages: ["en"]`, `removeStopWords: ["en"]`, `ignorePlurals: ["en"]`, `removeWordsIfNoResults: "allOptional"`. Without these, natural-language queries like `climbing in squamish` returned zero results because Algolia AND-required the connective word `in` against the searchable attributes (it doesn't appear in any of them, so the strict pass failed and the default `removeWordsIfNoResults=none` returned zero hits). `removeStopWords` drops `in`/`at`/`the`/`for`/`of` before token matching; `removeWordsIfNoResults: "allOptional"` is a defence-in-depth net for connective words that aren't on the English stop-word list. Required for any natural-language query that mixes an activity word with a location word. Replicas inherit on creation, so the replica `set_settings` calls don't need to repeat them. **Pushing settings changes:** `scraper-all.yml`'s daily run uses `--skip-settings` (records-only sync), so settings changes only reach the live index via a full `python algolia_sync.py` run — trigger via the `sync-algolia.yml` workflow (`workflow_dispatch`) or the admin Activity Tracking tab's Sync Algolia button.

**Replicas (price sort):** the primary index declares two replicas — `courses_v2_price_asc` and `courses_v2_price_desc` — created automatically by Algolia when `set_settings` includes the `replicas` array. Replicas inherit records, searchable attrs, and faceting from the primary; only `customRanking` differs:
- `courses_v2_price_asc` → `["asc(price_min)", "asc(next_date_sort)"]`
- `courses_v2_price_desc` → `["desc(price_min)", "asc(next_date_sort)"]`

Records without a positive `price_min` (~13% of the index — custom-dates / inquiry-only) have the field omitted by `algolia_sync.py` (`None` values are stripped). Algolia deprioritizes records missing a customRanking attribute, so they sort to the end on asc and to the start on desc *after* priced records — acceptable UX. The frontend price toggle (`cyclePriceSort()` in `js/search.js`) cycles `off → asc → desc → off` and swaps the active index via `search.helper.setIndex()`; existing facetFilters / numericFilters carry through the swap. Synonyms don't auto-replicate, so `configure_index()` calls `save_synonyms()` against all three index names.

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
1. **Providers** — stats row (providers / courses / auto-hidden / user flags), provider table with **Scraper** column (three-state classifier: green platform-name badge like `rezdy` / `zaui` / `checkfront` / `woocommerce` / `squarespace` when the provider uses reusable adapter code we can clone for similar providers; amber `bespoke` badge when the scraper is hand-written for that specific site and not reusable — covers custom HTML, Rails, custom WordPress, Playwright-specific parsers, and any site whose `booking_platform` is `'wordpress'`, `'unknown'`, `'custom'`, or null; em-dash when no `scraper_run_log` row exists yet. Reusable set is the hardcoded `REUSABLE_PLATFORMS` const in admin.html — `wordpress` is deliberately excluded because each WP site needs its own parser. When adding a new reusable platform adapter, update both `REUSABLE_PLATFORMS` and `PLATFORM_REFERENCE` in `copyPipelinePrompt`), active toggle, last run, course count, status badge, per-provider "Run" and "Validate" buttons (Validate calls `admin-trigger-scraper` with `workflow_id='validate-provider.yml'` + `inputs={provider_id}`), and "Run all" button. **All course counts on this tab are unique-course counts**, deduped by `(provider_id, title_hash)` to mirror the Algolia-side dedup landed in PR #43 — the stats row "Courses" total, the per-provider "Courses" column, the auto-hidden / user-flag stats, and the per-provider `N hidden` / `N user flags` status badges all dedup so a single course title with 30 scheduled dates counts as 1 course, not 30. Computed client-side in `loadProvidersTab()` via one paginated fetch of `(id, provider_id, active, flagged, auto_flagged)` for every row, then in-memory dedup by the last 8 chars of `id` (the V2 stable id's title_hash segment). A **Date added** column on the right edge renders `providers.created_at` as ISO date (`YYYY-MM-DD`, sliced from the raw timestamp; null/missing → `—`); useful for spotting recent onboarding work. The status cell also surfaces the **Initiative 7 count-drop signal** — when the last two `scraper_run_log` rows for a provider show a course count drop of >30%, a yellow `⚠ {pct}% drop ↗` badge renders alongside the normal status badge, linking directly to that provider's GitHub Actions workflow (`actions/workflows/scraper-{id}.yml`). Computed client-side in `renderProvidersTable()` from the `prevRun` / `lastRun` data attached in `loadProvidersTab()` — no server-side write, no `validator_warnings` row. Clears automatically on the next healthy scrape. Note that `scraper_run_log.course_count` is a per-(course,date) row count (set by `validate_provider.py` after the scrape), so the Initiative 7 drop signal compares like-for-like between the two log rows; it does NOT compare against the Providers tab's deduped Courses column. Column headers are clickable to sort (Provider / Scraper / Last run / Courses / Status / Date added), default is alphabetical by name. The Date added sort uses the raw ISO timestamp, not the displayed slice.
2. **Location Mappings** — pending + approved location mappings with inline Edit and Delete. Header has an **"Add mapping"** button that opens an inline form (Location raw + Location canonical text inputs) and POSTs directly to `/rest/v1/location_mappings` with the authenticated session token. Approved rows edit both `location_raw` and `location_canonical`. Course counts are on-demand via a "Load counts" button — one `countRows()` query per unique `location_canonical`, results cached for the session. Column headers (Raw / Canonical / Courses / Created) are clickable to sort ascending/descending; Courses is only sortable after counts are loaded. Default is alphabetical by `location_raw`.

### Sortable headers (shared pattern)
Two tables (Providers, Location Mappings) use a shared sort helper in `admin.html` (`cmpValues`, `sortIndicator`, `sortableHeader`, `toggleSortState`). Clicking a header toggles asc/desc on that column or switches to a new column (asc first). Nulls always sink to the bottom regardless of direction. Text sorts via `.toLowerCase().localeCompare()`. Numeric sorts cast via `Number(...)`. Timestamp columns (Last run, Date added) sort on the raw ISO string — `localeCompare` orders ISO 8601 strings chronologically, so no Date-cast is needed.
3. **Summary Review** — exception inbox (Initiative 3). Three row sources merged client-side: (a) `auto_flagged=true` + `flag_reason LIKE 'summary_bleed%'` — the validator detected identical summary text across different titles and auto-hid the second occurrence, (b) `flagged=true` + `flagged_reason='bad_description'` — user reports, (c) `summary IS NULL` + `active=true` — generation failures (the validator's inline `generate_summaries_batch()` backfill could not produce text). Two fields per row: **Card description** (editable textarea, maps to `courses.summary`) and **Search document** (read-only textarea, maps to `courses.search_document`). Two buttons: **Save** calls `admin-save-summary` edge function which writes the text, clears `auto_flagged` and/or `flagged` on the course, and inserts a `(provider_id, md5(summary), reason)` row into `validator_summary_exceptions` so the validator skips this summary text on future runs. **Regenerate** calls `admin-regenerate-summary` with the title as the description seed (scrapers strip description before upsert) and populates the textarea — does not save; admin must click Save to commit. The old `approved=false` queue from `course_summaries` is bypassed; scraper-generated summaries go live immediately via direct `courses.summary` writes at scrape time. The legacy `admin-approve-summary` / `admin-reject-summary` edge functions still exist but are not called by the current UI.
4. **Flags** — Stats row (User reports / Auto-hidden / Warnings). Header buttons: "Reload flags" (re-runs `loadFlagsTab`), "Re-validate all ↗" (loops `admin-trigger-scraper` over all active providers with 500ms spacing), "Copy fixable flags prompt" (bundles wrong_price, wrong_date, bad_description, sold_out flags for Claude Code). User reports section (only `button_broken` and `other` get a Mark resolved button — `bad_description` is handled in Summary Review, `wrong_date`/`wrong_price`/`sold_out` auto-clear via validator when the issue resolves). Validator auto-flags section is **grouped by `(title, flag_reason)`** so identical rows collapse to one row with an occurrences badge. `summary_bleed`, pre-escalation `past_date` / `future_date` / `invalid_price`, and all `*_escalated` auto-flags are filtered out of this section's fetch — they live in Summary Review or the escalation sub-sections below. **Duplicate groups are read-only (Initiative 6)** — each row shows one `scraper_{provider_id}.py ↗` button per provider_id linking directly to the offending scraper file on GitHub (`https://github.com/lukeram-3420/backcountryfinder/blob/main/scraper_{provider_id}.py`), plus a Clear all button that writes a title-scoped suppression as a last resort. No Diagnose or Whitelist button — the resolution is always "open the scraper, fix the iteration logic, re-run." A **Date escalations** sub-section (Initiative 5) renders courses with `flag_reason IN ('past_date_escalated','future_date_escalated')` — per row: booking URL, copyable provider-email body, Clear (writes course-id-scoped suppression). A **Price escalations** sub-section (Initiative 4) renders `flag_reason='invalid_price_escalated'` — same card layout: current price, booking URL, copyable email body, Clear. A **Warnings** sub-section surfaces the `validator_warnings` table (email-only issues persisted by `validate_provider.py`): grouped by `(title, check_type)`, actions per type — `null_avail` → View (opens booking URL); `all_sold` → informational only. The Warnings section no longer surfaces price rows (Initiative 4 retired `price_outlier`/`null_price`) nor count-drop rows (Initiative 7 moved `count_drop` to the Providers tab as a yellow badge). Remaining `validator_warnings` check_types are `null_avail` + `all_sold`.
5. **Audit Log** — last 100 rows of `admin_log` with search filter.
6. **Pipeline** — three stacked sections sharing one tab. **Top: Discovery Cloud** — two lists (activity terms + location terms) that drive the weekly automated provider discovery search queries. Each term shows a weight bar, quality indicator (X found / Y skipped — warning at >80% skip rate with 5+ total), last-used date, and a single **Remove** button (toggles to **Restore** once removed). Remove is a soft-delete — it sets `discovery_cloud.active = false` rather than DELETEing the row. This matters because `refresh_discovery_cloud.py` preserves `active=false` on every run, so a removed term stays blocked permanently instead of being re-added next Sunday; a hard DELETE would re-appear on the next refresh. Manual **Add term** POSTs `{term, type, weight: 1, source: 'manual', active: true}` directly to `/rest/v1/discovery_cloud` with the authenticated session token. Populated by `refresh_discovery_cloud.py`, consumed by `discover_providers.py`. **Middle: Provider pipeline** — onboarding tracker backed by `provider_pipeline` table. Header has an **"Add provider"** button that opens an inline URL-only form: `admin-analyse-provider` runs Haiku web_search + Google Places lookup, slugifies the returned name, then POSTs to `provider_pipeline` (status='candidate', `id` = slug). Each non-live row (candidate/scouted/scraper_built) has a **"Copy prompt ↗"** button that copies a Claude Code instruction to the clipboard for building the scraper. **Client-side hide of already-live providers:** on every tab load, `loadPipelineTab` fetches active providers alongside pipeline rows and builds `activeProviderKeys = {domains, names}`. `renderPipelineTable` hides any pipeline row whose normalised website domain or lowercase name is in those sets. Domain comparison uses `domainOf()` which normalises via lowercase → strip `https?://` → strip `www.` → strip trailing `/`. No PATCH writes happen during display — the pipeline's own `status` column is not updated by the UI's filter logic; the status PATCH only fires via the inline Edit form. Excludes `status='skip'` from display. Columns: Name (linked to website), Location, **Rating** (`★ X.X (N)` / `★ —` / `—`), Platform, Complexity, Status (coloured badge: candidate=grey, scouted=blue, scraper_built=yellow, live=green, skip=faded), Priority (1/2/3), Notes (truncated to ~60 chars with full-text tooltip), Edit + Copy prompt, **Date added** (`provider_pipeline.created_at` rendered as ISO date `YYYY-MM-DD`, sliced from the raw timestamp; null/missing → `—`; rightmost column). Inline edit lets you change status/platform/priority/notes plus the Google enrichment fields (`google_place_id`, `rating`, `review_count`); the Date added cell stays read-only since it's system-generated. Name/Platform/Status/Priority/Date added headers are sortable; Date added sorts on the raw ISO timestamp via `cmpValues`' string compare (chronological), with null sink at the bottom. Pipeline `id` is a text slug — onclick handlers must quote it (`editPipelineRow('${id}')`) or it will be evaluated as a global variable. **Bottom: URL drift** — surfaces program URLs detected on a provider homepage that aren't in the scraper's hardcoded list. Only populated for scrapers with hardcoded URL lists (currently `scraper_yamnuska.py` and `scraper_cloud_nine_guides.py` — see `detect_url_drift()` in `scraper_utils.py`). Rows grouped by provider_id, each with **Add** (copies the URL with paste instructions for the scraper file, marks `reviewed=true, action='added'`) and **Reject** (`reviewed=true, action='rejected'`) buttons. Reviewed rows stay in the table but don't re-surface; the unique constraint on `(provider_id, url)` makes re-detection idempotent.
7. **Activity Tracking** — Persistent catalog of every `(provider, activity)` pair any scraper has ever seen (backed by `activity_controls`). Two admin-editable controls per row: **Visible** (bool — `false` hides from frontend AND stops the scraper from scanning, replacing the old hardcoded `EXCLUDE_TITLES` lists) and **Tracking mode** (`immediate` / `extended`, Zaui-only, picks which lookahead window the scraper uses for `fetch_unavailability` walks). Top of tab has two number fields ("Extended window (days)" / "Immediate window (days)") that write to `scraper_config` via `admin-update-scraper-config`. Provider dropdown + free-text title search filter the render — rows never drop out of the fetched set on toggle (the catalog is the source of truth, not a work-inbox). Bulk actions act on the currently-filtered set; bulk tracking-mode changes silently skip non-Zaui rows. All columns sortable. Columns: checkbox, Provider, Activity key, Title, Last seen, Visible, Tracking.
8. **Settings** — Static reference for the canonical location format (`City, Province`). Discovery Cloud UI moved to the Pipeline tab.

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
| `admin-toggle-provider` | Set `providers.active` and cascade to that provider's `courses.active`. Toggle OFF sets all courses to `active=false`. Toggle ON only restores courses where `avail != 'sold'` — preserves sold-out and notify-me courses. On ON it also flips matching `provider_pipeline` rows to `status='live'` (matched by normalised domain or lowercase name, same logic as the client-side hide in admin.html; skips rows already `live` or `skip`). On OFF it intentionally does NOT revert pipeline rows — once a provider has been onboarded it stays "live" in the pipeline even if temporarily disabled, so stale candidates don't resurface on the next weekly discover run. The admin UI's `toggleProvider` handler additionally dispatches `sync-algolia.yml` via `admin-trigger-scraper` after every successful toggle (in either direction) so `courses_v2` reflects the change within ~30 seconds instead of waiting for the next daily `scraper-all.yml` tick. Failure of the sync dispatch is logged to the toast but does not fail the toggle. |
| `admin-analyse-provider` | Accepts `{url}`, calls Claude Haiku with `web_search` tool to derive `{name, location, platform, complexity, priority, notes}`, then enriches with Google Places `{google_place_id, rating, review_count}`. Used by Pipeline tab "Add provider" form. Falls back to URL-derived defaults on Haiku failure. **Platform is detected deterministically**, not trusted from Haiku: `detectPlatform(url)` fetches the homepage and signature-matches against `PLATFORM_SIGNATURES` (rezdy/checkfront/zaui/fareharbor/bokun/peek/thinkific/shopify/wix/squarespace/woocommerce/wordpress). First match wins; Haiku's platform guess is used only when detection returns `unknown`. The signature table mirrors `discover_providers.py` and `admin-detect-platform` — keep all three in sync when adding a platform. **Places result passes three validation checks before being accepted** (else all three Places fields are nulled): (1) name similarity ≥ 0.4 between Haiku-derived name and Places-returned name (alphanumeric-only char overlap), (2) `user_ratings_total` ≤ 2000, (3) `place_id` not already assigned to a different `provider_pipeline` row. Each rejection logs a reason. |
| `admin-detect-platform` | Accepts `{table, id, url}` where `table` is `'providers'` or `'provider_pipeline'`. Fetches the URL, matches against the same `PLATFORM_SIGNATURES` table used by `admin-analyse-provider`, PATCHes the target row's platform column (`providers.booking_platform` or `provider_pipeline.platform` — indirection in `PLATFORM_COLUMN` const), logs to `admin_log` with the matched evidence pattern. Returns `{platform, evidence}`. Writes the result even on `unknown` so the UI reflects "we tried and nothing matched" rather than leaving a stale value. Wired to the inline **Detect** button on the Pipeline tab Platform cell. The `providers` table path is supported but not currently surfaced in the UI — kept for future use and for any scripted backfill that wants to route through the edge function rather than direct REST. |
| `admin-trigger-scraper` | Call GitHub Actions `workflow_dispatches` — requires `GITHUB_TOKEN` secret in Supabase Edge Functions settings. Accepts `{workflow_id, inputs?}`; `inputs` is forwarded to `workflow_dispatch` (used for `validate-provider.yml` which requires `provider_id`). |
| `admin-toggle-activity-control` | Activity Tracking tab writes. Accepts `{rows: [{provider_id, activity_key, visible?, tracking_mode?}]}` — single-row and bulk use the same shape. Validates `tracking_mode ∈ {'immediate','extended'}` when provided, requires `provider_id + activity_key` on every row. PATCHes `activity_controls` one row at a time (Supabase client has no true multi-row update). **Cascades to `courses.active`** when `visible` flips: reads the latest `title` from `activity_controls` then PATCHes matching `(provider_id, title)` rows — OFF sets all to `active=false`, ON sets them to `active=true` except `avail='sold'` (mirrors `admin-toggle-provider`). The response includes `courses_affected`. Writes one `admin_log` row per request. No-op rows (neither `visible` nor `tracking_mode` set) are reported back as errors rather than silently bumping `updated_at`. |
| `admin-update-scraper-config` | Activity Tracking tab config writes. Accepts `{key, value}`. Whitelist: `extended_lookahead_days` / `immediate_lookahead_days` only — unknown keys return 400. Value must parse as integer ∈ [1, 730]. UPSERTs `scraper_config` on conflict=key, writes `admin_log`. |

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

### Algolia location search must target `location_canonical`
In `algolia_sync.py`, `searchableAttributes` and `attributesForFaceting` must reference `location_canonical`, not `location` or `location_raw`. The canonical field is the clean `"City, Province"` string (e.g. `"Canmore, AB"`, `"Squamish, BC"`) that matches the BC/AB/etc. synonyms. `location_raw` is whatever string the provider's site happens to use — inconsistent format, no guaranteed province code — so searching against it silently drops results. Records push `location_canonical` + `location_raw` as separate fields; never collapse them into a single `location` key and never add `location_raw` to the searchable list. Bug history: an earlier version of the sync script mapped `"location": course.get("location_raw")` and listed `"location"` in `searchableAttributes`, which broke every location-based search.

### PostgREST upsert pitfalls (May 2026)

Three rules learned the hard way over PRs #53, #54, #55. All three of these failures are silent and look healthy in the scraper logs unless you grep for the specific error codes.

**1. `on_conflict=col1,col2` is required when the unique constraint isn't the primary key.** PostgREST's `Prefer: resolution=merge-duplicates` defaults to PK conflict detection. The `course_summaries` table's unique constraint is `(provider_id, title)` — not the primary key — so the merge would silently fall through to a plain INSERT and 23505 every time. `_upsert_course_summaries` in `scraper_utils.py` uses `?on_conflict=provider_id,title` for this reason. Any new table with a non-PK unique constraint needs the same treatment. Symptom of the missing param: `WARNING course_summaries upsert failed 409` on every run, plus a stale-data side effect because the row never actually updates.

**2. Never reintroduce a local `sb_upsert` that bypasses keyset batching.** `scraper_utils.sb_upsert` groups rows by their key tuple and POSTs one request per group. PRs #54 removed broken local copies from `scraper_altus.py` and `scraper_hangfire.py` that posted everything in one request — this triggered PGRST102 (`"All object keys must match"`) on any batch where some rows had `summary`/`search_document` fields and others didn't. The local copies also swallowed errors with `log.error()` instead of raising, so the courses upsert silently dropped rows for weeks. **Audit:** `for f in scraper_*.py; do grep -l "^def sb_upsert" $f; done` should return zero hits except `scraper_utils.py`.

**3. V2 stable id collisions on per-day-multi-slot platforms (FareHarbor, possibly future).** The V2 id format `{provider_id}-{date_sort}-{title_hash}` collapses morning/afternoon slots of the same product on the same date into one row. Multiple distinct upstream `pk`s mapping to the same V2 id end up in one upsert batch → Postgres returns code 21000 (`"ON CONFLICT DO UPDATE command cannot affect row a second time"`). Add a `seen_ids = set()` filter loop right before `sb_upsert("courses", rows)` — pattern at `scraper_vibe_backcountry.py:457` and `scraper_altus.py:1054`. The collision is per-batch, so even small batches hit it. Symptom: `ERROR Supabase upsert error 500: {"code":"21000",...}` in the scraper log, followed by a hard exit and zero rows upserted.

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

## Progression pages

### Status
Phase 1 shipped 2026-05-03. Renders static HTML progression pages from `provider_progressions` + `progression_steps` Supabase tables via `generate_progression_pages.py`. Pages are not interactive yet — bundle inquiry form (Phase 2) and FAQ Q&A (Phase 3b) are stubs. Design spec lives at `design-spec/`.

### Architecture
- Build script `generate_progression_pages.py` runs every 6 hours and on `repository_dispatch` events of type `progression_updated`.
- Bundle math computed live from current `courses.price` values via `(provider_id, course_title)` lookup — scraper updates flow through automatically on next build.
- Capstone styling driven by `progression_steps.is_capstone = true`. Exactly one per progression.
- Hero image: `provider_progressions.hero_course_title` resolves to the matching course's `image_url`, falls back to capstone step's course.
- Schema.org: `Course` per rung, `BreadcrumbList`, `HowTo`, `FAQPage` (empty until Phase 3b).
- Stylesheet: `css/progression.css` is loaded by every generated page; the page's `<head>` defines the global tokens inline (matching `index.html`) so the CSS file relies on host-page tokens.
- Generated output lives at `/<provider_id>/<slug>/index.html`. URLs resolve natively via Vercel's folder/index.html static serving — no rewrite needed in `vercel.json`.

### Schema deviation from Phase 1 brief
The brief specified `uuid REFERENCES providers(id)` and `uuid REFERENCES courses(id)`. Both `providers.id` and `courses.id` are actually `text` in this database, and `courses.id` is unstable per session (encodes date_sort). The shipped schema uses `text` for the provider FK and references courses by `(provider_id, course_title)` instead of by `courses.id`. Documented in `progressions_schema.sql` header.

### Adding a new progression
1. Insert into `provider_progressions` (active = false)
2. Insert 5+ rows into `progression_steps`, exactly one with `is_capstone = true`
3. Confirm bundle discount %s with provider
4. Set `active = true`
5. Trigger `build-progressions.yml` manually or wait for next 6-hour cron
6. Provider page (the providers tab card) auto-detects active progressions and surfaces a "Curriculum paths" link

### Conventions
- One progression per provider per season
- Slug must match URL segment exactly: `summer-progression`, `winter-progression`
- `practice_gap_text` is the connector *before* a step (null for step 1)
- `gear_text` lives on the step, not the course (progression-context-specific)
- `design-spec/` files are source of truth for the visual layout — update them alongside any visual changes

### Phase 2.5 — Editorial FAQ seed
Shipped 2026-05-04. Added `faq_items jsonb NOT NULL DEFAULT '[]'::jsonb` column to `provider_progressions`. Build script renders FAQ items from this column when non-empty, falls back to the empty-state card otherwise. `FAQPage` JSON-LD `mainEntity` now populated from the same data; provider-attributed answers add an `author` Organization for E-E-A-T. MSAA seeded with 7 editorial FAQs via `progressions_phase_2_5_schema.sql`.

The "Ask {provider} directly" form remains inert — Phase 3a/3b will wire it.

### Editing FAQs
Direct jsonb edits in Supabase Studio. No admin UI at this scale. Schema per item:
- `question` (text)
- `answer` (HTML — paragraphs, occasional `<strong>`; trusted, not sanitized)
- `source` (`'editorial'` | `'provider'`)
- `reviewed_date` (ISO `YYYY-MM-DD`, only when `source='provider'`)
- `display_order` (1-indexed int)

When Phase 3a/3b ship, the build script will merge `faq_items` (editorial + provider-attributed) with `progression_questions WHERE status='published'` (user-submitted) at render time. No data migration needed — the two sources coexist.

### FAQ conventions
- `source: 'editorial'` — no badge on the page (Luke wrote it).
- `source: 'provider'` — green "✓ Answered by {provider name}" badge plus formatted reviewed date. The badge text uses the provider's friendly name from the joined `providers` row at render time, so the schema generalises across all providers without per-provider source values. Must include `reviewed_date`.
- HTML in `answer` is trusted — no sanitization in the build script.
- Items sorted by `display_order` ascending, ties broken by question alphabetical (deterministic builds).
- First item by sort renders expanded by default — gives crawlers and LLMs immediate visible content without a click.
- The build script enriches each item with `reviewed_date_display` (e.g. "Reviewed 14 April 2026") via `format_review_date()` so the template stays presentation-only.

## SEO landing pages — strategy and architecture

### Status

Not started. Strategy crystallized April 2026. Execution scheduled after Initiatives 1 and 2 (data quality cleanup) land and stabilize, and after 4+ weeks of fresh `course_availability_log` / `course_price_log` data has accumulated to power the data moat.

### Goal

Build a network of server-rendered SEO landing pages (~15-30 in V1, scaling to 50+) targeting transactional and informational queries for outdoor course discovery in BC and Alberta. Zero ad spend strategy — organic traffic is the only growth lever. Pages serve dual intent: convert in-season visitors to bookings, convert off-season visitors to notify-me email captures that convert to bookings months later.

### Why current architecture fails for SEO

The entire live site is one URL (`/`) with client-rendered Algolia content. Google sees an empty shell on first paint. JavaScript rendering happens in a delayed second pass that is slow, unreliable for low-authority sites, and costs Core Web Vitals points. Without server-rendered HTML at unique URLs, the site cannot rank for transactional queries regardless of content quality.

### Page architecture

One long page per topic, no sub-URLs for FAQ or supplementary content. Splitting FAQ to a sub-URL dilutes the parent page, splits link equity, and harms both pages. All content for a topic lives at one URL.

Page structure top-to-bottom:

1. **H1 + subhead** matching the search query exactly. H1 is dynamic — when zero "available now" courses exist, H1 becomes "{Topic} — Join Waitlist" form to manage SERP click intent.
2. **Course grid above the fold** — server-rendered static HTML cards, split into "Available now" (courses with confirmed dates) and "Notify me when available" (courses without current dates, with notify-me CTAs).
3. **Quick Stats box** — data-derived facts pulled from logs: average price, availability count, next available date, fill rate. Renders directly from DB queries, no AI involvement.
4. **Adaptive paragraph** — Haiku-generated, regenerated per scrape. Reflects current course state ("12 courses available starting December 12, prices range $295-$495, 3 providers added new dates this week"). Strictly data-derived statements, no editorial claims.
5. **Stable topical intro** (`<h2>About {topic}</h2>`) — hand-written, 200-300 words. What the topic is, who needs it, prerequisites, what to expect. The topical authority anchor.
6. **Region-specific section** (location pages only) — hand-written, 100-200 words. Local terrain, landmarks, conditions, distinctive characteristics. Required for non-cannibalization. Squamish page mentions Diamond Head, Garibaldi, Round Mountain. Whistler page mentions Spearhead Traverse, Singing Pass. Revelstoke page mentions Rogers Pass tenure.
7. **Featured providers strip** — horizontal row of provider logos with star ratings, linking to provider pages. Internal linking distribution.
8. **FAQ accordion** — 6-12 items with FAQPage JSON-LD schema. Hand-written for top 5-10 pages, Haiku-generated for lower-priority pages.
9. **Related pages footer** — 3-5 internal links to related topics. Breadcrumb above (Home → Activity → Location).

Content density: 200-300 words is sufficient for low-competition long-tail queries. Top 5-10 pages targeting head terms ("avalanche course Canada," "ski touring courses BC") need 800-1500 words to compete with established outdoor education sites.

### URL structure

Flat hierarchy. No parent/child URL nesting (rejected after consultant review — flat structure with strong internal linking and BreadcrumbList schema delivers the same ranking signal at zero engineering cost and stays cleaner for international expansion).

| Pattern | Example |
|---|---|
| Activity + region | `/courses/ast-1-courses-bc` |
| Activity + location | `/courses/ski-touring-squamish` |
| Activity hub | `/courses/ski-touring` |
| Region hub | `/regions/squamish` |
| Provider | `/providers/altus-mountain-guides` |
| Informational | `/learn/what-is-ast-1` |

Conventions: hyphens not underscores, lowercase only, trailing slash consistency (pick one, 301 the other). Vercel handles via rewrites in `vercel.json`: `{ "source": "/courses/:slug", "destination": "/seo/:slug/index.html" }`.

International expansion: when US pages launch, pattern becomes `/courses/ski-touring-wa` (Washington) parallel to `/courses/ski-touring-bc`. `hreflang` tags become required at that point, not before. Currency and activity terminology (Americans search "avalanche course" not "AST") will need US-specific variants — page generation script must keep these configurable per region from day one.

### The seasonality solution

Most guide companies leave course pages live year-round even when no dates are published. Scrapers retain these as `notify_me` mode rows with the course tile rendering and a notify-me CTA. When dates go live, users who clicked notify-me get emailed.

This converts the off-season problem into a structural advantage:
- SEO pages stay populated and content-rich year-round, no seasonal dead zones
- Off-season visitors convert to email captures → conversions months later
- Page identity stays stable for Google (always about the same topic, just different mix of available vs notify-me)
- The notifications table becomes a real-time demand signal no competitor has

Implementation requirement on the data layer: scrapers must persist dateless courses across runs as `notify_me` mode rows rather than dropping them. V2's `{provider_id}-flex-{title_hash}` ID format already handles this — the change is broadening what counts as flex-date to include "currently undated."

### The data moat

Two append-only intelligence tables (`course_availability_log`, `course_price_log`) capture how courses fill up and how prices move over time. This is content nobody else in the market has. Powers unique-content callouts on landing pages: "filling fast," "price dropped 15%," "newly listed by 3 providers this week," "average AST 1 price in Squamish has risen 8% since last winter."

Generic aggregators cannot produce this content. Google rewards uniqueness ("information gain"). This is the strongest differentiation lever the site has.

Activation timeline: needs 4+ weeks minimum of post-purge log data for velocity signals to be statistically meaningful, ideally 8-12 weeks. Logs were purged 2026-04-16. Earliest meaningful activation is roughly mid-May 2026.

### The Algolia hydration pattern (CLS critical)

SEO pages are server-rendered static HTML. The existing `js/search.js` Algolia InstantSearch must NOT auto-initialize on these pages — doing so causes Cumulative Layout Shift as Algolia replaces static cards, which directly harms ranking.

Pattern:
- SEO page template includes `<body data-seo-page="true" data-filter-activity="..." data-filter-location="...">` 
- `js/search.js` `initSearch()` checks for `data-seo-page="true"` on body
- If present: skip auto-search, leave server-rendered cards in place, attach one-time interaction listener to search box, filter dropdowns, date input, and pagination
- On first user interaction: initialize Algolia with the page's pre-set filters and replace the static cards with the live grid
- When Algolia activates, first render must visually match the static cards (same design, spacing, image dimensions) to avoid layout shift on activation

Most SEO traffic never interacts with filters (came for a specific query, converts or bounces) — Algolia never loads at all on those visits. Free performance win.

### The cannibalization problem

With 30+ pages targeting overlapping queries, multiple pages can compete for the same query. Google picks one and "omits" the others. Two prevention rules:

1. **Each child page needs substantive location-specific content.** Generic mentions ("Squamish is great for skiing") fail. Real local content — terrain, landmarks, conditions, what's distinctive — passes. This is hand-written by the founder, not Haiku-generated, because hallucinated specifics are worse than no specifics.

2. **Hub pages and child pages serve different intent.** `/courses/ski-touring-bc` (hub) is for "I don't know where to go yet" — comparison content, beginner regions, spring skiing recommendations. `/courses/ski-touring-squamish` (child) is for "tell me about this place." Different content, no overlap.

The temptation to copy-paste structure across child pages and let Haiku swap city names is exactly what gets pages cannibalized. Each child page is written like it's the only page on the site for that region.

### Internal linking architecture

The flat URL structure depends on serious internal linking. Without it, the homepage has no crawlable path to niche pages and Google never finds them.

Required link surfaces:

| Page type | Outbound links |
|---|---|
| Homepage | "Browse by activity" section (8-12 cards), "Browse by region" section (6-10 cards), footer with two columns: "Popular Activities" + "Popular Regions" (top 8 each) |
| Activity hub (e.g. `/courses/ski-touring`) | "{Activity} by region" section listing all child pages with anchor text matching child H1, sidebar/footer "Related activities" linking laterally |
| Region hub (e.g. `/regions/squamish`) | "Activities in {region}" section listing all activity-location pages, link to parent activity hub ("More ski touring across BC") |
| Activity-location child (e.g. `/courses/ski-touring-squamish`) | Breadcrumb (Home → Ski Touring → Squamish) with BreadcrumbList schema, "Related" footer linking to other locations for same activity, link to parent activity hub |
| Provider page (e.g. `/providers/altus-mountain-guides`) | Links to each location-activity page where this provider operates; reciprocally linked from every activity-location page that lists this provider |

Footer appears on every page, distributing link equity site-wide. Every SEO page reachable from homepage in 2-3 clicks via multiple paths.

`generate_seo_[pages.py](http://pages.py)` knows page relationships (which activities exist in which regions, which providers operate in which locations) by querying `courses` and joining on provider — this data already exists, the script just denormalizes it into navigation at build time.

### Content generation split

| Content type | Author | Regeneration cadence |
|---|---|---|
| Stable topical intro (top 15 pages) | Founder, hand-written | Once, edited rarely |
| Region "about" sections (top 10 locations) | Founder, hand-written | Once, edited rarely |
| FAQ items (top 5-10 pages) | Founder, hand-written | Once, edited rarely |
| FAQ items (lower-priority pages) | Haiku, audited periodically | On significant data change |
| Meta descriptions | Haiku, ~155 chars, formulaic template | Per scrape |
| Adaptive "current state" paragraph | Haiku, strictly data-derived | Per scrape (every 6 hours) |
| Quick Stats box | Direct DB rendering | Per scrape |
| Course tiles | Direct DB rendering | Per scrape |
| Annual "State of Backcountry" report | Founder, hand-written from log analysis | Annually |

Principle: AI handles content where errors are correctable and low-impact (data-summarization, meta descriptions). Founder handles content where authenticity and accuracy are the differentiators (topical claims, regional knowledge, FAQ on top pages). Haiku never makes editorial claims about terrain or logistics — only data-derived statements.

### Technical SEO requirements

Non-negotiable for V1:

- **Course schema** with `provider` and `offers` attributes — triggers price-and-date rich snippets in SERPs
- **FAQPage schema** for FAQ accordions — triggers FAQ rich results
- **BreadcrumbList schema** on all child pages — internal linking and crawlability boost
- **Sitemap.xml** generated by build script, submitted to Search Console and Bing Webmaster Tools
- **Robots.txt** allows `/courses/*`, `/providers/*`, `/regions/*`, `/learn/*`; disallows `/admin`
- **Canonical tags** on every page pointing to itself
- **Last-Modified headers** sent by Vercel on static HTML so Googlebot does not waste crawl budget on unchanged pages (verify Vercel default behavior; should work out of the box)
- **OpenGraph tags** for social shares
- **Core Web Vitals** measured with PageSpeed Insights once V1 ships — static HTML on Vercel scores well by default but verify

### Build architecture

Static generation at scrape time. Fits existing Python + GitHub Actions + static deploy pattern. No new infrastructure, no Next.js migration, no runtime serverless functions.

Components:

- `seo_pages.yml` — config defining each page: slug, page type, title, h1, filters, stable intro path, FAQ items
- `seo_content/` — directory of hand-written markdown files, one per page (intros, region content, FAQ where hand-written)
- `templates/seo_page.html` — Jinja2 template with the page architecture above
- `generate_seo_[pages.py](http://pages.py)` — loads config, queries Supabase, optionally calls Haiku for adaptive content, renders each page, writes to `/seo/{slug}/index.html`, generates `sitemap.xml`
- `vercel.json` — rewrite rules mapping `/courses/:slug`, `/providers/:slug`, `/regions/:slug`, `/learn/:slug` to `/seo/{slug}/index.html`
- `scraper-all.yml` — adds final step running `generate_seo_[pages.py](http://pages.py)` after `algolia_[sync.py](http://sync.py)`

Build runs every 6 hours alongside scrapers. Static HTML pushed to repo, Vercel auto-deploys.

### Search Console and feedback loop

Search Console + Bing Webmaster Tools setup is week-1 work, not deferred. Feedback loops to monitor weekly for first 3 months:

| Report | What to watch |
|---|---|
| Performance | Long-tail queries the site is accidentally ranking for — these become next page-build targets |
| Coverage | Pages indexed vs "Crawled - currently not indexed" (quality warning) vs "Duplicate, Google chose different canonical" (cannibalization warning) |
| Enhancements | Schema implementation errors — Course schema, FAQPage, BreadcrumbList |
| URL Inspection | Force-crawl high-value pages on launch |

The Performance tab specifically drives the page-expansion roadmap. Build new pages for queries the site is already getting impressions for at positions 11-30 — these are highest-ROI builds because Google has already decided the site is relevant.

### Off-page strategy

Three high-ROI moves for zero-budget niche aggregator:

1. **Provider reciprocity backlinks.** Pitch: "We've featured you on our provider page. Would you mind linking to your listing from your 'As Seen In' or 'Partners' section?" Use exact phrasing. Altus relationship is the test case — 12 providers were notified at launch, retain warm contacts.

2. **Annual "State of Backcountry" report.** Once per year, use logs to publish data report: "Average AST 1 price increased X% in 2026," "Squamish climbing courses fill 40% faster than 2023." Pitch to Mountain Life, Pique News Magazine, Gripped, Powder Canada. One feature is worth months of grinding.

3. **Reddit and community presence.** r/Vancouver, r/CanadaSkiing, r/backcountry, r/climbing. Be a useful person who runs the site, not a person promoting the site. Six months of presence before expecting anything. Founder's firefighter/local angle is credibility asset.

Skip: TikTok, Twitter/X, cold influencer outreach, listicle backlink schemes.

### Realistic timeline

For a low-authority domain in a niche this small, executed well:

- **Months 1-3:** Sandbox period. Pages get indexed but rank in positions 30-50. Focus on technical SEO correctness and content quality. Watch for any impressions in Search Console as early signal.
- **Months 3-6:** Long-tail climb. Begin ranking for specific queries ("AST 1 Squamish March 2026 available"). 10-50 monthly impressions per page. Notify-me captures begin accumulating.
- **Months 6-12:** Authority phase. Better long-tail rankings, occasional top-10 for head terms. Compounding internal linking + accumulated backlinks + log-data freshness signals. 20-30% MoM organic growth becomes plausible.
- **Months 12+:** State of Backcountry report publication unlocks higher-DR backlinks. Real authority accumulates.

Hockey-stick growth curves are real but later than commonly expected. Plan for nothing visible for 60-90 days, then gradual compounding.

### Execution sequence (when SEO build kicks off)

Prerequisites: Initiatives 1 and 2 landed, 4+ weeks of fresh log data, keyword research completed, foundational content hand-written.

Week 1 — technical guardrails:
- Search Console + Bing Webmaster Tools setup
- Verify Vercel `Last-Modified` header behavior
- Implement lazy-Algolia pattern in `js/search.js` for SEO pages
- Confirm robots.txt and sitemap.xml infrastructure

Week 2 — content + structure foundation:
- Hand-write 10-15 stable topical intros (the "Power 10")
- Hand-write 8-10 "About this region" sections for top locations
- Design and document internal linking architecture (which pages link where, footer structure, breadcrumb pattern)

Week 3 — build pipeline:
- `generate_seo_[pages.py](http://pages.py)` with hub-and-spoke linking output
- Course + FAQPage + BreadcrumbList schema implementation
- Quick Stats box rendering from logs
- Dynamic H1 logic for empty-availability case
- Homepage "Browse by" sections + footer sitemap

Week 4 — ship and validate:
- Deploy V1 pages (5-10 to start)
- Submit sitemap, request indexing on top 5 via URL Inspection
- Verify Core Web Vitals
- Verify schema implementation in GSC Enhancements

Month 2 — backlinks + monitoring:
- Provider reciprocity outreach
- Reddit presence ramp
- Weekly GSC Coverage and Performance checks

Months 3-6 — scale and refine:
- Layer Haiku-generated adaptive content as logs mature
- Expand to 30-50 pages based on GSC query signals
- Begin drafting State of Backcountry report

Month 6+ — compounding:
- Annual report publication and PR pitch
- Continue scaling pages based on data signals

### Open questions to resolve before build

- Keyword research output: which specific 15-30 queries to target in V1 (requires 4-6 hour focused research session using Search Console, Keyword Planner, autocomplete, competitor SERPs)
- Whether to fold informational pages (`/learn/*`) into V1 or defer to V2
- Whether `/regions/{slug}` hub pages launch in V1 or wait for child pages to mature first
- Exact cadence and audit process for Haiku-generated FAQ items on lower-priority pages

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
| price_tier | text | Which tier the price was resolved from (Zaui) — `'adults'` / `'seniors'` / `'inferred_min'` / `'scalar_<field>'` / `'array_<field>'`. Null for non-Zaui providers. |
| price_has_variations | boolean | default false. True when the activity has ≥2 distinct positive tier prices. Drives the `↕ Price varies` chip on the card. |
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

**Known historical pollution (pending Phase 5 cleanup):** rows scraped between `2026-04-16` (V2 cutover, when logging started) and `2026-04-29 02:30:00 UTC` (PR #41 merge) for providers `aaa` and `girth-hitch-guiding` may carry fake `spots_remaining=1, avail='critical'` for binary-flag Checkfront products. The pre-PR-41 scrapers ran a global probe across the catalog `/api/3.0/item/cal` response — if any product anywhere in the catalog returned `>1`, every other product's `1` was misread as "1 spot left". The fix landed in PR #41 (per-item `detect_checkfront_spot_counts` in `scraper_utils.py`); going forward, binary-flag products correctly log `spots_remaining=null, avail='open'`. The contaminated historical rows must be filtered out before Phase 5 velocity signals can be computed for these providers — see "Phase 5 prerequisites" below.

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
| price_tier | text | Which price tier the logged value came from (Zaui `extract_zaui_price` output — `'adults'` / `'seniors'` / `'inferred_min'` / etc.). Null for non-Zaui providers and for rows logged before the tier column landed. Phase 5 velocity signals should filter to a single tier per course so drift analysis stays apples-to-apples. |

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

### provider_pipeline
| column | type | notes |
|---|---|---|
| id | text | primary key — slug derived from provider name |
| name | text | |
| website | text | |
| location | text | `City, Province` |
| platform | text | `rezdy` / `zaui` / `checkfront` / `fareharbor` / `wordpress` / `unknown` etc. — detected by `admin-detect-platform` or `discover_providers.py` |
| complexity | text | `low` / `medium` / `high` — Haiku-derived signal for onboarding difficulty |
| status | text | `candidate` / `scouted` / `scraper_built` / `live` / `skip` |
| priority | integer | 1 (high) / 2 / 3 (low) |
| notes | text | free-text triage notes |
| google_place_id | text | enriched by `admin-analyse-provider` |
| rating | numeric | Google Places |
| review_count | integer | Google Places |
| discovered_by | text | `auto` (script) / `manual` (admin-added). Null counts as manual |
| discovery_query | text | which discover_providers.py search query found this row (debugging) |
| created_at | timestamptz | not null, default now() — surfaces as the Pipeline tab's Date added column. Schema migration: run `ALTER TABLE provider_pipeline ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();` once in Supabase SQL editor. Idempotent. The migration backfills existing prospects with the moment the column was added (real historical insert dates aren't recoverable). New inserts via `discover_providers.py` and the admin "Add provider" form auto-populate via the column default. |

Onboarding tracker for provider candidates. Populated by `discover_providers.py` (auto) and the admin Pipeline tab "Add provider" form (manual). Consumed by the Pipeline tab and by `admin-toggle-provider` (which flips matching rows to `status='live'` on provider activation). Live providers are hidden from the tab via a client-side domain/name match against `providers`, so the table accumulates history instead of being mutated on every onboarding event.

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

### activity_controls
| column | type | notes |
|---|---|---|
| id | bigserial | primary key |
| provider_id | text | not null |
| activity_key | text | not null — unified prefixed dedup key (`zaui:{id}` or `title:{hash}`) |
| title | text | not null — latest observed title |
| upstream_id | text | nullable — Zaui numeric id when available |
| title_hash | text | nullable — 8-char md5 fallback used by non-Zaui rows |
| platform | text | nullable — `'zaui'` / `'rezdy'` / `'checkfront'` / `'fareharbor'` etc. Written by scraper on upsert |
| visible | boolean | default true. false = hide from frontend AND stop scraper scan |
| tracking_mode | text | default `'immediate'`, check `tracking_mode IN ('immediate','extended')`. Zaui-only semantically — drives `fetch_unavailability` window pick |
| last_seen_at | timestamptz | default now(), refreshed every scraper run |
| updated_at | timestamptz | default now() |

Unique on `(provider_id, activity_key)`. Indexed on `provider_id` and `visible`. Populated by scrapers (one upsert per seen activity per run); admin writes `visible` and `tracking_mode` via `admin-toggle-activity-control`. Seed from historical `EXCLUDE_TITLES` lists via `seed_activity_controls.py`.

### scraper_config
| column | type | notes |
|---|---|---|
| key | text | primary key |
| value | text | not null |
| updated_at | timestamptz | default now() |

k/v config written by the Activity Tracking tab. Two canonical keys seeded on DDL: `extended_lookahead_days` (default `'180'`) and `immediate_lookahead_days` (default `'14'`). Edge function `admin-update-scraper-config` validates keys against a whitelist and values against an int [1,730] range. Zaui scrapers read via `load_lookahead_windows()`.

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
| — | Activity Tracking dashboard (Initiative 8 of data quality mission) | Complete — new `activity_controls` + `scraper_config` tables, new admin tab, two edge functions (`admin-toggle-activity-control` / `admin-update-scraper-config`), new seed workflow (`seed-activity-controls.yml`). **All 23 active scrapers** upsert `(provider_id, activity_key)` for every activity they see and gate expensive work behind `visible` (only `scraper.py` legacy monolith and `scraper_aaa_details.py` enrichment pass don't participate). Per-scraper `EXCLUDE_TITLES` lists retired: 5 real lists (altus, vibe, girth_hitch, cloud_nine, bow_valley) seeded into `activity_controls(visible=false)` via `seed_activity_controls.py`, then constants and call sites deleted; 4 empty `EXTRA_EXCLUDE_TITLES = []` constants also removed (canmore, banff, mt_norquay, toby_creek). Visible toggle **cascades to `courses.active`** on flip so the frontend hides / re-shows immediately without waiting for the next scraper run — makes Visible a general-purpose "hide this course" control. Zaui scrapers additionally pick per-activity lookahead from `tracking_mode` (`immediate`/14d or `extended`/180d by default) — projected ~90% reduction in `fetch_unavailability` calls on Banff Adventures once most activities default to immediate. Structural Zaui filters (hotels/transfers/categories/substring DEFAULT_EXCLUDE_TITLES) stay as code in `scraper_zaui_utils.py`. Tab degrades gracefully when the schema tables are missing (setup hint instead of JS crash). |
| — | Algolia server-side dedup + card-button unification | Complete — `algolia_sync.py group_courses_for_algolia()` emits one record per `(provider_id, title_hash)` with a `dates[]` array + `next_date_sort` scalar; `customRanking` switched to `asc(next_date_sort)`. `mapHit()` reads the new shape directly. `groupCoursesForCards()` is now passthrough on the search path. Multi-date affordance now visible on the default index page (no longer dependent on density-after-filter). Results count shows unique courses (~3-5k), not session count. Save button unified across primary + expanded session rows. `js/saved.js` `_dsKey()` coerces `date_sort` to string-or-null on store/lookup so the saved visual state actually flips on click. `renderSaved()` now renders one card per saved entry (no grouping) — saving feels per-date. |
| — | Sort + filter controls on the Search page | Complete — Location dropdown added inside the search bar between `#search-query` and `#search-date`; populated from a single Algolia facet query in `populateLocationDropdown()` (sorted A→Z, "Anywhere" prepended, course count appended), feeds a `location_canonical:{val}` facetFilter into `applyConfigFilters()` alongside the existing provider-deep-link filter. Price toggle pill added below the search bar (`#sort-price`); `cyclePriceSort()` cycles `off → asc → desc → off` and swaps the active Algolia index via `search.helper.setIndex()` between the primary and two new replicas (`courses_v2_price_asc` / `courses_v2_price_desc`), declared on the primary in `algolia_sync.py configure_index()`. Replicas inherit records / searchable attrs / faceting; only `customRanking` differs. Synonyms re-pushed to all three indexes (replicas don't auto-mirror synonyms). Lazy-init (PR #39) extended — location-dropdown focus/change and price-pill click also trigger Algolia activation on SEO pages. |
| 5 | Velocity signals (fill rate, price trend) | Not started — needs 4+ weeks of log data + completion of "Phase 5 prerequisites" (see below) |
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
`algolia_sync.py` pushes V2 courses to Algolia index `courses_v2`. Uses `replace_all_objects` for atomic full replacement — stale records are automatically removed. Configured with searchable attributes, facets, custom ranking, and activity/location synonyms (synonyms retained as free-text relevance boosters even after the activity facet was removed). Runs automatically after every `scraper-all.yml` run (daily at 00:00 UTC) with `--skip-settings`. Also available as standalone `sync-algolia.yml` workflow for manual triggers or settings reconfiguration.

### V2 Phase 4 — V2 frontend (implemented)
Algolia InstantSearch is live in `index.html` and replaces the Supabase-backed search stack on the Search page:
- **Search box** wired via `connectSearchBox`
- **Activity + Location dropdowns** fully removed — free-text Algolia search on `search_document` covers both. The Location synonyms + provider-name searchable attrs + date numericFilter handle the filter use cases the dropdowns used to serve.
- **Date filter** converted to a unix-timestamp `numericFilter` against `max_date_sort` (with `next_date_sort` OR fallback for transition safety). See "Date filter semantics" under the Algolia record schema for the full reasoning.
- **Provider deep link** (`?provider=`) applies as an Algolia `facetFilters` constraint rather than a Supabase `eq.` filter
- Old Supabase search functions are commented out (not deleted) as a fallback reference until V1 cutover
- `courses_v2` is the single source of truth for the search grid, synced once daily by `scraper-all.yml`'s final step and on-demand via `sync-algolia.yml`

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

### Card redesign (V2 multi-session card — implemented)
Live. Each course card consolidates all upcoming sessions of the same course title into one card with a primary session row + expandable secondary rows. **Grouping is now server-side**: `algolia_sync.py group_courses_for_algolia()` emits one Algolia record per `(provider_id, title_hash)` with the full `dates[]` array attached. `mapHit()` reads the pre-grouped record directly into a synthetic card object. `groupCoursesForCards()` is now passthrough on the search path (synthetics already arrive grouped) and only does real grouping for the saved-list path where `mapSupabaseRow()` still feeds per-session rows.

**Files involved:**
- [js/cards.js](js/cards.js) — `mapHit()` reads the pre-grouped Algolia record (top-level fields + `dates[]` array) and returns a fully-formed synthetic card object with `sessions[]`, `_group_key`, `has_more_sessions`, `velocity_fill_pct: null`, etc. `title_hash` is read from the explicit field with a slice-from-objectID fallback. **Session trim (PR #103):** `mapHit()` calls `_getUserDateTs()` to read the search-page `#search-date` value, drops sessions whose `date_sort < ts` from the synthetic, and recomputes `price_min` from the visible subset. Falls back to the full set when filtering would empty the group. `_getUserDateTs()` returns null in non-browser contexts and when the input is missing/empty, so the trim is a no-op outside the search page. `mapSupabaseRow()` stays per-session and intentionally doesn't apply the date trim (Supabase data shape didn't change; saved-list / shared-list views show every saved date regardless of the search-page filter). `groupCoursesForCards()` detects pre-grouped synthetics and passes them through; per-session inputs (saved-list path) take the bucket-and-collapse branch. `renderCards()` calls `groupCoursesForCards()` then iterates `buildCard()`. `buildCard()` always receives a synthetic group object. `_sessionRow()` renders both primary and expanded session variants from the same data. The save button class is now **unified** (`.save-btn` + optional `.save-btn-icon`-style mobile collapse via the existing breakpoint) — primary and expanded rows render identical save buttons. `toggleSessionList()` is the inline expand/collapse handler.
- [js/saved.js](js/saved.js) — saved shape changed from `string[]` to `{id: string, date_sort: string|null}[]`. `getSaved()` migrates legacy bare-string entries on read AND coerces every `date_sort` to a string-or-null via `_dsKey()` so subsequent strict-equality lookups don't false-negative on number-vs-string mismatch. `isSaved(id, date_sort)` matches on both fields after normalising the lookup `date_sort` through `_dsKey()`; passing `date_sort=null` does a whole-course "is anything from this title saved" check. `toggleSave(id, date_sort)` re-renders the affected card via `rebuildSyntheticForKey()`; falls back to in-place class swap when `currentCourses` doesn't carry the matching group (e.g. on the My List page where `currentCourses` holds search-page state). `renderSaved()` renders **one card per saved entry** (no grouping) — saving feels per-date, the saved list mirrors that.
- [js/search.js](js/search.js) — `customInfiniteHits` connector now hands ungrouped hits straight to `renderCards(mapped, false)` instead of inline-mapping `mapped.map(buildCard).join('')`. `renderCards()` owns empty-state markup, `#card-grid` write, `#results-count` text, and `addRemoveReadyListeners()`.
- [index.html](index.html) — new CSS only (no structure / JS changes). New classes: `.card-rating-tag`, `.card-meta-line`, `.card-price-row` / `.card-price-block` / `.card-price-from` / `.card-price-divider`, `.velocity-widget` (`.filling` / `.almost`), `.card-session-divider`, `.card-sessions`, `.session-row` (`.session-row-primary` / `.session-row-expanded`), `.session-date` / `.session-date-sm`, `.session-spots`, `.book-btn-sm`, `.more-dates-btn`, `.session-list-expanded`, `.more-dates-hint`. All inherit existing tokens (`--bg-card`, `--border`, `--green-dark`, etc.) — no new CSS variables. The previous `.save-btn-icon` rules were dropped along with the icon-only-button variant; primary and expanded session rows now share `.save-btn` styling.

**Synthetic card shape** (output of `groupCoursesForCards()`):

```js
{
  id, provider_id, title, title_hash, _group_key,
  summary, search_document,
  location_canonical, location_raw, duration_days, image_url, booking_mode,
  providers: { name, rating, review_count, logo_url },
  _queryID, _position,
  price_has_variations, price_min,
  sessions: [           // ascending by date_sort
    { id, date_display, date_sort, price, avail, spots_remaining,
      booking_url, custom_dates, booking_mode, _queryID, _position }
  ],
  has_more_sessions,    // true when sessions.length > SESSION_VISIBLE_CAP (4)
  velocity_fill_pct: null,         // null until V2 Phase 5
  velocity_days_to_book: null,
}
```

**Layout (top-to-bottom):**
1. Hero image — `c.image_url` or `FALLBACK_IMG`. Bottom-left: `.card-provider-tag`. Bottom-right: `.card-rating-tag` (only if `providers.rating` present).
2. Title — `c.title` (existing `.card-title` style).
3. Summary — `c.summary` (existing `.card-summary` style).
4. Meta line — `location · N day(s)`, single line, `.card-meta-line`.
5. Price + velocity row — price block (`FROM` label if `price_has_variations`, otherwise just amount). Centred when no velocity widget; left-aligned with vertical divider when velocity widget visible. Velocity widget itself is `display:none` when `velocity_fill_pct === null` — never a placeholder.
6. Horizontal divider (`#f0f0f0`, 1px) above the session area.
7. Primary session row — date (bold) + Book Now & My List buttons on the right; avail chip + spots text below. Wires `logClick()` + `trackAlgoliaConversion()` with the primary session's id / booking_url / `_queryID`. Save writes `{id: session.id, date_sort: session.date_sort}` to localStorage.
8. `+N more dates ▾` button — full-width secondary, only when `sessions.length > 1`. Toggles label between `+N more dates ▾` and `Hide dates ▴`.
9. Expanded session list — hidden by default. Renders `sessions[1]` through `sessions[SESSION_VISIBLE_CAP-1]` (max 3 expanded rows so total visible = 4). Each row: date + avail chip + spots, icon-only My List + `Book ↗` button. Same click wiring per session.
10. More-dates hint row — only when `has_more_sessions === true`: dashed border, calendar icon + "More dates available — adjust the date filter above". Informational.
11. Existing report strip / panel — unchanged.

**Velocity widget activation:** stays `display:none` until V2 Phase 5 (velocity signals) lands and `groupCoursesForCards()` starts populating `velocity_fill_pct` + `velocity_days_to_book` per group. Two visual states when active: `.filling` (orange bar, "🔥 Filling fast") for `60 ≤ pct < 80`, `.almost` (red bar, "⚡ Almost gone") for `pct ≥ 80`.

**Notes for future maintainers:**
- Grouping is server-side (algolia_sync.py). Each Algolia hit arrives with `sessions[]` already populated by `mapHit()`. `groupCoursesForCards()` is passthrough on the search path (idempotent on synthetics) and only runs the bucket-and-collapse logic for `mapSupabaseRow()`-fed inputs (My List / shared-list paths). `_groupKey()` is still the source of truth for `(provider_id, title_hash)` identity.
- `SESSION_VISIBLE_CAP` (currently 4) is the hard cap on displayed sessions per card. Beyond that, the hint row directs the user to narrow the date filter. Tunable per design intent.
- `data-group-key` on the rendered card and `data-save-id` / `data-save-date` on every save button are the contract `toggleSave()` uses to find and re-render the right card after a save state change.

### V2 phases remaining (not yet implemented)
- **Phase 5:** Velocity signal calculation (fill rate, price trend — needs 4+ weeks of log data + Phase 5 prerequisites below)
- **Phase 6:** Validator simplification (remaining admin-tab retirements; activity check already removed)
- **Phase 7:** Drop V1 columns + tables after cutover (includes `activity`, `activity_raw`, `activity_canonical`, `badge`, `badge_canonical` from `courses` and the `activity_mappings`, `pending_mappings`, `activity_labels` tables)

### Phase 5 prerequisites (data-quality cleanup before velocity signals)

Two pieces of historical pollution must be addressed before any velocity computation runs against the intelligence logs. Both follow the `course_price_log.bad_data` pattern from Initiative 4: add a nullable boolean column, backfill the contaminated rows in one pass, then have Phase 5 consumers filter `WHERE NOT bad_data`. Both are deferred work — schedule alongside Phase 5 kickoff.

**Prerequisite A — `course_availability_log.bad_data` for the Checkfront binary-flag bug**

*Window of contamination:* `scraped_at >= '2026-04-16 07:18:43 UTC'` (V2 cutover) `AND scraped_at < '2026-04-29 02:30:00 UTC'` (PR #41 merge)
*Affected providers:* `aaa`, `girth-hitch-guiding` only (Bow Valley scraper doesn't write per-date avail — uses HTML widget scrape, not `/api/3.0/item/cal`).
*Contamination shape:* rows with `spots_remaining=1` and `avail='critical'` that were actually binary-flag products (the API was just emitting "available yes/no", value `1`). The pre-PR-41 scraper had a global-vs-per-item probe bug that flipped any catalog-wide `>1` value into a per-item assumption that `1` meant "1 spot remaining".

*Schema migration:*
```sql
ALTER TABLE course_availability_log
  ADD COLUMN IF NOT EXISTS bad_data boolean NOT NULL DEFAULT false;
```

*Backfill query (run once, after the migration):*
```sql
-- Mark contaminated rows: for each (provider_id, title_hash) in the affected
-- providers + window, if the product NEVER logged spots_remaining > 1 anywhere
-- in its full history, then it's a binary-flag product and every row in the
-- contamination window with spots_remaining=1 / avail='critical' is fake.
UPDATE course_availability_log AS cal
SET bad_data = true
WHERE cal.provider_id IN ('aaa', 'girth-hitch-guiding')
  AND cal.scraped_at >= '2026-04-16 07:18:43+00'
  AND cal.scraped_at <  '2026-04-29 02:30:00+00'
  AND cal.spots_remaining = 1
  AND cal.avail = 'critical'
  AND NOT EXISTS (
    SELECT 1
    FROM course_availability_log cal2
    WHERE cal2.provider_id = cal.provider_id
      AND cal2.title_hash  = cal.title_hash
      AND cal2.spots_remaining IS NOT NULL
      AND cal2.spots_remaining > 1
  );
```

The `NOT EXISTS` clause is the safeguard: if a product **ever** legitimately logged `>1` (i.e. it's a real spot-tracking product), its `spots_remaining=1` rows are real "1 spot left" data and stay untouched. Run **after** at least one post-fix scrape has populated the logs with the new `null/open` values for binary-flag products — otherwise the heuristic can't distinguish.

*Phase 5 consumer contract:* every velocity calculation that joins `course_availability_log` MUST add `AND NOT bad_data` to its WHERE clause, mirroring the existing `course_price_log` filter.

**Prerequisite B — confirm `course_price_log.bad_data` is being honoured**

`course_price_log.bad_data` was added in Initiative 4 with write-time population (`log_price_change` sets `bad_data=true` when `price <= 0`). No backfill was required because the validator's auto-hide on `price<=0` predates the log-population work. Phase 5 must still add `AND NOT bad_data` to every price-trend query — verify this contract is wired before any velocity dashboards ship.

**Inherent limitation (not fixable, must be handled gracefully):**

Binary-flag Checkfront products produce no velocity granularity post-fix — they log `spots_remaining=null, avail='open'` until the date sells out. Phase 5 should detect this case (e.g. "course has zero log rows with `spots_remaining > 1`") and skip velocity computation for those products entirely. The synthetic card object's `velocity_fill_pct` should remain `null`, which the existing card render in `js/cards.js` already handles via `display:none` on the velocity widget.

### Data quality mission (parallel track)
See [data_quality_initiatives.md](data_quality_initiatives.md) for the initiative plan.
- **Initiative 1 — Activity mapping elimination:** fully complete.
- **Initiative 2 — Location mapping refinement:** fully complete.
- **Initiative 3 — Summary Review tab redesign:** fully complete. Tab is an exception inbox (bleed / user flag / generation failed). Validator backfills null summaries inline using a title-only Haiku seed (safety net). `bad_description` user reports no longer auto-clear. New `admin-save-summary` edge function commits admin edits + writes `(provider_id, md5(summary))` to `validator_summary_exceptions`. Bleed check consults the exception table so one admin save clears the whole collision group on the next run.
- **Initiative 5 — Date sanity provider loop:** fully complete. Past-date-active and far-future (>2 yr) both auto-hide on first detection and escalate to the Flags tab's Date escalations section 24 hours after the first `course_availability_log` entry. Escalation surfaces with booking URL + copyable provider email body; admin Clear writes a course-id-scoped suppression (new nullable column on `validator_suppressions`) so zombies from V2-id-drifting date corrections never re-escalate. `future_date` retired from `validator_warnings`. `custom_dates=true` / `date_sort IS NULL` is a hard skip.
- **Initiative 4 — Price sanity provider loop:** fully complete. Zero/negative price auto-hides on first detection with `flag_reason='invalid_price'`; a `course_price_log` row 24+ hours old upgrades the reason to `invalid_price_escalated` and surfaces the course in the Flags tab's Price escalations section with copyable provider email. Admin Clear reuses Initiative 5's course-id-scoped suppression mechanic. Null-price and >5x-median outlier checks are deleted permanently — no replacement, no median comparison anywhere in the codebase. Hardcoded `("Logan","Expedition","Traverse")` skip list and `validator_price_exceptions` reads are gone. New column `course_price_log.bad_data` (set at write time by `log_price_change` when `price <= 0`) protects Phase 5 velocity-signal consumers from zero-priced-row pollution without read-time filter logic. `wrong_price` user-flag auto-clear simplified to `price > 0`.
- **Initiative 6 — Duplicate detection simplification:** fully complete. Admin decision path stripped from duplicate handling: `validator_whitelist` load + lookup deleted, `admin-diagnose-duplicate` edge function deleted (also removed from `deploy-functions.yml`), Diagnose/Whitelist buttons removed from the Flags tab. Auto-hide stays. Duplicate rows render read-only with one `scraper_{provider_id}.py ↗` GitHub link per provider_id; the only manual action is `Clear all` which writes a title-scoped suppression as a last resort. Priority stack collapsed to 2 layers (suppressions → summary exceptions). `validator_whitelist` table orphaned, drops at V2 Phase 7.
- **Initiative 7 — Course count drop → Providers tab:** fully complete. `check_course_count()` deleted from `validate_provider.py`; the Providers tab now fetches the last two `scraper_run_log` rows per provider on load (via `fetchRunLog` + `lastRunByProvider` / `prevRunByProvider` maps) and renders a yellow `⚠ {pct}% drop ↗` badge on the status cell when the drop exceeds 30%. The badge links directly to that provider's GitHub Actions scraper workflow. No server-side write; the signal recomputes on every tab load and clears automatically on the next healthy run. `validator_warnings` types narrowed to `null_avail` + `all_sold`. No new tables, no Supabase SQL.
- **Initiative 8 — Activity Tracking dashboard:** fully complete. New `activity_controls` + `scraper_config` tables (DDL in `activity_controls_schema.sql`). New admin Activity Tracking tab — persistent per-activity catalog with Visible + Tracking-mode toggles, two edit-in-place global window inputs, graceful empty/missing-table states. Two edge functions: `admin-toggle-activity-control` (bulk-capable PATCH, **cascades `visible` to `courses.active`** on flip so the frontend hides / re-shows immediately) and `admin-update-scraper-config` (whitelisted k/v). New GitHub Actions workflow `seed-activity-controls.yml` wraps the seed script with an optional `dry_run` input. **All 23 active scrapers** (every `scraper_*.py` except the legacy monolith `scraper.py` and the enrichment-only `scraper_aaa_details.py`) upsert discovered activities into `activity_controls` and consult `visible` before expensive work; the 5 historical `EXCLUDE_TITLES` lists (altus, vibe, girth_hitch, cloud_nine, bow_valley) were seeded into the new table via `seed_activity_controls.py`, their constants and call sites deleted, and the 4 empty `EXTRA_EXCLUDE_TITLES = []` constants (canmore, banff, mt_norquay, toby_creek) removed. Non-Zaui scrapers use a shared `_is_visible()` helper + module-level `_CONTROLS` dict; Zaui scrapers (5 total: banff_adventures, canmore_adventures, mt_norquay, toby_creek_adventures, vanmtnguides) use the bulk-upsert helper and additionally pick per-activity lookahead from `tracking_mode`: `immediate` (14d default) vs `extended` (180d default). Projected ~90% API-call reduction on Banff Adventures (~96k → ~7.4k `fetch_unavailability` calls) once most activities default to immediate. Structural Zaui filters in `scraper_zaui_utils.is_experience_product` (hotels / transfers / rentals substring / excluded categories) intentionally stay as code — not per-row toggle candidates. The Visible toggle now doubles as a general-purpose "hide this course" control, useful for out-of-scope activities or anything surfacing while a scraper is being fixed.

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

-- For the Pipeline tab Date added column (idempotent, backfills existing
-- prospects with the moment of the migration):
ALTER TABLE provider_pipeline
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
```

For Initiative 8 (Activity Tracking), run `activity_controls_schema.sql` —
creates `activity_controls` + `scraper_config` tables with defaults seeded.
Then `python seed_activity_controls.py` to migrate historical
`EXCLUDE_TITLES` entries (safe to re-run; idempotent UPSERT).

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
