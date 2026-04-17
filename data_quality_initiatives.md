# Data quality initiative briefs

Living reference for the data-quality cleanup mission. Each initiative below is self-contained — read it cold and you should understand what, why, and the decisions already made.

Order of execution: **Initiative 1 first, Initiative 2 second.** Activity is a pure deletion (low risk, unblocks the audit backlog); location is a behaviour change (medium risk, benefits from cleaner audit ground).

---

## Initiative 1 — Activity mapping elimination

### Goal
Remove the concept of a canonical course `activity` from the system entirely. Scrapers stop classifying, the admin tab disappears, the Algolia facet goes, the columns drop at cutover.

### Why
- Activity is inferred, not authoritative. `resolve_activity()` is a three-tier guess chain (admin mapping table → Claude Haiku → keyword fallback `"guided"`), with every Haiku call polluting `pending_mappings`.
- The V2 frontend already dropped the Activity dropdown. Nothing user-facing filters on it anymore.
- `search_document` already carries activity keywords; free-text Algolia search covers the use case without a canonical field.
- `crawl_courses.py` audit: 311 `activity_mismatch` flags + 845 existing `auto_flags` — most are false positives from keyword heuristics against admin-approved mappings. Killing activity kills this entire class of noise.
- Admin burden: one tab, 4 edge functions, two tables (`activity_mappings` + `pending_mappings`) all stop earning their keep.

### Scope — what changes in a single commit

| Layer | Change |
|---|---|
| 14 scrapers | Remove calls to `resolve_activity()` and `build_badge()`. Stop writing `activity`, `activity_raw`, `badge`, `badge_canonical` to the `courses` upsert payload |
| `scraper_utils.py` | Delete `resolve_activity`, `detect_activity`, `load_activity_mappings`, `load_activity_labels`, `build_badge`. The `ACTIVITY_KEYWORDS` dict goes with them |
| `generate_summaries_batch` | Drop `activity` from per-course input dict and from the Haiku prompt context. Quality risk flagged but acceptable — title + description are richer signals |
| `algolia_sync.py` | Remove `activity` from the record mapping. Remove from `searchableAttributes` and `facets`. Keep the activity synonym group (skiing/ski touring/splitboarding etc.) — it still helps free-text search on `search_document` |
| `js/cards.js` | Drop the `badge` render block. Collapse `IMG[activity]` fallback chain to a single `FALLBACK_IMG` constant. Remove `activity_canonical` / `activity_raw` / `activity` references from the display path |
| `index.html` | Replace the per-activity `IMG` dict (line 729) with a single `FALLBACK_IMG` neutral hero URL |
| `js/ui.js` | Remove `course_type` from `click_events` payload (the `activity_canonical || activity_raw` fallback at line 13) |
| `validate_provider.py` | Delete Check 2 (activity mapping). Drop the `activity_mappings` precedence branch from the validator priority stack |
| `crawl_courses.py` | Remove the `activity_mismatch` category |
| CLAUDE.md | Update: filter bar description, the 6-query list, Phase 4 frontend notes, scraper conventions (activity canonical values list), schema table entries for `activity`/`activity_raw`/`badge`/`badge_canonical` (mark as deprecated, will drop at cutover), validator priority stack docs |

### Fast-follow commit (after scraper-side lands clean)
- Delete the `Activity Mappings` admin tab from `admin.html`
- Delete 4 edge functions: `admin-approve-mapping`, `admin-reject-mapping`, `admin-update-mapping`, `admin-delete-mapping`
- Remove them from `deploy-functions.yml`

### Out of scope
- Dropping the `activity`, `activity_raw`, `badge`, `badge_canonical` columns from Supabase — that's V2 Phase 7 cutover housekeeping, not part of this work
- Deleting the `activity_mappings` + `pending_mappings` tables — also Phase 7
- The `Altus EXCLUDE_TITLES` pattern stays — it's title-based product exclusion, unrelated mechanism

### Decisions already made
- **IMG fallback:** option (b) — require `image_url` from scraper, single neutral `FALLBACK_IMG` when missing
- **Commit shape:** one commit for all 14 scrapers + shared helpers + Algolia sync + frontend + CLAUDE.md. Admin-tab removal is a separate fast-follow commit
- **`click_events.course_type`:** becomes null. Acceptable — Algolia Insights is the funnel signal now

### Success criteria
- Next scrape run produces 0 rows with non-null `activity` on V2
- Next Algolia sync produces records with no `activity` field
- `crawl_courses.py` audit report shows `activity_mismatch` section gone and `auto_flag` count substantially lower
- Admin Flags tab Warnings stops surfacing activity-related entries
- No `pending_mappings` row writes from scrapers

---

## Initiative 2 — Location mapping refinement

### Goal
Keep location mapping (it's load-bearing for Algolia search + card display + filtering UX quality), but stop queuing obvious cases to admin review. Only genuinely hard cases hit the pending queue.

### Why
- The approved `location_mappings` table is already clean and reusable (Banff → Banff/Lake Louise, Blackcomb → Whistler, BC, etc.). The mapping asset is valuable and should stay.
- Today every unresolved location goes through Haiku AND every Haiku result lands in `pending_location_mappings` regardless of how confident the classification is. That's the noise source.
- Structural validation (`"City, Province"` with 2-letter province code) is a deterministic confidence proxy that works for Canada and generalises to US/other.
- Net effect: admin queue shrinks to real unknowns. Approved table grows automatically from Haiku's confident calls.

### Scope — what changes

| Layer | Change |
|---|---|
| `scraper_utils.py` `normalise_location()` | Rewrite the Haiku branch. Prompt Haiku for structured JSON: `{"city": "...", "province": "XX"}`. If both fields are present AND province matches `^[A-Z]{2}$` → compose `"City, XX"`, write directly to `location_mappings` (new: scraper writes to canonical table), set on the course. Otherwise → leave `location_canonical = NULL` on the course, write to `pending_location_mappings` for admin |
| CLAUDE.md | Update the *Mapping tables are admin-write-only* rule to split: activity = admin-write-only (moot after Initiative 1); location = Haiku-live-write on structural confidence, pending fallback for unknowns. Document the structural confidence rule explicitly |
| Admin Location Mappings tab | No code change needed. The pending queue will simply get quieter; approved table will grow faster |

### Policy shift to document in CLAUDE.md
> Scrapers may write directly to `location_mappings` **only** when Haiku returns a structurally valid `{"city": "...", "province": "XX"}` response where province is a 2-letter uppercase code. All other Haiku responses, null responses, and API failures continue to queue to `pending_location_mappings` for admin review. Scrapers must never write to `activity_mappings` (which is being removed entirely in Initiative 1).

### Decisions already made
- **Confidence definition:** hard structural rule — both `city` and `province` fields present, province matches `^[A-Z]{2}$`. No model-self-reported confidence, no scoring. Scales cleanly to US (CA/NY/…), UK (no 2-letter regions), etc. — if it doesn't match the format, it goes to pending, which is the safe behaviour.
- **Keep canonical.** Don't collapse to `location_raw`. The canonical asset is worth the small admin cost.
- **Keep Algolia facet config** (`location_canonical` stays in `searchableAttributes`, `facets`) even though the live frontend only uses it for search, not faceting — leaves the door open for adding a region-browse UI later.

### Dependencies
- Initiative 1 must land first. Cleaner audit backlog = better ground for validating the new Haiku write policy on live data.

### Out of scope
- Changing the card display of location (stays as `location_canonical || location_raw`)
- Backfilling existing V2 rows with null `location_canonical` — they'll refresh on next scrape
- Adding an area allowlist for wilderness names that Places-style geocoding might miss (we're not using Places here, we're using Haiku — the admin pending queue handles the edge cases)
- Geocoding via Google Places — was considered (Option B from the discussion) but rejected in favour of keeping the admin-curated canonical table

### Success criteria
- `pending_location_mappings` row-count per scraper run drops substantially (only genuinely unknown locations hit it)
- `location_mappings` row-count grows automatically between admin sessions
- No `location_canonical` regressions on existing rows (mappings table is never shrunk, only appended)
- Admin can visit the pending tab and only see locations that genuinely need a human decision

---

## Cross-initiative notes

- Both initiatives leave the *two-flag system* (user `flagged` vs validator `auto_flagged`) intact.
- `validator_suppressions` and `validator_whitelist` stay as admin-decision sources for the remaining checks (duplicates, summaries, prices) — only the `activity_mappings` branch is removed from the priority stack.
- Discovery pipeline (`discover_providers.py`, `refresh_discovery_cloud.py`) is unaffected by either initiative.
- V2 Phase 7 (drop columns + tables post-cutover) is the natural final step after both initiatives have been running clean for a cycle or two.
