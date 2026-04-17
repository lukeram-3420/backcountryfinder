# Data quality initiative briefs

Living reference for the data-quality cleanup mission. Each initiative below is self-contained — read it cold and you should understand what, why, and the decisions already made.

Order of execution: **Initiative 1 → 2 → 3 → 5 → 4.** Activity is a pure deletion (low risk, unblocks the audit backlog); location is a behaviour change (medium risk, benefits from cleaner audit ground); Summary Review is a workflow redesign (low-to-medium risk, benefits from the validator being quiet); Date sanity is an active provider-outreach loop (medium risk, benefits from every other queue being tame); Price sanity ports that same active-loop shape to the remaining passive warnings, re-using the course-id-scoped suppression mechanic.

**Status (2026-04-17):** Initiatives 1, 2, 3, and 5 shipped. Initiative 4 planned (this document). Initiative 1 scraper-side in `5157faa`, admin fast-follow in `c83bd4a`. Initiative 2 in `5abb3f1`. Initiative 3 plan in `cd13bcc`, implementation in `615c5e9`. Initiative 5 plan in `8885144`, implementation in `300eae3`.

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

---

## Initiative 5 — Date sanity + provider improvement loop

### Goal
Replace passive date warnings with an active provider improvement loop. Bad dates auto-hide immediately and escalate to a provider touchpoint after 24 hours. Symmetric policy for past-date-active and far-future dates.

### Why
- Past-dated active courses and far-future (>2yr) dates are either scraper bugs or stale provider listings — both warrant provider outreach, not just admin awareness.
- Email-only warnings for >2yr future were too passive — a course dated 2028 showing on the frontend today is worse than a hidden course.
- Time-based escalation (24 hours) is more robust than scrape-count-based — scrape frequency may change, wall-clock time doesn't.
- `course_availability_log` already provides the 24-hour confirmation signal. No new detection table required.

### Two conditions covered

| Condition | Trigger | On first detection |
|---|---|---|
| A — Past date, active | `date_sort < today AND active=true` | `auto_flagged=true`, `flag_reason='past_date'`. Hidden from frontend. Not yet in Flags tab |
| B — Far future (>2yr) | `date_sort > today + interval '2 years'` | `auto_flagged=true`, `flag_reason='future_date'`. Hidden from frontend. Not yet in Flags tab |

Both skip courses with `custom_dates=true` OR `date_sort IS NULL` — hard skip, not soft.

### 24-hour escalation (both conditions)
On every validate run, for each auto-flagged course with `flag_reason IN ('past_date', 'future_date')`:
1. Look up the course in the batch result of:
   ```sql
   SELECT DISTINCT course_id
   FROM course_availability_log
   WHERE provider_id = '{pid}'
     AND scraped_at <= now() - interval '24 hours'
   ```
2. If the course_id is in that set → upgrade `flag_reason` to `past_date_escalated` or `future_date_escalated`.
3. Admin UI filters on `flag_reason LIKE '%_escalated'` to render the Flags tab date-escalation section.

One query per provider per run, intersected in-process. Cheap.

### Why this signal works
`course_availability_log` writes a row on the first-ever scrape of a course (no previous row to compare against), then only writes again when `avail` or `spots_remaining` changes. Since V2 course_id encodes `date_sort` (`{provider}-{date_sort}-{title_hash}`), any log row for a given course_id from 24+ hours ago is evidence that the bad-date state was present then — the bad date is baked into the id.

### Flag_reason progression (explicit two-state)
- `past_date` / `future_date` → auto-hidden, NOT in Flags tab
- `past_date_escalated` / `future_date_escalated` → auto-hidden AND in Flags tab with provider-email copy

Admin UI filter is trivial (`LIKE '%_escalated'`). Validator re-evaluates on every run — if 24-hour log evidence exists, the suffix is set.

### Admin workflow — Flags tab date-escalation section
For each escalated group:
- Course title, provider_id, booking_url (direct link to provider's listing)
- Copyable pre-written email body ("One of your listings appears to have an incorrect date — here's the direct link to update it: …")
- **Clear** button

Admin copies the body, emails the provider out-of-band (no `providers.contact_email` column — admin finds contact info from the provider site), then clicks Clear to acknowledge the outreach.

### Clear behaviour — course-id-scoped suppression (critical)
Clear does NOT simply flip `auto_flagged=false`. The validator's `reset_flags(provider_id)` wipes auto_flagged at the start of every run; without a suppression row, the same course would re-flag and re-escalate on the next 6-hour run forever. Every admin Clear click would be re-undone.

Clear writes a row to `validator_suppressions` keyed on `course_id + flag_reason`. Validator's priority stack consults suppressions BEFORE the date check, short-circuits for that course_id, and the zombie stays inert permanently.

### Schema change
Single `ALTER` on the existing `validator_suppressions` table:
```sql
ALTER TABLE validator_suppressions ADD COLUMN course_id text;
-- no default, nullable. Existing title-based suppressions get course_id=NULL,
-- preserving current match behaviour for duplicate / activity / summary flows.
```

No new table.

### Match rule in `is_suppressed()`
- If suppression's `course_id` is set → match requires `course_id + flag_reason category` match (exact course_id, same `flag_reason` prefix before `':'`)
- Otherwise → existing `title_contains + flag_reason` match, unchanged

One new branch in the match function. Title-based suppressions stay broad (duplicates, mappings) — only date escalations use course-id precision.

### Zombie mechanics (known, accepted)
V2 course_id includes `date_sort`. When a provider fixes a bad date, the scraper emits a NEW course_id; the OLD course_id persists in the DB with its stale date_sort (no scraper currently deactivates missing IDs in general — orthogonal scraper-hygiene problem).

Under Initiative 5, every validator run sees the old course_id, sees `date_sort < today AND active=true`, re-flags and re-escalates. The course-id-scoped Clear suppression handles this cleanly: one admin click per zombie, permanent. Orphan case (provider takes the page down entirely) is handled by the same path — admin clicks Clear once, done.

Auto-clear via `title_hash` correlation was considered and rejected:
- Doesn't cover the orphan case → admin still needs the manual path → course-id suppression is required either way
- Has to distinguish date-correction-zombies from legitimate same-title/different-date sessions
- Saves one click per date-correction event at the cost of a new branch in check_dates

Course-id suppression is the universal tool.

### First-deploy behaviour note
Current far-future courses (>2yr) are EMAIL ONLY warnings. On the first validate run after Initiative 5 lands, every existing far-future course that's been in the DB for 24+ hours will auto-hide AND immediately escalate (the first-scrape log entry predates the cutoff). Expect a one-time spike in the Flags tab date-escalation section. Not a bug — it's the point. Admin processes the backlog once, steady-state resumes.

### Scope — what changes

| Layer | Change |
|---|---|
| Supabase SQL (manual, before deploy) | `ALTER TABLE validator_suppressions ADD COLUMN course_id text;` |
| `validate_provider.py` | Rewrite `check_dates`. Add far-future auto-hide symmetric with past-date. Skip `custom_dates=true` and `date_sort IS NULL`. Batch-query `course_availability_log` once per provider for 24+hr-old course_ids. Upgrade `flag_reason` suffix on confirmed escalations. Extend `is_suppressed()` / `any_check_suppressed()` to accept optional `course_id`; `check_dates` passes it. Remove the email-only `future_date` branch — writes flag, not warning |
| `admin.html` | New Flags tab sub-section "Date escalations" rendering `flag_reason LIKE '%_escalated'`. Per row: booking_url link + copyable email body + Clear button. Clear posts to `validator_suppressions` with `{course_id, provider_id, flag_reason}`. Drop any stale UI that surfaced `future_date` from `validator_warnings` |
| `validator_warnings` | `future_date` is no longer a valid `check_type` — replaced by the Flags tab escalation. `reset_warnings` at run start flushes any stale rows automatically; no migration needed |
| `crawl_courses.py` | Audit categories `past_date_active` and `far_future_date` keep their detection. Reason strings updated to note "auto-hidden immediately; escalation after 24h" |
| CLAUDE.md | Update Check 4 description (two auto-hide conditions + escalation mechanic + custom_dates skip). Update validator priority stack (course_id-scoped suppressions). Update `validator_warnings` check_type list (drop future_date). Update admin Flags-tab description (new Date escalations sub-section). Update `validator_suppressions` row in admin-facing tables list with the new column |

### Out of scope
- Deactivating stale course_ids not seen in recent scrapes — broader scraper-hygiene issue
- Auto-clear via title_hash correlation — considered, rejected
- `providers.contact_email` column — admin finds contact info from provider site
- Automated email sending — admin copies + sends out-of-band
- Migrating existing `validator_warnings` rows with `future_date` — `reset_warnings` handles it

### Decisions made in planning (post-brief draft)
- **Zombie handling:** course-id-scoped suppression on Clear, via nullable column on `validator_suppressions`. Universal tool; auto-clear rejected.
- **flag_reason progression:** explicit two-state (`*_date` → `*_date_escalated`). Admin UI filters on `LIKE '%_escalated'`.
- **Batch log query:** one DISTINCT course_id query per provider per run. Cheap.
- **Provider contact_email:** skipped. Admin finds contact info from the provider site.
- **First-deploy spike:** accepted. Processing the existing far-future backlog once is the point of the behaviour change.

### Dependencies
- Initiatives 1–3 complete ✓
- `ALTER TABLE validator_suppressions ADD COLUMN course_id text;` run in Supabase before the validator changes deploy (user runs SQL manually)

### Success criteria
- Past-dated active courses auto-hide on first detection — no frontend exposure
- Far-future courses (>2yr) auto-hide on first detection — no frontend exposure
- Both conditions escalate to the Flags tab with provider-email copy after 24 hours confirmed via log
- `custom_dates=true` courses never caught by either condition
- `validator_warnings` contains no `future_date` entries after next validate run
- Admin Clear on a date escalation writes a course-id-scoped suppression; the zombie never re-escalates on subsequent validate runs
- Flags tab becomes the single place for date-related provider outreach

---

## Initiative 4 — Price sanity + provider improvement loop

### Goal
Replace passive price warnings with an active provider-outreach loop, symmetric with Initiative 5's date model. Null price (with priced peers) and outlier price (>5x median) auto-hide immediately and escalate to a Flags-tab provider touchpoint after 24 hours. Retire the hardcoded Logan/Expedition/Traverse title skip list from validator code.

### Why
- Today `null_price` and `price_outlier` are email-only warnings. The course stays live on the frontend with the questionable (or missing) price visible to users until admin notices.
- A course scraped as $9999 by a parsing bug shows $9999 to every visitor until admin manually intervenes — trust hit, and the same bug will re-surface the same bogus number every 6 hours.
- A course with no price degrades the card's information density and implies "click through to find out" — users bounce.
- The hardcoded `("Logan", "Expedition", "Traverse")` title skip list in `validate_provider.py` is a code smell. Admin-added exceptions already live in `validator_price_exceptions`; the seed rows belong there too.
- Initiative 5 proved the auto-hide + 24h log-confirmation + course-id suppression pattern. Ports directly to price.

### Two conditions covered

| Condition | Trigger | On first detection |
|---|---|---|
| A — Null price with priced peers | `price IS NULL` AND the provider has at least one priced course (the existing `has_prices` guard) | `auto_flagged=true`, `flag_reason='null_price'`. Hidden from frontend. Not yet in Flags tab |
| B — Outlier (>5x median) | `price > median_price * 5` AND title not matched by `validator_price_exceptions` | `auto_flagged=true`, `flag_reason='price_outlier'`. Hidden from frontend. Not yet in Flags tab |

Zero/negative price continues to auto-hide immediately with `flag_reason='invalid price: {value}'` — unchanged by this initiative. A $0 course is unambiguously broken and needs no provider loop. Providers whose entire catalog is null-priced (all custom/quote-based) are untouched by Condition A — the `has_prices` guard preserves them wholesale.

### 24-hour escalation (both conditions)
On every validate run, for each auto-flagged course with `flag_reason IN ('null_price', 'price_outlier')`:
1. Look up the course in the batch result of:
   ```sql
   SELECT DISTINCT course_id
   FROM course_availability_log
   WHERE provider_id = '{pid}'
     AND scraped_at <= now() - interval '24 hours'
   ```
2. If the course_id is in that set → upgrade `flag_reason` to `null_price_escalated` or `price_outlier_escalated`.
3. Admin UI filters on `flag_reason LIKE '%_escalated'` to render the Flags tab price-escalation section (existing filter already covers date escalations — extend the UI filter, not the query).

One query per provider per run — the `load_escalation_candidates(provider_id)` helper added in Initiative 5 is already exactly this query. Reuse it verbatim. Rename to `load_escalation_candidates` (no change) or leave named as-is — it's generic enough already.

### Why `course_availability_log`, not `course_price_log`
`course_price_log` is the more "principled" signal for Condition B (it confirms this exact price has been observed for 24h+). But:
- `log_price_change` skips null-price rows entirely — Condition A has no price-log signal at all.
- A one-off outlier price produces exactly one `course_price_log` row on first scrape, same as `course_availability_log`'s first-seen entry. Same information content.
- Using one signal for both conditions keeps the query shape identical to Initiative 5 and avoids a second query per provider.

`course_price_log` stays sacred but unread by the validator under Initiative 4. A future initiative around price-change-detection (flag a 3x jump versus the last logged value) is the natural consumer.

### Flag_reason progression (explicit two-state)
- `null_price` / `price_outlier` → auto-hidden, NOT in Flags tab
- `null_price_escalated` / `price_outlier_escalated` → auto-hidden AND in Flags tab with provider-email copy + actions

Same shape as Initiative 5's date escalations. Validator re-evaluates on every run; if 24-hour log evidence exists, the suffix is set.

### Admin workflow — Flags tab price-escalation section
New sub-section directly below the Date escalations section. For each escalated row:
- Course title, provider_id, **current price** (or `—` for null), booking_url
- Copyable pre-written email body (text differs per condition — "The price on this listing appears to be missing…" vs "The price on this listing appears significantly higher than similar courses…")
- **Open listing** link to `booking_url`
- **Mark as expected** button — writes `{title_contains: <first 40 chars of title>, provider_id, reason: 'Admin-confirmed price'}` to `validator_price_exceptions`. Suppresses the title pattern across the whole provider going forward.
- **Clear (suppress)** button — writes `{course_id, provider_id, flag_reason: <category>}` to `validator_suppressions`. Course-id-scoped suppression that handles zombies and orphans.

Two admin actions because the semantics differ:
- **Mark as expected** = "this price is correct; stop flagging this title pattern for this provider." Appropriate when the provider's catalog legitimately has premium multi-day courses (Logan-style) that trip the outlier threshold.
- **Clear** = "I've dealt with this specific course; stop bothering me about it regardless of the price." Appropriate for scraper-bug zombies where the bad id persists in the DB even after the provider fixes it.

Initiative 5 had only Clear because there's no "title-scoped date exception" concept. Prices have that concept in `validator_price_exceptions`, so both actions surface.

### Clear behaviour — course-id-scoped suppression
Reuses the Initiative 5 mechanic unchanged. `is_suppressed()` already matches on `course_id + flag_reason category` when the suppression row's `course_id` column is set. The category string derives from `flag_reason` by stripping the `_escalated` suffix and `:` value (e.g. `price_outlier_escalated` → `price_outlier` → `price_outlier` category). Validator priority stack entry 1 already documents both match modes.

### Schema change
**None.** Reuses:
- `validator_suppressions.course_id` (added in Initiative 5)
- `validator_price_exceptions` (existing)
- `course_availability_log` (existing)

One manual SQL step to migrate the hardcoded title skip list:
```sql
INSERT INTO validator_price_exceptions (title_contains, provider_id, reason) VALUES
  ('Logan',      NULL, 'Multi-day premium expedition (migrated from hardcoded list)'),
  ('Expedition', NULL, 'Multi-day premium expedition (migrated from hardcoded list)'),
  ('Traverse',   NULL, 'Multi-day premium traverse (migrated from hardcoded list)');
```
Global-scope (`provider_id=NULL`) so the existing `is_price_exception` match behaviour is preserved.

### Zombie mechanics (known, accepted)
Price is not encoded in course_id (unlike date), so zombie risk is much lower here than in Initiative 5. When a provider fixes a price, the scraper upserts the SAME course_id with the new price; the validator's next run sees the price is fine, no flag fires. No zombie.

The zombie case for price is narrower:
- Provider takes the listing down entirely — orphan course_id lingers in DB with bad price forever. Admin clicks Clear → course-id suppression → inert.
- Scraper bug that can't be fixed provider-side (e.g. the provider's site has a genuinely malformed price field that the scraper reads as $9999 and no site-level fix is coming). Admin clicks Clear or Mark as expected, depending on whether it's a one-off or a title pattern.

Both handled by the same two actions.

### First-deploy behaviour note
Any course currently auto-flagged via the old warning flow is already surfaced in the Flags tab Warnings section — no such rows exist for price (warnings don't auto-flag). On the first validate run after Initiative 4 lands, every existing null-price-with-priced-peers course and every existing >5x-median outlier will:
1. Auto-hide with `flag_reason='null_price'` or `'price_outlier'`
2. Immediately escalate to `*_escalated` if the course has a `course_availability_log` row 24+ hours old (almost always true for courses scraped more than once)

Expect a one-time spike in the Flags tab Price escalations section. Admin processes the backlog once, steady-state resumes. Same shape as Initiative 5's first-deploy note.

For Condition A specifically: the backlog could be large if multiple providers have spotty price scraping. Recommend admin batch-reviews by Mark as expected per provider (one title pattern at a time) rather than Clear-per-course, to avoid accumulating thousands of course-id-scoped suppressions.

### Scope — what changes

| Layer | Change |
|---|---|
| Supabase SQL (manual, before deploy) | Insert 3 rows into `validator_price_exceptions` migrating the hardcoded Logan/Expedition/Traverse list (see schema section above) |
| `validate_provider.py` | Rewrite `check_prices`: null-price branch (with `has_prices` guard) auto-hides instead of emitting warning, flag_reason `null_price`. Outlier branch auto-hides instead of emitting warning, flag_reason `price_outlier`. Both branches apply escalation upgrade via `load_escalation_candidates` set (re-use Initiative 5 helper verbatim, passed in from `main()` alongside the date escalation set — same helper, called once). Zero/negative branch unchanged. Remove `null_price` and `price_outlier` cases from `write_warnings()`. Delete the hardcoded `("Logan", "Expedition", "Traverse")` skip list — `validator_price_exceptions` covers it post-migration. Suppression priority stack unchanged (priority-1 suppressions check already runs before this check) |
| `admin.html` | New Flags tab sub-section "Price escalations" directly below Date escalations, rendering `flag_reason LIKE 'null_price_escalated' OR flag_reason LIKE 'price_outlier_escalated'`. Per row: current price display (dash for null), booking URL link, copyable email body (two templates), Open listing, Mark as expected, Clear buttons. Remove `null_price` and `price_outlier` from the Warnings sub-section (`prettyWarning()` and the action-btn switch). Update Flags tab "How to use this tab" help text |
| `validator_warnings` | `null_price` and `price_outlier` are no longer valid `check_type` values. `reset_warnings` flushes existing rows automatically on first validate run — no migration needed. `validator_warnings` is effectively down to `count_drop` and `all_sold` after this initiative |
| `crawl_courses.py` | If audit categories exist for price conditions: keep detection, update reason strings to note "auto-hidden immediately; escalation after 24h" (mirror Initiative 5's crawl_courses update) |
| CLAUDE.md | Check 2 row rewritten with the two auto-hide conditions, escalation mechanic, and the zero/negative carve-out. `validator_warnings` check_type list loses `null_price` and `price_outlier`. Flags tab description gains the Price escalations sub-section. Mention hardcoded skip list removal in the validator section |

### Out of scope
- Price-change-detection (flag courses whose price jumped >2x since last `course_price_log` entry) — natural future initiative; brings `course_price_log` into the read path.
- Surfacing price history in the admin UI (graph or timeline of `course_price_log` per course).
- Cross-provider median comparison — today's median is per-provider and stays that way.
- Currency-aware median (all priced courses today use CAD default).
- Migrating the existing `validator_warnings` rows — `reset_warnings` handles it at the start of each run.
- A `providers.contact_email` column — admin finds contact info from the provider site, same as Initiative 5.
- Changing the `wrong_price` user-flag auto-clear rule — current rule (price present + positive + ≤5x median) stays consistent with the new auto-hide condition. If a user flags a price and the price is later within range, both the user flag clears AND the auto_flag doesn't fire. No change needed.

### Decisions made in planning
- **One signal for both conditions:** `course_availability_log` for age confirmation, not `course_price_log`. Null-price has no price_log row, and the marginal precision of price_log for outliers isn't worth a second query branch.
- **Retain `has_prices` peer guard:** null-price only auto-hides when the provider has at least one priced course in their catalog. Preserves all-custom/quote-based providers.
- **Outlier threshold unchanged:** >5x median. Robust enough with the `validator_price_exceptions` migration covering multi-day premiums.
- **Two admin actions:** Mark as expected (title-scoped, global-or-provider) AND Clear (course-id-scoped, surgical). Maps onto the existing `validator_price_exceptions` and `validator_suppressions` tables respectively.
- **Zero/negative price unchanged:** stays as immediate auto-hide with its original reason string. No provider loop — $0 is unambiguously broken.
- **Hardcoded list removal:** migrate Logan/Expedition/Traverse to `validator_price_exceptions` rather than keep in code. Admin-visible, admin-editable, consistent with the discoverability of new exceptions.
- **Mark-as-expected scope on escalation:** provider-scoped, not global. An outlier on provider X shouldn't silently suppress the same title pattern on provider Y. `title_contains` = first 40 chars of title (or full title if shorter) to stay specific.

### Dependencies
- Initiatives 1, 2, 3, 5 complete ✓
- `validator_suppressions.course_id` column exists (added in Initiative 5) ✓
- `load_escalation_candidates(provider_id)` helper exists (added in Initiative 5) ✓
- `course_availability_log` populated for all providers ✓ (V2 Phase 2 onward)
- Manual SQL step: 3 INSERT rows into `validator_price_exceptions` before the validator deploy

### Success criteria
- Zero outlier-priced or null-priced-with-priced-peers courses visible on the frontend at steady state
- Both conditions escalate to the Flags tab Price escalations section with provider-email copy after 24 hours confirmed via log
- `validator_warnings` contains no `null_price` or `price_outlier` entries after next validate run
- Admin Clear on a price escalation writes a course-id-scoped suppression; the course never re-escalates on subsequent runs
- Admin Mark as expected on a price escalation writes a `validator_price_exceptions` row; the whole title pattern stops flagging for that provider
- Hardcoded `("Logan", "Expedition", "Traverse")` skip list is removed from `validate_provider.py`; the three migrated `validator_price_exceptions` rows cover the same behaviour
- Flags tab becomes the single place for price-related provider outreach (alongside date outreach from Initiative 5)
