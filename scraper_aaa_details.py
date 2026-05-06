#!/usr/bin/env python3
"""
Scraper: Alpine Air Adventures — Details (aaa_details)
Scrapes WordPress product pages for price + description,
generates Haiku summaries, patches courses + updates provider reviews.
Run manually / occasionally — not on a cron.
"""

import os
import re
import json
import time
import datetime
import anthropic
import requests
from bs4 import BeautifulSoup

from scraper_utils import (
    sb_get, sb_patch, sb_upsert,
    find_place_id, get_place_details, send_email,
    generate_summaries_batch,
    log_price_change,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY,
    GOOGLE_PLACES_API_KEY, ANTHROPIC_API_KEY,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER_ID   = "aaa"
NOTIFY_EMAIL  = "luke@backcountryfinder.com"

CATEGORY_PAGES = [
    "https://alpineairadventures.com/backcountry-skiing/",
    "https://alpineairadventures.com/ice-climbing/",
    "https://alpineairadventures.com/avalanche-training/",
    "https://alpineairadventures.com/rock-climbing/",
    "https://alpineairadventures.com/hiking-trekking/",
    "https://alpineairadventures.com/alpine-climbing/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Manual fuzzy match overrides ──────────────────────────────────────────────
MANUAL_MATCHES = {
    "Backcountry Skiing: Ski Touring & Splitboarding (Private)": "Ski Touring & Splitboarding: Private",
    "Backcountry Skiing: Ski Touring & Splitboarding (Spring)":  "Ski Touring & Splitboarding: Spring Rockies",
    "Hiking & Trekking: Plain of 6 Glacier":                    "Plain of Six Glaciers Tea House",
    "Rock Climbing: Half Day Rock Experience":                   "Rock Climbing Adventure: Half-Day",
    "Backcountry Ski: Ski Basecamp - Women's Only":             "Ski Basecamp: Sorcerer Pass, Selkirk Mountains British Columbia",
    "Backcountry Skiing: Ski Basecamp Sorcerer Pass":           "Ski Basecamp: Sorcerer Pass, Selkirk Mountains British Columbia",
    "Alpine Climbing: Private Alpine Climbing Guide":            "Private Alpine, Customize Your Summit Experience",
    "Backcountry Riding: Intro Backcountry Riding":             None,
    "Rock Climbing: Multi Pitch":                               "Multi-Pitch Rock Climbing",
    "Rock Climbing: Rappelling":                                None,
}

MANUAL_OVERRIDES = {
    "Backcountry Riding: Intro Backcountry Riding": {
        "price":   130,
        "summary": "Introduction to backcountry riding in the Canadian Rockies with ACMG guides.",
    },
    "Rock Climbing: Rappelling": {
        "price":   None,
        "summary": "Learn fundamental rappelling techniques on Banff's stunning rock faces.",
    },
}

# ── Fetch category pages → {wp_title: {price, url}} ──────────────────────────
def scrape_category_pages() -> dict:
    products = {}
    for url in CATEGORY_PAGES:
        print(f"  Fetching {url}...")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Derive category from URL path segment
            category = url.rstrip("/").split("/")[-1]  # e.g. "rock-climbing", "backcountry-skiing"

            for card in soup.select("li.product"):
                link_el  = card.select_one("a.woocommerce-loop-product__link, a[href*='/product/']")
                price_el = card.select_one(".price .amount, .woocommerce-Price-amount")
                title_el = card.select_one("h2, .woocommerce-loop-product__title")

                if not link_el:
                    continue

                product_url = link_el["href"].split("?")[0].rstrip("/")
                if "/product/" not in product_url:
                    continue

                price = None
                if price_el:
                    price_text = price_el.get_text(strip=True).replace(",", "").replace("$", "")
                    try:
                        price = int(float(price_text))
                    except ValueError:
                        pass

                title = title_el.get_text(strip=True) if title_el else ""
                if title:
                    products[title] = {"price": price, "url": product_url, "category": category}

            time.sleep(0.5)
        except Exception as e:
            print(f"  ⚠ Error fetching {url}: {e}")

    print(f"  Found {len(products)} products across category pages")
    return products

# ── Fetch individual product page → description ───────────────────────────────
def scrape_product_page(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        desc_el = (
            soup.select_one(".woocommerce-product-details__short-description") or
            soup.select_one(".elementor-widget-woocommerce-product-short-description") or
            soup.select_one(".woocommerce-Tabs-panel--description p")
        )
        if desc_el:
            return desc_el.get_text(separator=" ", strip=True)[:1000]

        for p in soup.select(".entry-content p, .elementor-text-editor p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text[:1000]

        return ""
    except Exception as e:
        print(f"    ⚠ Error: {e}")
        return ""

# ── Haiku summaries ───────────────────────────────────────────────────────────
def generate_summaries(items: list) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        "For each course below, write a single punchy 1-sentence description "
        "(≤18 words) for a backcountry adventure aggregator. "
        "Use the description as context. "
        "Return only raw JSON with no markdown: {\"title\": \"summary\"}.\n\n" +
        "\n".join(
            f"- {i['title']}: {i['description'][:200]}"
            for i in items
        )
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as e:
        print(f"  ⚠ Summary generation failed: {e}")
        return {}

# ── Google Places ─────────────────────────────────────────────────────────────
def update_provider_reviews():
    print("  Fetching google_place_id from providers table...")
    rows = sb_get("providers", {"id": f"eq.{PROVIDER_ID}", "select": "google_place_id"})
    if not rows or not rows[0].get("google_place_id"):
        print("  ⚠ No google_place_id found — skipping reviews")
        return

    place_id = rows[0]["google_place_id"]
    print(f"  Place ID: {place_id}")

    details = get_place_details(place_id)
    rating       = details.get("rating")
    review_count = details.get("review_count")

    if rating is None:
        print("  ⚠ No rating returned from Google Places")
        return

    print(f"  Rating: {rating} ({review_count} reviews)")
    sb_patch(
        "providers",
        f"id=eq.{PROVIDER_ID}",
        {"rating": rating, "review_count": review_count}
    )
    print("  ✅ Provider reviews updated")

# ── Category extraction ───────────────────────────────────────────────────────

# Map Checkfront title prefixes to WordPress category URL slugs
CF_PREFIX_TO_CATEGORY = {
    "rock climbing masters": "rock-climbing",
    "rock climbing progression": "rock-climbing",
    "rock climbing":       "rock-climbing",
    "ice climbing":        "ice-climbing",
    "alpine climbing":     "alpine-climbing",
    "backcountry skiing":  "backcountry-skiing",
    "backcountry ski":     "backcountry-skiing",
    "backcountry riding":  "backcountry-skiing",
    "hiking & trekking":   "hiking-trekking",
    "hiking":              "hiking-trekking",
    "avalanche":           "avalanche-training",
    "mountaineering":      "alpine-climbing",
}


def extract_cf_category(cf_title: str) -> str | None:
    """Extract the category from a Checkfront title prefix (everything before ':')."""
    m = re.match(r"^([^:]+):", cf_title)
    if not m:
        return None
    prefix = m.group(1).strip().lower()
    for key, cat in CF_PREFIX_TO_CATEGORY.items():
        if key in prefix:
            return cat
    return None


# ── Fuzzy title match ─────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    """Normalise title for comparison — keeps the FULL title including category prefix."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def best_match(cf_title: str, products: dict) -> str | None:
    """
    Match a Checkfront title to a WordPress product title.
    Only matches within the same activity category to prevent cross-category confusion.
    products: {wp_title: {price, url, category}}
    """
    if cf_title in MANUAL_MATCHES:
        return MANUAL_MATCHES[cf_title]

    cf_category = extract_cf_category(cf_title)
    cf_norm  = normalize(cf_title)
    cf_words = set(cf_norm.split())
    best_wp  = None
    best_score = 0

    for wp_title, wp_data in products.items():
        # Only match within the same category
        if cf_category and wp_data.get("category") and wp_data["category"] != cf_category:
            continue

        wp_norm  = normalize(wp_title)
        wp_words = set(wp_norm.split())
        if not cf_words or not wp_words:
            continue
        intersection = cf_words & wp_words
        union        = cf_words | wp_words
        score        = len(intersection) / len(union)
        if score > best_score:
            best_score = score
            best_wp    = wp_title

    return best_wp if best_score > 0.3 else None

# ── Email summary ─────────────────────────────────────────────────────────────
def send_summary(updated: int, overridden: int, no_match: int, no_price: int):
    body = (
        f"<h2>Alpine Air Adventures — details scrape complete</h2>"
        f"<p>Updated <strong>{updated}</strong> · "
        f"manual overrides <strong>{overridden}</strong> · "
        f"no match <strong>{no_match}</strong> · "
        f"no price <strong>{no_price}</strong>.</p>"
        f"<p>{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</p>"
    )
    send_email(
        "✅ Scraper — Alpine Air Adventures Details",
        body,
        to=NOTIFY_EMAIL,
    )

# ── Price-log helper ──────────────────────────────────────────────────────────
def _log_price_after_patch(title: str) -> None:
    """Append a course_price_log row for every session of (PROVIDER_ID, title)
    after a price-touching sb_patch. Required because scraper_aaa.py's listing
    pass writes price=None (Checkfront cal endpoint has no prices), so without
    this aaa never produces a single price-log row despite having 1,156+ priced
    sessions in the courses table.

    log_price_change no-change-skips when the value matches the last logged
    row, so this is idempotent and safe to call on every patch.
    """
    try:
        rows = sb_get("courses", {
            "provider_id": f"eq.{PROVIDER_ID}",
            "title":       f"eq.{title}",
            "select":      "id,provider_id,title,date_sort,price,currency",
        })
    except Exception as e:
        print(f"      ⚠ price-log refetch failed for '{title}': {e}")
        return
    for c in rows:
        if c.get("price") is not None:
            log_price_change(c)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🏔 Alpine Air Adventures — details scraper")

    # 1. Update Google reviews
    if GOOGLE_PLACES_API_KEY:
        update_provider_reviews()
    else:
        print("  ⚠ No GOOGLE_PLACES_API_KEY — skipping reviews")

    # 2. Scrape category pages
    print("\n  Scraping category pages...")
    products = scrape_category_pages()

    # 3. Scrape product pages for descriptions
    print(f"\n  Scraping {len(products)} product pages for descriptions...")
    for wp_title, data in products.items():
        print(f"    {wp_title}...")
        data["description"] = scrape_product_page(data["url"])
        time.sleep(0.3)

    # 4. Generate summaries
    print(f"\n  Generating summaries for {len(products)} courses...")
    items_for_summary = [
        {"title": t, "description": d.get("description", "")}
        for t, d in products.items()
    ]
    summaries = generate_summaries(items_for_summary)
    print(f"  Got {len(summaries)} summaries")

    # 5. Fetch unique Checkfront titles from Supabase
    print("\n  Fetching existing aaa courses from Supabase...")
    cf_courses = sb_get(
        "courses",
        {"provider_id": f"eq.{PROVIDER_ID}", "select": "title", "active": "eq.true"}
    )
    cf_titles = list({c["title"] for c in cf_courses})
    print(f"  Found {len(cf_titles)} unique course titles in Supabase")

    # 6. Match and patch
    print("\n  Matching and patching courses...")
    updated    = 0
    overridden = 0
    no_match   = 0
    no_price   = 0

    for cf_title in cf_titles:
        if cf_title in MANUAL_OVERRIDES:
            override = MANUAL_OVERRIDES[cf_title]
            payload  = {k: v for k, v in override.items() if v is not None}
            if payload:
                sb_patch(
                    "courses",
                    f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(cf_title)}",
                    payload
                )
                if "price" in payload:
                    _log_price_after_patch(cf_title)
            print(f"    ✅ Override: {cf_title} → ${override.get('price')} | {override.get('summary','')[:50]}")
            overridden += 1
            continue

        match = best_match(cf_title, products)

        if match is None and cf_title in MANUAL_MATCHES:
            print(f"    — Skipping (no WP page): {cf_title}")
            continue

        if not match:
            print(f"    ⚠ No match: {cf_title}")
            no_match += 1
            continue

        data    = products[match]
        price   = data.get("price")
        result = summaries.get(match, {})
        summary = result.get("summary", "") if isinstance(result, dict) else result
        search_doc = result.get("search_document", "") if isinstance(result, dict) else ""

        if price is None:
            no_price += 1

        payload = {}
        if summary:
            payload["summary"] = summary
        if search_doc:
            payload["search_document"] = search_doc
        if price is not None:
            payload["price"] = price

        if payload:
            sb_patch(
                "courses",
                f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(cf_title)}",
                payload
            )
            if "price" in payload:
                _log_price_after_patch(cf_title)

        print(f"    ✅ {cf_title} → ${price} | {summary[:50] if summary else 'no summary'}")
        updated += 1
        time.sleep(0.1)

    # 7. Fix specific data issues — re-scrape product pages and regenerate summaries/prices
    print("\n  Fixing specific data issues...")
    DATA_FIXES = [
        # (cf_title, extra_field_patches)
        ("Rock Climbing: Beginner", {}),
        ("Rock Climbing: Private", {}),
        ("Rock Climbing Progression: Intermediate", {}),
        ("Rock Climbing Masters: Advanced", {}),
        ("Ice Climbing: Beginner", {}),
        ("Hiking & Trekking: Hiking week in the Canadian Rockies with Lake O'Hara", {}),
    ]
    for fix_title, fix_payload in DATA_FIXES:
        print(f"    Fixing: {fix_title}")
        # Apply explicit field fixes (e.g. activity correction)
        if fix_payload:
            sb_patch(
                "courses",
                f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(fix_title)}",
                fix_payload,
            )
            print(f"      Patched fields: {list(fix_payload.keys())}")

        # Find the correct WP product match and re-scrape its page
        match = best_match(fix_title, products)
        if not match:
            # No WP match — leave existing summary alone. The fallback pass
            # at the end of main() will regenerate it if still null.
            print(f"      No WP match — leaving existing summary untouched")
            continue

        wp_data = products[match]
        # Re-scrape the product page for fresh description
        description = scrape_product_page(wp_data["url"])
        patch = {}

        # Regenerate summary from fresh description. Never clear — the
        # fallback pass at end of main() will attempt regeneration for any
        # courses still missing a summary.
        if description:
            items = [{"title": fix_title, "description": description[:200]}]
            result = generate_summaries(items)
            new_summary = result.get(fix_title, "")
            if new_summary:
                patch["summary"] = new_summary
                print(f"      Summary: {new_summary[:60]}")
            else:
                print(f"      Summary generation failed — leaving existing summary untouched")
        else:
            print(f"      No description found — leaving existing summary untouched")

        # Patch price from the WP product page
        if wp_data.get("price") is not None:
            patch["price"] = wp_data["price"]
            print(f"      Price: ${wp_data['price']}")

        if patch:
            sb_patch(
                "courses",
                f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(fix_title)}",
                patch,
            )
            if "price" in patch:
                _log_price_after_patch(fix_title)
        time.sleep(0.3)

    # 8. Fallback: regenerate summaries for any AAA courses still missing one
    print("\n  Fallback summary pass — finding courses with null/empty summary…")
    stubs = sb_get("courses", {
        "provider_id": f"eq.{PROVIDER_ID}",
        "or": "(summary.is.null,summary.eq.)",
        "select": "id,title",
    })
    # Dedup by title — all dates of the same title share one summary
    by_title = {}
    for c in stubs:
        t = (c.get("title") or "").strip()
        if not t or t in by_title:
            continue
        by_title[t] = c
    print(f"    {len(stubs)} courses need summaries, {len(by_title)} unique titles")

    if by_title:
        # Build generate_summaries_batch input. courses has no description
        # column — use the title as the description seed so generation still
        # runs. generate_summaries_batch requires a truthy description.
        inputs = []
        for title, row in by_title.items():
            inputs.append({
                "id":          title,
                "title":       title,
                "description": title,
                "provider":    "Alpine Air Adventures",
            })

        try:
            summaries = generate_summaries_batch(inputs, provider_id=PROVIDER_ID)
            print(f"    Generated {len(summaries)} summaries")
        except Exception as e:
            print(f"    ⚠ generate_summaries_batch failed: {e}")
            summaries = {}

        regen_patched = 0
        for title, result in summaries.items():
            if not result:
                continue
            summary = result.get("summary", "") if isinstance(result, dict) else result
            search_doc = result.get("search_document", "") if isinstance(result, dict) else ""
            if not summary:
                continue
            payload = {"summary": summary}
            if search_doc:
                payload["search_document"] = search_doc
            sb_patch(
                "courses",
                f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(title)}",
                payload,
            )
            regen_patched += 1
        print(f"    ✅ Patched {regen_patched} titles with regenerated summaries")

    print(f"\n  Done — updated: {updated} · overridden: {overridden} · no match: {no_match} · no price: {no_price}")
    # EMAILS OFF
    # send_summary(updated, overridden, no_match, no_price)


if __name__ == "__main__":
    main()
