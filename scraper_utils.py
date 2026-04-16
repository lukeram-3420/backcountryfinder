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
  Logging:   title_hash, log_availability_change, log_price_change
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
            log.info(f"Claude normalised '{raw}' → '{canonical}' (pending admin review)")
            # Queue for admin review — do NOT write directly to location_mappings
            sb_insert("pending_location_mappings", {
                "location_raw": raw,
                "suggested_canonical": canonical,
                "provider_id": None,
                "course_title": None,
                "reviewed": False,
            })
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
            # Queue for admin review — do NOT write directly to activity_mappings
            sb_insert("pending_mappings", {
                "title_contains": title.lower()[:100],
                "suggested_activity": activity,
                "description": description[:500] if description else None,
                "provider_id": None,
                "course_title": title,
                "reviewed": False,
            })
            mappings.append((title.lower()[:100], activity))
            log.info(f"Claude classified '{title}' → '{activity}' (pending admin review)")
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
        if not r.ok:
            log.warning(f"Claude API HTTP {r.status_code}: {r.text[:300]}")
            return {}
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

    # Deduplicate by description — same description gets one summary, reused for all IDs
    # This is semantically correct: the summary represents the description, not the title.
    desc_to_ids = {}       # normalised_desc → [all ids with this description]
    desc_to_course = {}    # normalised_desc → first course (used for batching)
    unique_courses = []
    for c in to_summarise:
        norm_desc = c["description"].strip().lower()
        if norm_desc not in desc_to_course:
            desc_to_course[norm_desc] = c
            desc_to_ids[norm_desc] = [c["id"]]
            unique_courses.append(c)
        else:
            desc_to_ids[norm_desc].append(c["id"])

    # Build id→course lookup for post-processing
    id_to_course = {c["id"]: c for c in unique_courses}

    results = {}
    BATCH_SIZE = 12

    for i in range(0, len(unique_courses), BATCH_SIZE):
        batch = unique_courses[i:i + BATCH_SIZE]
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

For each course, write a 2-sentence summary that MUST begin with the course name. The summary must be specific to that course only. Use plain language, no marketing fluff. Do not use the word "perfect". Write in third person.

{items}

Respond with JSON only — an array of objects with "id" and "summary" keys. Example:
[{{"id": "provider-activity-2026-05-16", "summary": "Two sentences here."}}]"""

        batch_num = i // BATCH_SIZE + 1
        for attempt in range(2):
            try:
                result = claude_classify(prompt, max_tokens=1500)
                if isinstance(result, list) and result:
                    for item in result:
                        if item.get("id") and item.get("summary"):
                            results[item["id"]] = item["summary"]
                    log.info(f"Batch summaries: {len(result)} generated (batch {batch_num})")
                    break
                # Empty or non-list response — treat as failure
                raise ValueError(f"unexpected response type: {type(result)}")
            except Exception as e:
                if attempt == 0:
                    log.warning(f"Batch {batch_num} failed ({e}), retrying in 3s...")
                    time.sleep(3)
                else:
                    log.warning(f"Batch {batch_num} retry also failed: {e}")

    # ── Post-processing: detect and fix duplicate summary bleed ──
    # Bleed = same summary text for courses with DIFFERENT descriptions.
    # Same description → same summary is intentional (handled by dedup above).
    summary_to_ids = {}
    for cid, summary in results.items():
        s = summary.strip()
        if s:
            summary_to_ids.setdefault(s, []).append(cid)

    for summary_text, ids in summary_to_ids.items():
        if len(ids) <= 1:
            continue
        # These are unique-description courses (deduped above) — if they share a summary, it's bleed
        titles = set(id_to_course[cid]["title"] for cid in ids if cid in id_to_course)
        if len(titles) <= 1:
            continue  # same title — not bleed

        # Keep summary for first course, regenerate for the rest
        log.warning(f"Duplicate summary bleed across {len(titles)} titles with different descriptions — regenerating")
        all_summaries = set(results.values())
        for cid in ids[1:]:
            c = id_to_course.get(cid)
            if not c:
                continue
            regen_prompt = (
                f"Write a 2-sentence summary for '{c['title']}'. "
                f"The summary MUST start with '{c['title']}'. Be specific to this course only. "
                f"Description: {c['description'][:400]}. "
                f"Return only the summary text, no JSON."
            )
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
                        "max_tokens": 150,
                        "messages": [{"role": "user", "content": regen_prompt}],
                    },
                    timeout=30,
                )
                new_summary = r.json()["content"][0]["text"].strip()
                new_summary = re.sub(r"^#+\s*", "", new_summary).strip()

                if new_summary in all_summaries:
                    log.warning(f"Regenerated summary for '{c['title']}' is still a duplicate — clearing")
                    results[cid] = ""
                else:
                    results[cid] = new_summary
                    all_summaries.add(new_summary)
                    log.info(f"Regenerated unique summary for '{c['title']}'")
            except Exception as e:
                log.warning(f"Failed to regenerate summary for '{c['title']}': {e}")
                results[cid] = ""

    # Expand results: copy summaries to all IDs that share the same description
    expanded = dict(results)
    for norm_desc, ids in desc_to_ids.items():
        first_id = desc_to_course[norm_desc]["id"]
        if first_id in results and results[first_id]:
            for cid in ids:
                if cid != first_id:
                    expanded[cid] = results[first_id]

    return expanded


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
    """Generate a stable course ID: {provider}-{activity}-{date}-{title_hash} or hash fallback."""
    if date_sort:
        title_hash = hashlib.md5(title.encode()).hexdigest()[:6]
        return f"{provider_id}-{activity}-{date_sort}-{title_hash}"
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


# ── Intelligence logging (V2 — sacred append-only tables) ───────────────────

def title_hash(title: str) -> str:
    """Stable 8-char hash for grouping all dates of the same course title.
    Normalises to stripped lowercase before hashing so 'AST 1 ' and ' ast 1'
    produce the same hash. This is the SINGLE source of truth for title
    hashing — every stable_id_v2, log function, and Algolia objectID must
    call this function. Never compute an inline md5 of titles elsewhere.
    """
    return hashlib.md5((title or "").strip().lower().encode()).hexdigest()[:8]


def stable_id_v2(provider_id: str, date_sort: Optional[str], title: str) -> str:
    """V2 stable course ID: {provider}-{date}-{title_hash_8} or {provider}-flex-{title_hash_8}.
    No activity segment. Platform-agnostic. Three segments, always three.
    """
    th = title_hash(title)
    if date_sort:
        return f"{provider_id}-{date_sort}-{th}"
    return f"{provider_id}-flex-{th}"


def log_availability_change(course: dict) -> None:
    """Append to course_availability_log if spots_remaining or avail differs
    from the last logged value for this (course_id, date_sort).

    Call after sb_upsert("courses", ...) on every scrape run. Only writes
    when values actually change — not on every run.

    course dict must include: id, provider_id, title, date_sort,
    spots_remaining, avail.
    """
    cid = course.get("id")
    pid = course.get("provider_id")
    ds = course.get("date_sort")
    if not cid or not pid or not ds:
        return
    th = title_hash(course.get("title", ""))
    spots = course.get("spots_remaining")
    avail = course.get("avail")

    # Fetch most recent log entry by (provider_id, title_hash, date_sort) —
    # ID-format-agnostic so logs survive the V1→V2 ID migration.
    try:
        prev = sb_get("course_availability_log", {
            "provider_id": f"eq.{pid}",
            "title_hash": f"eq.{th}",
            "date_sort": f"eq.{ds}",
            "select": "spots_remaining,avail",
            "order": "scraped_at.desc",
            "limit": "1",
        })
    except Exception:
        prev = []

    if prev:
        last = prev[0]
        if last.get("spots_remaining") == spots and last.get("avail") == avail:
            return  # no change

    try:
        sb_insert("course_availability_log", {
            "course_id": cid,
            "provider_id": pid,
            "title_hash": th,
            "date_sort": ds,
            "spots_remaining": spots,
            "avail": avail,
            "event_type": "update",
        })
    except Exception as e:
        log.warning(f"avail log failed for {cid}: {e}")


def log_price_change(course: dict) -> None:
    """Append to course_price_log if price differs from the last logged
    value for this (provider_id, title_hash, date_sort).

    Call after sb_upsert("courses", ...) on every scrape run.

    course dict must include: provider_id, title, date_sort, price.
    Optional: currency (defaults to 'CAD').
    """
    pid = course.get("provider_id")
    price = course.get("price")
    if not pid or price is None:
        return
    th = title_hash(course.get("title", ""))
    ds = course.get("date_sort")
    currency = course.get("currency", "CAD")

    # Fetch the most recent price log entry for this title+date
    try:
        params = {
            "provider_id": f"eq.{pid}",
            "title_hash": f"eq.{th}",
            "select": "price",
            "order": "logged_at.desc",
            "limit": "1",
        }
        if ds:
            params["date_sort"] = f"eq.{ds}"
        else:
            params["date_sort"] = "is.null"
        prev = sb_get("course_price_log", params)
    except Exception:
        prev = []

    if prev and prev[0].get("price") == price:
        return  # no change

    try:
        sb_insert("course_price_log", {
            "provider_id": pid,
            "title_hash": th,
            "date_sort": ds,
            "price": price,
            "currency": currency,
        })
    except Exception as e:
        log.warning(f"price log failed for {pid}/{th}: {e}")


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
