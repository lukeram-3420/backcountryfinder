#!/usr/bin/env python3
"""
Scraper: Bow Valley Canyon Tours (bow-valley-canyon-tours)
Platform: Checkfront (widget scrape — Public API is disabled on this tenant)

bowvalleycanyoning.ca embeds a Checkfront iframe pointing to
canadian-wilderness-school-expeditions.checkfront.com. The tenant has the
public JSON API turned off (401 on /api/3.0/item), so we render the
booking widget HTML with Playwright and extract item data from the rendered
DOM. All rows are emitted as custom_dates=True (flex-date) because dates
live behind an interactive calendar modal that requires per-item click-through
— not worth the complexity until the provider enables the public API.

When the provider eventually toggles Public API on (Settings → API →
Public API in Checkfront admin), swap this back to a JSON-API scraper
mirroring scraper_aaa.py / scraper_girth_hitch_guiding.py.
"""

import os
import re
import time
import datetime
import logging

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from scraper_utils import (
    sb_upsert, stable_id_v2,
    log_availability_change, log_price_change,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    append_utm,
    title_hash,
    activity_key, upsert_activity_control, load_activity_controls,
    UTM,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "bow-valley-canyon-tours",
    "name":     "Bow Valley Canyon Tours",
    "website":  "https://www.bowvalleycanyoning.ca/",
    "location": "Banff, AB",
}

# Checkfront widget base URL (parent company tenant)
WIDGET_BASE = "https://canadian-wilderness-school-expeditions.checkfront.com/reserve/"

# Item IDs from the iframe filter on bowvalleycanyoning.ca/booking/
# (14 items the BVC site explicitly surfaces)
ITEM_FILTER = "9,8,37,40,26,32,14,7,17,29,36,34,35,28"

# Categories to crawl (matching the iframe filter, minus add-ons + gift certs)
# Keys are Checkfront category IDs from the iframe HTML.
KEEP_CATEGORIES = [
    (3, "Canyoning"),
    (4, "4x4 Tours"),
    (5, "Courses"),
]

# Per-title exclusions live in activity_controls now — hide a title by
# flipping visible=false in the admin Activity Tracking tab. Seeded from
# the historical list via seed_activity_controls.py.
_CONTROLS: dict = {}


def _is_visible(provider_id: str, title: str) -> bool:
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="checkfront",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

LOCATION_MAP = [
    ("kananaskis",   "Kananaskis, AB"),
    ("canmore",      "Canmore, AB"),
    ("three sisters","Canmore, AB"),
    ("ha ling",      "Canmore, AB"),
    ("lake louise",  "Lake Louise, AB"),
    ("banff",        "Banff, AB"),
    ("bow valley",   "Banff, AB"),
    ("yoho",         "Field, BC"),
    ("kootenay",     "Kootenay, BC"),
    ("radium",       "Radium Hot Springs, BC"),
]


def resolve_location_raw(title: str, description: str = "") -> str:
    combined = f"{title} {description}".lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in combined:
            return loc
    return PROVIDER["location"]


# ── Playwright widget scrape ──────────────────────────────────────────────────

def widget_url(category_id: int) -> str:
    # Probe with a mid-season date range so canyoning (summer-only) items
    # all render. The widget filters item visibility by the start/end date
    # availability, so a shoulder-season probe (April) hides summer items.
    today = datetime.date.today()
    summer_start = today.replace(month=7, day=15) if today.month < 7 else today
    summer_end = summer_start + datetime.timedelta(days=30)
    return (
        f"{WIDGET_BASE}?inline=1&header=hide&options=tabs"
        f"&filter_item_id={ITEM_FILTER}"
        f"&filter_category_id=3,4,5"
        f"&category_id={category_id}"
        f"&start_date={summer_start.isoformat()}&end_date={summer_end.isoformat()}"
        f"&ssl=1&provider=droplet"
    )


def parse_price(text: str) -> int | None:
    """Extract first $XXX price (>=10) from text. Strips commas."""
    for m in re.finditer(r"\$\s*([\d,]+)(?:\.\d+)?", text or ""):
        try:
            val = int(m.group(1).replace(",", ""))
            if val >= 10:
                return val
        except ValueError:
            continue
    return None


def parse_duration_days(text: str) -> float | None:
    """Extract '4 hours' / '1 day' / '2 days' from text."""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*day", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*hour", text, re.IGNORECASE)
    if m:
        return round(float(m.group(1)) / 8, 2)  # rough day-equivalent
    return None


def normalize_title(raw: str) -> str:
    """Strip trailing punctuation and collapse internal whitespace. The
    Checkfront widget sometimes renders a title with a trailing period and
    sometimes without — identical titles that differ only by punctuation hash
    to different stable IDs and leave orphan courses/course_summaries rows
    behind each run.
    """
    if not raw:
        return ""
    t = re.sub(r"\s+", " ", raw).strip()
    return t.rstrip(".,;:!?·")


def _item_scope(h, container):
    """Return a list of Tag nodes covering a single item anchored at heading h.

    Ascends from h until we hit a parent that either (a) encloses multiple
    sibling item headings (it's a list container, not an item container) or
    (b) carries a price/book signal. When the first ancestor is already a
    list container, the item has no dedicated wrapper — slice by sibling
    from h up to (but not including) the next heading.
    """
    node = h
    for _ in range(6):
        parent = node.parent
        if not parent or parent is container:
            break
        if len(parent.find_all(["h2", "h3", "h4"])) > 1:
            break  # list container — don't collapse neighbours into this item
        text = parent.get_text(" ", strip=True)
        has_price = bool(re.search(r"\$\s*\d", text))
        has_book  = bool(parent.find("a", href=re.compile(r"reserve|book|item_id", re.I))) \
                    or bool(parent.find(string=re.compile(r"book|reserve|details", re.I)))
        if has_price or has_book:
            return [parent]
        node = parent

    if node is h:
        slice_ = [h]
        for sib in h.find_next_siblings():
            if getattr(sib, "name", None) in ("h2", "h3", "h4"):
                break
            slice_.append(sib)
        return slice_
    return [node]


def scrape_category(browser, category_id: int, category_name: str) -> list:
    """Load the widget for one category, wait for items, parse the DOM."""
    url = widget_url(category_id)
    log.info(f"  Category {category_id} ({category_name}): {url}")
    items = []

    try:
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-CA,en;q=0.9"})
        page.goto(url, wait_until="networkidle", timeout=45000)
        # Wait for the items container to populate (or for the spinner to leave)
        try:
            page.wait_for_function(
                "document.querySelector('#cf-items') && document.querySelector('#cf-items').children.length > 0",
                timeout=20000,
            )
        except PlaywrightTimeout:
            log.warning(f"    No #cf-items children rendered for category {category_id}")
        # Give late AJAX a moment to settle
        page.wait_for_timeout(1500)
        html = page.content()
        page.close()
    except Exception as e:
        log.warning(f"  Playwright error on category {category_id}: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", id="cf-items")
    if not container:
        return []

    if os.environ.get("BVC_DEBUG"):
        dump = str(container)[:4000]
        log.info(f"    [DEBUG] cf-items HTML (first 4000 chars):\n{dump}")

    # Checkfront widget renders item classes inconsistently across tenants.
    # Anchor each item on a heading, then find the smallest DOM scope that
    # belongs to that item only. _item_scope stops ascending when a parent
    # encloses multiple sibling headings (collapsing them would produce
    # identical descriptions and kneecap summary generation — Haiku dedups
    # by description).
    headings = container.find_all(["h2", "h3", "h4"])
    scopes = []  # [(title, [Tag, ...]), ...]
    seen_titles = set()
    for h in headings:
        title = normalize_title(h.get_text(" ", strip=True))
        if len(title) < 3:
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        if not _is_visible(PROVIDER["id"], title):
            seen_titles.add(key)
            continue
        scopes.append((title, _item_scope(h, container)))
        seen_titles.add(key)

    log.info(f"    Found {len(scopes)} item scopes from {len(headings)} headings")

    for title, scope in scopes:
        node_text = " ".join(n.get_text(" ", strip=True) for n in scope if hasattr(n, "get_text"))
        price = parse_price(node_text)
        duration = parse_duration_days(node_text)

        # Description — first substantial <p> in scope, fallback to node_text slice
        desc = ""
        for n in scope:
            if not hasattr(n, "find_all"):
                continue
            for p in n.find_all("p"):
                t = p.get_text(strip=True)
                if len(t) > 60:
                    desc = t[:600]
                    break
            if desc:
                break
        if not desc:
            desc = node_text[:400]

        # Image — first <img> anywhere in scope
        image_url = None
        for n in scope:
            if not hasattr(n, "find"):
                continue
            img = n.find("img")
            if img:
                image_url = img.get("src") or img.get("data-src")
                if image_url and image_url.startswith("//"):
                    image_url = "https:" + image_url
                break

        # Booking URL — first data-item-id attribute anywhere in scope
        item_id = ""
        for n in scope:
            if hasattr(n, "get") and n.get("data-item-id"):
                item_id = n.get("data-item-id")
                break
            if hasattr(n, "find"):
                nested = n.find(attrs={"data-item-id": True})
                if nested:
                    item_id = nested.get("data-item-id")
                    break
        if item_id:
            booking_url = append_utm(f"{WIDGET_BASE}?item_id={item_id}")
        else:
            booking_url = append_utm(WIDGET_BASE)

        items.append({
            "title":       title,
            "price":       price,
            "duration":    duration,
            "description": desc,
            "image_url":   image_url,
            "booking_url": booking_url,
            "category":    category_name,
        })

    return items


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info(f"🏞 {PROVIDER['name']} — Checkfront widget scraper (Playwright)")

    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        log.warning(f"Places update failed: {e}")

    loc_mappings = load_location_mappings()
    log.info(f"Loaded {len(loc_mappings)} location mappings")

    global _CONTROLS
    _CONTROLS = load_activity_controls(PROVIDER["id"])
    log.info(f"Loaded {len(_CONTROLS)} activity controls")

    scraped_at = datetime.datetime.utcnow().isoformat()
    raw_items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        log.info("Playwright browser launched")
        for cat_id, cat_name in KEEP_CATEGORIES:
            raw_items.extend(scrape_category(browser, cat_id, cat_name))
            time.sleep(0.5)
        browser.close()
        log.info("Playwright browser closed")

    # Dedup by title (same item may appear in multiple categories). Key on the
    # same normalisation title_hash uses (strip + lowercase) so titles that
    # differ only by case can't slip through and collide on the course `id`
    # (stable_id_v2 uses title_hash under the hood → Postgres rejects the
    # upsert with "ON CONFLICT DO UPDATE command cannot affect row a second
    # time").
    seen = {}
    for item in raw_items:
        key = (item["title"] or "").strip().lower()
        if key and key not in seen:
            seen[key] = item
    unique = list(seen.values())
    log.info(f"Built {len(unique)} unique items from {len(raw_items)} raw")

    if not unique:
        log.warning("No items scraped — keeping existing Supabase data")
        return

    # Build rows — flex-date because dates require modal interaction
    rows = []
    for item in unique:
        title = item["title"]
        loc_raw       = resolve_location_raw(title, item.get("description") or "")
        loc_canonical = normalise_location(loc_raw, loc_mappings)
        course_id     = stable_id_v2(PROVIDER["id"], None, title)

        row = {
            "id":              course_id,
            "provider_id":     PROVIDER["id"],
            "title":           title,
            "location_raw":    loc_raw,
            "date_sort":       None,
            "date_display":    "Flexible dates",
            "duration_days":   item.get("duration"),
            "price":           item.get("price"),
            "currency":        "CAD",
            "spots_remaining": None,
            "avail":           "open",
            "active":          True,
            "booking_url":     item.get("booking_url"),
            "summary":         "",
            "search_document": "",
            "image_url":       item.get("image_url"),
            "custom_dates":    True,
            "booking_mode":    "instant",
            "description":     item.get("description") or None,
            "scraped_at":      scraped_at,
        }
        if loc_canonical is not None:
            row["location_canonical"] = loc_canonical
        rows.append(row)

    # Summaries — dedup by title
    if rows:
        by_title = {}
        for r in rows:
            if r.get("description") and r["title"] not in by_title:
                by_title[r["title"]] = {
                    "id":          r["title"],
                    "title":       r["title"],
                    "description": r["description"],
                    "provider":    PROVIDER["name"],
                }
        if by_title:
            try:
                summaries = generate_summaries_batch(
                    list(by_title.values()), provider_id=PROVIDER["id"]
                )
                log.info(f"Generated {len(summaries)} summaries")
                for r in rows:
                    result = summaries.get(r["title"])
                    if result:
                        r["summary"]         = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                log.warning(f"Summary batch failed: {e}")

    # Strip description
    for r in rows:
        r.pop("description", None)

    sb_upsert("courses", rows)
    log.info(f"✅ Upserted {len(rows)} flex-date rows")

    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
