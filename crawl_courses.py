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
from statistics import median

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://owzrztaguehebkatnatc.supabase.co")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_KEY")
    or "sb_publishable_lqIyTGAgCn09Yfh1eacSPg_tcs9SJcB"
)

PAGE = 1000

# Rough activity-from-title fingerprint — used only to flag *suspected* mismatches,
# never to auto-correct. Admin still decides.
ACTIVITY_KEYWORDS = {
    "skiing":           ["ski touring", "backcountry ski", "splitboard", "avy", "avalanche", "ast "],
    "hiking":           ["hike", "hiking", "backpack", "trek"],
    "climbing":         ["climb", "rock climb", "multi-pitch", "rappel", "crag", "trad ", "sport climb"],
    "mountaineering":   ["mountaineer", "glacier travel", "alpine climb", "crampon", "crevasse"],
    "biking":           ["mtb", "mountain bike", "cycling"],
    "fishing":          ["fly fish", "angling", "fishing"],
    "heli":             ["heli skiing", "heli ski", "heli tour"],
    "cat":              ["cat ski", "cat skiing"],
    "via_ferrata":      ["via ferrata"],
    "snowshoeing":      ["snowshoe"],
    "avalanche_safety": ["ast 1", "ast 2", "avalanche skills"],
}


def fetch_v2_courses():
    """Paginate through every V2 row, returning a flat list."""
    rows = []
    offset = 0
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/courses"
            f"?select=id,title,provider_id,activity,activity_canonical,location_raw,location_canonical,"
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


def title_suggests_activity(title: str, current: str):
    """Return a suggested canonical activity if title keywords disagree with `current`."""
    t = (title or "").lower()
    # ski mountaineering straddles skiing <-> mountaineering; accept either
    if "ski" in t and "mountaineer" in t:
        return None
    for canon, kws in ACTIVITY_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return None if canon == current else canon
    return None


def classify(courses):
    """Run every check against every row. Returns (issues, by_provider, medians)."""
    issues = defaultdict(list)
    by_provider = defaultdict(Counter)

    # Price medians per activity — for outlier detection
    by_act = defaultdict(list)
    for c in courses:
        p = c.get("price")
        a = c.get("activity")
        if p and p > 0 and a:
            by_act[a].append(p)
    medians = {a: median(ps) for a, ps in by_act.items() if ps}

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
                "activity": c.get("activity"),
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
        if not c.get("activity"):
            flag("null_activity", "activity is null", "critical")
        if not c.get("avail"):
            flag("null_avail", "avail is null")

        # Price
        p = c.get("price")
        if p is None:
            flag("null_price", "price is null")
        elif p <= 0:
            flag("bad_price", f"price = {p}", "critical")
        else:
            a = c.get("activity")
            if a and a in medians and p > 5 * medians[a]:
                flag("price_outlier", f"${p} > 5× median ${medians[a]:.0f} for {a}")

        # Activity/title mismatch
        if c.get("title") and c.get("activity"):
            sugg = title_suggests_activity(c["title"], c["activity"])
            if sugg:
                flag("activity_mismatch", f"title suggests '{sugg}', marked '{c['activity']}'")

        # Summary
        if not c.get("summary"):
            flag("null_summary", "summary is null/empty")

        # Dates
        ds = c.get("date_sort")
        if ds:
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                if d < today and c.get("active"):
                    flag("past_date_active", f"date {ds} is past but active=true", "critical")
                if d > two_years_out:
                    flag("far_future_date", f"date {ds} is >2 years out")
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

        # Duplicates
        key = (c.get("provider_id"), c.get("title"), c.get("date_sort"))
        if len(dup[key]) > 1:
            flag("duplicate", f"{len(dup[key])} rows share provider+title+date_sort")

    return issues, by_provider, medians


def render_markdown(courses, issues, by_provider, medians):
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

    # Price medians reference
    lines.append("## Price medians by activity")
    lines.append("Used for the `price_outlier` check (>5× median).")
    lines.append("")
    lines.append("| Activity | Median price | Sample size |")
    lines.append("|---|---:|---:|")
    for a, m in sorted(medians.items()):
        n = sum(1 for c in courses if c.get("activity") == a and c.get("price"))
        lines.append(f"| {a} | ${m:.0f} | {n} |")
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

    issues, by_provider, medians = classify(courses)

    if args.json:
        payload = {
            "total": len(courses),
            "issues": dict(issues),
            "by_provider": {k: dict(v) for k, v in by_provider.items()},
            "medians": medians,
        }
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_markdown(courses, issues, by_provider, medians)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {args.out} ({len(out.splitlines())} lines)", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
