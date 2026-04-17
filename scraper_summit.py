#!/usr/bin/env python3
"""
scraper_summit.py — Summit Mountain Guides (The Events Calendar)
Site:    https://summitmountainguides.com
Platform: WordPress with The Events Calendar plugin
"""

import os
import re
import json
import time
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import Optional

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_get, sb_upsert, sb_insert,
    normalise_location,
    send_email, send_scraper_summary,
    generate_summaries_batch,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, ANTHROPIC_API_KEY,
    GOOGLE_PLACES_API_KEY, UTM, CLAUDE_MODEL, NOTIFY_EMAIL, FROM_EMAIL,
    stable_id_v2,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":       "summit",
    "name":     "Summit Mountain Guides",
    "listing_url": "https://summitmountainguides.com/upcoming-trips-courses/",
    "base_url": "https://summitmountainguides.com",
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
    "months_ahead": 6,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ── Helper functions (replicated from scraper.py for standalone use) ──────────

def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def is_future(date_sort: Optional[str]) -> bool:
    if not date_sort:
        return True  # keep if we can't parse
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


# ── Scraping functions ────────────────────────────────────────────────────────

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
                    "search_document": "",
                    "description":   description,
                    "custom_dates":  False,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })

        except Exception as e:
            log.error(f"Failed to scrape Summit month {target.strftime('%B %Y')}: {e}")

        time.sleep(1)

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    provider = PROVIDER
    log.info(f"=== Summit scraper starting ===")

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    location_flags = []

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
        course_id = stable_id_v2(provider["id"], c.get("date_sort"), c["title"])
        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        provider["id"],
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
            "search_document":    "",
            "description":        c.get("description", ""),
            "scraped_at":         c["scraped_at"],
        })

    # Batch generate summaries
    if processed:
        summary_inputs = [{"id": c["id"], "title": c["title"], "description": c.get("description",""), "provider": provider["name"]} for c in processed if c.get("description")]
        if summary_inputs:
            summaries = generate_summaries_batch(summary_inputs)
            for c in processed:
                if c["id"] in summaries:
                    result = summaries.get(c["id"], {})
                    c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                    c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""

    # Strip description before upsert
    for c in processed:
        c.pop("description", None)

    # Deduplicate by ID — last one wins
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

    # Flag unmatched locations
    if location_flags:
        for flag in location_flags:
            sb_insert("location_flags", flag)

    # Send summary email
    send_scraper_summary(provider["name"], len(processed))

    log.info("=== Summit scraper complete ===")


if __name__ == "__main__":
    main()
