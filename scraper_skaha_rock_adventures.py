#!/usr/bin/env python3
"""
Scraper: Skaha Rock Adventures (skaha-rock-adventures)
Site:    https://www.skaharockclimbing.com
Platform: Static HTML — each course page lists dates with Book Now or Sold Out buttons
Approach: BeautifulSoup — per-course-page date extraction from booking links
"""

import os
import re
import time
import random
import hashlib
import logging
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_get, sb_upsert, sb_insert,
    stable_id_v2, is_future,
    generate_summaries_batch,
    update_provider_ratings,
    send_scraper_summary,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY,
    ANTHROPIC_API_KEY, UTM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

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


# ── Skaha helpers ─────────────────────────────────────────────────────────────

def _skaha_fetch(url: str) -> Optional[BeautifulSoup]:
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


# ── Scraper ───────────────────────────────────────────────────────────────────

def scrape_skaha() -> list:
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
                "location_raw": "Penticton, BC", "date_display": "Flexible dates",
                "date_sort": None, "duration_days": None, "price": price,
                "spots_remaining": None, "avail": "open", "image_url": None,
                "booking_url": f"{base_url}{course_meta['path']}?{utm}",
                "description": description, "summary": "", "search_document": "", "custom_dates": True, "scraped_at": scraped_at,
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
                    "location_raw": "Penticton, BC", "date_display": date_display,
                    "date_sort": entry["date_iso"], "duration_days": None, "price": price,
                    "spots_remaining": None, "avail": entry["avail"], "image_url": None,
                    "booking_url": entry["booking_url"], "description": description,
                    "summary": "", "search_document": "", "custom_dates": False, "scraped_at": scraped_at,
                })
        if i < len(SKAHA_COURSE_PAGES) - 1:
            delay = random.uniform(2, 7)
            log.info(f"  Waiting {delay:.1f}s...")
            time.sleep(delay)
    log.info(f"Skaha total raw courses: {len(all_courses)}")
    return all_courses


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"=== BackcountryFinder scraper starting (provider=skaha-rock-adventures) ===")

    # Update provider ratings from Google Places
    update_provider_ratings(SKAHA_PROVIDER["id"])

    raw_courses = scrape_skaha()
    processed = []
    for c in raw_courses:
        loc_canonical = "Penticton / Skaha Bluffs"
        course_id = stable_id_v2(SKAHA_PROVIDER["id"], c.get("date_sort"), c["title"])
        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        SKAHA_PROVIDER["id"],
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
            "search_document":    "",
            "description":        c.get("description", ""),
            "scraped_at":         c["scraped_at"],
        })

    if processed:
        seen_titles = {}
        unique_inputs = []
        for c in processed:
            if c.get("description") and c["title"] not in seen_titles:
                seen_titles[c["title"]] = c["id"]
                unique_inputs.append({"id": c["id"], "title": c["title"], "description": c.get("description", ""), "provider": SKAHA_PROVIDER["name"]})
        if unique_inputs:
            summaries = generate_summaries_batch(unique_inputs)
            title_to_summary = {}
            for c in unique_inputs:
                result = summaries.get(c["id"], {})
                title_to_summary[c["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
            for c in processed:
                result = title_to_summary.get(c["title"], {})
                c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            log.info(f"Summaries generated: {len(summaries)}")

    for c in processed:
        c.pop("description", None)

    # Deduplicate by ID
    if processed:
        seen = {}
        for c in processed:
            seen[c["id"]] = c
        deduped = list(seen.values())
        if len(deduped) < len(processed):
            log.warning(f"Deduplicated {len(processed) - len(deduped)} duplicate course IDs before upsert")
        sb_upsert("courses", deduped)
        # Log intelligence (V2 — append-only, change-detected)
        for c in deduped:
            log_availability_change(c)
            log_price_change(c)
        log.info(f"Total courses upserted: {len(deduped)}")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    send_scraper_summary(SKAHA_PROVIDER["name"], len(processed), ok=len(processed) > 0)
    log.info("=== Scraper complete ===")


if __name__ == "__main__":
    main()
