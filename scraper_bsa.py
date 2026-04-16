#!/usr/bin/env python3
"""
Scraper: Black Sheep Adventure (bsa)
Site:    https://blacksheepadventure.ca
Platform: Custom WordPress — static HTML dates, custom booking form
Approach: BeautifulSoup + browser UA spoof (site blocks non-browser UAs)
"""

import os
import re
import json
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date

from scraper_utils import (
    log_availability_change, log_price_change,
    stable_id_v2,
    sb_upsert, sb_patch, sb_get,
    send_email, append_utm,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, ANTHROPIC_API_KEY,
    GOOGLE_PLACES_API_KEY, UTM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":       "bsa",
    "name":     "Black Sheep Adventure",
    "website":  "https://blacksheepadventure.ca",
    "location": "Squamish, BC",
}

# Course listing pages to crawl
LISTING_PAGES = [
    "https://blacksheepadventure.ca/courses/",
    "https://blacksheepadventure.ca/skiing-riding/",
    "https://blacksheepadventure.ca/climbing-mountaineering/",
    "https://blacksheepadventure.ca/hikes-treks/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Activity + location resolution ───────────────────────────────────────────

ACTIVITY_MAP = {
    "ast 1":          "skiing",
    "ast 2":          "skiing",
    "ast1":           "skiing",
    "ast2":           "skiing",
    "avalanche":      "skiing",
    "ski tour":       "skiing",
    "skiing":         "skiing",
    "snowboard":      "skiing",
    "splitboard":     "skiing",
    "steep":          "skiing",
    "heli":           "heli",
    "climbing":       "climbing",
    "mountaineer":    "mountaineering",
    "alpine":         "mountaineering",
    "via ferrata":    "via_ferrata",
    "hike":           "hiking",
    "trek":           "hiking",
    "rappel":         "rappelling",
    "snowshoe":       "snowshoeing",
    "hut":            "huts",
}

LOCATION_MAP = {
    "squamish":    "Squamish, BC",
    "whistler":    "Whistler, BC",
    "pemberton":   "Pemberton, BC",
    "rogers pass": "Rogers Pass, BC",
    "tantalus":    "Squamish, BC",
    "garibaldi":   "Squamish, BC",
    "bugaboo":     "Bugaboos, BC",
    "logan":       "Yukon, YT",
    "patagonia":   None,   # skip — international
}


def resolve_activity(title: str, url: str = "") -> str:
    combined = (title + " " + url).lower()
    for kw, act in ACTIVITY_MAP.items():
        if kw in combined:
            return act
    if "/trip/" in url:
        if "ski" in combined:
            return "skiing"
    if "climb" in combined:
        return "climbing"
    if "mountaineer" in combined or "alpine" in combined:
        return "mountaineering"
    if "hike" in combined or "trek" in combined:
        return "hiking"
    return "guided"


def resolve_location(title: str, description: str = "") -> str:
    combined = (title + " " + description).lower()
    for kw, loc in LOCATION_MAP.items():
        if kw in combined:
            return loc
    return PROVIDER["location"]


# ── Google Places ─────────────────────────────────────────────────────────────

def find_place_id_bsa(location: str) -> dict | None:
    """Get place info from Google Places API. Returns dict with place_id, rating, review_count."""
    if not GOOGLE_PLACES_API_KEY:
        return None

    place_id = "ChIJMW6e0Ub4hlQRvCYfsv0sFgk"
    log.info(f"Using hardcoded place_id: {place_id}")

    rating = None
    review_count = None

    details_url = "https://maps.googleapis.com/maps/api/place/details/json"
    r = requests.get(details_url, params={
        "place_id": place_id,
        "fields": "rating,user_ratings_total",
        "key": GOOGLE_PLACES_API_KEY,
    })
    details = r.json()
    log.info(f"Google Places old API details response: {details}")
    result = details.get("result", {})
    rating = result.get("rating")
    review_count = result.get("user_ratings_total")

    if not rating:
        log.info(f"Old API returned no rating, trying new API v1")
        new_api_url = f"https://places.googleapis.com/v1/places/{place_id}"
        r = requests.get(new_api_url, headers={
            "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": "rating,userRatingCount",
        })
        new_details = r.json()
        log.info(f"Google Places new API v1 response: {new_details}")
        rating = new_details.get("rating")
        review_count = new_details.get("userRatingCount")

    return {
        "place_id": place_id,
        "rating": rating,
        "review_count": review_count,
    }


def update_provider_place_id(provider_id: str, place_info: dict):
    """Update providers table with place_id, rating, and review_count."""
    if not place_info:
        return
    sb_patch(
        "providers",
        f"id=eq.{provider_id}",
        {
            "google_place_id": place_info.get("place_id"),
            "rating": place_info.get("rating"),
            "review_count": place_info.get("review_count"),
        },
    )
    log.info(f"Updated provider {provider_id} with place info")


# ── Supabase helpers ──────────────────────────────────────────────────────────

def upsert_courses(rows: list[dict]):
    if not rows:
        return
    sb_upsert("courses", rows)

    # Log intelligence (V2 — append-only, change-detected)
    for c in rows:
        log_availability_change(c)
        log_price_change(c)


def deactivate_missing(seen_ids: set[str]):
    """Set active=false for courses not seen this run."""
    existing_rows = sb_get("courses", {"provider_id": f"eq.{PROVIDER['id']}", "select": "id"})
    existing = {row["id"] for row in existing_rows}
    to_deactivate = existing - seen_ids
    for cid in to_deactivate:
        sb_patch("courses", f"id=eq.{cid}", {"active": False})
    if to_deactivate:
        log.info(f"Deactivated {len(to_deactivate)} stale courses")


# ── Claude Haiku summary ──────────────────────────────────────────────────────

_summary_cache: dict[str, str] = {}


def generate_summary(title: str, description: str) -> str:
    key = title.strip().lower()
    if key in _summary_cache:
        return _summary_cache[key]
    if not ANTHROPIC_API_KEY or not description:
        return ""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 120,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Write a 1–2 sentence plain-English summary of this outdoor course for a booking aggregator. "
                        f"No marketing fluff. Course: {title}. Details: {description[:600]}"
                    ),
                }],
            },
            timeout=20,
        )
        summary = r.json()["content"][0]["text"].strip()
        summary = re.sub(r"^#+\s*", "", summary)
        _summary_cache[key] = summary
        return summary
    except Exception as e:
        log.warning(f"Summary generation failed: {e}")
        return ""


# ── HTML fetch ────────────────────────────────────────────────────────────────

def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"Fetch failed {url}: {e}")
        return None


# ── Date / availability parsing ───────────────────────────────────────────────

DATE_PATTERN = re.compile(
    r"(✓|✗|•)?\s*"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:[–\-](\d{1,2}))?(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_dates_from_text(text: str) -> list[dict]:
    results = []
    current_year = date.today().year
    for m in DATE_PATTERN.finditer(text):
        marker, month_str, day_str, day_end, duration_str = m.groups()
        month_key = month_str[:3].lower()
        month_num = MONTH_MAP.get(month_key, 1)
        day = int(day_str)

        yr = current_year
        if month_num < date.today().month:
            yr = current_year + 1

        try:
            d = date(yr, month_num, day)
        except ValueError:
            continue

        avail = "sold" if marker and "✗" in marker else "open"
        duration = duration_str.strip() if duration_str else ""
        date_label = f"{month_str} {day}"
        if day_end:
            date_label += f"–{day_end}"
        if duration:
            date_label += f" ({duration})"

        results.append({
            "date_str":     date_label,
            "date_sort":    d.strftime("%Y-%m-%d"),
            "avail":        avail,
            "duration_str": duration,
        })
    return results


# ── Course page scraping ──────────────────────────────────────────────────────

def stable_id_bsa(provider_id: str, activity: str, date_sort: str, title: str) -> str:
    h = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"{provider_id}-{activity}-{date_sort}-{h}"


def scrape_course_page(url: str) -> list[dict]:
    soup = fetch(url)
    if not soup:
        return []

    image_url = None
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        image_url = og_image["content"]

    title_el = soup.find("h1") or soup.find("h2")
    title = title_el.get_text(strip=True) if title_el else "Unknown Course"

    paras = soup.find_all("p")
    chunks = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 60]
    description = " ".join(chunks[:3])
    log.info(f"Description length for '{title}': {len(description)} chars")

    price = None
    price_pattern = re.compile(r"\$\s?(\d[\d,]+)")
    full_text = soup.get_text()
    price_matches = price_pattern.findall(full_text)
    if price_matches:
        for pm in price_matches:
            val = int(pm.replace(",", ""))
            if val > 50:
                price = val
                break

    relevant_texts = []
    booking_keywords = re.compile(r"(BOOK NOW|Book Now)", re.IGNORECASE)
    for element in soup.find_all(string=booking_keywords):
        parent = element.parent
        if parent:
            relevant_texts.append(parent.get_text())
    class_keywords = ["book", "date", "schedule", "availability"]
    for kw in class_keywords:
        for el in soup.find_all(class_=re.compile(kw, re.IGNORECASE)):
            relevant_texts.append(el.get_text())
    unique_texts = list(set(relevant_texts))
    combined_text = " ".join(unique_texts)
    dates = parse_dates_from_text(combined_text) if combined_text else []

    activity = resolve_activity(title, url)
    location = resolve_location(title, description)
    summary  = generate_summary(title, description)
    log.info(f"Summary for '{title}': {'generated' if summary else 'empty'}")
    booking_url = append_utm(url)

    rows = []
    if dates:
        for d in dates:
            cid = stable_id_v2(PROVIDER["id"], d["date_sort"], title)
            rows.append({
                "id":               cid,
                "provider_id":      PROVIDER["id"],
                "title":            title,
                "activity":         activity,
                "activity_canonical": None,  # V2: null hides from V1 frontend
                "location_raw":     location,
                "location_canonical": location,
                "date_display":     d["date_str"],
                "date_sort":        d["date_sort"],
                "avail":            d["avail"],
                "price":            price,
                "image_url":        image_url,
                "summary":          summary,
                "booking_url":      booking_url,
                "active":           d["avail"] != "sold",
                "spots_remaining":  None,
            })
    else:
        cid = stable_id_v2(PROVIDER["id"], None, title)
        rows.append({
            "id":               cid,
            "provider_id":      PROVIDER["id"],
            "title":            title,
            "activity":         activity,
            "activity_canonical": None,  # V2: null hides from V1 frontend
            "location_raw":     location,
            "location_canonical": location,
            "date_display":     None,
            "date_sort":        None,
            "avail":            "open",
            "price":            price,
            "image_url":        image_url,
            "summary":          summary,
            "booking_url":      booking_url,
            "active":           True,
            "spots_remaining":  None,
        })

    return rows


# ── Listing page crawl ────────────────────────────────────────────────────────

def collect_course_urls() -> list[str]:
    seen = set()
    urls = []
    for listing in LISTING_PAGES:
        soup = fetch(listing)
        if not soup:
            log.warning(f"Failed to fetch listing page: {listing}")
            continue
        links = soup.find_all("a", href=True)
        log.info(f"Listing page {listing}: found {len(links)} total links")
        matched = 0
        for a in links:
            href = a["href"]
            if re.search(r"blacksheepadventure\.ca.*(course|trip)/", href):
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
                    matched += 1
        log.info(f"  → {matched} course/trip URLs matched on this page")
    log.info(f"Found {len(urls)} course/trip URLs total")
    return urls


# ── Email summary ─────────────────────────────────────────────────────────────

def send_summary(total: int, upserted: int):
    if not RESEND_API_KEY:
        return
    send_email(
        f"[BSA scraper] {upserted} courses upserted",
        f"<p><b>Black Sheep Adventure</b> scrape complete.</p>"
        f"<p>Course pages scraped: {total}<br>"
        f"Rows upserted: {upserted}</p>",
        to="luke@backcountryfinder.com",
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"Starting scraper: {PROVIDER['name']}")

    place_info = find_place_id_bsa(PROVIDER["location"])
    if place_info:
        log.info(f"Place ID found: {place_info['place_id']}")
        update_provider_place_id(PROVIDER["id"], place_info)
    else:
        log.warning("Place ID not found")

    course_urls = collect_course_urls()

    all_rows = []
    for url in course_urls:
        rows = scrape_course_page(url)
        all_rows.extend(rows)
        log.info(f"  {url} → {len(rows)} row(s)")

    all_rows = [r for r in all_rows if r["location_raw"] is not None]

    seen_ids = set()
    deduplicated_rows = []
    for row in all_rows:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            deduplicated_rows.append(row)

    upsert_courses(deduplicated_rows)
    deactivate_missing(seen_ids)

    log.info(f"Done. {len(course_urls)} pages → {len(deduplicated_rows)} rows (after dedup)")
    # EMAILS OFF
    # send_summary(len(course_urls), len(deduplicated_rows))


if __name__ == "__main__":
    main()
