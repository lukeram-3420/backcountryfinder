# Data quality initiative briefs

Living reference for the data-quality cleanup mission. Each initiative below is self-contained — read it cold and you should understand what, why, and the decisions already made.

Order of execution: **Initiative 1 first, Initiative 2 second.** Activity is a pure deletion (low risk, unblocks the audit backlog); location is a behaviour change (medium risk, benefits from cleaner audit ground).

**Status (2026-04-17):** both initiatives shipped. Initiative 1 scraper-side in `5157faa`, admin fast-follow in `c83bd4a`. Initiative 2 in this commit.

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

### Fast-follow commit (completed)
- Deleted the `Activity Mappings` admin tab and its JS from `admin.html`
- Deleted 4 edge functions: `admin-approve-mapping`, `admin-reject-mapping`, `admin-update-mapping`, `admin-delete-mapping`
- Removed them from `deploy-functions.yml`
- Removed the Settings-tab canonical activity values CRUD (activity_labels table now read by nothing)
- Removed the Flags-tab "Add mapping" root-cause fix path plus `ADMIN_ACTIVITY_CONTRADICTIONS`, `summaryStillContradicts`, `patchCoursesActivityForMapping`, and `saveAddMapping`

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

### Scope — what changed (completed)

| Layer | Change |
|---|---|
| `scraper_utils.py` `normalise_location()` | Rewrote the Haiku branch. Prompt now returns `{"city": "...", "province": "XX"}`. Accepted ONLY when `city` is non-empty with no comma AND `province` matches `^[A-Z]{2}$`. On accept → `sb_upsert("location_mappings", ...)` live, update in-memory dict, return canonical. On reject / null / API error / no key → `sb_insert("pending_location_mappings", ...)` with null `suggested_canonical`, return `None` |
| `scraper_utils.py` `_get_popular_canonicals()` | New helper. Queries the top-1000 active courses and ranks `location_canonical` by frequency, returning the top-50 as prompt anchors so Haiku reuses existing canonicals rather than minting spelling variants. Module-level cache — one query per scraper process. Falls back to mapping-alias-count frequency if the courses read fails |
| 9 scraper upsert sites | Added the `if loc_canonical is not None: row["location_canonical"] = loc_canonical` guard around every dict that writes to `sb_upsert("courses", ...)`. Covers altus (2 sites), cwms, summit, iag, hvi, msaa, yamnuska, vanmtnguides (2 sites), hangfire. Removed the `or loc_raw` fallback that had been polluting canonical with raw in yamnuska |
| CLAUDE.md | New "Location mapping policy — Haiku-live-write on structural confidence" section replaced the old admin-write-only rule. New "Never pass `location_canonical: None` to a courses upsert" section documents the re-scrape safety pattern. API table row for `normalise_location` updated to describe the four-tier resolution and caller contract |
| Admin Location Mappings tab | No code change needed — pending queue now receives only real unknowns, approved table grows automatically from Haiku's confident writes |

### Policy shift documented in CLAUDE.md
> Scrapers may write directly to `location_mappings` **only** when Haiku returns a structurally valid `{"city": "...", "province": "XX"}` response where province is a 2-letter uppercase code. All other Haiku responses, null responses, and API failures queue to `pending_location_mappings` for admin review. Scrapers must never write to `activity_mappings` (retired in Initiative 1).

### Decisions made in implementation (post-brief)
- **Confidence definition:** hard structural rule — both `city` and `province` fields present, city contains no comma (defensive), province matches `^[A-Z]{2}$`.
- **Known-canonicals ordering:** top-50 by *course frequency* (not mapping-alias count), computed once per scraper process. Falls back to mapping-alias frequency if the courses query fails or returns nothing.
- **City casing:** trust Haiku's output as-is. No `.title()` munging. Admin fixes rare slips via the existing approved-table edit.
- **No-Haiku-key fallback:** queue to pending + return `None` (deviates from the brief's pure framing but consistent with the unconfident branch — the old `return raw` fallback polluted canonical with un-normalised strings).
- **Keep canonical.** Did not collapse to `location_raw`. The canonical asset is worth the small admin cost.
- **Keep Algolia facet config.** `location_canonical` stays in `searchableAttributes` and `facets` — leaves the door open for adding a region-browse UI later.

### Re-scrape safety — caller-side guard
User flagged mid-implementation: Supabase `merge-duplicates` treats explicit `null` in an upsert payload as "overwrite existing with null". So if `normalise_location()` returns `None` on a re-scrape (transient Haiku hiccup), a previously-resolved canonical would be silently destroyed. Fix: every `normalise_location` caller guards with `if loc_canonical is not None: row["location_canonical"] = loc_canonical` instead of inlining the field unconditionally. 9 scrapers / 10 call sites touched. Documented as a hard rule in CLAUDE.md.

### Out of scope
- Changing the card display of location (stays as `location_canonical || location_raw`)
- Backfilling existing V2 rows with null `location_canonical` — they'll refresh on next scrape
- Adding an area allowlist for wilderness names
- Geocoding via Google Places — rejected in favour of keeping the admin-curated canonical table

### Success criteria
- `pending_location_mappings` receives only genuinely unknown locations going forward ✓ (Haiku-confident writes go live, unconfident go pending)
- `location_mappings` grows automatically from Haiku writes between admin sessions ✓
- No `location_canonical` regressions on existing rows ✓ (`None`-guard preserves existing DB values)
- Admin pending tab only shows entries that need a human decision ✓

---

## Cross-initiative notes

- Both initiatives leave the *two-flag system* (user `flagged` vs validator `auto_flagged`) intact.
- `validator_suppressions` and `validator_whitelist` stay as admin-decision sources for the remaining checks (duplicates, summaries, prices) — only the `activity_mappings` branch is removed from the priority stack.
- Discovery pipeline (`discover_providers.py`, `refresh_discovery_cloud.py`) is unaffected by either initiative.
- V2 Phase 7 (drop columns + tables post-cutover) is the natural final step after both initiatives have been running clean for a cycle or two.
