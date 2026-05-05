#!/usr/bin/env python3
"""
validate_provider.py — Post-scrape validation for a single provider.

Queries Supabase for the provider's courses, runs data quality checks,
auto-hides bad rows (auto_flagged=true), auto-clears resolved user reports,
and emails a report.

Two-flag system:
  - auto_flagged + flag_reason → set by this script only, reset each run
  - flagged + flagged_reason + flagged_note → set by notify-report edge function (user reports),
    auto-cleared by this script when the underlying issue is resolved

Usage:
    python validate_provider.py <provider_id>
"""

import hashlib
import logging
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta

import requests

from scraper_utils import (
    sb_get, sb_upsert, sb_patch, send_email,
    generate_summaries_batch,
    SUPABASE_URL, SUPABASE_KEY,
)


# ── Flag helpers ─────────────────────────────────────────────────────────────

# Module-level suppression cache populated by main() for each run. Kept as a
# module global so every check function's existing flag_course calls honour
# it without signature threading.
_current_provider_id: str = ""
_suppressions_cache: list = []


def _reason_category(reason: str) -> str:
    """Top-level category of a flag_reason string — portion before the first
    ':'. e.g. 'activity mismatch: ...' -> 'activity mismatch'."""
    return (reason or "").split(":", 1)[0].strip().lower()


def is_suppressed(title: str, reason: str, course_id: str = "") -> bool:
    """True if an admin 'Clear all' suppression exists for this (title, reason)
    or (course_id, reason) combo.

    Two match modes (Initiative 5):
      - Course-scoped: suppression.course_id is set → requires exact course_id
        match + reason-category match. Used by Clear on date escalations so the
        Flags-tab date section can retire a specific stale course_id without
        over-suppressing other rows sharing the same title.
      - Title-scoped (unchanged): suppression.course_id is NULL → requires
        title_contains substring + reason-category match. Used by everything
        else (duplicates, activity before retirement, summary flows).

    In both modes, provider_id must be NULL (global) or match current provider.
    """
    if not _suppressions_cache:
        return False
    t = (title or "").lower()
    proposed_cat = _reason_category(reason)
    if not proposed_cat:
        return False
    for s in _suppressions_cache:
        s_pid = s.get("provider_id")
        if s_pid and s_pid != _current_provider_id:
            continue
        if _reason_category(s.get("flag_reason") or "") != proposed_cat:
            continue
        s_cid = s.get("course_id")
        if s_cid:
            # Course-scoped suppression — exact match required.
            if course_id and s_cid == course_id:
                return True
            continue
        # Title-scoped suppression (legacy / default).
        s_tc = (s.get("title_contains") or "").strip().lower()
        if not s_tc or s_tc not in t:
            continue
        return True
    return False


def any_check_suppressed(title: str, categories, course_id: str = "") -> bool:
    """True if an admin suppression matches this title/course_id for ANY of
    the given flag-reason categories. Used at the top of each check's
    per-course loop so admin decisions short-circuit the whole check — not
    just the final flag_course() write.
    """
    for cat in categories:
        if is_suppressed(title, cat, course_id=course_id):
            return True
    return False


def flag_course(course_id: str, reason: str, auto_hidden: list, title: str = ""):
    """Patch a single course row as auto_flagged and record it — unless a
    (title, reason-category) OR (course_id, reason-category) suppression exists.
    """
    if is_suppressed(title, reason, course_id=course_id):
        logging.info(f"Suppressed flag (admin-cleared): {course_id or title!r} / {reason!r}")
        return
    sb_patch("courses", f"id=eq.{course_id}", {
        "auto_flagged": True,
        "flag_reason": reason[:200],
    })
    auto_hidden.append({"id": course_id, "reason": reason})


def reset_flags(provider_id: str):
    """Clear all auto_flags for this provider before re-validating.
    Never touches flagged/flagged_reason/flagged_note (user reports)."""
    sb_patch(
        "courses",
        f"provider_id=eq.{provider_id}&auto_flagged=eq.true",
        {"auto_flagged": False, "flag_reason": None},
    )


def reset_warnings(provider_id: str):
    """Delete existing validator_warnings rows for this provider (clean slate)."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/validator_warnings?provider_id=eq.{provider_id}"
    resp = requests.delete(url, headers=headers, timeout=30)
    resp.raise_for_status()


def write_warnings(provider_id: str, provider_name: str, email_only: list):
    """Write collected email-only warnings to validator_warnings table."""
    if not email_only:
        return
    rows = []
    for i in email_only:
        check_type = i.get("check_type")
        if not check_type:
            continue
        rows.append({
            "provider_id": provider_id,
            "course_id": i.get("id") or None,
            "title": i.get("title") or provider_name,
            "check_type": check_type,
            "reason": i.get("issue", "")[:500],
        })
    if rows:
        sb_upsert("validator_warnings", rows)


# ── User report auto-clear ───────────────────────────────────────────────────

def auto_clear_user_flags(provider_id: str, courses: list) -> tuple:
    """
    Check user-flagged courses and auto-clear if the issue is resolved.
    Returns (cleared_list, still_open_list).
    """
    # Fetch user-flagged courses for this provider
    flagged_courses = sb_get("courses", {
        "provider_id": f"eq.{provider_id}",
        "flagged": "eq.true",
        "select": "id,title,flagged_reason,flagged_note,price,date_sort,avail,summary",
    })

    if not flagged_courses:
        return [], []

    cleared = []
    still_open = []

    for fc in flagged_courses:
        reason = fc.get("flagged_reason") or ""
        can_clear = False

        if reason == "wrong_price":
            price = fc.get("price")
            if price is not None and price > 0:
                can_clear = True

        elif reason == "wrong_date":
            ds = fc.get("date_sort")
            if ds:
                try:
                    d = datetime.strptime(ds, "%Y-%m-%d").date()
                    if d >= date.today():
                        can_clear = True
                except (ValueError, TypeError):
                    pass

        elif reason == "sold_out":
            if fc.get("avail") != "open":
                can_clear = True

        # bad_description / button_broken / other → never auto-clear.
        # Per Initiative 3, every user-flagged bad_description reaches the
        # admin via the Summary Review tab for explicit acknowledgement.

        if can_clear:
            sb_patch("courses", f"id=eq.{fc['id']}", {
                "flagged": False,
                "flagged_reason": None,
                "flagged_note": None,
            })
            cleared.append({"id": fc["id"], "title": fc["title"], "reason": reason})
        else:
            still_open.append({
                "id": fc["id"],
                "title": fc["title"],
                "reason": reason,
                "note": fc.get("flagged_note") or "",
            })

    return cleared, still_open


# ── Checks ───────────────────────────────────────────────────────────────────

def _summary_hash(text: str) -> str:
    """md5 of the stripped summary text. Matches the hash key scheme used
    when admin saves write to validator_summary_exceptions."""
    return hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()


def backfill_missing_summaries(courses: list, provider_id: str, provider_name: str) -> list:
    """Initiative 3 — inline regeneration safety net. For any course with a
    null/empty summary, call generate_summaries_batch() with the title as
    the description seed (scrapers strip real description before upsert).
    Writes results directly to courses.summary + courses.search_document.

    Courses that still have no summary after this call are returned — they
    surface in the Summary Review tab via the null-summary row source. The
    validator does NOT auto-flag them.

    Safety net, not quality floor: a mediocre title-seeded summary is
    strictly better than a blank card.
    """
    missing = [c for c in courses if not (c.get("summary") or "").strip()]
    if not missing:
        return []

    logging.info(f"Backfilling {len(missing)} missing summaries via inline Haiku…")
    # generate_summaries_batch skips inputs with empty description, so seed
    # with the title itself — same fallback admin-regenerate-summary uses.
    inputs = [{
        "id":          c["id"],
        "title":       c["title"],
        "description": c["title"],
        "provider":    provider_name,
        "provider_id": provider_id,
    } for c in missing]
    try:
        results = generate_summaries_batch(inputs, provider_id=provider_id) or {}
    except Exception as e:
        logging.warning(f"backfill_missing_summaries: generate_summaries_batch failed: {e}")
        results = {}

    patched = 0
    for c in missing:
        r = results.get(c["id"]) or {}
        summary = (r.get("summary") or "").strip() if isinstance(r, dict) else str(r or "").strip()
        search_doc = r.get("search_document", "") if isinstance(r, dict) else ""
        if not summary:
            continue
        try:
            sb_patch("courses", f"id=eq.{c['id']}", {
                "summary": summary,
                "search_document": search_doc,
            })
            c["summary"] = summary
            c["search_document"] = search_doc
            patched += 1
        except Exception as e:
            logging.warning(f"backfill_missing_summaries: patch failed for {c['id']}: {e}")

    logging.info(f"Backfill patched {patched}/{len(missing)} missing summaries")
    # Return the courses still missing a summary (for reporting only)
    return [c for c in missing if not (c.get("summary") or "").strip()]


def check_summaries(courses: list, auto_hidden: list, summary_exceptions: set) -> list:
    """Check 1: Summary quality (Initiative 3 — bleed-only).

    Priority stack per course:
      1. Admin suppression (validator_suppressions for 'summary mismatch')
         → skip the whole check.
      2. validator_summary_exceptions — admin-saved summary text. If the
         course's current (provider_id, md5(summary)) is in the exception
         cache, skip the bleed check. One admin save clears the whole
         collision group — including the other side on the NEXT run.
      3. Bleed auto-hide: identical summary text across DIFFERENT titles
         within the same provider. Second (and subsequent) occurrences
         are auto-flagged with flag_reason='summary_bleed'. The first
         occurrence stays visible and un-touched.

    Empty-summary detection was retired — callers use
    backfill_missing_summaries() before this function runs. Any course
    that still has a null summary after backfill surfaces in the Summary
    Review tab via the admin-side row query; it is NOT auto-flagged.
    """
    email_only: list = []

    # Group by stripped summary text. Only texts shared across different
    # course titles within this provider are bleed candidates.
    summary_to_courses: dict = defaultdict(list)
    for c in courses:
        s = (c.get("summary") or "").strip()
        if s:
            summary_to_courses[s].append(c)

    for summary_text, group in summary_to_courses.items():
        titles = set(c["title"] for c in group)
        if len(titles) <= 1:
            continue  # same title across multiple rows = intentional reuse

        # Exception lookup — skip the whole group if admin has reviewed
        # this text for this provider.
        if _summary_hash(summary_text) in summary_exceptions:
            logging.info(f"Skipping summary bleed — admin-reviewed exception exists (titles: {', '.join(list(titles)[:3])}…)")
            continue

        # Stable ordering: first course by id keeps its summary visible.
        # Every subsequent course in the group is auto-hidden.
        ordered = sorted(group, key=lambda x: x.get("id") or "")
        for c in ordered[1:]:
            if any_check_suppressed(c.get("title", ""), ["summary mismatch", "summary_bleed"]):
                continue
            other_titles = [t for t in titles if t != c["title"]]
            reason = f"summary_bleed: shares text with '{(other_titles[0] or '')[:80]}'"
            flag_course(c["id"], reason, auto_hidden, title=c.get("title", ""))

    return email_only


def check_prices(courses: list, auto_hidden: list, price_escalation_ids: set) -> list:
    """Check 2: Price sanity (Initiative 4 — active provider loop).

    One condition: zero/negative price auto-hides on first detection. Null
    price is ignored entirely — some listings legitimately omit a displayed
    price and the frontend renders gracefully. No median comparison.

    `price_escalation_ids` is the pre-computed set of course_ids (reconstructed
    from course_price_log rows 24+ hours old). Any zero/negative course in
    that set has its flag_reason upgraded to 'invalid_price_escalated' — the
    admin UI filters on that suffix to render the Flags tab Price escalations
    section.

    Priority stack per course:
      1. validator_suppressions (course_id-scoped Clears) matching
         'invalid_price' → skip the check.
      2. Zero/negative auto-hide + 24h escalation upgrade.
    """
    email_only: list = []

    for c in courses:
        cid = c.get("id") or ""
        title = c.get("title", "")

        # 1. Admin suppression — course-id-scoped Clears write here.
        if any_check_suppressed(title, ["invalid_price"], course_id=cid):
            continue

        price = c.get("price")
        if price is None or price > 0:
            continue

        # 2. Zero/negative — auto-hide + optional escalation.
        reason = "invalid_price_escalated" if cid in price_escalation_ids else "invalid_price"
        flag_course(cid, reason, auto_hidden, title=title)

    return email_only


def load_escalation_candidates(provider_id: str) -> set:
    """Initiative 5 — return the set of course_ids that have a log row in
    course_availability_log older than 24 hours. Any auto-flagged course in
    this set has been in the bad-date state for long enough to warrant a
    provider touchpoint, so its flag_reason is upgraded to the '_escalated'
    suffix and surfaced in the Flags tab.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    try:
        rows = sb_get("course_availability_log", {
            "provider_id": f"eq.{provider_id}",
            "scraped_at": f"lte.{cutoff}",
            "select": "course_id",
        })
    except Exception as e:
        logging.warning(f"load_escalation_candidates: {e}")
        return set()
    return {r["course_id"] for r in rows if r.get("course_id")}


def load_price_escalation_candidates(provider_id: str) -> set:
    """Initiative 4 — return the set of course_ids that have a log row in
    course_price_log older than 24 hours. Any zero/negative-priced course in
    this set has been in the bad-price state long enough to warrant a
    provider touchpoint, so check_prices upgrades flag_reason to
    'invalid_price_escalated'.

    Reconstructs course_ids from (provider_id, date_sort, title_hash) per V2
    id format: '{provider}-{date_sort or flex}-{title_hash}'. course_price_log
    has no course_id column — the V2 id is derivable from its grouping keys.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    try:
        rows = sb_get("course_price_log", {
            "provider_id": f"eq.{provider_id}",
            "logged_at": f"lte.{cutoff}",
            "select": "title_hash,date_sort",
        })
    except Exception as e:
        logging.warning(f"load_price_escalation_candidates: {e}")
        return set()
    ids: set = set()
    for r in rows:
        th = r.get("title_hash")
        if not th:
            continue
        ds = r.get("date_sort") or "flex"
        ids.add(f"{provider_id}-{ds}-{th}")
    return ids


def _build_session_index(courses: list) -> dict:
    """Group active session rows by `(provider_id, lower(title))` so the
    date-escalation check can look up siblings of a candidate row in O(1).

    Used by `_has_current_sibling` to suppress past_date_escalated /
    future_date_escalated upgrades on V2 stable-id orphans — the rows that
    accumulate when a provider transitions a course from "scheduled with
    dates" to "inquiry-only / flex-date". V2 ids encode `date_sort`, so the
    new flex row gets a different course_id and the old dated row stays in
    the DB forever; on date-passing it would otherwise escalate forever.
    """
    index: dict = {}
    for c in courses:
        title = (c.get("title") or "").strip().lower()
        if not title:
            continue
        key = (c.get("provider_id") or "", title)
        index.setdefault(key, []).append(c)
    return index


def _has_current_sibling(c: dict, session_index: dict, today: date) -> bool:
    """True if `c` has at least one active sibling row (same provider+title,
    different course_id) that is currently bookable. "Currently bookable"
    means active=True AND (`custom_dates=True` OR `date_sort >= today`).

    Returns False for courses with no sibling at all — those are genuine
    stale provider listings and should still escalate.
    """
    title = (c.get("title") or "").strip().lower()
    if not title:
        return False
    key = (c.get("provider_id") or "", title)
    siblings = session_index.get(key) or []
    cid = c.get("id")
    for s in siblings:
        if s.get("id") == cid:
            continue
        if s.get("active") is not True:
            continue
        if s.get("custom_dates"):
            return True
        sds = s.get("date_sort")
        if not sds:
            continue
        try:
            sd = datetime.strptime(sds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if sd >= today:
            return True
    return False


def check_dates(courses: list, auto_hidden: list, escalation_ids: set) -> list:
    """Check 4: Date sanity (Initiative 5 — active provider loop).

    Two symmetric conditions, both auto-hide on first detection:
      A. Past date with active=true    → flag_reason='past_date'
      B. Far future (>2 years out)     → flag_reason='future_date'

    `escalation_ids` is the pre-computed set of course_ids with at least one
    course_availability_log row from 24+ hours ago. Any course caught by
    condition A or B whose course_id is in that set has its flag_reason
    upgraded to the '_escalated' suffix — the admin UI filters on that
    suffix to render the Flags tab Date escalations section.

    Sibling-aware escalation guard: a row is kept at the non-escalated
    `past_date` / `future_date` flag (so it stays auto-hidden) but is NOT
    upgraded to `_escalated` when another active row for the same
    (provider_id, title) is currently bookable. This blocks the false-flag
    flood from V2-id orphans — when a provider switches a course from
    scheduled to flex, the old dated rows would otherwise escalate forever.

    Priority stack per course:
      1. custom_dates=true OR date_sort IS NULL → skip entirely. Flex-date
         and private-guiding rows by design; hard skip, not soft.
      2. validator_suppressions (course_id-scoped) matching 'past_date' /
         'future_date' → skip entire check for that course_id.
      3. Condition A / B automated checks → auto-hide, escalate only if
         aged AND no current sibling exists for the same title.
    """
    email_only: list = []
    today = date.today()
    two_years = today + timedelta(days=730)
    session_index = _build_session_index(courses)

    for c in courses:
        # 1. Hard skip for flex-date / private-guiding rows.
        if c.get("custom_dates"):
            continue
        ds = c.get("date_sort")
        if not ds:
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        cid = c.get("id") or ""
        title = c.get("title", "")

        # 2. Admin suppression — course-id-scoped Clears write here.
        if any_check_suppressed(title, ["past_date", "future_date"], course_id=cid):
            continue

        # 3. Condition A — Past date with active=true.
        if d < today and c.get("active") is True:
            should_escalate = (
                cid in escalation_ids
                and not _has_current_sibling(c, session_index, today)
            )
            reason = "past_date_escalated" if should_escalate else "past_date"
            flag_course(cid, reason, auto_hidden, title=title)
            continue

        # 3. Condition B — Far future (>2 years).
        if d > two_years:
            should_escalate = (
                cid in escalation_ids
                and not _has_current_sibling(c, session_index, today)
            )
            reason = "future_date_escalated" if should_escalate else "future_date"
            flag_course(cid, reason, auto_hidden, title=title)
            continue

    return email_only


def check_availability(courses: list, auto_hidden: list) -> list:
    """Check 5: Availability."""
    email_only = []

    for c in courses:
        if not c.get("avail"):
            email_only.append({
                "check": "Availability",
                "check_type": "null_avail",
                "title": c["title"],
                "issue": "avail is null",
                "value": "",
                "id": c["id"],
            })

    avail_values = [c.get("avail") for c in courses if c.get("avail")]
    if avail_values and all(v == "sold" for v in avail_values):
        email_only.append({
            "check": "Availability",
            "check_type": "all_sold",
            "title": "(all courses)",
            "issue": "CRITICAL: Every course shows avail='sold' — possible availability parsing error",
            "value": f"{len(avail_values)} courses all sold",
            "id": "",
        })

    return email_only


def check_duplicates(courses: list, auto_hidden: list) -> list:
    """Check 5: Duplicates — auto-hide all but first occurrence (Initiative 6).

    Pure scraper-signal check. Resolution is always "fix the scraper" — the
    Flags tab surfaces duplicate groups with a GitHub link to the offending
    scraper_{provider_id}.py file; admin does not make per-duplicate decisions.

    Priority stack per course:
      1. validator_suppressions matching 'duplicate' → skip entire check.
      2. Automated first-occurrence-wins duplicate detection.

    No whitelist layer — if two identical title+date courses are intentional,
    the scraper is wrong, not the data. `validator_whitelist` was retired in
    Initiative 6; existing rows are harmless and drop at V2 Phase 7.
    """
    email_only: list = []
    seen: dict = {}

    for c in courses:
        title = c.get("title") or ""
        # 1. Admin suppression — skip entire check.
        if any_check_suppressed(title, ["duplicate"]):
            seen[(c["title"], c.get("date_sort"))] = c["id"]
            continue

        # 2. Automated duplicate detection
        key = (c["title"], c.get("date_sort"))
        if key in seen:
            flag_course(c["id"], "duplicate: same title and date", auto_hidden, title=c.get("title", ""))
        else:
            seen[key] = c["id"]

    return email_only


# ── Email report ─────────────────────────────────────────────────────────────

def build_report_html(
    provider_name: str, provider_id: str, courses: list,
    auto_hidden: list, email_only: list,
    current_count: int, last_count,
    user_cleared: list, user_open: list,
) -> str:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total_issues = len(auto_hidden) + len(email_only)

    critical = [i for i in email_only if "CRITICAL" in i.get("issue", "")]
    non_critical = [i for i in email_only if "CRITICAL" not in i.get("issue", "")]

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;">
      <div style="background:#1a2e1a;padding:20px 28px;border-radius:10px 10px 0 0;">
        <p style="margin:0;font-size:18px;color:#fff;font-family:Georgia,serif;">
          backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
        </p>
      </div>
      <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e8e8e8;border-top:none;">
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">validation report</p>
        <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 8px;">{provider_name} ({provider_id})</h2>
        <p style="font-size:13px;color:#888;margin:0 0 4px;">Run at {timestamp}</p>
        <p style="font-size:13px;color:#888;margin:0 0 16px;">Courses checked: <strong>{len(courses)}</strong> · This run: <strong>{current_count}</strong>{f" · Last run: <strong>{last_count}</strong>" if last_count is not None else ""}</p>"""

    if total_issues == 0 and not user_cleared and not user_open:
        html += f"""
        <div style="background:#eaf3de;border:1px solid #c0dd97;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
          <p style="margin:0;font-size:14px;color:#2d6a11;font-weight:600;">All checks passed — {len(courses)} courses validated</p>
        </div>"""
    else:
        # Critical flags
        if critical:
            html += """
        <div style="background:#fde8e8;border:1px solid #f5c6cb;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
          <p style="margin:0 0 4px;font-size:11px;font-weight:700;text-transform:uppercase;color:#a32d2d;">Critical</p>"""
            for c in critical:
                html += f"""
          <p style="margin:0 0 6px;font-size:13px;color:#a32d2d;font-weight:700;">{c['issue']}</p>"""
            html += """
        </div>"""

        # Auto-hidden rows
        if auto_hidden:
            html += f"""
        <div style="margin-bottom:16px;">
          <p style="font-size:13px;font-weight:700;color:#a32d2d;margin:0 0 8px;border-bottom:1px solid #f5c6cb;padding-bottom:6px;">Auto-hidden ({len(auto_hidden)} row{"s" if len(auto_hidden) != 1 else ""})</p>
          <table style="width:100%;font-size:12px;border-collapse:collapse;">"""
            for h in auto_hidden[:30]:
                html += f"""
            <tr>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#333;max-width:220px;word-break:break-word;">{h.get('title', h['id'][:40])}</td>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#666;">{h['reason'][:80]}</td>
            </tr>"""
            if len(auto_hidden) > 30:
                html += f"""
            <tr><td colspan="2" style="padding:4px 8px;color:#999;font-style:italic;">… and {len(auto_hidden) - 30} more</td></tr>"""
            html += """
          </table>
        </div>"""

        # Email-only flags grouped by check
        if non_critical:
            grouped = defaultdict(list)
            for i in non_critical:
                grouped[i["check"]].append(i)

            for check_name, check_issues in grouped.items():
                html += f"""
        <div style="margin-bottom:16px;">
          <p style="font-size:13px;font-weight:700;color:#333;margin:0 0 8px;border-bottom:1px solid #eee;padding-bottom:6px;">{check_name} ({len(check_issues)} issue{"s" if len(check_issues) != 1 else ""})</p>
          <table style="width:100%;font-size:12px;border-collapse:collapse;">"""
                for i in check_issues[:20]:
                    html += f"""
            <tr>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#333;max-width:200px;word-break:break-word;">{i['title'][:60]}</td>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#666;">{i['issue']}</td>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#999;font-size:11px;">{i['value'][:40]}</td>
            </tr>"""
                if len(check_issues) > 20:
                    html += f"""
            <tr><td colspan="3" style="padding:4px 8px;color:#999;font-style:italic;">… and {len(check_issues) - 20} more</td></tr>"""
                html += """
          </table>
        </div>"""

    # User flags auto-resolved
    if user_cleared:
        html += f"""
        <div style="margin-bottom:16px;">
          <p style="font-size:13px;font-weight:700;color:#2d6a11;margin:0 0 8px;border-bottom:1px solid #c0dd97;padding-bottom:6px;">User flags auto-resolved ({len(user_cleared)})</p>
          <table style="width:100%;font-size:12px;border-collapse:collapse;">"""
        for uc in user_cleared[:20]:
            html += f"""
            <tr>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#333;">{uc['title'][:60]}</td>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#2d6a11;">{uc['reason']}</td>
            </tr>"""
        html += """
          </table>
        </div>"""

    # User flags still open
    if user_open:
        html += f"""
        <div style="margin-bottom:16px;">
          <p style="font-size:13px;font-weight:700;color:#854f0b;margin:0 0 8px;border-bottom:1px solid #faeeda;padding-bottom:6px;">User flags still open ({len(user_open)})</p>
          <table style="width:100%;font-size:12px;border-collapse:collapse;">"""
        for uo in user_open[:20]:
            note_cell = f" — {uo['note'][:40]}" if uo.get('note') else ""
            html += f"""
            <tr>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#333;">{uo['title'][:60]}</td>
              <td style="padding:4px 8px;border-bottom:1px solid #f5f5f5;color:#854f0b;">{uo['reason']}{note_cell}</td>
            </tr>"""
        html += """
          </table>
        </div>"""

    # Footer
    html += f"""
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Auto-hidden: {len(auto_hidden)} · Email-only: {len(email_only)} · User cleared: {len(user_cleared)} · User open: {len(user_open)}</p>"""
    if auto_hidden:
        html += """
        <p style="font-size:11px;color:#a32d2d;margin-top:8px;font-style:italic;">Auto-flagged rows are hidden from backcountryfinder.com until resolved.</p>"""
    html += """
      </div>
    </div>"""

    return html


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_provider.py <provider_id>")
        sys.exit(1)

    provider_id = sys.argv[1]
    print(f"── Validating provider: {provider_id} ──")

    # Fetch provider name
    providers = sb_get("providers", {"id": f"eq.{provider_id}", "select": "id,name"})
    provider_name = providers[0]["name"] if providers else provider_id

    # Reset auto_flagged for this provider (clean slate each run)
    print("  Resetting previous auto_flags...")
    try:
        reset_flags(provider_id)
    except Exception as e:
        print(f"  ⚠ Could not reset flags: {e}")

    # Clear previous validator_warnings for this provider (clean slate each run)
    print("  Resetting previous validator_warnings...")
    try:
        reset_warnings(provider_id)
    except Exception as e:
        print(f"  ⚠ Could not reset validator_warnings: {e}")

    # Fetch all courses for this provider
    courses = sb_get("courses", {
        "provider_id": f"eq.{provider_id}",
        "select": "id,provider_id,title,summary,price,date_sort,date_display,avail,active,spots_remaining,custom_dates,booking_url",
    })
    current_count = len(courses)
    print(f"  Fetched {current_count} courses")

    # Load admin suppressions (from "Clear all" in the Flags tab). A suppression
    # row prevents the validator from re-flagging the same (title, flag_reason
    # category) combo on subsequent runs.
    global _current_provider_id, _suppressions_cache
    _current_provider_id = provider_id
    suppression_rows = []
    try:
        suppression_rows = sb_get("validator_suppressions", {
            "select": "provider_id,title_contains,flag_reason,course_id",
            "or": f"(provider_id.eq.{provider_id},provider_id.is.null)",
        })
    except Exception as e:
        print(f"  ⚠ Could not load validator_suppressions: {e}")
    _suppressions_cache = suppression_rows
    logging.info(f"Loaded {len(_suppressions_cache)} admin suppressions")

    # Initiative 5 — load the set of course_ids with log rows 24+ hours old.
    # check_dates upgrades flag_reason to the '_escalated' suffix for any
    # auto-hidden course in this set, routing it to the Flags tab.
    escalation_ids = load_escalation_candidates(provider_id)
    logging.info(f"Loaded {len(escalation_ids)} escalation candidates (24h+)")

    # Initiative 4 — same pattern, sourced from course_price_log. Any
    # zero/negative-priced course with a price-log row 24h+ old escalates
    # to the Flags tab Price escalations section.
    price_escalation_ids = load_price_escalation_candidates(provider_id)
    logging.info(f"Loaded {len(price_escalation_ids)} price escalation candidates (24h+)")

    # Initiative 3 — load summary exceptions for this provider. Entries are
    # admin-saved summary hashes; the bleed check skips any group matching.
    summary_exception_rows = []
    try:
        summary_exception_rows = sb_get("validator_summary_exceptions", {
            "provider_id": f"eq.{provider_id}",
            "select": "summary_hash",
        })
    except Exception as e:
        print(f"  ⚠ Could not load validator_summary_exceptions: {e}")
    summary_exceptions = {r["summary_hash"] for r in summary_exception_rows if r.get("summary_hash")}
    logging.info(f"Loaded {len(summary_exceptions)} summary exceptions")

    # Initiative 3 — inline backfill for null/empty summaries. Patches
    # courses.summary + courses.search_document in place using a title-only
    # seed so the bleed check below sees the fresh text. Safety net, not
    # quality floor — admin reviews failures via the Summary Review tab.
    print("  Backfilling missing summaries inline…")
    try:
        still_missing = backfill_missing_summaries(courses, provider_id, provider_name)
        if still_missing:
            print(f"  ⚠ {len(still_missing)} courses still have no summary after backfill — surfaced in Summary Review tab")
    except Exception as e:
        print(f"  ⚠ backfill_missing_summaries failed: {e}")

    # Get last run count
    last_count = None
    try:
        prev_runs = sb_get("scraper_run_log", {
            "provider_id": f"eq.{provider_id}",
            "select": "course_count",
            "order": "run_at.desc",
            "limit": "1",
        })
        if prev_runs:
            last_count = prev_runs[0]["course_count"]
    except Exception:
        pass

    # Build a title lookup for auto-hidden report
    id_to_title = {c["id"]: c["title"] for c in courses}

    # Run all checks
    auto_hidden = []
    email_only = []

    email_only.extend(check_summaries(courses, auto_hidden, summary_exceptions))
    email_only.extend(check_prices(courses, auto_hidden, price_escalation_ids))
    email_only.extend(check_dates(courses, auto_hidden, escalation_ids))
    email_only.extend(check_availability(courses, auto_hidden))
    email_only.extend(check_duplicates(courses, auto_hidden))

    # Enrich auto_hidden with titles for the report
    for h in auto_hidden:
        h["title"] = id_to_title.get(h["id"], h["id"])

    # Auto-clear resolved user reports
    print("  Checking user flags for auto-clear...")
    user_cleared, user_open = auto_clear_user_flags(provider_id, courses)
    if user_cleared:
        print(f"  ✅ Auto-cleared {len(user_cleared)} user flags")
    if user_open:
        print(f"  ⚠ {len(user_open)} user flags still open")

    # Print results
    total = len(auto_hidden) + len(email_only)
    if total:
        print(f"  ⚠ {total} issues found ({len(auto_hidden)} auto-hidden, {len(email_only)} email-only):")
        for h in auto_hidden:
            print(f"    [AUTO-HIDE] {h['title'][:50]} — {h['reason']}")
        for i in email_only:
            print(f"    [{i['check']}] {i['title'][:50]} — {i['issue']}")
    else:
        print("  ✅ All checks passed")

    # Log this run
    try:
        sb_upsert("scraper_run_log", [{
            "provider_id": provider_id,
            "course_count": current_count,
        }])
    except Exception as e:
        print(f"  ⚠ Could not log run to scraper_run_log: {e}")

    # Write email-only warnings to validator_warnings table (replaces email report)
    try:
        write_warnings(provider_id, provider_name, email_only)
    except Exception as e:
        print(f"  ⚠ Could not write validator_warnings: {e}")

    print(f"── Validation complete: {len(auto_hidden)} hidden, {len(email_only)} warnings, {len(user_cleared)} user-cleared, {len(user_open)} user-open ──")


if __name__ == "__main__":
    main()
