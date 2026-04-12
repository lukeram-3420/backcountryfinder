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

# ── CONFIG ──
SUPABASE_URL          = os.environ["SUPABASE_URL"]
SUPABASE_KEY          = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
NOTIFY_EMAIL          = "hello@backcountryfinder.com"
FROM_EMAIL            = "hello@backcountryfinder.com"
PLACES_API_URL        = "https://maps.googleapis.com/maps/api/place"
CLAUDE_MODEL          = "claude-haiku-4-5-20251001"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── PROVIDER CONFIG ──
PROVIDER = {
    "id":       "yamnuska",
    "name":     "Yamnuska Mountain Adventures",
    "base_url": "https://yamnuska.com",
    "utm":      "utm_source=backcountryfinder&utm_medium=referral",
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
# Each course page has one location; the param key identifies it.
# Expand as new keys are discovered in logs.
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


# ── SUPABASE HELPERS ──

def sb_get(table: str, params: dict = {}) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def sb_upsert(table: str, data: list) -> None:
    if not data:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    r = requests.post(url, headers=headers, json=data)
    if not r.ok:
        log.error(f"Supabase upsert error {r.status_code}: {r.text}")
    else:
        log.info(f"Upserted {len(data)} rows to {table}")


def sb_insert(table: str, data: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    r = requests.post(url, headers=headers, json=data)
    if not r.ok:
        log.error(f"Supabase insert error {r.status_code}: {r.text}")


# ── LOCATION & ACTIVITY HELPERS ──

def load_location_mappings() -> dict:
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def load_activity_mappings() -> list:
    try:
        rows = sb_get("activity_mappings", {"select": "title_contains,activity"})
        mappings = [(r["title_contains"].lower(), r["activity"]) for r in rows]
        return sorted(mappings, key=lambda x: len(x[0]), reverse=True)
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def load_activity_labels() -> dict:
    try:
        rows = sb_get("activity_labels", {"select": "activity,label"})
        return {r["activity"]: r["label"] for r in rows}
    except Exception as e:
        log.warning(f"Could not load activity labels: {e}")
        return {}


def normalise_location(raw: str, mappings: dict) -> Optional[str]:
    if not raw:
        return None
    key = raw.lower().strip()
    if key in mappings:
        return mappings[key]
    for known_raw, canonical in mappings.items():
        if known_raw in key or key in known_raw:
            return canonical
    if ANTHROPIC_API_KEY:
        known = list(set(mappings.values()))
        result = claude_classify(
            f"""Normalise this location for a backcountry booking aggregator in western Canada.
Known canonical locations: {", ".join(known)}
Raw location: "{raw}"
If it matches a known location, return that exact value. Otherwise suggest a clean canonical name.
Respond with JSON only: {{"location_canonical": "value", "is_new": false}}"""
        )
        if result.get("location_canonical"):
            canonical = result["location_canonical"]
            log.info(f"Claude normalised '{raw}' → '{canonical}'")
            sb_insert("location_mappings", {"location_raw": raw, "location_canonical": canonical})
            mappings[key] = canonical
            return canonical
    return raw


def resolve_activity(title: str, description: str, mappings: list) -> str:
    text = (title + " " + description).lower()
    for pattern, activity in mappings:
        if pattern in text:
            return activity
    if ANTHROPIC_API_KEY:
        known = list(set(a for _, a in mappings))
        result = claude_classify(
            f"""Classify this backcountry course activity type.
Known types: {", ".join(known) if known else "skiing, climbing, mountaineering, hiking, guided"}
Title: "{title}"
Description: "{description}"
Respond with JSON only: {{"activity": "value", "label": "Human Label", "is_new": false}}"""
        )
        if result.get("activity"):
            activity = result["activity"]
            label = result.get("label", activity.replace("_", " ").title())
            sb_upsert("activity_labels", [{"activity": activity, "label": label}])
            sb_insert("activity_mappings", {"title_contains": title.lower()[:100], "activity": activity})
            mappings.append((title.lower()[:100], activity))
            log.info(f"Claude classified '{title}' → '{activity}'")
            return activity
    # Keyword fallback
    keywords = {
        "skiing":         ["ast", "avalanche", "backcountry ski", "splitboard", "freerider", "ski tour"],
        "climbing":       ["climb", "rock", "multi-pitch", "rappel", "trad", "sport lead", "via ferrata"],
        "mountaineering": ["mountaineer", "alpine", "scramble", "glacier", "crevasse", "11,000", "alpinist"],
        "hiking":         ["hik", "backpack", "navigation", "trek", "wapta"],
    }
    for activity, kws in keywords.items():
        if any(kw in text for kw in kws):
            return activity
    return "guided"


def build_badge(activity: str, duration_days, activity_labels: dict) -> str:
    label = activity_labels.get(activity, activity.replace("_", " ").title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


# ── DATE HELPERS ──

def parse_date_sort(text: str) -> Optional[str]:
    if not text:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(r"(\w+)\s+(\d+).*?(\d{4})", text, re.IGNORECASE)
    if m:
        month_str = m.group(1).lower()[:3]
        day = m.group(2).zfill(2)
        year = m.group(3)
        if month_str in months:
            return f"{year}-{months[month_str]}-{day}"
    return None


def is_future(date_sort: Optional[str]) -> bool:
    if not date_sort:
        return True
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}"
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    return f"{provider_id}-{activity}-{h}"


# ── CLAUDE HELPERS ──

def claude_classify(prompt: str, max_tokens: int = 256) -> dict:
    if not ANTHROPIC_API_KEY:
        return {}
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
            timeout=30,
        )
        text = r.json()["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return {}


def generate_summaries_batch(courses: list) -> dict:
    if not ANTHROPIC_API_KEY:
        return {}
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
[{{"id": "yamnuska-mountaineering-2026-05-16", "summary": "Two sentences here."}}]"""
        try:
            result = claude_classify(prompt, max_tokens=1500)
            if isinstance(result, list):
                for item in result:
                    if item.get("id") and item.get("summary"):
                        results[item["id"]] = item["summary"]
                log.info(f"Batch summaries: {len(result)} generated (batch {i//BATCH_SIZE + 1})")
        except Exception as e:
            log.warning(f"Batch summary generation failed: {e}")
        time.sleep(0.5)
    return results


# ── GOOGLE PLACES ──

def update_provider_ratings():
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key — skipping ratings")
        return
    log.info("Updating provider ratings from Google Places...")
    providers = sb_get("providers", {"select": "id,name,location,google_place_id", "id": f"eq.{PROVIDER['id']}"})
    for p in providers:
        pid = p.get("google_place_id")
        if not pid:
            try:
                clean_loc = re.split(r"[/,]", p.get("location", ""))[0].strip()
                r = requests.get(
                    f"{PLACES_API_URL}/findplacefromtext/json",
                    params={"input": f"{p['name']} {clean_loc}", "inputtype": "textquery",
                            "fields": "place_id,name", "key": GOOGLE_PLACES_API_KEY},
                    timeout=10,
                )
                candidates = r.json().get("candidates", [])
                if candidates:
                    pid = candidates[0]["place_id"]
                    log.info(f"Found Place ID for {p['name']}: {pid}")
                    sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid}])
            except Exception as e:
                log.warning(f"Place ID lookup failed: {e}")
            time.sleep(0.5)
        if not pid:
            continue
        try:
            r = requests.get(
                f"{PLACES_API_URL}/details/json",
                params={"place_id": pid, "fields": "rating,user_ratings_total", "key": GOOGLE_PLACES_API_KEY},
                timeout=10,
            )
            result = r.json().get("result", {})
            if result.get("rating"):
                sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid,
                                         "rating": result["rating"],
                                         "review_count": result.get("user_ratings_total")}])
                log.info(f"{p['name']}: ★ {result['rating']} ({result.get('user_ratings_total', 0)} reviews)")
        except Exception as e:
            log.warning(f"Place details failed: {e}")
        time.sleep(0.5)
    log.info("Provider ratings update complete")


# ── EMAIL ──

def send_email(subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        return
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": f"BackcountryFinder Scraper <{FROM_EMAIL}>",
              "to": [NOTIFY_EMAIL], "subject": subject, "html": html},
    )
    if not r.ok:
        log.error(f"Email failed: {r.status_code} {r.text}")
    else:
        log.info(f"Email sent to {NOTIFY_EMAIL}")


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


# ── SESSION ──

def make_session() -> requests.Session:
    """Create a requests session that looks like a real browser."""
    session = requests.Session()
    session.headers.update({
        "User-Agent":                random.choice(USER_AGENTS),
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-CA,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "none",
        "Cache-Control":             "max-age=0",
    })
    try:
        log.info("Seeding session with homepage visit...")
        session.get("https://yamnuska.com/", timeout=15)
        time.sleep(random.uniform(1.5, 3.0))
        session.headers.update({"Sec-Fetch-Site": "same-origin"})
    except Exception as e:
        log.warning(f"Homepage seed failed: {e}")
    return session


# ── SCRAPER ──

def scrape_course_page(session: requests.Session, course_url: str, utm: str) -> list:
    """
    Scrape one Yamnuska course page.

    Structure:
      - Main page has <iframe data-for='tripDates' src='//yamnuska.com/tripDates.php?...'>
      - iframe src params: location GUID (e.g. canmore=GUID), price (e.g. priceCanmore=1598)
      - Fetching the iframe URL returns HTML with <div class="row" data-spaces="N"> radio buttons
    """
    results = []
    try:
        session.headers.update({"User-Agent": random.choice(USER_AGENTS)})
        resp = session.get(course_url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else (
            course_url.rstrip("/").split("/")[-1].replace("-", " ").title()
        )

        # Description — first 2 substantial paragraphs from entry-content
        description = ""
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

        # OG image
        image_url = None
        og = soup.find("meta", property="og:image")
        if og:
            image_url = og.get("content")

        # Find tripDates iframe
        iframe = soup.find("iframe", attrs={"data-for": "tripDates"})
        if not iframe:
            log.info(f"  No tripDates iframe — flexible dates card")
            return [{
                "title":           title,
                "location_raw":    PROVIDER["location"],
                "price":           None,
                "date_display":    "Flexible dates",
                "date_sort":       None,
                "spots_remaining": None,
                "avail":           "open",
                "booking_url":     f"{course_url}?{utm}",
                "image_url":       image_url,
                "description":     description,
                "custom_dates":    True,
            }]

        # Parse iframe src for location key and price
        iframe_src = iframe.get("src", "")
        if iframe_src.startswith("//"):
            iframe_src = "https:" + iframe_src

        parsed = urlparse(iframe_src)
        params = parse_qs(parsed.query)

        # Find which location param has a real GUID (GUIDs are long; skip "1" placeholders)
        location_key  = None
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
                "price":           None,
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

        # Price — param is priceCanmore, priceCalgary, priceRogers etc.
        price = None
        price_key = f"price{location_key.title()}"
        raw_price = params.get(price_key, params.get("priceCanmore", [""]))[0]
        if raw_price:
            try:
                price = int(float(raw_price))
            except (ValueError, TypeError):
                pass
        log.info(f"  Price ({price_key}): ${price}")

        # Fetch the iframe to get date radio buttons
        time.sleep(random.uniform(0.5, 1.5))
        iframe_resp = session.get(iframe_src, timeout=20)
        iframe_resp.raise_for_status()
        iframe_soup = BeautifulSoup(iframe_resp.text, "html.parser")

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

            # Spots remaining → availability (max 12)
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
        log.error(f"  HTTP {e.response.status_code} on {course_url}")
    except Exception as e:
        log.error(f"  Error on {course_url}: {e}")

    return results


def scrape_yamnuska(session: requests.Session) -> list:
    all_courses = []
    scraped_at  = datetime.utcnow().isoformat()
    utm         = PROVIDER["utm"]
    provider_id = PROVIDER["id"]

    log.info(f"=== Scraping {PROVIDER['name']} ({len(PROVIDER['courses'])} pages) ===")

    for i, course_url in enumerate(PROVIDER["courses"]):
        log.info(f"[{i+1}/{len(PROVIDER['courses'])}] {course_url}")
        entries = scrape_course_page(session, course_url, utm)
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
            time.sleep(random.uniform(2, 4))

    log.info(f"Total raw courses scraped: {len(all_courses)}")
    return all_courses


# ── MAIN ──

def main():
    log.info("=== Yamnuska scraper starting ===")

    update_provider_ratings()

    loc_mappings    = load_location_mappings()
    activity_maps   = load_activity_mappings()
    activity_labels = load_activity_labels()
    log.info(f"Loaded {len(loc_mappings)} location mappings, {len(activity_maps)} activity mappings")

    session     = make_session()
    raw_courses = scrape_yamnuska(session)

    if not raw_courses:
        log.warning("No courses scraped — keeping existing Supabase data")
        send_summary(0, ok=False)
        return

    processed = []
    for c in raw_courses:
        loc_raw       = c.get("location_raw") or PROVIDER["location"]
        loc_canonical = normalise_location(loc_raw, loc_mappings) or loc_raw

        activity_canonical = resolve_activity(c["title"], c.get("description", ""), activity_maps)
        badge_canonical    = build_badge(activity_canonical, c.get("duration_days"), activity_labels)
        course_id          = stable_id(PROVIDER["id"], activity_canonical, c.get("date_sort"), c["title"])

        processed.append({
            "id":                 course_id,
            "title":              c["title"],
            "provider_id":        PROVIDER["id"],
            "badge":              badge_canonical,
            "activity":           activity_canonical,
            "activity_raw":       c.get("activity_raw", ""),
            "activity_canonical": activity_canonical,
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
    log.info(f"Total courses upserted: {len(deduped)}")

    send_summary(len(deduped), ok=True)
    log.info("=== Yamnuska scraper complete ===")


if __name__ == "__main__":
    main()
