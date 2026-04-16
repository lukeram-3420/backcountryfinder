#!/usr/bin/env python3
"""
BackcountryFinder — Yamnuska Mountain Adventures scraper
Standalone scraper, runs independently of scraper.py.

Platform: Custom WordPress + forms.yamnuska.com booking system.
Dates, prices and location GUIDs are embedded in a tripDates iframe src URL.
The iframe itself contains the date radio buttons with data-spaces availability.
"""

import os
import re
import json
import time
import random
import logging
import hashlib
from datetime import datetime, date
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_get, sb_upsert, sb_insert,
    load_location_mappings, normalise_location,
    load_activity_mappings, load_activity_labels, resolve_activity, build_badge,
    claude_classify, generate_summaries_batch,
    parse_date_sort, is_future, stable_id_v2,
    update_provider_ratings, send_email,
    SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY, GOOGLE_PLACES_API_KEY,
    RESEND_API_KEY, UTM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── PROVIDER CONFIG ──
PROVIDER = {
    "id":       "yamnuska",
    "name":     "Yamnuska Mountain Adventures",
    "base_url": "https://yamnuska.com",
    "utm":      UTM,
    "location": "Canmore, AB",
    "courses": [
        # ── Avalanche ──
        "https://yamnuska.com/avalanche-courses/ast-1/",
        "https://yamnuska.com/avalanche-courses/avalanche-skills-training-1-for-ice-climbers/",
        "https://yamnuska.com/avalanche-courses/ast-1-refresher/",
        "https://yamnuska.com/avalanche-courses/ast-2/",
        "https://yamnuska.com/avalanche-courses/ast2-rogerspass/",
        "https://yamnuska.com/avalanche-courses/purcell-mountain-lodge-ast-2/",
        "https://yamnuska.com/avalanche-courses/ast-2-battle-abbey-lodge/",
        # ── Mountaineering — Beginner ──
        "https://yamnuska.com/mountaineering/beginner-programs/mountain-scrambling/",
        "https://yamnuska.com/mountaineering/beginner-programs/mountain-climbing-instruction/",
        "https://yamnuska.com/mountaineering/beginner-programs/intro-mountaineering-course-canadian-rockies/",
        "https://yamnuska.com/mountaineering/beginner-programs/womens-intro-to-mountaineering/",
        "https://yamnuska.com/mountaineering/beginner-programs/youth-mountaineering-course/",
        "https://yamnuska.com/mountaineering/crevasse-rescue/",
        "https://yamnuska.com/navigation-1-back-to-basics-with-map-compass/",
        "https://yamnuska.com/navigation-2-digital-trip-planning-electronic-navigation/",
        "https://yamnuska.com/mountaineering/beginner-programs/wapta-icefields/",
        # ── Mountaineering — Intermediate/Advanced ──
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/yoho-climbing/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/intro-alpine-rock-bugaboos/",
        "https://yamnuska.com/ice-climbing/intermediate-and-advanced-programs/intro-to-alpine-rock-fairy-meadows/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/alpine-iceclimbing/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/castle-mountain-alpine-rock-climbing-adventure/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/moraine-lake-alpine-classics/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/rogers-pass-alpinist-camp/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/bugaboos-climbing/",
        "https://yamnuska.com/mountaineering/intermediate-advanced-programs/tonquin-valley-alpine-climbing/",
        # ── 11,000ers ──
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mount-temple-11000/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mt-woolley-diadem/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mount-hector/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mount-athabasca/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mt-joffre-expedition/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mount-victoria/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/mt-edith-cavell-climb/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/climb-mount-assiniboine/",
        "https://yamnuska.com/mountaineering/canadian-rockies-11000ers1/climb-mount-robson/",
        # ── Fast and Light ──
        "https://yamnuska.com/mountaineering/fast-light-series/mt-lady-macdonald-se-ridge/",
        "https://yamnuska.com/mountaineering/fast-light-series/achilles-spire/",
        "https://yamnuska.com/mountaineering/fast-light-series/castle-mountain/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-victoria-south-east-ridge/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-aberdeen/",
        "https://yamnuska.com/mountaineering/fast-light-series/mount-andromeda/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-stanley/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-louis-kain-route/",
        "https://yamnuska.com/mountaineering/fast-light-series/climb-mt-fay/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-sir-donald-northwest-ridge-new-2014/",
        "https://yamnuska.com/mountaineering/fast-light-series/mt-temple-east-ridge/",
        # ── Rock ──
        "https://yamnuska.com/rock-climbing/discover-rock-climbing/",
        "https://yamnuska.com/rock-climbing/beginner-rock-climbing-course/",
        "https://yamnuska.com/rock-climbing/instruction-outdoors/",
        "https://yamnuska.com/rock-climbing/multi-pitch-climbing-course/",
        "https://yamnuska.com/rock-climbing/trad-lessons/",
        "https://yamnuska.com/rock-climbing/lead-climbing-essentials-from-sport-to-trad/",
        "https://yamnuska.com/rock-climbing/rock-climbing-level-5-rock-rescue/",
        "https://yamnuska.com/rock-climbing/multi-pitch-rock-climbing-days/",
        "https://yamnuska.com/rock-climbing/guide-service/",
        "https://yamnuska.com/rock-climbing/signature-series-rock-leader-with-sean-isaac/",
        "https://yamnuska.com/rock-climbing/el-potrero-chico-rock-climbing/",
        # ── Ski ──
        "https://yamnuska.com/ski-mountaineering/backcountry-skiing/",
        "https://yamnuska.com/ski-mountaineering/instructional-backcountry-skiing-boarding/womens-intro-to-backcountry-skiing-splitboarding/",
        "https://yamnuska.com/ski-mountaineering/2-day-backcountry-freerider-camp/",
        "https://yamnuska.com/ski-mountaineering/canadian-rockies-steep-deep-couloir-camp/",
        "https://yamnuska.com/ski-mountaineering/rogerspass-mountain-guide/",
        # ── Alpine School ──
        "https://yamnuska.com/mountain-semesters/alpine-school/",
    ],
}

# iframe src param key → canonical location raw string
IFRAME_LOCATION_MAP = {
    "canmore": "Canmore, AB",
    "calgary": "Calgary, AB",
    "rogers":  "Rogers Pass, BC",
    "bugaboo": "Bugaboos, BC",
    "purcell": "Purcell Mountains, BC",
    "battle":  "Battle Abbey, BC",
    "banff":   "Banff, AB",
    "golden":  "Golden, BC",
    "yoho":    "Yoho, BC",
    "jasper":  "Jasper, AB",
    "tonquin": "Jasper, AB",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ── REQUESTS SESSION (for iframe fetches only) ──

def make_session() -> requests.Session:
    """Lightweight session for fetching tripDates.php iframe URLs — plain HTML, no JS needed."""
    session = requests.Session()
    session.headers.update({
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Referer":         "https://yamnuska.com/",
    })
    return session


# ── PLAYWRIGHT: extract iframe src from JS-rendered main page ──

def get_iframe_src_playwright(browser, course_url: str) -> tuple:
    """
    Use a headless browser to load the course page and extract:
      - page title
      - description text
      - OG image URL
      - tripDates iframe src URL

    Returns (title, description, image_url, iframe_src)
    iframe_src is None if no tripDates iframe found.
    """
    title = course_url.rstrip("/").split("/")[-1].replace("-", " ").title()
    description = ""
    image_url   = None
    iframe_src  = None
    page_price  = None

    try:
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
        page.goto(course_url, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector("iframe[data-for='tripDates']", timeout=10000)
            iframe_el  = page.query_selector("iframe[data-for='tripDates']")
            iframe_src = iframe_el.get_attribute("src") if iframe_el else None
            if iframe_src and iframe_src.startswith("//"):
                iframe_src = "https:" + iframe_src
        except PlaywrightTimeout:
            pass

        html  = page.content()
        soup  = BeautifulSoup(html, "html.parser")

        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        content = soup.find("div", class_=re.compile(r"entry-content|page-content|course-content"))
        if content:
            paras = []
            for p in content.find_all("p"):
                text = p.get_text(" ", strip=True)
                if len(text) > 60:
                    paras.append(text)
                if len(paras) >= 2:
                    break
            description = " ".join(paras)

        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content")

        # Try to extract price from page HTML (fallback for when iframe has no price param)
        price_match = re.search(r"\$\s?([\d,]+)", soup.get_text())
        if price_match:
            try:
                val = int(price_match.group(1).replace(",", ""))
                if val >= 50:  # ignore tiny numbers
                    page_price = val
            except ValueError:
                pass

        page.close()

    except Exception as e:
        log.error(f"  Playwright error on {course_url}: {e}")
        try:
            page.close()
        except Exception:
            pass

    return title, description, image_url, iframe_src, page_price


# ── SCRAPER ──

def scrape_course_page(session: requests.Session, browser, course_url: str, utm: str) -> list:
    """
    Scrape one Yamnuska course page using a hybrid approach:
      1. Playwright loads the main page (JS-rendered) → extracts iframe src
      2. requests fetches the iframe URL (plain HTML) → parses dates + availability
    """
    results = []
    try:
        title, description, image_url, iframe_src, page_price = get_iframe_src_playwright(browser, course_url)

        if not iframe_src:
            log.info(f"  No tripDates iframe — flexible dates card")
            return [{
                "title":           title,
                "location_raw":    PROVIDER["location"],
                "price":           page_price,
                "date_display":    "Flexible dates",
                "date_sort":       None,
                "spots_remaining": None,
                "avail":           "open",
                "booking_url":     f"{course_url}?{utm}",
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    True,
            }]

        parsed = urlparse(iframe_src)
        params = parse_qs(parsed.query)

        location_key = None
        for key in IFRAME_LOCATION_MAP:
            val = params.get(key, [""])[0]
            if val and len(val) > 10:
                location_key = key
                break

        if not location_key:
            log.info(f"  No location GUID in iframe src — flexible dates card")
            log.info(f"  iframe params: { {k: v for k, v in params.items()} }")
            return [{
                "title":           title,
                "location_raw":    PROVIDER["location"],
                "price":           page_price,
                "date_display":    "Flexible dates",
                "date_sort":       None,
                "spots_remaining": None,
                "avail":           "open",
                "booking_url":     f"{course_url}?{utm}",
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    True,
            }]

        location_raw = IFRAME_LOCATION_MAP[location_key]
        log.info(f"  Location: {location_key} → {location_raw}")

        # ── Price extraction: 4-level fallback chain ──
        price      = None
        price_src  = "null — no source found"

        # Level 1: iframe URL params (e.g. priceCanmore=598)
        price_key = f"price{location_key.title()}"
        raw_price = params.get(price_key, [""])[0]
        if not raw_price:
            raw_price = params.get("priceCanmore", [""])[0]
        if not raw_price:
            for k, v in params.items():
                if k.lower().startswith("price") and v and v[0]:
                    raw_price = v[0]
                    price_key = k
                    break
        if raw_price:
            try:
                val = int(float(raw_price))
                if val >= 10:  # ignore placeholder prices (e.g. priceCanmore=1)
                    price = val
                    price_src = f"URL param ({price_key})"
            except (ValueError, TypeError):
                pass

        # Fetch iframe HTML (needed for dates AND level-2 price fallback)
        time.sleep(random.uniform(0.5, 1.0))
        iframe_resp = session.get(iframe_src, timeout=20)
        iframe_resp.raise_for_status()
        iframe_soup = BeautifulSoup(iframe_resp.text, "html.parser")

        # Level 2: iframe HTML body (price embedded in rendered iframe content)
        if price is None:
            iframe_text = iframe_soup.get_text()
            pm = re.search(r"\$\s?([\d,]+)", iframe_text)
            if pm:
                try:
                    val = int(pm.group(1).replace(",", ""))
                    if val >= 50:
                        price = val
                        price_src = "iframe HTML"
                except ValueError:
                    pass

        # Level 3: page HTML (price from Playwright-rendered main page)
        if price is None and page_price is not None:
            price = page_price
            price_src = "page HTML"

        log.info(f"  Price: ${price} ({price_src})")

        date_rows = iframe_soup.find_all("div", class_="row", attrs={"data-spaces": True})

        if not date_rows:
            log.info(f"  No date rows in iframe — flexible dates card")
            return [{
                "title":           title,
                "location_raw":    location_raw,
                "price":           price,
                "date_display":    "Flexible dates",
                "date_sort":       None,
                "spots_remaining": None,
                "avail":           "open",
                "booking_url":     f"{course_url}?{utm}",
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    True,
            }]

        open_count = sold_count = 0
        for row in date_rows:
            radio = row.find("input", {"type": "radio"})
            if not radio:
                continue

            did       = radio.get("value", "")
            date_text = row.get_text(strip=True)
            date_sort = parse_date_sort(date_text)

            if not date_sort:
                log.warning(f"  Could not parse date: '{date_text}'")
                continue
            if not is_future(date_sort):
                continue

            spaces = int(row.get("data-spaces", 12))
            if spaces == 0:
                avail = "sold"
                sold_count += 1
            elif spaces <= 2:
                avail = "critical"
                open_count += 1
            elif spaces <= 5:
                avail = "low"
                open_count += 1
            else:
                avail = "open"
                open_count += 1

            try:
                date_display = datetime.strptime(date_sort, "%Y-%m-%d").strftime("%b %-d, %Y")
            except Exception:
                date_display = date_text

            booking_url = (
                f"https://forms.yamnuska.com/booking.aspx"
                f"?DID={did}&NG=1&PRICE={price or ''}&{utm}"
            )

            results.append({
                "title":           title,
                "location_raw":    location_raw,
                "price":           price,
                "date_display":    date_display,
                "date_sort":       date_sort,
                "spots_remaining": spaces,
                "avail":           avail,
                "booking_url":     booking_url,
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    False,
            })

        log.info(f"  '{title}' — {open_count} open, {sold_count} sold | price=${price}")

    except requests.HTTPError as e:
        log.error(f"  HTTP {e.response.status_code} fetching iframe for {course_url}")
    except Exception as e:
        log.error(f"  Error on {course_url}: {e}")

    return results


def scrape_yamnuska() -> list:
    all_courses = []
    scraped_at  = datetime.utcnow().isoformat()
    utm         = PROVIDER["utm"]
    provider_id = PROVIDER["id"]

    session = make_session()

    log.info(f"=== Scraping {PROVIDER['name']} ({len(PROVIDER['courses'])} pages) ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        log.info("Playwright browser launched")

        for i, course_url in enumerate(PROVIDER["courses"]):
            log.info(f"[{i+1}/{len(PROVIDER['courses'])}] {course_url}")
            entries = scrape_course_page(session, browser, course_url, utm)
            for entry in entries:
                all_courses.append({
                    **entry,
                    "provider_id":   provider_id,
                    "activity_raw":  "",
                    "duration_days": None,
                    "summary":       "",
                    "scraped_at":    scraped_at,
                })
            if i < len(PROVIDER["courses"]) - 1:
                time.sleep(random.uniform(1, 3))

        browser.close()
        log.info("Playwright browser closed")

    log.info(f"Total raw courses scraped: {len(all_courses)}")
    return all_courses


# ── EMAIL ──

def send_summary(count: int, ok: bool) -> None:
    status = "✓ ok" if ok else "✗ failed"
    color  = "#2d6a11" if ok else "#a32d2d"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#1a2e1a;padding:20px 28px;border-radius:10px 10px 0 0;">
        <p style="margin:0;font-size:18px;color:#fff;font-family:Georgia,serif;">
          backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
        </p>
      </div>
      <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e8e8e8;border-top:none;">
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">yamnuska scraper</p>
        <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 16px;">{count} courses upserted</h2>
        <p style="font-size:13px;color:{color};background:#f8f8f8;padding:10px 14px;border-radius:6px;">{status} — Yamnuska Mountain Adventures</p>
        <p style="font-size:11px;color:#aaa;margin-top:16px;">Run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
      </div>
    </div>"""
    send_email(f"Yamnuska scraper — {count} courses updated", html)


# ── MAIN ──

def main():
    log.info("=== Yamnuska scraper starting ===")

    update_provider_ratings(PROVIDER["id"])

    loc_mappings    = load_location_mappings()
    activity_maps   = load_activity_mappings()
    activity_labels = load_activity_labels()
    log.info(f"Loaded {len(loc_mappings)} location mappings, {len(activity_maps)} activity mappings")

    raw_courses = scrape_yamnuska()

    if not raw_courses:
        log.warning("No courses scraped — keeping existing Supabase data")
        # EMAILS OFF
        # send_summary(0, ok=False)
        return

    processed = []
    for c in raw_courses:
        loc_raw       = c.get("location_raw") or PROVIDER["location"]
        loc_canonical = normalise_location(loc_raw, loc_mappings) or loc_raw

        activity_canonical = resolve_activity(c["title"], c.get("description", ""), activity_maps)
        badge_canonical    = build_badge(activity_canonical, c.get("duration_days"), activity_labels)
        course_id          = stable_id_v2(PROVIDER["id"], c.get("date_sort"), c["title"])

        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        PROVIDER["id"],
            "badge":              badge_canonical,
            "activity":           activity_canonical,
            "activity_raw":       c.get("activity_raw", ""),
            "activity_canonical": None,  # V2: null hides from V1 frontend
            "badge_canonical":    badge_canonical,
            "location_raw":       loc_raw,
            "location_canonical": loc_canonical,
            "date_display":       c.get("date_display"),
            "date_sort":          c.get("date_sort"),
            "duration_days":      c.get("duration_days"),
            "price":              c.get("price"),
            "spots_remaining":    c.get("spots_remaining"),
            "avail":              c.get("avail", "open"),
            "image_url":          c.get("image_url"),
            "booking_url":        c.get("booking_url"),
            "active":             c.get("avail") != "sold",
            "custom_dates":       c.get("custom_dates", False),
            "summary":            "",
            "description":        c.get("description", ""),
            "scraped_at":         c["scraped_at"],
        })

    # Batch summaries — deduplicated by title
    if processed:
        log.info(f"Generating summaries for {len(processed)} courses...")
        seen_titles   = {}
        unique_inputs = []
        for c in processed:
            if c.get("description") and c["title"] not in seen_titles:
                seen_titles[c["title"]] = c["id"]
                unique_inputs.append({
                    "id":          c["id"],
                    "title":       c["title"],
                    "description": c.get("description", ""),
                    "provider":    PROVIDER["name"],
                    "activity":    c.get("activity_canonical", "guided"),
                })
        if unique_inputs:
            summaries        = generate_summaries_batch(unique_inputs)
            title_to_summary = {c["title"]: summaries.get(c["id"], "") for c in unique_inputs}
            for c in processed:
                c["summary"] = title_to_summary.get(c["title"], "")
            log.info(f"Summaries generated: {len(summaries)}")

    # Strip description before upsert
    for c in processed:
        c.pop("description", None)

    # Deduplicate by ID
    seen = {}
    for c in processed:
        seen[c["id"]] = c
    deduped = list(seen.values())
    if len(deduped) < len(processed):
        log.warning(f"Deduplicated {len(processed) - len(deduped)} duplicate IDs")

    sb_upsert("courses", deduped)
    # Log intelligence (V2 — append-only, change-detected)
    for c in deduped:
        log_availability_change(c)
        log_price_change(c)
    log.info(f"Total courses upserted: {len(deduped)}")

    # EMAILS OFF
    # send_summary(len(deduped), ok=True)
    log.info("=== Yamnuska scraper complete ===")


if __name__ == "__main__":
    main()
