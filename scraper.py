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

# ── CONFIG ──
SUPABASE_URL          = os.environ["SUPABASE_URL"]
SUPABASE_KEY          = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY        = os.environ["RESEND_API_KEY"]
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
NOTIFY_EMAIL          = "luke@backcountryfinder.com"
FROM_EMAIL            = "luke@backcountryfinder.com"
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
        return [(r["title_contains"].lower(), r["activity"]) for r in rows]
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
        if result.get("activity"):
            activity = result["activity"]
            is_new = result.get("is_new", False)
            label = result.get("label", activity.replace("_", " ").title())
            log.info(f"Claude classified '{title}' as '{activity}' (new={is_new}): {result.get('reasoning','')}")
            sb_insert("activity_labels", {"activity": activity, "label": label})
            return activity, is_new, True
    return detect_activity(title, description), False, False



def build_badge(activity: str, duration_days) -> str:
    """Build a clean badge string from canonical activity and duration."""
    label = ACTIVITY_LABELS.get(activity, activity.title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


def normalise_location(raw, mappings):
    """
    Normalise a raw location string to canonical value.
    Returns (canonical, is_new, should_add_mapping)
    """
    if not raw:
        return None, False, False
    key = raw.lower().strip()
    if key in mappings:
        return mappings[key], False, False
    for known_raw, canonical in mappings.items():
        if known_raw in key or key in known_raw:
            return canonical, False, False
    if ANTHROPIC_API_KEY:
        known = get_known_locations(mappings)
        result = claude_classify_location(raw, known)
        if result.get("location_canonical"):
            canonical = result["location_canonical"]
            is_new = result.get("is_new", False)
            log.info(f"Claude normalised '{raw}' to '{canonical}' (new={is_new})")
            return canonical, is_new, True
    return None, False, False


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
                            avail = "low" if spots <= 4 else "open"

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

    except Exception as e:
        log.warning(f"Could not scrape CWMS course page {course_url}: {e}")

    return sessions


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
                            avail = "low" if spots <= 4 else "open"

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

    except Exception as e:
        log.warning(f"Could not scrape CWMS course page {course_url}: {e}")

    return sessions


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

    except Exception as e:
        log.warning(f"Could not check course page {booking_url}: {e}")
        # If we can't check, assume available to avoid hiding valid courses
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

def claude_classify(prompt: str) -> dict:
    """Call Claude API and return parsed JSON response."""
    if not ANTHROPIC_API_KEY:
        return {}
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
                "max_tokens": 256,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20
        )
        text = r.json()["content"][0]["text"].strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return {}


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


def get_known_activities(activity_maps: list) -> list:
    """Extract unique activity values from the mappings list."""
    return list(set(activity for _, activity in activity_maps))


def get_known_locations(location_maps: dict) -> list:
    """Extract unique canonical location values from the mappings dict."""
    return list(set(location_maps.values()))


# -- GOOGLE PLACES --

def find_place_id(provider_name, location):
    if not GOOGLE_PLACES_API_KEY:
        return None
    try:
        r = requests.get(
            f"{PLACES_API_URL}/findplacefromtext/json",
            params={"input": f"{provider_name} {location} BC Canada", "inputtype": "textquery", "fields": "place_id,name", "key": GOOGLE_PLACES_API_KEY},
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
    parser.add_argument("--provider", default="all", help="Provider to scrape: altus, msaa, cwms, or all")
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

    # Scrape Canada West (WooCommerce)
    for provider in (CWMS_PROVIDERS if provider_filter in ("all", "cwms") else []):
        raw_courses = scrape_cwms(provider)
        processed = []
        for c in raw_courses:
            if not is_future(c.get("date_sort")):
                continue
            loc_raw = c.get("location_raw") or ""
            if loc_raw:
                loc_canonical, loc_is_new, loc_add_mapping = normalise_location(loc_raw, mappings)
                if loc_add_mapping:
                    sb_insert("location_mappings", {"location_raw": loc_raw, "location_canonical": loc_canonical})
                    mappings[loc_raw.lower().strip()] = loc_canonical
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
                "scraped_at": c["scraped_at"],
            })
        # For each CWMS course, visit the page and create one row per date
        dated_processed = []
        for course in processed:
            booking_url = course.get("booking_url")
            if not booking_url:
                dated_processed.append(course)
                continue

            sessions = scrape_cwms_course_page(booking_url)
            time.sleep(0.5)

            if not sessions:
                # No dates found — keep as flexible dates
                dated_processed.append(course)
                continue

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
                })
                dated_processed.append(session_course)

        provider_summary.append({"name": provider["name"], "count": len(dated_processed), "ok": len(dated_processed) > 0})
        all_courses.extend(dated_processed)
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
                loc_canonical, loc_is_new, loc_add_mapping = normalise_location(loc_raw, mappings)
                if loc_add_mapping:
                    # Write new mapping to Supabase immediately
                    sb_insert("location_mappings", {"location_raw": loc_raw, "location_canonical": loc_canonical})
                    mappings[loc_raw.lower().strip()] = loc_canonical
                    if loc_is_new:
                        log.info(f"New canonical location added: '{loc_raw}' -> '{loc_canonical}'")
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

            if booking_url:
                page_check = check_course_page(booking_url)
                if not page_check["available"]:
                    log.info(f"Hiding unavailable course: {c['title']}")
                    active = False
                else:
                    custom_dates = page_check["custom_dates"]
                    if custom_dates:
                        date_display = "Flexible dates"
                        date_sort = None
                    elif page_check["dates"] and not date_display:
                        # Use first static date found
                        date_display = page_check["dates"][0]
                        date_sort = parse_date_sort(date_display)
                time.sleep(0.5)  # be polite between course page requests

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
                "scraped_at":         c["scraped_at"],
            })

        provider_summary.append({
            "name":  provider["name"],
            "count": len(processed),
            "ok":    len(processed) > 0,
        })

        all_courses.extend(processed)
        time.sleep(2)  # be polite between providers

    # Upsert to Supabase (only if we got data — fallback protection)
    if all_courses:
        sb_upsert("courses", all_courses)
        log.info(f"Total courses upserted: {len(all_courses)}")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    # Flag unmatched locations
    if location_flags:
        for flag in location_flags:
            sb_insert("location_flags", flag)
        send_flag_email(location_flags)

    # Send summary email
    send_scrape_summary(len(all_courses), provider_summary, len(location_flags))

    log.info("=== Scraper complete ===")


if __name__ == "__main__":
    main()
