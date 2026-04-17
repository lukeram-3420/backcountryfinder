#!/usr/bin/env python3
"""
scraper_hangfire.py — Standalone Rezdy storefront scraper for Hangfire Training Ltd.

Same pattern as scraper_altus.py — Rezdy HTML scrape of explicit catalog URLs.
All Hangfire courses are avalanche_safety; location is inferred from the title.
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

from scraper_utils import normalise_location, log_availability_change, log_price_change, stable_id_v2, generate_summaries_batch

# ── CONFIG ──
SUPABASE_URL          = os.environ["SUPABASE_URL"]
SUPABASE_KEY          = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
PLACES_API_URL        = "https://maps.googleapis.com/maps/api/place"
CLAUDE_MODEL          = "claude-haiku-4-5-20251001"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PROVIDER = {
    "id":         "hangfire",
    "name":       "Hangfire Training Ltd",
    "storefront": "https://hangfiretraining.rezdy.com",
    "catalogs": [
        "catalog/159773/sled-ast-1-full-course",
        "catalog/160515/sled-ast-1-field-day",
        "catalog/160518/sled-ast-2",
        "catalog/160517/ski-ast-1-full-course",
    ],
    "utm": "utm_source=backcountryfinder&utm_medium=referral",
}

# Ordered — first match wins
LOCATION_KEYWORDS = [
    (("golden", "kicking horse"), "Golden, BC"),
    (("revelstoke", "revy"),      "Revelstoke, BC"),
    (("valemount",),              "Valemount, BC"),
    (("mcbride",),                "McBride, BC"),
    (("fernie",),                 "Fernie, BC"),
    (("radium",),                 "Radium Hot Springs, BC"),
    (("kimberley",),              "Kimberley, BC"),
    (("big white",),              "Kelowna, BC"),
    (("apex",),                   "Penticton, BC"),
]
DEFAULT_LOCATION_RAW = "Golden, BC"

NO_AVAILABILITY_SIGNALS = [
    "no availability", "please try again later", "no sessions available",
    "not available", "sold out", "no upcoming",
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
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
               "Content-Type": "application/json"}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def sb_upsert(table: str, data: list) -> None:
    if not data:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    r = requests.post(url, headers=headers, json=data)
    if not r.ok:
        log.error(f"Supabase upsert error {r.status_code}: {r.text}")
    else:
        log.info(f"Upserted {len(data)} rows to {table}")


def sb_insert(table: str, data: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
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
            params={"input": f"{provider_name} {clean_location} BC Canada", "inputtype": "textquery",
                    "fields": "place_id,name", "key": GOOGLE_PLACES_API_KEY},
            timeout=10,
        )
        candidates = r.json().get("candidates", [])
        if candidates:
            return candidates[0]["place_id"]
    except Exception as e:
        log.warning(f"Place ID lookup failed for {provider_name}: {e}")
    return None


def get_place_details(place_id):
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return {}
    try:
        r = requests.get(
            f"{PLACES_API_URL}/details/json",
            params={"place_id": place_id, "fields": "rating,user_ratings_total",
                    "key": GOOGLE_PLACES_API_KEY},
            timeout=10,
        )
        result = r.json().get("result", {})
        return {"rating": result.get("rating"), "review_count": result.get("user_ratings_total")}
    except Exception as e:
        log.warning(f"Place details fetch failed for {place_id}: {e}")
    return {}


def update_provider_ratings(provider_id):
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key — skipping ratings update")
        return
    log.info("Updating provider ratings from Google Places...")
    providers = sb_get("providers", {"select": "id,name,location,google_place_id", "id": f"eq.{provider_id}"})
    for p in providers:
        pid = p.get("google_place_id")
        if not pid:
            pid = find_place_id(p["name"], p.get("location", ""))
            if pid:
                sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid}])
            time.sleep(0.5)
        if not pid:
            log.warning(f"No Place ID found for {p['name']} — skipping")
            continue
        details = get_place_details(pid)
        if details.get("rating"):
            sb_upsert("providers", [{
                "id": p["id"], "name": p["name"], "google_place_id": pid,
                "rating": details["rating"], "review_count": details.get("review_count"),
            }])
            log.info(f"{p['name']}: ★ {details['rating']} ({details.get('review_count', 0)} reviews)")
        time.sleep(0.5)


def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


# ── LOCATION FROM TITLE KEYWORDS ──

def location_raw_from_title(title: str) -> str:
    t = (title or "").lower()
    for keywords, raw in LOCATION_KEYWORDS:
        if any(kw in t for kw in keywords):
            return raw
    return DEFAULT_LOCATION_RAW


# ── CLAUDE ──

def claude_classify(prompt: str, max_tokens: int = 256):
    if not ANTHROPIC_API_KEY:
        return {}
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        text = r.json()["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return {}


# ── DATE / ID HELPERS ──

def parse_date_sort(date_str: str) -> Optional[str]:
    if not date_str:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str[:10]
    months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
              "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
    m = re.search(r"(\w+)\s+(\d+).*?(\d{4})", date_str, re.IGNORECASE)
    if m:
        ms = m.group(1).lower()[:3]
        if ms in months:
            return f"{m.group(3)}-{months[ms]}-{m.group(2).zfill(2)}"
    return None


def is_future(date_sort: Optional[str]) -> bool:
    if not date_sort:
        return True
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    title_hash = hashlib.md5(title.encode()).hexdigest()[:6]
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}-{title_hash}"
    return f"{provider_id}-{activity}-{title_hash}"


# ── REZDY SCRAPE ──

def scrape_rezdy_page(provider: dict, url: str) -> list:
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
                title_el = item.select_one("h2 a")
                title = title_el.get_text(strip=True) if title_el else None
                if not title:
                    continue

                booking_url = None
                href = title_el.get("href", "") if title_el else ""
                if href.startswith("http"):
                    booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"
                elif href.startswith("/"):
                    booking_url = f"{provider['storefront']}{href}?{provider['utm']}"
                elif href:
                    booking_url = f"{provider['storefront']}/{href}?{provider['utm']}"

                # Price
                price = None
                price_el = item.select_one("span.price")
                if price_el:
                    raw = price_el.get("data-original-amount", "") or price_el.get_text(strip=True)
                    pm = re.search(r"[\d,]+\.?\d*", raw.replace(",", ""))
                    if pm:
                        try:
                            price = int(float(pm.group().replace(",", "")))
                        except ValueError:
                            pass

                # Duration
                duration_days = None
                for li in item.select("ul.unstyled li"):
                    text = li.get_text(strip=True)
                    if "duration" in text.lower():
                        dm = re.search(r"(\d+(?:\.\d+)?)\s*day", text, re.I)
                        if dm:
                            duration_days = float(dm.group(1))
                        break
                if not duration_days:
                    dm = re.search(r"(\d+(?:\.\d+)?)\s*day", title, re.I)
                    if dm:
                        duration_days = float(dm.group(1))

                # Image
                image_url = None
                img_el = item.select_one("div.products-list-image img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")
                    if image_url and image_url.startswith("//"):
                        image_url = "https:" + image_url

                # Short description from the listing card (extended via check_course_page later)
                desc_text = ""
                desc_el = item.select_one("div.products-list-item-overview p")
                if desc_el:
                    desc_text = desc_el.get_text(strip=True)

                courses.append({
                    "title":            title,
                    "price":            price,
                    "duration_days":    duration_days,
                    "image_url":        image_url,
                    "booking_url":      booking_url,
                    "description":      desc_text,
                    "scraped_at":       datetime.utcnow().isoformat(),
                })
            except Exception as e:
                log.warning(f"Error parsing item at {url}: {e}")
                continue
    except Exception as e:
        log.error(f"Failed to scrape {url}: {e}")
    return courses


def scrape_rezdy(provider: dict) -> list:
    all_courses = []
    seen_titles = set()
    for catalog in provider["catalogs"]:
        url = f"{provider['storefront']}/{catalog}"
        log.info(f"Scraping catalog: {url}")
        for c in scrape_rezdy_page(provider, url):
            if c["title"] not in seen_titles:
                seen_titles.add(c["title"])
                all_courses.append(c)
        time.sleep(1)
    log.info(f"Total unique courses from {provider['name']}: {len(all_courses)}")
    return all_courses


def check_course_page(booking_url: str) -> dict:
    """Visit a course page — returns {available, custom_dates, dates, description}."""
    result = {"available": True, "custom_dates": False, "dates": [], "description": ""}
    try:
        clean_url = booking_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        text = r.text.lower()
        soup = BeautifulSoup(r.text, "html.parser")
        for signal in NO_AVAILABILITY_SIGNALS:
            if signal in text:
                result["available"] = False
                return result
        page_text = soup.get_text()
        found = []
        for pattern in STATIC_DATE_PATTERNS:
            found.extend(re.findall(pattern, page_text))
        if found:
            result["dates"] = list(set(found))
        else:
            result["custom_dates"] = True
        desc_el = soup.find("div", class_=lambda c: c and any(x in c for x in
                  ["product-description", "description", "course-description", "entry-content"]))
        if not desc_el:
            desc_el = soup.find("div", {"itemprop": "description"})
        if desc_el:
            result["description"] = desc_el.get_text(separator=" ", strip=True)[:800]
    except Exception as e:
        log.warning(f"Could not check course page {booking_url}: {e}")
        result["custom_dates"] = True
    return result


# ── MAIN ──

def main():
    provider = PROVIDER
    log.info(f"=== {provider['name']} scraper starting ===")

    update_provider_ratings(provider["id"])

    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    raw_courses = scrape_rezdy(provider)

    processed = []
    for c in raw_courses:
        title = c["title"]

        # Location: title keywords → raw → canonical via scraper_utils
        loc_raw = location_raw_from_title(title)
        loc_canonical = normalise_location(loc_raw, mappings)

        booking_url = c.get("booking_url")
        date_display = None
        date_sort = None
        custom_dates = False
        active = True
        page_description = c.get("description", "")

        if booking_url:
            pc = check_course_page(booking_url)
            if pc.get("description"):
                page_description = pc["description"]
            if not pc["available"]:
                custom_dates = True
                date_display = "Flexible dates"
            elif pc["custom_dates"]:
                custom_dates = True
                date_display = "Flexible dates"
            elif pc["dates"]:
                date_display = pc["dates"][0]
                date_sort = parse_date_sort(date_display)
            time.sleep(0.5)

        if date_sort and not is_future(date_sort):
            log.info(f"Skipping past course: {title} ({date_sort})")
            continue

        course_id = stable_id_v2(provider["id"], date_sort, title)
        duration_days = c.get("duration_days")

        row = {
            "id":                 course_id,
            "title":              title,
            "provider_id":        provider["id"],
            "location_raw":       loc_raw,
            "date_display":       date_display,
            "date_sort":          date_sort,
            "duration_days":      duration_days,
            "price":              c.get("price"),
            "spots_remaining":    None,
            "avail":              "open",
            "image_url":          c.get("image_url"),
            "booking_url":        booking_url,
            "active":             active,
            "custom_dates":       custom_dates,
            "summary":            "",
            "search_document":    "",
            "description":        page_description,
            "scraped_at":         c["scraped_at"],
        }
        # Omit location_canonical when None so a failed Haiku call doesn't
        # null out a previously-resolved canonical on re-scrape.
        if loc_canonical is not None:
            row["location_canonical"] = loc_canonical
        processed.append(row)

    log.info(f"Total processed: {len(processed)}")

    # Summaries
    if processed:
        summary_inputs = [
            {"id": c["id"], "title": c["title"], "description": c.get("description", ""),
             "provider": provider["name"]}
            for c in processed if c.get("description")
        ]
        if summary_inputs:
            summaries = generate_summaries_batch(summary_inputs)
            for c in processed:
                if c["id"] in summaries:
                    result = summaries.get(c["id"], {})
                    c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                    c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            log.info(f"Summaries generated: {len(summaries)}")

    # Dedup by stable ID
    seen_id = set()
    deduped = []
    for c in processed:
        if c["id"] in seen_id:
            continue
        seen_id.add(c["id"])
        deduped.append(c)

    # description is scrape-time only, not a courses column
    for c in deduped:
        c.pop("description", None)

    if deduped:
        sb_upsert("courses", deduped)
        # Log intelligence (V2 — append-only, change-detected)
        for c in deduped:
            log_availability_change(c)
            log_price_change(c)
        log.info(f"Total courses upserted: {len(deduped)}")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    log.info(f"=== {provider['name']} scraper complete ===")


if __name__ == "__main__":
    main()
