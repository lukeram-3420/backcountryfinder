#!/usr/bin/env python3
"""scraper_cloud_nine_guides.py — Hybrid Rezdy + Squarespace scraper for Cloud Nine Guides.

Cloud Nine uses TWO booking systems:
  Pass 1 (Rezdy storefront at cloudnineguides.rezdy.com):
    Wapta Ski Traverse variants, private ski guiding, alpinism (Mt Columbia),
    international ski trips (Chamonix-Zermatt, Lofoten). Dated, transactional.
  Pass 2 (Squarespace site at cloudnineguides.com):
    Rock climbing, ice climbing, alpinism (BMC etc.), AST courses, via ferrata,
    private rock/ice/alpine guiding. Inquiry-based — emit flex-date rows.

Squarespace exposes no clean nav-discoverable listing pages, so program URLs
are hardcoded (discovered via web search probe). Pass 2 dedupes against Pass 1
by fuzzy title match so Wapta + private ski guiding pages don't double-count.
"""

import re
import time
import logging
from datetime import datetime
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
    detect_url_drift,
    title_hash,
    activity_key, upsert_activity_control, load_activity_controls,
    UTM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PROVIDER = {
    "id":         "cloud-nine-guides",
    "name":       "Cloud Nine Guides",
    "storefront": "https://cloudnineguides.rezdy.com",
    "website":    "http://cloudnineguides.com",
    "location":   "Canmore, AB",
    "utm":        UTM,
}

# Cloud Nine's storefront catalog is small enough that the root URL lists
# every product. If a future scrape reveals sub-catalogs, add them here.
CATALOG_URLS = [
    "https://cloudnineguides.rezdy.com/",
]

# Title-keyword location resolution. First match wins; result is fed to
# normalise_location() so unknowns still queue to pending_location_mappings.
LOCATION_MAP = [
    ("sorcerer",      "Sorcerer Lodge, BC"),
    ("selkirk",       "Sorcerer Lodge, BC"),
    ("rogers pass",   "Rogers Pass, BC"),
    ("bugaboo",       "Bugaboos, BC"),
    ("ha ling",       "Canmore, AB"),
    ("east end of rundle", "Canmore, AB"),
    ("eeor",          "Canmore, AB"),
    ("canmore",       "Canmore, AB"),
    ("banff",         "Banff, AB"),
    ("lake louise",   "Lake Louise, AB"),
    ("yoho",          "Field, BC"),
    ("waddington",    "Waddington, BC"),
    ("revelstoke",    "Revelstoke, BC"),
    ("golden",        "Golden, BC"),
    # International expeditions — country codes are 2-letter (ISO 3166-1)
    ("chamonix",      "Chamonix, FR"),
    ("zermatt",       "Zermatt, CH"),
    ("haute route",   "Chamonix, FR"),
    ("lofoten",       "Lofoten, NO"),
]

# Per-title exclusions live in activity_controls now — hide a title by
# flipping visible=false in the admin Activity Tracking tab. Seeded from
# the historical list via seed_activity_controls.py.
_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="rezdy",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

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

# Required scoping for any HTML date regex extraction (CLAUDE.md hard rule).
SCHEDULE_CONTAINER_KEYWORDS = re.compile(
    r"schedule|dates|upcoming|session|availability|calendar",
    re.IGNORECASE,
)

# Pass 2 — Squarespace program pages. Discovered via web search probe (Apr 2026)
# since Squarespace has no clean listing-page hierarchy. Excludes Wapta Ski
# Traverse + Private ski guiding (already covered by Pass 1 Rezdy with dates).
WEBSITE_BASE = "https://www.cloudnineguides.com"
WEBSITE_PROGRAM_URLS = [
    # Rock climbing
    f"{WEBSITE_BASE}/c9g-day-rock-climbing-experience",
    f"{WEBSITE_BASE}/learn-rock-climb-series",
    f"{WEBSITE_BASE}/learn-to-lead-rock-climbing-course",
    f"{WEBSITE_BASE}/multipitch-rock-climbing-course",
    f"{WEBSITE_BASE}/trad-rock-systems-course",
    f"{WEBSITE_BASE}/rock-rescue-course",
    f"{WEBSITE_BASE}/private-rock-guiding-instruction",
    # Ice climbing
    f"{WEBSITE_BASE}/ice-climbing-experience",
    f"{WEBSITE_BASE}/iceclimbing-banff-cascade-falls",
    f"{WEBSITE_BASE}/bourgeau-right-wi4r-310m",
    f"{WEBSITE_BASE}/guided-ice-climbing-field-twisted-wi5",
    f"{WEBSITE_BASE}/wicked-wanda-wi4-65m",
    f"{WEBSITE_BASE}/guided-multi-pitch-ice-climbing",
    f"{WEBSITE_BASE}/private-ice-climbing",
    # Alpinism / mountaineering (Mt Columbia is in Rezdy — skip here)
    f"{WEBSITE_BASE}/beginner-mountaineering-course",
    f"{WEBSITE_BASE}/womens-bmc",
    f"{WEBSITE_BASE}/crevasse-rescue-course",
    f"{WEBSITE_BASE}/climb-mount-athabasca",
    f"{WEBSITE_BASE}/climb-assiniboine",
    f"{WEBSITE_BASE}/bugaboo-mountain-guides",
    f"{WEBSITE_BASE}/private-alpine-guides",
    # AST courses
    f"{WEBSITE_BASE}/AST1",
    f"{WEBSITE_BASE}/ast-1-canmore-banff-lake-louise",
    f"{WEBSITE_BASE}/ast-1-golden",
    f"{WEBSITE_BASE}/ast-1-revelstoke",
    f"{WEBSITE_BASE}/ast-1-lethbridge-fernie",
    f"{WEBSITE_BASE}/AST2",
    f"{WEBSITE_BASE}/AST2/Golden-Rogers-Pass",
    f"{WEBSITE_BASE}/ast-2-lethbridge-fernie",
    f"{WEBSITE_BASE}/private-ast-1-network",
    f"{WEBSITE_BASE}/ast-connect",
    # Via Ferrata
    f"{WEBSITE_BASE}/Mt-Stelfox-Via-Ferrata",
]

# Pass 2 — keywords (lowercased) that indicate a Pass 1 product. If the
# website page title contains any of these, skip the page (Pass 1 already
# covered it with real date data).
PASS2_TITLE_SKIP_KEYWORDS = [
    "wapta",                 # Pass 1 has 3-day, 4-day, 5-day Wapta variants
    "private backcountry ski",  # Pass 1 has 4 regional variants
    "private ski",
    "mount columbia",        # Pass 1 has Mount Columbia Ascent
    "haute route",           # Pass 1 has Chamonix-Zermatt
    "lofoten",               # Pass 1 has Lofoten DreamTrip
    "terrace ski",           # Pass 1 has Terrace Ski Touring
]

# URL drift detection — Squarespace flat-slug pattern.
# Match top-level slugs (one or two segments deep) and exclude obvious
# non-program pages (about, contact, guides, gear, etc.).
DRIFT_URL_PATTERN = re.compile(
    r"cloudnineguides\.com/[A-Za-z][A-Za-z0-9\-]+(?:/[A-Za-z0-9\-]+)?/?$"
)
DRIFT_URL_EXCLUDE = re.compile(
    r"/(about|contact|our-guides|resources|gear-shop|guides-resources|"
    r"shop|cart|checkout|account|login|admin|search|blog|press|media|"
    r"privacy|terms|policies|sitemap|wp-content|wp-admin)",
    re.IGNORECASE,
)


# ── Location resolution ──────────────────────────────────────────────────────

def resolve_location_raw(title: str, description: str = "") -> str:
    combined = f"{title} {description}".lower()
    for kw, loc in LOCATION_MAP:
        if kw in combined:
            return loc
    return PROVIDER["location"]


# ── Rezdy catalog scraping ───────────────────────────────────────────────────

def scrape_rezdy_catalogs(provider: dict) -> list:
    """Scrape every CATALOG_URLS entry, dedup by Rezdy product URL."""
    log.info(f"Scraping {provider['name']} — {provider['storefront']}")
    all_courses = []
    seen_keys = set()
    for url in CATALOG_URLS:
        log.info(f"Catalog: {url}")
        courses = scrape_rezdy_page(provider, url)
        for c in courses:
            booking_base = (c.get("booking_url") or "").split("?")[0]
            dedup_key = booking_base or c["title"]
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                all_courses.append(c)
        time.sleep(1)
    log.info(f"Total unique products: {len(all_courses)}")
    return all_courses


def scrape_rezdy_page(provider: dict, url: str) -> list:
    """Parse a Rezdy catalog page (static HTML) and return product rows."""
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
                if not _is_visible(PROVIDER["id"], title):
                    log.info(f"Skipping hidden title: {title}")
                    continue

                # Booking URL (relative or absolute)
                booking_url = None
                if title_el:
                    href = title_el.get("href", "")
                    if href.startswith("http"):
                        booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"
                    elif href.startswith("/"):
                        booking_url = f"{provider['storefront']}{href}?{provider['utm']}"
                    else:
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

                # Catalog-page description (often empty for Cloud Nine — refined by Playwright)
                desc_text = ""
                desc_el = item.select_one("p.products-list-item-overview") or \
                          item.select_one("div.products-list-item-overview p")
                if desc_el:
                    desc_text = desc_el.get_text(strip=True)

                courses.append({
                    "title":           title,
                    "provider_id":     provider["id"],
                    "location_raw":    None,
                    "date_display":    None,
                    "date_sort":       None,
                    "duration_days":   duration_days,
                    "price":           price,
                    "spots_remaining": None,
                    "avail":           "open",
                    "image_url":       image_url,
                    "booking_url":     booking_url,
                    "description":     desc_text,
                    "scraped_at":      datetime.utcnow().isoformat(),
                })
            except Exception as e:
                log.warning(f"Error parsing item: {e}")
                continue

    except Exception as e:
        log.error(f"Failed to scrape {url}: {e}")

    log.info(f"Scraped {len(courses)} courses from {url}")
    return courses


# ── Playwright product detail page ───────────────────────────────────────────

def check_course_page_playwright(browser, booking_url: str) -> dict:
    """Render Rezdy product page and extract description, price, dates."""
    result = {"available": True, "custom_dates": True, "dates": [],
              "description": "", "price": None}
    try:
        clean_url = booking_url.split("?")[0]
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
        page.goto(clean_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text()
        text_lower = page_text.lower()

        for sig in NO_AVAILABILITY_SIGNALS:
            if sig in text_lower:
                log.info(f"  No availability: {clean_url}")
                result["available"] = False
                page.close()
                return result

        desc_el = (
            soup.find("div", class_=re.compile(r"product-description|description|overview", re.I)) or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="products-list-item-overview")
        )
        if desc_el:
            result["description"] = desc_el.get_text(separator=" ", strip=True)[:800]
        if not result["description"]:
            paras = []
            for p in soup.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > 60 and len(paras) < 3:
                    paras.append(t)
            if paras:
                result["description"] = " ".join(paras)[:800]

        pm = re.search(r"(?:CAD\s*)?\$\s?([\d,]+(?:\.\d{2})?)", page_text)
        if pm:
            try:
                val = int(float(pm.group(1).replace(",", "")))
                if val >= 10:
                    result["price"] = val
            except ValueError:
                pass

        found_dates = []
        for pattern in STATIC_DATE_PATTERNS:
            found_dates.extend(re.findall(pattern, page_text))
        if found_dates:
            log.info(f"  Found {len(found_dates)} dates")
            result["dates"] = list(set(found_dates))
            result["custom_dates"] = False

        page.close()
    except PlaywrightTimeout:
        log.warning(f"  Timeout: {booking_url}")
        try: page.close()
        except Exception: pass
    except Exception as e:
        log.warning(f"  Detail page error: {e}")
        try: page.close()
        except Exception: pass
    return result


# ── Pass 2: Squarespace program pages ────────────────────────────────────────

def extract_schedule_text(soup: BeautifulSoup) -> str:
    """Per CLAUDE.md hard rule: only run date regex against schedule-like
    containers, never against the whole page text. Returns concatenated text
    from elements whose class/id matches schedule|dates|upcoming|session|
    availability|calendar (case-insensitive) plus content following an
    h2/h3/h4 whose text matches the same pattern."""
    chunks = []
    seen   = set()

    def add(el):
        if id(el) in seen:
            return
        seen.add(id(el))
        chunks.append(el.get_text(separator=" ", strip=True))

    for el in soup.find_all(True):
        class_str = " ".join(el.get("class") or [])
        id_str    = el.get("id") or ""
        if SCHEDULE_CONTAINER_KEYWORDS.search(class_str) or SCHEDULE_CONTAINER_KEYWORDS.search(id_str):
            add(el)

    for h in soup.find_all(["h2", "h3", "h4"]):
        if SCHEDULE_CONTAINER_KEYWORDS.search(h.get_text()):
            sib = h.find_next_sibling()
            while sib and getattr(sib, "name", None) not in ("h1", "h2", "h3", "h4"):
                add(sib)
                sib = sib.find_next_sibling()

    return " ".join(chunks)


def scrape_website_program(url: str) -> Optional[dict]:
    """Fetch a Cloud Nine website program page and return a row dict.
    All Pass 2 rows are flex-date (custom_dates=True) — bookings are
    inquiry-based via the website's contact form. Returns None on failure
    or if the title looks like a non-program page."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"  Failed to fetch {url}: {e}")
        return None

    # Title — h1
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1].replace("-", " ").title()
    if not title or len(title) < 3:
        return None

    title_lower = title.lower()
    if not _is_visible(PROVIDER["id"], title):
        return None
    if any(kw in title_lower for kw in PASS2_TITLE_SKIP_KEYWORDS):
        log.info(f"  Skipping (Pass 1 covers): {title}")
        return None

    # Description — first 2-3 substantial paragraphs
    desc_parts = []
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 80 and len(desc_parts) < 3:
            desc_parts.append(text)
    description = " ".join(desc_parts)[:800]

    # Image — og:image
    image_url = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image_url = og["content"]

    # Price — $XXX, accept >= $100 to avoid stray small numbers
    price = None
    pm = re.search(r"\$\s?([\d,]+)", soup.get_text())
    if pm:
        try:
            val = int(pm.group(1).replace(",", ""))
            if val >= 100:
                price = val
        except ValueError:
            pass

    # Duration — "N day(s)" near top of body
    duration_days = None
    dm = re.search(r"(\d+)\s*[-\s]?day", title + " " + description, re.IGNORECASE)
    if dm:
        duration_days = int(dm.group(1))

    # Booking URL — the website URL itself with UTM
    sep = "&" if "?" in url else "?"
    booking_url = f"{url}{sep}{UTM}"

    return {
        "title":           title,
        "provider_id":     PROVIDER["id"],
        "location_raw":    None,  # resolved later via resolve_location_raw
        "date_display":    "Flexible dates",
        "date_sort":       None,
        "duration_days":   duration_days,
        "price":           price,
        "spots_remaining": None,
        "avail":           "open",
        "image_url":       image_url,
        "booking_url":     booking_url,
        "description":     description,
        "scraped_at":      datetime.utcnow().isoformat(),
        "_pass":           "website",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    provider = PROVIDER
    log.info(f"=== {provider['name']} scraper starting ===")

    try:
        update_provider_ratings(provider["id"])
    except Exception as e:
        log.warning(f"Places update failed: {e}")

    update_provider_shared_utils(provider["id"], provider.get("shared_utils_module"))

    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    global _CONTROLS
    _CONTROLS = load_activity_controls(provider["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    raw_courses = scrape_rezdy_catalogs(provider)
    if not raw_courses:
        log.warning("No products scraped — keeping existing Supabase data")
        return

    processed = []

    log.info("Launching Playwright browser for product page rendering...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for c in raw_courses:
            if not is_future(c.get("date_sort")):
                continue

            booking_url = c.get("booking_url")
            active        = True
            custom_dates  = False
            date_display  = c.get("date_display")
            date_sort     = c.get("date_sort")
            page_desc     = ""
            page_price    = c.get("price")

            if booking_url:
                log.info(f"  Rendering: {c['title']}")
                pc = check_course_page_playwright(browser, booking_url)
                page_desc = pc.get("description", "")
                if page_price is None and pc.get("price"):
                    page_price = pc["price"]
                if not pc["available"]:
                    custom_dates = True
                    date_display = "Flexible dates"
                    date_sort    = None
                else:
                    custom_dates = pc["custom_dates"]
                    if custom_dates:
                        date_display = "Flexible dates"
                        date_sort    = None
                    elif pc["dates"] and not date_display:
                        date_display = pc["dates"][0]
                        date_sort    = parse_date_sort(date_display)
                time.sleep(1)

            description    = c.get("description") or page_desc
            loc_raw        = resolve_location_raw(c["title"], description)
            loc_canonical  = normalise_location(loc_raw, mappings)
            course_id      = stable_id_v2(provider["id"], date_sort, c["title"])

            row = {
                "id":              course_id,
                "title":           c["title"],
                "provider_id":     provider["id"],
                "location_raw":    loc_raw,
                "date_display":    date_display,
                "date_sort":       date_sort,
                "duration_days":   c.get("duration_days"),
                "price":           page_price,
                "currency":        "CAD",
                "spots_remaining": None,
                "avail":           c.get("avail", "open"),
                "image_url":       c.get("image_url"),
                "booking_url":     booking_url,
                "active":          active,
                "custom_dates":    custom_dates,
                "summary":         "",
                "search_document": "",
                "description":     description,
                "scraped_at":      c["scraped_at"],
            }
            if loc_canonical is not None:
                row["location_canonical"] = loc_canonical
            processed.append(row)

        browser.close()
        log.info("Playwright browser closed")

    log.info(f"Total processed (Pass 1): {len(processed)}")

    # ── Pass 2: Squarespace website program pages ──
    log.info(f"\n=== Pass 2: Scraping {provider['name']} website ({len(WEBSITE_PROGRAM_URLS)} URLs) ===")
    pass1_titles_lower = {c["title"].lower() for c in processed}

    for url in WEBSITE_PROGRAM_URLS:
        log.info(f"  Fetching: {url}")
        row = scrape_website_program(url)
        if not row:
            time.sleep(0.5)
            continue
        # Skip if Pass 1 already produced this title
        if row["title"].lower() in pass1_titles_lower:
            log.info(f"  Skipping (Pass 1 title match): {row['title']}")
            time.sleep(0.5)
            continue

        loc_raw       = resolve_location_raw(row["title"], row.get("description") or "")
        loc_canonical = normalise_location(loc_raw, mappings)
        course_id     = stable_id_v2(provider["id"], None, row["title"])

        out = {
            "id":              course_id,
            "title":           row["title"],
            "provider_id":     provider["id"],
            "location_raw":    loc_raw,
            "date_display":    "Flexible dates",
            "date_sort":       None,
            "duration_days":   row.get("duration_days"),
            "price":           row.get("price"),
            "currency":        "CAD",
            "spots_remaining": None,
            "avail":           "open",
            "image_url":       row.get("image_url"),
            "booking_url":     row.get("booking_url"),
            "active":          True,
            "custom_dates":    True,
            "summary":         "",
            "search_document": "",
            "description":     row.get("description", ""),
            "scraped_at":      row["scraped_at"],
        }
        if loc_canonical is not None:
            out["location_canonical"] = loc_canonical
        processed.append(out)
        time.sleep(0.5)

    log.info(f"Total processed (after Pass 2): {len(processed)}")

    # Batch summaries — dedup by title
    if processed:
        seen_titles = {}
        unique_inputs = []
        for c in processed:
            if c.get("description") and c["title"] not in seen_titles:
                seen_titles[c["title"]] = c["id"]
                unique_inputs.append({
                    "id":          c["id"],
                    "title":       c["title"],
                    "description": c.get("description", ""),
                    "provider":    provider["name"],
                })
        if unique_inputs:
            try:
                summaries = generate_summaries_batch(unique_inputs, provider_id=provider["id"])
                title_to_summary = {}
                for s in unique_inputs:
                    result = summaries.get(s["id"], {})
                    title_to_summary[s["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
                for c in processed:
                    result = title_to_summary.get(c["title"], {})
                    c["summary"]         = result.get("summary", "") if isinstance(result, dict) else result
                    c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
                log.info(f"Generated {len(summaries)} summaries")
            except Exception as e:
                log.warning(f"Summary batch failed: {e}")

    # Strip description, dedup by ID, upsert
    seen_id = {}
    for c in processed:
        c.pop("description", None)
        seen_id[c["id"]] = c
    deduped = list(seen_id.values())
    if len(deduped) < len(processed):
        log.warning(f"Deduplicated {len(processed) - len(deduped)} duplicate IDs")

    sb_upsert("courses", deduped)

    # Deactivate stale flex rows when we now have dated rows for the same title
    titles_with_dates = {c["title"] for c in deduped if c.get("date_sort")}
    if titles_with_dates:
        existing = sb_get("courses", {
            "provider_id": f"eq.{provider['id']}",
            "active":      "eq.true",
            "date_sort":   "is.null",
            "select":      "id,title",
        })
        stale = [r for r in existing if r["title"] in titles_with_dates]
        for r in stale:
            sb_patch("courses", f"id=eq.{r['id']}", {"active": False})
        if stale:
            log.info(f"Deactivated {len(stale)} stale flex rows replaced by dated rows")

    # Intelligence logging
    for c in deduped:
        log_availability_change(c)
        log_price_change(c)

    log.info(f"✅ Upserted {len(deduped)} rows")

    # URL drift check — surface any homepage program URLs not in our hardcoded list
    try:
        known = set(WEBSITE_PROGRAM_URLS) | set(CATALOG_URLS)
        detect_url_drift(
            provider_id=provider["id"],
            homepage_url=WEBSITE_BASE,
            known_urls=known,
            url_pattern=DRIFT_URL_PATTERN,
            exclude_pattern=DRIFT_URL_EXCLUDE,
        )
    except Exception as e:
        log.warning(f"URL drift check failed: {e}")

    log.info(f"=== {provider['name']} scraper complete ===")


if __name__ == "__main__":
    main()
