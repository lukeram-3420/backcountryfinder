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

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER_ID   = "aaa"
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_KEY    = os.environ["RESEND_API_KEY"]
GOOGLE_KEY    = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTIFY_EMAIL  = "luke@backcountryfinder.com"

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates",
}

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

# ── Supabase helpers ──────────────────────────────────────────────────────────
def sb_get(path, params=""):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}?{params}",
        headers=SUPABASE_HEADERS
    )
    r.raise_for_status()
    return r.json()

def sb_patch(path, params, payload):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{path}?{params}",
        headers=SUPABASE_HEADERS,
        json=payload
    )
    if not r.ok:
        print(f"  ⚠ Supabase PATCH error {r.status_code}: {r.text[:200]}")
    r.raise_for_status()

# ── Fetch category pages → {product_url: price} ──────────────────────────────
def scrape_category_pages() -> dict:
    """Returns dict of product_url → price (int)"""
    products = {}
    for url in CATEGORY_PAGES:
        print(f"  Fetching {url}...")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # WooCommerce product cards: <li class="product">
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
                products[product_url] = {"price": price, "title": title}

            time.sleep(0.5)
        except Exception as e:
            print(f"  ⚠ Error fetching {url}: {e}")

    print(f"  Found {len(products)} product URLs across category pages")
    return products

# ── Fetch individual product page → description ───────────────────────────────
def scrape_product_page(url: str) -> str:
    """Returns raw description text from a product page."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # WooCommerce short description
        desc_el = (
            soup.select_one(".woocommerce-product-details__short-description") or
            soup.select_one(".entry-summary .elementor-widget-text-editor") or
            soup.select_one(".elementor-widget-woocommerce-product-short-description") or
            soup.select_one(".product_description") or
            soup.select_one(".woocommerce-Tabs-panel--description p")
        )
        if desc_el:
            return desc_el.get_text(separator=" ", strip=True)[:1000]

        # Fallback: first substantial paragraph in entry-content
        for p in soup.select(".entry-content p, .elementor-text-editor p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text[:1000]

        return ""
    except Exception as e:
        print(f"    ⚠ Error fetching product page {url}: {e}")
        return ""

# ── Haiku summaries ───────────────────────────────────────────────────────────
def generate_summaries(items: list[dict]) -> dict:
    """
    items: list of {title, description}
    Returns {title: summary}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    unique = list({i["title"]: i for i in items}.values())

    prompt = (
        "For each course below, write a single punchy 1-sentence description "
        "(≤18 words) for a backcountry adventure aggregator. "
        "Use the description provided as context. "
        "Return only JSON: {\"title\": \"summary\"}.\n\n" +
        "\n".join(
            f"- {i['title']}: {i['description'][:200]}" for i in unique
        )
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return json.loads(resp.content[0].text.strip())
    except Exception:
        return {}

# ── Google Places — fetch rating + review count ───────────────────────────────
def update_provider_reviews():
    print("  Fetching google_place_id from providers table...")
    rows = sb_get("providers", f"id=eq.{PROVIDER_ID}&select=google_place_id")
    if not rows or not rows[0].get("google_place_id"):
        print("  ⚠ No google_place_id found — skipping reviews")
        return

    place_id = rows[0]["google_place_id"]
    print(f"  Place ID: {place_id}")

    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields":   "rating,user_ratings_total",
            "key":      GOOGLE_KEY
        }
    )
    result = r.json().get("result", {})
    rating       = result.get("rating")
    review_count = result.get("user_ratings_total")

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

# ── Fuzzy title match ─────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    """Lowercase, strip punctuation and common prefixes for matching."""
    s = s.lower()
    # Strip Checkfront category prefix e.g. "Ice Climbing: " or "AST 1: "
    s = re.sub(r"^[^:]+:\s*", "", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def best_match(cf_title: str, wp_titles: list[str]) -> str | None:
    """Find best WordPress title match for a Checkfront title."""
    cf_norm = normalize(cf_title)
    cf_words = set(cf_norm.split())

    best_url  = None
    best_score = 0

    for wp_title in wp_titles:
        wp_norm  = normalize(wp_title)
        wp_words = set(wp_norm.split())
        # Jaccard similarity on words
        if not cf_words or not wp_words:
            continue
        intersection = cf_words & wp_words
        union        = cf_words | wp_words
        score        = len(intersection) / len(union)
        if score > best_score:
            best_score = score
            best_url   = wp_title

    return best_url if best_score > 0.3 else None

# ── Email summary ─────────────────────────────────────────────────────────────
def send_summary(updated: int, no_match: int, no_price: int):
    body = (
        f"<h2>Alpine Air Adventures — details scrape complete</h2>"
        f"<p>Updated <strong>{updated}</strong> course titles · "
        f"no match: <strong>{no_match}</strong> · "
        f"no price: <strong>{no_price}</strong>.</p>"
        f"<p>{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC</p>"
    )
    requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}",
                 "Content-Type": "application/json"},
        json={"from":    "scraper@backcountryfinder.com",
              "to":      NOTIFY_EMAIL,
              "subject": "✅ Scraper — Alpine Air Adventures Details",
              "html":    body}
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🏔 Alpine Air Adventures — details scraper")

    # 1. Update Google reviews
    if GOOGLE_KEY:
        update_provider_reviews()
    else:
        print("  ⚠ No GOOGLE_PLACES_API_KEY — skipping reviews")

    # 2. Scrape category pages → product URLs + prices
    print("\n  Scraping category pages...")
    products = scrape_category_pages()
    # products = {url: {title, price}}

    # 3. Scrape each product page for description
    print(f"\n  Scraping {len(products)} product pages for descriptions...")
    for url, data in products.items():
        print(f"    {data['title']}...")
        data["description"] = scrape_product_page(url)
        time.sleep(0.3)

    # 4. Generate Haiku summaries
    print(f"\n  Generating summaries for {len(products)} courses...")
    items_for_summary = [
        {"title": d["title"], "description": d.get("description", "")}
        for d in products.values()
    ]
    summaries = generate_summaries(items_for_summary)
    print(f"  Got {len(summaries)} summaries")

    # 5. Fetch unique Checkfront titles from Supabase
    print("\n  Fetching existing aaa courses from Supabase...")
    cf_courses = sb_get(
        "courses",
        f"provider_id=eq.{PROVIDER_ID}&select=title&active=eq.true"
    )
    cf_titles = list({c["title"] for c in cf_courses})
    print(f"  Found {len(cf_titles)} unique course titles in Supabase")

    # Build lookup: wp_title → {price, summary, description}
    wp_lookup = {}
    for url, data in products.items():
        wp_title = data["title"]
        wp_lookup[wp_title] = {
            "price":   data.get("price"),
            "summary": summaries.get(wp_title, ""),
        }

    # 6. Match and patch
    print("\n  Matching and patching courses...")
    updated  = 0
    no_match = 0
    no_price = 0

    for cf_title in cf_titles:
        match = best_match(cf_title, list(wp_lookup.keys()))
        if not match:
            print(f"    ⚠ No match: {cf_title}")
            no_match += 1
            continue

        data  = wp_lookup[match]
        price = data["price"]
        summary = data["summary"]

        if price is None:
            no_price += 1

        payload = {"summary": summary}
        if price is not None:
            payload["price"] = price

        sb_patch(
            "courses",
            f"provider_id=eq.{PROVIDER_ID}&title=eq.{requests.utils.quote(cf_title)}",
            payload
        )
        print(f"    ✅ {cf_title} → £{price} | {summary[:50]}...")
        updated += 1
        time.sleep(0.1)

    print(f"\n  Done — updated: {updated} · no match: {no_match} · no price: {no_price}")
    send_summary(updated, no_match, no_price)


if __name__ == "__main__":
    main()
