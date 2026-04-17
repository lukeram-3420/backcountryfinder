#!/usr/bin/env python3
"""
crawl_courses.py — Read-only audit of V2 course data quality.

Pulls every V2 row (`activity_canonical IS NULL`) from Supabase, classifies
data-quality issues across ~15 categories, and emits a structured report.
Intended as the grounding dataset for admin-page cleanup workflows.

No writes. Safe to run any time, as often as you like.

Usage:
  python crawl_courses.py                    # markdown report to stdout
  python crawl_courses.py --json             # JSON instead
  python crawl_courses.py --provider altus   # scope to a single provider
  python crawl_courses.py --out report.md    # write to file

Env:
  SUPABASE_URL             (required)
  SUPABASE_SERVICE_KEY     (preferred)  OR  SUPABASE_KEY (anon, falls through)
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://owzrztaguehebkatnatc.supabase.co")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or "sb_publishable_lqIyTGAgCn09Yfh1eacSPg_tcs9SJcB"
)

PAGE = 1000


def fetch_v2_courses():
    """Paginate through every V2 row, returning a flat list."""
    rows = []
    offset = 0
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/courses"
            f"?select=id,title,provider_id,activity_canonical,location_raw,location_canonical,"
            f"date_display,date_sort,duration_days,price,currency,spots_remaining,avail,"
            f"booking_url,booking_mode,active,custom_dates,summary,scraped_at,"
            f"flagged,flagged_reason,flagged_note,auto_flagged,flag_reason,"
            f"providers(name,active)"
            f"&activity_canonical=is.null"
            f"&order=provider_id.asc,title.asc"
            f"&limit={PAGE}&offset={offset}"
        )
        r = requests.get(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def classify(courses):
    """Run every check against every row. Returns (issues, by_provider)."""
    issues = defaultdict(list)
    by_provider = defaultdict(Counter)

    # Duplicate detection: provider_id + title + date_sort
    dup = defaultdict(list)
    for c in courses:
        dup[(c.get("provider_id"), c.get("title"), c.get("date_sort"))].append(c["id"])

    today = date.today()
    two_years_out = date(today.year + 2, today.month, today.day)

    for c in courses:
        pid = c.get("provider_id") or "?"

        def flag(kind, reason, severity="warn"):
            issues[kind].append({
                "id": c["id"],
                "provider_id": pid,
                "title": c.get("title"),
                "price": c.get("price"),
                "date_sort": c.get("date_sort"),
                "avail": c.get("avail"),
                "booking_url": c.get("booking_url"),
                "reason": reason,
                "severity": severity,
            })
            by_provider[pid][kind] += 1

        # Structural
        if not c.get("title"):
            flag("missing_title", "title is null/empty", "critical")
        if not c.get("avail"):
            flag("null_avail", "avail is null")

        # Price — Initiative 4: zero/negative only. Null and >5x-median
        # checks retired; validator no longer acts on them.
        p = c.get("price")
        if p is not None and p <= 0:
            flag("bad_price", f"price = {p} (auto-hidden by validator, escalates after 24h)", "critical")

        # Summary
        if not c.get("summary"):
            flag("null_summary", "summary is null/empty")

        # Dates
        ds = c.get("date_sort")
        if ds:
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                if d < today and c.get("active"):
                    # validate_provider.py Check 4 auto-hides + escalates after 24h.
                    flag("past_date_active", f"date {ds} is past but active=true (auto-hidden by validator, escalates after 24h)", "critical")
                if d > two_years_out:
                    # Symmetric with past-date under Initiative 5 — auto-hide + 24h escalation.
                    flag("far_future_date", f"date {ds} is >2 years out (auto-hidden by validator, escalates after 24h)")
            except ValueError:
                flag("unparseable_date", f"date_sort={ds!r}")
        elif not c.get("custom_dates"):
            # no date_sort AND not flagged as custom_dates → likely incomplete
            flag("no_date_no_custom_flag", "date_sort null and custom_dates is not true")

        # Location
        if c.get("location_raw") and not c.get("location_canonical"):
            flag(
                "null_location_canonical",
                f"location_raw={c['location_raw']!r} but canonical is null",
            )

        # User / validator flags (already flagged, needs admin action)
        if c.get("flagged"):
            flag(
                "user_flag",
                f"reason={c.get('flagged_reason')} note={c.get('flagged_note')}",
            )
        if c.get("auto_flagged"):
            flag("auto_flag", f"reason={c.get('flag_reason')}")

        # Booking URL
        bu = c.get("booking_url") or ""
        if not bu or not (bu.startswith("http://") or bu.startswith("https://")):
            flag("bad_booking_url", f"booking_url={bu!r}", "critical")
        elif "utm_source=backcountryfinder" not in bu:
            flag("booking_url_missing_utm", "booking_url lacks utm_source=backcountryfinder")

        # Currency
        if c.get("currency") and c["currency"] != "CAD":
            flag("currency_non_cad", f"currency={c['currency']}")

        # Duplicates — Initiative 6: validator auto-hides all-but-first.
        # No whitelist layer — fix is always in the scraper.
        key = (c.get("provider_id"), c.get("title"), c.get("date_sort"))
        if len(dup[key]) > 1:
            flag("duplicate", f"{len(dup[key])} rows share provider+title+date_sort (auto-hidden by validator; fix scraper_{c.get('provider_id')}.py)")

    return issues, by_provider


def render_markdown(courses, issues, by_provider):
    lines = []
    total = len(courses)
    providers = sorted({c.get("provider_id") for c in courses if c.get("provider_id")})
    total_issues = sum(len(v) for v in issues.values())

    lines.append("# Course data audit — V2 rows")
    lines.append(f"_Generated {datetime.utcnow().isoformat(timespec='seconds')}Z_")
    lines.append("")
    lines.append(f"- **{total}** V2 courses across **{len(providers)}** providers")
    lines.append(f"- **{len(issues)}** distinct issue categories, **{total_issues}** total flags")
    lines.append(
        f"- **{sum(1 for c in courses if c.get('active'))}** active, "
        f"**{sum(1 for c in courses if c.get('flagged'))}** user-flagged, "
        f"**{sum(1 for c in courses if c.get('auto_flagged'))}** auto-flagged"
    )
    lines.append("")

    # Overview table
    lines.append("## Issue categories (by volume)")
    lines.append("| Category | Count | Critical | Warn |")
    lines.append("|---|---:|---:|---:|")
    for kind, rows in sorted(issues.items(), key=lambda kv: -len(kv[1])):
        sev = Counter(r["severity"] for r in rows)
        lines.append(
            f"| `{kind}` | {len(rows)} | {sev.get('critical', 0)} | {sev.get('warn', 0)} |"
        )
    lines.append("")

    # Per-provider heatmap
    lines.append("## Per-provider issue counts")
    all_kinds = sorted(issues.keys())
    header = "| Provider | " + " | ".join(k.replace("_", " ") for k in all_kinds) + " | Total |"
    sep = "|---|" + "|".join("---:" for _ in all_kinds) + "|---:|"
    lines.append(header)
    lines.append(sep)
    for pid in providers:
        counts = by_provider.get(pid, Counter())
        row_total = sum(counts.values())
        if row_total == 0:
            continue
        cells = [str(counts.get(k, 0)) if counts.get(k, 0) else "" for k in all_kinds]
        lines.append(f"| {pid} | " + " | ".join(cells) + f" | **{row_total}** |")
    lines.append("")

    # Sample rows per category
    lines.append("## Sample rows per category")
    for kind, rows in sorted(issues.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"### `{kind}` — {len(rows)}")
        lines.append("")
        lines.append("| Provider | Course ID | Title | Reason |")
        lines.append("|---|---|---|---|")
        for r in rows[:10]:
            t = (r["title"] or "")[:70].replace("|", "\\|")
            reason = r["reason"].replace("|", "\\|")
            lines.append(f"| {r['provider_id']} | `{r['id']}` | {t} | {reason} |")
        if len(rows) > 10:
            lines.append(f"_…and {len(rows) - 10} more_")
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="Emit raw JSON instead of markdown")
    ap.add_argument("--provider", help="Scope to a single provider_id")
    ap.add_argument("--out", help="Write to file instead of stdout")
    args = ap.parse_args()

    print("Fetching V2 courses…", file=sys.stderr)
    courses = fetch_v2_courses()
    if args.provider:
        courses = [c for c in courses if c.get("provider_id") == args.provider]
    print(f"Fetched {len(courses)} V2 courses", file=sys.stderr)

    issues, by_provider = classify(courses)

    if args.json:
        payload = {
            "total": len(courses),
            "issues": dict(issues),
            "by_provider": {k: dict(v) for k, v in by_provider.items()},
        }
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_markdown(courses, issues, by_provider)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {args.out} ({len(out.splitlines())} lines)", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
