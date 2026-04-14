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

import logging
import re
import sys
import statistics
from collections import defaultdict
from datetime import datetime, date, timedelta

import requests

from scraper_utils import (
    sb_get, sb_upsert, sb_patch, send_email,
    SUPABASE_URL, SUPABASE_KEY,
)

# ── Constants ────────────────────────────────────────────────────────────────

ACTIVITY_CONTRADICTIONS = {
    "climbing": ["splitboard", "ski touring", "ski resort", "ski lift", "backcountry skiing", "downhill ski"],
    "skiing":   ["rock climbing", "ice climbing", "trad climbing", "sport climbing"],
    "hiking":   ["splitboard", "ski touring", "rock climbing", "ice climbing"],
}

# Order matters — first match wins. More specific rules must come before generic ones.
# Titles containing these phrases accept EITHER activity without flagging:
TITLE_ACTIVITY_EXCEPTIONS = ["ski mountaineering"]

TITLE_ACTIVITY_RULES = [
    (["alpine climbing", "alpine rock"],                     "mountaineering"),
    (["rock climbing", "ice climbing"],                      "climbing"),
    (["ski traverse", "ski touring", "splitboard", "backcountry ski"], "skiing"),
    (["hik"],                                                "hiking"),
    (["mountaineer"],                                        "mountaineering"),
]

# These keywords use word-boundary matching (re.search with \b) to avoid false positives
TITLE_ACTIVITY_RULES_WORD_BOUNDARY = [
    (r"\bski\b",     "skiing"),
    (r"\balpine\b",  "mountaineering"),
]


# ── Flag helpers ─────────────────────────────────────────────────────────────

def flag_course(course_id: str, reason: str, auto_hidden: list):
    """Patch a single course row as auto_flagged and record it."""
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
        "select": "id,title,flagged_reason,flagged_note,price,date_sort,avail,summary,activity,activity_canonical",
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

        elif reason == "bad_description":
            summary = (fc.get("summary") or "").strip()
            activity = fc.get("activity") or fc.get("activity_canonical") or ""
            if summary:
                # Check no contradiction
                contradictions = ACTIVITY_CONTRADICTIONS.get(activity, [])
                has_contradiction = any(kw in summary.lower() for kw in contradictions)
                if not has_contradiction:
                    can_clear = True

        # button_broken and other → never auto-clear

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

def check_summaries(courses: list, auto_hidden: list) -> list:
    """Check 1: Summary quality."""
    email_only = []

    # Empty summaries → EMAIL ONLY
    for c in courses:
        if not c.get("summary"):
            email_only.append({
                "check": "Summary quality",
                "check_type": "summary_empty",
                "title": c["title"],
                "issue": "Summary is empty or null",
                "value": "",
                "id": c["id"],
            })

    # Cross-activity contradiction → AUTO-HIDE
    for c in courses:
        summary = (c.get("summary") or "").lower()
        activity = c.get("activity") or c.get("activity_canonical") or ""
        if not summary or not activity:
            continue
        contradictions = ACTIVITY_CONTRADICTIONS.get(activity, [])
        for kw in contradictions:
            if kw in summary:
                reason = f"summary mismatch: '{kw}' on {activity} course"
                flag_course(c["id"], reason, auto_hidden)
                break

    # Duplicate summaries across different titles → EMAIL ONLY
    # Without the source description stored in Supabase, the validator cannot
    # reliably distinguish intentional shared summaries from genuine bleed.
    summary_to_courses = defaultdict(list)
    for c in courses:
        s = (c.get("summary") or "").strip()
        if s:
            summary_to_courses[s].append(c)
    for summary_text, group in summary_to_courses.items():
        titles = set(c["title"] for c in group)
        if len(titles) <= 1:
            continue  # same title — intentional reuse
        for c in group:
            other_titles = [t for t in titles if t != c["title"]]
            email_only.append({
                "check": "Summary quality",
                "check_type": "summary_empty",
                "title": c["title"],
                "issue": f"Possible summary bleed: shared with {other_titles[0][:60]}",
                "value": summary_text[:80],
                "id": c["id"],
            })

    return email_only


def check_activities(courses: list, auto_hidden: list) -> list:
    """Check 2: Activity mapping."""
    email_only = []

    for c in courses:
        activity = c.get("activity") or c.get("activity_canonical") or ""
        title_lower = c["title"].lower()

        # Null activity → AUTO-HIDE
        if not activity:
            flag_course(c["id"], "null activity", auto_hidden)
            continue

        # Skip titles that legitimately span two activity types
        if any(exc in title_lower for exc in TITLE_ACTIVITY_EXCEPTIONS):
            continue

        # Title/activity mismatch → AUTO-HIDE
        matched = False
        for keywords, expected in TITLE_ACTIVITY_RULES:
            if any(kw in title_lower for kw in keywords):
                if activity != expected:
                    reason = f"activity mismatch: title suggests {expected} but got {activity}"
                    flag_course(c["id"], reason, auto_hidden)
                matched = True
                break

        if not matched:
            for pattern, expected in TITLE_ACTIVITY_RULES_WORD_BOUNDARY:
                if re.search(pattern, title_lower):
                    if activity != expected:
                        reason = f"activity mismatch: title suggests {expected} but got {activity}"
                        flag_course(c["id"], reason, auto_hidden)
                    break

    return email_only


def check_prices(courses: list, auto_hidden: list) -> list:
    """Check 3: Price sanity."""
    email_only = []
    prices = [c["price"] for c in courses if c.get("price") and c["price"] > 0]
    has_prices = len(prices) > 0
    median_price = statistics.median(prices) if prices else 0

    for c in courses:
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
            flag_course(c["id"], f"invalid price: {price}", auto_hidden)
        elif price is not None and median_price > 0 and price > median_price * 5:
            title_lower = c["title"].lower()
            if any(kw in title_lower for kw in ("logan", "expedition", "traverse")):
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
    """Check 4: Date sanity."""
    email_only = []
    today = date.today()
    two_years = today + timedelta(days=730)

    for c in courses:
        ds = c.get("date_sort")
        if not ds:
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        if d < today and c.get("active") is True:
            flag_course(c["id"], "past date still active", auto_hidden)

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

    Skips titles present in validator_whitelist for this provider (or globally).
    """
    email_only = []
    seen = {}

    for c in courses:
        title = c.get("title") or ""
        title_key = title.strip().lower()
        if (title_key, provider_id) in whitelisted or (title_key, None) in whitelisted:
            if (c["title"], c.get("date_sort")) in seen:
                logging.info(f"Skipping whitelisted duplicate: {title} ({provider_id})")
            seen[(c["title"], c.get("date_sort"))] = c["id"]
            continue
        key = (c["title"], c.get("date_sort"))
        if key in seen:
            flag_course(c["id"], "duplicate: same title and date", auto_hidden)
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
        "select": "id,title,activity,activity_canonical,summary,price,date_sort,date_display,avail,active,spots_remaining",
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

    email_only.extend(check_summaries(courses, auto_hidden))
    email_only.extend(check_activities(courses, auto_hidden))
    email_only.extend(check_prices(courses, auto_hidden))
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
        write_warnings(provider_id, provider_name, email_only)
    except Exception as e:
        print(f"  ⚠ Could not write validator_warnings: {e}")

    print(f"── Validation complete: {len(auto_hidden)} hidden, {len(email_only)} warnings, {len(user_cleared)} user-cleared, {len(user_open)} user-open ──")


if __name__ == "__main__":
    main()
