#!/usr/bin/env python3
"""
Scraper: Bow Valley Canyon Tours (bow-valley-canyon-tours)
Platform: Checkfront Public API v3.0 at canadian-wilderness-school-expeditions.checkfront.com

The bowvalleycanyoning.ca booking page embeds a Checkfront iframe pointing
to the parent company's tenant ("Canadian Wilderness School & Expeditions").
The iframe filters to category IDs 3,4,8,5,7 (Canyoning / 4x4 Tours / Add Ons
/ Courses / Gift Certificates) and 14 specific item IDs.

We scrape via the public Checkfront API (anonymous, no auth) and filter to
the same product categories the website surfaces, dropping Gift Certificates
and Add Ons. Mirrors scraper_girth_hitch_guiding.py / scraper_aaa.py.

Endpoints used:
  GET /api/3.0/item          — full item catalogue
  GET /api/3.0/item/cal      — availability bitmap by date (per-item)
"""

import re
import datetime
import requests

from scraper_utils import (
    sb_upsert, stable_id_v2,
    log_availability_change, log_price_change,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    spots_to_avail, append_utm,
    UTM,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "bow-valley-canyon-tours",
    "name":     "Bow Valley Canyon Tours",
    "website":  "https://www.bowvalleycanyoning.ca/",
    "location": "Banff, AB",
}

CF_BASE     = "https://canadian-wilderness-school-expeditions.checkfront.com/api/3.0"
BOOKING_URL = "https://canadian-wilderness-school-expeditions.checkfront.com/reserve/"

LOOKAHEAD_DAYS = 180

CF_HEADERS = {
    "X-On-Behalf": "Off",
}

# Non-course product titles to skip.
EXCLUDE_TITLES = [
    "gift card",
    "gift certificate",
    "deposit",
    "membership",
    "merchandise",
]

# Category whitelist — only keep items whose Checkfront category matches one
# of these (case-insensitive). From the iframe HTML inspection: Canyoning,
# 4x4 Tours, Courses are the bookable products. Add Ons (gear add-ons) and
# Gift Certificates are excluded as non-courses.
KEEP_CATEGORIES = {
    "canyoning",
    "4x4 tours",
    "courses",
}

EXCLUDE_CATEGORIES = {
    "add ons",
    "add-ons",
    "gift certificates",
    "gift certificate",
}

# Title-keyword location resolution. First match wins; result passes through
# normalise_location() so unknowns queue to pending_location_mappings.
LOCATION_MAP = [
    ("kananaskis",   "Kananaskis, AB"),
    ("canmore",      "Canmore, AB"),
    ("three sisters","Canmore, AB"),
    ("ha ling",      "Canmore, AB"),
    ("lake louise",  "Lake Louise, AB"),
    ("banff",        "Banff, AB"),
    ("bow valley",   "Banff, AB"),
    ("yoho",         "Field, BC"),
    ("kootenay",     "Kootenay, BC"),
    ("radium",       "Radium Hot Springs, BC"),
]


def resolve_location_raw(title: str) -> str:
    t = (title or "").lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]


# ── Checkfront API ────────────────────────────────────────────────────────────
def cf_get(endpoint, params=None):
    r = requests.get(
        f"{CF_BASE}/{endpoint}",
        params=params,
        headers=CF_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_items() -> dict:
    data = cf_get("item")
    items = data.get("items", {})
    cats = sorted({(item.get("category") or "none") for item in items.values()})
    print(f"  Categories found: {cats}")
    return items


def fetch_availability(item_ids: list, start: str, end: str) -> dict:
    # Fetch one item at a time — some Checkfront tenants 500 on multi-item requests.
    result = {}
    for iid in item_ids:
        try:
            params = {
                "item_id[]": [iid],
                "start_date": start,
                "end_date":   end,
            }
            data = cf_get("item/cal", params=params)
            result.update(data.get("items", {}))
        except Exception as e:
            print(f"  item/cal failed for item {iid}: {e}")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🏞 {PROVIDER['name']} — Checkfront API scraper")

    # Places rating
    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        print(f"  Places update failed: {e}")

    loc_mappings = load_location_mappings()
    print(f"  Loaded {len(loc_mappings)} location mappings")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    start_s    = today.strftime("%Y%m%d")
    end_s      = end_date.strftime("%Y%m%d")
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Fetch item catalogue
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    # 2. Filter: EXCLUDE_TITLES + EXCLUDE_CATEGORIES + KEEP_CATEGORIES
    course_items = {}
    for iid, item in items.items():
        title = (item.get("name") or "").strip()
        if not title:
            continue
        if title.lower().strip() in EXCLUDE_TITLES:
            print(f"  excluding non-course product: {title!r}")
            continue
        cat = (item.get("category") or "").lower().strip()
        if cat in EXCLUDE_CATEGORIES:
            print(f"  excluding non-course category {cat!r}: {title!r}")
            continue
        if KEEP_CATEGORIES and cat not in KEEP_CATEGORIES:
            print(f"  excluding category not in KEEP list {cat!r}: {title!r}")
            continue
        course_items[iid] = item
    print(f"  {len(course_items)} course items after filtering")

    if not course_items:
        print("  No course items — exiting")
        return

    # 3. Fetch availability calendar
    print(f"  Fetching availability {start_s} → {end_s}...")
    item_ids = list(course_items.keys())
    cal = fetch_availability(item_ids, start_s, end_s)
    print(f"  Calendar entries returned: {len(cal)}")

    # Probe /item/cal response shape: integer counts vs 0/1 boolean bitmap.
    raw_values = set()
    for _ic in cal.values():
        for v in (_ic or {}).values():
            try:
                raw_values.add(int(v))
            except (ValueError, TypeError):
                pass
    api_has_spot_counts = any(v > 1 for v in raw_values)
    print(
        f"  Availability value distribution: {sorted(raw_values)[:20]} "
        f"→ spot tracking {'ENABLED (integer counts)' if api_has_spot_counts else 'disabled (API returns 0/1 only)'}"
    )

    # 4. Build rows
    rows = []
    skipped = 0

    for item_id, item in course_items.items():
        title = item.get("name", "").strip()

        # Price — Checkfront returns either a scalar or a per-tier dict
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

        loc_raw       = resolve_location_raw(title)
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        # Description from Checkfront summary HTML (strip tags)
        description_html = item.get("summary") or ""
        description = re.sub(r"<[^>]+>", "", description_html).strip()

        item_cal = cal.get(str(item_id), {})
        if not item_cal:
            skipped += 1
            continue

        duration_days = item.get("len") or None
        if duration_days == 0:
            duration_days = None

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

            spots_remaining = None
            if api_has_spot_counts:
                try:
                    spots_remaining = int(available)
                except (ValueError, TypeError):
                    spots_remaining = None

            avail = spots_to_avail(spots_remaining)

            date_sort    = d.isoformat()
            date_display = d.strftime("%b %-d, %Y")
            course_id    = stable_id_v2(PROVIDER["id"], date_sort, title)
            booking_url  = append_utm(
                f"{BOOKING_URL}?item_id={item_id}&start_date={date_key}"
            )

            row = {
                "id":                 course_id,
                "provider_id":        PROVIDER["id"],
                "title":              title,
                "location_raw":       loc_raw,
                "date_sort":          date_sort,
                "date_display":       date_display,
                "duration_days":      duration_days,
                "price":              price,
                "currency":           "CAD",
                "spots_remaining":    spots_remaining,
                "avail":              avail,
                "active":             avail != "sold",
                "booking_url":        booking_url,
                "summary":            "",
                "search_document":    "",
                "image_url":          None,
                "custom_dates":       False,
                "description":        description or None,
                "scraped_at":         scraped_at,
            }
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            rows.append(row)

    print(f"  Built {len(rows)} course-date rows · {skipped} items skipped")

    # 5. Summaries — dedup by title
    if rows:
        by_title = {}
        for r in rows:
            if r.get("description") and r["title"] not in by_title:
                by_title[r["title"]] = {
                    "id":          r["title"],
                    "title":       r["title"],
                    "description": r["description"],
                    "provider":    PROVIDER["name"],
                }
        if by_title:
            try:
                summaries = generate_summaries_batch(
                    list(by_title.values()), provider_id=PROVIDER["id"]
                )
                print(f"  Generated {len(summaries)} summaries")
                for r in rows:
                    result = summaries.get(r["title"])
                    if result:
                        r["summary"]         = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                print(f"  Summary batch failed: {e}")

    # 6. Strip description (not a courses column)
    for r in rows:
        r.pop("description", None)

    # 7. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    # Intelligence logging (V2 — append-only, change-detected)
    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
