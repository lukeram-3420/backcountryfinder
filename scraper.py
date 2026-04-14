#!/usr/bin/env python3
"""
BackcountryFinder scraper — Rezdy providers (Altus + MSAA)
Runs every 6 hours via GitHub Actions
"""

import os
import argparse
import re
import json
import time
import logging
import hashlib
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from scraper_utils import normalise_location

# ── CONFIG ──
SUPABASE_URL          = os.environ["SUPABASE_URL"]
SUPABASE_KEY          = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY        = os.environ["RESEND_API_KEY"]
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
NOTIFY_EMAIL          = "hello@backcountryfinder.com"
FROM_EMAIL            = "hello@backcountryfinder.com"
PLACES_API_URL        = "https://maps.googleapis.com/maps/api/place"
CLAUDE_MODEL          = "claude-haiku-4-5-20251001"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

ACTIVITY_LABELS = {
    "skiing":         "Backcountry Skiing",
    "climbing":       "Rock Climbing",
    "mountaineering": "Mountaineering",
    "hiking":         "Hiking",
    "biking":         "Mountain Biking",
    "fishing":        "Fly Fishing",
    "hunting":        "Hunting",
    "heli":           "Heli Skiing",
    "cat":            "Cat Skiing",
    "huts":           "Alpine Huts",
    "guided":         "Guided Tour",
    "glissading":     "Glissading",
    "rappelling":     "Rappelling",
    "snowshoeing":    "Snowshoeing",
    "snowshoe":       "Snowshoeing",
    "via_ferrata":    "Via Ferrata",
}

REZDY_PROVIDERS = [
    {
        "id":       "altus",
        "name":     "Altus Mountain Guides",
        "storefront": "https://altusmountainguides.rezdy.com",
        "catalogs": [
            "catalog/540907/altus-ast-1",
            "catalog/540908/altus-ast-1",
            "catalog/628633/altus-ast-2",
        ],
        "utm":      "utm_source=backcountryfinder&utm_medium=referral",
    },
    {
        "id":       "msaa",
        "name":     "Mountain Skills Academy",
        "storefront": "https://mountainskillsacademy.rezdy.com",
        "catalogs": [
            "catalog/315469/luxury-experiences",
            "catalog/517471/whistler-mountain-top",
            "catalog/436573/squamish-via-ferrata",
            "catalog/486576/hiking-tours",
            "catalog/517472/climbing-adventures",
            "catalog/517474/winter-tours",
            "catalog/622663/via-ferrata-s-no-lift-ticket",
            "catalog/628248/crevasse-rescue-refresher",
            "catalog/633549/ast-1-online",
        ],
        "utm":      "utm_source=backcountryfinder&utm_medium=referral",
    },
]

# Canada West uses WooCommerce — separate scraper
CWMS_PROVIDERS = [
    {
        "id":       "cwms",
        "name":     "Canada West Mountain School",
        "listing_url": "https://themountainschool.com/programs-and-courses/",
        "base_url": "https://themountainschool.com",
        "utm":      "utm_source=backcountryfinder&utm_medium=referral",
    },
]

# Summit Mountain Guides uses The Events Calendar WordPress plugin
SUMMIT_PROVIDERS = [
    {
        "id":       "summit",
        "name":     "Summit Mountain Guides",
        "listing_url": "https://summitmountainguides.com/upcoming-trips-courses/",
        "base_url": "https://summitmountainguides.com",
        "utm":      "utm_source=backcountryfinder&utm_medium=referral",
        "months_ahead": 6,
    },
]

# Island Alpine Guides + Hike Vancouver Island — same custom Rails platform
IAG_PROVIDERS = [
    {
        "id":          "iag",
        "name":        "Island Alpine Guides",
        "listing_url": "https://www.islandalpineguides.com/trips/upcoming",
        "base_url":    "https://www.islandalpineguides.com",
        "utm":         "utm_source=backcountryfinder&utm_medium=referral",
        "location":    "Vancouver Island, BC",
    },
    {
        "id":          "hvi",
        "name":        "Hike Vancouver Island",
        "listing_url": "https://www.hikevancouverisland.com/trips/upcoming",
        "base_url":    "https://www.hikevancouverisland.com",
        "utm":         "utm_source=backcountryfinder&utm_medium=referral",
        "location":    "Vancouver Island, BC",
    },
]

# Squamish Rock Guides — custom WordPress booking system
SRG_PROVIDERS = [
    {
        "id":        "srg",
        "name":      "Squamish Rock Guides",
        "programs_url": "https://squamishrockguides.com/booking/booking-information/",
        "booking_base": "https://squamishrockguides.com/booking/",
        "base_url":  "https://squamishrockguides.com",
        "utm":       "utm_source=backcountryfinder&utm_medium=referral",
    },
]


# ── SKAHA ROCK ADVENTURES ──
# Static HTML site — each course page lists dates with Book Now or Sold Out buttons.
# Sold-out:  <a class="btn btn_sold_out" href="/bookings/?course=NNN&start_date=YYYY-MM-DD" disabled="disabled">Sold Out</a>
# Bookable:  <a class="btn btn_book_now" href="/bookings/?course=NNN&start_date=YYYY-MM-DD">Book Now</a>

SKAHA_PROVIDER = {
    "id":       "skaha-rock-adventures",
    "name":     "Skaha Rock Adventures",
    "base_url": "https://www.skaharockclimbing.com",
    "logo_url": "https://www.skaharockclimbing.com/site/templates/sratheme/img/sra-logo.png",
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
}

SKAHA_COURSE_PAGES = [
    {"path": "/rock-climbing/rock-1-introduction-to-rock-climbing-rappelling/", "title": "Rock 1 — Introduction to Rock Climbing & Rappelling", "activity": "climbing"},
    {"path": "/rock-climbing/rappel-tours/",                                    "title": "3-Day Teen Rock Climbing",                              "activity": "climbing"},
    {"path": "/rock-climbing/rock-2-top-rope-anchors/",                         "title": "Rock 2 — Top Rope Anchors",                            "activity": "climbing"},
    {"path": "/rock-climbing/rock-3-sport-lead-climbing/",                      "title": "Rock 3 — Sport Lead Climbing",                         "activity": "climbing"},
    {"path": "/rock-climbing/rock-5-multi-pitch-traditional-lead-climbing/",    "title": "Rock 4 — Sport Multi-Pitch Lead Climbing",             "activity": "climbing"},
    {"path": "/rock-climbing/rock-4-placement-protection-lead-climbing/",       "title": "Rock 5 — Placement Protection Lead Climbing",          "activity": "climbing"},
    {"path": "/rock-climbing/rock-6-rock-rescue/",                              "title": "Rock 6 — Rock Rescue",                                 "activity": "climbing"},
    {"path": "/rock-climbing/basic-rock/",                                      "title": "2-Day Basic Rock",                                     "activity": "climbing"},
    {"path": "/rock-climbing/accelerator-rock/",                                "title": "2-Day Accelerator Rock",                               "activity": "climbing"},
    {"path": "/rock-climbing/complete-rock/",                                   "title": "3-Day Complete Rock",                                  "activity": "climbing"},
    {"path": "/rock-climbing/total-rock/",                                      "title": "4-Day Total Rock",                                     "activity": "climbing"},
    {"path": "/rock-climbing/rappel-sampler/",                                  "title": "5.5 Hr Rappel Thriller",                               "activity": "climbing"},
    {"path": "/rock-climbing/rock-climbing-sampler/",                           "title": "5.5 Hr Rock Climbing Sampler",                         "activity": "climbing"},
    {"path": "/summer-alpine/4-day-alpine-rock-climbing-trip-cathedral-lakes-park/", "title": "4-Day Alpine Rock Climbing — Cathedral Lakes Park", "activity": "mountaineering"},
    {"path": "/summer-alpine/5-day-backpacking-trip-cathedral-lakes-park/",          "title": "4-Day Backpacking Trip — Cathedral Lakes Park",      "activity": "hiking"},
    {"path": "/summer-alpine/crevasse-rescue-and-glacier-travel/",                   "title": "Crevasse Rescue & Glacier Travel",                    "activity": "mountaineering"},
]

# Activity keyword mapping
ACTIVITY_KEYWORDS = {
    # Order matters — checked top to bottom, first match wins
    "skiing":         ["ast", "avalanche", "backcountry ski", "ski touring", "splitboard", "avy", "heli ski", "cat ski"],
    "hiking":         ["hik", "backpack", "navigation", "wilderness travel", "heli-accessed hik", "heli access"],
    "climbing":       ["climb", "rock", "multi-pitch", "rappel", "belay", "trad", "sport climb", "via ferrata", "ferrata"],
    "mountaineering": ["glacier", "mountaineer", "alpine", "crampon", "crevasse", "scramble", "summit", "alpine climb"],
    "biking":         ["bike", "biking", "mtb", "mountain bike", "cycling"],
    "fishing":        ["fish", "fly fish", "angl", "cast", "river guide"],
    "heli":           ["heli adventure", "heli tour", "heli experience"],
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── SUPABASE HELPERS ──

def sb_get(table: str, params: dict = {}) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def sb_upsert(table: str, data: list) -> None:
    if not data:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    r = requests.post(url, headers=headers, json=data)
    if not r.ok:
        log.error(f"Supabase upsert error {r.status_code}: {r.text}")
    else:
        log.info(f"Upserted {len(data)} rows to {table}")


def sb_insert(table: str, data: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    r = requests.post(url, headers=headers, json=data)
    if not r.ok:
        log.error(f"Supabase insert error {r.status_code}: {r.text}")


# ── LOCATION NORMALISATION ──

def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def load_activity_labels():
    try:
        rows = sb_get("activity_labels", {"select": "activity,label"})
        return {r["activity"]: r["label"] for r in rows}
    except Exception as e:
        log.warning(f"Could not load activity labels: {e}")
        return {}


def load_activity_mappings_table() -> list:
    """Load activity mappings from Supabase — [{title_contains, activity}]."""
    try:
        rows = sb_get("activity_mappings", {"select": "title_contains,activity"})
        # Sort by length descending — longer/more specific patterns match first
        mappings = [(r["title_contains"].lower(), r["activity"]) for r in rows]
        return sorted(mappings, key=lambda x: len(x[0]), reverse=True)
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def resolve_activity(title, description, mappings, provider=""):
    """
    Resolve activity using mappings table first, then Claude, then keyword detection.
    Returns (activity, is_new, should_add_mapping)
    """
    text = (title + " " + description).lower()
    for pattern, activity in mappings:
        if pattern.lower() in text:
            return activity, False, False
    if ANTHROPIC_API_KEY:
        known = get_known_activities(mappings) if mappings else []
        result = claude_classify_activity(title, description, provider, known)
        if isinstance(result, list) and result:
            result = result[0]
        if isinstance(result, dict) and result.get("activity"):
            activity = result["activity"]
            is_new = result.get("is_new", False)
            label = result.get("label", activity.replace("_", " ").title())
            log.info(f"Claude classified '{title}' as '{activity}' (new={is_new}): {result.get('reasoning','')}")
            sb_upsert("activity_labels", [{"activity": activity, "label": label}])
            return activity, is_new, True
    return detect_activity(title, description), False, False



def build_badge(activity: str, duration_days) -> str:
    """Build a clean badge string from canonical activity and duration."""
    label = ACTIVITY_LABELS.get(activity, activity.title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


# ── ACTIVITY DETECTION ──

def detect_activity(title: str, description: str = "") -> str:
    text = (title + " " + description).lower()
    for activity, keywords in ACTIVITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return activity
    return "guided"  # default


def get_activity(course_id: str, detected: str, existing_overrides: dict) -> tuple:
    """Return (activity_raw, activity_override, activity) respecting any manual override."""
    override = existing_overrides.get(course_id)
    activity = override if override else detected
    return detected, override, activity


# ── AVAILABILITY ──

def spots_to_avail(spots: Optional[int]) -> str:
    if spots is None:
        return "open"
    if spots == 0:
        return "sold"
    if spots <= 4:
        return "low"
    return "open"


# ── DATE HELPERS ──

def parse_date_sort(date_str: str) -> Optional[str]:
    """Try to extract a YYYY-MM-DD date from various string formats."""
    if not date_str:
        return None
    # Already ISO
    if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str[:10]
    # Try common patterns
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12"
    }
    # "Apr 19, 2026" or "Apr 19–20, 2026"
    m = re.search(r"(\w+)\s+(\d+).*?(\d{4})", date_str, re.IGNORECASE)
    if m:
        month_str = m.group(1).lower()[:3]
        day = m.group(2).zfill(2)
        year = m.group(3)
        if month_str in months:
            return f"{year}-{months[month_str]}-{day}"
    return None


def is_future(date_sort: Optional[str]) -> bool:
    if not date_sort:
        return True  # keep if we can't parse
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}"
    # Fallback: hash of title
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    return f"{provider_id}-{activity}-{h}"



# ── SKAHA ROCK ADVENTURES SCRAPER ──

def _skaha_fetch(url: str) -> Optional[BeautifulSoup]:
    import random
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    ]
    headers = {
        "User-Agent":      random.choice(user_agents),
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        log.warning(f"Skaha fetch failed for {url}: {e}")
        return None


def _skaha_parse_price(soup: BeautifulSoup) -> Optional[int]:
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2 and "cost" in cells[0].get_text(strip=True).lower():
            m = re.search(r"\$?(\d+)", cells[1].get_text(strip=True))
            if m:
                return int(m.group(1))
    m = re.search(r"\$(\d{2,4})", soup.get_text())
    return int(m.group(1)) if m else None


def _skaha_parse_description(soup: BeautifulSoup) -> str:
    main = soup.find("main") or soup.find("div", id="main") or soup.body
    if not main:
        return ""
    paras = []
    for p in main.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > 60 and "contact our office" not in text.lower():
            paras.append(text)
        if len(paras) >= 2:
            break
    return " ".join(paras)


def _skaha_parse_dates(soup: BeautifulSoup, base_url: str, utm: str) -> list:
    today = date.today()
    results = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"/bookings/\?course=\d+&start_date=")):
        href = a.get("href", "")
        m = re.search(r"start_date=(\d{4}-\d{2}-\d{2})", href)
        if not m:
            continue
        date_iso = m.group(1)
        if date_iso in seen:
            continue
        try:
            if datetime.strptime(date_iso, "%Y-%m-%d").date() < today:
                continue
        except ValueError:
            continue
        seen.add(date_iso)
        classes = a.get("class", [])
        if "btn_sold_out" in classes or a.get("disabled"):
            avail = "sold"
            booking_url = base_url + "/rock-climbing/"
        else:
            avail = "open"
            full_url = base_url + href if href.startswith("/") else href
            sep = "&" if "?" in full_url else "?"
            booking_url = f"{full_url}{sep}{utm}"
        results.append({"date_iso": date_iso, "avail": avail, "booking_url": booking_url})
    results.sort(key=lambda x: x["date_iso"])
    return results


def scrape_skaha() -> list:
    import random
    provider    = SKAHA_PROVIDER
    base_url    = provider["base_url"]
    utm         = provider["utm"]
    provider_id = provider["id"]
    scraped_at  = datetime.utcnow().isoformat()
    log.info(f"=== Scraping {provider['name']} ===")
    all_courses = []
    for i, course_meta in enumerate(SKAHA_COURSE_PAGES):
        url = base_url + course_meta["path"]
        log.info(f"[{i+1}/{len(SKAHA_COURSE_PAGES)}] {course_meta['title']}")
        soup = _skaha_fetch(url)
        if soup is None:
            if i < len(SKAHA_COURSE_PAGES) - 1:
                time.sleep(random.uniform(2, 5))
            continue
        price        = _skaha_parse_price(soup)
        description  = _skaha_parse_description(soup)
        date_entries = _skaha_parse_dates(soup, base_url, utm)
        if not date_entries:
            log.info(f"  No dates found — adding as flexible dates card")
            all_courses.append({
                "title": course_meta["title"], "provider_id": provider_id,
                "activity": course_meta["activity"], "activity_raw": course_meta["activity"],
                "location_raw": "Penticton, BC", "date_display": "Flexible dates",
                "date_sort": None, "duration_days": None, "price": price,
                "spots_remaining": None, "avail": "open", "image_url": None,
                "booking_url": f"{base_url}{course_meta['path']}?{utm}",
                "description": description, "summary": "", "custom_dates": True, "scraped_at": scraped_at,
            })
        else:
            open_count = sum(1 for e in date_entries if e["avail"] == "open")
            sold_count = sum(1 for e in date_entries if e["avail"] == "sold")
            log.info(f"  {open_count} open, {sold_count} sold out | price=${price}")
            for entry in date_entries:
                try:
                    date_display = datetime.strptime(entry["date_iso"], "%Y-%m-%d").strftime("%b %-d, %Y")
                except Exception:
                    date_display = entry["date_iso"]
                all_courses.append({
                    "title": course_meta["title"], "provider_id": provider_id,
                    "activity": course_meta["activity"], "activity_raw": course_meta["activity"],
                    "location_raw": "Penticton, BC", "date_display": date_display,
                    "date_sort": entry["date_iso"], "duration_days": None, "price": price,
                    "spots_remaining": None, "avail": entry["avail"], "image_url": None,
                    "booking_url": entry["booking_url"], "description": description,
                    "summary": "", "custom_dates": False, "scraped_at": scraped_at,
                })
        if i < len(SKAHA_COURSE_PAGES) - 1:
            delay = random.uniform(2, 7)
            log.info(f"  Waiting {delay:.1f}s...")
            time.sleep(delay)
    log.info(f"Skaha total raw courses: {len(all_courses)}")
    return all_courses

# ── REZDY SCRAPER ──

def scrape_rezdy(provider: dict) -> list:
    """Scrape a Rezdy storefront using confirmed HTML structure."""
    log.info(f"Scraping {provider['name']} — {provider['storefront']}")

    # If provider has specific catalogs, scrape each one
    catalogs = provider.get("catalogs", [])
    if catalogs:
        all_courses = []
        seen_titles = set()
        for catalog in catalogs:
            url = f"{provider['storefront']}/{catalog}"
            log.info(f"Scraping catalog: {url}")
            courses = scrape_rezdy_page(provider, url)
            for c in courses:
                if c["title"] not in seen_titles:
                    seen_titles.add(c["title"])
                    all_courses.append(c)
            time.sleep(1)
        log.info(f"Total unique courses from {provider['name']}: {len(all_courses)}")
        return all_courses

    # Otherwise scrape root storefront
    return scrape_rezdy_page(provider, provider["storefront"])


def scrape_rezdy_page(provider: dict, url: str) -> list:
    """Scrape a single Rezdy page and return courses."""
    courses = []

    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        items = soup.select("div.products-list-item")
        if not items:
            log.warning(f"No products-list-item found at {url}")
            return []

        log.info(f"Found {len(items)} items at {url}")

        for item in items:
            try:
                # Title — h2 > a.rezdy-modal
                title_el = item.select_one("h2 a")
                title = title_el.get_text(strip=True) if title_el else None
                if not title:
                    continue

                # Booking URL — relative href on the title link
                booking_url = None
                if title_el:
                    href = title_el.get("href", "")
                    if href.startswith("http"):
                        booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"
                    elif href.startswith("/"):
                        booking_url = f"{provider['storefront']}{href}?{provider['utm']}"
                    else:
                        booking_url = f"{provider['storefront']}/{href}?{provider['utm']}"

                # Price — span.price data-original-amount="CA$1,980.00"
                price = None
                price_el = item.select_one("span.price")
                if price_el:
                    raw = price_el.get("data-original-amount", "") or price_el.get_text(strip=True)
                    price_match = re.search(r"[\d,]+\.?\d*", raw.replace(",", ""))
                    if price_match:
                        try:
                            price = int(float(price_match.group().replace(",", "")))
                        except ValueError:
                            pass

                # Duration — li text after "Duration:"
                duration_days = None
                for li in item.select("ul.unstyled li"):
                    text = li.get_text(strip=True)
                    if "duration" in text.lower():
                        dur_match = re.search(r"(\d+(?:\.\d+)?)\s*day", text, re.I)
                        if dur_match:
                            duration_days = float(dur_match.group(1))
                        break
                # Also try extracting from title
                if not duration_days:
                    dur_match = re.search(r"(\d+(?:\.\d+)?)\s*day", title, re.I)
                    if dur_match:
                        duration_days = float(dur_match.group(1))

                # Image — div.products-list-image img src
                image_url = None
                img_el = item.select_one("div.products-list-image img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")
                    if image_url and image_url.startswith("//"):
                        image_url = "https:" + image_url

                # Description — p tag in overview
                desc_text = ""
                desc_el = item.select_one("div.products-list-item-overview p")
                if desc_el:
                    desc_text = desc_el.get_text(strip=True)

                # Location — extract from title or description
                location_raw = None
                loc_match = re.search(
                    r"(Whistler|Squamish|Seymour|Garibaldi|Pemberton|Tantalus|Vancouver|North Shore|Golden|Revelstoke|Banff|Canmore)",
                    title + " " + desc_text, re.I
                )
                if loc_match:
                    location_raw = loc_match.group(0).title()

                # Activity detection
                activity = detect_activity(title, desc_text)

                # Badge
                dur_str = f" · {int(duration_days)} day{'s' if duration_days > 1 else ''}" if duration_days else ""
                badge = f"{activity.title()}{dur_str}"

                courses.append({
                    "title":         title,
                    "provider_id":   provider["id"],
                    "badge":         badge,
                    "activity":      activity,
                    "activity_raw":  activity,
                    "location_raw":  location_raw,
                    "date_display":  None,   # fetched from course page in future
                    "date_sort":     None,
                    "duration_days": duration_days,
                    "price":         price,
                    "spots_remaining": None,
                    "avail":         "open",
                    "image_url":     image_url,
                    "booking_url":   booking_url,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })

            except Exception as e:
                log.warning(f"Error parsing item from {provider['name']}: {e}")
                continue

    except Exception as e:
        log.error(f"Failed to scrape {url}: {e}")

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


def scrape_cwms_course_page(course_url):
    """
    Visit a CWMS course page and extract individual date sessions.
    Parses quantity labels: "Course Name Month Day-Day Year quantity"
    Returns list of dicts: {date_display, date_sort, spots_remaining, avail, product_id}
    """
    sessions = []
    try:
        clean_url = course_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Strategy 1: find all quantity labels — most reliable
        # Label text: "Complete Mountaineering June 1-7 2026 quantity"
        labels = soup.find_all("label", class_="screen-reader-text")
        date_pattern = re.compile(
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([\d]+(?:-[\d]+)?)\s+(20\d{2})",
            re.I
        )

        for label in labels:
            label_text = label.get_text(strip=True)
            m = date_pattern.search(label_text)
            if not m:
                continue

            month = m.group(1)
            days  = m.group(2)
            year  = m.group(3)
            date_display = f"{month} {days}, {year}"

            # Parse date_sort using first day
            first_day = days.split("-")[0]
            date_sort = None
            try:
                from datetime import datetime as dt
                for fmt in ["%B %d %Y", "%b %d %Y"]:
                    try:
                        date_sort = dt.strptime(f"{month} {first_day} {year}", fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

            # Find parent block for stock + product ID
            parent = label.find_parent("div", class_=lambda c: c and "iconic-woo-bundled-product" in c)
            spots = None
            avail = "open"
            product_id = None

            if parent:
                stock_el = parent.find("p", class_="stock")
                if stock_el:
                    stock_text = stock_el.get_text(strip=True).lower()
                    if "out of stock" in stock_text:
                        avail = "sold"
                        spots = 0
                    else:
                        sm = re.search(r"(\d+)", stock_text)
                        if sm:
                            spots = int(sm.group(1))
                            avail = "critical" if spots <= 2 else "low" if spots <= 4 else "open"

                btn = parent.find("button", class_="single_add_to_cart_button")
                if btn:
                    product_id = btn.get("value")

            # Skip past dates
            if date_sort and date_sort < datetime.utcnow().strftime("%Y-%m-%d"):
                continue

            sessions.append({
                "date_display": date_display,
                "date_sort":    date_sort,
                "spots_remaining": spots,
                "avail":        avail,
                "product_id":   product_id,
            })

        if sessions:
            log.info(f"Found {len(sessions)} date sessions at {clean_url}")

        # Also extract description from the course page
        description = ""
        desc_el = (
            soup.find("div", class_="woocommerce-product-details__short-description") or
            soup.find("div", class_="product-short-description") or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="entry-content")
        )
        if not desc_el:
            # Fallback: find first substantial paragraph after the product title
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 80 and "skip" not in text.lower():
                    description = text[:800]
                    break
        else:
            description = desc_el.get_text(separator=" ", strip=True)[:800]

    except Exception as e:
        log.warning(f"Could not scrape CWMS course page {course_url}: {e}")
        description = ""

    return sessions, description


def scrape_rezdy_api(provider: dict) -> list:
    """Fallback: try Rezdy's JSON endpoint."""
    log.info(f"Trying Rezdy API for {provider['name']}")
    courses = []
    try:
        api_url = f"{provider['storefront']}/products"
        r = requests.get(api_url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)
        if r.ok and "application/json" in r.headers.get("Content-Type", ""):
            data = r.json()
            products = data if isinstance(data, list) else data.get("products", [])
            for p in products:
                title = p.get("name") or p.get("title")
                if not title:
                    continue
                price = p.get("advertisedPrice") or p.get("price")
                if price:
                    price = int(float(price))
                image_url = None
                images = p.get("images", [])
                if images:
                    image_url = images[0].get("thumbnailUrl") or images[0].get("itemUrl")
                activity = detect_activity(title, p.get("shortDescription", ""))
                courses.append({
                    "title":        title,
                    "provider_id":  provider["id"],
                    "badge":        activity.title(),
                    "activity":     activity,
                    "location_raw": p.get("locationAddress"),
                    "date_display": None,
                    "date_sort":    None,
                    "duration_days": None,
                    "price":        price,
                    "spots_remaining": None,
                    "avail":        "open",
                    "image_url":    image_url,
                    "booking_url":  f"{provider['storefront']}/{p.get('productCode', '')}?{provider['utm']}",
                    "scraped_at":   datetime.utcnow().isoformat(),
                })
    except Exception as e:
        log.error(f"Rezdy API fallback failed for {provider['name']}: {e}")
    return courses


# -- CANADA WEST (WOOCOMMERCE) SCRAPER --

CWMS_ACTIVITY_MAP = {
    "hiking":           "hiking",
    "skiing":           "skiing",
    "mountaineering":   "mountaineering",
    "avalanche-safety": "skiing",
    "backcountry":      "skiing",
    "squamish-rock":    "climbing",
    "rock":             "climbing",
    "first-aid":        "guided",
    "alpine":           "mountaineering",
}


def scrape_cwms(provider):
    """Scrape Canada West Mountain School WooCommerce listing page."""
    log.info(f"Scraping {provider['name']} -- {provider['listing_url']}")
    courses = []
    try:
        r = requests.get(provider["listing_url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("div.mix")
        if not items:
            log.warning(f"No div.mix found for {provider['name']}")
            return []
        log.info(f"Found {len(items)} items for {provider['name']}")
        for item in items:
            try:
                style = item.get("style", "")
                if "display: none" in style or "display:none" in style:
                    continue
                title_el = item.select_one("h3.product-archive-heading")
                title = title_el.get_text(strip=True) if title_el else None
                if not title:
                    continue
                link_el = item.select_one("a[href]")
                booking_url = None
                if link_el:
                    href = link_el.get("href", "")
                    if href.startswith("http"):
                        booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"
                    else:
                        booking_url = f"{provider['base_url']}{href}?{provider['utm']}"
                price = None
                price_el = item.select_one("div.product-price")
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    m = re.search(r"[\d,]+", price_text.replace(",", ""))
                    if m:
                        try:
                            price = int(float(m.group().replace(",", "")))
                        except ValueError:
                            pass
                image_url = None
                img_el = item.select_one("div.product-image-wrap img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")
                desc_text = ""
                desc_el = item.select_one("div.product-short-descr")
                if desc_el:
                    desc_text = desc_el.get_text(strip=True)
                classes = item.get("class", [])
                activity = "guided"
                for cls in classes:
                    if cls in CWMS_ACTIVITY_MAP:
                        activity = CWMS_ACTIVITY_MAP[cls]
                        break
                location_raw = None
                loc_m = re.search(r"(Whistler|Squamish|Seymour|Garibaldi|Pemberton|Tantalus|Vancouver|North Shore|Golden|Revelstoke|Manning|Chilcotin|Spearhead|Brandywine|Joffre)", title + " " + desc_text, re.I)
                if loc_m:
                    location_raw = loc_m.group(0).title()
                duration_days = None
                dur_m = re.search(r"(\d+)[\s-]*day", title + " " + desc_text, re.I)
                if dur_m:
                    duration_days = float(dur_m.group(1))
                courses.append({
                    "title":         title,
                    "provider_id":   provider["id"],
                    "badge":         activity.title(),
                    "activity":      activity,
                    "activity_raw":  activity,
                    "location_raw":  location_raw,
                    "date_display":  None,
                    "date_sort":     None,
                    "duration_days": duration_days,
                    "price":         price,
                    "spots_remaining": None,
                    "avail":         "open",
                    "image_url":     image_url,
                    "booking_url":   booking_url,
                    "summary":       "",
                    "description":   desc_text,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })
            except Exception as e:
                log.warning(f"Error parsing CWMS item: {e}")
                continue
    except Exception as e:
        log.error(f"Failed to scrape {provider['name']}: {e}")
    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


def scrape_cwms_course_page(course_url):
    """
    Visit a CWMS course page and extract individual date sessions.
    Parses quantity labels: "Course Name Month Day-Day Year quantity"
    Returns list of dicts: {date_display, date_sort, spots_remaining, avail, product_id}
    """
    sessions = []
    try:
        clean_url = course_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Strategy 1: find all quantity labels — most reliable
        # Label text: "Complete Mountaineering June 1-7 2026 quantity"
        labels = soup.find_all("label", class_="screen-reader-text")
        date_pattern = re.compile(
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([\d]+(?:-[\d]+)?)\s+(20\d{2})",
            re.I
        )

        for label in labels:
            label_text = label.get_text(strip=True)
            m = date_pattern.search(label_text)
            if not m:
                continue

            month = m.group(1)
            days  = m.group(2)
            year  = m.group(3)
            date_display = f"{month} {days}, {year}"

            # Parse date_sort using first day
            first_day = days.split("-")[0]
            date_sort = None
            try:
                from datetime import datetime as dt
                for fmt in ["%B %d %Y", "%b %d %Y"]:
                    try:
                        date_sort = dt.strptime(f"{month} {first_day} {year}", fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

            # Find parent block for stock + product ID
            parent = label.find_parent("div", class_=lambda c: c and "iconic-woo-bundled-product" in c)
            spots = None
            avail = "open"
            product_id = None

            if parent:
                stock_el = parent.find("p", class_="stock")
                if stock_el:
                    stock_text = stock_el.get_text(strip=True).lower()
                    if "out of stock" in stock_text:
                        avail = "sold"
                        spots = 0
                    else:
                        sm = re.search(r"(\d+)", stock_text)
                        if sm:
                            spots = int(sm.group(1))
                            avail = "critical" if spots <= 2 else "low" if spots <= 4 else "open"

                btn = parent.find("button", class_="single_add_to_cart_button")
                if btn:
                    product_id = btn.get("value")

            # Skip past dates
            if date_sort and date_sort < datetime.utcnow().strftime("%Y-%m-%d"):
                continue

            sessions.append({
                "date_display": date_display,
                "date_sort":    date_sort,
                "spots_remaining": spots,
                "avail":        avail,
                "product_id":   product_id,
            })

        if sessions:
            log.info(f"Found {len(sessions)} date sessions at {clean_url}")

        # Also extract description from the course page
        description = ""
        desc_el = (
            soup.find("div", class_="woocommerce-product-details__short-description") or
            soup.find("div", class_="product-short-description") or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="entry-content")
        )
        if not desc_el:
            # Fallback: find first substantial paragraph after the product title
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 80 and "skip" not in text.lower():
                    description = text[:800]
                    break
        else:
            description = desc_el.get_text(separator=" ", strip=True)[:800]

    except Exception as e:
        log.warning(f"Could not scrape CWMS course page {course_url}: {e}")
        description = ""

    return sessions, description


# ── COURSE PAGE CHECK ──

NO_AVAILABILITY_SIGNALS = [
    "no availability",
    "please try again later",
    "no sessions available",
    "not available",
    "sold out",
    "no upcoming",
]

STATIC_DATE_PATTERNS = [
    # Full date: month name + day + year e.g. "Apr 19, 2026" or "April 19 2026"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+20\d{2}",
    # ISO date e.g. "2026-04-19"
    r"20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])",
    # Numeric date e.g. "19/04/2026"
    r"\d{1,2}/\d{1,2}/20\d{2}",
]

def check_course_page(booking_url: str) -> dict:
    """
    Visit a course page and determine:
    - is it available?
    - are there static dates we can scrape?
    - is it a custom date picker?
    Returns dict: {available, custom_dates, dates}
    """
    result = {"available": True, "custom_dates": False, "dates": []}

    try:
        # Strip UTM params for clean page fetch
        clean_url = booking_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        text = r.text.lower()
        soup = BeautifulSoup(r.text, "html.parser")

        # Check for no availability signals
        for signal in NO_AVAILABILITY_SIGNALS:
            if signal in text:
                log.info(f"No availability found at {clean_url}")
                result["available"] = False
                return result

        # Try to find static dates in HTML
        page_text = soup.get_text()
        found_dates = []
        for pattern in STATIC_DATE_PATTERNS:
            matches = re.findall(pattern, page_text)
            found_dates.extend(matches)

        if found_dates:
            log.info(f"Found {len(found_dates)} static dates at {clean_url}")
            result["dates"] = list(set(found_dates))
        else:
            # No static dates — assume JS calendar / custom date picker
            log.info(f"No static dates found at {clean_url} — marking as custom dates")
            result["custom_dates"] = True

        # Extract description text while we're on the page
        desc_el = soup.find("div", class_=lambda c: c and any(x in c for x in ["product-description", "description", "course-description", "entry-content"]))
        if not desc_el:
            desc_el = soup.find("div", {"itemprop": "description"})
        if desc_el:
            result["description"] = desc_el.get_text(separator=" ", strip=True)[:800]

    except Exception as e:
        log.warning(f"Could not check course page {booking_url}: {e}")
        result["available"] = True
        result["custom_dates"] = True

    return result


# ── SEND EMAIL ──

def send_email(to: str, subject: str, html: str) -> None:
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": f"BackcountryFinder Scraper <{FROM_EMAIL}>", "to": [to], "subject": subject, "html": html}
    )
    if not r.ok:
        log.error(f"Email send failed: {r.status_code} {r.text}")
    else:
        log.info(f"Email sent to {to}")


def send_flag_email(flags: list) -> None:
    if not flags:
        return
    rows = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;'>{f['location_raw']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;'>{f['provider_id']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#888;'>{f['course_title']}</td></tr>"
        for f in flags
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#1a2e1a;padding:20px 28px;border-radius:10px 10px 0 0;">
        <p style="margin:0;font-size:18px;color:#fff;font-family:Georgia,serif;">
          backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
        </p>
      </div>
      <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e8e8e8;border-top:none;">
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">location mapping needed</p>
        <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 10px;letter-spacing:-0.3px;">
          {len(flags)} unmatched location{' string' if len(flags)==1 else ' strings'} found
        </h2>
        <p style="font-size:13px;color:#555;margin:0 0 20px;line-height:1.6;">
          The scraper found location strings it couldn't normalise. Add them to the 
          <code style="background:#f5f5f5;padding:2px 6px;border-radius:4px;">location_mappings</code> 
          table in Supabase to fix search filtering. Courses are still visible using the raw string.
        </p>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="background:#f8f8f8;">
              <th style="padding:8px 12px;text-align:left;font-weight:700;color:#333;">Raw string</th>
              <th style="padding:8px 12px;text-align:left;font-weight:700;color:#333;">Provider</th>
              <th style="padding:8px 12px;text-align:left;font-weight:700;color:#333;">Course</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <div style="margin-top:20px;padding-top:16px;border-top:1px solid #f0f0f0;">
          <p style="font-size:12px;color:#888;margin:0;">
            Fix: INSERT INTO location_mappings (location_raw, location_canonical) VALUES ('raw string', 'Canonical Name');
          </p>
        </div>
      </div>
    </div>"""
    send_email(NOTIFY_EMAIL, f"Location mapping needed — {len(flags)} unmatched string{'s' if len(flags)>1 else ''}", html)


def send_scrape_summary(total: int, providers: list, flags_count: int) -> None:
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#1a2e1a;padding:20px 28px;border-radius:10px 10px 0 0;">
        <p style="margin:0;font-size:18px;color:#fff;font-family:Georgia,serif;">
          backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
        </p>
      </div>
      <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e8e8e8;border-top:none;">
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">scraper run complete</p>
        <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 16px;letter-spacing:-0.3px;">
          {total} courses updated
        </h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;">
          <thead><tr style="background:#f8f8f8;">
            <th style="padding:8px 12px;text-align:left;font-weight:700;">Provider</th>
            <th style="padding:8px 12px;text-align:right;font-weight:700;">Courses</th>
            <th style="padding:8px 12px;text-align:right;font-weight:700;">Status</th>
          </tr></thead>
          <tbody>{"".join(f"<tr><td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;'>{p['name']}</td><td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;text-align:right;'>{p['count']}</td><td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;text-align:right;color:{'#2d6a11' if p['ok'] else '#a32d2d'};font-weight:600;'>{'✓ ok' if p['ok'] else '✗ failed'}</td></tr>" for p in providers)}</tbody>
        </table>
        {'<p style="font-size:13px;color:#854f0b;background:#faeeda;padding:10px 14px;border-radius:6px;">⚠ ' + str(flags_count) + ' unmatched location string' + ('s' if flags_count>1 else '') + ' — check your other email for details.</p>' if flags_count else '<p style="font-size:13px;color:#2d6a11;background:#eaf3de;padding:10px 14px;border-radius:6px;">✓ All locations normalised cleanly.</p>'}
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
      </div>
    </div>"""
    send_email(NOTIFY_EMAIL, f"Scraper run — {total} courses updated", html)


# ── MAIN ──



# -- CLAUDE CLASSIFICATION --

def claude_classify(prompt: str, max_tokens: int = 256, return_text: bool = False):
    """Call Claude API. Returns parsed JSON dict by default, or raw text if return_text=True."""
    if not ANTHROPIC_API_KEY:
        return "" if return_text else {}
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30
        )
        text = r.json()["content"][0]["text"].strip()
        if return_text:
            return text
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return "" if return_text else {}


def claude_classify_activity(title: str, description: str, provider: str, known_activities: list) -> dict:
    """Ask Claude to classify the activity type for a course."""
    activities_list = ", ".join(known_activities) if known_activities else "skiing, climbing, mountaineering, hiking, biking, fishing, heli, cat, guided"
    prompt = f"""You are classifying backcountry outdoor experiences for a booking aggregator.

Known activity types: {activities_list}

Course title: "{title}"
Description: "{description}"
Provider: "{provider}"

Classify this course. If it matches a known activity type, use that exact value.
If it is genuinely a new type not in the list, suggest a short lowercase slug (e.g. "via_ferrata", "ice_climbing").

Also provide a short human-readable display label (e.g. "Via Ferrata", "Ice Climbing").

Respond with JSON only, no other text:
{{"activity": "the_canonical_value", "label": "Human Readable Label", "is_new": false, "confidence": "high", "reasoning": "one line explanation"}}"""

    return claude_classify(prompt)


def claude_classify_location(location_raw: str, known_locations: list) -> dict:
    """Ask Claude to normalise a raw location string to a canonical value."""
    locations_list = ", ".join(known_locations) if known_locations else "Whistler / Blackcomb, Squamish, North Shore / Seymour, Pemberton / Duffey, Garibaldi Park, Tantalus Range"
    prompt = f"""You are normalising location strings for a backcountry booking aggregator in British Columbia, Canada.

Known canonical locations: {locations_list}

Raw location string: "{location_raw}"

If this matches one of the known canonical locations, return that exact canonical value.
If it is a genuinely new location not in the list, suggest a clean canonical name.

Respond with JSON only, no other text:
{{"location_canonical": "the_canonical_value", "is_new": false, "confidence": "high", "reasoning": "one line explanation"}}"""

    return claude_classify(prompt)


def generate_summaries_batch(courses: list) -> dict:
    """
    Batch generate 2-sentence summaries for a list of courses.
    courses: list of dicts with keys: id, title, description, provider, activity
    Returns dict: {course_id: summary_text}
    """
    if not ANTHROPIC_API_KEY:
        return {}

    # Filter to courses that have descriptions
    to_summarise = [c for c in courses if c.get("description", "").strip()]
    if not to_summarise:
        return {}

    results = {}
    BATCH_SIZE = 12

    for i in range(0, len(to_summarise), BATCH_SIZE):
        batch = to_summarise[i:i + BATCH_SIZE]
        items = ""
        for c in batch:
            desc = c["description"][:600].strip()
            items += f"""---
ID: {c["id"]}
Provider: {c["provider"]}
Activity: {c["activity"]}
Title: {c["title"]}
Description: {desc}
"""

        prompt = f"""You are writing 2-sentence summaries for backcountry experience listings on a booking aggregator.

For each course below, write exactly 2 sentences. Be specific and enticing. Use plain language, no marketing fluff. Do not start with the provider name or course title. Do not use the word "perfect". Write in third person.

{items}

Respond with JSON only — an array of objects with "id" and "summary" keys. Example:
[{{"id": "cwms-hiking-abc123", "summary": "Two sentences here."}}]"""

        try:
            result = claude_classify(prompt, max_tokens=1500)
            if isinstance(result, list):
                for item in result:
                    if item.get("id") and item.get("summary"):
                        results[item["id"]] = item["summary"]
                log.info(f"Batch summaries: generated {len(result)} summaries (batch {i//BATCH_SIZE + 1})")
            else:
                log.warning(f"Unexpected summary batch response format")
        except Exception as e:
            log.warning(f"Batch summary generation failed: {e}")

        time.sleep(0.5)

    return results


def get_known_activities(activity_maps: list) -> list:
    """Extract unique activity values from the mappings list."""
    return list(set(activity for _, activity in activity_maps))


def get_known_locations(location_maps: dict) -> list:
    """Extract unique canonical location values from the mappings dict."""
    return list(set(location_maps.values()))



# -- SUMMIT MOUNTAIN GUIDES (THE EVENTS CALENDAR) SCRAPER --

def scrape_summit(provider):
    """Scrape Summit Mountain Guides using The Events Calendar WordPress plugin."""
    log.info(f"Scraping {provider['name']} -- {provider['listing_url']}")
    courses = []
    seen_titles_dates = set()

    months_ahead = provider.get("months_ahead", 6)
    now = datetime.utcnow()

    for month_offset in range(months_ahead):
        target = datetime(now.year + (now.month + month_offset - 1) // 12,
                         (now.month + month_offset - 1) % 12 + 1, 1)
        url = f"{provider['listing_url']}?tribe-bar-date={target.strftime('%Y-%m-%d')}"
        log.info(f"Scraping Summit month: {target.strftime('%B %Y')}")

        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Find all event articles
            events = soup.select("article.type-tribe_events, div.tribe-events-calendar-list__event")
            if not events:
                # Fallback: find event links in list view
                events = soup.select("h3.tribe-events-list-event-title, h2.tribe-event-url")

            # Use broader selector for list view
            event_items = soup.select("div.tribe-event-schedule-details, h3.tribe-events-list-event-title")

            # Try the monthly list structure
            articles = soup.find_all("h3", class_=lambda c: c and "tribe" in (c or ""))
            if not articles:
                articles = soup.find_all("h3")

            # Best approach: find all event links on the page
            event_links = soup.select("a.tribe-event-url, h3 a[href*='/events/']")
            if not event_links:
                event_links = soup.select("a[href*='/events/']")

            processed_links = set()
            for link in event_links:
                href = link.get("href", "")
                if not href or href in processed_links or "/events/" not in href:
                    continue
                processed_links.add(href)

                # Find the parent container
                parent = link.find_parent("article") or link.find_parent("div", class_=lambda c: c and "tribe" in (c or "")) or link.find_parent("li")

                title = link.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                # Date — extract from occurrence param + date range text from listing
                date_display = None
                date_sort = None
                duration_days = None
                occ_match = re.search(r"occurrence=(\d{4}-\d{2}-\d{2})", href)
                if occ_match:
                    date_sort = occ_match.group(1)

                # Try to find date range text near the event
                if parent:
                    page_text = parent.get_text(separator=" ", strip=True)
                    # Match "May 16 to June 5, 2026"
                    range_match = re.search(
                        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})\s+to\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),?\s+(20\d{2})",
                        page_text, re.I
                    )
                    # Match "June 11-14, 2026" (same month range)
                    same_month_match = re.search(
                        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})-(\d{1,2}),?\s+(20\d{2})",
                        page_text, re.I
                    )

                    if range_match:
                        m1, d1, m2, d2, yr = range_match.groups()
                        try:
                            start = datetime.strptime(f"{m1} {d1} {yr}", "%B %d %Y")
                            end   = datetime.strptime(f"{m2} {d2} {yr}", "%B %d %Y")
                            # Only use range if start date matches occurrence date
                            start_str = start.strftime("%Y-%m-%d")
                            if not date_sort or date_sort == start_str:
                                duration_days = (end - start).days + 1
                                if start.month == end.month:
                                    date_display = f"{start.strftime('%b')} {d1}–{d2}, {yr}"
                                else:
                                    date_display = f"{start.strftime('%b')} {d1} – {end.strftime('%b')} {d2}, {yr}"
                                if not date_sort:
                                    date_sort = start_str
                        except ValueError:
                            pass
                    elif same_month_match:
                        month, d1, d2, yr = same_month_match.groups()
                        try:
                            start = datetime.strptime(f"{month} {d1} {yr}", "%B %d %Y")
                            end   = datetime.strptime(f"{month} {d2} {yr}", "%B %d %Y")
                            start_str = start.strftime("%Y-%m-%d")
                            if not date_sort or date_sort == start_str:
                                duration_days = (end - start).days + 1
                                date_display = f"{start.strftime('%b')} {d1}–{d2}, {yr}"
                                if not date_sort:
                                    date_sort = start_str
                        except ValueError:
                            pass

                # Fallback display if no range found
                if not date_display and date_sort:
                    try:
                        dt = datetime.strptime(date_sort, "%Y-%m-%d")
                        date_display = dt.strftime("%B %-d, %Y")
                    except ValueError:
                        pass

                # Skip past events
                if date_sort and date_sort < now.strftime("%Y-%m-%d"):
                    continue

                # Dedup by title+date
                key = f"{title}|{date_sort}"
                if key in seen_titles_dates:
                    continue
                seen_titles_dates.add(key)

                # Price
                price = None
                if parent:
                    price_el = parent.find(string=re.compile(r"CAD\$[\d,]+|\$[\d,]+"))
                    if price_el:
                        m = re.search(r"[\d,]+", price_el.replace(",", ""))
                        if m:
                            try:
                                price = int(float(m.group()))
                            except ValueError:
                                pass

                # Image
                image_url = None
                if parent:
                    img = parent.find("img")
                    if img:
                        image_url = img.get("src") or img.get("data-src")

                # Location
                location_raw = None
                if parent:
                    loc_el = parent.find(class_=lambda c: c and "location" in (c or "").lower())
                    if loc_el:
                        location_raw = loc_el.get_text(strip=True)[:100]

                # Booking URL with occurrence parameter
                booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"

                # Fetch description from event page
                description = scrape_summit_event_page(href)
                time.sleep(0.5)

                courses.append({
                    "title":         title,
                    "provider_id":   provider["id"],
                    "badge":         "",
                    "activity":      "guided",
                    "activity_raw":  "guided",
                    "location_raw":  location_raw,
                    "date_display":  date_display,
                    "date_sort":     date_sort,
                    "duration_days": duration_days,
                    "price":         price,
                    "spots_remaining": None,
                    "avail":         "open",
                    "image_url":     image_url,
                    "booking_url":   booking_url,
                    "summary":       "",
                    "description":   description,
                    "custom_dates":  False,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })

        except Exception as e:
            log.error(f"Failed to scrape Summit month {target.strftime('%B %Y')}: {e}")

        time.sleep(1)

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses

def scrape_summit_event_page(event_url):
    """Visit a Summit event page and extract description text."""
    try:
        clean_url = event_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Try common event description selectors
        # Remove nav/header/footer noise first
        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()

        # Find all paragraphs in the page, skip short ones (nav items etc)
        # Look for the first substantial paragraph after the h1
        h1 = soup.find("h1")
        description_parts = []
        if h1:
            # Walk siblings and descendants after h1
            for el in h1.find_all_next("p"):
                text = el.get_text(strip=True)
                if len(text) > 60 and len(description_parts) < 3:
                    description_parts.append(text)
        
        if description_parts:
            return " ".join(description_parts)[:800]
        
        # Final fallback: all substantial paragraphs
        all_p = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 60]
        if all_p:
            return " ".join(all_p[:3])[:800]
    except Exception as e:
        log.warning(f"Could not fetch Summit event page {event_url}: {e}")
    return ""



# -- SQUAMISH ROCK GUIDES SCRAPER --

def scrape_srg(provider):
    """
    Scrape Squamish Rock Guides:
    1. Get all program URLs from sidebar
    2. Visit each program page — title, price, description, progID from Book Now link
    3. Hit booking page with progID — get dates from select dropdown
    4. One card per date
    """
    log.info(f"Scraping {provider['name']}")
    courses = []
    now = datetime.utcnow()

    try:
        # Step 1: Get all program URLs from the sidebar on the booking info page
        r = requests.get(provider["programs_url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Programs are listed in the sidebar
        program_links = soup.select("aside a[href*='/program/'], #genesis-sidebar-primary a[href*='/program/']")
        if not program_links:
            # Fallback: all links containing /program/
            program_links = [a for a in soup.find_all("a", href=True) if "/program/" in a.get("href", "")]

        program_urls = list({a["href"] if a["href"].startswith("http") else provider["base_url"] + a["href"]
                            for a in program_links if "/program/" in a.get("href", "")})

        log.info(f"Found {len(program_urls)} programs for {provider['name']}")

        for prog_url in program_urls:
            try:
                time.sleep(0.5)
                r2 = requests.get(prog_url, headers=HEADERS, timeout=20)
                r2.raise_for_status()
                soup2 = BeautifulSoup(r2.text, "html.parser")

                # Remove sidebar to avoid grabbing wrong h1/content
                for sidebar in soup2.select("aside, #genesis-sidebar-primary, .sidebar"):
                    sidebar.decompose()

                # Title — second h1 is the course title (first is sidebar "Instruction")
                h1s = soup2.find_all("h1")
                h1 = h1s[-1] if h1s else None  # last h1 is the course title
                title = h1.get_text(strip=True) if h1 else None
                if not title or title.lower() in ("instruction", "courses", "guiding", "groups"):
                    # Fallback to page title tag
                    title_tag = soup2.find("title")
                    if title_tag:
                        title = title_tag.get_text(strip=True).split(" - ")[0].strip()
                if not title:
                    continue

                # Price — find the last price mentioned (most accurate)
                price = None
                price_matches = []
                for el in soup2.find_all(string=re.compile(r"\$[\d,]+")):
                    m = re.search(r"\$([\d,]+\.?\d*)", str(el))
                    if m:
                        try:
                            val = int(float(m.group(1).replace(",", "")))
                            if val > 0:
                                price_matches.append(val)
                        except ValueError:
                            pass
                if price_matches:
                    price = price_matches[-1]  # last price = actual course price

                # Description — paragraphs after the course title h1
                desc_parts = []
                if h1:
                    for p in h1.find_all_next("p"):
                        text = p.get_text(strip=True)
                        if len(text) > 60 and len(desc_parts) < 3:
                            desc_parts.append(text)
                description = " ".join(desc_parts)[:800]

                # Image — first image in main content
                image_url = None
                img = soup2.select_one(".entry-content img, main img, article img")
                if img:
                    image_url = img.get("src") or img.get("data-src")

                # progID from Book Now link
                book_link = soup2.find("a", href=re.compile(r"progID=\d+"))
                if not book_link:
                    log.warning(f"No progID found for {title} — skipping")
                    continue

                book_href = book_link.get("href", "")
                prog_id_match = re.search(r"progID=(\d+)", book_href)
                prog_name_match = re.search(r"prg=([^&]+)", book_href)
                if not prog_id_match:
                    continue

                prog_id = prog_id_match.group(1)
                prog_name = prog_name_match.group(1) if prog_name_match else title

                # Booking URL
                booking_url = f"{provider['booking_base']}?prg={prog_name}&progID={prog_id}&{provider['utm']}"

                # Scrape dates from booking page dropdown — clean YYYY-MM-DD option values
                date_strs = []
                booking_page_url = f"{provider['booking_base']}?prg={prog_name}&progID={prog_id}"
                try:
                    time.sleep(0.5)
                    r3 = requests.get(booking_page_url, headers=HEADERS, timeout=20)
                    r3.raise_for_status()
                    soup3 = BeautifulSoup(r3.text, "html.parser")
                    for sel in soup3.find_all("select"):
                        opts = [o.get("value","").strip() for o in sel.find_all("option")]
                        iso_opts = [o for o in opts if re.match(r"20\d{2}-\d{2}-\d{2}", o)]
                        if iso_opts:
                            date_strs = sorted(set(iso_opts))
                            break
                except Exception as e:
                    log.warning(f"Could not fetch booking page for {title}: {e}")

                if not date_strs:
                    log.warning(f"No dates found for {title} — adding as flexible")
                    courses.append({
                        "title":        title,
                        "provider_id":  provider["id"],
                        "activity":     "climbing",
                        "activity_raw": "climbing",
                        "price":        price,
                        "date_display": "Flexible dates",
                        "date_sort":    None,
                        "custom_dates": True,
                        "image_url":    image_url,
                        "booking_url":  booking_url,
                        "description":  description,
                        "summary":      "",
                        "avail":        "open",
                        "scraped_at":   datetime.utcnow().isoformat(),
                    })
                    continue

                for date_str in sorted(set(date_strs)):
                    if date_str < now.strftime("%Y-%m-%d"):
                        continue
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        date_display = dt.strftime("%B %-d, %Y")
                    except ValueError:
                        date_display = date_str

                    courses.append({
                        "title":        title,
                        "provider_id":  provider["id"],
                        "activity":     "climbing",
                        "activity_raw": "climbing",
                        "price":        price,
                        "date_display": date_display,
                        "date_sort":    date_str,
                        "custom_dates": False,
                        "image_url":    image_url,
                        "booking_url":  booking_url,
                        "description":  description,
                        "summary":      "",
                        "avail":        "open",
                        "scraped_at":   datetime.utcnow().isoformat(),
                    })

            except Exception as e:
                log.warning(f"Error scraping SRG program {prog_url}: {e}")
                continue

    except Exception as e:
        log.error(f"Failed to scrape {provider['name']}: {e}")

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


# -- ISLAND ALPINE GUIDES / HIKE VANCOUVER ISLAND SCRAPER --

def parse_iag_spots(dt_text):
    """Parse spot count and avail from DT text like '6 SPOTS LEFT', '1 SPOT LEFT', 'FULL'"""
    text = dt_text.strip().upper()
    if "FULL" in text or "SOLD" in text:
        return 0, "sold"
    m = re.search(r"(\d+)\s+SPOT", text)
    if m:
        spots = int(m.group(1))
        if spots <= 2:
            return spots, "critical"
        elif spots <= 4:
            return spots, "low"
        return spots, "open"
    return None, "open"

def parse_iag_date(dd_text):
    """Parse date display and date_sort from DD text like 'May 8 - 10, 2026', 'May 9, 2026 (day trip)'"""
    text = dd_text.strip()
    # Remove "(day trip)" etc
    text_clean = re.sub(r"\s*\(.*?\)", "", text).strip()

    # "May 8 - 10, 2026" — same month range
    m = re.match(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s*-\s*(\d{1,2}),?\s*(20\d{2})",
        text_clean, re.I
    )
    if m:
        month, d1, d2, yr = m.groups()
        try:
            start = datetime.strptime(f"{month} {d1} {yr}", "%b %d %Y")
            end = datetime.strptime(f"{month} {d2} {yr}", "%b %d %Y")
            duration = (end - start).days + 1
            return f"{start.strftime('%b')} {d1}–{d2}, {yr}", start.strftime("%Y-%m-%d"), duration
        except ValueError:
            pass

    # "May 8 - Jun 10, 2026" — cross month range
    m = re.match(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s*-\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s*(20\d{2})",
        text_clean, re.I
    )
    if m:
        m1, d1, m2, d2, yr = m.groups()
        try:
            start = datetime.strptime(f"{m1} {d1} {yr}", "%b %d %Y")
            end = datetime.strptime(f"{m2} {d2} {yr}", "%b %d %Y")
            duration = (end - start).days + 1
            return f"{start.strftime('%b')} {d1} – {end.strftime('%b')} {d2}, {yr}", start.strftime("%Y-%m-%d"), duration
        except ValueError:
            pass

    # "Feb 6 - 14, 2027" already handled above — also try "Dec 28 - 31, 2026"
    # Single date "May 9, 2026"
    m = re.match(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s*(20\d{2})",
        text_clean, re.I
    )
    if m:
        month, day, yr = m.groups()
        try:
            dt = datetime.strptime(f"{month} {day} {yr}", "%b %d %Y")
            return dt.strftime("%B %-d, %Y"), dt.strftime("%Y-%m-%d"), 1
        except ValueError:
            pass

    return text, None, None

def scrape_iag_style(provider):
    """
    Scrape Island Alpine Guides or Hike Vancouver Island.
    Both use identical HTML: list-upcoming with h4, p, dl/dt/dd structure.
    One row per occurrence on the listing page.
    Visits each trip page for price and booking URL.
    """
    log.info(f"Scraping {provider['name']} -- {provider['listing_url']}")
    courses = []
    now = datetime.utcnow()
    trip_cache = {}  # cache price + booking URL per trip_id

    try:
        r = requests.get(provider["listing_url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        items = soup.select("ul.list-upcoming li")
        log.info(f"Found {len(items)} upcoming items for {provider['name']}")

        for item in items:
            try:
                # Title
                h4 = item.find("h4")
                if not h4:
                    continue
                title = h4.get_text(strip=True)

                # Description snippet
                desc_p = item.select_one(".upcoming-trip--text p p")
                description = desc_p.get_text(strip=True) if desc_p else ""

                # Image
                img = item.find("img")
                image_url = img.get("src") if img else None

                # Trip ID from href
                trip_link = item.find("a", href=re.compile(r"/trips/\d+"))
                if not trip_link:
                    continue
                trip_href = trip_link.get("href", "")
                trip_id_match = re.search(r"/trips/(\d+)", trip_href)
                if not trip_id_match:
                    continue
                trip_id = trip_id_match.group(1)
                trip_url = f"{provider['base_url']}/trips/{trip_id}"

                # Date and spots from dl
                dl = item.find("dl")
                date_display, date_sort, duration_days = None, None, None
                spots, avail = None, "open"
                if dl:
                    dt_el = dl.find("dt")
                    dd_el = dl.find("dd")
                    if dt_el:
                        spots, avail = parse_iag_spots(dt_el.get_text(strip=True))
                    if dd_el:
                        date_display, date_sort, duration_days = parse_iag_date(dd_el.get_text(strip=True))

                # Skip past dates and sold out
                if date_sort and date_sort < now.strftime("%Y-%m-%d"):
                    continue
                if avail == "sold":
                    continue

                # Get price + booking URL from trip page (cached per trip_id)
                price = None
                booking_url = f"{trip_url}?{provider['utm']}"

                if trip_id not in trip_cache:
                    try:
                        time.sleep(0.5)
                        r2 = requests.get(trip_url, headers=HEADERS, timeout=20)
                        r2.raise_for_status()
                        soup2 = BeautifulSoup(r2.text, "html.parser")

                        # Price — look for $ amount
                        price_el = soup2.find(string=re.compile(r"\$\d+"))
                        if price_el:
                            pm = re.search(r"\$(\d+(?:,\d+)?)", price_el)
                            if pm:
                                try:
                                    price = int(pm.group(1).replace(",", ""))
                                except ValueError:
                                    pass

                        # Also try structured price elements
                        if not price:
                            for el in soup2.select(".price, .cost, [class*='price']"):
                                pm = re.search(r"\$(\d+)", el.get_text())
                                if pm:
                                    price = int(pm.group(1))
                                    break

                        # Full description from trip page
                        soup2_copy = BeautifulSoup(str(soup2), "html.parser")
                        for tag in soup2_copy.find_all(["nav", "header", "footer", "script", "style"]):
                            tag.decompose()
                        h1 = soup2_copy.find("h1")
                        full_desc_parts = []
                        if h1:
                            for p in h1.find_all_next("p"):
                                t = p.get_text(strip=True)
                                if len(t) > 60 and len(full_desc_parts) < 3:
                                    full_desc_parts.append(t)
                        full_desc = " ".join(full_desc_parts)[:800] if full_desc_parts else description

                        # Booking link — look for /bookings/ link
                        book_link = soup2.find("a", href=re.compile(r"/bookings/"))
                        if book_link:
                            bk_href = book_link.get("href", "")
                            if bk_href.startswith("/"):
                                bk_href = provider["base_url"] + bk_href
                            booking_url = f"{bk_href}?{provider['utm']}"

                        trip_cache[trip_id] = {
                            "price": price,
                            "booking_url": booking_url,
                            "description": full_desc,
                        }

                    except Exception as e:
                        log.warning(f"Could not fetch trip page {trip_url}: {e}")
                        trip_cache[trip_id] = {"price": None, "booking_url": booking_url, "description": description}

                cached = trip_cache[trip_id]
                price = cached["price"]
                booking_url = cached["booking_url"]
                full_description = cached.get("description", description)

                courses.append({
                    "title":        title,
                    "provider_id":  provider["id"],
                    "activity":     "guided",
                    "activity_raw": "guided",
                    "location_raw": provider["location"],
                    "price":        price,
                    "date_display": date_display,
                    "date_sort":    date_sort,
                    "duration_days": duration_days,
                    "spots_remaining": spots,
                    "avail":        avail,
                    "image_url":    image_url,
                    "booking_url":  booking_url,
                    "description":  full_description,
                    "summary":      "",
                    "custom_dates": False,
                    "scraped_at":   datetime.utcnow().isoformat(),
                })

            except Exception as e:
                log.warning(f"Error parsing IAG item: {e}")
                continue

    except Exception as e:
        log.error(f"Failed to scrape {provider['name']}: {e}")

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


# -- NOTIFICATIONS --

def send_course_notifications(provider_id, course_title, new_courses):
    """
    Check if any subscribers are waiting for this course.
    If so, send them an email and mark notified_at.
    """
    try:
        # Find unnotified subscribers for this course
        rows = sb_get("notifications", {
            "select": "id,email,course_title",
            "provider_id": f"eq.{provider_id}",
            "course_title": f"eq.{course_title}",
            "notified_at": "is.null",
        })
        if not rows:
            return

        # Build date pills
        dates = sorted(set(c["date_display"] for c in new_courses if c.get("date_display")))[:6]
        date_pills = "".join(
            f'<div style="display:inline-block;background:#f5f4f0;border:1px solid #e0dfda;border-radius:20px;font-size:11px;font-weight:600;color:#444;padding:4px 12px;margin:4px;">{d}</div>'
            for d in dates
        )

        # First course for card details
        c = new_courses[0]
        price = f"${c['price']} <span style='font-size:11px;color:#888;font-weight:400;'>CAD</span>" if c.get("price") else ""
        booking_url = c.get("booking_url", "https://backcountryfinder.com")

        for row in rows:
            unsub_url = f"https://owzrztaguehebkatnatc.supabase.co/functions/v1/unsubscribe-notification?id={row['id']}"

            html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Dates just dropped — BackcountryFinder</title></head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4f0;padding:24px 16px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

<tr><td style="background:#1a2e1a;border-radius:10px 10px 0 0;padding:28px 32px;text-align:center;">
  <p style="margin:0 0 6px;font-size:24px;color:#ffffff;font-family:Georgia,serif;letter-spacing:-0.3px;">backcountry<span style="color:#7ec87e;font-style:italic;">finder</span></p>
  <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.45);">Find your course. Find your line.</p>
</td></tr>

<tr><td style="background:#fff;padding:24px 32px 8px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#eaf3de;border:1px solid #c0dd97;border-radius:10px;padding:18px 20px;margin-bottom:20px;">
    <tr><td>
      <p style="margin:0 0 4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#3b6d11;font-family:Arial,sans-serif;">Dates just dropped</p>
      <p style="margin:0 0 4px;font-size:17px;font-weight:700;color:#1a2e1a;font-family:Arial,sans-serif;">{course_title} is now booking</p>
      <p style="margin:0;font-size:12px;color:#639922;font-weight:600;font-family:Arial,sans-serif;">{c.get('providers', {}).get('name', '') or provider_id} &nbsp;·&nbsp; {c.get('location_canonical','')}</p>
    </td></tr>
  </table>
  <p style="margin:0 0 12px;font-size:14px;color:#444;line-height:1.7;font-family:Arial,sans-serif;">You asked us to let you know when this course opened up. It just did — here are the available dates:</p>
  <div style="margin-bottom:16px;">{date_pills}</div>
</td></tr>

<tr><td style="background:#fff;padding:0 32px 20px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;border:1px solid #e8e7e3;overflow:hidden;">
    <tr>
      <td width="5" style="background:#1a2e1a;">&nbsp;</td>
      <td style="padding:14px 16px;">
        <span style="font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;color:#7ec87e;background:#1a2e1a;padding:2px 8px;border-radius:20px;font-family:Arial,sans-serif;">{c.get('badge_canonical','')}</span>
        <p style="margin:8px 0 4px;font-size:15px;font-weight:700;color:#1a1a1a;font-family:Arial,sans-serif;">{course_title}</p>
        <p style="margin:0 0 10px;font-size:12px;color:#777;font-family:Arial,sans-serif;">{dates[0] if dates else 'Dates available'} &nbsp;·&nbsp; {c.get('location_canonical','')}</p>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #f0efeb;padding-top:10px;">
          <tr>
            <td><p style="margin:0;font-size:18px;font-weight:700;color:#1a1a1a;font-family:Arial,sans-serif;">{price}</p></td>
            <td align="right"><a href="{booking_url}" style="background:#1a2e1a;color:#fff;font-size:12px;font-weight:500;padding:9px 18px;border-radius:6px;text-decoration:none;font-family:Arial,sans-serif;display:inline-block;">Book Now →</a></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>

<tr><td style="background:#fff;padding:0 32px 24px;text-align:center;">
  <p style="margin:0 0 12px;font-size:13px;color:#666;font-family:Arial,sans-serif;">More courses opening up all season — we'll keep an eye out for you.</p>
  <a href="https://backcountryfinder.com" style="background:#1a2e1a;color:#fff;font-size:13px;font-weight:500;padding:11px 24px;border-radius:6px;text-decoration:none;display:inline-block;font-family:Arial,sans-serif;">browse all courses →</a>
</td></tr>

<tr><td style="background:#1a2e1a;border-radius:0 0 10px 10px;padding:20px 32px;text-align:center;">
  <p style="margin:0 0 8px;font-size:14px;color:rgba(255,255,255,0.6);font-family:Georgia,serif;">backcountry<span style="color:#7ec87e;">finder</span></p>
  <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.3);line-height:1.8;font-family:Arial,sans-serif;">
    <a href="https://backcountryfinder.com" style="color:rgba(255,255,255,0.45);text-decoration:none;">backcountryfinder.com</a> &nbsp;·&nbsp;
    <a href="mailto:hello@backcountryfinder.com" style="color:rgba(255,255,255,0.45);text-decoration:none;">hello@backcountryfinder.com</a><br>
    You're receiving this because you asked to be notified.<br>
    <a href="{unsub_url}" style="color:rgba(255,255,255,0.35);text-decoration:none;">unsubscribe</a>
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

            try:
                resend_key = os.environ.get("RESEND_API_KEY", "")
                res = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={
                        "from": "BackcountryFinder <hello@backcountryfinder.com>",
                        "to": [row["email"]],
                        "subject": f"Dates just dropped — {course_title}",
                        "html": html.replace("{{NOTIF_ID}}", str(notif_id)),
                    },
                    timeout=15,
                )
                if res.ok:
                    # Mark as notified
                    sb_upsert("notifications", [{"id": row["id"], "notified_at": datetime.utcnow().isoformat()}])
                    log.info(f"Notification sent to {row['email']} for {course_title}")
                else:
                    log.warning(f"Resend failed for {row['email']}: {res.text}")
            except Exception as e:
                log.warning(f"Failed to send notification to {row['email']}: {e}")

    except Exception as e:
        log.warning(f"Notification check failed for {course_title}: {e}")





# -- NOTIFY ME: Check and send notifications when courses get dates --

def check_and_notify():
    """
    After each scraper run, check if any courses that had no dates
    now have real dates. If so, email subscribers and mark as notified.
    """
    if not RESEND_API_KEY:
        log.warning("No RESEND_API_KEY — skipping notifications")
        return

    try:
        # Find notifications that haven't been sent yet
        pending = sb_get("notifications", {
            "select": "id,email,provider_id,course_title",
            "notified_at": "is.null"
        })
        if not pending:
            log.info("No pending notifications")
            return

        log.info(f"Checking {len(pending)} pending notifications...")

        # Get all currently active dated courses
        dated_courses = sb_get("courses", {
            "select": "title,provider_id,date_display,date_sort,price,avail,booking_url,badge_canonical,location_canonical,providers(name,rating)",
            "active": "eq.true",
            "custom_dates": "eq.false",
        })

        # Build lookup by (provider_id, title)
        from collections import defaultdict
        course_lookup = defaultdict(list)
        for c in (dated_courses or []):
            key = (c["provider_id"], c["title"].lower().strip())
            course_lookup[key].append(c)

        notified_ids = []
        for notif in pending:
            key = (notif["provider_id"], notif["course_title"].lower().strip())
            matches = course_lookup.get(key, [])
            if not matches:
                continue

            # Course now has dates — send notification email
            log.info(f"Notifying {notif['email']} about {notif['course_title']}")
            try:
                send_notification_email(notif["email"], notif["course_title"], matches, notif["id"])
                notified_ids.append(notif["id"])
            except Exception as e:
                log.error(f"Failed to send notification to {notif['email']}: {e}")

        # Mark as notified
        if notified_ids:
            from datetime import datetime as dt
            now_iso = dt.utcnow().isoformat()
            for nid in notified_ids:
                try:
                    requests.patch(
                        f"{SUPABASE_URL}/rest/v1/notifications?id=eq.{nid}",
                        headers={
                            "apikey": SUPABASE_SERVICE_KEY,
                            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                            "Content-Type": "application/json",
                        },
                        json={"notified_at": now_iso},
                        timeout=10
                    )
                except Exception as e:
                    log.error(f"Failed to mark notification {nid} as sent: {e}")

            log.info(f"Sent {len(notified_ids)} notifications")

    except Exception as e:
        log.error(f"check_and_notify failed: {e}")


def send_notification_email(email, course_title, courses, notif_id=""):
    """Send a notification email when a course gets dates."""
    provider_name = ""
    if courses:
        p = courses[0].get("providers") or {}
        provider_name = p.get("name") if isinstance(p, dict) else courses[0].get("provider_id", "")

    location = courses[0].get("location_canonical", "") if courses else ""
    badge = courses[0].get("badge_canonical", "") if courses else ""
    price = courses[0].get("price") if courses else None
    booking_url = courses[0].get("booking_url", "#") if courses else "#"
    rating = ""
    if courses:
        p = courses[0].get("providers") or {}
        rating = p.get("rating", "") if isinstance(p, dict) else ""

    # Date pills — up to 6
    date_pills = "".join([
        f'<span style="background:#f5f4f0;border:1px solid #e0dfda;border-radius:20px;font-size:11px;font-weight:600;color:#444;padding:4px 12px;margin:3px;display:inline-block;">{c["date_display"]}</span>'
        for c in courses[:6] if c.get("date_display")
    ])

    # First course card
    c = courses[0]
    meta_parts = [p for p in [c.get("date_display"), location, provider_name, f"★ {rating}" if rating else ""] if p]
    meta = " · ".join(meta_parts)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4f0;padding:24px 16px;">
<tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

<tr><td style="background:#1a2e1a;border-radius:10px 10px 0 0;padding:28px 32px;text-align:center;">
  <p style="margin:0 0 4px;font-size:22px;color:#fff;font-family:Georgia,serif;letter-spacing:-0.3px;">backcountry<span style="color:#7ec87e;font-style:italic;">finder</span></p>
  <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.4);">Find your course. Find your line.</p>
</td></tr>

<tr><td style="background:#fff;padding:24px 32px 8px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#eaf3de;border:1px solid #c0dd97;border-radius:10px;padding:18px 20px;margin-bottom:20px;">
    <tr><td>
      <p style="margin:0 0 4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#3b6d11;">Dates just dropped</p>
      <p style="margin:0 0 4px;font-size:17px;font-weight:700;color:#1a2e1a;">{course_title} is now booking</p>
      <p style="margin:0;font-size:12px;color:#639922;font-weight:600;">{provider_name}{' · ' + location if location else ''}</p>
    </td></tr>
  </table>
  <p style="margin:0 0 14px;font-size:14px;color:#444;line-height:1.7;">You asked us to let you know when this course opened up. It just did — here are the available dates:</p>
  <div style="margin-bottom:16px;">{date_pills}</div>
</td></tr>

<tr><td style="background:#fff;padding:0 32px 12px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e8e7e3;border-radius:10px;overflow:hidden;">
    <tr>
      <td width="5" style="background:#1a2e1a;">&nbsp;</td>
      <td style="padding:14px 16px;">
        <p style="margin:0 0 6px;"><span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;color:#7ec87e;background:#1a2e1a;padding:2px 8px;border-radius:20px;">{badge}</span></p>
        <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:#1a1a1a;">{course_title}</p>
        <p style="margin:0 0 10px;font-size:12px;color:#777;line-height:1.5;">{meta}</p>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #f0efeb;padding-top:10px;">
          <tr>
            <td><p style="margin:0;font-size:18px;font-weight:700;color:#1a1a1a;">${price or '—'} <span style="font-size:11px;color:#888;font-weight:400;">CAD</span></p></td>
            <td align="right"><a href="{booking_url}" style="background:#1a2e1a;color:#fff;font-size:12px;font-weight:500;padding:9px 18px;border-radius:6px;text-decoration:none;display:inline-block;">Book Now →</a></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>

<tr><td style="background:#fff;padding:0 32px 24px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#eaf3de;border:1px solid #c0dd97;border-radius:10px;padding:18px 20px;">
    <tr><td>
      <p style="margin:0 0 4px;font-size:14px;font-weight:700;color:#1a2e1a;">Know someone else who'd be keen?</p>
      <p style="margin:0 0 12px;font-size:12px;color:#3b6d11;">Share this course — their list will already have it saved when they open the link.</p>
      <a href="https://wa.me/?text={requests.utils.quote(f'Check out {course_title} — just opened for booking on BackcountryFinder: {booking_url}')}" style="background:#25D366;color:#fff;font-size:12px;font-weight:500;padding:8px 16px;border-radius:6px;text-decoration:none;display:inline-block;">Share via WhatsApp</a>
      <p style="margin:10px 0 0;font-size:11px;color:#639922;">or just forward this email →</p>
    </td></tr>
  </table>
</td></tr>

<tr><td style="background:#fff;padding:0 32px;"><hr style="border:none;border-top:1px solid #f0efeb;margin:0;"></td></tr>
<tr><td style="background:#fff;padding:20px 32px;text-align:center;">
  <p style="margin:0 0 12px;font-size:13px;color:#666;">More courses opening up all season — we'll keep an eye out for you.</p>
  <a href="https://backcountryfinder.com" style="background:#1a2e1a;color:#fff;font-size:13px;font-weight:500;padding:11px 24px;border-radius:6px;text-decoration:none;display:inline-block;">browse all courses →</a>
</td></tr>

<tr><td style="background:#1a2e1a;border-radius:0 0 10px 10px;padding:20px 32px;text-align:center;">
  <p style="margin:0 0 8px;font-size:13px;color:rgba(255,255,255,0.6);font-family:Georgia,serif;">backcountry<span style="color:#7ec87e;">finder</span></p>
  <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.3);line-height:1.8;">
    <a href="https://backcountryfinder.com" style="color:rgba(255,255,255,0.4);text-decoration:none;">backcountryfinder.com</a> &nbsp;·&nbsp;
    <a href="mailto:hello@backcountryfinder.com" style="color:rgba(255,255,255,0.4);text-decoration:none;">hello@backcountryfinder.com</a><br>
    You're receiving this because you asked to be notified about this course.<br>
    <a href="https://owzrztaguehebkatnatc.supabase.co/functions/v1/unsubscribe-notification?id={{NOTIF_ID}}" style="color:rgba(255,255,255,0.35);text-decoration:none;">unsubscribe</a>
  </p>
</td></tr>

</table></td></tr></table>
</body></html>"""

    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": "BackcountryFinder <hello@backcountryfinder.com>",
            "to": [email],
            "subject": f"{course_title} just opened for booking — BackcountryFinder",
            "html": html.replace("{{NOTIF_ID}}", str(notif_id)),
        },
        timeout=15
    )
    if not res.ok:
        raise Exception(f"Resend error {res.status_code}: {res.text}")
    log.info(f"Notification sent to {email} for {course_title}")

# -- GOOGLE PLACES --

def find_place_id(provider_name, location):
    if not GOOGLE_PLACES_API_KEY:
        return None
    try:
        clean_location = re.split(r"[/,]", location)[0].strip() if location else ""
        r = requests.get(
            f"{PLACES_API_URL}/findplacefromtext/json",
            params={"input": f"{provider_name} {clean_location} BC Canada", "inputtype": "textquery", "fields": "place_id,name", "key": GOOGLE_PLACES_API_KEY},
            timeout=10
        )
        candidates = r.json().get("candidates", [])
        if candidates:
            pid = candidates[0]["place_id"]
            log.info(f"Found Place ID for {provider_name}: {pid}")
            return pid
    except Exception as e:
        log.warning(f"Place ID lookup failed for {provider_name}: {e}")
    return None


def get_place_details(place_id):
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return {}
    try:
        r = requests.get(
            f"{PLACES_API_URL}/details/json",
            params={"place_id": place_id, "fields": "rating,user_ratings_total", "key": GOOGLE_PLACES_API_KEY},
            timeout=10
        )
        result = r.json().get("result", {})
        return {"rating": result.get("rating"), "review_count": result.get("user_ratings_total")}
    except Exception as e:
        log.warning(f"Place details fetch failed for {place_id}: {e}")
    return {}


def update_provider_ratings(provider_filter='all'):
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key -- skipping ratings update")
        return
    log.info("Updating provider ratings from Google Places...")
    places_params = {"select": "id,name,location,google_place_id", "active": "eq.true"}
    if provider_filter != "all":
        places_params["id"] = f"eq.{provider_filter}"
    providers = sb_get("providers", places_params)
    for p in providers:
        pid = p.get("google_place_id")
        if not pid:
            pid = find_place_id(p["name"], p.get("location", ""))
            if pid:
                sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid}])
            time.sleep(0.5)
        if not pid:
            log.warning(f"No Place ID found for {p['name']} -- skipping")
            continue
        details = get_place_details(pid)
        if details.get("rating"):
            sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid, "rating": details["rating"], "review_count": details.get("review_count")}])
            log.info(f"{p['name']}: star {details['rating']} ({details.get('review_count', 0)} reviews)")
        time.sleep(0.5)
    log.info("Provider ratings update complete")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="all", help="Provider to scrape: altus, msaa, cwms, summit, iag, hvi, srg, skaha-rock-adventures, or all")
    args = parser.parse_args()
    provider_filter = args.provider.lower()

    log.info(f"=== BackcountryFinder scraper starting (provider={provider_filter}) ===")

    # Update provider ratings from Google Places
    update_provider_ratings(provider_filter)

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    # Load activity mappings and labels from Supabase
    activity_maps = load_activity_mappings_table()
    log.info(f"Loaded {len(activity_maps)} activity mappings")
    global ACTIVITY_LABELS
    ACTIVITY_LABELS = load_activity_labels()
    log.info(f"Loaded {len(ACTIVITY_LABELS)} activity labels")

    all_courses = []
    location_flags = []
    provider_summary = []

    # Scrape Skaha Rock Adventures
    if provider_filter in ("all", "skaha-rock-adventures"):
        raw_courses = scrape_skaha()
        processed = []
        for c in raw_courses:
            loc_canonical = "Penticton / Skaha Bluffs"
            course_id = stable_id(SKAHA_PROVIDER["id"], c["activity"], c.get("date_sort"), c["title"])
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], c.get("description", ""), activity_maps, SKAHA_PROVIDER["name"])
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            processed.append({
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        SKAHA_PROVIDER["id"],
                "badge":              badge_canonical,
                "activity":           activity_canonical,
                "activity_raw":       c.get("activity_raw", c["activity"]),
                "activity_canonical": activity_canonical,
                "badge_canonical":    badge_canonical,
                "location_raw":       "Penticton, BC",
                "location_canonical": loc_canonical,
                "date_display":       c.get("date_display"),
                "date_sort":          c.get("date_sort"),
                "duration_days":      c.get("duration_days"),
                "price":              c.get("price"),
                "spots_remaining":    None,
                "avail":              c.get("avail", "open"),
                "image_url":          None,
                "booking_url":        c.get("booking_url"),
                "active":             c.get("avail") != "sold",
                "custom_dates":       c.get("custom_dates", False),
                "summary":            "",
                "description":        c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })
        if processed:
            seen_titles = {}
            unique_inputs = []
            for c in processed:
                if c.get("description") and c["title"] not in seen_titles:
                    seen_titles[c["title"]] = c["id"]
                    unique_inputs.append({"id": c["id"], "title": c["title"], "description": c.get("description", ""), "provider": SKAHA_PROVIDER["name"], "activity": c.get("activity_canonical", "climbing")})
            if unique_inputs:
                summaries = generate_summaries_batch(unique_inputs)
                title_to_summary = {c["title"]: summaries.get(c["id"], "") for c in unique_inputs}
                for c in processed:
                    c["summary"] = title_to_summary.get(c["title"], "")
                log.info(f"Summaries generated: {len(summaries)}")
        for c in processed:
            c.pop("description", None)
        provider_summary.append({"name": SKAHA_PROVIDER["name"], "count": len(processed), "ok": len(processed) > 0})
        all_courses.extend(processed)
        time.sleep(2)

    # Scrape Canada West (WooCommerce)
    for provider in (CWMS_PROVIDERS if provider_filter in ("all", "cwms") else []):
        raw_courses = scrape_cwms(provider)
        processed = []
        for c in raw_courses:
            if not is_future(c.get("date_sort")):
                continue
            loc_raw = c.get("location_raw") or ""
            if loc_raw:
                loc_canonical = normalise_location(loc_raw, mappings)
                if not loc_canonical:
                    location_flags.append({"location_raw": loc_raw, "provider_id": provider["id"], "course_title": c["title"]})
            else:
                loc_canonical = None
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])
            activity_raw = c.get("activity_raw") or "guided"
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], c.get("location_raw") or "", activity_maps, provider["name"])
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            booking_url = c.get("booking_url")
            active = True
            custom_dates = True  # CWMS uses WooCommerce date picker
            date_display = "Flexible dates"
            date_sort = None
            processed.append({
                "id": course_id, "title": c["title"], "provider_id": provider["id"],
                "badge": badge_canonical, "activity": activity_canonical,
                "activity_raw": activity_raw, "activity_canonical": activity_canonical,
                "badge_canonical": badge_canonical, "location_raw": loc_raw or None,
                "location_canonical": loc_canonical, "date_display": date_display,
                "date_sort": date_sort, "duration_days": c.get("duration_days"),
                "price": c.get("price"), "spots_remaining": None, "avail": "open",
                "image_url": c.get("image_url"), "booking_url": booking_url,
                "active": active, "custom_dates": custom_dates,
                "summary": "", "description": c.get("description", ""),
                "scraped_at": c["scraped_at"],
            })
        # For each CWMS course, visit the page and create one row per date
        dated_processed = []
        for course in processed:
            booking_url = course.get("booking_url")
            if not booking_url:
                dated_processed.append(course)
                continue

            sessions, page_description = scrape_cwms_course_page(booking_url)
            if page_description:
                course["description"] = page_description
            time.sleep(0.5)

            if not sessions:
                # No dates found — keep as flexible dates card
                dated_processed.append(course)
                continue

            # Sessions found — create one card per date, discard flexible dates card
            # Create one row per date session
            for session in sessions:
                product_id = session.get("product_id")
                # Deep-link to specific date if we have a product ID
                if product_id:
                    base_url = booking_url.split("?")[0]
                    session_url = f"{base_url}?add-to-cart={product_id}&{provider['utm']}"
                else:
                    session_url = booking_url

                # Build stable ID using date_sort
                date_sort = session.get("date_sort")
                # Include product_id as tiebreaker to avoid duplicate stable IDs
                id_key = f"{course['title']} {session.get('product_id', '')}"
                session_id = stable_id(provider["id"], course["activity"], date_sort, id_key)

                session_course = dict(course)
                session_course.update({
                    "id":            session_id,
                    "date_display":  session.get("date_display"),
                    "date_sort":     date_sort,
                    "spots_remaining": session.get("spots_remaining"),
                    "avail":         session.get("avail", "open"),
                    "booking_url":   session_url,
                    "custom_dates":  False,
                    "active":        session.get("avail") != "sold",
                    "description":   course.get("description", ""),
                })
                dated_processed.append(session_course)

        # Batch generate summaries for all CWMS courses
        if dated_processed:
            log.info(f"Generating summaries for {len(dated_processed)} {provider['name']} courses...")
            summary_inputs = [
                {
                    "id":          c["id"],
                    "title":       c["title"],
                    "description": c.get("description", ""),
                    "provider":    provider["name"],
                    "activity":    c.get("activity_canonical", c.get("activity", "")),
                }
                for c in dated_processed
            ]
            summaries = generate_summaries_batch(summary_inputs)
            for c in dated_processed:
                if c["id"] in summaries:
                    c["summary"] = summaries[c["id"]]
            log.info(f"Summaries generated: {len(summaries)}")

        provider_summary.append({"name": provider["name"], "count": len(dated_processed), "ok": len(dated_processed) > 0})
        all_courses.extend(dated_processed)
        time.sleep(2)

    # Scrape Island Alpine Guides + Hike Vancouver Island
    for provider in [p for p in IAG_PROVIDERS if provider_filter in ("all", p["id"])]:
        raw_courses = scrape_iag_style(provider)
        processed = []
        for c in raw_courses:
            loc_canonical = normalise_location(c.get("location_raw",""), mappings)
            if not loc_canonical:
                loc_canonical = "Vancouver Island"
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], "", activity_maps, provider["name"])
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            processed.append({
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        provider["id"],
                "badge":              badge_canonical,
                "activity":           activity_canonical,
                "activity_raw":       c.get("activity_raw", "guided"),
                "activity_canonical": activity_canonical,
                "badge_canonical":    badge_canonical,
                "location_raw":       c.get("location_raw"),
                "location_canonical": loc_canonical,
                "date_display":       c.get("date_display"),
                "date_sort":          c.get("date_sort"),
                "duration_days":      c.get("duration_days"),
                "price":              c.get("price"),
                "spots_remaining":    c.get("spots_remaining"),
                "avail":              c.get("avail", "open"),
                "image_url":          c.get("image_url"),
                "booking_url":        c.get("booking_url"),
                "active":             True,
                "custom_dates":       c.get("custom_dates", False),
                "summary":            "",
                "description":        c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })

        # Batch summaries — deduplicate by title
        if processed:
            seen_titles = {}
            unique_inputs = []
            for c in processed:
                if c.get("description") and c["title"] not in seen_titles:
                    seen_titles[c["title"]] = c["id"]
                    unique_inputs.append({"id": c["id"], "title": c["title"], "description": c.get("description",""), "provider": provider["name"], "activity": c.get("activity_canonical","guided")})
            if unique_inputs:
                summaries = generate_summaries_batch(unique_inputs)
                title_to_summary = {c["title"]: summaries.get(c["id"], "") for c in unique_inputs}
                for c in processed:
                    c["summary"] = title_to_summary.get(c["title"], "")

        for c in processed:
            c.pop("description", None)

        provider_summary.append({"name": provider["name"], "count": len(processed), "ok": len(processed) > 0})
        all_courses.extend(processed)
        time.sleep(2)

    # Scrape Squamish Rock Guides
    for provider in (SRG_PROVIDERS if provider_filter in ("all", "srg") else []):
        raw_courses = scrape_srg(provider)
        processed = []
        for c in raw_courses:
            loc_canonical = "Squamish"  # always Squamish
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], "", activity_maps, provider["name"])
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            processed.append({
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        provider["id"],
                "badge":              badge_canonical,
                "activity":           activity_canonical,
                "activity_raw":       c.get("activity_raw", "climbing"),
                "activity_canonical": activity_canonical,
                "badge_canonical":    badge_canonical,
                "location_raw":       "Squamish",
                "location_canonical": loc_canonical,
                "date_display":       c.get("date_display"),
                "date_sort":          c.get("date_sort"),
                "duration_days":      c.get("duration_days"),
                "price":              c.get("price"),
                "spots_remaining":    None,
                "avail":              "open",
                "image_url":          c.get("image_url"),
                "booking_url":        c.get("booking_url"),
                "active":             True,
                "custom_dates":       c.get("custom_dates", False),
                "summary":            "",
                "description":        c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })

        # Batch summaries
        if processed:
            summary_inputs = [{"id": c["id"], "title": c["title"], "description": c.get("description",""), "provider": provider["name"], "activity": c.get("activity_canonical","climbing")} for c in processed if c.get("description")]
            if summary_inputs:
                # Deduplicate by title — same summary for all dates of same course
                seen_titles = {}
                unique_inputs = []
                for s in summary_inputs:
                    if s["title"] not in seen_titles:
                        seen_titles[s["title"]] = s["id"]
                        unique_inputs.append(s)
                summaries = generate_summaries_batch(unique_inputs)
                # Apply summary to all cards with same title
                title_to_summary = {s["title"]: summaries.get(s["id"], "") for s in unique_inputs}
                for c in processed:
                    c["summary"] = title_to_summary.get(c["title"], "")

        for c in processed:
            c.pop("description", None)

        provider_summary.append({"name": provider["name"], "count": len(processed), "ok": len(processed) > 0})
        all_courses.extend(processed)
        time.sleep(2)

    # Scrape Summit Mountain Guides (Events Calendar)
    for provider in (SUMMIT_PROVIDERS if provider_filter in ("all", "summit") else []):
        raw_courses = scrape_summit(provider)
        processed = []
        for c in raw_courses:
            loc_raw = c.get("location_raw") or ""
            if loc_raw:
                loc_canonical = normalise_location(loc_raw, mappings)
                if not loc_canonical:
                    location_flags.append({"location_raw": loc_raw, "provider_id": provider["id"], "course_title": c["title"]})
            else:
                loc_canonical = None
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], "", activity_maps, provider["name"])
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            processed.append({
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        provider["id"],
                "badge":              badge_canonical,
                "activity":           activity_canonical,
                "activity_raw":       c.get("activity_raw", "guided"),
                "activity_canonical": activity_canonical,
                "badge_canonical":    badge_canonical,
                "location_raw":       loc_raw or None,
                "location_canonical": loc_canonical,
                "date_display":       c.get("date_display"),
                "date_sort":          c.get("date_sort"),
                "duration_days":      c.get("duration_days"),
                "price":              c.get("price"),
                "spots_remaining":    c.get("spots_remaining"),
                "avail":              c.get("avail", "open"),
                "image_url":          c.get("image_url"),
                "booking_url":        c.get("booking_url"),
                "active":             True,
                "custom_dates":       False,
                "summary":            "",
                "description":        c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })

        # Batch generate summaries
        if processed:
            summary_inputs = [{"id": c["id"], "title": c["title"], "description": c.get("description",""), "provider": provider["name"], "activity": c.get("activity_canonical","guided")} for c in processed if c.get("description")]
            if summary_inputs:
                summaries = generate_summaries_batch(summary_inputs)
                for c in processed:
                    if c["id"] in summaries:
                        c["summary"] = summaries[c["id"]]

        for c in processed:
            c.pop("description", None)

        provider_summary.append({"name": provider["name"], "count": len(processed), "ok": len(processed) > 0})
        all_courses.extend(processed)
        time.sleep(2)

    # Scrape Rezdy providers
    active_rezdy = [p for p in REZDY_PROVIDERS if provider_filter == "all" or p["id"] == provider_filter]
    for provider in active_rezdy:
        raw_courses = scrape_rezdy(provider)
        processed = []

        for c in raw_courses:
            # Skip past courses
            if not is_future(c.get("date_sort")):
                log.info(f"Skipping past course: {c['title']}")
                continue

            # Normalise location
            loc_raw = c.get("location_raw") or ""
            if loc_raw:
                loc_canonical = normalise_location(loc_raw, mappings)
                if not loc_canonical:
                    log.warning(f"Unmatched location: '{loc_raw}' for '{c['title']}'")
                    location_flags.append({"location_raw": loc_raw, "provider_id": provider["id"], "course_title": c["title"]})
            else:
                loc_canonical = None

            # Build stable ID
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])

            # Resolve canonical activity
            activity_raw = c.get("activity_raw") or c.get("activity") or "guided"
            desc = ""
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], desc, activity_maps, provider["name"])
            if act_add_mapping:
                # Write new mapping to Supabase immediately
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))
                if act_is_new:
                    log.info(f"New canonical activity added: '{activity_canonical}' for '{c['title']}'")
            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))


            # Check individual course page for availability and dates
            booking_url = c.get("booking_url")
            active = True
            custom_dates = False
            date_display = c.get("date_display")
            date_sort = c.get("date_sort")

            page_description = ""
            if booking_url:
                page_check = check_course_page(booking_url)
                page_description = page_check.get("description", "")
                if not page_check["available"]:
                    # Keep visible as flexible dates with Notify me button
                    log.info(f"No availability — showing as flexible dates: {c['title']}")
                    custom_dates = True
                    date_display = "Flexible dates"
                    date_sort = None
                    active = True
                else:
                    custom_dates = page_check["custom_dates"]
                    if custom_dates:
                        date_display = "Flexible dates"
                        date_sort = None
                    elif page_check["dates"] and not date_display:
                        date_display = page_check["dates"][0]
                        date_sort = parse_date_sort(date_display)
                time.sleep(0.5)

            processed.append({
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        provider["id"],
                "badge":              badge_canonical,
                "activity":           activity_canonical,
                "activity_raw":       activity_raw,
                "activity_canonical": activity_canonical,
                "badge_canonical":    badge_canonical,
                "location_raw":       loc_raw or None,
                "location_canonical": loc_canonical,
                "date_display":       date_display,
                "date_sort":          date_sort,
                "duration_days":      c.get("duration_days"),
                "price":              c.get("price"),
                "spots_remaining":    c.get("spots_remaining"),
                "avail":              c.get("avail", "open"),
                "image_url":          c.get("image_url"),
                "booking_url":        booking_url,
                "active":             active,
                "custom_dates":       custom_dates,
                "summary":            c.get("summary", ""),
                "description":        page_description or c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })

        # Batch generate summaries for Rezdy courses
        if processed:
            log.info(f"Generating summaries for {len(processed)} {provider['name']} courses...")
            summary_inputs = [
                {
                    "id":          c["id"],
                    "title":       c["title"],
                    "description": c.get("description", ""),
                    "provider":    provider["name"],
                    "activity":    c.get("activity_canonical", c.get("activity", "")),
                }
                for c in processed if c.get("description")
            ]
            if summary_inputs:
                summaries = generate_summaries_batch(summary_inputs)
                for c in processed:
                    if c["id"] in summaries:
                        c["summary"] = summaries[c["id"]]
                log.info(f"Summaries generated: {len(summaries)}")

        provider_summary.append({
            "name":  provider["name"],
            "count": len(processed),
            "ok":    len(processed) > 0,
        })

        all_courses.extend(processed)
        time.sleep(2)

    # Upsert to Supabase (only if we got data — fallback protection)
    if all_courses:
        # Deduplicate by ID — last one wins if duplicates exist
        seen = {}
        for c in all_courses:
            seen[c["id"]] = c
        deduped = list(seen.values())
        if len(deduped) < len(all_courses):
            log.warning(f"Deduplicated {len(all_courses) - len(deduped)} duplicate course IDs before upsert")

        # Strip description — it's a scrape-time field, not stored in Supabase
        for c in deduped:
            c.pop("description", None)
        sb_upsert("courses", deduped)
        log.info(f"Total courses upserted: {len(deduped)}")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    # Flag unmatched locations
    if location_flags:
        for flag in location_flags:
            sb_insert("location_flags", flag)
        # EMAILS OFF
        # send_flag_email(location_flags)

    # Send summary email
    # EMAILS OFF
    # send_scrape_summary(len(all_courses), provider_summary, len(location_flags))

    check_and_notify()
    log.info("=== Scraper complete ===")


if __name__ == "__main__":
    main()
