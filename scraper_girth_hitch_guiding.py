#!/usr/bin/env python3
"""
Scraper: Girth Hitch Guiding (girth-hitch-guiding)
Platform: Checkfront Public API v3.0 at girth-hitch-guiding.checkfront.com

Provider offers rock / ice / alpine / via ferrata climbing + guided peaks
out of Nordegg, AB with satellite operations in Bow Valley, Bugaboos,
Jasper, Yoho, and Squamish.

API + parsing logic lives in scraper_checkfront_utils. This file is
provider-config + main() orchestration only — same pattern as the Zaui
tenant scrapers calling scraper_zaui_utils.

Endpoints used (all public, no credentials required):
  GET /api/3.0/item                                — full catalogue
  GET /api/3.0/item/cal?item_id[]=…&start_date=…   — date discovery
  GET /api/3.0/item/{id}?start_date=…&end_date=…   — rated (price + stock)
"""

import re
import datetime

from scraper_utils import (
    sb_upsert, stable_id_v2,
    log_availability_change, log_price_change,
    update_provider_ratings, update_provider_shared_utils,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    spots_to_avail, append_utm,
    detect_checkfront_spot_counts,
    title_hash,
    activity_key, upsert_activity_control, load_activity_controls,
)
from scraper_checkfront_utils import (
    fetch_catalog, fetch_calendar, fetch_rated_price_sampled,
    parse_rated_price,
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
# Empty default means "keep all" — safer than over-filtering a small catalog.
KEEP_CATEGORIES: set = set()

# Hardcoded skip list — these item_ids cause Checkfront's /item/cal to
# 500 with no recovery (verified via PRs #55-#57). Most likely
# archived/misconfigured products on the tenant. Skip entirely so the
# scraper doesn't waste API calls on the per-item fallback. Re-evaluate
# when the provider confirms a fix on their side.
BROKEN_ITEM_IDS = {8, 14, 20, 134, 143}

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


# ── Activity-controls visibility gate ─────────────────────────────────────────
# Per-title exclusions live in activity_controls — hide a title by flipping
# visible=false in the admin Activity Tracking tab. Seeded from the historical
# list via seed_activity_controls.py.
_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="checkfront",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🧗 {PROVIDER['name']} — Checkfront API scraper")

    # Places rating
    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        print(f"  Places update failed: {e}")

    update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))

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
    items = fetch_catalog(CF_BASE)
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

    # 3. Fetch availability calendar (date discovery + binary availability fallback)
    print(f"  Fetching availability {start_s} → {end_s}...")
    item_ids = [i for i in course_items.keys() if int(i) not in BROKEN_ITEM_IDS]
    cal = fetch_calendar(CF_BASE, item_ids, start_s, end_s)
    print(f"  Calendar entries returned: {len(cal)}")

    # 4. Sampled rated-price discovery. Three scope-limits keep wall-time
    # bounded on this flaky tenant:
    #   (a) Only sample items that succeeded on the calendar pass — items
    #       that 500'd on /item/cal will almost certainly fail on
    #       /item/{id} too (same upstream-data root cause).
    #   (b) Sampled (1-day) requests instead of full-window 180-day rated
    #       requests — Checkfront computes rates across every date × every
    #       customer type combination, so a 180-day window times out at 15s
    #       on this tenant. A 1-day window with param[guests]=1 returns in
    #       a fraction of a second.
    #   (c) attempts=1 in fetch_rated_price_sampled (the helper's default)
    #       and first-failure short-circuit — if today's sample times out,
    #       remaining offsets are skipped for that item.
    rated_eligible = [iid for iid in item_ids if str(iid) in cal]
    skipped_due_to_cal_failure = len(item_ids) - len(rated_eligible)
    print(f"  Sampling rated prices at +0/+30/+90/+150 day offsets "
          f"({len(rated_eligible)} items; "
          f"{skipped_due_to_cal_failure} skipped due to calendar 500s)...")
    price_by_item: dict = {}     # {item_id_str: int}
    rated_failed: list = []
    for item_id in rated_eligible:
        samples = fetch_rated_price_sampled(
            CF_BASE, item_id, start_s,
            lookahead_days=LOOKAHEAD_DAYS,
        )
        valid = {k: v for k, v in samples.items() if v is not None}
        if not valid:
            rated_failed.append(item_id)
            continue
        unique_prices = set(valid.values())
        if len(unique_prices) > 1:
            print(f"    ⚠ item {item_id}: price varies across samples: {valid} — using earliest")
        # Earliest sample (today's date by construction) is canonical
        price_by_item[str(item_id)] = sorted(valid.items())[0][1]
    if rated_failed:
        print(f"  ⚠ {len(rated_failed)} item(s) failed sampled rated fetch; "
              f"those will fall back to catalog price (likely None)")
    print(f"  Rated prices captured for {len(price_by_item)}/{len(rated_eligible)} eligible items")

    # 5. Build rows
    rows = []
    skipped = 0

    for item_id, item in course_items.items():
        title = item.get("name", "").strip()

        # Price preference: sampled rated response (authoritative) →
        # catalog scalar/dict fallback (unreliable on this tenant —
        # usually missing). The sampled helper canonicalizes one price
        # per item; row-level price is therefore the same across all
        # dates for that item, which is correct for Checkfront's flat
        # rate-per-product pricing.
        price = price_by_item.get(str(item_id))
        if price is None:
            # Catalog fallback for the rare item where every rated
            # sample failed (usually a tenant-side 500/timeout).
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

        # Per-date stock counts come from the calendar response only.
        # The sampled rated helper doesn't fetch stock (1-day windows would
        # only cover the sample dates anyway). detect_checkfront_spot_counts
        # is the per-item heuristic: items that ever return a value > 1 in
        # the calendar dict are interpreted as real counts; binary-flag
        # items report spots_remaining=None ("open" until sold).
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

            # Spot count resolution:
            # 1. Calendar integer if detect_checkfront_spot_counts says safe
            # 2. None (binary-flag product, avail='open' until sold)
            spots_remaining = None
            rated_stock_for_date = rated_stock.get(date_key) or {}
            if rated_stock_for_date.get("available") is not None:
                spots_remaining = rated_stock_for_date["available"]
            elif item_has_spot_counts:
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

    # 6. Summaries — dedup by title (all dates of same course share the summary)
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

    # 7. Strip description (not a courses column)
    for r in rows:
        r.pop("description", None)

    # 8. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    # Log intelligence (V2 — append-only, change-detected)
    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
