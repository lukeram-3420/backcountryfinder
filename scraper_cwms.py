#!/usr/bin/env python3
"""
scraper_cwms.py — Canada West Mountain School (WooCommerce)
Site:    https://themountainschool.com
Platform: WooCommerce — listing page + individual course pages with date sessions
"""

import os
import re
import json
import time
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import Optional

from scraper_utils import (
    sb_get, sb_upsert, sb_insert,
    normalise_location,
    send_email, send_scraper_summary,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, ANTHROPIC_API_KEY,
    GOOGLE_PLACES_API_KEY, UTM, CLAUDE_MODEL, NOTIFY_EMAIL, FROM_EMAIL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

PROVIDER = {
    "id":       "cwms",
    "name":     "Canada West Mountain School",
    "listing_url": "https://themountainschool.com/programs-and-courses/",
    "base_url": "https://themountainschool.com",
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

CWMS_ACTIVITY_MAP = {
    "hiking":           "hiking",
    "skiing":           "skiing",
    "mountaineering":   "mountaineering",
    "avalanche-safety": "skiing",
    "backcountry":      "skiing",
    "squamish-rock":    "climbing",
    "rock":             "climbing",
    "first-aid":        "guided",
    "alpine":           "mountaineering",
}

ACTIVITY_LABELS = {
    "skiing":         "Backcountry Skiing",
    "climbing":       "Rock Climbing",
    "mountaineering": "Mountaineering",
    "hiking":         "Hiking",
    "biking":         "Mountain Biking",
    "fishing":        "Fly Fishing",
    "hunting":        "Hunting",
    "heli":           "Heli Skiing",
    "cat":            "Cat Skiing",
    "huts":           "Alpine Huts",
    "guided":         "Guided Tour",
    "glissading":     "Glissading",
    "rappelling":     "Rappelling",
    "snowshoeing":    "Snowshoeing",
    "snowshoe":       "Snowshoeing",
    "via_ferrata":    "Via Ferrata",
}

ACTIVITY_KEYWORDS = {
    "skiing":         ["ast", "avalanche", "backcountry ski", "ski touring", "splitboard", "avy", "heli ski", "cat ski"],
    "hiking":         ["hik", "backpack", "navigation", "wilderness travel", "heli-accessed hik", "heli access"],
    "climbing":       ["climb", "rock", "multi-pitch", "rappel", "belay", "trad", "sport climb", "via ferrata", "ferrata"],
    "mountaineering": ["glacier", "mountaineer", "alpine", "crampon", "crevasse", "scramble", "summit", "alpine climb"],
    "biking":         ["bike", "biking", "mtb", "mountain bike", "cycling"],
    "fishing":        ["fish", "fly fish", "angl", "cast", "river guide"],
    "heli":           ["heli adventure", "heli tour", "heli experience"],
}


# ── Helper functions (replicated from scraper.py for standalone use) ──────────

def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def load_activity_labels():
    try:
        rows = sb_get("activity_labels", {"select": "activity,label"})
        return {r["activity"]: r["label"] for r in rows}
    except Exception as e:
        log.warning(f"Could not load activity labels: {e}")
        return {}


def load_activity_mappings_table() -> list:
    """Load activity mappings from Supabase — [{title_contains, activity}]."""
    try:
        rows = sb_get("activity_mappings", {"select": "title_contains,activity"})
        mappings = [(r["title_contains"].lower(), r["activity"]) for r in rows]
        return sorted(mappings, key=lambda x: len(x[0]), reverse=True)
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def get_known_activities(activity_maps: list) -> list:
    """Extract unique activity values from the mappings list."""
    return list(set(activity for _, activity in activity_maps))


def get_known_locations(location_maps: dict) -> list:
    """Extract unique canonical location values from the mappings dict."""
    return list(set(location_maps.values()))


def claude_classify(prompt: str, max_tokens: int = 256, return_text: bool = False):
    """Call Claude API. Returns parsed JSON dict by default, or raw text if return_text=True."""
    if not ANTHROPIC_API_KEY:
        return "" if return_text else {}
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30
        )
        text = r.json()["content"][0]["text"].strip()
        if return_text:
            return text
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return "" if return_text else {}


def claude_classify_activity(title: str, description: str, provider: str, known_activities: list) -> dict:
    """Ask Claude to classify the activity type for a course."""
    activities_list = ", ".join(known_activities) if known_activities else "skiing, climbing, mountaineering, hiking, biking, fishing, heli, cat, guided"
    prompt = f"""You are classifying backcountry outdoor experiences for a booking aggregator.

Known activity types: {activities_list}

Course title: "{title}"
Description: "{description}"
Provider: "{provider}"

Classify this course. If it matches a known activity type, use that exact value.
If it is genuinely a new type not in the list, suggest a short lowercase slug (e.g. "via_ferrata", "ice_climbing").

Also provide a short human-readable display label (e.g. "Via Ferrata", "Ice Climbing").

Respond with JSON only, no other text:
{{"activity": "the_canonical_value", "label": "Human Readable Label", "is_new": false, "confidence": "high", "reasoning": "one line explanation"}}"""

    return claude_classify(prompt)


def claude_classify_location(location_raw: str, known_locations: list) -> dict:
    """Ask Claude to normalise a raw location string to a canonical value."""
    locations_list = ", ".join(known_locations) if known_locations else "Whistler / Blackcomb, Squamish, North Shore / Seymour, Pemberton / Duffey, Garibaldi Park, Tantalus Range"
    prompt = f"""You are normalising location strings for a backcountry booking aggregator in British Columbia, Canada.

Known canonical locations: {locations_list}

Raw location string: "{location_raw}"

If this matches one of the known canonical locations, return that exact canonical value.
If it is a genuinely new location not in the list, suggest a clean canonical name.

Respond with JSON only, no other text:
{{"location_canonical": "the_canonical_value", "is_new": false, "confidence": "high", "reasoning": "one line explanation"}}"""

    return claude_classify(prompt)


def detect_activity(title: str, description: str = "") -> str:
    text = (title + " " + description).lower()
    for activity, keywords in ACTIVITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return activity
    return "guided"  # default


def resolve_activity(title, description, mappings, provider=""):
    """
    Resolve activity using mappings table first, then Claude, then keyword detection.
    Returns (activity, is_new, should_add_mapping)
    """
    text = (title + " " + description).lower()
    for pattern, activity in mappings:
        if pattern.lower() in text:
            return activity, False, False
    if ANTHROPIC_API_KEY:
        known = get_known_activities(mappings) if mappings else []
        result = claude_classify_activity(title, description, provider, known)
        if isinstance(result, list) and result:
            result = result[0]
        if isinstance(result, dict) and result.get("activity"):
            activity = result["activity"]
            is_new = result.get("is_new", False)
            label = result.get("label", activity.replace("_", " ").title())
            log.info(f"Claude classified '{title}' as '{activity}' (new={is_new}): {result.get('reasoning','')}")
            sb_upsert("activity_labels", [{"activity": activity, "label": label}])
            return activity, is_new, True
    return detect_activity(title, description), False, False


def build_badge(activity: str, duration_days) -> str:
    """Build a clean badge string from canonical activity and duration."""
    label = ACTIVITY_LABELS.get(activity, activity.title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}"
    # Fallback: hash of title
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    return f"{provider_id}-{activity}-{h}"


def is_future(date_sort: Optional[str]) -> bool:
    if not date_sort:
        return True  # keep if we can't parse
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


def generate_summaries_batch(courses: list) -> dict:
    """
    Batch generate 2-sentence summaries for a list of courses.
    courses: list of dicts with keys: id, title, description, provider, activity
    Returns dict: {course_id: summary_text}
    """
    if not ANTHROPIC_API_KEY:
        return {}

    # Filter to courses that have descriptions
    to_summarise = [c for c in courses if c.get("description", "").strip()]
    if not to_summarise:
        return {}

    results = {}
    BATCH_SIZE = 12

    for i in range(0, len(to_summarise), BATCH_SIZE):
        batch = to_summarise[i:i + BATCH_SIZE]
        items = ""
        for c in batch:
            desc = c["description"][:600].strip()
            items += f"""---
ID: {c["id"]}
Provider: {c["provider"]}
Activity: {c["activity"]}
Title: {c["title"]}
Description: {desc}
"""

        prompt = f"""You are writing 2-sentence summaries for backcountry experience listings on a booking aggregator.

For each course below, write exactly 2 sentences. Be specific and enticing. Use plain language, no marketing fluff. Do not start with the provider name or course title. Do not use the word "perfect". Write in third person.

{items}

Respond with JSON only — an array of objects with "id" and "summary" keys. Example:
[{{"id": "cwms-hiking-abc123", "summary": "Two sentences here."}}]"""

        try:
            result = claude_classify(prompt, max_tokens=1500)
            if isinstance(result, list):
                for item in result:
                    if item.get("id") and item.get("summary"):
                        results[item["id"]] = item["summary"]
                log.info(f"Batch summaries: generated {len(result)} summaries (batch {i//BATCH_SIZE + 1})")
            else:
                log.warning(f"Unexpected summary batch response format")
        except Exception as e:
            log.warning(f"Batch summary generation failed: {e}")

        time.sleep(0.5)

    return results


# ── Scraping functions ────────────────────────────────────────────────────────

def scrape_cwms(provider):
    """Scrape Canada West Mountain School WooCommerce listing page."""
    log.info(f"Scraping {provider['name']} -- {provider['listing_url']}")
    courses = []
    try:
        r = requests.get(provider["listing_url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("div.mix")
        if not items:
            log.warning(f"No div.mix found for {provider['name']}")
            return []
        log.info(f"Found {len(items)} items for {provider['name']}")
        for item in items:
            try:
                style = item.get("style", "")
                if "display: none" in style or "display:none" in style:
                    continue
                title_el = item.select_one("h3.product-archive-heading")
                title = title_el.get_text(strip=True) if title_el else None
                if not title:
                    continue
                link_el = item.select_one("a[href]")
                booking_url = None
                if link_el:
                    href = link_el.get("href", "")
                    if href.startswith("http"):
                        booking_url = f"{href}{'&' if '?' in href else '?'}{provider['utm']}"
                    else:
                        booking_url = f"{provider['base_url']}{href}?{provider['utm']}"
                price = None
                price_el = item.select_one("div.product-price")
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    m = re.search(r"[\d,]+", price_text.replace(",", ""))
                    if m:
                        try:
                            price = int(float(m.group().replace(",", "")))
                        except ValueError:
                            pass
                image_url = None
                img_el = item.select_one("div.product-image-wrap img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")
                desc_text = ""
                desc_el = item.select_one("div.product-short-descr")
                if desc_el:
                    desc_text = desc_el.get_text(strip=True)
                classes = item.get("class", [])
                activity = "guided"
                for cls in classes:
                    if cls in CWMS_ACTIVITY_MAP:
                        activity = CWMS_ACTIVITY_MAP[cls]
                        break
                location_raw = None
                loc_m = re.search(r"(Whistler|Squamish|Seymour|Garibaldi|Pemberton|Tantalus|Vancouver|North Shore|Golden|Revelstoke|Manning|Chilcotin|Spearhead|Brandywine|Joffre)", title + " " + desc_text, re.I)
                if loc_m:
                    location_raw = loc_m.group(0).title()
                duration_days = None
                dur_m = re.search(r"(\d+)[\s-]*day", title + " " + desc_text, re.I)
                if dur_m:
                    duration_days = float(dur_m.group(1))
                courses.append({
                    "title":         title,
                    "provider_id":   provider["id"],
                    "badge":         activity.title(),
                    "activity":      activity,
                    "activity_raw":  activity,
                    "location_raw":  location_raw,
                    "date_display":  None,
                    "date_sort":     None,
                    "duration_days": duration_days,
                    "price":         price,
                    "spots_remaining": None,
                    "avail":         "open",
                    "image_url":     image_url,
                    "booking_url":   booking_url,
                    "summary":       "",
                    "description":   desc_text,
                    "scraped_at":    datetime.utcnow().isoformat(),
                })
            except Exception as e:
                log.warning(f"Error parsing CWMS item: {e}")
                continue
    except Exception as e:
        log.error(f"Failed to scrape {provider['name']}: {e}")
    log.info(f"Scraped {len(courses)} courses from {provider['name']}")
    return courses


def scrape_cwms_course_page(course_url):
    """
    Visit a CWMS course page and extract individual date sessions.
    Parses quantity labels: "Course Name Month Day-Day Year quantity"
    Returns list of dicts: {date_display, date_sort, spots_remaining, avail, product_id}
    """
    sessions = []
    try:
        clean_url = course_url.split("?")[0]
        r = requests.get(clean_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Strategy 1: find all quantity labels — most reliable
        # Label text: "Complete Mountaineering June 1-7 2026 quantity"
        labels = soup.find_all("label", class_="screen-reader-text")
        date_pattern = re.compile(
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([\d]+(?:-[\d]+)?)\s+(20\d{2})",
            re.I
        )

        for label in labels:
            label_text = label.get_text(strip=True)
            m = date_pattern.search(label_text)
            if not m:
                continue

            month = m.group(1)
            days  = m.group(2)
            year  = m.group(3)
            date_display = f"{month} {days}, {year}"

            # Parse date_sort using first day
            first_day = days.split("-")[0]
            date_sort = None
            try:
                from datetime import datetime as dt
                for fmt in ["%B %d %Y", "%b %d %Y"]:
                    try:
                        date_sort = dt.strptime(f"{month} {first_day} {year}", fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

            # Find parent block for stock + product ID
            parent = label.find_parent("div", class_=lambda c: c and "iconic-woo-bundled-product" in c)
            spots = None
            avail = "open"
            product_id = None

            if parent:
                stock_el = parent.find("p", class_="stock")
                if stock_el:
                    stock_text = stock_el.get_text(strip=True).lower()
                    if "out of stock" in stock_text:
                        avail = "sold"
                        spots = 0
                    else:
                        sm = re.search(r"(\d+)", stock_text)
                        if sm:
                            spots = int(sm.group(1))
                            avail = "critical" if spots <= 2 else "low" if spots <= 4 else "open"

                btn = parent.find("button", class_="single_add_to_cart_button")
                if btn:
                    product_id = btn.get("value")

            # Skip past dates
            if date_sort and date_sort < datetime.utcnow().strftime("%Y-%m-%d"):
                continue

            sessions.append({
                "date_display": date_display,
                "date_sort":    date_sort,
                "spots_remaining": spots,
                "avail":        avail,
                "product_id":   product_id,
            })

        if sessions:
            log.info(f"Found {len(sessions)} date sessions at {clean_url}")

        # Also extract description from the course page
        description = ""
        desc_el = (
            soup.find("div", class_="woocommerce-product-details__short-description") or
            soup.find("div", class_="product-short-description") or
            soup.find("div", {"itemprop": "description"}) or
            soup.find("div", class_="entry-content")
        )
        if not desc_el:
            # Fallback: find first substantial paragraph after the product title
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 80 and "skip" not in text.lower():
                    description = text[:800]
                    break
        else:
            description = desc_el.get_text(separator=" ", strip=True)[:800]

    except Exception as e:
        log.warning(f"Could not scrape CWMS course page {course_url}: {e}")
        description = ""

    return sessions, description


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    provider = PROVIDER
    log.info(f"=== CWMS scraper starting ===")

    # Load location mappings
    mappings = load_location_mappings()
    log.info(f"Loaded {len(mappings)} location mappings")

    # Load activity mappings and labels from Supabase
    activity_maps = load_activity_mappings_table()
    log.info(f"Loaded {len(activity_maps)} activity mappings")
    global ACTIVITY_LABELS
    ACTIVITY_LABELS = load_activity_labels()
    log.info(f"Loaded {len(ACTIVITY_LABELS)} activity labels")

    location_flags = []

    raw_courses = scrape_cwms(provider)
    processed = []
    for c in raw_courses:
        if not is_future(c.get("date_sort")):
            continue
        loc_raw = c.get("location_raw") or ""
        if loc_raw:
            loc_canonical = normalise_location(loc_raw, mappings)
            if not loc_canonical:
                location_flags.append({"location_raw": loc_raw, "provider_id": provider["id"], "course_title": c["title"]})
        else:
            loc_canonical = None
        course_id = stable_id(provider["id"], c["activity"], c.get("date_sort"), c["title"])
        activity_raw = c.get("activity_raw") or "guided"
        activity_canonical, act_is_new, act_add_mapping = resolve_activity(c["title"], c.get("location_raw") or "", activity_maps, provider["name"])
        if act_add_mapping:
            sb_insert("activity_mappings", {"title_contains": c["title"].lower()[:100], "activity": activity_canonical})
            activity_maps.append((c["title"].lower()[:100], activity_canonical))
        badge_canonical = build_badge(activity_canonical, c.get("duration_days"))
        booking_url = c.get("booking_url")
        active = True
        custom_dates = True  # CWMS uses WooCommerce date picker
        date_display = "Flexible dates"
        date_sort = None
        processed.append({
            "id": course_id, "title": c["title"], "provider_id": provider["id"],
            "badge": badge_canonical, "activity": activity_canonical,
            "activity_raw": activity_raw, "activity_canonical": activity_canonical,
            "badge_canonical": badge_canonical, "location_raw": loc_raw or None,
            "location_canonical": loc_canonical, "date_display": date_display,
            "date_sort": date_sort, "duration_days": c.get("duration_days"),
            "price": c.get("price"), "spots_remaining": None, "avail": "open",
            "image_url": c.get("image_url"), "booking_url": booking_url,
            "active": active, "custom_dates": custom_dates,
            "summary": "", "description": c.get("description", ""),
            "scraped_at": c["scraped_at"],
        })
    # For each CWMS course, visit the page and create one row per date
    dated_processed = []
    for course in processed:
        booking_url = course.get("booking_url")
        if not booking_url:
            dated_processed.append(course)
            continue

        sessions, page_description = scrape_cwms_course_page(booking_url)
        if page_description:
            course["description"] = page_description
        time.sleep(0.5)

        if not sessions:
            # No dates found — keep as flexible dates card
            dated_processed.append(course)
            continue

        # Sessions found — create one card per date, discard flexible dates card
        # Create one row per date session
        for session in sessions:
            product_id = session.get("product_id")
            # Deep-link to specific date if we have a product ID
            if product_id:
                base_url = booking_url.split("?")[0]
                session_url = f"{base_url}?add-to-cart={product_id}&{provider['utm']}"
            else:
                session_url = booking_url

            # Build stable ID using date_sort
            date_sort = session.get("date_sort")
            # Include product_id as tiebreaker to avoid duplicate stable IDs
            id_key = f"{course['title']} {session.get('product_id', '')}"
            session_id = stable_id(provider["id"], course["activity"], date_sort, id_key)

            session_course = dict(course)
            session_course.update({
                "id":            session_id,
                "date_display":  session.get("date_display"),
                "date_sort":     date_sort,
                "spots_remaining": session.get("spots_remaining"),
                "avail":         session.get("avail", "open"),
                "booking_url":   session_url,
                "custom_dates":  False,
                "active":        session.get("avail") != "sold",
                "description":   course.get("description", ""),
            })
            dated_processed.append(session_course)

    # Batch generate summaries for all CWMS courses
    if dated_processed:
        log.info(f"Generating summaries for {len(dated_processed)} {provider['name']} courses...")
        summary_inputs = [
            {
                "id":          c["id"],
                "title":       c["title"],
                "description": c.get("description", ""),
                "provider":    provider["name"],
                "activity":    c.get("activity_canonical", c.get("activity", "")),
            }
            for c in dated_processed
        ]
        summaries = generate_summaries_batch(summary_inputs)
        for c in dated_processed:
            if c["id"] in summaries:
                c["summary"] = summaries[c["id"]]
        log.info(f"Summaries generated: {len(summaries)}")

    # Deduplicate by ID — last one wins
    if dated_processed:
        seen = {}
        for c in dated_processed:
            seen[c["id"]] = c
        deduped = list(seen.values())
        if len(deduped) < len(dated_processed):
            log.warning(f"Deduplicated {len(dated_processed) - len(deduped)} duplicate course IDs before upsert")

        # Strip description — it's a scrape-time field, not stored in Supabase
        for c in deduped:
            c.pop("description", None)
        sb_upsert("courses", deduped)
        log.info(f"Total courses upserted: {len(deduped)}")
    else:
        log.warning("No courses scraped — keeping existing Supabase data")

    # Flag unmatched locations
    if location_flags:
        for flag in location_flags:
            sb_insert("location_flags", flag)

    # Send summary email
    send_scraper_summary(provider["name"], len(dated_processed))

    log.info("=== CWMS scraper complete ===")


if __name__ == "__main__":
    main()
