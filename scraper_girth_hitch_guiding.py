#!/usr/bin/env python3
"""
Scraper: Girth Hitch Guiding (girth-hitch-guiding)
Platform: Checkfront Public API v3.0 at girth-hitch-guiding.checkfront.com
Endpoints used:
  GET /api/3.0/item          — full item catalogue
  GET /api/3.0/item/cal      — availability bitmap by date

Mirrors scraper_aaa.py. Provider offers rock / ice / alpine / via ferrata
climbing + guided peaks out of Nordegg, AB with satellite operations in
Bow Valley, Bugaboos, Jasper, Yoho, and Squamish.
"""

import re
import time
import datetime
import requests

from scraper_utils import (
    sb_upsert, stable_id_v2,
    log_availability_change, log_price_change,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    spots_to_avail, append_utm,
    detect_checkfront_spot_counts,
    title_hash,
    activity_key, upsert_activity_control, load_activity_controls,
    UTM,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "girth-hitch-guiding",
    "name":     "Girth Hitch Guiding",
    "website":  "http://www.girthhitchguiding.ca",
    "location": "Nordegg, AB",
}

CF_BASE     = "https://girth-hitch-guiding.checkfront.com/api/3.0"
BOOKING_URL = "https://girth-hitch-guiding.checkfront.com/reserve/"

LOOKAHEAD_DAYS = 180

CF_HEADERS = {
    "X-On-Behalf": "Off",
}

# Per-title exclusions live in activity_controls now — hide a title by
# flipping visible=false in the admin Activity Tracking tab. Seeded from
# the historical list via seed_activity_controls.py.
_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="checkfront",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

# Non-course Checkfront categories. Confirmed from first-run log:
# Merchandise / Equipment / Samples are retail or internal items, not guided
# activities. Drop In and Trailhead may be clinics / meetup points — leave
# them flowing through the admin Flags tab for now.
EXCLUDE_CATEGORIES = {
    "merchandise",
    "equipment",
    "samples",
}

# Category whitelist — only keep items whose Checkfront category matches.
# First-run logs print all categories; tune this set if real categories differ.
# Empty-string default means "keep" — safer than over-filtering a small catalog.
KEEP_CATEGORIES: set = set()  # keep-all if empty; first run prints categories

# Title-keyword location resolution. First match wins; result passes through
# normalise_location() so unknowns queue to pending_location_mappings.
LOCATION_MAP = [
    ("nordegg",           "Nordegg, AB"),
    ("lake abraham",      "Nordegg, AB"),
    ("david thompson",    "Nordegg, AB"),
    ("bow valley",        "Canmore, AB"),
    ("canmore",           "Canmore, AB"),
    ("banff",             "Banff, AB"),
    ("lake louise",       "Lake Louise, AB"),
    ("jasper",            "Jasper, AB"),
    ("yoho",              "Field, BC"),
    ("bugaboos",          "Bugaboos, BC"),
    ("squamish",          "Squamish, BC"),
    ("penticton",         "Penticton, BC"),
    ("kananaskis",        "Kananaskis, AB"),
]


def resolve_location_raw(title: str) -> str:
    t = (title or "").lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]


# ── Checkfront API ────────────────────────────────────────────────────────────
def cf_get(endpoint, params=None):
    """GET a Checkfront endpoint with retry-on-5xx.

    Checkfront /item/cal occasionally returns 500 on otherwise valid
    requests — usually transient. Three attempts with exponential
    backoff (2s, 4s, 8s) before giving up. 4xx responses still raise
    immediately (no point retrying a bad request).
    """
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(
                f"{CF_BASE}/{endpoint}",
                params=params,
                headers=CF_HEADERS,
                timeout=15,
            )
            if 500 <= r.status_code < 600:
                last_err = requests.HTTPError(
                    f"{r.status_code} {r.reason} for {r.url}", response=r
                )
                if attempt < 2:
                    backoff = 2 ** (attempt + 1)
                    print(f"  Checkfront {r.status_code} on {endpoint} — retry {attempt + 1}/2 in {backoff}s")
                    time.sleep(backoff)
                    continue
                raise last_err
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < 2:
                backoff = 2 ** (attempt + 1)
                print(f"  Checkfront request error on {endpoint} ({e}) — retry {attempt + 1}/2 in {backoff}s")
                time.sleep(backoff)
                continue
            raise
    raise last_err  # unreachable, but keeps the contract explicit


def fetch_items() -> dict:
    data = cf_get("item")
    items = data.get("items", {})
    cats = sorted({(item.get("category") or "none") for item in items.values()})
    print(f"  Categories found: {cats}")
    return items


def fetch_availability(item_ids: list, start: str, end: str) -> dict:
    """Fetch availability with batch-then-per-item fallback.

    Default path: batch 5 items per /item/cal request (Checkfront 500s
    on larger lists from this tenant). When a batch fails after the
    retry-on-5xx logic in cf_get, fall back to per-item queries for
    that batch — one or more specific item_ids is poisoning the batch,
    and we don't want one bad item to take out the whole run.
    Individual failures are logged and skipped.
    """
    result = {}
    failed_items: list = []
    for i in range(0, len(item_ids), 5):
        batch = item_ids[i:i + 5]
        params = {
            "item_id[]": batch,
            "start_date": start,
            "end_date":   end,
        }
        try:
            data = cf_get("item/cal", params=params)
            result.update(data.get("items", {}))
            continue
        except requests.HTTPError as e:
            print(f"  Batch {batch} failed ({e.response.status_code if e.response is not None else '?'}); falling back to per-item")
        # Per-item fallback — at least the healthy items in this batch get through.
        for iid in batch:
            try:
                data = cf_get("item/cal", params={
                    "item_id[]": [iid],
                    "start_date": start,
                    "end_date":   end,
                })
                result.update(data.get("items", {}))
            except Exception as e:
                failed_items.append(iid)
                print(f"    item {iid}: {e}")
    if failed_items:
        print(f"  Skipped {len(failed_items)} item(s) that 500'd individually: {failed_items}")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🧗 {PROVIDER['name']} — Checkfront API scraper")

    # Places rating
    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        print(f"  Places update failed: {e}")

    loc_mappings = load_location_mappings()
    print(f"  Loaded {len(loc_mappings)} location mappings")

    global _CONTROLS
    _CONTROLS = load_activity_controls(PROVIDER["id"])
    print(f"  Loaded {len(_CONTROLS)} activity controls")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    start_s    = today.strftime("%Y%m%d")
    end_s      = end_date.strftime("%Y%m%d")
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Fetch item catalogue
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    # 2. Filter: activity_controls visibility + EXCLUDE_CATEGORIES + optional KEEP_CATEGORIES
    course_items = {}
    for iid, item in items.items():
        title = (item.get("name") or "").strip()
        if not title:
            continue
        if not _is_visible(PROVIDER["id"], title):
            print(f"  hidden via activity_controls: {title!r}")
            continue
        cat = (item.get("category") or "").lower()
        if cat in EXCLUDE_CATEGORIES:
            print(f"  excluding non-course category {cat!r}: {title!r}")
            continue
        if KEEP_CATEGORIES and cat not in KEEP_CATEGORIES:
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

    # Spot-count semantics are decided per item, not globally — see
    # scraper_utils.detect_checkfront_spot_counts. The probe runs inside the
    # per-item loop below.

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

        # Location — title keyword → default → normalise_location
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

        # Per-item spot-count probe: only trust integer values when THIS item
        # has at least one date with >1 spots. Items returning 0/1 only are
        # using the binary availability flag and must report spots_remaining=None.
        item_has_spot_counts = detect_checkfront_spot_counts(item_cal)

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

            # Real spot count if THIS item has integer counts; else None ("open").
            spots_remaining = None
            if item_has_spot_counts:
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

    # 5. Summaries — dedup by title (all dates of same course share the summary)
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

    # 6. Strip description (not a courses column)
    for r in rows:
        r.pop("description", None)

    # 7. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    # Log intelligence (V2 — append-only, change-detected)
    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
