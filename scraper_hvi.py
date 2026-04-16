#!/usr/bin/env python3
"""
scraper_hvi.py — Hike Vancouver Island scraper
Site:     https://www.hikevancouverisland.com
Platform: Custom Rails — list-upcoming with h4, p, dl/dt/dd structure
          (same platform as Island Alpine Guides)
"""

import re
import time
import hashlib
import logging
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_get, sb_upsert, sb_insert,
    load_location_mappings, normalise_location,
    load_activity_mappings, load_activity_labels, resolve_activity, build_badge,
    generate_summaries_batch,
    stable_id_v2, spots_to_avail,
    update_provider_ratings, send_scraper_summary,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":          "hvi",
    "name":        "Hike Vancouver Island",
    "listing_url": "https://www.hikevancouverisland.com/trips/upcoming",
    "base_url":    "https://www.hikevancouverisland.com",
    "utm":         "utm_source=backcountryfinder&utm_medium=referral",
    "location":    "Vancouver Island, BC",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


# ── IAG-style parsing functions ──────────────────────────────────────────────

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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    provider = PROVIDER
    log.info(f"=== {provider['name']} scraper starting ===")

    # Update provider ratings
    update_provider_ratings(provider["id"])

    # Load mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")
    activity_maps = load_activity_mappings()
    log.info(f"Loaded {len(activity_maps)} activity mappings")
    activity_labels = load_activity_labels()
    log.info(f"Loaded {len(activity_labels)} activity labels")

    # Scrape
    raw_courses = scrape_iag_style(provider)
    processed = []
    for c in raw_courses:
        loc_canonical = normalise_location(c.get("location_raw", ""), mappings)
        if not loc_canonical:
            loc_canonical = "Vancouver Island"
        course_id = stable_id_v2(provider["id"], c.get("date_sort"), c["title"])
        activity_canonical = resolve_activity(c["title"], "", activity_maps)
        badge_canonical = build_badge(activity_canonical, c.get("duration_days"), activity_labels)
        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        provider["id"],
            "badge":              badge_canonical,
            "activity":           activity_canonical,
            "activity_raw":       c.get("activity_raw", "guided"),
            "activity_canonical": None,  # V2: null hides from V1 frontend
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
            title_to_summary = {}
            for c in unique_inputs:
                result = summaries.get(c["id"], {})
                title_to_summary[c["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
            for c in processed:
                result = title_to_summary.get(c["title"], {})
                c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""

    # Strip description before upsert
    for c in processed:
        c.pop("description", None)

    # Deduplicate by ID
    if processed:
        seen = {}
        for c in processed:
            seen[c["id"]] = c
        deduped = list(seen.values())
        sb_upsert("courses", deduped)
        # Log intelligence (V2 — append-only, change-detected)
        for c in deduped:
            log_availability_change(c)
            log_price_change(c)
        log.info(f"Upserted {len(deduped)} courses for {provider['name']}")
    else:
        log.warning(f"No courses scraped for {provider['name']}")

    send_scraper_summary(provider["name"], len(processed))
    log.info(f"=== {provider['name']} scraper complete ===")


if __name__ == "__main__":
    main()
