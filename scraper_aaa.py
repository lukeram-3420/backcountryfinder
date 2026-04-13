#!/usr/bin/env python3
"""
Scraper: Alpine Air Adventures (aaa)
Platform: Checkfront Public API v3.0 (no auth required — public API enabled)
Endpoints used:
  GET /api/3.0/item          — full item catalogue
  GET /api/3.0/item/cal      — availability bitmap by date
"""

import os
import re
import datetime
import requests

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "aaa",
    "name":     "Alpine Air Adventures",
    "website":  "https://alpineairadventures.com/",
    "location": "Banff, AB",
}

CF_BASE        = "https://alpineair.checkfront.com/api/3.0"
BOOKING_URL    = "https://alpineair.checkfront.com/reserve/"
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_KEY     = os.environ["RESEND_API_KEY"]
GOOGLE_KEY     = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NOTIFY_EMAIL   = "luke@backcountryfinder.com"

LOOKAHEAD_DAYS = 180

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates",
}

CF_HEADERS = {
    "X-On-Behalf": "Off",
}

# ── Keep only real course categories ─────────────────────────────────────────
KEEP_CATEGORIES = {
    "alpine climbing",
    "avalanche safety training",
    "backcountry riding",
    "backcountry skiing",
    "hiking & trekking",
    "ice climbing",
    "rock climbing",
}

# ── Activity resolution ───────────────────────────────────────────────────────
ACTIVITY_MAP = [
    (["ast", "avalanche", "companion rescue", "crevasse"],        "skiing"),
    (["ice climbing"],                                             "climbing"),
    (["rock climbing", "trad", "lead", "rappel", "rope rescue", "via ferrat",
      "weekend warrior", "climb the ghost"],                      "climbing"),
    (["mountaineering", "mountaineer", "alpine", "athabasca", "victoria",
      "andromeda", "logan", "bugaboos", "fay", "huber",
      "mountain skills week", "alberta high"],                    "mountaineering"),
    (["ski touring", "splitboard", "backcountry ski", "ski camp",
      "ski traverse", "wapta", "bow yoho", "rogers pass",
      "spring rockies", "off piste", "bugs to rogers",
      "waddington", "ski and ride"],                              "skiing"),
    (["hiking", "trekking", "scramble", "temple", "sulphur",
      "larch", "o'hara", "six glaciers", "castle mountain",
      "moraine lake", "plain of 6"],                             "hiking"),
]

LOCATION_MAP = [
    ("rogers pass",   "Rogers Pass, BC"),
    ("bugs to rogers","Rogers Pass, BC"),
    ("bugaboos",      "Bugaboos, BC"),
    ("waddington",    "Waddington, BC"),
    ("tantalus",      "Tantalus Range, BC"),
    ("selkirk",       "Revelstoke, BC"),
    ("kananaskis",    "Kananaskis, AB"),
    ("lake louise",   "Lake Louise, AB"),
    ("bow yoho",      "Banff, AB"),
    ("wapta",         "Banff, AB"),
    ("jasper",        "Jasper, AB"),
    ("canmore",       "Canmore, AB"),
    ("ghost",         "Canmore, AB"),
]

def resolve_activity(title: str) -> str:
    t = title.lower()
    for keywords, activity in ACTIVITY_MAP:
        if any(k in t for k in keywords):
            return activity
    return "guided"

def resolve_location(title: str) -> str:
    t = title.lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]

# ── Supabase ──────────────────────────────────────────────────────────────────
def sb_upsert(table, rows):
    if not rows:
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=SUPABASE_HEADERS,
        json=rows
    )
    if not r.ok:
        print(f"  ⚠ Supabase error {r.status_code}: {r.text[:300]}")
    r.raise_for_status()

# ── Google Places ─────────────────────────────────────────────────────────────
def find_place_id(location: str) -> str | None:
    if not GOOGLE_KEY:
        return None
    city = re.split(r"[/,]", location)[0].strip()
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
        params={"input": city, "inputtype": "textquery",
                "fields": "place_id", "key": GOOGLE_KEY}
    )
    candidates = r.json().get("candidates", [])
    return candidates[0]["place_id"] if candidates else None

# ── Checkfront API ────────────────────────────────────────────────────────────
def cf_get(endpoint, params=None):
    r = requests.get(
        f"{CF_BASE}/{endpoint}",
        params=params,
        headers=CF_HEADERS,
        timeout=15
    )
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

# ── Email summary ─────────────────────────────────────────────────────────────
def send_summary(upserted: int, skipped: int):
    body = (
        f"<h2>Alpine Air Adventures scrape complete</h2>"
        f"<p>Upserted <strong>{upserted}</strong> course-date rows · "
        f"skipped <strong>{skipped}</strong>.</p>"
        f"<p>{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</p>"
    )
    requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}",
                 "Content-Type": "application/json"},
        json={"from":    "scraper@backcountryfinder.com",
              "to":      NOTIFY_EMAIL,
              "subject": "✅ Scraper — Alpine Air Adventures",
              "html":    body}
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🏔 Alpine Air Adventures — Checkfront API scraper")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    start_s    = today.strftime("%Y%m%d")
    end_s      = end_date.strftime("%Y%m%d")
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Fetch item catalogue
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    course_items = {
        iid: item for iid, item in items.items()
        if item.get("category", "").lower() in KEEP_CATEGORIES
    }
    print(f"  {len(course_items)} course items after filtering")

    # 2. Fetch availability calendar
    print(f"  Fetching availability {start_s} → {end_s}...")
    item_ids = list(course_items.keys())
    cal = fetch_availability(item_ids, start_s, end_s)
    print(f"  Calendar entries returned: {len(cal)}")

    # 3. Build rows
    rows = []
    skipped = 0

    for item_id, item in course_items.items():
        title = item.get("name", "").strip()
        if not title:
            skipped += 1
            continue

        # Price
        price_raw = item.get("price")
        if isinstance(price_raw, dict):
            try:
                price = int(float(next(iter(price_raw.values()))))
            except (StopIteration, ValueError):
                price = None
        else:
            try:
                price = int(float(price_raw)) if price_raw else None
            except (ValueError, TypeError):
                price = None

        activity = resolve_activity(title)
        location = resolve_location(title)
        category = item.get("category", "")

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
                    int(date_key[6:8])
                )
            except ValueError:
                continue

            date_sort    = d.isoformat()
            date_display = d.strftime("%b %-d, %Y")
            course_id    = make_id(PROVIDER["id"], activity, date_key,
                                   item_id, title)
            booking_url  = (
                f"{BOOKING_URL}?item_id={item_id}&start_date={date_key}"
                f"&utm_source=backcountryfinder&utm_medium=referral"
            )

            rows.append({
                "id":                 course_id,
                "provider_id":        PROVIDER["id"],
                "title":              title,
                "activity":           activity,
                "activity_raw":       category,
                "activity_canonical": activity,
                "location_raw":       location,
                "location_canonical": location,
                "date_sort":          date_sort,
                "date_display":       date_display,
                "duration_days":      item.get("len", 1),
                "price":              price,
                "spots_remaining":    None,
                "avail":              "open",
                "active":             True,
                "booking_url":        booking_url,
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
    send_summary(len(rows), skipped)


if __name__ == "__main__":
    main()
