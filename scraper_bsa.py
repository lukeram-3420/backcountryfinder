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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":       "bsa",
    "name":     "Black Sheep Adventure",
    "website":  "https://blacksheepadventure.ca",
    "location": "Squamish, BC",
}

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_KEY   = os.environ.get("RESEND_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_KEY   = os.environ.get("GOOGLE_PLACES_API_KEY", "")

UTM = "utm_source=backcountryfinder&utm_medium=referral"

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


def resolve_activity(title: str) -> str:
    t = title.lower()
    for kw, act in ACTIVITY_MAP.items():
        if kw in t:
            return act
    return "guided"


def resolve_location(title: str, description: str = "") -> str:
    combined = (title + " " + description).lower()
    for kw, loc in LOCATION_MAP.items():
        if kw in combined:
            return loc
    return PROVIDER["location"]


# ── Google Places ─────────────────────────────────────────────────────────────

def find_place_id(location: str) -> str | None:
    if not GOOGLE_KEY:
        return None
    city = location.split(",")[0].strip()
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    r = requests.get(url, params={
        "input": city,
        "inputtype": "textquery",
        "fields": "place_id",
        "key": GOOGLE_KEY,
    })
    candidates = r.json().get("candidates", [])
    return candidates[0]["place_id"] if candidates else None


# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def upsert_courses(rows: list[dict]):
    if not rows:
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/courses",
        headers=sb_headers(),
        json=rows,
    )
    r.raise_for_status()
    log.info(f"Upserted {len(rows)} courses")


def deactivate_missing(seen_ids: set[str]):
    """Set active=false for courses not seen this run."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/courses",
        headers=sb_headers(),
        params={"provider_id": f"eq.{PROVIDER['id']}", "select": "id"},
    )
    r.raise_for_status()
    existing = {row["id"] for row in r.json()}
    to_deactivate = existing - seen_ids
    for cid in to_deactivate:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/courses",
            headers=sb_headers(),
            params={"id": f"eq.{cid}"},
            json={"active": False},
        ).raise_for_status()
    if to_deactivate:
        log.info(f"Deactivated {len(to_deactivate)} stale courses")


# ── Claude Haiku summary ──────────────────────────────────────────────────────

_summary_cache: dict[str, str] = {}


def generate_summary(title: str, description: str) -> str:
    key = title.strip().lower()
    if key in _summary_cache:
        return _summary_cache[key]
    if not ANTHROPIC_KEY or not description:
        return ""
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
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

# Matches patterns like: "✓ March 3rd (4 Days)" or "March 3rd (4 Days)" or
# "February 14-15" or "January 20 (2 Days)"
DATE_PATTERN = re.compile(
    r"(✓|✗|•)?\s*"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:[–\-](\d{1,2}))?(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_dates_from_text(text: str) -> list[dict]:
    """
    Returns list of dicts with keys: date_str, date_sort, avail, duration_days
    Checkmark prefix (✓) = open, ✗ = sold out, no prefix = open.
    """
    results = []
    current_year = date.today().year
    for m in DATE_PATTERN.finditer(text):
        marker, month_str, day_str, day_end, duration_str = m.groups()
        month_key = month_str[:3].lower()
        month_num = MONTH_MAP.get(month_key, 1)
        day = int(day_str)

        # Guess year — if month already passed this year assume next year
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

def stable_id(provider_id: str, activity: str, date_sort: str, title: str) -> str:
    base = f"{provider_id}-{activity}-{date_sort}"
    # If date_sort is not unique enough, hash the title
    h = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"{base}-{h}"


def scrape_course_page(url: str) -> list[dict]:
    """Scrape a single course/trip detail page and return course rows."""
    soup = fetch(url)
    if not soup:
        return []

    # Title
    title_el = soup.find("h1") or soup.find("h2")
    title = title_el.get_text(strip=True) if title_el else "Unknown Course"

    # Description — grab first substantial paragraph
    entry = soup.find("div", class_=re.compile(r"entry-content|page-content|post-content"))
    description = ""
    if entry:
        paras = entry.find_all("p")
        chunks = [p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 60]
        description = " ".join(chunks[:3])

    # Price — look for "$" pattern
    price = None
    price_pattern = re.compile(r"\$\s?(\d[\d,]+)")
    full_text = soup.get_text()
    price_matches = price_pattern.findall(full_text)
    if price_matches:
        # Take the first reasonable price (> $50)
        for pm in price_matches:
            val = int(pm.replace(",", ""))
            if val > 50:
                price = val
                break

    # Dates — search full page text
    dates = parse_dates_from_text(full_text)

    activity = resolve_activity(title)
    location = resolve_location(title, description)
    summary  = generate_summary(title, description)
    booking_url = f"{url}?{UTM}"

    rows = []
    if dates:
        for d in dates:
            cid = stable_id(PROVIDER["id"], activity, d["date_sort"], title)
            rows.append({
                "id":           cid,
                "provider_id":  PROVIDER["id"],
                "title":        title,
                "activity":     activity,
                "location":     location,
                "date_str":     d["date_str"],
                "date_sort":    d["date_sort"],
                "avail":        d["avail"],
                "price":        price,
                "summary":      summary,
                "booking_url":  booking_url,
                "active":       d["avail"] != "sold",
                "spots_remaining": None,
            })
    else:
        # No specific dates found — create a single evergreen row with null date
        cid = f"{PROVIDER['id']}-{activity}-{hashlib.md5(title.encode()).hexdigest()[:8]}"
        rows.append({
            "id":           cid,
            "provider_id":  PROVIDER["id"],
            "title":        title,
            "activity":     activity,
            "location":     location,
            "date_str":     None,
            "date_sort":    None,
            "avail":        "open",
            "price":        price,
            "summary":      summary,
            "booking_url":  booking_url,
            "active":       True,
            "spots_remaining": None,
        })

    return rows


# ── Listing page crawl ────────────────────────────────────────────────────────

def collect_course_urls() -> list[str]:
    """
    Crawl listing pages and collect individual course/trip page URLs.
    URLs follow patterns: /course/{slug}/ or /trip/{slug}/
    """
    seen = set()
    urls = []
    for listing in LISTING_PAGES:
        soup = fetch(listing)
        if not soup:
            continue
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"blacksheepadventure\.ca/(course|trip)/[^/]+/?$", href):
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
    log.info(f"Found {len(urls)} course/trip URLs")
    return urls


# ── Email summary ─────────────────────────────────────────────────────────────

def send_summary(total: int, upserted: int):
    if not RESEND_KEY:
        return
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
            json={
                "from":    "scraper@backcountryfinder.com",
                "to":      ["luke@backcountryfinder.com"],
                "subject": f"[BSA scraper] {upserted} courses upserted",
                "html": (
                    f"<p><b>Black Sheep Adventure</b> scrape complete.</p>"
                    f"<p>Course pages scraped: {total}<br>"
                    f"Rows upserted: {upserted}</p>"
                ),
            },
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Email send failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"Starting scraper: {PROVIDER['name']}")

    # Resolve Google Place ID
    place_id = find_place_id(PROVIDER["location"])
    if place_id:
        log.info(f"Place ID found: {place_id}")
    else:
        log.warning("Place ID not found")

    # Crawl listing pages → collect individual URLs
    course_urls = collect_course_urls()

    all_rows = []
    for url in course_urls:
        rows = scrape_course_page(url)
        all_rows.extend(rows)
        log.info(f"  {url} → {len(rows)} row(s)")

    # Filter out international trips (location=None)
    all_rows = [r for r in all_rows if r["location"] is not None]

    seen_ids = {r["id"] for r in all_rows}
    upsert_courses(all_rows)
    deactivate_missing(seen_ids)

    log.info(f"Done. {len(course_urls)} pages → {len(all_rows)} rows")
    send_summary(len(course_urls), len(all_rows))


if __name__ == "__main__":
    main()