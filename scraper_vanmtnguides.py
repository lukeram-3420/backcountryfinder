#!/usr/bin/env python3
"""scraper_vanmtnguides.py — Zaui scraper for Vancouver Mountain Guides.

Platform: Zaui booking API at https://vanmtnguides.zaui.net/booking/api
All read endpoints are anonymous — no auth, no CSRF, no Playwright required.

Flow:
  1. update_provider_ratings + load location/activity mappings.
  2. fetch_categories (excluding Rentals) + fetch_activity_list per category.
  3. Split all activities into 4 interleaved groups; process only --group N.
  4. Per activity: walk 7-day windows across LOOKAHEAD_DAYS, accumulate
     fetchUnavailability blackouts, compute bookable dates from the weekly
     template minus blackouts.
  5. Emit one courses row per (activity, bookable date).
  6. Batch Claude summaries (deduped by title).
  7. Upsert.
"""

import argparse
import datetime
import hashlib
import logging
import re

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_upsert, stable_id_v2,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    load_activity_mappings, resolve_activity, build_badge,
    generate_summaries_batch,
    UTM,
)
from scraper_zaui_utils import (
    fetch_categories, fetch_activity_list, fetch_unavailability,
    compute_bookable_dates, get_activity_group,
)

PROVIDER = {
    "id":               "vanmtnguides",
    "name":             "Vancouver Mountain Guides",
    "website":          "https://www.vanmtnguides.com",
    "tenant_slug":      "vanmtnguides",
    "portal_id":        1,
    "default_location": "Squamish, BC",
}

BOOKING_URL_PATTERN = "https://vanmtnguides.zaui.net/booking/web/?{utm}#/default/activity/{id}"

LOOKAHEAD_DAYS = 180
WINDOW_DAYS = 7
TOTAL_GROUPS = 4

# Title-keyword → canonical location_raw. First match wins; no match falls
# through to PROVIDER["default_location"].
LOCATION_MAP = [
    ("bugaboo",         "Bugaboos, BC"),
    ("slesse",          "Chilliwack, BC"),
    ("nesakwatch",      "Chilliwack, BC"),
    ("marble canyon",   "Lillooet, BC"),
    ("lighthouse park", "West Vancouver, BC"),
    ("blackcomb",       "Whistler, BC"),
    ("whistler",        "Whistler, BC"),
    ("garibaldi",       "Squamish, BC"),
    ("chief",           "Squamish, BC"),
    ("tantalus",        "Tantalus Range, BC"),
    ("sky pilot",       "Squamish, BC"),
    ("squamish",        "Squamish, BC"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def resolve_location_raw(title: str) -> str:
    t = (title or "").lower()
    for kw, loc in LOCATION_MAP:
        if kw in t:
            return loc
    return PROVIDER["default_location"]


def make_id(provider_id: str, activity: str, date_iso: str, item_id, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:20]
    if not slug:
        slug = hashlib.md5((title or "").encode()).hexdigest()[:6]
    return f"{provider_id}-{activity}-{date_iso}-{item_id}-{slug}"


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
    activity_maps = load_activity_mappings()
    log.info(f"Loaded {len(loc_mappings)} location mappings, {len(activity_maps)} activity mappings")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Categories (Rentals excluded)
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
            seen_ids.add(aid)
            a["_category_name"] = cat.get("name") or ""
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

        # Price: prefer listPrice, then price.adults
        raw_price = act.get("listPrice")
        if not raw_price:
            p = act.get("price") or {}
            if isinstance(p, dict):
                raw_price = p.get("adults")
        try:
            price = int(raw_price) if raw_price else None
        except (ValueError, TypeError):
            price = None

        # Image URL (may be absolute CDN URL or relative)
        image_url = act.get("image") or None
        if image_url and image_url.startswith("/"):
            image_url = f"https://{PROVIDER['tenant_slug']}.zaui.net{image_url}"

        # Activity classification (three-tier via scraper_utils)
        activity_canonical = resolve_activity(title, description, activity_maps) or "guided"

        # Location: title keyword → raw → canonical via scraper_utils
        loc_raw = resolve_location_raw(title)
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        # Booking URL with UTM in query + hash route for the Vue SPA
        booking_url = BOOKING_URL_PATTERN.format(utm=UTM, id=aid)

        duration_days = act.get("durationDays") or None
        if duration_days == 0:
            duration_days = None
        badge = build_badge(activity_canonical, duration_days)

        category_name = act.get("_category_name") or ""

        # Private Guiding courses don't run on a schedule — guides are booked
        # on-demand. Emit one flexible-dates card instead of dated rows.
        if category_name == "Private Guiding":
            log.info(f"  [{aid}] {title!r}: private-guiding → 1 flexible-dates row")
            course_id = stable_id_v2(PROVIDER["id"], None, title)
            rows.append({
                "id":                 course_id,
                "title":              title,
                "provider_id":        PROVIDER["id"],
                "activity":           activity_canonical,
                "activity_raw":       category_name,
                "activity_canonical": None,  # V2: null hides from V1 frontend
                "badge":              badge,
                "badge_canonical":    badge,
                "location_raw":       loc_raw,
                "location_canonical": loc_canonical,
                "date_sort":          None,
                "date_display":       "Flexible dates",
                "duration_days":      duration_days,
                "price":              price,
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "custom_dates":       True,
                "booking_url":        booking_url,
                "image_url":          image_url,
                "summary":            "",
                "description":        description or None,
                "scraped_at":         scraped_at,
            })
            continue

        # All other categories — walk unavailability 7 days at a time across
        # the full lookahead window, then compute bookable dates from the
        # weekly availability template minus blackouts.
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

        # Emit one row per bookable date
        for d in bookable:
            date_iso = d.isoformat()
            course_id = stable_id_v2(PROVIDER["id"], date_iso, title)
            rows.append({
                "id":                 course_id,
                "title":              title,
                "provider_id":        PROVIDER["id"],
                "activity":           activity_canonical,
                "activity_raw":       category_name,
                "activity_canonical": None,  # V2: null hides from V1 frontend
                "badge":              badge,
                "badge_canonical":    badge,
                "location_raw":       loc_raw,
                "location_canonical": loc_canonical,
                "date_sort":          date_iso,
                "date_display":       d.strftime("%b %-d, %Y"),
                "duration_days":      duration_days,
                "price":              price,
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "custom_dates":       False,
                "booking_url":        booking_url,
                "image_url":          image_url,
                "summary":            "",
                "description":        description or None,
                "scraped_at":         scraped_at,
            })

    log.info(f"Built {len(rows)} course-date rows")

    # 5. Summaries — dedup by title (all dates of same activity share the summary)
    if rows:
        by_title = {}
        for r in rows:
            if r["description"] and r["title"] not in by_title:
                by_title[r["title"]] = {
                    "id":          r["title"],
                    "title":       r["title"],
                    "description": r["description"],
                    "provider":    PROVIDER["name"],
                    "activity":    r["activity_canonical"],
                }
        if by_title:
            try:
                summaries = generate_summaries_batch(list(by_title.values()))
                log.info(f"Generated {len(summaries)} summaries")
                for r in rows:
                    result = summaries.get(r["title"])
                    if result:
                        r["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                log.warning(f"Summary batch failed: {e}")

    # 6. Dedup by stable id (safety); strip description (not a courses column)
    seen_id = set()
    final = []
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

    # Log intelligence (V2 — append-only, change-detected)
    for c in final:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
