# Data quality initiative briefs

Living reference for the data-quality cleanup mission. Each initiative below is self-contained — read it cold and you should understand what, why, and the decisions already made.

Order of execution: **Initiative 1 → 2 → 3 → 5 → 4 → 6 → 7.** Activity is a pure deletion (low risk, unblocks the audit backlog); location is a behaviour change (medium risk, benefits from cleaner audit ground); Summary Review is a workflow redesign (low-to-medium risk, benefits from the validator being quiet); Date sanity is an active provider-outreach loop (medium risk, benefits from every other queue being tame); Price sanity ports that same active-loop shape to the remaining passive warnings, re-using the course-id-scoped suppression mechanic; Duplicate simplification strips the validator's final admin-decision path, reducing the priority stack to two layers; Count-drop relocation moves the last scraper-health signal off the Flags tab and onto the Providers tab where it belongs.

**Status (2026-04-17):** Initiatives 1, 2, 3, 4, and 5 shipped. Initiatives 6 and 7 planned (this document). Initiative 1 scraper-side in `5157faa`, admin fast-follow in `c83bd4a`. Initiative 2 in `5abb3f1`. Initiative 3 plan in `cd13bcc`, implementation in `615c5e9`. Initiative 5 plan in `8885144`, implementation in `300eae3`. Initiative 4 plan in `1b5604c` (corrected scope replacing rejected `39eb59b` two-condition version), implementation in `cdb0581`.

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

## Initiative 4 — Price sanity: zero/negative auto-hide + escalation

### Goal
One active check, one provider-outreach loop. Zero/negative price auto-hides immediately and escalates to the Flags tab after 24 hours confirmed via `course_price_log`. Delete null-price and outlier checks entirely — they do not come back in any form.

### Why
- Null-price and outlier warnings produced more admin noise than value.
- Outlier (>5x median) is self-referential (provider's own median) and already required a hardcoded Logan/Expedition/Traverse carve-out — a check that solves for false positives rather than real bugs.
- Null-price isn't a bug — some listings legitimately have no displayed price; the frontend renders gracefully.
- Zero/negative is always wrong. Unambiguous signal, worth an active loop.
- Initiative 5's auto-hide + 24h log-confirmation + course-id-scoped suppression pattern ports directly to this single condition.

### One condition covered
| Condition | Trigger | On first detection |
|---|---|---|
| Zero/negative price | `price IS NOT NULL AND price <= 0` | `auto_flagged=true`, `flag_reason='invalid_price'`. Hidden from frontend. Not yet in Flags tab |

Null price is ignored. No median comparison anywhere.

### 24-hour escalation via `course_price_log`
One query per provider per run:
```sql
SELECT DISTINCT title_hash, date_sort
FROM course_price_log
WHERE provider_id = '{pid}'
  AND logged_at <= now() - interval '24 hours'
```
Validator reconstructs course_ids from `(provider_id, date_sort, title_hash)` per V2 id format (`{provider}-{date_sort | 'flex'}-{title_hash}`) and builds a set. Any `invalid_price` course in that set upgrades to `invalid_price_escalated`.

`log_price_change` already writes zero/negative prices (only null is skipped), so the signal exists from first scrape onward. New helper `load_price_escalation_candidates(provider_id)` — sibling to Initiative 5's availability-log version.

### Flag_reason progression
- `invalid_price` → auto-hidden, NOT in Flags tab
- `invalid_price_escalated` → auto-hidden AND in Flags tab with provider-email copy

Renames the existing free-text `f"invalid price: {price}"` to clean `invalid_price`. Admin UI reads the numeric value from the course row.

### Admin workflow — Flags tab Price escalations section
New sub-section between Date escalations and Warnings. Per row: title, provider_id, current price, booking_url, copyable email body ("listing price is showing as $0 or negative"), Open listing, **Clear**. One action only — no Mark-as-expected (no legitimate zero-price pattern worth whitelisting).

### Clear — course-id-scoped suppression
Reuses Initiative 5's mechanic unchanged. `validator_suppressions` write with `{course_id, provider_id, flag_reason: 'invalid_price'}`.

### What gets deleted
| Target | Fate |
|---|---|
| `check_prices` null-price branch | Deleted. No flag, no warning, no write when `price IS NULL` |
| `check_prices` outlier branch | Deleted. No median comparison |
| `is_price_exception` helper + `validator_price_exceptions` load at `main()` start | Deleted |
| Hardcoded `("Logan", "Expedition", "Traverse")` skip list | Deleted |
| Validator priority stack `validator_price_exceptions` layer | Deleted. Stack: suppressions → summary exceptions → whitelist |
| `validator_warnings` types `null_price` / `price_outlier` | Deleted. `reset_warnings` flushes stale rows |
| `prettyWarning()` and action-btn cases for `null_price` / `price_outlier` | Deleted from `admin.html` |
| `markPriceExpected` / `warningMarkExpected` JS | Deleted (unreachable) |
| `auto_clear_user_flags` 5x-median check on `wrong_price` | Simplified to just `price > 0` |

### What's unchanged
- `course_price_log` append-only semantics (still sacred — never truncate/delete)
- `log_price_change` change-detection logic (zero/negative still logged normally)
- `validator_suppressions.course_id` (reused)
- Scrapers
- `wrong_price` intake via `notify-report`

### Schema change
**One.** User runs manually in Supabase SQL editor before deploy:

```sql
ALTER TABLE course_price_log
ADD COLUMN IF NOT EXISTS bad_data boolean default false;
```

`log_price_change` in `scraper_utils.py` sets `bad_data=true` on the insert payload when `price <= 0`, else `false`. Protects Phase 5 velocity-signal consumers from zero-priced rows polluting price-trend analytics without requiring read-time filter logic in every future consumer. Existing log rows default to `false` — acceptable because (a) historical zero-priced rows are rare, (b) Phase 5 hasn't started so there are no consumers reading stale data, (c) any remaining polluters can be backfilled with `UPDATE course_price_log SET bad_data=true WHERE price <= 0` if Phase 5 reveals a need.

`validator_price_exceptions` table stays in Supabase, orphaned, drops at V2 Phase 7 with other retired surfaces.

### Zombie mechanics
Price isn't encoded in course_id → when a provider fixes the price, the same course_id upserts with the new value, validator sees `price > 0`, no flag. No zombie. Remaining cases (abandoned listing, unfixable scraper bug) handled by one-click Clear.

### First-deploy behaviour
Existing zero/negative courses with a `course_price_log` row from 24h+ ago auto-hide (most already are) and immediately escalate. Small one-time spike expected in the Flags tab — `$0` courses are rarer than the old null-price/outlier backlogs.

### Scope — what changes
| Layer | Change |
|---|---|
| `validate_provider.py` | `check_prices` collapsed to one branch. Delete null-price, outlier, `is_price_exception`, hardcoded skip list, `validator_price_exceptions` load. Rename `flag_reason` from `f"invalid price: {price}"` to `invalid_price`. Add `load_price_escalation_candidates(provider_id)`. Apply escalation upgrade. Remove `null_price` / `price_outlier` from `write_warnings`. Simplify `wrong_price` auto-clear |
| `scraper_utils.py` | `log_price_change` writes `bad_data=true` when `price <= 0`, else `false`. Only line touched — change-detection logic unchanged |
| `admin.html` | New Flags tab "Price escalations" sub-section. Delete `null_price` / `price_outlier` from Warnings handlers + JS. Update Flags-tab help text |
| `validator_warnings` | Types narrowed to `count_drop`, `all_sold` |
| `crawl_courses.py` | Delete null-price and outlier audit categories. Update/add zero-negative category reason string |
| `course_price_log` schema | New `bad_data boolean default false` column. Manual SQL before deploy |
| CLAUDE.md | Check 2 rewritten (one condition). Validator priority stack simplified. `validator_warnings` type list trimmed. Flags tab section updated. `course_price_log` schema table gains `bad_data` row. Note `validator_price_exceptions` orphaned-pending-Phase 7 |

### Out of scope
- Price-change-detection, cross-provider comparison — no replacement for deleted checks, permanent
- Dropping `validator_price_exceptions` table — Phase 7
- Changing `<= 0` threshold

### Decisions
- **Null price ignored entirely** — no flag, no warning, no escalation. Not deferred, deleted.
- **Outlier check deleted permanently** — no median, no delta, no percentage. Will not return.
- **`course_price_log` as escalation signal** — user-specified.
- **`bad_data` column on `course_price_log`** — user-specified. Set at write time by `log_price_change`. Protects Phase 5 analytics from zero-priced-row pollution without read-time filter logic.
- **Clear only** — no Mark-as-expected; no legitimate zero-price whitelist pattern.
- **`flag_reason` enum rename** — consistent with Initiative 5.
- **`wrong_price` auto-clear simplified** to `price > 0`.

### Dependencies
- Initiatives 1, 2, 3, 5 complete ✓
- `validator_suppressions.course_id` exists ✓
- `course_price_log` populated ✓
- `course_price_log.bad_data` column added by user before deploy (manual SQL — see Schema change)

### Success criteria
- Zero/negative auto-hide on first detection
- Escalate to Flags tab after 24h via `course_price_log`
- Zero null-price or outlier flags written, ever
- `is_price_exception`, hardcoded list, `validator_price_exceptions` load all gone from `validate_provider.py`
- Admin Clear writes course-id suppression; no re-escalation
- Flags tab Price escalations is the only price-related admin surface
- `log_price_change` writes `bad_data=true` for zero/negative prices going forward

---

## Initiative 6 — Duplicate detection simplification

### Goal
Strip the duplicate check back to a pure scraper-signal. Auto-hide protects the frontend; the Flags tab tells you which scraper to fix. No Haiku, no whitelist, no admin decision path.

### Why
- Duplicates are always scraper bugs, not provider data issues. The resolution path is always "fix the scraper" — Haiku diagnosis and whitelist management were solving the wrong problem.
- `validator_whitelist` added complexity to the priority stack for an escape hatch that shouldn't exist — if two identical title+date courses are intentional, the scraper is wrong, not the data.
- `admin-diagnose-duplicate` edge function added a Haiku call per duplicate group for a verdict that was always "fix the scraper."

### Scope — what changes

| Layer | Change |
|---|---|
| `validate_provider.py` | Keep auto-hide logic (all but first occurrence of same title+date). Remove `validator_whitelist` load and lookup entirely. `check_duplicates` signature drops the `whitelisted` param. Admin suppression lookup stays — it's still the universal ignore mechanism |
| `admin.html` Flags tab | Remove Diagnose and Whitelist actions from duplicate auto-flag groups. Duplicate rows render as read-only signals: provider badge + title + occurrence count + link to the scraper file on GitHub (`https://github.com/lukeram-3420/backcountryfinder/blob/main/scraper_{provider_id}.py`). One link per `provider_id` in the group. Clear all stays (writes title-scoped suppression — unchanged) |
| `supabase/functions/admin-diagnose-duplicate/` | Delete entirely |
| `deploy-functions.yml` | Remove `admin-diagnose-duplicate` deploy step |
| `validator_whitelist` | Stop reading. Existing rows harmless — drops at V2 Phase 7 alongside other orphaned tables |
| `crawl_courses.py` | Update duplicate category reason string — note auto-hide behaviour, remove whitelist reference |
| CLAUDE.md | Remove `validator_whitelist` from priority stack description. Update Flags tab section (duplicate rows now read-only with scraper link; no Diagnose / Whitelist buttons). Remove `admin-diagnose-duplicate` row from edge function table. Mark `validator_whitelist` as orphaned-pending-Phase 7 in the admin-tables reference list |

### What stays
- Auto-hide: all but first occurrence of same `(title, date_sort)` — correct behaviour, protects frontend.
- Duplicate rows visible in the Flags tab as read-only scraper signal.
- `validator_whitelist` table in Supabase — orphaned, drops at V2 Phase 7.
- `validator_suppressions` still consulted for duplicate flags — admin can still "Clear all" to globally suppress a specific title-pattern + reason combo if needed.

### What gets dropped
- `admin-diagnose-duplicate` edge function — deleted.
- Diagnose button in Flags tab — deleted.
- Whitelist button in Flags tab — deleted.
- `validator_whitelist` load + lookup in `validate_provider.py` — deleted.
- Haiku call for duplicate analysis — deleted (admin-initiated, not in validator).

### Validator priority stack after Initiative 6
Two layers only:

1. `validator_suppressions` — explicit admin "ignore this" decision (highest priority, both title- and course-id-scoped match modes).
2. `validator_summary_exceptions` — admin-resolved summary text (bleed check only).

No price-exceptions layer (removed in Initiative 4). No whitelist layer (removed in this initiative).

### No provider outreach loop
Duplicates are scraper bugs. Resolution path: identify the scraper from the Flags-tab signal → fix it in Claude Code → re-run scraper + validate → duplicates clear automatically on the next validate run because the second occurrence stops existing. No admin action required, no provider email. If the scraper can't be fixed immediately, admin uses the existing "Clear all" button to write a title-scoped suppression.

### Scraper file link format
`https://github.com/lukeram-3420/backcountryfinder/blob/main/scraper_{provider_id}.py`. One link per `provider_id` in the duplicate group. For cross-provider duplicate groups (rare), render one chip per provider. For the legacy monolith case (`scraper.py --provider X`), fall back to the monolith link + the provider flag in the UI label.

### Dependencies
- Initiatives 1–5 complete ✓
- No Supabase SQL required
- No new tables or columns

### Success criteria
- Duplicate courses still auto-hidden correctly (no frontend regression).
- Flags tab shows duplicate groups as read-only signals with scraper file link (one per provider_id).
- No Diagnose or Whitelist button anywhere in admin.
- `admin-diagnose-duplicate` gone from Supabase Edge Functions list (also removed from `deploy-functions.yml`).
- `validator_whitelist` no longer loaded or referenced in `validate_provider.py`.
- Priority stack has 2 layers (suppressions → summary exceptions).

---

## Initiative 7 — Course count drop: scraper health signal on Providers tab

### Goal
Move the >30% course count drop check out of the Flags tab (data quality surface) and into the Providers tab (scraper health surface) where it belongs. Computed client-side from `scraper_run_log` on every Providers-tab load. No server-side write, no `validator_warnings` row.

### Why
- A course count drop is a scraper alarm, not a data quality issue. No course row is wrong — the scraper just fetched fewer of them.
- The Providers tab already shows last run, course count, and status per provider — a count-drop badge fits there naturally.
- Resolution is always: check GitHub Actions scraper run logs → identify the failure → re-run. The Flags tab adds no value to that workflow.
- `validator_warnings.count_drop` rows are currently the only surface for this — moving to the Providers tab makes the signal immediately visible without navigating to Flags.

### Scope — what changes (Option 1 — client-side only, no validator write)

| Layer | Change |
|---|---|
| `validate_provider.py` | Delete `check_course_count()` function entirely. Remove its call from `main()`. Detection logic moves client-side — one source of truth |
| `admin.html` Providers tab | On tab load, fetch the last 2 rows of `scraper_run_log` per provider (ordered by `run_at desc`). Compute drop pct = `(prev - curr) / prev`. If >30%, render a yellow warning badge on the status cell. Badge links to `https://github.com/lukeram-3420/backcountryfinder/actions/workflows/scraper-{provider_id}.yml`. Hover tooltip: `"Course count dropped {N}% ({prev} → {curr}) — check scraper run"` |
| `admin.html` Flags tab | Drop `count_drop` from `prettyWarning()` switch. Drop the `count_drop` action-btn branch (`warningViewProvider`). Function stays if needed for other types, else delete |
| `validator_warnings` | `count_drop` is no longer a valid `check_type` — types narrow to `null_avail` + `all_sold` (supersedes Initiative 4's "count_drop, all_sold"). `reset_warnings` at run start flushes any stale rows automatically; no migration needed |
| `scraper_run_log` | Schema unchanged — already tracks `(provider_id, run_at, course_count)` which is exactly what the Providers-tab client query needs |
| CLAUDE.md | Remove Check 6 (Course count) from validator 6-check table — no longer a validator concern. Update `validator_warnings` check_type list (drop `count_drop`; remaining: `null_avail`, `all_sold`). Update Flags tab description (no more count_drop rows). Update Providers tab description with the new count-drop badge + link. Note that count-drop detection moved from server-side validator to client-side Providers-tab query |

### What stays
- `scraper_run_log` table and the scrape-time write in `validate_provider.py`'s `main()` — the 1-row-per-provider-per-run log is still the authoritative scraper health signal. Only `check_course_count()` (the 6th check) is removed.
- >30% threshold — unchanged, just computed client-side now.
- `notify-report` user flow — untouched.
- All other validator checks — untouched.

### What gets dropped
- `check_course_count()` in `validate_provider.py` — deleted.
- `count_drop` as a `check_type` in `validator_warnings` — retired (types narrow to `null_avail`, `all_sold`).
- `prettyWarning('count_drop', ...)` case + `warningViewProvider` action in admin.html — deleted.
- Flags-tab visibility of scraper-health signals — by design. The Flags tab is data-quality only after this initiative.

### Providers-tab indicator behaviour
- Normal state: no indicator.
- Drop >30%: yellow warning badge on the status cell with the drop-pct label + GitHub Actions link.
- Clears automatically on the next healthy scrape run where the count recovers — the badge is a function of the last two `scraper_run_log` rows, so fresh data recomputes on every tab load. No admin action to clear.

### No new tables, no server-side write
Everything the Providers-tab badge needs already exists in `scraper_run_log`. The only write that survives in `main()` is the existing `sb_upsert("scraper_run_log", ...)` at the end of validation — that's the data source. Detection moves from server-side (`check_course_count` writes to `validator_warnings`) to client-side (`loadProvidersTab` reads `scraper_run_log`, computes inline). One query per provider on tab load, or a single query with `ORDER BY provider_id, run_at DESC` and client-side grouping. Cheap.

### Dependencies
- Initiatives 1–6 complete ✓
- No Supabase SQL required
- No new tables or columns

### Success criteria
- >30% count drop visible as yellow warning badge in Providers tab.
- Badge links to GitHub Actions scraper workflow for that provider.
- Flags tab Warnings section no longer shows `count_drop` entries.
- `validator_warnings` contains only `null_avail` and `all_sold` types after next validate run.
- Count-drop badge clears automatically on the next healthy scrape run.
- `scraper_run_log` unchanged (schema and write path).
- `check_course_count()` gone from `validate_provider.py`.
