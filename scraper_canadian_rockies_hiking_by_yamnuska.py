#!/usr/bin/env python3
"""
Scraper: Canadian Rockies Hiking by Yamnuska (canadian-rockies-hiking-by-yamnuska)
Site:    https://canadianrockieshiking.com/
Platform: WordPress + forms.yamnuska.com booking system (same as scraper_yamnuska.py)

Booking dates and availability are embedded in a tripDates iframe on each trip page.
The iframe contains date radio buttons with data-spaces availability counts.
Uses Playwright to extract the iframe src (JS-rendered), then requests to parse
the iframe HTML for dates and spots.
"""

import re
import time
import random
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from scraper_utils import (
    sb_upsert,
    sb_get,
    sb_patch,
    stable_id_v2,
    load_location_mappings,
    normalise_location,
    generate_summaries_batch,
    log_availability_change,
    log_price_change,
    update_provider_ratings,
    update_provider_shared_utils,
    spots_to_avail,
    append_utm,
    parse_date_sort,
    is_future,
    UTM,
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
    "id":       "canadian-rockies-hiking-by-yamnuska",
    "name":     "Canadian Rockies Hiking by Yamnuska",
    "website":  "https://canadianrockieshiking.com/",
    "location": "Canmore, AB",
}

BASE_URL = "https://canadianrockieshiking.com"

# Listing pages whose linked trip pages we want to collect.
# Include sub-category pages explicitly so Playwright doesn't need to recurse.
LISTING_PAGES = [
    "https://canadianrockieshiking.com/backpacking-trips/",
    "https://canadianrockieshiking.com/backpacking-trips/easiest-programs/",
    "https://canadianrockieshiking.com/backpacking-trips/moderate-trips/",
    "https://canadianrockieshiking.com/backpacking-trips/challenging-trips/",
    "https://canadianrockieshiking.com/canadian-rockies-hiking-tours/",
    "https://canadianrockieshiking.com/courses-specialty-programs/",
    "https://canadianrockieshiking.com/winter-adventure-programs/",
]

# URL path segments that are definitely not bookable trip pages.
EXCLUDE_SLUGS = {
    "rental-gear", "contact-us", "about-us", "guides-and-staff",
    "booking-information", "trip-difficulty-ratings", "groupcorporate",
    "policies", "private-program-request-form", "custom-trips",
    "blog", "news", "faq", "gallery", "media", "press",
    "sitemap", "search", "cart", "checkout", "shop",
}

# iframe src param key → canonical location raw string.
# Keys are matched against the query-string of the tripDates iframe src URL.
# The actual key names are discovered at runtime; this covers known possibilities.
IFRAME_LOCATION_MAP = {
    "canmore":    "Canmore, AB",
    "banff":      "Banff, AB",
    "jasper":     "Jasper, AB",
    "lakelouise": "Lake Louise, AB",
    "yoho":       "Field, BC",
    "rogers":     "Rogers Pass, BC",
    "robson":     "Mount Robson, BC",
    "golden":     "Golden, BC",
    "kootenay":   "Kootenay, BC",
    "calgary":    "Calgary, AB",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ── Requests session (for iframe fetches — plain HTML, no JS) ─────────────────

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Referer":         BASE_URL + "/",
    })
    return session


# ── URL collection ────────────────────────────────────────────────────────────

# Category index pages — these are crawled as listings, not as trip detail pages.
CATEGORY_INDEX_PATHS = {
    "/backpacking-trips/",
    "/backpacking-trips/easiest-programs/",
    "/backpacking-trips/moderate-trips/",
    "/backpacking-trips/challenging-trips/",
    "/canadian-rockies-hiking-tours/",
    "/courses-specialty-programs/",
    "/winter-adventure-programs/",
    "/wilderness-first-aid-courses/",
}

# File extensions that are definitely not trip pages.
STATIC_FILE_EXTS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".zip", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mov",
)


def _is_trip_url(href: str) -> bool:
    """Return True if href looks like a bookable trip/course detail page."""
    if not href:
        return False
    parsed = urlparse(href)

    # Host must be exactly canadianrockieshiking.com — reject yamnuska.com
    # strays and double-prefixed paths like canadianrockieshiking.com//yamnuska.com/.
    if parsed.netloc.lower() not in ("canadianrockieshiking.com", "www.canadianrockieshiking.com"):
        return False

    path = parsed.path.lower()

    # Static assets (PDFs, images) routed through wp-content/uploads/
    if path.endswith(STATIC_FILE_EXTS):
        return False
    if "/wp-content/" in path:
        return False

    # Double-prefixed paths like /canadianrockieshiking.com/... (broken <a href>s)
    if "canadianrockieshiking.com" in path or "yamnuska.com" in path:
        return False

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return False  # top-level pages

    # Category index pages
    normalised = "/" + "/".join(parts) + "/"
    if normalised in CATEGORY_INDEX_PATHS:
        return False

    # Exclude known non-trip slugs at any depth
    if any(p in EXCLUDE_SLUGS for p in parts):
        return False

    return True


def collect_course_urls(browser) -> list:
    """
    Use Playwright to load each listing page and collect trip detail page URLs.
    Returns a deduplicated list of absolute trip URLs.
    """
    seen = set()
    urls = []

    for listing_url in LISTING_PAGES:
        log.info(f"  Crawling listing page: {listing_url}")
        try:
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
            page.goto(listing_url, wait_until="domcontentloaded", timeout=30000)
            html = page.content()
            page.close()

            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/"):
                    href = BASE_URL + href
                if not href.startswith("http"):
                    continue
                # Normalise: strip query strings and fragments
                href = urlparse(href)._replace(query="", fragment="").geturl().rstrip("/") + "/"
                if _is_trip_url(href) and href not in seen:
                    seen.add(href)
                    urls.append(href)
                    log.info(f"    Found trip URL: {href}")

        except Exception as e:
            log.warning(f"  Failed to crawl listing page {listing_url}: {e}")
        time.sleep(random.uniform(0.5, 1.5))

    log.info(f"Collected {len(urls)} trip URLs from listing pages")
    return urls


# ── Playwright: extract iframe src from JS-rendered course page ───────────────

def get_iframe_src_playwright(browser, course_url: str) -> tuple:
    """
    Load a course page with headless Chromium and extract:
      - title (h1)
      - description (first 2 long paragraphs in entry-content)
      - OG image URL
      - tripDates iframe src URL
      - page_price (fallback from page HTML)

    Returns (title, description, image_url, iframe_src, page_price).
    iframe_src is None if no tripDates iframe found.
    """
    title       = course_url.rstrip("/").split("/")[-1].replace("-", " ").title()
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

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        page.close()

        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        if not _is_visible(PROVIDER["id"], title):
            log.info(f"  Skipping hidden title: {title}")
            return []

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

        price_match = re.search(r"\$\s?([\d,]+)", soup.get_text())
        if price_match:
            try:
                val = int(price_match.group(1).replace(",", ""))
                if val >= 50:
                    page_price = val
            except ValueError:
                pass

    except Exception as e:
        log.error(f"  Playwright error on {course_url}: {e}")
        try:
            page.close()
        except Exception:
            pass

    return title, description, image_url, iframe_src, page_price


# ── Per-course scraper ────────────────────────────────────────────────────────

# Titles that indicate the page is not a real trip (404, listing, etc.)
SKIP_TITLE_PATTERNS = re.compile(
    r"^(404|not found|error|easiest programs|moderate trips|challenging trips|"
    r"winter adventure programs|more backcountry adventures|"
    r"wilderness first responder recertifications)$",
    re.IGNORECASE,
)


def scrape_course_page(session: requests.Session, browser, course_url: str) -> list:
    """
    Scrape one trip page using the same hybrid approach as scraper_yamnuska.py:
      1. Playwright loads the main page → extracts tripDates iframe src
      2. requests fetches the iframe URL → parses dates + availability radio buttons
    Falls back to a single custom_dates=True card when no iframe is found.
    """
    results  = []
    fallback = {
        "title":           course_url.rstrip("/").split("/")[-1].replace("-", " ").title(),
        "location_raw":    PROVIDER["location"],
        "price":           None,
        "date_display":    None,
        "date_sort":       None,
        "spots_remaining": None,
        "avail":           "open",
        "booking_url":     append_utm(course_url),
        "image_url":       None,
        "description":     "",
        "custom_dates":    True,
    }

    try:
        title, description, image_url, iframe_src, page_price = get_iframe_src_playwright(
            browser, course_url
        )

        # Skip 404 pages and category listings that slipped through URL filtering
        if SKIP_TITLE_PATTERNS.match((title or "").strip()):
            log.info(f"  Skipping non-trip page: {title!r}")
            return []

        fallback.update({"title": title, "image_url": image_url,
                         "description": description, "price": page_price,
                         "booking_url": append_utm(course_url)})

        if not iframe_src:
            log.info(f"  No tripDates iframe — flex card: {title}")
            return [fallback]

        parsed = urlparse(iframe_src)
        params = parse_qs(parsed.query)

        # Resolve location from iframe src param keys
        location_raw = PROVIDER["location"]
        location_key = None
        for key in IFRAME_LOCATION_MAP:
            val = params.get(key, [""])[0]
            if val and len(val) > 8:
                location_key = key
                location_raw = IFRAME_LOCATION_MAP[key]
                break

        if not location_key:
            log.info(f"  No location GUID in iframe src — flex card: {title}")
            log.info(f"  iframe params: { {k: v for k, v in params.items()} }")
            fallback["location_raw"] = location_raw
            return [fallback]

        log.info(f"  Location: {location_key} → {location_raw}")

        # Price — 4-level fallback (mirrors scraper_yamnuska.py)
        price     = None
        price_src = "null"

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
                if val >= 10:
                    price = val
                    price_src = f"URL param ({price_key})"
            except (ValueError, TypeError):
                pass

        # Fetch iframe HTML
        time.sleep(random.uniform(0.5, 1.0))
        iframe_resp = session.get(iframe_src, timeout=20)
        iframe_resp.raise_for_status()
        iframe_soup = BeautifulSoup(iframe_resp.text, "html.parser")

        if price is None:
            pm = re.search(r"\$\s?([\d,]+)", iframe_soup.get_text())
            if pm:
                try:
                    val = int(pm.group(1).replace(",", ""))
                    if val >= 50:
                        price = val
                        price_src = "iframe HTML"
                except ValueError:
                    pass

        if price is None and page_price is not None:
            price = page_price
            price_src = "page HTML"

        log.info(f"  Price: ${price} ({price_src})")

        date_rows = iframe_soup.find_all("div", class_="row", attrs={"data-spaces": True})

        if not date_rows:
            log.info(f"  No date rows in iframe — flex card: {title}")
            fallback.update({"location_raw": location_raw, "price": price})
            return [fallback]

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

            spaces          = int(row.get("data-spaces", 12))
            spots_remaining = spaces
            avail           = spots_to_avail(spots_remaining)

            if avail == "sold":
                sold_count += 1
            else:
                open_count += 1

            try:
                date_display = datetime.strptime(date_sort, "%Y-%m-%d").strftime("%b %-d, %Y")
            except Exception:
                date_display = date_text

            # Booking URL: use the domain from the iframe src so we don't hardcode
            booking_base = f"{parsed.scheme}://{parsed.netloc}/booking.aspx"
            booking_url  = append_utm(
                f"{booking_base}?DID={did}&NG=1&PRICE={price or ''}"
            )

            results.append({
                "title":           title,
                "location_raw":    location_raw,
                "price":           price,
                "date_display":    date_display,
                "date_sort":       date_sort,
                "spots_remaining": spots_remaining,
                "avail":           avail,
                "booking_url":     booking_url,
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    False,
            })

        log.info(f"  '{title}' — {open_count} open, {sold_count} sold | price=${price}")

    except requests.HTTPError as e:
        log.error(f"  HTTP {e.response.status_code} on iframe for {course_url}")
    except Exception as e:
        log.error(f"  Error on {course_url}: {e}")

    return results if results else [fallback]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info(f"=== {PROVIDER['name']} scraper starting ===")

    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        log.warning(f"  Places update failed: {e}")

    update_provider_shared_utils(PROVIDER["id"], PROVIDER.get("shared_utils_module"))

    global _CONTROLS
    _CONTROLS = load_activity_controls(PROVIDER["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    loc_mappings = load_location_mappings()
    log.info(f"Loaded {len(loc_mappings)} location mappings")

    scraped_at = datetime.utcnow().isoformat()
    all_raw    = []
    session    = make_session()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        log.info("Playwright browser launched")

        # 1. Collect trip URLs from listing pages
        course_urls = collect_course_urls(browser)
        if not course_urls:
            log.warning("No trip URLs found — exiting")
            browser.close()
            return

        # 2. Scrape each trip page
        for i, url in enumerate(course_urls):
            log.info(f"[{i+1}/{len(course_urls)}] {url}")
            entries = scrape_course_page(session, browser, url)
            for entry in entries:
                all_raw.append({
                    **entry,
                    "provider_id":    PROVIDER["id"],
                    "duration_days":  None,
                    "currency":       "CAD",
                    "summary":        "",
                    "search_document": "",
                    "scraped_at":     scraped_at,
                })
            if i < len(course_urls) - 1:
                time.sleep(random.uniform(1, 2))

        browser.close()
        log.info("Playwright browser closed")

    log.info(f"Total raw rows scraped: {len(all_raw)}")

    if not all_raw:
        log.warning("No rows — keeping existing Supabase data")
        return

    # 3. Normalise locations + build final rows
    processed = []
    for c in all_raw:
        loc_raw       = c.get("location_raw") or PROVIDER["location"]
        loc_canonical = normalise_location(loc_raw, loc_mappings)

        course_id = stable_id_v2(PROVIDER["id"], c.get("date_sort"), c["title"])
        row = {
            "id":              course_id,
            "title":           c["title"],
            "provider_id":     PROVIDER["id"],
            "location_raw":    loc_raw,
            "date_display":    c.get("date_display"),
            "date_sort":       c.get("date_sort"),
            "duration_days":   c.get("duration_days"),
            "price":           c.get("price"),
            "currency":        "CAD",
            "spots_remaining": c.get("spots_remaining"),
            "avail":           c.get("avail", "open"),
            "image_url":       c.get("image_url"),
            "booking_url":     c.get("booking_url"),
            "active":          c.get("avail") != "sold",
            "custom_dates":    c.get("custom_dates", False),
            "summary":         "",
            "search_document": "",
            "description":     c.get("description", ""),
            "scraped_at":      scraped_at,
        }
        if loc_canonical is not None:
            row["location_canonical"] = loc_canonical
        processed.append(row)

    # 4. Generate summaries — deduplicated by title
    if processed:
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
                })
        if unique_inputs:
            summaries        = generate_summaries_batch(unique_inputs, provider_id=PROVIDER["id"])
            title_to_summary = {}
            for s in unique_inputs:
                result = summaries.get(s["id"], {})
                title_to_summary[s["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
            for c in processed:
                result = title_to_summary.get(c["title"], {})
                c["summary"]         = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            log.info(f"Generated summaries for {len(summaries)} courses")

    # 5. Strip description (not a courses column)
    for c in processed:
        c.pop("description", None)

    # 6. Deduplicate by ID
    seen   = {}
    for c in processed:
        seen[c["id"]] = c
    deduped = list(seen.values())
    if len(deduped) < len(processed):
        log.warning(f"Deduplicated {len(processed) - len(deduped)} duplicate IDs")

    # 7. Upsert
    sb_upsert("courses", deduped)

    # 8. Deactivate stale rows (cleans up garbage from earlier bad runs —
    # rows upserted this run stay active; everything else gets active=false)
    seen_ids       = {c["id"] for c in deduped}
    existing       = sb_get("courses", {
        "provider_id": f"eq.{PROVIDER['id']}",
        "select":      "id",
    })
    stale_ids      = {row["id"] for row in existing} - seen_ids
    for cid in stale_ids:
        sb_patch("courses", f"id=eq.{cid}", {"active": False})
    if stale_ids:
        log.info(f"Deactivated {len(stale_ids)} stale rows")

    # 9. Intelligence logging
    for c in deduped:
        log_availability_change(c)
        log_price_change(c)

    log.info(f"✅ Upserted {len(deduped)} rows")
    log.info("=== Scraper complete ===")


if __name__ == "__main__":
    main()
