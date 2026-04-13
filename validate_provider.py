#!/usr/bin/env python3
"""
validate_provider.py — Post-scrape validation for a single provider.

Queries Supabase for the provider's courses, runs data quality checks,
auto-hides bad rows (flagged=true), and emails a report.

Usage:
    python validate_provider.py <provider_id>
"""

import re
import sys
import statistics
from collections import defaultdict
from datetime import datetime, date, timedelta

from scraper_utils import sb_get, sb_upsert, sb_patch, send_email

# ── Constants ────────────────────────────────────────────────────────────────

ACTIVITY_CONTRADICTIONS = {
    "climbing": ["splitboard", "ski touring", "ski resort", "ski lift", "backcountry skiing", "downhill ski"],
    "skiing":   ["rock climbing", "ice climbing", "trad climbing", "sport climbing"],
    "hiking":   ["splitboard", "ski touring", "rock climbing", "ice climbing"],
}

# Order matters — first match wins. More specific rules must come before generic ones.
TITLE_ACTIVITY_RULES = [
    (["rock climbing", "ice climbing", "alpine climbing"],  "climbing"),
    (["splitboard", "backcountry ski"],                      "skiing"),
    (["hik"],                                                "hiking"),
    (["mountaineer"],                                        "mountaineering"),
]

# These keywords use word-boundary matching (re.search with \b) to avoid false positives
# e.g. "ski" must not match "Skills"
TITLE_ACTIVITY_RULES_WORD_BOUNDARY = [
    (r"\bski\b",     "skiing"),
    (r"\balpine\b",  "mountaineering"),
]


# ── Flag helpers ─────────────────────────────────────────────────────────────

def flag_course(course_id: str, reason: str, auto_hidden: list):
    """Patch a single course row as flagged and record it."""
    sb_patch("courses", f"id=eq.{course_id}", {
        "flagged": True,
        "flagged_reason": reason[:200],
    })
    auto_hidden.append({"id": course_id, "reason": reason})


def reset_flags(provider_id: str):
    """Clear all flags for this provider before re-validating."""
    sb_patch(
        "courses",
        f"provider_id=eq.{provider_id}&flagged=eq.true",
        {"flagged": False, "flagged_reason": None},
    )


# ── Checks ───────────────────────────────────────────────────────────────────

def check_summaries(courses: list, auto_hidden: list) -> list:
    """Check 1: Summary quality."""
    email_only = []

    # Empty summaries → EMAIL ONLY
    for c in courses:
        if not c.get("summary"):
            email_only.append({
                "check": "Summary quality",
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

    # Duplicate summaries across different titles → AUTO-HIDE
    summary_to_courses = defaultdict(list)
    for c in courses:
        s = (c.get("summary") or "").strip()
        if s:
            summary_to_courses[s].append(c)
    for summary_text, group in summary_to_courses.items():
        titles = set(c["title"] for c in group)
        if len(titles) > 1:
            for c in group:
                other_titles = [t for t in titles if t != c["title"]]
                reason = f"duplicate summary bleed: shared with {other_titles[0][:60]}"
                flag_course(c["id"], reason, auto_hidden)

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

        # Title/activity mismatch → AUTO-HIDE
        # Pass 1: substring rules (multi-word phrases, checked first)
        matched = False
        for keywords, expected in TITLE_ACTIVITY_RULES:
            if any(kw in title_lower for kw in keywords):
                if activity != expected:
                    reason = f"activity mismatch: title suggests {expected} but got {activity}"
                    flag_course(c["id"], reason, auto_hidden)
                matched = True
                break

        # Pass 2: word-boundary rules (single words that need \b to avoid false positives)
        if not matched:
            for pattern, expected in TITLE_ACTIVITY_RULES_WORD_BOUNDARY:
                if re.search(pattern, title_lower):
                    # "Alpine Climbing:" titles already matched climbing above — skip alpine→mountaineering
                    if expected == "mountaineering" and "alpine climbing" in title_lower:
                        break
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

        # Null price → EMAIL ONLY
        if price is None and has_prices:
            email_only.append({
                "check": "Price sanity",
                "title": c["title"],
                "issue": "Price is null (other courses have prices)",
                "value": "null",
                "id": c["id"],
            })
        # Zero or negative → AUTO-HIDE
        elif price is not None and price <= 0:
            flag_course(c["id"], f"invalid price: {price}", auto_hidden)
        # Outlier → EMAIL ONLY (skip known expensive courses)
        elif price is not None and median_price > 0 and price > median_price * 5:
            title_lower = c["title"].lower()
            if any(kw in title_lower for kw in ("logan", "expedition", "traverse")):
                continue  # known edge case — legitimately expensive
            email_only.append({
                "check": "Price sanity",
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

        # Past date still active → AUTO-HIDE
        if d < today and c.get("active") is True:
            flag_course(c["id"], "past date still active", auto_hidden)

        # Far future → EMAIL ONLY
        if d > two_years:
            email_only.append({
                "check": "Date sanity",
                "title": c["title"],
                "issue": f"Date {ds} is more than 2 years in the future",
                "value": ds,
                "id": c["id"],
            })

    return email_only


def check_availability(courses: list, auto_hidden: list) -> list:
    """Check 5: Availability."""
    email_only = []

    # Null avail → EMAIL ONLY
    for c in courses:
        if not c.get("avail"):
            email_only.append({
                "check": "Availability",
                "title": c["title"],
                "issue": "avail is null",
                "value": "",
                "id": c["id"],
            })

    # All sold → EMAIL ONLY warning (do not hide any rows)
    avail_values = [c.get("avail") for c in courses if c.get("avail")]
    if avail_values and all(v == "sold" for v in avail_values):
        email_only.append({
            "check": "Availability",
            "title": "(all courses)",
            "issue": "CRITICAL: Every course shows avail='sold' — possible availability parsing error",
            "value": f"{len(avail_values)} courses all sold",
            "id": "",
        })

    return email_only


def check_duplicates(courses: list, auto_hidden: list) -> list:
    """Check 6: Duplicates — auto-hide all but first occurrence."""
    email_only = []
    seen = {}  # (title, date_sort) → first course id

    for c in courses:
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
) -> str:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total_issues = len(auto_hidden) + len(email_only)

    # Separate critical email-only issues
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

    if total_issues == 0:
        html += f"""
        <div style="background:#eaf3de;border:1px solid #c0dd97;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
          <p style="margin:0;font-size:14px;color:#2d6a11;font-weight:600;">All checks passed — {len(courses)} courses validated</p>
        </div>"""
    else:
        # Section 1: Critical flags
        if critical:
            html += """
        <div style="background:#fde8e8;border:1px solid #f5c6cb;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
          <p style="margin:0 0 4px;font-size:11px;font-weight:700;text-transform:uppercase;color:#a32d2d;">Critical</p>"""
            for c in critical:
                html += f"""
          <p style="margin:0 0 6px;font-size:13px;color:#a32d2d;font-weight:700;">{c['issue']}</p>"""
            html += """
        </div>"""

        # Section 2: Auto-hidden rows
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

        # Section 3: Email-only flags grouped by check
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

    # Footer
    html += f"""
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Auto-hidden: {len(auto_hidden)} · Email-only: {len(email_only)} · Total: {total_issues}</p>"""
    if auto_hidden:
        html += """
        <p style="font-size:11px;color:#a32d2d;margin-top:8px;font-style:italic;">Flagged rows are hidden from backcountryfinder.com until resolved.</p>"""
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

    # Reset all flags for this provider (clean slate each run)
    print("  Resetting previous flags...")
    try:
        reset_flags(provider_id)
    except Exception as e:
        print(f"  ⚠ Could not reset flags: {e}")

    # Fetch all courses for this provider
    courses = sb_get("courses", {
        "provider_id": f"eq.{provider_id}",
        "select": "id,title,activity,activity_canonical,summary,price,date_sort,date_display,avail,active,spots_remaining",
    })
    current_count = len(courses)
    print(f"  Fetched {current_count} courses")

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
    auto_hidden = []  # list of {"id": ..., "reason": ...}
    email_only = []

    email_only.extend(check_summaries(courses, auto_hidden))
    email_only.extend(check_activities(courses, auto_hidden))
    email_only.extend(check_prices(courses, auto_hidden))
    email_only.extend(check_dates(courses, auto_hidden))
    email_only.extend(check_availability(courses, auto_hidden))
    email_only.extend(check_duplicates(courses, auto_hidden))
    email_only.extend(check_course_count(provider_id, current_count))

    # Enrich auto_hidden with titles for the report
    for h in auto_hidden:
        h["title"] = id_to_title.get(h["id"], h["id"])

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

    # Send email
    emoji = "✅" if total == 0 else "⚠️"
    subject = f"{emoji} Validation {'passed' if total == 0 else 'issues'} — {provider_name}"
    html = build_report_html(
        provider_name, provider_id, courses,
        auto_hidden, email_only,
        current_count, last_count,
    )
    send_email(subject, html)

    print(f"── Validation complete: {len(auto_hidden)} hidden, {len(email_only)} email-only ──")


if __name__ == "__main__":
    main()
