#!/usr/bin/env python3
"""
BackcountryFinder scraper — Rezdy providers (Altus + MSAA)
Runs every 6 hours via GitHub Actions
"""

import os
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
NOTIFY_EMAIL          = "luke@backcountryfinder.com"
FROM_EMAIL            = "luke@backcountryfinder.com"
PLACES_API_URL        = "https://maps.googleapis.com/maps/api/place"

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


def load_activity_mappings_table() -> list:
    """Load activity mappings from Supabase — [{title_contains, activity}]."""
    try:
        rows = sb_get("activity_mappings", {"select": "title_contains,activity"})
        return [(r["title_contains"].lower(), r["activity"]) for r in rows]
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def resolve_activity(title: str, description: str, mappings: list) -> str:
    """Resolve activity using mappings table first, then keyword detection."""
    text = (title + " " + description).lower()
    # Check mappings table first — both sides lowercased
    for pattern, activity in mappings:
        if pattern.lower() in text:
            return activity
    # Fall back to keyword detection
    return detect_activity(title, description)


def build_badge(activity: str, duration_days) -> str:
    """Build a clean badge string from canonical activity and duration."""
    label = ACTIVITY_LABELS.get(activity, activity.title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


def normalise_location(raw: str, mappings: dict) -> Optional[str]:
    if not raw:
        return None
    key = raw.lower().strip()
    # Exact match
    if key in mappings:
        return mappings[key]
    # Partial match — check if any known raw string is contained in the scraped string
    for known_raw, canonical in mappings.items():
        if known_raw in key or key in known_raw:
            return canonical
    return None


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


# -- GOOGLE PLACES --

def find_place_id(provider_name, location):
    if not GOOGLE_PLACES_API_KEY:
        return None
    try:
        r = requests.get(
            f"{PLACES_API_URL}/findplacefromtext/json",
            params={"input": f"{provider_name} {location}", "inputtype": "textquery", "fields": "place_id,name", "key": GOOGLE_PLACES_API_KEY},
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


def update_provider_ratings():
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key -- skipping ratings update")
        return
    log.info("Updating provider ratings from Google Places...")
    providers = sb_get("providers", {"select": "id,name,location,google_place_id", "active": "eq.true"})
    for p in providers:
        pid = p.get("google_place_id")
        if not pid:
            pid = find_place_id(p["name"], p.get("location", ""))
            if pid:
                sb_upsert("providers", [{"id": p["id"], "google_place_id": pid}])
            time.sleep(0.5)
        if not pid:
            log.warning(f"No Place ID found for {p['name']} -- skipping")
            continue
        details = get_place_details(pid)
        if details.get("rating"):
            sb_upsert("providers", [{"id": p["id"], "google_place_id": pid, "rating": details["rating"], "review_count": details.get("review_count")}])
            log.info(f"{p['name']}: star {details['rating']} ({details.get('review_count', 0)} reviews)")
        time.sleep(0.5)
    log.info("Provider ratings update complete")

def main():
    log.info("=== BackcountryFinder scraper starting ===")

    # Update provider ratings from Google Places
    update_provider_ratings()

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    # Load activity mappings table
    activity_maps = load_activity_mappings_table()
    log.info(f"Loaded {len(activity_maps)} activity mappings")

    all_courses = []
    location_flags = []
    provider_summary = []

    for provider in REZDY_PROVIDERS:
        raw_courses = scrape_rezdy(provider)
        processed = []

        for c in raw_courses:
            # Skip past courses
            if not is_future(c.get("date_sort")):
                log.info(f"Skipping past course: {c['title']}")
                continue

            # Normalise location
            loc_raw = c.get("location_raw") or ""
            loc_canonical = normalise_location(loc_raw, mappings) if loc_raw else None

            if loc_raw and not loc_canonical:
                log.warning(f"Unmatched location: '{loc_raw}' for '{c['title']}'")
                location_flags.append({
                    "location_raw": loc_raw,
                    "provider_id":  provider["id"],
                    "course_title": c["title"],
                })

            # Build stable ID
            course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])

            # Resolve canonical activity using mappings table
            activity_raw = c.get("activity_raw") or c.get("activity") or "guided"
            desc = ""
            activity_canonical = resolve_activity(c["title"], desc, activity_maps)
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
