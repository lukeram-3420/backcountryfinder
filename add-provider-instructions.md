# Adding a New Provider

End-to-end checklist for onboarding a new outdoor adventure provider into BackcountryFinder. Always read this file plus `CLAUDE.md` before starting — the "Checkfront — onboarding pattern", "Variable pricing — Zaui tier policy", "Hybrid platform pattern", and "Date extraction must be scoped" subsections are required reading depending on the provider's platform.

## 1. Pipeline

The provider should already exist in `provider_pipeline` via the admin Pipeline tab "Add provider" form. If not, add it there first — Claude Haiku auto-fills name, location, platform, complexity, priority, and Google Places rating.

## 2. Build the scraper

### Reference scraper by platform

Pick the reference that matches the new tenant's platform — copy and adapt, do not start from scratch:

| Platform | Reference scraper | Notes |
|---|---|---|
| Checkfront | `scraper_girth_hitch_guiding.py` | Modern pattern: shared `scraper_checkfront_utils` + sampled rated-price + flex-row emission. **Do NOT copy `scraper_aaa.py`** — it's on the deferred-migration WordPress-hybrid path tracked in [issue #110](https://github.com/lukeram-3420/backcountryfinder/issues/110). |
| Rezdy | `scraper_msaa.py` | HTML storefront scrape (no auth). Public API doesn't expose seat counts — see CLAUDE.md "Velocity signal granularity ceiling". |
| Zaui | `scraper_banff_adventures.py` + `scraper_zaui_utils.py` | Grouped scraper pattern (4 interleaved groups per run). Tier extraction in utils. |
| FareHarbor | `scraper_vibe_backcountry.py` | Playwright XHR interception for the price-preview endpoint. |
| WordPress (custom) | `scraper_yamnuska.py` (Playwright) or `scraper_srg.py` (BeautifulSoup) | Pick by JS-rendering. |
| Squarespace | `scraper_jht.py` | BeautifulSoup over the public site. |
| WooCommerce | `scraper_cwms.py` | Standard WordPress + WooCommerce REST surface. |
| The Events Calendar | `scraper_summit.py` | WordPress plugin. |
| Custom Rails | `scraper_iag.py` / `scraper_hvi.py` | Provider-specific. |

### Required imports (every scraper)

```python
from scraper_utils import (
    sb_upsert, stable_id_v2, title_hash,
    normalise_location, load_location_mappings,
    generate_summaries_batch,
    log_availability_change, log_price_change,
    update_provider_ratings, update_provider_shared_utils,
    spots_to_avail, append_utm,
    parse_date_sort, is_future,
    activity_key, upsert_activity_control, load_activity_controls,
    UTM,
)
```

When the scraper imports from a platform-specific utils module (e.g. `scraper_checkfront_utils`, `scraper_zaui_utils`), the PROVIDER dict must declare it:

```python
PROVIDER = {
    "id":       "tenant-slug",
    "name":     "Tenant Name",
    "website":  "https://example.com",
    "location": "City, AB",
    "shared_utils_module": "scraper_checkfront_utils",  # or scraper_zaui_utils, etc.
}
```

Then `main()` calls `update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))` once per run, right after `update_provider_ratings`. Drives the admin Providers-tab Type column ("Shared utils" green badge vs "Unique" amber badge). Bespoke scrapers omit the field — the helper writes a NULL and the row classifies as "Unique" on the next scrape.

Platform-specific utility imports go in addition to the above. For Checkfront:

```python
from scraper_checkfront_utils import (
    fetch_catalog, fetch_calendar, fetch_rated_price_sampled,
    parse_rated_price,
)
from scraper_utils import detect_checkfront_spot_counts
```

For Zaui see `scraper_zaui_utils` imports in any of the 5 Zaui scrapers.

### Course upsert dict — minimum shape

```python
{
    "id":               stable_id_v2(PROVIDER_ID, date_sort, title),  # date_sort=None for flex rows
    "provider_id":      PROVIDER_ID,
    "title":            title,
    "location_raw":     loc_raw,
    "date_sort":        date_sort,            # None for flex rows
    "date_display":     "Mar 15, 2026",       # or "Inquire for dates" for flex
    "duration_days":    duration_days,
    "price":            price,                # int CAD; None acceptable but log_price_change skips Nones
    "currency":         "CAD",
    "spots_remaining":  spots_remaining,      # int or None (binary-flag)
    "avail":            spots_to_avail(spots_remaining),
    "active":           avail != "sold",
    "image_url":        image_url,
    "booking_url":      append_utm(booking_url),
    "booking_mode":     "instant",            # "request" for flex / inquiry-only rows
    "custom_dates":     False,                # True for flex rows
    "summary":          "",                   # populated by generate_summaries_batch
    "search_document":  "",                   # populated by generate_summaries_batch
    "scraped_at":       scraped_at,
}
# Add location_canonical only when normalise_location returned a value:
if loc_canonical is not None:
    row["location_canonical"] = loc_canonical
```

### Hard rules

- **`location_canonical` omit-when-None.** When `normalise_location()` returns None, OMIT the key entirely from the upsert dict. Never set `location_canonical=None` — Supabase's `Prefer: resolution=merge-duplicates` would treat that as "overwrite with NULL" and destroy a previously-resolved canonical on a transient Haiku failure. CLAUDE.md "Never pass `location_canonical: None` to a courses upsert" has the full rationale.

- **Never write these columns from a scraper.** They're owned by other systems:
  - `flagged`, `flagged_reason`, `flagged_note` — user reports via `notify-report` edge function
  - `auto_flagged`, `flag_reason` — validator only (`validate_provider.py`)
  - `activity`, `activity_raw`, `badge`, `badge_canonical`, `activity_canonical` — retired in V2 Phase 4 (Initiative 1)

- **Per-title visibility uses `activity_controls` table, not `EXCLUDE_TITLES`.** The historical `EXCLUDE_TITLES` constant pattern was retired in Initiative 8. Every scraper now upserts `(provider_id, activity_key)` for each activity it sees and consults `visible` before any expensive work. Reference: `_is_visible()` in `scraper_girth_hitch_guiding.py`. Admin flips `visible=false` via the Activity Tracking tab. Module-level category exclusions still apply for platforms that expose categories (e.g. Checkfront's `EXCLUDE_CATEGORIES = {"merchandise", "equipment", "samples"}`).

- **Date regex must be scoped.** When parsing dates from HTML, only match inside elements whose class/id matches `(schedule|dates|upcoming|session|availability|calendar)` (case-insensitive). Never run regex against `soup.get_text()` — fabricated dates from footers, testimonials, copyright notices have caused real bugs. Reference: `extract_schedule_text()` in `scraper_altus.py`. Full background: CLAUDE.md "Date extraction must be scoped".

- **Playwright import discipline.** Only import Playwright at the top of scrapers that actually use it (currently `scraper_yamnuska.py`, `scraper_vibe_backcountry.py`). Never add it to `scraper_utils.py` — would force every scraper to install Chromium.

### After upsert — order matters

```python
sb_upsert("courses", rows)
generate_summaries_batch(courses_for_summary, provider_id=PROVIDER_ID)  # PATCHes summary + search_document
for c in rows:
    log_availability_change(c)
    log_price_change(c)
```

`generate_summaries_batch` reads `description` from the input dicts but doesn't include it in the courses upsert (it's not a courses column). Strip `description` from your row dicts before the upsert if you stored it inline.

## 3. Platform-specific orchestration

### Checkfront (the modern pattern)

Reference: `scraper_girth_hitch_guiding.py`. Full pattern documented in CLAUDE.md "Checkfront — onboarding pattern (no credentials required)". The `main()` orchestration:

```
1. fetch_catalog(CF_BASE)
2. Filter via _is_visible(activity_controls) + EXCLUDE_CATEGORIES
3. fetch_calendar(CF_BASE, item_ids minus BROKEN_ITEM_IDS)
4. For each cal-eligible item: fetch_rated_price_sampled (4 dates, fast-fail)
5. For each dateless item: flex-row probe with 3-strikes circuit breaker
6. Build dated rows + flex rows (merge before summary generation)
7. summaries → upsert → logs
```

**Required tenant config:**

- `CF_BASE = "https://{tenant-slug}.checkfront.com/api/3.0"`
- `BROKEN_ITEM_IDS: set` — start empty; populate after observing chronic 500s across 2-3 runs
- `EXCLUDE_CATEGORIES: set` — exclude "merchandise", "equipment", "samples" minimum; tune per tenant
- `LOCATION_MAP: list[(keyword, canonical)]` — title-keyword → canonical location
- `PROVIDER` dict must include `"shared_utils_module": "scraper_checkfront_utils"` — drives the admin Providers-tab Type column ("Shared utils" badge). The `update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))` call goes right after `update_provider_ratings` in `main()`. Reference: PR #112 + the maintainer note at the top of `scraper_checkfront_utils.py`.

**Use `fetch_rated_price_sampled`, not `fetch_rated_item`.** Full-window 180-day rated requests time out at 15s on slow tenants (Girth Hitch was 0/8 success on full-window, 8/8 on sampled). Sampled is the production path. Override only if you've verified the tenant returns full-window rated reliably.

**Pre-emptive `BROKEN_ITEM_IDS` skip.** Saves ~14s per skipped item on chronic 500-ers. Update the set after observing the same item IDs fail across 2-3 runs.

**3-strikes circuit breaker on flex-row probing.** When iterating over many dateless items to emit flex rows, abort the loop if 3 consecutive items fail their rated sample. Bounds worst-case wall-time on storm days.

### Zaui

Reference: `scraper_banff_adventures.py` + `scraper_zaui_utils.py`. **Grouped scraper** — split activities into 4 interleaved groups, run via `--group N`. Tier extraction (adults / seniors / etc.) in `extract_zaui_price`. Public API exposes no capacity — `spots_remaining=None` always; `avail='open'` until further data sources arrive (ZAPI / OCTO credentials, see CLAUDE.md "Velocity signal granularity ceiling").

### Two-pass (hybrid platform + own website)

Reference: `scraper_altus.py` (Rezdy + WordPress) or `scraper_cloud_nine_guides.py` (Rezdy + Squarespace). Use when the booking platform's storefront only lists transactional products and the marketing site has additional inquiry-based programs. Pass-2 dedup contract: skip rows whose title overlaps Pass-1.

### FareHarbor

Reference: `scraper_vibe_backcountry.py`. Playwright + XHR interception. Catalog is plain HTTP; pricing requires the price-preview embed endpoint. See CLAUDE.md "FareHarbor-specific notes" for the full reverse-engineered API surface.

## 4. GitHub Actions workflow

`.github/workflows/scraper-{id}.yml` with `workflow_dispatch` only:

- 5 standard secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `RESEND_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`
- Final step: `python validate_provider.py {id}` with `continue-on-error: true`
- Add Playwright cache + install steps only if Step 1 analysis says JS-rendered

Append to `.github/workflows/scraper-all.yml` (read it first, append-only):

```yaml
- name: {Provider Name}
  run: python scraper_{id}.py
  continue-on-error: true
- name: Validate {Provider Name}
  run: python validate_provider.py {id}
  continue-on-error: true
```

## 5. Supabase SQL

Run in the Supabase SQL editor. All `ON CONFLICT DO NOTHING` for safe re-runs:

```sql
-- Provider starts inactive for staging. Flip active=true via admin
-- Providers tab once data is clean.
INSERT INTO providers (id, name, website, location, booking_platform, active)
VALUES ('{id}', '{name}', '{website}', '{location}', '{platform}', false)
ON CONFLICT DO NOTHING;

-- Known location aliases (raw → canonical). Add any local
-- location keywords from the title parsing.
INSERT INTO location_mappings (location_raw, location_canonical) VALUES
  ('Squamish',    'Squamish, BC'),
  ('Sea to Sky',  'Squamish, BC')
ON CONFLICT DO NOTHING;
```

Do NOT run `INSERT INTO activity_mappings` — that table is retired (Initiative 1). Per-title visibility lives in `activity_controls`, populated by the scraper itself on first run; admin flips toggles via the Activity Tracking tab afterward.

## 6. First run

1. Trigger `scraper-{id}.yml` from the Actions tab (or admin Providers → Run).
2. Run `python validate_provider.py {id}` (auto-runs if scraper-{id}.yml has the validate step).
3. Inspect:
   - admin **Flags** tab Warnings + Auto-flags (price escalations, summary bleed, etc.)
   - admin **Summary Review** tab (any auto-hidden bleed groups, generation failures)
   - admin **Activity Tracking** tab (every activity the scraper saw, default `visible=true` — flip noise to `visible=false` for rentals / merch / lodging)
   - admin **Location Mappings** tab (any pending suggestions awaiting approval)
4. Fix mappings, refine `EXCLUDE_CATEGORIES` / activity_controls visibility, regenerate summaries via the Summary Review tab, add `BROKEN_ITEM_IDS` if Checkfront-specific.
5. When clean, flip `providers.active = true` via the admin Providers tab toggle. Pipeline auto-syncs to `live`. Algolia syncs on next `scraper-all.yml` cron tick (within 6h) or on-demand via `sync-algolia.yml`.
