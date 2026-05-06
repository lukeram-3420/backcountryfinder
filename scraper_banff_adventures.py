#!/usr/bin/env python3
"""scraper_banff_adventures.py — Zaui scraper for Banff Adventures.

Platform: Zaui booking API at https://banffadventures.zaui.net/booking/api
All read endpoints are anonymous — no auth, no CSRF, no Playwright required.

Banff Adventures is a multi-location aggregator (Banff / Lake Louise / Canmore /
Golden / Kootenay / Yoho) reselling winter sports, sightseeing, wildlife, and
adventure packages from a Zaui tenant. Catalog is large enough to need
grouping — runs accept --group N (0..3) and process every Nth activity.

Flow:
  1. update_provider_ratings + load location mappings.
  2. fetch_categories (excluding Rentals) + fetch_activity_list per category.
  3. Split all activities into 4 interleaved groups; process only --group N.
  4. Per activity: walk 7-day windows across the per-activity lookahead
     (immediate/extended via activity_controls.tracking_mode), accumulate
     fetchUnavailability blackouts, compute bookable dates from the weekly
     template minus blackouts.
  5. Emit one courses row per (activity, bookable date).
  6. Batch Claude summaries (deduped by title).
  7. Upsert + log availability + price changes.
"""

import argparse
import datetime
import logging
import re

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_upsert, stable_id_v2,
    update_provider_ratings, update_provider_shared_utils,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    activity_key, bulk_upsert_activity_controls, load_activity_controls,
    load_lookahead_windows,
    UTM,
)
from scraper_zaui_utils import (
    fetch_categories, fetch_activity_list, fetch_unavailability,
    compute_bookable_dates, get_activity_group,
    is_experience_product, extract_zaui_price,
)

PROVIDER = {
    "id":               "banff-adventures",
    "name":             "Banff Adventures",
    "website":          "https://www.banffadventures.com",
    "tenant_slug":      "banffadventures",
    "portal_id":        1,
    "default_location": "Banff, AB",
    "shared_utils_module": "scraper_zaui_utils",
}

BOOKING_URL_PATTERN = "https://banffadventures.zaui.net/booking/web/?{utm}#/default/activity/{id}"

WINDOW_DAYS = 7
TOTAL_GROUPS = 4

# Per-activity visibility + tracking-mode lookahead now live in
# `activity_controls`. The shared structural filters in
# scraper_zaui_utils.is_experience_product (hotels / transfers / rentals /
# categories) are kept as code since they're domain-invariant.

# Title-keyword pre-resolution hint. First match wins; result is fed to
# normalise_location() as the raw input (not as a bypass) so unknown mappings
# still surface in pending_location_mappings for admin review (Initiative 2).
LOCATION_MAP = [
    ("lake louise",      "Lake Louise, AB"),
    ("moraine lake",     "Lake Louise, AB"),
    ("canmore",          "Canmore, AB"),
    ("kananaskis",       "Kananaskis, AB"),
    ("golden",           "Golden, BC"),
    ("kootenay",         "Kootenay National Park, BC"),
    ("paint pots",       "Kootenay National Park, BC"),
    ("marble canyon",    "Kootenay National Park, BC"),
    ("yoho",             "Field, BC"),
    ("emerald lake",     "Field, BC"),
    ("jasper",           "Jasper, AB"),
    ("icefields",        "Jasper, AB"),
    ("columbia icefield", "Jasper, AB"),
    ("sunshine",         "Banff, AB"),
    ("johnston canyon",  "Banff, AB"),
    ("lake minnewanka",  "Banff, AB"),
    ("bow valley",       "Banff, AB"),
    ("banff",            "Banff, AB"),
]

# Zaui activity fields that may carry a human-readable location string.
# Probed in order; first non-empty wins. No documented schema — probe is defensive.
ZAUI_LOCATION_FIELDS = ("location", "meetingLocation", "address", "venue", "city")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def resolve_location_raw(title: str, act: dict) -> str:
    """Pick the raw location string to pass into normalise_location().

    Priority:
      1. Title keyword match in LOCATION_MAP (strongest signal — titles like
         "Lake Louise Sightseeing" unambiguously identify the location).
      2. Zaui activity location-ish field (whatever the API exposes).
      3. PROVIDER["default_location"] (last-resort guess).
    """
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

    update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))

    # Mappings
    loc_mappings = load_location_mappings()
    log.info(f"Loaded {len(loc_mappings)} location mappings")

    # Activity tracking — visibility + per-activity lookahead window.
    controls = load_activity_controls(PROVIDER["id"])
    windows  = load_lookahead_windows()
    max_lookahead = max(windows["extended"], windows["immediate"])
    log.info(f"Loaded {len(controls)} activity controls; windows={windows}")

    today      = datetime.date.today()
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Categories (Rentals excluded by helper default)
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
            if not is_experience_product(title, cat_name):
                log.info(f"  excluding non-experience: {title!r} (cat={cat_name!r})")
                continue
            seen_ids.add(aid)
            a["_category_name"] = cat_name
            all_activities.append(a)
    log.info(f"Total unique activities: {len(all_activities)}")

    # Batch-upsert every discovered activity into activity_controls so the
    # admin can see them in the Activity Tracking tab on the next page load.
    # Idempotent — merge-duplicates on (provider_id, activity_key) preserves
    # admin-edited visible/tracking_mode columns on existing rows.
    control_rows = []
    for a in all_activities:
        aid = a.get("id")
        title = (a.get("name") or "").strip()
        if not aid or not title:
            continue
        control_rows.append({
            "provider_id":  PROVIDER["id"],
            "activity_key": activity_key("zaui", aid, title),
            "title":        title,
            "upstream_id":  aid,
            "platform":     "zaui",
        })
    bulk_upsert_activity_controls(control_rows)

    # 3. Pick this run's group
    group_acts = get_activity_group(all_activities, group, TOTAL_GROUPS)
    log.info(f"Group {group}: {len(group_acts)} activities to scrape")

    # 4. Per activity: walk 7-day windows → blackouts → bookable dates → rows
    rows = []
    hidden_count = 0
    for act in group_acts:
        aid   = act.get("id")
        title = (act.get("name") or "").strip()
        if not aid or not title:
            continue

        # Activity Tracking gate — skip hidden activities before any expensive
        # work. Default for first-seen activities is visible=true; scraper
        # pays one fetch_unavailability cost cycle until admin flips it.
        akey = activity_key("zaui", aid, title)
        ctrl = controls.get(akey, {"visible": True, "tracking_mode": "immediate"})
        if ctrl.get("visible") is False:
            hidden_count += 1
            continue
        # Per-activity lookahead pick — drives the fetch_unavailability window
        # walk. 'immediate' (14d default) vs 'extended' (180d default).
        tmode = ctrl.get("tracking_mode") or "immediate"
        act_lookahead = windows["extended" if tmode == "extended" else "immediate"]
        end_date = today + datetime.timedelta(days=act_lookahead)

        description = html_to_text(act.get("description") or act.get("shortDescription") or "")

        _p = extract_zaui_price(act)
        price = _p["price"]
        price_tier = _p["tier"]
        price_has_variations = _p["has_variations"]

        # Image URL (may be absolute CDN URL or relative)
        image_url = act.get("image") or None
        if image_url and image_url.startswith("/"):
            image_url = f"https://{PROVIDER['tenant_slug']}.zaui.net{image_url}"

        # Location: title keyword → Zaui field → default → normalise_location
        loc_raw = resolve_location_raw(title, act)
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        # Booking URL with UTM in query + hash route for the Vue SPA
        booking_url = BOOKING_URL_PATTERN.format(utm=UTM, id=aid)

        duration_days = act.get("durationDays") or None
        if duration_days == 0:
            duration_days = None

        category_name = act.get("_category_name") or ""

        # Private Guiding / on-demand bookings don't run on a schedule — emit
        # one flexible-dates card instead of dated rows.
        if category_name in ("Private Guiding", "Private Tours", "Custom Tours"):
            log.info(f"  [{aid}] {title!r}: on-demand category → 1 flexible-dates row")
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
                "booking_mode":       "request",
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

        # Walk unavailability 7 days at a time across the lookahead window,
        # then compute bookable dates from the weekly template minus blackouts.
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
            date_iso = d.isoformat()
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

    log.info(f"Built {len(rows)} course-date rows ({hidden_count} activities hidden via activity_controls)")

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
                }
        if by_title:
            try:
                summaries = generate_summaries_batch(
                    list(by_title.values()), provider_id=PROVIDER["id"],
                )
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
