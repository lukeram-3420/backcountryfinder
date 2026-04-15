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
    sb_upsert, sb_patch, send_email,
    find_place_id, update_provider_ratings,
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

def fetch_availability(item_ids: list, start: str, end: str,
                       chunk: int = 10, window_days: int = 30) -> dict:
    """Fetch /item/cal in chunks of items × time windows.

    Checkfront returns 500 on large item_id[] lists AND on wide date ranges.
    Each request covers up to `chunk` items and `window_days` days.
    Per-item calendars from overlapping responses are merged into a single
    date-keyed dict per item.
    """
    # ── DIAGNOSTIC ──────────────────────────────────────────────────────────
    # Single-item, 7-day probe to see exactly what the endpoint returns.
    # Prints status + body before any raise, so even a 5xx is visible.
    if item_ids:
        diag_start = start
        diag_end = (
            datetime.datetime.strptime(start, "%Y%m%d").date()
            + datetime.timedelta(days=7)
        ).strftime("%Y%m%d")
        diag_params = {
            "item_id[]": [item_ids[0]],
            "start_date": diag_start,
            "end_date":   diag_end,
        }
        try:
            diag_resp = requests.get(
                f"{CF_BASE}/item/cal",
                params=diag_params,
                headers=CF_HEADERS,
                timeout=20,
            )
            print(f"  ── DIAG item/cal probe: item_id={item_ids[0]} {diag_start}→{diag_end}")
            print(f"  ── DIAG final URL: {diag_resp.url}")
            print(f"  ── DIAG status:    {diag_resp.status_code}")
            body = diag_resp.text or ""
            print(f"  ── DIAG body ({len(body)} bytes):")
            print(body[:2000])
            if len(body) > 2000:
                print(f"  ── DIAG body truncated; full length {len(body)} bytes")
        except Exception as e:
            print(f"  ── DIAG request failed: {e}")
    # ── END DIAGNOSTIC ──────────────────────────────────────────────────────

    start_d = datetime.datetime.strptime(start, "%Y%m%d").date()
    end_d   = datetime.datetime.strptime(end,   "%Y%m%d").date()

    windows: list = []
    cur = start_d
    while cur <= end_d:
        w_end = min(cur + datetime.timedelta(days=window_days - 1), end_d)
        windows.append((cur.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")))
        cur = w_end + datetime.timedelta(days=1)

    merged: dict = {}
    for w_start, w_end in windows:
        for i in range(0, len(item_ids), chunk):
            batch = item_ids[i:i + chunk]
            params = {
                "item_id[]": batch,
                "start_date": w_start,
                "end_date":   w_end,
            }
            data = cf_get("item/cal", params=params)
            for iid, cal in data.get("items", {}).items():
                if iid in merged:
                    merged[iid].update(cal)
                else:
                    merged[iid] = dict(cal)
    return merged

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

    # Seed Google Place ID with an explicit name+location query before the
    # generic update_provider_ratings lookup (which uses just name+city).
    place_id = find_place_id("Girth Hitch Guiding Nordegg Alberta")
    if place_id:
        print(f"  Seeded google_place_id: {place_id}")
        sb_patch("providers", f"id=eq.{PROVIDER['id']}", {"google_place_id": place_id})
    else:
        print("  Could not find google_place_id via explicit query")
    update_provider_ratings(PROVIDER["id"])

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
