#!/usr/bin/env python3
"""
scraper_utils.py — Shared utilities for all BackcountryFinder standalone scrapers.

Every scraper_{id}.py file imports from here instead of duplicating common logic.
scraper.py (the original monolith) is NOT affected — it keeps its own copies.

Public API:
  Supabase:  sb_get, sb_upsert, sb_insert, sb_patch
  Places:    find_place_id, get_place_details, update_provider_ratings
  Location:  load_location_mappings, normalise_location
  Activity:  load_activity_mappings, load_activity_labels, resolve_activity, build_badge
  Claude:    claude_classify, generate_summaries_batch
  Dates:     parse_date_sort, is_future
  IDs:       stable_id
  Avail:     spots_to_avail
  Email:     send_email, send_scraper_summary
  Two-pass:  fetch_detail_pages
"""

import os
import re
import json
import time
import hashlib
import logging
from datetime import datetime, date
from typing import Optional, Callable

import requests

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scraper_utils")

# ── Environment ──────────────────────────────────────────────────────────────

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_SERVICE_KEY", "")
RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")

NOTIFY_EMAIL = "hello@backcountryfinder.com"
FROM_EMAIL   = "hello@backcountryfinder.com"
PLACES_API_URL = "https://maps.googleapis.com/maps/api/place"
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"

UTM = "utm_source=backcountryfinder&utm_medium=referral"

ACTIVITY_LABELS_DEFAULTS = {
    "skiing":         "Backcountry Skiing",
    "climbing":       "Rock Climbing",
    "mountaineering": "Mountaineering",
    "hiking":         "Hiking",
    "biking":         "Mountain Biking",
    "fishing":        "Fly Fishing",
    "heli":           "Heli Skiing",
    "cat":            "Cat Skiing",
    "huts":           "Alpine Huts",
    "guided":         "Guided Tour",
    "glissading":     "Glissading",
    "rappelling":     "Rappelling",
    "snowshoeing":    "Snowshoeing",
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


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_headers(prefer: str = "resolution=merge-duplicates") -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        prefer,
    }


def sb_get(table: str, params: dict = None) -> list:
    """GET rows from a Supabase table. params is a dict of query-string filters."""
    if params is None:
        params = {}
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        params=params,
    )
    r.raise_for_status()
    return r.json()


def sb_upsert(table: str, rows: list) -> None:
    """POST rows to a Supabase table with merge-duplicates upsert semantics."""
    if not rows:
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        json=rows,
    )
    if not r.ok:
        log.error(f"Supabase upsert error {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    log.info(f"Upserted {len(rows)} rows to {table}")


def sb_insert(table: str, data: dict) -> None:
    """INSERT a single row (no upsert). Silently ignores conflicts."""
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(prefer="return=minimal"),
        json=data,
    )
    if not r.ok:
        log.error(f"Supabase insert error {r.status_code}: {r.text[:300]}")


def sb_patch(table: str, filter_params: str, payload: dict) -> None:
    """PATCH rows matching filter_params (e.g. 'id=eq.abc')."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{filter_params}",
        headers=_sb_headers(),
        json=payload,
    )
    if not r.ok:
        log.error(f"Supabase PATCH error {r.status_code}: {r.text[:200]}")
    r.raise_for_status()


# ── Google Places ────────────────────────────────────────────────────────────

def find_place_id(query: str) -> Optional[str]:
    """Find a Google Place ID by text query. Returns place_id or None."""
    if not GOOGLE_PLACES_API_KEY:
        return None
    try:
        city = re.split(r"[/,]", query)[0].strip()
        r = requests.get(
            f"{PLACES_API_URL}/findplacefromtext/json",
            params={
                "input": city,
                "inputtype": "textquery",
                "fields": "place_id",
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=10,
        )
        candidates = r.json().get("candidates", [])
        return candidates[0]["place_id"] if candidates else None
    except Exception as e:
        log.warning(f"Place ID lookup failed for '{query}': {e}")
        return None


def get_place_details(place_id: str) -> dict:
    """Get rating and review_count from Google Places Details API."""
    if not GOOGLE_PLACES_API_KEY or not place_id:
        return {}
    try:
        r = requests.get(
            f"{PLACES_API_URL}/details/json",
            params={
                "place_id": place_id,
                "fields": "rating,user_ratings_total",
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=10,
        )
        result = r.json().get("result", {})
        return {
            "rating": result.get("rating"),
            "review_count": result.get("user_ratings_total"),
        }
    except Exception as e:
        log.warning(f"Place details failed for {place_id}: {e}")
        return {}


def update_provider_ratings(provider_id: str) -> None:
    """Look up / refresh Google Places rating for a single provider."""
    if not GOOGLE_PLACES_API_KEY:
        log.info("No Google Places API key — skipping ratings")
        return
    providers = sb_get("providers", {
        "select": "id,name,location,google_place_id",
        "id": f"eq.{provider_id}",
    })
    for p in providers:
        pid = p.get("google_place_id")
        if not pid:
            query = f"{p['name']} {re.split(r'[/,]', p.get('location', ''))[0].strip()}"
            pid = find_place_id(query)
            if pid:
                sb_upsert("providers", [{"id": p["id"], "name": p["name"], "google_place_id": pid}])
            time.sleep(0.5)
        if not pid:
            log.warning(f"No Place ID for {p['name']}")
            continue
        details = get_place_details(pid)
        if details.get("rating"):
            sb_upsert("providers", [{
                "id": p["id"], "name": p["name"], "google_place_id": pid,
                "rating": details["rating"],
                "review_count": details.get("review_count"),
            }])
            log.info(f"{p['name']}: ★ {details['rating']} ({details.get('review_count', 0)} reviews)")
        time.sleep(0.5)


# ── Location normalisation ───────────────────────────────────────────────────

def load_location_mappings() -> dict:
    """Load location_mappings table → {raw_lower: canonical}."""
    rows = sb_get("location_mappings", {"select": "location_raw,location_canonical"})
    return {r["location_raw"].lower().strip(): r["location_canonical"] for r in rows}


def normalise_location(raw: str, mappings: dict) -> Optional[str]:
    """
    Resolve a raw location string to a canonical value.
    Tries exact match, then substring match, then Claude, then returns raw as-is.
    """
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


# ── Activity resolution ──────────────────────────────────────────────────────

def load_activity_mappings() -> list:
    """Load activity_mappings table → [(title_contains_lower, activity)]."""
    try:
        rows = sb_get("activity_mappings", {"select": "title_contains,activity"})
        mappings = [(r["title_contains"].lower(), r["activity"]) for r in rows]
        return sorted(mappings, key=lambda x: len(x[0]), reverse=True)
    except Exception as e:
        log.warning(f"Could not load activity mappings: {e}")
        return []


def load_activity_labels() -> dict:
    """Load activity_labels table → {activity: label}."""
    try:
        rows = sb_get("activity_labels", {"select": "activity,label"})
        return {r["activity"]: r["label"] for r in rows}
    except Exception as e:
        log.warning(f"Could not load activity labels: {e}")
        return {}


def detect_activity(title: str, description: str = "") -> str:
    """Keyword-based activity detection fallback."""
    text = (title + " " + description).lower()
    for activity, keywords in ACTIVITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return activity
    return "guided"


def resolve_activity(title: str, description: str, mappings: list) -> str:
    """
    Three-tier activity resolution:
    1. Mapping table match
    2. Claude classification (if ANTHROPIC_API_KEY set)
    3. Keyword fallback
    Returns canonical activity string.
    """
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
    return detect_activity(title, description)


def build_badge(activity: str, duration_days, activity_labels: dict = None) -> str:
    """Build a badge string like 'Mountaineering · 3 days'."""
    labels = activity_labels or ACTIVITY_LABELS_DEFAULTS
    label = labels.get(activity, activity.replace("_", " ").title())
    if duration_days:
        days = int(duration_days)
        return f"{label} · {days} day{'s' if days > 1 else ''}"
    return label


# ── Claude API ───────────────────────────────────────────────────────────────

def claude_classify(prompt: str, max_tokens: int = 256) -> dict:
    """Call Claude Haiku and return parsed JSON. Returns {} on failure."""
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
    """
    Batch-generate 2-sentence summaries via Claude Haiku.
    courses: list of dicts with keys: id, title, description, provider, activity.
    Returns {course_id: summary_text}. Deduplication by title is the caller's job.
    """
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
[{{"id": "provider-activity-2026-05-16", "summary": "Two sentences here."}}]"""

        try:
            result = claude_classify(prompt, max_tokens=1500)
            if isinstance(result, list):
                for item in result:
                    if item.get("id") and item.get("summary"):
                        results[item["id"]] = item["summary"]
                log.info(f"Batch summaries: {len(result)} generated (batch {i // BATCH_SIZE + 1})")
        except Exception as e:
            log.warning(f"Batch summary generation failed: {e}")
        time.sleep(0.5)

    return results


# ── Date helpers ─────────────────────────────────────────────────────────────

def parse_date_sort(date_str: str) -> Optional[str]:
    """Try to extract a YYYY-MM-DD date from various string formats."""
    if not date_str:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str[:10]
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m = re.search(r"(\w+)\s+(\d+).*?(\d{4})", date_str, re.IGNORECASE)
    if m:
        month_str = m.group(1).lower()[:3]
        day = m.group(2).zfill(2)
        year = m.group(3)
        if month_str in months:
            return f"{year}-{months[month_str]}-{day}"
    return None


def is_future(date_sort: Optional[str]) -> bool:
    """Return True if date_sort is today or later (or unparseable)."""
    if not date_sort:
        return True
    try:
        return datetime.strptime(date_sort, "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return True


# ── Stable ID ────────────────────────────────────────────────────────────────

def stable_id(provider_id: str, activity: str, date_sort: Optional[str], title: str) -> str:
    """Generate a stable course ID: {provider}-{activity}-{date} or hash fallback."""
    if date_sort:
        return f"{provider_id}-{activity}-{date_sort}"
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    return f"{provider_id}-{activity}-{h}"


# ── Availability ─────────────────────────────────────────────────────────────

def spots_to_avail(spots: Optional[int]) -> str:
    """Convert spots_remaining to avail string: open/low/critical/sold."""
    if spots is None:
        return "open"
    if spots == 0:
        return "sold"
    if spots <= 2:
        return "critical"
    if spots <= 4:
        return "low"
    return "open"


# ── UTM helper ───────────────────────────────────────────────────────────────

def append_utm(url: str) -> str:
    """Append UTM params to a URL if not already present."""
    if not url:
        return url
    if "utm_source" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{UTM}"


# ── Email ────────────────────────────────────────────────────────────────────

def send_email(subject: str, html: str, to: str = None) -> None:
    """Send an email via Resend API."""
    if not RESEND_API_KEY:
        return
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": f"BackcountryFinder Scraper <{FROM_EMAIL}>",
            "to": [to or NOTIFY_EMAIL],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    if not r.ok:
        log.error(f"Email failed: {r.status_code} {r.text[:200]}")
    else:
        log.info(f"Email sent: {subject}")


def send_scraper_summary(provider_name: str, count: int, ok: bool = True) -> None:
    """Send a simple scraper run summary email. EMAILS OFF."""
    return


# ── Two-pass scraping helper ─────────────────────────────────────────────────

def fetch_detail_pages(
    urls: list,
    parse_fn: Callable[[str, str], list],
    delay: float = 0.5,
    headers: dict = None,
) -> list:
    """
    Two-pass helper: fetch each URL and call parse_fn(url, html) on the response.
    parse_fn should return a list of row dicts for that page.
    Handles errors per-page so one failure doesn't block the rest.
    Returns flat list of all row dicts.

    Parameters:
        urls:     list of URLs to fetch
        parse_fn: callable(url, html_text) -> list[dict]
        delay:    seconds to sleep between requests
        headers:  optional request headers (defaults to a browser UA)
    """
    if headers is None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-CA,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    all_rows = []
    for i, url in enumerate(urls):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            rows = parse_fn(url, r.text)
            all_rows.extend(rows)
            log.info(f"  [{i+1}/{len(urls)}] {url} → {len(rows)} row(s)")
        except Exception as e:
            log.warning(f"  [{i+1}/{len(urls)}] Failed {url}: {e}")
        if i < len(urls) - 1 and delay > 0:
            time.sleep(delay)

    return all_rows
