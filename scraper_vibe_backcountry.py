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
and hydrates the grid via an in-browser Angular app that issues XHRs to
/availabilities/ endpoints at runtime. No list-availabilities endpoint
exists on the anonymous surface. We confirmed this by DevTools capture +
static-HTML key-sniffing: start_at / capacity_remaining / availability_pk
all have count=0 in the raw HTML body.

So we use Playwright: load the item's lightframe, intercept every
JSON /availabilities/ response the Angular app fetches, advance through
N months by navigating to calendar/{YYYY}/{MM}/ URLs, then dedup and
parse. All availability data comes from the captured XHR responses — we
don't parse the rendered DOM.

Vibe Backcountry is an ACMG-certified Vancouver Island guiding operation
covering backcountry skiing / splitboarding / AST / rock / alpine /
mountaineering / sea kayaking out of Nanaimo, BC.
"""

import re
import datetime
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


# ── FareHarbor availability (Playwright XHR capture) ──────────────────────────
def months_between(start: datetime.date, end: datetime.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def collect_availabilities(browser, item_pk: int, months: list) -> list:
    """
    Open the item's Lightframe calendar in a headless page. Listen to every
    response the Angular app fetches and keep those whose URL contains
    /availabilities/ with a JSON body. Navigate through each month in the
    lookahead range so every month's XHRs fire. Return a deduped list of
    availability dicts.
    """
    captured = {}  # availability_pk → dict

    # ── DIAGNOSTIC v2 (remove after root cause identified) ──
    # v1 confirmed the page makes 350+ requests but ZERO contain "/availabilities".
    # FareHarbor renamed/relocated the endpoint. v2 filters out static-asset noise
    # so the sample_urls reveal the actual API endpoint the Angular app now calls.
    _STATIC_EXTS = (".css", ".js", ".woff", ".woff2", ".ttf", ".otf",
                    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                    ".webp", ".map", ".mp4", ".mp3")
    _STATIC_PATHS = ("/static/", "/fonts/", "/jstranslation/", "/dist/",
                     "/assets/", "/cache/")

    def _is_interesting(url: str) -> bool:
        """Heuristic: drop obvious static assets and third-party tracking,
        keep anything that could plausibly be the new availability endpoint."""
        u = url.split("?", 1)[0].lower()
        if u.endswith(_STATIC_EXTS):
            return False
        if any(p in u for p in _STATIC_PATHS):
            return False
        if any(tld in u for tld in (
            "googletagmanager.com", "google-analytics.com",
            "doubleclick.net", "stripe.com", "translate.google.com",
            "featureassets.org", "cloudfront.net",
        )):
            return False
        return True

    diag = {
        "total_responses":  0,
        "interesting":      0,   # responses passing _is_interesting
        "matched_url":      0,
        "skipped_ctype":    0,
        "skipped_parse":    0,
        "skipped_shape":    0,
        "parsed_rows":      0,
        "networkidle_timeout": 0,
        "sample_urls":      [],  # interesting URLs (up to 30)
        "sample_ctypes":    [],
        "sample_keys":      [],
        "sample_errors":    [],
    }

    def on_response(response):
        diag["total_responses"] += 1
        try:
            url = response.url
        except Exception:
            return
        if _is_interesting(url):
            diag["interesting"] += 1
            if len(diag["sample_urls"]) < 30:
                diag["sample_urls"].append(url[:200])
        if "/availabilities" not in url:
            return
        diag["matched_url"] += 1
        ctype = response.headers.get("content-type", "") if hasattr(response, "headers") else ""
        if len(diag["sample_ctypes"]) < 4:
            diag["sample_ctypes"].append(ctype[:60])
        if "json" not in ctype:
            diag["skipped_ctype"] += 1
            return
        try:
            body = response.json()
        except Exception as e:
            diag["skipped_parse"] += 1
            if len(diag["sample_errors"]) < 4:
                diag["sample_errors"].append(f"parse: {e}")
            return
        if not isinstance(body, dict):
            diag["skipped_shape"] += 1
            if len(diag["sample_errors"]) < 4:
                diag["sample_errors"].append(f"not-dict: {type(body).__name__}")
            return
        if len(diag["sample_keys"]) < 4:
            diag["sample_keys"].append(list(body.keys())[:6])
        # Collect both shapes: {"availability": {...}} and {"availabilities": [...]}.
        one = body.get("availability")
        if isinstance(one, dict):
            pk = one.get("pk")
            if pk is not None:
                captured[pk] = one
                diag["parsed_rows"] += 1
        many = body.get("availabilities")
        if isinstance(many, list):
            for a in many:
                if isinstance(a, dict):
                    pk = a.get("pk")
                    if pk is not None:
                        captured[pk] = a
                        diag["parsed_rows"] += 1

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
                diag["networkidle_timeout"] += 1
            page.wait_for_timeout(500)
    finally:
        try:
            page.close()
        except Exception:
            pass

    # ── DIAGNOSTIC summary line — one per item ──
    print(
        f"    [diag {item_pk}] "
        f"resp={diag['total_responses']} "
        f"interesting={diag['interesting']} "
        f"matched={diag['matched_url']} "
        f"parsed={diag['parsed_rows']} "
        f"unique={len(captured)} "
        f"netidle_timeouts={diag['networkidle_timeout']}/{len(months)} "
        f"skipped(ctype/parse/shape)={diag['skipped_ctype']}/{diag['skipped_parse']}/{diag['skipped_shape']}"
    )
    if diag["matched_url"] == 0 and diag["interesting"] > 0:
        # Print one URL per line so they're readable in CI logs.
        print(f"    [diag {item_pk}] interesting URLs (up to 30):")
        for u in diag["sample_urls"]:
            print(f"      {u}")
    if diag["matched_url"] > 0 and diag["parsed_rows"] == 0:
        print(f"    [diag {item_pk}] sample_ctypes: {diag['sample_ctypes']}")
        print(f"    [diag {item_pk}] sample_keys:   {diag['sample_keys']}")
        print(f"    [diag {item_pk}] sample_errors: {diag['sample_errors']}")
    if diag["total_responses"] == 0:
        print(f"    [diag {item_pk}] NO responses captured at all — page never made any requests")

    return list(captured.values())


# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_iso_date(ts: str) -> datetime.date:
    return datetime.datetime.fromisoformat(ts).date()


def cheapest_price_cad(avail: dict, item: dict) -> "int | None":
    """
    Prefer the availability's own customer_type_rates (live per-date price).
    Fall back to the item's customer_prototypes (catalogue default).
    FareHarbor amounts are in cents — convert to CAD int.
    """
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
            avails = collect_availabilities(browser, pk, months)
            if not avails:
                skipped += 1
                continue
            print(f"           captured {len(avails)} availabilities")

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
                price = cheapest_price_cad(a, item)

                date_sort    = d_start.isoformat()
                date_display = d_start.strftime("%b %-d, %Y")
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

    # 5. Upsert in batches of 50
    for i in range(0, len(rows), 50):
        sb_upsert("courses", rows[i:i + 50])

    print(f"  ✅ Upserted {len(rows)} rows")

    for c in rows:
        log_availability_change(c)
        log_price_change(c)


if __name__ == "__main__":
    main()
