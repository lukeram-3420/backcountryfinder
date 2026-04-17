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
import re
import sys
import statistics
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


def is_suppressed(title: str, reason: str) -> bool:
    """True if an admin 'Clear all' suppression exists for this title+reason.

    Matches when:
      - suppression.provider_id is null (global) OR matches current provider
      - suppression.title_contains (lowercased) is a substring of title
      - the proposed reason's category matches the suppression's reason category
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
        s_tc = (s.get("title_contains") or "").strip().lower()
        if not s_tc or s_tc not in t:
            continue
        if _reason_category(s.get("flag_reason") or "") == proposed_cat:
            return True
    return False


def any_check_suppressed(title: str, categories) -> bool:
    """True if an admin suppression matches this title for ANY of the given
    flag-reason categories. Used at the top of each check's per-course loop
    so admin decisions short-circuit the whole check — not just the final
    flag_course() write.
    """
    for cat in categories:
        if is_suppressed(title, cat):
            return True
    return False


def flag_course(course_id: str, reason: str, auto_hidden: list, title: str = ""):
    """Patch a single course row as auto_flagged and record it — unless the
    (title, reason-category) combo has been suppressed via admin 'Clear all'.
    """
    if title and is_suppressed(title, reason):
        logging.info(f"Suppressed flag (admin-cleared): {title!r} / {reason!r}")
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


def is_price_exception(title: str, provider_id: str, exceptions: list) -> bool:
    """Return True if the title matches a validator_price_exceptions entry
    for this provider (or globally when exc_provider is None)."""
    title_lower = (title or "").strip().lower()
    for (contains, exc_provider) in exceptions:
        if contains and contains in title_lower:
            if exc_provider is None or exc_provider == provider_id:
                return True
    return False


def reset_warnings(provider_id: str):
    """Delete existing validator_warnings rows for this provider (clean slate)."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/validator_warnings?provider_id=eq.{provider_id}"
    resp = requests.delete(url, headers=headers, timeout=30)
    resp.raise_for_status()


def write_warnings(provider_id: str, provider_name: str, email_only: list, price_exceptions: list):
    """Write collected email-only warnings to validator_warnings table.

    Price outlier warnings matching a validator_price_exceptions entry are
    filtered out before writing.
    """
    if not email_only:
        return
    rows = []
    for i in email_only:
        check_type = i.get("check_type")
        if not check_type:
            continue
        if check_type == "price_outlier" and is_price_exception(i.get("title", ""), provider_id, price_exceptions):
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

    # Build lookup for median price
    prices = [c["price"] for c in courses if c.get("price") and c["price"] > 0]
    median_price = statistics.median(prices) if prices else 0

    cleared = []
    still_open = []

    for fc in flagged_courses:
        reason = fc.get("flagged_reason") or ""
        can_clear = False

        if reason == "wrong_price":
            price = fc.get("price")
            if price is not None and price > 0 and (median_price == 0 or price <= median_price * 5):
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


def check_prices(courses: list, auto_hidden: list, price_exceptions: list, provider_id: str) -> list:
    """Check 3: Price sanity.

    Priority stack per course:
      1. validator_suppressions matching 'invalid price' → skip entire check.
      2. validator_price_exceptions → skip the >5x median outlier warning
         (runs just before the outlier branch, below).
      3. Automated null-price / zero-or-negative / outlier checks.
    """
    email_only = []
    prices = [c["price"] for c in courses if c.get("price") and c["price"] > 0]
    has_prices = len(prices) > 0
    median_price = statistics.median(prices) if prices else 0

    for c in courses:
        # 1. Admin suppression — skip entire check.
        if any_check_suppressed(c.get("title", ""), ["invalid price"]):
            continue

        price = c.get("price")

        if price is None and has_prices:
            email_only.append({
                "check": "Price sanity",
                "check_type": "null_price",
                "title": c["title"],
                "issue": "Price is null (other courses have prices)",
                "value": "null",
                "id": c["id"],
            })
        elif price is not None and price <= 0:
            flag_course(c["id"], f"invalid price: {price}", auto_hidden, title=c.get("title",""))
        elif price is not None and median_price > 0 and price > median_price * 5:
            title_lower = c["title"].lower()
            if any(kw in title_lower for kw in ("logan", "expedition", "traverse")):
                continue
            if is_price_exception(c["title"], provider_id, price_exceptions):
                logging.info(f"Skipping price exception: {c['title']}")
                continue
            email_only.append({
                "check": "Price sanity",
                "check_type": "price_outlier",
                "title": c["title"],
                "issue": f"Price ${price} is >5x median (${median_price:.0f})",
                "value": str(price),
                "id": c["id"],
            })

    return email_only


def check_dates(courses: list, auto_hidden: list) -> list:
    """Check 4: Date sanity.

    Priority stack per course:
      1. validator_suppressions matching 'past date still active'
         → skip entire check.
      2. Automated past-date auto-hide + future-date warning.
    """
    email_only = []
    today = date.today()
    two_years = today + timedelta(days=730)

    for c in courses:
        if any_check_suppressed(c.get("title", ""), ["past date still active"]):
            continue
        ds = c.get("date_sort")
        if not ds:
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        if d < today and c.get("active") is True:
            flag_course(c["id"], "past date still active", auto_hidden, title=c.get("title",""))

        if d > two_years:
            email_only.append({
                "check": "Date sanity",
                "check_type": "future_date",
                "title": c["title"],
                "issue": f"Date {ds} is more than 2 years in the future",
                "value": ds,
                "id": c["id"],
            })

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


def check_duplicates(courses: list, auto_hidden: list, whitelisted: set, provider_id: str) -> list:
    """Check 6: Duplicates — auto-hide all but first occurrence.

    Priority stack per course:
      1. validator_suppressions matching 'duplicate' → skip entire check.
      2. validator_whitelist → title known-safe for this provider (or global)
         → skip, but still record the (title,date) in `seen` so other rows
         with the same title+date also skip.
      3. Automated first-occurrence-wins duplicate detection.
    """
    email_only = []
    seen = {}

    for c in courses:
        title = c.get("title") or ""
        # 1. Admin suppression — skip entire check.
        if any_check_suppressed(title, ["duplicate"]):
            seen[(c["title"], c.get("date_sort"))] = c["id"]
            continue

        title_key = title.strip().lower()
        # 2. Whitelist
        if (title_key, provider_id) in whitelisted or (title_key, None) in whitelisted:
            if (c["title"], c.get("date_sort")) in seen:
                logging.info(f"Skipping whitelisted duplicate: {title} ({provider_id})")
            seen[(c["title"], c.get("date_sort"))] = c["id"]
            continue

        # 3. Automated duplicate detection
        key = (c["title"], c.get("date_sort"))
        if key in seen:
            flag_course(c["id"], "duplicate: same title and date", auto_hidden, title=c.get("title",""))
        else:
            seen[key] = c["id"]

    return email_only


def check_course_count(provider_id: str, current_count: int) -> list:
    """Check 7: Course count vs previous run → EMAIL ONLY."""
    email_only = []

    try:
        prev_runs = sb_get("scraper_run_log", {
            "provider_id": f"eq.{provider_id}",
            "select": "course_count,run_at",
            "order": "run_at.desc",
            "limit": "1",
        })
    except Exception:
        prev_runs = []

    if prev_runs:
        last_count = prev_runs[0]["course_count"]
        if last_count > 0 and current_count < last_count * 0.7:
            drop_pct = ((last_count - current_count) / last_count) * 100
            email_only.append({
                "check": "Course count",
                "check_type": "count_drop",
                "title": "(provider-level)",
                "issue": f"CRITICAL: Course count dropped {drop_pct:.0f}% ({last_count} → {current_count})",
                "value": f"{last_count} → {current_count}",
                "id": "",
            })

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
        "select": "id,title,summary,price,date_sort,date_display,avail,active,spots_remaining",
    })
    current_count = len(courses)
    print(f"  Fetched {current_count} courses")

    # Load duplicate whitelist
    whitelist_rows = []
    try:
        whitelist_rows = sb_get("validator_whitelist", {"select": "title,provider_id"})
    except Exception as e:
        print(f"  ⚠ Could not load validator_whitelist: {e}")
    logging.info(f"Loaded {len(whitelist_rows)} whitelist entries")
    whitelisted = set()
    for row in whitelist_rows:
        title = (row.get("title") or "").strip().lower()
        pid = row.get("provider_id")
        whitelisted.add((title, pid if pid else None))

    # Load price exceptions
    exception_rows = []
    try:
        exception_rows = sb_get("validator_price_exceptions", {"select": "title_contains,provider_id"})
    except Exception as e:
        print(f"  ⚠ Could not load validator_price_exceptions: {e}")
    price_exceptions = [
        ((r.get("title_contains") or "").strip().lower(), r.get("provider_id"))
        for r in exception_rows
    ]
    logging.info(f"Loaded {len(price_exceptions)} price exceptions")

    # Load admin suppressions (from "Clear all" in the Flags tab). A suppression
    # row prevents the validator from re-flagging the same (title, flag_reason
    # category) combo on subsequent runs.
    global _current_provider_id, _suppressions_cache
    _current_provider_id = provider_id
    suppression_rows = []
    try:
        suppression_rows = sb_get("validator_suppressions", {
            "select": "provider_id,title_contains,flag_reason",
            "or": f"(provider_id.eq.{provider_id},provider_id.is.null)",
        })
    except Exception as e:
        print(f"  ⚠ Could not load validator_suppressions: {e}")
    _suppressions_cache = suppression_rows
    logging.info(f"Loaded {len(_suppressions_cache)} admin suppressions")

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
    email_only.extend(check_prices(courses, auto_hidden, price_exceptions, provider_id))
    email_only.extend(check_dates(courses, auto_hidden))
    email_only.extend(check_availability(courses, auto_hidden))
    email_only.extend(check_duplicates(courses, auto_hidden, whitelisted, provider_id))
    email_only.extend(check_course_count(provider_id, current_count))

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
        write_warnings(provider_id, provider_name, email_only, price_exceptions)
    except Exception as e:
        print(f"  ⚠ Could not write validator_warnings: {e}")

    print(f"── Validation complete: {len(auto_hidden)} hidden, {len(email_only)} warnings, {len(user_cleared)} user-cleared, {len(user_open)} user-open ──")


if __name__ == "__main__":
    main()
