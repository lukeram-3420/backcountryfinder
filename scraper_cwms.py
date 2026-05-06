#!/usr/bin/env python3
"""
scraper_cwms.py — Canada West Mountain School (WooCommerce)
Site:    https://themountainschool.com
Platform: WooCommerce — listing page + individual course pages with date sessions
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
    title_hash, activity_key,
    upsert_activity_control, load_activity_controls,
)

_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="woocommerce",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":       "cwms",
    "name":     "Canada West Mountain School",
    "listing_url": "https://themountainschool.com/programs-and-courses/",
    "base_url": "https://themountainschool.com",
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
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
                if not _is_visible(provider["id"], title):
                    log.info(f"Skipping hidden title: {title}")
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
                    "search_document": "",
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    provider = PROVIDER
    log.info(f"=== CWMS scraper starting ===")

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    global _CONTROLS
    _CONTROLS = load_activity_controls(provider["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    location_flags = []

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
        course_id = stable_id_v2(provider["id"], c.get("date_sort"), c["title"])
        booking_url = c.get("booking_url")
        active = True
        custom_dates = True  # CWMS uses WooCommerce date picker
        date_display = "Flexible dates"
        date_sort = None
        row = {
            "id": course_id, "title": c["title"], "provider_id": provider["id"],
            "location_raw": loc_raw or None,
            "date_display": date_display,
            "date_sort": date_sort, "duration_days": c.get("duration_days"),
            "price": c.get("price"), "spots_remaining": None, "avail": "open",
            "image_url": c.get("image_url"), "booking_url": booking_url,
            "active": active, "custom_dates": custom_dates,
            "booking_mode": "request" if custom_dates else "instant",
            "summary": "", "search_document": "",
            "description": c.get("description", ""),
            "scraped_at": c["scraped_at"],
        }
        # Omit location_canonical when None so a failed Haiku call doesn't
        # null out a previously-resolved canonical on re-scrape.
        if loc_canonical is not None:
            row["location_canonical"] = loc_canonical
        processed.append(row)
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
            session_id = stable_id_v2(provider["id"], date_sort, course["title"])

            session_course = dict(course)
            session_course.update({
                "id":            session_id,
                "date_display":  session.get("date_display"),
                "date_sort":     date_sort,
                "spots_remaining": session.get("spots_remaining"),
                "avail":         session.get("avail", "open"),
                "booking_url":   session_url,
                "custom_dates":  False,
                "booking_mode":  "instant",
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
            }
            for c in dated_processed
        ]
        summaries = generate_summaries_batch(summary_inputs, provider_id=PROVIDER["id"])
        for c in dated_processed:
            if c["id"] in summaries:
                result = summaries.get(c["id"], {})
                c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
        log.info(f"Summaries generated: {len(summaries)}")

    # Deduplicate by ID — last one wins
    if dated_processed:
        seen = {}
        for c in dated_processed:
            seen[c["id"]] = c
        deduped = list(seen.values())
        if len(deduped) < len(dated_processed):
            log.warning(f"Deduplicated {len(dated_processed) - len(deduped)} duplicate course IDs before upsert")

        # Strip description — it's a scrape-time field, not stored in Supabase
        for c in deduped:
            c.pop("description", None)
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
    send_scraper_summary(provider["name"], len(dated_processed))

    log.info("=== CWMS scraper complete ===")


if __name__ == "__main__":
    main()
