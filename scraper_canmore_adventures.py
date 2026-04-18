#!/usr/bin/env python3
"""scraper_canmore_adventures.py — Zaui scraper for Canmore Adventures.

Platform: Zaui booking API at https://canmoreadventures.zaui.net/booking/api
All read endpoints are anonymous — no auth, no CSRF, no Playwright required.

Mirrors scraper_vanmtnguides.py / scraper_mt_norquay.py. Grouped scraper —
accepts --group N (0..3). Shared helpers now multi-date probe internally so
seasonal categories surface in shoulder seasons.
"""

import argparse
import datetime
import logging
import re

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_upsert, stable_id_v2,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    UTM,
)
from scraper_zaui_utils import (
    fetch_categories, fetch_activity_list, fetch_unavailability,
    compute_bookable_dates, get_activity_group,
    is_experience_product, extract_zaui_price,
)

PROVIDER = {
    "id":               "canmore-adventures",
    "name":             "Canmore Adventures",
    "website":          "https://www.canmoreadventures.com/",
    "tenant_slug":      "canmoreadventures",
    "portal_id":        1,
    "default_location": "Canmore, AB",
}

BOOKING_URL_PATTERN = "https://canmoreadventures.zaui.net/booking/web/?{utm}#/default/activity/{id}"

LOOKAHEAD_DAYS = 180
WINDOW_DAYS    = 7
TOTAL_GROUPS   = 4

# Canmore Adventures operates in the Bow Valley / Banff National Park region.
# Title-keyword location hints. First match wins; result passes through
# normalise_location() so unknown strings still queue to pending_location_mappings.
LOCATION_MAP = [
    ("canmore",       "Canmore, AB"),
    ("banff",         "Banff, AB"),
    ("lake louise",   "Lake Louise, AB"),
    ("moraine lake",  "Lake Louise, AB"),
    ("johnston",      "Banff, AB"),
    ("sunshine",      "Banff, AB"),
    ("kananaskis",    "Kananaskis, AB"),
    ("yoho",          "Field, BC"),
    ("jasper",        "Jasper, AB"),
    ("icefields",     "Jasper, AB"),
    ("columbia",      "Jasper, AB"),
]

ZAUI_LOCATION_FIELDS = ("location", "meetingLocation", "address", "venue", "city")

# Provider-specific title exclusions to layer on top of the shared Zaui
# defaults in is_experience_product(). The shared filter already covers
# gift cards, deposits, memberships, rentals, season passes, lift tickets,
# merchandise, and add-ons — add entries here only if Canmore Adventures
# carries a non-experience product the shared set doesn't catch.
EXTRA_EXCLUDE_TITLES: list = []

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def resolve_location_raw(title: str, act: dict) -> str:
    """Pick the raw location string to pass into normalise_location()."""
    t = (title or "").lower()
    for kw, loc in LOCATION_MAP:
        if kw in t:
            return loc
    for fld in ZAUI_LOCATION_FIELDS:
        v = act.get(fld)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return PROVIDER["default_location"]


def html_to_text(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"<[^>]+>", " ", html).replace("\u00a0", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", type=int, default=0, help="Activity group 0..3 to scrape this run")
    args = ap.parse_args()
    group = args.group % TOTAL_GROUPS

    log.info(f"🏔 {PROVIDER['name']} — Zaui scraper (group={group}/{TOTAL_GROUPS-1})")

    # Places rating
    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        log.warning(f"Places update failed: {e}")

    # Mappings
    loc_mappings = load_location_mappings()
    log.info(f"Loaded {len(loc_mappings)} location mappings")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Categories (Rentals excluded). Shared helper probes multiple dates
    # so seasonal categories surface in shoulder seasons.
    cats = fetch_categories(PROVIDER["tenant_slug"], PROVIDER["portal_id"])
    log.info(f"Categories ({len(cats)}): " +
             ", ".join(f"{c.get('id')}={c.get('name')!r}:{c.get('totalActivities')}" for c in cats))

    # 2. All activities across in-scope categories, deduped by id
    all_activities = []
    seen_ids = set()
    for cat in cats:
        try:
            items = fetch_activity_list(PROVIDER["tenant_slug"], PROVIDER["portal_id"], cat["id"])
        except Exception as e:
            log.warning(f"activity/list failed for category {cat.get('id')}: {e}")
            continue
        log.info(f"  cat {cat['id']} {cat.get('name')!r}: {len(items)} activities")
        for a in items:
            aid = a.get("id")
            if aid is None or aid in seen_ids:
                continue
            title = (a.get("name") or "").strip()
            cat_name = cat.get("name") or ""
            if not is_experience_product(title, cat_name, EXTRA_EXCLUDE_TITLES):
                log.info(f"  excluding non-experience: {title!r} (cat={cat_name!r})")
                continue
            seen_ids.add(aid)
            a["_category_name"] = cat_name
            all_activities.append(a)
    log.info(f"Total unique activities: {len(all_activities)}")

    # 3. Pick this run's group
    group_acts = get_activity_group(all_activities, group, TOTAL_GROUPS)
    log.info(f"Group {group}: {len(group_acts)} activities to scrape")

    # 4. Per activity: walk 7-day windows → blackouts → bookable dates → rows
    rows = []
    for act in group_acts:
        aid   = act.get("id")
        title = (act.get("name") or "").strip()
        if not aid or not title:
            continue

        description = html_to_text(act.get("description") or act.get("shortDescription") or "")

        _p = extract_zaui_price(act)
        price = _p["price"]
        price_tier = _p["tier"]
        price_has_variations = _p["has_variations"]

        # Image URL
        image_url = act.get("image") or None
        if image_url and image_url.startswith("/"):
            image_url = f"https://{PROVIDER['tenant_slug']}.zaui.net{image_url}"

        loc_raw       = resolve_location_raw(title, act)
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        booking_url = BOOKING_URL_PATTERN.format(utm=UTM, id=aid)

        duration_days = act.get("durationDays") or None
        if duration_days == 0:
            duration_days = None

        category_name = act.get("_category_name") or ""

        # Private/on-demand categories: emit flex-dates card.
        if category_name.lower() in ("private guiding", "private", "private tours"):
            log.info(f"  [{aid}] {title!r}: private → 1 flex row")
            course_id = stable_id_v2(PROVIDER["id"], None, title)
            row = {
                "id":                 course_id,
                "title":              title,
                "provider_id":        PROVIDER["id"],
                "location_raw":       loc_raw,
                "date_sort":          None,
                "date_display":       "Flexible dates",
                "duration_days":      duration_days,
                "price":              price,
                "price_tier":         price_tier,
                "price_has_variations": price_has_variations,
                "currency":           "CAD",
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "custom_dates":       True,
                "booking_url":        booking_url,
                "image_url":          image_url,
                "summary":            "",
                "search_document":    "",
                "description":        description or None,
                "scraped_at":         scraped_at,
            }
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            rows.append(row)
            continue

        # Walk unavailability 7 days at a time across the lookahead window.
        blackouts = set()
        cur = today
        while cur <= end_date:
            try:
                arr = fetch_unavailability(
                    PROVIDER["tenant_slug"], PROVIDER["portal_id"],
                    aid, cur, WINDOW_DAYS,
                )
                for s in arr or []:
                    blackouts.add(s)
            except Exception as e:
                log.warning(f"fetchUnavailability failed id={aid} date={cur}: {e}")
            cur += datetime.timedelta(days=WINDOW_DAYS)

        bookable = compute_bookable_dates(act, blackouts, start_date=today, end_date=end_date)
        log.info(f"  [{aid}] {title!r}: {len(bookable)} bookable / {len(blackouts)} blackouts")
        if not bookable:
            continue

        for d in bookable:
            date_iso  = d.isoformat()
            course_id = stable_id_v2(PROVIDER["id"], date_iso, title)
            row = {
                "id":                 course_id,
                "title":              title,
                "provider_id":        PROVIDER["id"],
                "location_raw":       loc_raw,
                "date_sort":          date_iso,
                "date_display":       d.strftime("%b %-d, %Y"),
                "duration_days":      duration_days,
                "price":              price,
                "price_tier":         price_tier,
                "price_has_variations": price_has_variations,
                "currency":           "CAD",
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "custom_dates":       False,
                "booking_url":        booking_url,
                "image_url":          image_url,
                "summary":            "",
                "search_document":    "",
                "description":        description or None,
                "scraped_at":         scraped_at,
            }
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            rows.append(row)

    log.info(f"Built {len(rows)} course-date rows")

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
                log.info(f"Generated {len(summaries)} summaries")
                for r in rows:
                    result = summaries.get(r["title"])
                    if result:
                        r["summary"]         = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                log.warning(f"Summary batch failed: {e}")

    # 6. Dedup by stable id; strip description (not a courses column)
    seen_id = set()
    final   = []
    for r in rows:
        if r["id"] in seen_id:
            continue
        seen_id.add(r["id"])
        r.pop("description", None)
        final.append(r)

    # 7. Upsert in batches of 50
    for i in range(0, len(final), 50):
        sb_upsert("courses", final[i:i + 50])
    log.info(f"✅ Upserted {len(final)} rows")

    # Intelligence logging
    for c in final:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
