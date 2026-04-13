#!/usr/bin/env python3
"""
validate_provider.py — Post-scrape validation for a single provider.

Queries Supabase for the provider's courses, runs data quality checks,
and emails a report. Read-only — never modifies course data.

Usage:
    python validate_provider.py <provider_id>
"""

import sys
import statistics
from collections import defaultdict
from datetime import datetime, date, timedelta

from scraper_utils import sb_get, sb_upsert, send_email

# ── Activity contradiction keywords ─────────────────────────────────────────

ACTIVITY_CONTRADICTIONS = {
    "climbing": ["ski", "splitboard", "powder", "backcountry ski", "ski tour"],
    "skiing":   ["climb", "rock climb", "belay", "rappel", "multi-pitch"],
    "hiking":   ["ski", "climb", "splitboard"],
}

TITLE_ACTIVITY_RULES = [
    (["rock climbing", "ice climbing"],              "climbing"),
    (["ski", "splitboard", "backcountry ski"],        "skiing"),
    (["hik"],                                         "hiking"),
    (["alpine", "mountaineer"],                       "mountaineering"),
]


# ── Checks ───────────────────────────────────────────────────────────────────

def check_summaries(courses: list) -> list:
    """Check 1: Summary quality."""
    issues = []
    # Empty summaries
    for c in courses:
        if not c.get("summary"):
            issues.append({
                "check": "Summary quality",
                "title": c["title"],
                "issue": "Summary is empty or null",
                "value": "",
            })

    # Cross-activity contradiction
    for c in courses:
        summary = (c.get("summary") or "").lower()
        activity = c.get("activity") or c.get("activity_canonical") or ""
        if not summary or not activity:
            continue
        contradictions = ACTIVITY_CONTRADICTIONS.get(activity, [])
        for kw in contradictions:
            if kw in summary:
                issues.append({
                    "check": "Summary quality",
                    "title": c["title"],
                    "issue": f"Summary contains '{kw}' but activity is '{activity}'",
                    "value": c["summary"][:80],
                })
                break

    # Duplicate summaries across different titles
    summary_to_titles = defaultdict(set)
    for c in courses:
        s = (c.get("summary") or "").strip()
        if s:
            summary_to_titles[s].add(c["title"])
    for summary, titles in summary_to_titles.items():
        if len(titles) > 1:
            for t in titles:
                issues.append({
                    "check": "Summary quality",
                    "title": t,
                    "issue": f"Identical summary shared with {len(titles)-1} other title(s)",
                    "value": summary[:80],
                })

    return issues


def check_activities(courses: list) -> list:
    """Check 2: Activity mapping."""
    issues = []
    for c in courses:
        activity = c.get("activity") or c.get("activity_canonical") or ""
        title_lower = c["title"].lower()

        if not activity:
            issues.append({
                "check": "Activity mapping",
                "title": c["title"],
                "issue": "Activity is null",
                "value": "",
            })
            continue

        for keywords, expected in TITLE_ACTIVITY_RULES:
            if any(kw in title_lower for kw in keywords):
                if activity != expected:
                    issues.append({
                        "check": "Activity mapping",
                        "title": c["title"],
                        "issue": f"Title suggests '{expected}' but activity is '{activity}'",
                        "value": activity,
                    })
                break

    return issues


def check_prices(courses: list) -> list:
    """Check 3: Price sanity."""
    issues = []
    prices = [c["price"] for c in courses if c.get("price") and c["price"] > 0]
    has_prices = len(prices) > 0
    median_price = statistics.median(prices) if prices else 0

    for c in courses:
        price = c.get("price")

        if price is None and has_prices:
            issues.append({
                "check": "Price sanity",
                "title": c["title"],
                "issue": "Price is null (other courses have prices)",
                "value": "null",
            })
        elif price is not None and price <= 0:
            issues.append({
                "check": "Price sanity",
                "title": c["title"],
                "issue": f"Price is {price} (zero or negative)",
                "value": str(price),
            })
        elif price is not None and median_price > 0 and price > median_price * 5:
            issues.append({
                "check": "Price sanity",
                "title": c["title"],
                "issue": f"Price ${price} is >5x median (${median_price:.0f})",
                "value": str(price),
            })

    return issues


def check_dates(courses: list) -> list:
    """Check 4: Date sanity."""
    issues = []
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
            issues.append({
                "check": "Date sanity",
                "title": c["title"],
                "issue": f"Past date {ds} but active=true",
                "value": ds,
            })

        if d > two_years:
            issues.append({
                "check": "Date sanity",
                "title": c["title"],
                "issue": f"Date {ds} is more than 2 years in the future",
                "value": ds,
            })

    return issues


def check_availability(courses: list) -> list:
    """Check 5: Availability."""
    issues = []

    for c in courses:
        if not c.get("avail"):
            issues.append({
                "check": "Availability",
                "title": c["title"],
                "issue": "avail is null",
                "value": "",
            })

    avail_values = [c.get("avail") for c in courses if c.get("avail")]
    if avail_values and all(v == "sold" for v in avail_values):
        issues.append({
            "check": "Availability",
            "title": "(all courses)",
            "issue": "CRITICAL: Every course shows avail='sold' — likely parsing error",
            "value": f"{len(avail_values)} courses all sold",
        })

    return issues


def check_duplicates(courses: list) -> list:
    """Check 6: Duplicates."""
    issues = []
    seen = defaultdict(int)
    for c in courses:
        key = (c["title"], c.get("date_sort"))
        seen[key] += 1

    for (title, date_sort), count in seen.items():
        if count > 1:
            issues.append({
                "check": "Duplicates",
                "title": title,
                "issue": f"title + date_sort appears {count} times",
                "value": date_sort or "(no date)",
            })

    return issues


def check_course_count(provider_id: str, current_count: int) -> list:
    """Check 7: Course count vs previous run."""
    issues = []

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
            issues.append({
                "check": "Course count",
                "title": "(provider-level)",
                "issue": f"CRITICAL: Course count dropped {drop_pct:.0f}% ({last_count} → {current_count})",
                "value": f"{last_count} → {current_count}",
            })

    return issues


# ── Email report ─────────────────────────────────────────────────────────────

def build_report_html(provider_name: str, provider_id: str, courses: list,
                      issues: list, current_count: int, last_count: int | None) -> str:
    """Build the HTML email body."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Separate critical issues
    critical = [i for i in issues if "CRITICAL" in i.get("issue", "")]
    non_critical = [i for i in issues if "CRITICAL" not in i.get("issue", "")]

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
        <p style="font-size:13px;color:#888;margin:0 0 16px;">Courses checked: <strong>{len(courses)}</strong> · Course count this run: <strong>{current_count}</strong>{f" (last run: {last_count})" if last_count is not None else ""}</p>"""

    if not issues:
        html += """
        <div style="background:#eaf3de;border:1px solid #c0dd97;border-radius:8px;padding:14px 18px;margin-bottom:16px;">
          <p style="margin:0;font-size:14px;color:#2d6a11;font-weight:600;">✅ All checks passed</p>
        </div>"""
    else:
        # Critical issues first
        if critical:
            html += """
        <div style="background:#fde8e8;border:1px solid #f5c6cb;border-radius:8px;padding:14px 18px;margin-bottom:16px;">"""
            for c in critical:
                html += f"""
          <p style="margin:0 0 6px;font-size:13px;color:#a32d2d;font-weight:700;">{c['issue']}</p>"""
            html += """
        </div>"""

        # Group non-critical by check type
        grouped = defaultdict(list)
        for i in non_critical:
            grouped[i["check"]].append(i)

        for check_name, check_issues in grouped.items():
            html += f"""
        <div style="margin-bottom:16px;">
          <p style="font-size:13px;font-weight:700;color:#333;margin:0 0 8px;border-bottom:1px solid #eee;padding-bottom:6px;">{check_name} ({len(check_issues)} issue{"s" if len(check_issues) != 1 else ""})</p>
          <table style="width:100%;font-size:12px;border-collapse:collapse;">"""
            for i in check_issues[:20]:  # cap at 20 per group
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

    html += f"""
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Total issues: {len(issues)}</p>
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

    # Fetch all active courses for this provider
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

    # Run all checks
    all_issues = []
    all_issues.extend(check_summaries(courses))
    all_issues.extend(check_activities(courses))
    all_issues.extend(check_prices(courses))
    all_issues.extend(check_dates(courses))
    all_issues.extend(check_availability(courses))
    all_issues.extend(check_duplicates(courses))
    all_issues.extend(check_course_count(provider_id, current_count))

    # Print results
    if all_issues:
        print(f"  ⚠ {len(all_issues)} issues found:")
        for i in all_issues:
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
    emoji = "✅" if not all_issues else "⚠️"
    subject = f"{emoji} Validation {'passed' if not all_issues else 'issues'} — {provider_name}"
    html = build_report_html(provider_name, provider_id, courses, all_issues, current_count, last_count)
    send_email(subject, html)

    print(f"── Validation complete: {len(all_issues)} issues ──")


if __name__ == "__main__":
    main()
