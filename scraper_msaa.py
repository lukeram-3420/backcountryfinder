#!/usr/bin/env python3
"""
scraper_msaa.py — Standalone Rezdy scraper for Mountain Skills Academy (MSAA).

Uses Playwright to render Rezdy product pages for descriptions and dates.
Imports shared utilities from scraper_utils.py.
"""

import os
import re
import json
import time
import logging
from datetime import datetime, date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from scraper_utils import (
    log_availability_change, log_price_change,
    sb_get, sb_upsert, sb_insert, sb_patch,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    parse_date_sort, is_future, stable_id_v2,
    update_provider_ratings, update_provider_shared_utils,
    title_hash, activity_key,
    upsert_activity_control, load_activity_controls,
    discover_rezdy_catalogs, fetch_rezdy_calendar_products,
)

# Populated at main() start via load_activity_controls(). Consulted by the
# deep scrape functions without threading it through every signature.
_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    """Activity Tracking gate. Upserts the (provider, title) pair so the
    admin sees it, then returns False if visible=false has been flipped."""
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="rezdy",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG ──

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REZDY_DOMAIN = "mountainskillsacademy.rezdy.com"
MARKETING_SITE_BASE = "https://www.mountainskillsacademy.com"

# Rezdy category IDs that drive the marketing-site monthly calendar widgets.
# Each ID maps to one marketing-site listing page (Rock Climbing Courses,
# Mountaineering Courses, AST, etc.) and surfaces every product including
# orphans not shelved in any storefront catalog.
#
# To find an ID: open the marketing listing page in a browser, F12, Network
# tab → filter "rezdy", click VIEW CALENDAR, copy the URL like
# `productsMonthlyCalendar/{id}`. Append the numeric {id} below.
MSAA_CALENDAR_CATEGORY_IDS = [
    (508377, f"{MARKETING_SITE_BASE}/rock-climbing-courses/"),  # Rock Climbing
    # TODO once captured from DevTools: Mountaineering, AST, Hiking, Skiing,
    # Guided Rock Climbing (/guided-rock-climbing/ — currently unbacked, see
    # MSAA_EXTRA_ORPHAN_URLS below).
]

# Hardcoded orphan product URLs that aren't reachable via any category in
# MSAA_CALENDAR_CATEGORY_IDS yet. Each entry is a Rezdy product URL on the
# storefront. Pass 2 injects these into the orphan render queue so the
# scraper picks them up alongside category-discovered products.
#
# REMOVE an entry once the marketing-site listing page that hosts that
# product gets its productsMonthlyCalendar/{id} category captured and added
# to MSAA_CALENDAR_CATEGORY_IDS — at that point the category fetch will
# discover the product naturally and this hardcoded entry is redundant.
MSAA_EXTRA_ORPHAN_URLS = [
    # /guided-rock-climbing/conquer-the-chief/ — capstone of the MSAA
    # progression page. Lives on the /guided-rock-climbing/ marketing
    # listing whose Rezdy category ID has not been captured yet.
    f"https://{REZDY_DOMAIN}/443528/conquer-the-chief",
]

LOCATION_RE = re.compile(
    r"(Whistler|Squamish|Seymour|Garibaldi|Pemberton|Tantalus|Vancouver|North Shore|Golden|Revelstoke|Banff|Canmore)",
    re.I,
)


PROVIDER = {
    "id":       "msaa",
    "name":     "Mountain Skills Academy",
    "storefront": "https://mountainskillsacademy.rezdy.com",
    # Fallback list used only when homepage discovery fails (network error,
    # Rezdy block, etc). The live source of truth is discover_rezdy_catalogs().
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


# ── REZDY SCRAPING FUNCTIONS ──

def scrape_rezdy(provider: dict) -> list:
    """Scrape a Rezdy storefront using confirmed HTML structure."""
    log.info(f"Scraping {provider['name']} — {provider['storefront']}")

    # Live-discover catalogs from the storefront homepage and /index. The
    # marketing site is unreachable from GitHub Actions (Cloudflare blocks
    # datacenter IPs) so it's not crawled here; orphan products are covered
    # by Pass 2 via the productsMonthlyCalendar Rezdy endpoint instead.
    crawl_pages = [provider["storefront"] + "/index"]
    discovered = discover_rezdy_catalogs(provider["storefront"], extra_pages=crawl_pages)
    fallback = provider.get("catalogs", []) or []
    seen_slugs = set()
    catalogs: list = []
    for slug in list(discovered) + list(fallback):
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        catalogs.append(slug)
    new_slugs = [s for s in discovered if s not in set(fallback)]
    if new_slugs:
        log.info(f"Discovered {len(new_slugs)} catalog(s) not in fallback list: {new_slugs}")

    if catalogs:
        all_courses = []
        seen_keys = set()  # deduplicate by booking URL base (Rezdy product ID) or title
        for catalog in catalogs:
            url = f"{provider['storefront']}/{catalog}"
            log.info(f"Scraping catalog: {url}")
            courses = scrape_rezdy_page(provider, url)
            for c in courses:
                # Use booking URL without UTM as dedup key (contains Rezdy product ID)
                booking_base = (c.get("booking_url") or "").split("?")[0]
                dedup_key = booking_base or c["title"]
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
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
                if not _is_visible(PROVIDER["id"], title):
                    log.info(f"Skipping hidden title: {title}")
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

                # Description — p.products-list-item-overview (class is on the p itself)
                desc_text = ""
                desc_el = item.select_one("p.products-list-item-overview")
                if not desc_el:
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
                    "description":   desc_text,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })

            except Exception as e:
                log.warning(f"Error parsing item from {provider['name']}: {e}")
                continue

    except Exception as e:
        log.error(f"Failed to scrape {url}: {e}")

    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


def check_course_page_playwright(browser, booking_url: str) -> dict:
    """
    Use Playwright to render a Rezdy product page and extract:
    - description (JS-rendered content)
    - price (from rendered HTML)
    - availability signals
    - any visible date information
    Returns dict: {available, custom_dates, dates, description, price}
    """
    result = {"available": True, "custom_dates": True, "dates": [], "description": "", "price": None}

    try:
        clean_url = booking_url.split("?")[0]
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
        page.goto(clean_url, wait_until="networkidle", timeout=30000)

        # Wait a moment for any remaining JS to settle
        page.wait_for_timeout(2000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text()
        text_lower = page_text.lower()

        # Check no-availability signals
        for signal in NO_AVAILABILITY_SIGNALS:
            if signal in text_lower:
                log.info(f"No availability found at {clean_url}")
                result["available"] = False
                page.close()
                return result

        # Extract description from rendered HTML
        desc_el = (
            soup.find("div", class_=re.compile(r"product-description|description|overview", re.I)) or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="products-list-item-overview")
        )
        if desc_el:
            result["description"] = desc_el.get_text(separator=" ", strip=True)[:800]
        if not result["description"]:
            # Fallback: grab first substantial paragraphs
            paras = []
            for p in soup.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > 60 and len(paras) < 3:
                    paras.append(t)
            if paras:
                result["description"] = " ".join(paras)[:800]

        # Extract price from rendered HTML
        price_match = re.search(r"(?:CAD\s*)?\$\s?([\d,]+(?:\.\d{2})?)", page_text)
        if price_match:
            try:
                val = int(float(price_match.group(1).replace(",", "")))
                if val >= 10:
                    result["price"] = val
            except ValueError:
                pass

        # Check for any visible date text in the rendered page
        found_dates = []
        for pattern in STATIC_DATE_PATTERNS:
            matches = re.findall(pattern, page_text)
            found_dates.extend(matches)
        if found_dates:
            log.info(f"Found {len(found_dates)} dates at {clean_url}")
            result["dates"] = list(set(found_dates))
            result["custom_dates"] = False

        page.close()

    except PlaywrightTimeout:
        log.warning(f"Timeout loading {booking_url}")
        try:
            page.close()
        except Exception:
            pass
    except Exception as e:
        log.warning(f"Could not check course page {booking_url}: {e}")
        try:
            page.close()
        except Exception:
            pass

    return result


def scrape_rezdy_product_page(browser, product_url: str, utm: str) -> Optional[dict]:
    """Render an individual Rezdy product page via Playwright and synthesize
    a full course dict. Used by the Pass 2 orphan-product path — products
    that aren't in any catalog but are linked from MSAA's marketing site.

    Returns None if the page can't be loaded or has no recognizable title.
    The dict carries `_pass2_rendered=True` so the main loop skips a second
    render of the same page.
    """
    page = None
    try:
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
        page.goto(product_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text()
        text_lower = page_text.lower()

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None
        if not title:
            page.close()
            return None

        image_url = None
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            image_url = og["content"]
        else:
            # Fallback: walk every <img> and pick the first one that isn't a
            # tracking pixel. Rezdy product pages embed a Facebook Pixel
            # (`facebook.com/tr?id=...`) and sometimes Google Analytics
            # beacons as the first <img> in the DOM, so a naive
            # `soup.find("img")` writes the tracking URL to courses.image_url.
            # Conquer The Chief surfaced this in May 2026 — its og:image was
            # missing and the FB pixel got promoted to the hero image on
            # the progression page.
            tracking_substrings = (
                "facebook.com/tr",
                "google-analytics.com",
                "googletagmanager.com",
                "doubleclick.net",
                "/pixel",
            )
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if not src:
                    continue
                if any(t in src for t in tracking_substrings):
                    continue
                image_url = src
                if image_url.startswith("//"):
                    image_url = "https:" + image_url
                break

        price = None
        price_match = re.search(r"(?:CAD\s*)?\$\s?([\d,]+(?:\.\d{2})?)", page_text)
        if price_match:
            try:
                val = int(float(price_match.group(1).replace(",", "")))
                if val >= 10:
                    price = val
            except ValueError:
                pass

        duration_days = None
        dur_match = re.search(r"(\d+(?:\.\d+)?)\s*day", title, re.I)
        if dur_match:
            duration_days = float(dur_match.group(1))

        location_raw = None
        loc_match = LOCATION_RE.search(title + " " + page_text[:1000])
        if loc_match:
            location_raw = loc_match.group(0).title()

        desc_el = (
            soup.find("div", class_=re.compile(r"product-description|description|overview", re.I)) or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="products-list-item-overview")
        )
        description = desc_el.get_text(separator=" ", strip=True)[:800] if desc_el else ""
        if not description:
            paras = []
            for p in soup.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > 60 and len(paras) < 3:
                    paras.append(t)
            if paras:
                description = " ".join(paras)[:800]

        custom_dates = True
        date_display = "Flexible dates"
        date_sort = None
        unavailable = any(s in text_lower for s in NO_AVAILABILITY_SIGNALS)
        if not unavailable:
            found_dates: list = []
            for pattern in STATIC_DATE_PATTERNS:
                found_dates.extend(re.findall(pattern, page_text))
            if found_dates:
                seen_d: set = set()
                ordered = [d for d in found_dates if not (d in seen_d or seen_d.add(d))]
                date_display = ordered[0]
                date_sort = parse_date_sort(date_display)
                custom_dates = False

        page.close()
        booking_url = f"{product_url.rstrip('/')}?{utm}"

        return {
            "title":            title,
            "provider_id":      PROVIDER["id"],
            "location_raw":     location_raw,
            "date_display":     date_display,
            "date_sort":        date_sort,
            "duration_days":    duration_days,
            "price":            price,
            "spots_remaining":  None,
            "avail":            "open",
            "image_url":        image_url,
            "booking_url":      booking_url,
            "description":      description,
            "custom_dates":     custom_dates,
            "booking_mode":     "request" if custom_dates else "instant",
            "scraped_at":       datetime.utcnow().isoformat(),
            "_pass2_rendered":  True,
        }
    except PlaywrightTimeout:
        log.warning(f"Timeout loading orphan product {product_url}")
    except Exception as e:
        log.warning(f"Could not scrape orphan product {product_url}: {e}")
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
    return None


# ── MAIN ──

def main():
    provider = PROVIDER
    log.info(f"=== {provider['name']} scraper starting ===")

    # Update provider ratings from Google Places
    update_provider_ratings(provider["id"])

    update_provider_shared_utils(provider["id"], provider.get("shared_utils_module"))

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    # Activity tracking — admin-toggled visibility per (provider, title).
    global _CONTROLS
    _CONTROLS = load_activity_controls(provider["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    location_flags = []

    # Scrape Rezdy catalog pages (static HTML — no Playwright needed)
    raw_courses = scrape_rezdy(provider)
    processed = []

    # Launch Playwright for product detail page rendering
    log.info("Launching Playwright browser for product page rendering...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        log.info("Playwright browser launched")

        # Pass 2 — calendar-endpoint discovery on the Rezdy domain.
        # MSAA's marketing site embeds productsMonthlyCalendar widgets; one
        # category ID per listing page covers products outside the
        # storefront catalogs (e.g. Trad Lead & Progression, Rock Rescue).
        # Endpoint is on rezdy.com so it's reachable from GitHub Actions —
        # the marketing site itself is Cloudflare-blocked. Returns empty if
        # Rezdy's referer/origin allowlist rejects the request.
        discovered_products: list = []
        for cat_id, cat_referer in MSAA_CALENDAR_CATEGORY_IDS:
            urls = fetch_rezdy_calendar_products(
                provider["storefront"], cat_id, referer=cat_referer,
            )
            discovered_products.extend(urls)

        # Inject hardcoded orphans (products not reachable via any category
        # listing yet — see MSAA_EXTRA_ORPHAN_URLS for rationale).
        if MSAA_EXTRA_ORPHAN_URLS:
            log.info(f"Injecting {len(MSAA_EXTRA_ORPHAN_URLS)} hardcoded orphan URL(s)")
            discovered_products.extend(MSAA_EXTRA_ORPHAN_URLS)

        pass1_bases = {(c.get("booking_url") or "").split("?")[0] for c in raw_courses}
        seen_orphans: set = set()
        orphan_urls: list = []
        for u in discovered_products:
            if u in seen_orphans or u in pass1_bases:
                continue
            seen_orphans.add(u)
            orphan_urls.append(u)

        log.info(f"Pass 2: {len(discovered_products)} URL(s) found across "
                 f"{len(MSAA_CALENDAR_CATEGORY_IDS)} categor(ies), "
                 f"{len(orphan_urls)} not already in Pass 1")
        pass2_count = 0
        for url in orphan_urls:
            log.info(f"  Pass 2 render: {url}")
            course = scrape_rezdy_product_page(browser, url, provider["utm"])
            if not course:
                continue
            if not _is_visible(provider["id"], course["title"]):
                log.info(f"  Skipping hidden title: {course['title']}")
                continue
            raw_courses.append(course)
            pass2_count += 1
            time.sleep(1)
        log.info(f"Pass 2: {pass2_count} orphan product(s) added to scrape queue")

        for c in raw_courses:
            # Skip past courses
            if not is_future(c.get("date_sort")):
                log.info(f"Skipping past course: {c['title']}")
                continue

            # Normalise location (returns Optional[str])
            loc_raw = c.get("location_raw") or ""
            loc_canonical = None
            if loc_raw:
                loc_canonical = normalise_location(loc_raw, mappings)
                if not loc_canonical:
                    log.warning(f"Unmatched location: '{loc_raw}' for '{c['title']}'")
                    location_flags.append({"location_raw": loc_raw, "provider_id": provider["id"], "course_title": c["title"]})

            # Render product page with Playwright for description + dates.
            # Pass 2 rows are already fully rendered — skip the second fetch.
            booking_url = c.get("booking_url")
            if c.get("_pass2_rendered"):
                active = True
                custom_dates = c.get("custom_dates", False)
                date_display = c.get("date_display")
                date_sort = c.get("date_sort")
                page_description = c.get("description", "")
                page_price = c.get("price")
            else:
                active = True
                custom_dates = False
                date_display = c.get("date_display")
                date_sort = c.get("date_sort")
                page_description = ""
                page_price = c.get("price")
            if booking_url and not c.get("_pass2_rendered"):
                log.info(f"  Rendering: {c['title']}")
                page_check = check_course_page_playwright(browser, booking_url)
                page_description = page_check.get("description", "")
                # Use Playwright-extracted price if catalog didn't have one
                if page_price is None and page_check.get("price"):
                    page_price = page_check["price"]
                if not page_check["available"]:
                    log.info(f"  No availability — flexible dates: {c['title']}")
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
                time.sleep(1)  # brief pause between Playwright page loads

            course_id = stable_id_v2(provider["id"], date_sort, c["title"])

            row = {
                "id":                 course_id,
                "title":              c["title"],
                "provider_id":        provider["id"],
                "location_raw":       loc_raw or None,
                "date_display":       date_display,
                "date_sort":          date_sort,
                "duration_days":      c.get("duration_days"),
                "price":              page_price,
                "spots_remaining":    c.get("spots_remaining"),
                "avail":              c.get("avail", "open"),
                "image_url":          c.get("image_url"),
                "booking_url":        booking_url,
                "active":             active,
                "custom_dates":       custom_dates,
                "booking_mode":       "request" if custom_dates else "instant",
                "summary":            c.get("summary", ""),
                "search_document":    c.get("search_document", ""),
                "description":        c.get("description", "") or page_description,
                "scraped_at":         c["scraped_at"],
            }
            # Omit location_canonical when None so a failed Haiku call doesn't
            # null out a previously-resolved canonical on re-scrape.
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            processed.append(row)

        browser.close()
        log.info("Playwright browser closed")

    # Batch generate summaries
    if processed:
        log.info(f"Generating summaries for {len(processed)} {provider['name']} courses...")
        summary_inputs = [
            {
                "id":          c["id"],
                "title":       c["title"],
                "description": c.get("description", ""),
                "provider":    provider["name"],
            }
            for c in processed if c.get("description")
        ]
        if summary_inputs:
            summaries = generate_summaries_batch(summary_inputs, provider_id=PROVIDER["id"])
            for c in processed:
                if c["id"] in summaries:
                    result = summaries.get(c["id"], {})
                    c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                    c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            log.info(f"Summaries generated: {len(summaries)}")

    # Deduplicate by ID
    if processed:
        seen = {}
        for c in processed:
            seen[c["id"]] = c
        deduped = list(seen.values())
        if len(deduped) < len(processed):
            log.warning(f"Deduplicated {len(processed) - len(deduped)} duplicate course IDs before upsert")

        # Strip description — it's a scrape-time field, not stored in Supabase
        for c in deduped:
            c.pop("description", None)
        sb_upsert("courses", deduped)
        # Log intelligence (V2 — append-only, change-detected)
        for c in deduped:
            log_availability_change(c)
            log_price_change(c)
        log.info(f"Total courses upserted: {len(deduped)}")

        # Clean up stale flexible-date rows where we now have dated rows
        titles_with_dates = {c["title"] for c in deduped if c.get("date_sort")}
        if titles_with_dates:
            existing = sb_get("courses", {
                "provider_id": f"eq.{provider['id']}",
                "active": "eq.true",
                "date_sort": "is.null",
                "select": "id,title",
            })
            stale = [r for r in existing if r["title"] in titles_with_dates]
            for r in stale:
                sb_patch("courses", f"id=eq.{r['id']}", {"active": False})
            if stale:
                log.info(f"Deactivated {len(stale)} stale flexible-date rows now replaced by dated rows")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    # Flag unmatched locations
    if location_flags:
        for flag in location_flags:
            sb_insert("location_flags", flag)

    log.info(f"=== {provider['name']} scraper complete ===")


if __name__ == "__main__":
    main()
