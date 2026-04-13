#!/usr/bin/env python3
"""
scraper_altus.py — Standalone Rezdy scraper for Altus Mountain Guides.

Extracts the Rezdy scraping logic from scraper.py into a standalone file.
Uses the same HTML selectors, price parsing, date parsing, etc.
"""

import os
import re
import json
import time
import hashlib
import logging
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
NOTIFY_EMAIL          = "hello@backcountryfinder.com"
FROM_EMAIL            = "hello@backcountryfinder.com"
PLACES_API_URL        = "https://maps.googleapis.com/maps/api/place"
CLAUDE_MODEL          = "claude-haiku-4-5-20251001"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

PROVIDER = {
    "id":       "altus",
    "name":     "Altus Mountain Guides",
    "storefront": "https://altusmountainguides.rezdy.com",
    "catalogs": [
        "catalog/540907/altus-ast-1",
        "catalog/540908/altus-ast-1",
        "catalog/628633/altus-ast-2",
    ],
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
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

ACTIVITY_KEYWORDS = {
    "skiing":         ["ast", "avalanche", "backcountry ski", "ski touring", "splitboard", "avy", "heli ski", "cat ski"],
    "hiking":         ["hik", "backpack", "navigation", "wilderness travel", "heli-accessed hik", "heli access"],
    "climbing":       ["climb", "rock", "multi-pitch", "rappel", "belay", "trad", "sport climb", "via ferrata", "ferrata"],
    "mountaineering": ["glacier", "mountaineer", "alpine", "crampon", "crevasse", "scramble", "summit", "alpine climb"],
    "biking":         ["bike", "biking", "mtb", "mountain bike", "cycling"],
    "fishing":        ["fish", "fly fish", "angl", "cast", "river guide"],
    "heli":           ["heli adventure", "heli tour", "heli experience"],
}

NO_AVAILABILITY_SIGNALS = [
    "no availability",
    "please try again later",
    "no sessions available",
    "not available",
    "sold out",
    "no upcoming",
]

STATIC_DATE_PATTERNS = [
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+20\d{2}",
    r"20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])",
    r"\d{1,2}/\d{1,2}/20\d{2}",
]

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


# ── GOOGLE PLACES ──

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


def update_provider_ratings(provider_id):
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key -- skipping ratings update")
        return
    log.info("Updating provider ratings from Google Places...")
    places_params = {"select": "id,name,location,google_place_id", "id": f"eq.{provider_id}"}
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


# ── LOCATION NORMALISATION ──

def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def get_known_locations(location_maps: dict) -> list:
    """Extract unique canonical location values from the mappings dict."""
    return list(set(location_maps.values()))


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


# ── ACTIVITY RESOLUTION ──

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
        mappings = [(r["title_contains"].lower(), r["activity"]) for r in rows]
        return sorted(mappings, key=lambda x: len(x[0]), reverse=True)
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def detect_activity(title: str, description: str = "") -> str:
    text = (title + " " + description).lower()
    for activity, keywords in ACTIVITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return activity
    return "guided"


def get_known_activities(activity_maps: list) -> list:
    """Extract unique activity values from the mappings list."""
    return list(set(activity for _, activity in activity_maps))


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


# ── CLAUDE API ──

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
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return "" if return_text else {}


def generate_summaries_batch(courses: list) -> dict:
    """
    Batch generate 2-sentence summaries for a list of courses.
    courses: list of dicts with keys: id, title, description, provider, activity
    Returns dict: {course_id: summary_text}
    """
    if not ANTHROPIC_API_KEY:
        return {}

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


# ── DATE HELPERS ──

def parse_date_sort(date_str: str) -> Optional[str]:
    """Try to extract a YYYY-MM-DD date from various string formats."""
    if not date_str:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str[:10]
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
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
        return True
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


# ── STABLE ID ──

def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}"
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    return f"{provider_id}-{activity}-{h}"


# ── AVAILABILITY ──

def spots_to_avail(spots: Optional[int]) -> str:
    if spots is None:
        return "open"
    if spots == 0:
        return "sold"
    if spots <= 4:
        return "low"
    return "open"


# ── EMAIL ──

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
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">location flags</p>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:#f8f8f8;">
            <th style="padding:8px 12px;text-align:left;">Raw Location</th>
            <th style="padding:8px 12px;text-align:left;">Provider</th>
            <th style="padding:8px 12px;text-align:left;">Course</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>"""
    send_email(NOTIFY_EMAIL, f"Location flags — {len(flags)} unmatched", html)


def send_scrape_summary(total: int, provider_name: str, flags_count: int) -> None:
    status = "ok" if total > 0 else "failed"
    color = "#2d6a11" if total > 0 else "#a32d2d"
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
        <p style="font-size:13px;color:{color};background:#f8f8f8;padding:10px 14px;border-radius:6px;">{status} — {provider_name}</p>
        {'<p style="font-size:13px;color:#854f0b;background:#faeeda;padding:10px 14px;border-radius:6px;">⚠ ' + str(flags_count) + ' unmatched location string' + ('s' if flags_count>1 else '') + ' — check your other email for details.</p>' if flags_count else '<p style="font-size:13px;color:#2d6a11;background:#eaf3de;padding:10px 14px;border-radius:6px;">✓ All locations normalised cleanly.</p>'}
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
      </div>
    </div>"""
    send_email(NOTIFY_EMAIL, f"{provider_name} scraper — {total} courses updated", html)


# ── REZDY SCRAPING FUNCTIONS ──

def scrape_rezdy(provider: dict) -> list:
    """Scrape a Rezdy storefront using confirmed HTML structure."""
    log.info(f"Scraping {provider['name']} — {provider['storefront']}")

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
        clean_url = booking_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        text = r.text.lower()
        soup = BeautifulSoup(r.text, "html.parser")

        for signal in NO_AVAILABILITY_SIGNALS:
            if signal in text:
                log.info(f"No availability found at {clean_url}")
                result["available"] = False
                return result

        page_text = soup.get_text()
        found_dates = []
        for pattern in STATIC_DATE_PATTERNS:
            matches = re.findall(pattern, page_text)
            found_dates.extend(matches)

        if found_dates:
            log.info(f"Found {len(found_dates)} static dates at {clean_url}")
            result["dates"] = list(set(found_dates))
        else:
            log.info(f"No static dates found at {clean_url} — marking as custom dates")
            result["custom_dates"] = True

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


# ── WEBSITE SCRAPING (Pass 2 — WordPress course/trip pages) ──

WEBSITE_BASE = "https://altusmountainguides.com"

LISTING_PAGES = [
    f"{WEBSITE_BASE}/mountaineering-courses",
    f"{WEBSITE_BASE}/climbing-courses",
    f"{WEBSITE_BASE}/climbing-trips",
]

# Date patterns for specific dates like "May 29 - June 1" or "July 4 & 5"
WP_DATE_PATTERN = re.compile(
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+(\d{1,2})"
    r"(?:\s*[-–&]\s*"
    r"(?:(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)?"
    r"(\d{1,2}))?"
    r"(?:,?\s*(20\d{2}))?",
    re.IGNORECASE,
)

# Seasonal patterns like "Saturdays (May - Sept)" — no specific dates
SEASONAL_PATTERN = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\s*\(",
    re.IGNORECASE,
)


def collect_website_urls() -> list:
    """Scrape listing pages and collect individual course/trip URLs."""
    seen = set()
    urls = []
    for listing_url in LISTING_PAGES:
        log.info(f"  Fetching listing: {listing_url}")
        try:
            r = requests.get(listing_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.search(r"altusmountainguides\.com/(courses|trips)/[^/]+", href):
                    clean = href.split("?")[0].rstrip("/")
                    if clean not in seen:
                        seen.add(clean)
                        urls.append(clean)
        except Exception as e:
            log.warning(f"  Failed to fetch listing {listing_url}: {e}")
        time.sleep(0.5)
    log.info(f"  Found {len(urls)} course/trip URLs from website")
    return urls


def parse_wp_dates(text: str) -> list:
    """
    Extract specific dates from page text.
    Returns list of (date_sort, date_display, duration_days) tuples.
    """
    results = []
    current_year = date.today().year

    for m in WP_DATE_PATTERN.finditer(text):
        month1 = m.group(1)
        day1 = int(m.group(2))
        month2 = m.group(3)  # may be None (same-month range or single date)
        day2_str = m.group(4)  # may be None
        year_str = m.group(5)  # may be None

        year = int(year_str) if year_str else current_year

        # Parse start date
        try:
            start = datetime.strptime(f"{month1} {day1} {year}", "%B %d %Y")
        except ValueError:
            try:
                start = datetime.strptime(f"{month1} {day1} {year}", "%b %d %Y")
            except ValueError:
                continue

        # If month already passed and no year given, assume next year
        if not year_str and start.date() < date.today():
            year += 1
            try:
                start = start.replace(year=year)
            except ValueError:
                continue

        date_sort = start.strftime("%Y-%m-%d")

        # Parse end date for duration
        duration_days = 1
        if day2_str:
            day2 = int(day2_str)
            end_month = month2 or month1
            try:
                end = datetime.strptime(f"{end_month} {day2} {year}", "%B %d %Y")
            except ValueError:
                try:
                    end = datetime.strptime(f"{end_month} {day2} {year}", "%b %d %Y")
                except ValueError:
                    end = start
            duration_days = max((end - start).days + 1, 1)

        # Build display string
        if day2_str and month2:
            date_display = f"{start.strftime('%b')} {day1} – {month2[:3]} {day2_str}"
        elif day2_str:
            date_display = f"{start.strftime('%b')} {day1}–{day2_str}"
        else:
            date_display = start.strftime("%b %-d, %Y")

        results.append((date_sort, date_display, duration_days))

    return results


def scrape_website_course(url: str) -> list:
    """Scrape a single Altus course/trip detail page. Returns list of course row dicts."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"  Failed to fetch {url}: {e}")
        return []

    # Title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1].replace("-", " ").title()

    # Price — look for $NNN pattern in page text
    price = None
    price_match = re.search(r"\$\s?([\d,]+)", soup.get_text())
    if price_match:
        try:
            val = int(price_match.group(1).replace(",", ""))
            if val >= 50:
                price = val
        except ValueError:
            pass

    # Duration — look for "N Days" or "N Day" pattern
    duration_days = None
    dur_match = re.search(r"(\d+)\s*day", soup.get_text(), re.IGNORECASE)
    if dur_match:
        duration_days = int(dur_match.group(1))

    # Image — og:image
    image_url = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = og["content"]

    # Description — first substantial paragraph
    desc_parts = []
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 80 and len(desc_parts) < 3:
            desc_parts.append(text)
    description = " ".join(desc_parts)[:800]

    # Booking URL — Rezdy link from "Book Now" button
    booking_url = None
    rezdy_link = soup.find("a", href=re.compile(r"rezdy\.com"))
    if rezdy_link:
        href = rezdy_link["href"].split("?")[0]
        booking_url = f"{href}?{PROVIDER['utm']}"
    if not booking_url:
        booking_url = f"{url}?{PROVIDER['utm']}"

    # Location — extract from text or default
    location_raw = "Squamish, BC"  # most Altus courses are in Squamish
    loc_text = soup.get_text().lower()
    if "canmore" in loc_text or "rockies" in loc_text or "canadian rockies" in loc_text:
        location_raw = "Canmore, AB"
    elif "rogers pass" in loc_text:
        location_raw = "Rogers Pass, BC"
    elif "bugaboo" in loc_text:
        location_raw = "Bugaboos, BC"

    # Dates — check for specific dates vs seasonal
    page_text = soup.get_text()
    specific_dates = parse_wp_dates(page_text)
    is_seasonal = bool(SEASONAL_PATTERN.search(page_text))

    scraped_at = datetime.utcnow().isoformat()
    rows = []

    if specific_dates:
        for date_sort, date_display, dur in specific_dates:
            if not is_future(date_sort):
                continue
            rows.append({
                "title":         title,
                "provider_id":   PROVIDER["id"],
                "activity":      "guided",  # will be resolved later
                "activity_raw":  "guided",
                "location_raw":  location_raw,
                "price":         price,
                "date_display":  date_display,
                "date_sort":     date_sort,
                "duration_days": dur or duration_days,
                "image_url":     image_url,
                "booking_url":   booking_url,
                "description":   description,
                "summary":       "",
                "avail":         "open",
                "custom_dates":  False,
                "scraped_at":    scraped_at,
            })
    else:
        # Seasonal or no dates — single flexible-dates row
        rows.append({
            "title":         title,
            "provider_id":   PROVIDER["id"],
            "activity":      "guided",
            "activity_raw":  "guided",
            "location_raw":  location_raw,
            "price":         price,
            "date_display":  "Flexible dates",
            "date_sort":     None,
            "duration_days": duration_days,
            "image_url":     image_url,
            "booking_url":   booking_url,
            "description":   description,
            "summary":       "",
            "avail":         "open",
            "custom_dates":  True,
            "scraped_at":    scraped_at,
        })

    log.info(f"  {url} → {len(rows)} row(s) | ${price} | {len(specific_dates)} dates")
    return rows


# ── MAIN ──

def main():
    provider = PROVIDER
    log.info(f"=== {provider['name']} scraper starting ===")

    # Update provider ratings from Google Places
    update_provider_ratings(provider["id"])

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    # Load activity mappings and labels from Supabase
    activity_maps = load_activity_mappings_table()
    log.info(f"Loaded {len(activity_maps)} activity mappings")
    global ACTIVITY_LABELS
    ACTIVITY_LABELS = load_activity_labels()
    log.info(f"Loaded {len(ACTIVITY_LABELS)} activity labels")

    location_flags = []

    # Scrape Rezdy
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

    # ── Pass 2: Scrape website course/trip pages ──
    log.info(f"\n=== Pass 2: Scraping {provider['name']} website ===")
    wp_urls = collect_website_urls()

    for wp_url in wp_urls:
        wp_rows = scrape_website_course(wp_url)
        for c in wp_rows:
            # Resolve activity
            activity_canonical, act_is_new, act_add_mapping = resolve_activity(
                c["title"], c.get("description", ""), activity_maps, provider["name"]
            )
            if act_add_mapping:
                sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
                activity_maps.append((c["title"].lower()[:100], activity_canonical))

            # Resolve location
            loc_raw = c.get("location_raw") or ""
            loc_canonical = None
            if loc_raw:
                loc_canonical, loc_is_new, loc_add_mapping = normalise_location(loc_raw, mappings)
                if loc_add_mapping:
                    sb_insert("location_mappings", {"location_raw": loc_raw, "location_canonical": loc_canonical})
                    mappings[loc_raw.lower().strip()] = loc_canonical

            badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
            course_id = stable_id(provider["id"], activity_canonical, c.get("date_sort"), c["title"])

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
                "spots_remaining":    None,
                "avail":              c.get("avail", "open"),
                "image_url":          c.get("image_url"),
                "booking_url":        c.get("booking_url"),
                "active":             True,
                "custom_dates":       c.get("custom_dates", False),
                "summary":            "",
                "description":        c.get("description", ""),
                "scraped_at":         c["scraped_at"],
            })
        time.sleep(0.5)

    log.info(f"Total processed after both passes: {len(processed)}")

    # Batch generate summaries for all courses (both passes)
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

    # Deduplicate by title + date_sort — keep first occurrence (Pass 1 / Rezdy)
    # since it has better availability data than the website scrape
    if processed:
        seen_td = {}
        deduped = []
        for c in processed:
            key = (c["title"], c.get("date_sort"))
            if key not in seen_td:
                seen_td[key] = True
                deduped.append(c)
        if len(deduped) < len(processed):
            log.info(f"Deduplicated {len(processed) - len(deduped)} duplicate title+date courses (Pass 1 kept over Pass 2)")
        # Also deduplicate by stable ID as a safety net
        seen_id = {}
        final = []
        for c in deduped:
            if c["id"] not in seen_id:
                seen_id[c["id"]] = True
                final.append(c)
        if len(final) < len(deduped):
            log.warning(f"Deduplicated {len(deduped) - len(final)} duplicate stable IDs")
        deduped = final

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
    # send_scrape_summary(len(processed), provider["name"], len(location_flags))

    log.info(f"=== {provider['name']} scraper complete ===")


if __name__ == "__main__":
    main()
