#!/usr/bin/env python3
"""
Scraper: Squamish Rock Guides (srg)
Site:    https://squamishrockguides.com
Platform: Custom WordPress booking system
Approach: BeautifulSoup — sidebar program links → program pages → booking page date dropdowns
"""

import os
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
    stable_id_v2, is_future,
    generate_summaries_batch,
    update_provider_ratings, update_provider_shared_utils,
    send_scraper_summary,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY,
    ANTHROPIC_API_KEY, UTM,
    title_hash, activity_key,
    upsert_activity_control, load_activity_controls,
)

_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="wordpress",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":        "srg",
    "name":      "Squamish Rock Guides",
    "programs_url": "https://squamishrockguides.com/booking/booking-information/",
    "booking_base": "https://squamishrockguides.com/booking/",
    "base_url":  "https://squamishrockguides.com",
    "utm":       "utm_source=backcountryfinder&utm_medium=referral",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


# ── Scraper ───────────────────────────────────────────────────────────────────

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
                if not _is_visible(PROVIDER["id"], title):
                    log.info(f"Skipping hidden title: {title}")
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
                        "price":        price,
                        "date_display": "Flexible dates",
                        "date_sort":    None,
                        "custom_dates": True,
                        "image_url":    image_url,
                        "booking_url":  booking_url,
                        "description":  description,
                        "summary":      "",
                        "search_document": "",
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
                        "price":        price,
                        "date_display": date_display,
                        "date_sort":    date_str,
                        "custom_dates": False,
                        "image_url":    image_url,
                        "booking_url":  booking_url,
                        "description":  description,
                        "summary":      "",
                        "search_document": "",
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"=== BackcountryFinder scraper starting (provider=srg) ===")

    # Update provider ratings from Google Places
    update_provider_ratings(PROVIDER["id"])

    update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))

    global _CONTROLS
    _CONTROLS = load_activity_controls(PROVIDER["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    raw_courses = scrape_srg(PROVIDER)
    processed = []
    for c in raw_courses:
        loc_canonical = "Squamish"  # always Squamish
        course_id = stable_id_v2(PROVIDER["id"], c.get("date_sort"), c["title"])
        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        PROVIDER["id"],
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
            "booking_mode":       "request" if c.get("custom_dates") else "instant",
            "summary":            "",
            "search_document":    "",
            "description":        c.get("description", ""),
            "scraped_at":         c["scraped_at"],
        })

    # Batch summaries
    if processed:
        summary_inputs = [{"id": c["id"], "title": c["title"], "description": c.get("description",""), "provider": PROVIDER["name"]} for c in processed if c.get("description")]
        if summary_inputs:
            # Deduplicate by title — same summary for all dates of same course
            seen_titles = {}
            unique_inputs = []
            for s in summary_inputs:
                if s["title"] not in seen_titles:
                    seen_titles[s["title"]] = s["id"]
                    unique_inputs.append(s)
            summaries = generate_summaries_batch(unique_inputs, provider_id=PROVIDER["id"])
            # Apply summary to all cards with same title
            title_to_summary = {}
            for s in unique_inputs:
                result = summaries.get(s["id"], {})
                title_to_summary[s["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
            for c in processed:
                result = title_to_summary.get(c["title"], {})
                c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""

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

    send_scraper_summary(PROVIDER["name"], len(processed), ok=len(processed) > 0)
    log.info("=== Scraper complete ===")


if __name__ == "__main__":
    main()
