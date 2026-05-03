#!/usr/bin/env python3
"""
Scraper: Vibe Backcountry (vibe-backcountry)
Platform: FareHarbor (Lightframe embed on Squarespace store)

FareHarbor exposes an anonymous item catalogue at
  GET /api/v1/companies/vibebackcountry/items/
(works in plain requests — no auth needed).

But availability is NOT listable via public JSON. The widget renders a
server-side HTML shell at
  /embeds/book/vibebackcountry/items/{pk}/calendar/{YYYY}/{MM}/
and hydrates the grid via an in-browser app that issues XHRs to
calendar / availability endpoints at runtime. We use Playwright: load
the item's lightframe, intercept every JSON response on a FareHarbor
availability endpoint, advance through N months by navigating to
calendar/{YYYY}/{MM}/ URLs, then dedup and parse. Availability rows
are recognised structurally (any dict with ``pk`` + ``start_at``) so
the scraper is robust to wrapper-shape drift.

Endpoint history (don't be surprised when this list grows):
  legacy: /api/.../availabilities/ (list endpoint)
  current (2026-05): /api/v1/companies/{shortname}/items/{pk}/calendar/{YYYY}/{MM}/?allow_grouped=yes&...

Vibe Backcountry is an ACMG-certified Vancouver Island guiding operation
covering backcountry skiing / splitboarding / AST / rock / alpine /
mountaineering / sea kayaking out of Nanaimo, BC.
"""

import re
import datetime
from typing import Optional

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from scraper_utils import (
    sb_upsert, stable_id_v2,
    log_availability_change, log_price_change,
    update_provider_ratings,
    load_location_mappings, normalise_location,
    generate_summaries_batch,
    spots_to_avail, append_utm,
    title_hash,
    activity_key, upsert_activity_control, load_activity_controls,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER = {
    "id":       "vibe-backcountry",
    "name":     "Vibe Backcountry",
    "website":  "https://www.vibebackcountry.com",
    "location": "Nanaimo, BC",
}

FH_SHORTNAME = "vibebackcountry"
FH_BASE      = f"https://fareharbor.com/api/v1/companies/{FH_SHORTNAME}"
BOOKING_URL  = f"https://fareharbor.com/embeds/book/{FH_SHORTNAME}/items"

LOOKAHEAD_DAYS = 180

FH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.vibebackcountry.com/",
    "Origin":     "https://www.vibebackcountry.com",
}

# Per-title exclusions live in activity_controls now — hide a title by
# flipping visible=false in the admin Activity Tracking tab. Seeded from
# the historical list via seed_activity_controls.py.
_CONTROLS: dict = {}

# One-shot self-debug: log the first non-empty price_preview body shape
# we fail to parse, so future FareHarbor pricing-shape drift is visible
# without another diagnostic-only PR cycle.
_PRICE_SHAPE_LOGGED: dict = {"value": False}


def _is_visible(provider_id: str, title: str) -> bool:
    """Activity Tracking gate. Upserts the (provider, title) pair so the
    admin sees it, then returns False if the admin has flipped visible=false."""
    key = activity_key("title", None, title)
    upsert_activity_control(
        provider_id, key, title,
        title_hash_=title_hash(title), platform="fareharbor",
    )
    ctrl = _CONTROLS.get(key)
    return not (ctrl and ctrl.get("visible") is False)

LOCATION_MAP = [
    ("colonel foster",    "Strathcona Park, BC"),
    ("strathcona",        "Strathcona Park, BC"),
    ("elkhorn",           "Strathcona Park, BC"),
    ("vancouver island",  "Nanaimo, BC"),
    ("nanaimo",           "Nanaimo, BC"),
    ("squamish",          "Squamish, BC"),
    ("pemberton",         "Pemberton, BC"),
    ("whistler",          "Whistler, BC"),
    ("bugaboos",          "Bugaboos, BC"),
    ("sea to sky",        "Squamish, BC"),
]


def resolve_location_raw(title: str) -> str:
    t = (title or "").lower()
    for keyword, loc in LOCATION_MAP:
        if keyword in t:
            return loc
    return PROVIDER["location"]


# ── FareHarbor catalogue (plain HTTP) ─────────────────────────────────────────
def fh_get(path, params=None):
    r = requests.get(
        f"{FH_BASE}/{path}",
        params=params,
        headers=FH_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def fetch_items() -> list:
    data = fh_get("items/")
    return data.get("items") or []


# Per-item details cache (pk → full item dict). FareHarbor stripped pricing
# from the lightweight /items/ (plural) listing in their 2026 API rework, but
# the per-item /items/{pk}/ endpoint retains customer_prototypes — the catalog
# default rates per customer type. Cached per run so multiple availabilities
# sharing the same item only hit the API once.
_ITEM_DETAILS_CACHE: dict = {}


def fetch_item_details(pk: int) -> dict:
    if pk in _ITEM_DETAILS_CACHE:
        return _ITEM_DETAILS_CACHE[pk]
    try:
        data = fh_get(f"items/{pk}/")
    except Exception as e:
        print(f"  per-item details fetch failed for {pk}: {e}")
        _ITEM_DETAILS_CACHE[pk] = {}
        return {}
    detail = data.get("item") or data
    _ITEM_DETAILS_CACHE[pk] = detail
    return detail


# ── FareHarbor availability (Playwright XHR capture) ──────────────────────────
def months_between(start: datetime.date, end: datetime.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _walk_availabilities(node):
    """Recursively yield every dict inside ``node`` that looks like a
    FareHarbor availability — has both an integer ``pk`` and a string
    ``start_at`` field. Robust to wrapper-shape changes — works for the
    legacy ``{"availabilities": [...]}`` body, the new
    ``/api/v1/companies/.../calendar/`` body whose exact wrapper shape we
    haven't fully mapped, and any future shuffle as long as the inner
    availability dict keeps these two fields.
    """
    if isinstance(node, dict):
        if isinstance(node.get("pk"), int) and isinstance(node.get("start_at"), str):
            yield node
        for v in node.values():
            yield from _walk_availabilities(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_availabilities(item)


def collect_availabilities(browser, item_pk: int, months: list) -> tuple:
    """
    Open the item's Lightframe calendar in a headless page. Listen to every
    response the Angular app fetches and:
      1. Walk JSON bodies for availability dicts (recognised by ``pk`` +
         ``start_at``) → collected into ``captured``.
      2. Capture price-preview XHRs at
         /api/embed/.../price-preview/per-day/v2/ — pricing was stripped from
         the /api/v1/companies/.../items/... namespace in FH's 2026 rework
         and now lives only on the embed-side price-preview endpoint.

    Navigate through each month in the lookahead range so every month's XHRs
    fire. Return ``(availability_list, price_preview_body)`` where
    ``price_preview_body`` may be None if the page never issued a price-preview
    XHR (off-season items with no bookable dates).

    URL filter accepts both the legacy ``/availabilities`` endpoint and the
    new ``/api/v1/companies/.../calendar/{YYYY}/{MM}/`` endpoint that the
    embed widget switched to in 2026 (confirmed via diagnostic captures in
    PRs #50/#51). The ``/embeds/book/.../calendar/`` page-shell URL is
    explicitly excluded so we don't try to JSON-parse the HTML response.
    """
    captured = {}  # availability_pk → dict
    price_preview = {"body": None}  # latest non-empty price-preview body
    matched_url_count = 0
    parse_failures = []     # (url, error_str) — only when matched but failed
    sample_keys = []        # body top-level keys when matched but no rows extracted

    def _matches_avail_endpoint(url: str) -> bool:
        if "/embeds/" in url:
            return False
        if "/availabilities" in url:
            return True
        return "/api/" in url and "/calendar/" in url

    def on_response(response):
        nonlocal matched_url_count
        try:
            url = response.url
        except Exception:
            return
        # Capture price-preview body. The page may issue this XHR multiple
        # times; prefer the most recent non-empty body (one with prices).
        if "/price-preview/" in url:
            try:
                pp_body = response.json()
            except Exception:
                pp_body = None
            if isinstance(pp_body, dict):
                prices = pp_body.get("prices")
                if isinstance(prices, list) and prices:
                    price_preview["body"] = pp_body
                elif price_preview["body"] is None:
                    # Empty-prices fallback — still useful for `details.currency`
                    # if the item simply has no bookable dates.
                    price_preview["body"] = pp_body
        if not _matches_avail_endpoint(url):
            return
        matched_url_count += 1
        ctype = response.headers.get("content-type", "") if hasattr(response, "headers") else ""
        if "json" not in ctype:
            return
        try:
            body = response.json()
        except Exception as e:
            if len(parse_failures) < 3:
                parse_failures.append((url[:120], f"json parse: {e}"))
            return
        rows_before = len(captured)
        for a in _walk_availabilities(body):
            pk = a.get("pk")
            if pk is not None:
                captured[pk] = a
        rows_added = len(captured) - rows_before
        # If we matched a URL but extracted nothing, log the top-level keys
        # so a future drift in the wrapper shape is visible.
        if rows_added == 0 and isinstance(body, dict) and len(sample_keys) < 4:
            sample_keys.append((url[:120], list(body.keys())[:8]))

    page = browser.new_page()
    page.on("response", on_response)

    try:
        for y, m in months:
            nav_url = (
                f"https://fareharbor.com/embeds/book/{FH_SHORTNAME}/items/"
                f"{item_pk}/calendar/{y}/{m:02d}/?full-items=yes&flow=no&g4=yes"
            )
            try:
                page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                print(f"  [playwright] goto timeout {item_pk} {y}-{m:02d}")
                continue
            except Exception as e:
                print(f"  [playwright] goto failed {item_pk} {y}-{m:02d}: {e}")
                continue
            # Give the Angular app time to issue its availability XHRs.
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(500)
    finally:
        try:
            page.close()
        except Exception:
            pass

    # One summary line per item — keeps regression visibility without
    # the full diagnostic firehose.
    print(f"    [item {item_pk}] matched={matched_url_count} availabilities={len(captured)}")
    if matched_url_count > 0 and len(captured) == 0:
        # Rare: URLs matched but nothing extracted. Surface body shape so
        # the next FareHarbor migration is debuggable without a re-diag run.
        for url, keys in sample_keys:
            print(f"      [shape] {url} keys={keys}")
        for url, err in parse_failures:
            print(f"      [parse-err] {url}: {err}")

    return list(captured.values()), price_preview["body"]


# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_iso_date(ts: str) -> datetime.date:
    return datetime.datetime.fromisoformat(ts).date()


def _walk_for_amount(node) -> "int | None":
    """Recursively find the smallest positive integer/float amount-like value
    inside ``node``. Looks for keys named ``amount``, ``total``,
    ``total_amount``, ``cents``, ``price``, ``min_price``, ``from_price``.
    Returns the smallest such value found anywhere in the tree, or None.
    Used as a last-ditch shape-flexible extractor in case FareHarbor renames
    fields again.
    """
    AMOUNT_KEYS = ("amount", "total", "total_amount", "cents",
                   "price", "min_price", "from_price")
    best = None
    if isinstance(node, dict):
        for k in AMOUNT_KEYS:
            v = node.get(k)
            if isinstance(v, (int, float)) and v > 0 and not isinstance(v, bool):
                if best is None or v < best:
                    best = v
        for v in node.values():
            sub = _walk_for_amount(v)
            if sub is not None and (best is None or sub < best):
                best = sub
    elif isinstance(node, list):
        for item in node:
            sub = _walk_for_amount(item)
            if sub is not None and (best is None or sub < best):
                best = sub
    return best


def _date_from_price_entry(entry: dict) -> Optional[str]:
    """Pick the most plausible date string from a price-preview entry.
    FareHarbor uses ``date`` or ``day`` historically; future-proofed with
    a small set of alternates."""
    for k in ("date", "day", "start_at", "start_date"):
        v = entry.get(k)
        if isinstance(v, str) and v:
            return v[:10]  # YYYY-MM-DD prefix is enough
    return None


def cheapest_price_cad(price_preview: "dict | None",
                       avail: dict,
                       item: dict,
                       date_sort: Optional[str] = None) -> "int | None":
    """Resolve the cheapest CAD-int price for one availability.

    Resolution order (first non-None wins):
      1. price_preview.prices entry whose date matches ``date_sort`` —
         pull the smallest amount-like value from inside it.
      2. price_preview.prices min across all entries (any date).
      3. Legacy: avail.customer_type_rates min total (kept in case FH ever
         puts pricing back on the availability dict).
      4. Legacy: item.customer_prototypes min total (catalog default).

    FareHarbor amounts are stored in cents — the function divides by 100
    before returning. Returns None if no source produced a positive number.

    Logs the price-preview top-level shape ONCE per run if we matched a
    non-empty price-preview body but failed to extract a price — so a
    future FH shape drift surfaces without another diagnostic-only PR.
    """
    # 1 + 2: walk price_preview.prices
    if isinstance(price_preview, dict):
        prices = price_preview.get("prices")
        if isinstance(prices, list) and prices:
            # Pass 1 — date-matched entry
            if date_sort:
                for entry in prices:
                    if isinstance(entry, dict) and _date_from_price_entry(entry) == date_sort:
                        amt = _walk_for_amount(entry)
                        if amt is not None:
                            # Heuristic: amounts > 1000 are almost certainly cents.
                            # Amounts <= 1000 may already be CAD int (defensive).
                            return int(amt / 100) if amt > 1000 else int(amt)
            # Pass 2 — minimum across all entries
            min_amt = _walk_for_amount(prices)
            if min_amt is not None:
                return int(min_amt / 100) if min_amt > 1000 else int(min_amt)
            # Matched a non-empty prices list but failed to extract — log once.
            if not _PRICE_SHAPE_LOGGED["value"]:
                _PRICE_SHAPE_LOGGED["value"] = True
                first = prices[0] if isinstance(prices[0], dict) else {}
                print(f"  [price-shape] price-preview prices[0] keys={sorted(first.keys())[:12]}")

    # 3 + 4: legacy fallbacks
    for src in (avail.get("customer_type_rates") or [], item.get("customer_prototypes") or []):
        totals = []
        for r in src:
            t = r.get("total")
            if isinstance(t, (int, float)) and t > 0:
                totals.append(int(t))
        if totals:
            return int(min(totals) / 100)
    return None


def strip_html(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s).strip()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🧗 {PROVIDER['name']} — FareHarbor (Playwright XHR capture) scraper")

    try:
        update_provider_ratings(PROVIDER["id"])
    except Exception as e:
        print(f"  Places update failed: {e}")

    loc_mappings = load_location_mappings()
    print(f"  Loaded {len(loc_mappings)} location mappings")

    global _CONTROLS
    _CONTROLS = load_activity_controls(PROVIDER["id"])
    print(f"  Loaded {len(_CONTROLS)} activity controls")

    today      = datetime.date.today()
    end_date   = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    scraped_at = datetime.datetime.utcnow().isoformat()

    # 1. Catalogue (plain HTTP — confirmed working)
    print("  Fetching item catalogue...")
    items = fetch_items()
    print(f"  Found {len(items)} items total")

    course_items = []
    for item in items:
        title = (item.get("name") or "").strip()
        if not title:
            continue
        if not _is_visible(PROVIDER["id"], title):
            print(f"  hidden via activity_controls: {title!r}")
            continue
        if item.get("is_archived") or item.get("is_unlisted"):
            print(f"  skipping archived/unlisted: {title!r}")
            continue
        if item.get("is_bookable_online") is False:
            print(f"  skipping non-bookable item: {title!r}")
            continue
        course_items.append(item)
    print(f"  {len(course_items)} course items after filtering")

    if not course_items:
        print("  No course items — exiting")
        return

    # 2. Availability via Playwright XHR capture
    months = list(months_between(today, end_date))
    print(f"  Collecting availability via Playwright for {len(months)} months × {len(course_items)} items...")

    rows = []
    skipped = 0
    spot_samples = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        print("  [playwright] browser launched")

        for idx, item in enumerate(course_items, 1):
            pk    = item.get("pk")
            title = (item.get("name") or "").strip()

            description = strip_html(item.get("description") or item.get("headline") or "")
            image_url   = item.get("image_cdn_url") or None

            loc_raw       = resolve_location_raw(title)
            loc_canonical = normalise_location(loc_raw, loc_mappings)

            print(f"  [{idx:>2}/{len(course_items)}] item {pk} · {title!r}")
            avails, price_preview = collect_availabilities(browser, pk, months)
            if not avails:
                skipped += 1
                continue
            print(f"           captured {len(avails)} availabilities")

            # FareHarbor stripped pricing from /items/ entirely in their 2026
            # API rework — both the lightweight /items/ listing AND the deeper
            # /items/{pk}/ endpoint return None for customer_prototypes. The
            # only surviving price source is the price-preview XHR captured
            # alongside the availability data. Keep the per-item fetch as a
            # legacy fallback in case FH puts pricing back on items/ later.
            detail_item = fetch_item_details(pk) or item

            for a in avails:
                start_at = a.get("start_at")
                end_at   = a.get("end_at")
                if not start_at:
                    continue
                try:
                    d_start = parse_iso_date(start_at)
                except Exception:
                    continue
                if d_start < today or d_start > end_date:
                    continue

                duration_days = None
                if end_at:
                    try:
                        d_end = parse_iso_date(end_at)
                        delta = (d_end - d_start).days
                        if delta > 0:
                            duration_days = delta + 1
                    except Exception:
                        pass

                capacity_remaining = a.get("capacity_remaining")
                if isinstance(capacity_remaining, int):
                    spot_samples.append(capacity_remaining)
                    spots_remaining = capacity_remaining
                else:
                    spots_remaining = None

                avail = spots_to_avail(spots_remaining)
                date_sort    = d_start.isoformat()
                date_display = d_start.strftime("%b %-d, %Y")
                price = cheapest_price_cad(price_preview, a, detail_item, date_sort)
                course_id    = stable_id_v2(PROVIDER["id"], date_sort, title)
                booking_url  = append_utm(
                    f"{BOOKING_URL}/{pk}/?full-items=yes&flow=no&g4=yes"
                )

                row = {
                    "id":                 course_id,
                    "provider_id":        PROVIDER["id"],
                    "title":              title,
                    "location_raw":       loc_raw,
                    "date_sort":          date_sort,
                    "date_display":       date_display,
                    "duration_days":      duration_days,
                    "price":              price,
                    "currency":           "CAD",
                    "spots_remaining":    spots_remaining,
                    "avail":              avail,
                    "active":             avail != "sold",
                    "booking_url":        booking_url,
                    "summary":            "",
                    "search_document":    "",
                    "image_url":          image_url,
                    "custom_dates":       False,
                    "description":        description or None,
                    "scraped_at":         scraped_at,
                }
                if loc_canonical is not None:
                    row["location_canonical"] = loc_canonical
                rows.append(row)

        browser.close()

    if spot_samples:
        distinct = sorted(set(spot_samples))[:20]
        print(f"  capacity_remaining distribution (first 20 distinct): {distinct}")

    print(f"  Built {len(rows)} course-date rows · {skipped} items skipped (no availability)")

    # 3. Summaries — dedup by title
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
                print(f"  Generated {len(summaries)} summaries")
                for r in rows:
                    result = summaries.get(r["title"])
                    if result:
                        r["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                        r["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""
            except Exception as e:
                print(f"  Summary batch failed: {e}")

    # 4. Strip description (not a courses column)
    for r in rows:
        r.pop("description", None)

    # 5. Dedup by V2 stable id — FareHarbor sometimes exposes multiple
    # availability records per (date, product) (e.g. morning/afternoon
    # slots) with distinct pks. They collapse to the same V2 id
    # ({provider_id}-{date_sort}-{title_hash}) which Postgres rejects in
    # one UPSERT batch with code 21000 ("ON CONFLICT DO UPDATE command
    # cannot affect row a second time"). Same pattern as
    # scraper_altus.py:1054. Keep the first occurrence.
    seen_ids = set()
    deduped_rows = []
    for r in rows:
        rid = r.get("id")
        if rid and rid in seen_ids:
            continue
        if rid:
            seen_ids.add(rid)
        deduped_rows.append(r)
    if len(deduped_rows) < len(rows):
        print(f"  Deduplicated {len(rows) - len(deduped_rows)} duplicate stable IDs")
    rows = deduped_rows

    # 6. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
