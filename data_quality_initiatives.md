# Data quality initiative briefs

Living reference for the data-quality cleanup mission. Each initiative below is self-contained — read it cold and you should understand what, why, and the decisions already made.

Order of execution: **Initiative 1 → Initiative 2 → Initiative 3.** Activity is a pure deletion (low risk, unblocks the audit backlog); location is a behaviour change (medium risk, benefits from cleaner audit ground); Summary Review is a workflow redesign (low-to-medium risk, benefits from the validator being quiet).

**Status (2026-04-17):** Initiatives 1 and 2 shipped. Initiative 1 scraper-side in `5157faa`, admin fast-follow in `c83bd4a`. Initiative 2 in `5abb3f1`. Initiative 3 planned below; implementation to follow.

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

- All initiatives leave the *two-flag system* (user `flagged` vs validator `auto_flagged`) intact.
- `validator_suppressions` and `validator_whitelist` stay as admin-decision sources for the remaining checks (duplicates, prices) — only the `activity_mappings` branch is removed from the priority stack. Initiative 3 adds `validator_summary_exceptions` as a new sibling.
- Discovery pipeline (`discover_providers.py`, `refresh_discovery_cloud.py`) is unaffected by any initiative.
- V2 Phase 7 (drop columns + tables post-cutover) is the natural final step after all initiatives have been running clean for a cycle or two.

---

## Initiative 3 — Summary Review tab redesign (exception inbox, not approval queue)

### Goal
Reduce the Summary Review tab from a high-volume approval queue to a low-volume exception inbox. Summaries go live automatically by default. Admin only sees genuine exceptions.

### Why
- Haiku output quality is high enough that reviewing every summary is busywork.
- The approval gate is already half-fiction — scrapers write `courses.summary` directly at scrape time, so cards show summaries before admin approves anything.
- Summary-related admin work is currently split across two tabs (Summary Review for content, Flags Warnings for empty/regenerate) — one queue, one workflow is cleaner.
- `summary_empty` warnings in the Flags tab are redundant once the validator auto-fills them inline.

### Three routes into the Summary Review tab

| Trigger | Course visibility | How it gets there |
|---|---|---|
| Duplicate summary bleed | Auto-hidden (`auto_flagged=true`, `flag_reason='summary_bleed'`) | Validator Check 1 detects identical summary text on two different course titles. Second occurrence is flagged; first stays visible and untouched. |
| Missing/null summary — generation failed | Visible, summary section empty on card | Validator attempts `generate_summaries_batch()` inline using `title` as the description seed. Only lands in the tab if generation fails or returns empty. |
| User-flagged `bad_description` | Auto-hidden (`flagged=true`) | User report via `notify-report` edge function. Auto-clear path retired — every `bad_description` user report routes to the admin explicitly. |

### Auto-fill behaviour (validator inline regeneration)
When the validator detects `summary IS NULL` and the course is not in `validator_summary_exceptions`:
1. Build a `generate_summaries_batch`-compatible input using `{id, title, description: title, provider}`. Description seed is title-only because scrapers strip the real description before upsert — this is a **safety net, not a quality floor**. A mediocre summary beats no summary on the card.
2. Call `generate_summaries_batch` directly from `validate_provider.py` (import from `scraper_utils`).
3. On success: write `{summary, search_document}` directly to `courses`. Do not queue to `validator_warnings`. Do not touch `course_summaries.approved`.
4. On failure (Haiku returns empty, API error, no key): leave the course as-is. The course is then surfaced in the Summary Review tab via its null summary state.

This replaces the current `summary_empty` → `validator_warnings` → Flags-tab "Regenerate" flow entirely.

### Admin workflow in Summary Review tab
For all three routes, the admin sees the same row:
- Course title + provider
- Current summary text (editable textarea)
- Search document (read-only)
- **Regenerate** button — calls Haiku fresh via `admin-regenerate-summary`, populates the textarea
- **Save** button — commits the text

### Save behaviour
When the admin clicks Save:
1. Write text to `courses.summary` + `courses.search_document`.
2. Clear `auto_flagged` if set (`auto_flagged=false, flag_reason=null`).
3. Clear `flagged` if set (`flagged=false, flagged_reason=null, flagged_note=null`).
4. Insert a row into `validator_summary_exceptions` keyed on `(provider_id, summary_hash)` — see table schema below. `summary_hash = md5(summary_text)`.

On next validate run, Check 1's bleed detection first looks up `(provider_id, summary_hash)` against `validator_summary_exceptions` and skips the check for any matching course. **One admin save clears the whole bleed group**, regardless of which side gets flagged next run — because the exception is on the TEXT, not the course.

### `validator_summary_exceptions` table (new)

```sql
create table validator_summary_exceptions (
  id           bigserial primary key,
  provider_id  text not null,
  summary_hash text not null,  -- md5 of the saved summary text
  course_id    text,           -- course that triggered the save (audit only)
  reason       text not null,  -- 'summary_bleed' | 'bad_description' | 'generation_failed'
  saved_at     timestamptz default now(),
  unique (provider_id, summary_hash)
);
grant select, insert on validator_summary_exceptions to anon, authenticated, service_role;
```

Keyed on `(provider_id, summary_hash)`, not `course_id`. The bleed check cares about the text, not the course — one admin review of a colliding summary resolves the whole group.

Scope — **bleed check only**. The empty-summary check does not consult this table. Admin-saved summaries are non-empty by definition, so the empty check won't fire; if a course somehow loses its summary later (rare re-scrape edge case), the validator's auto-fill should run — that's the safety net.

### Validator priority stack update
Add `validator_summary_exceptions` as a new layer. Final order:

1. `validator_suppressions` — explicit admin "ignore this" decision. Highest priority.
2. **`validator_summary_exceptions`** *(new)* — admin-resolved summary text. Skip Check 1 bleed detection for any course whose `(provider_id, md5(summary))` is in the table.
3. `validator_price_exceptions` — skip the price outlier check.
4. `validator_whitelist` — skip the duplicate check.

### Default behaviour change — no more approval gate
- Scraper-generated summaries go live immediately (they already do — this just makes it explicit).
- `course_summaries.approved` flag becomes irrelevant for new rows — deprecated until Phase 7.
- `admin-approve-summary` edge function still exists for backward compatibility but is no longer called by the normal flow.
- Summary Review tab's existing `approved=false` backlog can be bulk-approved or ignored — the rows are already live on cards. No migration needed.

### Scope — what changes

| Layer | Change |
|---|---|
| Supabase SQL (manual, before deploy) | Create `validator_summary_exceptions` table per schema above |
| `validate_provider.py` | Check 1 reads `validator_summary_exceptions` at run start. Empty-summary branch: inline-call `generate_summaries_batch` with `{id, title, description: title, provider}` seed, write result to `courses`; only fall through to the email-only warning if generation fails. Bleed branch: compute `md5(summary)` per course, skip pairs where `(provider_id, summary_hash)` is in the exceptions cache. Auto-flag the second occurrence with `flag_reason='summary_bleed'`. Drop `summary_empty` from `validator_warnings` entirely. Drop the `bad_description` auto-clear branch from `auto_clear_user_flags` — user flags stay open until admin save |
| `supabase/functions/` | Optional new `admin-save-summary` edge function that does the full save flow (courses write + flag clears + exceptions insert) atomically. Alternatively, the admin UI can do three sequential writes via existing edge functions — simpler to ship, more network round-trips. Ship the simpler version first; consolidate later if latency bites |
| `admin.html` Summary Review tab | Rebuilt query: show every course where `auto_flagged=true AND flag_reason='summary_bleed'`, OR `flagged=true AND flagged_reason='bad_description'`, OR `summary IS NULL AND active=true` (the generation-failed bucket). Remove the `approved=false` filter. Keep the existing edit textarea + Regenerate + Save buttons — only the row-source query changes, and the Save action writes to `validator_summary_exceptions` in addition to the existing side effects |
| `admin.html` Flags tab | Drop the `summary_empty` Warnings sub-branch (the Regenerate action there). Keep `summary_bleed` as the `summary mismatch` group action — it moves to Summary Review via auto-flag, not via the Flags tab |
| CLAUDE.md | Validator priority stack updated. Validator 6-check table updated: Check 1 (Summary quality) now reads as "Bleed auto-hide via md5 comparison; empty is auto-filled inline with title-only seed". Admin panel tabs section: Summary Review tab description rewritten as exception inbox. `validator_summary_exceptions` table added to the admin-facing tables reference list |

### Out of scope
- Dropping `course_summaries.approved` — Phase 7.
- Dropping `admin-approve-summary` edge function — Phase 7.
- Changing the Regenerate/edit UX on existing Summary Review rows — same UI, just fewer rows.
- Migrating existing `approved=false` backlog in `course_summaries` — they're already live on cards, leave until Phase 7.

### Decisions made in planning (post-brief draft)
- **Bleed exception keying:** per-summary-hash, not per-course_id. One admin save clears the whole collision group. Keying on the text matches what the bleed check actually evaluates.
- **Auto-fill quality framing:** safety net, not quality floor. Title-only seed is acceptable because a mediocre summary beats no summary on the card. Admins don't need to be told "this summary was auto-filled from title" — quality signal is that Check 1 won't flag it, and cards render as normal.
- **`bad_description` auto-clear:** dropped. Every user report reaches the admin for explicit acknowledgement via the Summary Review tab. Silent auto-clear loses the reason for the complaint.

### Dependencies
- Initiative 1 complete ✓
- Initiative 2 complete ✓
- `validator_summary_exceptions` table created in Supabase before the validator changes deploy (user runs SQL manually).

### Success criteria
- Summary Review tab shows only bleed occurrences, generation failures, and user-flagged courses.
- Flags tab Warnings has no `summary_empty` entries.
- `validator_warnings` table stops accumulating `summary_empty` rows.
- First scraper run after a new provider is onboarded: summaries appear on cards without admin touch.
- Admin-saved summaries never re-appear in the queue on subsequent validate runs (exception row matches on the text hash).
