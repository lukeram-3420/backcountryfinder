#!/usr/bin/env python3
"""
Scraper: Girth Hitch Guiding (girth-hitch-guiding)
Platform: Checkfront Public API v3.0 (no auth required — public API enabled)
Endpoints used:
  GET /api/3.0/item          — full item catalogue
  GET /api/3.0/item/cal      — availability bitmap by date

Same pattern as scraper_aaa.py, with location routed through
scraper_utils.normalise_location per CLAUDE.md hard constraint.
"""

import os
import re
import datetime
import requests

from scraper_utils import (
    sb_upsert, send_email,
    load_location_mappings, normalise_location,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, UTM,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "girth-hitch-guiding",
    "name":     "Girth Hitch Guiding",
    "website":  "https://girthhitchguiding.ca/",
    "location": "Nordegg, AB",
}

CF_BASE     = "https://girth-hitch-guiding.checkfront.com/api/3.0"
BOOKING_URL = "https://girth-hitch-guiding.checkfront.com/reserve/"
GOOGLE_KEY  = os.environ.get("GOOGLE_PLACES_API_KEY", "")

LOOKAHEAD_DAYS = 180

CF_HEADERS = {"X-On-Behalf": "Off"}

# Categories we treat as real bookable courses. Everything else (Equipment,
# Merchandise, Drop In, Indigenous, Samples, Trailhead) is filtered out.
KEEP_CATEGORIES = {
    "alpine climbing",
    "ice climbing",
    "ice climbing (try)",
    "mountain skills courses",
    "rock climbing",
    "rock climbing (try)",
    "via ferrata",
}

# Title-keyword → canonical activity. First match wins; checked in order.
ACTIVITY_MAP = [
    (["via ferrata"],                                                 "via_ferrata"),
    (["ice climb", "ice rescue", "steep ice"],                        "climbing"),
    (["rock climb", "rock safe", "spring into rock", "multi pitch",
      "multi-pitch", "trad lead", "lead rock", "rappel"],             "climbing"),
    (["alpine empowerment", "alpine leadership", "alpine skills",
      "alpine climb", "mountaineer", "bugaboos", "rockies 11",
      "classic canadian", "cline tarns", "spearhead", "traverse"],    "mountaineering"),
]

def resolve_activity_local(title: str, category: str) -> str:
    """Title keywords first, then category fallback, then 'guided'."""
    t = title.lower()
    for keywords, activity in ACTIVITY_MAP:
        if any(k in t for k in keywords):
            return activity
    cat = category.lower()
    if "via ferrata" in cat:
        return "via_ferrata"
    if "alpine climbing" in cat:
        return "mountaineering"
    if "ice climbing" in cat or "rock climbing" in cat:
        return "climbing"
    return "guided"

# Title-keyword → location_raw. Fall back to provider home.
LOCATION_MAP = [
    ("bugaboo",            "Bugaboos, BC"),
    ("rogers pass",        "Rogers Pass, BC"),
    ("banff",              "Banff, AB"),
    ("canmore",            "Canmore, AB"),
    ("jasper",             "Jasper, AB"),
    ("kananaskis",         "Kananaskis, AB"),
    ("lake louise",        "Lake Louise, AB"),
    ("ghost",              "Canmore, AB"),
    ("cline tarns",        "David Thompson Country, AB"),
    ("canadian rockies",   "Canmore, AB"),
    ("rockies 11",         "Canmore, AB"),
]

def resolve_location_raw(title: str) -> str:
    t = title.lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]

# ── Checkfront API ────────────────────────────────────────────────────────────
def cf_get(endpoint, params=None):
    r = requests.get(f"{CF_BASE}/{endpoint}", params=params, headers=CF_HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def fetch_items() -> dict:
    data = cf_get("item")
    items = data.get("items", {})
    cats = sorted({item.get("category", "none") for item in items.values()})
    print(f"  Categories found: {cats}")
    return items

def fetch_availability(item_ids: list, start: str, end: str) -> dict:
    params = {
        "item_id[]": item_ids,
        "start_date": start,
        "end_date":   end,
    }
    data = cf_get("item/cal", params=params)
    return data.get("items", {})

# ── Stable ID — includes item_id to prevent slug collisions ──────────────────
def make_id(provider_id, activity, date_key, item_id, title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:20]
    return f"{provider_id}-{activity}-{date_key}-{item_id}-{slug}"

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🏔 {PROVIDER['name']} — Checkfront API scraper")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    start_s    = today.strftime("%Y%m%d")
    end_s      = end_date.strftime("%Y%m%d")
    scraped_at = datetime.datetime.utcnow().isoformat()

    # Load location mappings for canonical resolution
    loc_mappings = load_location_mappings()
    print(f"  Loaded {len(loc_mappings)} location mappings")

    # 1. Fetch item catalogue
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    course_items = {
        iid: item for iid, item in items.items()
        if item.get("category", "").lower() in KEEP_CATEGORIES
    }
    print(f"  {len(course_items)} course items after filtering")
    if not course_items:
        print("  Nothing to scrape — exiting")
        return

    # 2. Fetch availability calendar
    print(f"  Fetching availability {start_s} → {end_s}...")
    item_ids = list(course_items.keys())
    cal = fetch_availability(item_ids, start_s, end_s)
    print(f"  Calendar entries returned: {len(cal)}")

    # 3. Build rows
    rows = []
    skipped = 0

    for item_id, item in course_items.items():
        title = (item.get("name") or "").strip()
        if not title:
            skipped += 1
            continue

        # Price — may be a dict (net/total) or scalar or None
        price_raw = item.get("price")
        if isinstance(price_raw, dict):
            try:
                price = int(float(next(iter(price_raw.values()))))
            except (StopIteration, ValueError, TypeError):
                price = None
        else:
            try:
                price = int(float(price_raw)) if price_raw else None
            except (ValueError, TypeError):
                price = None

        category = item.get("category", "")
        activity = resolve_activity_local(title, category)

        # Location: title keyword → raw → canonical via scraper_utils
        location_raw = resolve_location_raw(title)
        location_canonical = normalise_location(location_raw, loc_mappings)

        # Description from Checkfront summary HTML (strip tags)
        description_html = item.get("summary") or ""
        description = re.sub(r"<[^>]+>", "", description_html).strip()

        item_cal = cal.get(str(item_id), {})
        if not item_cal:
            skipped += 1
            continue

        for date_key, available in item_cal.items():
            if not available:
                continue
            try:
                d = datetime.date(
                    int(date_key[:4]),
                    int(date_key[4:6]),
                    int(date_key[6:8]),
                )
            except ValueError:
                continue

            date_sort    = d.isoformat()
            date_display = d.strftime("%b %-d, %Y")
            course_id    = make_id(PROVIDER["id"], activity, date_key, item_id, title)
            booking_url  = (
                f"{BOOKING_URL}?item_id={item_id}&start_date={date_key}"
                f"&{UTM}"
            )

            rows.append({
                "id":                 course_id,
                "provider_id":        PROVIDER["id"],
                "title":              title,
                "activity":           activity,
                "activity_raw":       category,
                "activity_canonical": activity,
                "location_raw":       location_raw,
                "location_canonical": location_canonical,
                "date_sort":          date_sort,
                "date_display":       date_display,
                "duration_days":      item.get("len", 1),
                "price":              price,
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "booking_url":        booking_url,
                "description":        description or None,
                "summary":            "",
                "image_url":          None,
                "badge":              None,
                "badge_canonical":    None,
                "custom_dates":       False,
                "scraped_at":         scraped_at,
            })

    print(f"  Built {len(rows)} course-date rows · {skipped} items skipped")

    # 4. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i+50])

    print(f"  ✅ Upserted {len(rows)} rows")


if __name__ == "__main__":
    main()
