# Handover — Initiative 4 implementation

**Read this file first. Then read `CLAUDE.md` in full. Then begin work.**

You're starting a fresh codespace with no conversation history. Your first task is to implement Initiative 4 of the data-quality mission.

**Delete this file in the same commit as the implementation.** It's ephemeral.

---

## Critical: the committed Initiative 4 brief is wrong

`data_quality_initiatives.md` was committed with a two-condition Initiative 4 scope (null-price + outlier + zero/negative with migration of hardcoded skip list to `validator_price_exceptions`). That scope was **rejected by the user** after commit. The corrected scope is **one condition only** — zero/negative price — with null-price and outlier logic deleted entirely.

### Your first commit should be doc correction

Before writing any implementation code:
1. Open `data_quality_initiatives.md`
2. Replace the entire existing `## Initiative 4 — ...` section with the **Corrected Initiative 4 brief** below (copy it verbatim).
3. Commit + push: `docs: correct Initiative 4 scope — zero/negative only, null/outlier deleted`

This gives a clean starting point and aligns the committed plan with the actual work.

---

## Corrected Initiative 4 brief (authoritative — use this, not the committed version)

```markdown
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
- `course_price_log` schema and `log_price_change` (sacred, writes zero/negative normally)
- `validator_suppressions.course_id` (reused)
- Scrapers
- `wrong_price` intake via `notify-report`

### Schema change
**None.** No manual SQL step. `validator_price_exceptions` table stays in Supabase, orphaned, drops at V2 Phase 7 with other retired surfaces.

### Zombie mechanics
Price isn't encoded in course_id → when a provider fixes the price, the same course_id upserts with the new value, validator sees `price > 0`, no flag. No zombie. Remaining cases (abandoned listing, unfixable scraper bug) handled by one-click Clear.

### First-deploy behaviour
Existing zero/negative courses with a `course_price_log` row from 24h+ ago auto-hide (most already are) and immediately escalate. Small one-time spike expected in the Flags tab — `$0` courses are rarer than the old null-price/outlier backlogs.

### Scope — what changes
| Layer | Change |
|---|---|
| `validate_provider.py` | `check_prices` collapsed to one branch. Delete null-price, outlier, `is_price_exception`, hardcoded skip list, `validator_price_exceptions` load. Rename `flag_reason` from `f"invalid price: {price}"` to `invalid_price`. Add `load_price_escalation_candidates(provider_id)`. Apply escalation upgrade. Remove `null_price` / `price_outlier` from `write_warnings`. Simplify `wrong_price` auto-clear |
| `admin.html` | New Flags tab "Price escalations" sub-section. Delete `null_price` / `price_outlier` from Warnings handlers + JS. Update Flags-tab help text |
| `validator_warnings` | Types narrowed to `count_drop`, `all_sold` |
| `crawl_courses.py` | Delete null-price and outlier audit categories. Update/add zero-negative category reason string |
| CLAUDE.md | Check 2 rewritten (one condition). Validator priority stack simplified. `validator_warnings` type list trimmed. Flags tab section updated. Note `validator_price_exceptions` orphaned-pending-Phase 7 |

### Out of scope
- Price-change-detection, cross-provider comparison — no replacement for deleted checks, permanent
- Dropping `validator_price_exceptions` table — Phase 7
- Changing `<= 0` threshold

### Decisions
- **Null price ignored entirely** — no flag, no warning, no escalation. Not deferred, deleted.
- **Outlier check deleted permanently** — no median, no delta, no percentage. Will not return.
- **`course_price_log` as escalation signal** — user-specified.
- **Clear only** — no Mark-as-expected; no legitimate zero-price whitelist pattern.
- **`flag_reason` enum rename** — consistent with Initiative 5.
- **`wrong_price` auto-clear simplified** to `price > 0`.

### Dependencies
- Initiatives 1, 2, 3, 5 complete ✓
- `validator_suppressions.course_id` exists ✓
- `course_price_log` populated ✓

### Success criteria
- Zero/negative auto-hide on first detection
- Escalate to Flags tab after 24h via `course_price_log`
- Zero null-price or outlier flags written, ever
- `is_price_exception`, hardcoded list, `validator_price_exceptions` load all gone from `validate_provider.py`
- Admin Clear writes course-id suppression; no re-escalation
- Flags tab Price escalations is the only price-related admin surface
```

---

## What's already in place (do not rebuild — reuse)

Initiative 5 shipped these, you will reuse them:

- **`validator_suppressions.course_id`** column (nullable text). Initiative 5 added it. `is_suppressed()` / `any_check_suppressed()` already branch on course-id-scoped vs title-scoped matches.
- **`load_escalation_candidates(provider_id)`** helper in `validate_provider.py` — queries `course_availability_log` for 24h+-old course_ids. Your new `load_price_escalation_candidates` is a sibling that queries `course_price_log` instead and reconstructs course_ids from `(provider_id, date_sort, title_hash)` since the price log has no `course_id` column.
- **`course_price_log`** is populated — every scraper calls `log_price_change` after upsert. It skips null prices only; zero/negative prices are logged normally. A row for a zero-priced course from 24h+ ago is your escalation signal.
- **Flags tab Date escalations** sub-section in `admin.html` — model Price escalations on it. Same card layout, same copyable-email pattern, same Clear button writing to `validator_suppressions` with `course_id` scope.

---

## Key file pointers

- `validate_provider.py`
  - `check_prices` — around line 362. This is the function you collapse to one branch.
  - `is_price_exception` helper — around line 127. Delete.
  - `auto_clear_user_flags` — around line 186. The `wrong_price` branch has a 5x-median check around line 205; simplify to `price > 0`.
  - `write_warnings` — around line 149. Has a `price_outlier` exception-skip branch at line 162; that whole branch goes since `price_outlier` as a warning type is deleted.
  - `main()` — around line 773. Loads `price_exceptions` from `validator_price_exceptions`; delete the load. Call `load_price_escalation_candidates` alongside Initiative 5's `load_escalation_candidates` and pass both sets into the checks that need them.
- `admin.html`
  - `prettyWarning()` — around line 1527. Delete the `price_outlier` and `null_price` cases.
  - Warnings action-btn switch — around line 1700. Delete `price_outlier` / `null_price` / `markPriceExpected` / `warningMarkExpected`.
  - Direct write to `validator_price_exceptions` — around line 1778. Delete.
  - Flags tab "How to use this tab" — around line 253. Update to describe Price escalations sub-section, drop Mark-as-expected language.
  - Model the new Price escalations sub-section on the existing Date escalations block (search for `past_date_escalated` / `future_date_escalated`).
- `crawl_courses.py` — audit categories for price. Delete null-price and outlier; keep zero/negative, update reason string.
- `scraper_utils.py` — `log_price_change` at line 738. **Do not modify.** It's sacred and already writes zero/negative prices correctly.
- `CLAUDE.md` — Check 2 row in the validator 6-check table, validator priority stack description, `validator_warnings` check_type list, admin Flags-tab description.

---

## Working conventions (from CLAUDE.md — repeated for emphasis)

- Read `CLAUDE.md` in full before making changes. All conventions, stack context, naming standards live there.
- **Commit + push immediately after any file change, no confirmation prompt:** `git add -A && git commit -m "..." && git push`.
- Never ask for confirmation mid-task. Complete the prompt end-to-end.
- Only touch columns listed in the `CLAUDE.md` schema section. No `ALTER TABLE` in migration files without flagging.
- Every tab has a "How to use this tab" help section that must be updated in the same commit as any UX change.
- After any admin write action, the actioned row is immediately removed from the UI.

---

## Suggested commit shape

1. `docs: correct Initiative 4 scope — zero/negative only, null/outlier deleted` (this doc's Corrected brief → replaces committed Initiative 4 section)
2. `feat(data quality): Initiative 4 — price sanity zero/negative auto-hide + escalation` (implementation: validate_provider.py, admin.html, crawl_courses.py, CLAUDE.md) — also deletes this `HANDOVER_INITIATIVE_4.md` file.

Alternatively: one combined commit if you prefer. The user's preference is fast iteration over ceremonial splitting.

---

## Verification after deploy

No schema change, no SQL step. After the implementation is pushed:
1. Trigger `validate-provider` workflow for one provider via GitHub Actions (`validate-provider.yml` with a provider_id input) to sanity-check.
2. Inspect Supabase: `validator_warnings` for that provider should have no `null_price` or `price_outlier` rows. Any existing zero-priced courses should have `flag_reason = 'invalid_price'` (clean enum, not the old free-text `invalid price: 0`). If the course has a `course_price_log` row 24h+ old, `flag_reason` should be `invalid_price_escalated`.
3. Load the admin Flags tab. The new Price escalations sub-section should render below Date escalations. Warnings sub-section should no longer show Mark-as-expected buttons for price rows.

---

## If you get stuck

- Re-read the corrected brief above. Follow the "What gets deleted" table literally — every item is a deletion, not a refactor.
- Initiative 5 is the working template — its code in `validate_provider.py` and `admin.html` is the pattern to copy for Price escalations.
- The user has been explicit: **no null-price logic, no outlier logic, in any form.** If you find yourself writing a median comparison or a null-check that produces a flag/warning, you're off-scope.
