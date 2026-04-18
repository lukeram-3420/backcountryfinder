#!/usr/bin/env python3
"""
Scraper: Vibe Backcountry (vibe-backcountry)
Platform: FareHarbor External API v1 at fareharbor.com/api/external/v1/companies/vibebackcountry
Endpoints used:
  GET /items/                                                — full item catalogue
  GET /items/{pk}/availabilities/date-range/{start}/{end}/   — per-item availability

Mirrors scraper_girth_hitch_guiding.py (Checkfront). Single-pass API scraper.
Vibe Backcountry is an ACMG-certified Vancouver Island guiding operation
covering backcountry skiing / splitboarding / AST / rock / alpine /
mountaineering / sea kayaking out of Nanaimo, BC.

First FareHarbor adapter in the codebase. If a second FareHarbor provider
lands, extract the fh_get / fetch_items / fetch_availability helpers into
scraper_fareharbor_utils.py (mirror the scraper_zaui_utils.py precedent).
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
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "vibe-backcountry",
    "name":     "Vibe Backcountry",
    "website":  "https://www.vibebackcountry.com",
    "location": "Nanaimo, BC",
}

FH_SHORTNAME = "vibebackcountry"
FH_BASE      = f"https://fareharbor.com/api/v1/companies/{FH_SHORTNAME}"
BOOKING_URL  = f"https://fareharbor.com/embeds/book/{FH_SHORTNAME}/items"

LOOKAHEAD_DAYS = 180

# FareHarbor's /api/v1/ widget path is the anonymous CORS-enabled endpoint
# used by the Lightframe / Flow-Down JS embed on any operator's website.
# /api/external/v1/ is auth-only (X-FareHarbor-API-App + X-FareHarbor-API-User
# headers) and returns 400 without them. Operator Referer + browser UA help
# satisfy CDN heuristics without triggering auth requirements.
FH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.vibebackcountry.com/",
    "Origin":     "https://www.vibebackcountry.com",
}

# Non-course product titles to skip. Mirrors the Girth Hitch list.
EXCLUDE_TITLES = [
    "gift card",
    "gift certificate",
    "deposit",
    "membership",
    "custom trip",
]

# Title-keyword location resolution. First match wins; result passes through
# normalise_location() so unknowns queue to pending_location_mappings.
LOCATION_MAP = [
    ("colonel foster",    "Strathcona Park, BC"),
    ("strathcona",        "Strathcona Park, BC"),
    ("elkhorn",           "Strathcona Park, BC"),
    ("vancouver island",  "Nanaimo, BC"),
    ("nanaimo",           "Nanaimo, BC"),
    ("squamish",          "Squamish, BC"),
    ("pemberton",         "Pemberton, BC"),
    ("whistler",          "Whistler, BC"),
    ("bugaboos",          "Bugaboos, BC"),
    ("sea to sky",        "Squamish, BC"),
]


def resolve_location_raw(title: str) -> str:
    t = (title or "").lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]


# ── FareHarbor API ────────────────────────────────────────────────────────────
def fh_get(path, params=None):
    r = requests.get(
        f"{FH_BASE}/{path}",
        params=params,
        headers=FH_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def fetch_items() -> list:
    data = fh_get("items/")
    items = data.get("items") or []
    if items:
        sample_keys = sorted(items[0].keys())
        print(f"  Item keys (first row): {sample_keys}")
    return items


def fetch_availability(item_pk: int, start: str, end: str) -> list:
    # FareHarbor's anonymous widget API requires the /minimal/ segment for
    # availability — bare /availabilities/... is auth-only (External API)
    # and 404s on this path. The /minimal/ payload still includes start_at,
    # end_at, capacity, capacity_remaining, and customer_type_rates.
    try:
        data = fh_get(f"items/{item_pk}/minimal/availabilities/date-range/{start}/{end}/")
        return data.get("availabilities") or []
    except Exception as e:
        print(f"  availabilities failed for item {item_pk}: {e}")
        return []


def parse_iso_date(ts: str) -> datetime.date:
    # FareHarbor returns start_at like "2025-05-15T09:00:00-08:00" — python's
    # fromisoformat handles this on 3.11. Strip to date only.
    return datetime.datetime.fromisoformat(ts).date()


def cheapest_price_cad(item: dict) -> "int | None":
    """
    FareHarbor stores prices in cents on customer_prototypes[].total (when
    set at the item level). Return the cheapest adult-like rate as an int
    CAD amount, or None if not extractable.
    """
    protos = item.get("customer_prototypes") or []
    totals = []
    for p in protos:
        t = p.get("total")
        if isinstance(t, (int, float)) and t > 0:
            totals.append(int(t))
    if not totals:
        return None
    return int(min(totals) / 100)


def strip_html(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s).strip()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🧗 {PROVIDER['name']} — FareHarbor API scraper")

    # Places rating
    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        print(f"  Places update failed: {e}")

    loc_mappings = load_location_mappings()
    print(f"  Loaded {len(loc_mappings)} location mappings")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    start_s    = today.strftime("%Y-%m-%d")
    end_s      = end_date.strftime("%Y-%m-%d")
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Fetch item catalogue
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    # 2. Filter: EXCLUDE_TITLES + is_bookable_online
    course_items = []
    for item in items:
        title = (item.get("name") or "").strip()
        if not title:
            continue
        if title.lower().strip() in EXCLUDE_TITLES:
            print(f"  excluding non-course product: {title!r}")
            continue
        if item.get("is_archived") or item.get("is_unlisted"):
            print(f"  skipping archived/unlisted: {title!r}")
            continue
        if item.get("is_bookable_online") is False:
            print(f"  skipping non-bookable item: {title!r}")
            continue
        course_items.append(item)
    print(f"  {len(course_items)} course items after filtering")

    if not course_items:
        print("  No course items — exiting")
        return

    # 3. Build rows — per-item availability fetch
    print(f"  Fetching availability {start_s} → {end_s}...")
    rows = []
    skipped = 0
    spot_samples = []

    for item in course_items:
        pk    = item.get("pk")
        title = (item.get("name") or "").strip()

        price       = cheapest_price_cad(item)
        description = strip_html(item.get("description") or item.get("headline") or "")
        image_url   = item.get("image_cdn_url") or None

        loc_raw       = resolve_location_raw(title)
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        avails = fetch_availability(pk, start_s, end_s)
        if not avails:
            skipped += 1
            continue

        for a in avails:
            start_at = a.get("start_at")
            end_at   = a.get("end_at")
            if not start_at:
                continue
            try:
                d_start = parse_iso_date(start_at)
            except Exception:
                continue

            duration_days = None
            if end_at:
                try:
                    d_end = parse_iso_date(end_at)
                    delta = (d_end - d_start).days
                    if delta > 0:
                        duration_days = delta + 1
                except Exception:
                    pass

            capacity_remaining = a.get("capacity_remaining")
            if isinstance(capacity_remaining, int):
                spot_samples.append(capacity_remaining)
                spots_remaining = capacity_remaining
            else:
                spots_remaining = None

            avail = spots_to_avail(spots_remaining)

            date_sort    = d_start.isoformat()
            date_display = d_start.strftime("%b %-d, %Y")
            course_id    = stable_id_v2(PROVIDER["id"], date_sort, title)
            booking_url  = append_utm(
                f"{BOOKING_URL}/{pk}/?full-items=yes&flow=no&g4=yes"
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
                "image_url":          image_url,
                "custom_dates":       False,
                "description":        description or None,
                "scraped_at":         scraped_at,
            }
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            rows.append(row)

    if spot_samples:
        distinct = sorted(set(spot_samples))[:20]
        print(f"  capacity_remaining distribution (first 20 distinct): {distinct}")

    print(f"  Built {len(rows)} course-date rows · {skipped} items skipped (no availability)")

    # 4. Summaries — dedup by title (all dates of same course share the summary)
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
                        r["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                print(f"  Summary batch failed: {e}")

    # 5. Strip description (not a courses column)
    for r in rows:
        r.pop("description", None)

    # 6. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    # Log intelligence (V2 — append-only, change-detected)
    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
