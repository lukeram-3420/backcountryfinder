#!/usr/bin/env python3
"""
scraper_utils.py — Shared utilities for all BackcountryFinder standalone scrapers.

Every scraper_{id}.py file imports from here instead of duplicating common logic.
scraper.py (the original monolith) is NOT affected — it keeps its own copies.

Public API:
  Supabase:  sb_get, sb_upsert, sb_insert, sb_patch
  Places:    find_place_id, get_place_details, update_provider_ratings
  Location:  load_location_mappings, normalise_location
  Claude:    claude_classify, generate_summaries_batch
  Dates:     parse_date_sort, is_future
  IDs:       stable_id_v2, title_hash
  Avail:     spots_to_avail
  Email:     send_email, send_scraper_summary
  Logging:   log_availability_change, log_price_change
  Two-pass:  fetch_detail_pages
"""

import os
import re
import json
import time
import hashlib
import logging
from collections import Counter
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


# Province / state short-code: two uppercase letters. Canada (BC/AB/ON/…),
# US (CA/NY/WA/…), and most other jurisdictions fit.
_PROVINCE_RE = re.compile(r"^[A-Z]{2}$")

# Module-level cache for the top-N most-used canonicals, computed once per
# scraper process and passed to Haiku as anchors so it reuses existing
# canonicals rather than minting new spelling variants.
_popular_canonicals_cache: Optional[list] = None


def _get_popular_canonicals(mappings: dict, limit: int = 50) -> list:
    """Return the top `limit` most-frequently-used location_canonical values
    from the active courses table, sorted by frequency descending. Cached at
    module level so one query serves every normalise_location() call in a
    scraper process. Falls back to mapping-alias-count frequency if the
    courses read fails or returns nothing.
    """
    global _popular_canonicals_cache
    if _popular_canonicals_cache is not None:
        return _popular_canonicals_cache
    top: list = []
    try:
        rows = sb_get("courses", {
            "select": "location_canonical",
            "location_canonical": "not.is.null",
            "active": "eq.true",
            "limit": "1000",
        })
        counter = Counter(r["location_canonical"] for r in rows if r.get("location_canonical"))
        top = [c for c, _ in counter.most_common(limit)]
        if len(rows) >= 1000:
            log.info("Popular canonicals sampled from first 1000 active rows (limit hit)")
    except Exception as e:
        log.warning(f"Could not compute popular canonicals from courses: {e}")
    if not top:
        # Fallback: how many location_raw aliases point to each canonical
        top = [c for c, _ in Counter(mappings.values()).most_common(limit)]
    _popular_canonicals_cache = top
    return _popular_canonicals_cache


def normalise_location(raw: str, mappings: dict) -> Optional[str]:
    """
    Resolve a raw location string to a canonical "City, Province" value.

    Tiered resolution:
      1. Exact match in the in-memory mappings dict → return canonical.
      2. Substring match against mappings dict → return canonical.
      3. Haiku with structural confidence: prompt for {"city", "province"}.
         - On structural match (city non-empty, no comma in city, province
           matching ^[A-Z]{2}$): compose "City, XX" → upsert to
           location_mappings (LIVE — not pending) → update the in-memory
           dict → return canonical.
         - On failure / malformed / null response: queue to
           pending_location_mappings with no suggestion → return None.
      4. Haiku unavailable (no API key) or API error: queue to
         pending_location_mappings → return None.

    Caller contract — CRITICAL:
      When this returns None, the course's upsert payload MUST OMIT the
      `location_canonical` key entirely. Do NOT write
      `"location_canonical": None`. Explicit null overwrites a
      previously-resolved canonical on re-scrape (Supabase merge-duplicates
      treats present-null as an overwrite). Omitting the key preserves the
      existing DB value. See the location-canonical upsert guard in every
      scraper_{id}.py.
    """
    if not raw:
        return None
    key = raw.lower().strip()
    if key in mappings:
        return mappings[key]
    for known_raw, canonical in mappings.items():
        if known_raw in key or key in known_raw:
            return canonical

    def _queue_pending(suggested: Optional[str] = None) -> None:
        try:
            sb_insert("pending_location_mappings", {
                "location_raw": raw,
                "suggested_canonical": suggested,
                "provider_id": None,
                "course_title": None,
                "reviewed": False,
            })
        except Exception as e:
            log.warning(f"pending_location_mappings insert failed for '{raw}': {e}")

    if not ANTHROPIC_API_KEY:
        _queue_pending()
        log.info(f"Haiku disabled — '{raw}' queued to pending_location_mappings")
        return None

    known = _get_popular_canonicals(mappings)
    result = claude_classify(
        f"""Normalise this location for a backcountry booking aggregator. Canonical format is "City, Province" where Province is a 2-letter uppercase code (CA: BC/AB/ON/QC/etc., US: CA/NY/WA/CO/etc.).

Known canonical locations (most-used first): {", ".join(known) if known else "Canmore, AB; Squamish, BC; Revelstoke, BC; Rogers Pass, BC"}

Raw location: "{raw}"

If you can confidently resolve this to a city + province (reusing an existing canonical whenever possible), respond with:
{{"city": "Canmore", "province": "AB"}}

If you cannot confidently parse both fields, respond with:
{{"city": null, "province": null}}

Respond with JSON only, no other text."""
    )

    city = ""
    province = ""
    if isinstance(result, dict):
        city = (result.get("city") or "").strip()
        province = (result.get("province") or "").strip().upper()

    if city and "," not in city and _PROVINCE_RE.match(province):
        canonical = f"{city}, {province}"
        try:
            sb_upsert("location_mappings", [{
                "location_raw": raw,
                "location_canonical": canonical,
            }])
            mappings[key] = canonical
            log.info(f"Haiku resolved '{raw}' → '{canonical}' (written live)")
            return canonical
        except Exception as e:
            log.warning(f"location_mappings upsert failed for '{raw}': {e} — queuing to pending instead")
            _queue_pending(suggested=canonical)
            return None

    log.info(f"Haiku could not confidently resolve '{raw}' — queued to pending_location_mappings")
    _queue_pending()
    return None


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


def generate_summaries_batch(courses: list, provider_id: str = None) -> dict:
    """
    Batch-generate two-field summaries via Claude Haiku (Phase 1 V2):
      - display_summary: 2 sentences for the course card (user-facing)
      - search_document: keyword-rich text for Algolia (never shown to users)

    courses: list of dicts with keys: id, title, description, provider.
    provider_id: optional — used for course_summaries upsert. If not provided,
      falls back to each course dict's "provider_id" key.

    Returns {course_id: display_summary_text} — backward-compatible with V1 callers.
    Internally upserts both fields to course_summaries table.
    """
    if not ANTHROPIC_API_KEY:
        return {}
    to_summarise = [c for c in courses if c.get("description", "").strip()]
    if not to_summarise:
        return {}

    # Deduplicate by description — same description gets one summary, reused for all IDs
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

    # results stores {course_id: {"display_summary": str, "search_document": str}}
    results = {}
    BATCH_SIZE = 12

    for i in range(0, len(unique_courses), BATCH_SIZE):
        batch = unique_courses[i:i + BATCH_SIZE]
        items = ""
        for c in batch:
            desc = c["description"][:600].strip()
            loc = c.get("location", "")
            items += f"""---
ID: {c["id"]}
Provider: {c["provider"]}
Title: {c["title"]}
Location: {loc}
Description: {desc}
"""
        prompt = f"""Given these backcountry course listings, generate two outputs for each:

1. display_summary: 2 sentences for the course card.
   Do not repeat the title or location (shown separately on card).
   Focus on the experience, what participants learn, who it's for.
   Use plain language, no marketing fluff. Do not use the word "perfect". Write in third person.

2. search_document: Comprehensive keyword text for Algolia search indexing.
   Include: title, location, certification body, skill level,
   terrain type, equipment, synonyms, all relevant search terms.
   Write as space-separated keywords, not sentences. Never shown to users.

{items}

Respond with valid JSON only — an array of objects:
[{{"id": "course-id", "display_summary": "Two sentences here.", "search_document": "keywords here"}}]"""

        batch_num = i // BATCH_SIZE + 1
        for attempt in range(2):
            try:
                result = claude_classify(prompt, max_tokens=2500)
                if isinstance(result, list) and result:
                    for item in result:
                        cid = item.get("id")
                        ds = item.get("display_summary") or item.get("summary", "")
                        sd = item.get("search_document", "")
                        if cid and ds:
                            results[cid] = {"display_summary": ds, "search_document": sd}
                    log.info(f"Batch summaries: {len(result)} generated (batch {batch_num})")
                    break
                raise ValueError(f"unexpected response type: {type(result)}")
            except Exception as e:
                if attempt == 0:
                    log.warning(f"Batch {batch_num} failed ({e}), retrying in 3s...")
                    time.sleep(3)
                else:
                    log.warning(f"Batch {batch_num} retry also failed: {e}")

    # ── Post-processing: detect and fix duplicate summary bleed ──
    summary_to_ids = {}
    for cid, fields in results.items():
        s = fields["display_summary"].strip()
        if s:
            summary_to_ids.setdefault(s, []).append(cid)

    for summary_text, ids in summary_to_ids.items():
        if len(ids) <= 1:
            continue
        titles = set(id_to_course[cid]["title"] for cid in ids if cid in id_to_course)
        if len(titles) <= 1:
            continue

        log.warning(f"Duplicate summary bleed across {len(titles)} titles with different descriptions — regenerating")
        all_summaries = set(f["display_summary"] for f in results.values())
        for cid in ids[1:]:
            c = id_to_course.get(cid)
            if not c:
                continue
            loc = c.get("location", "")
            regen_prompt = (
                f"Given this course, generate two outputs:\n\n"
                f"1. display_summary: 2 sentences for the course card. "
                f"Do not repeat the title or location. Focus on the experience.\n\n"
                f"2. search_document: Keyword text for search indexing. "
                f"Include title, location, skill level, terrain, equipment, synonyms.\n\n"
                f"Title: {c['title']}\nProvider: {c['provider']}\n"
                f"Location: {loc}\nDescription: {c['description'][:400]}\n\n"
                f"Respond with valid JSON only:\n"
                f"{{\"display_summary\": \"...\", \"search_document\": \"...\"}}"
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
                        "max_tokens": 300,
                        "messages": [{"role": "user", "content": regen_prompt}],
                    },
                    timeout=30,
                )
                regen_data = json.loads(r.json()["content"][0]["text"].strip())
                new_summary = regen_data.get("display_summary", "").strip()
                new_summary = re.sub(r"^#+\s*", "", new_summary).strip()
                new_sd = regen_data.get("search_document", "").strip()

                if new_summary in all_summaries:
                    log.warning(f"Regenerated summary for '{c['title']}' is still a duplicate — clearing")
                    results[cid] = {"display_summary": "", "search_document": ""}
                else:
                    results[cid] = {"display_summary": new_summary, "search_document": new_sd}
                    all_summaries.add(new_summary)
                    log.info(f"Regenerated unique summary for '{c['title']}'")
            except Exception as e:
                log.warning(f"Failed to regenerate summary for '{c['title']}': {e}")
                results[cid] = {"display_summary": "", "search_document": ""}

    # Expand results: copy to all IDs that share the same description
    expanded = dict(results)
    for norm_desc, ids in desc_to_ids.items():
        first_id = desc_to_course[norm_desc]["id"]
        if first_id in results and results[first_id].get("display_summary"):
            for cid in ids:
                if cid != first_id:
                    expanded[cid] = results[first_id]

    # ── Upsert to course_summaries table (both fields) ──
    _upsert_course_summaries(expanded, id_to_course, desc_to_ids, desc_to_course, provider_id)

    # Return {course_id: {"summary": str, "search_document": str}}
    return {cid: {"summary": fields["display_summary"], "search_document": fields.get("search_document", "")}
            for cid, fields in expanded.items()}


def _upsert_course_summaries(expanded: dict, id_to_course: dict,
                             desc_to_ids: dict, desc_to_course: dict,
                             fallback_provider_id: str = None) -> None:
    """Upsert display_summary + search_document to course_summaries table.
    Keyed by (provider_id, title). Writes title_hash and description_hash."""
    if not expanded:
        return

    # Build one row per unique (provider_id, title)
    seen = set()
    rows = []
    for cid, fields in expanded.items():
        ds = fields.get("display_summary", "")
        if not ds:
            continue
        # Look up course metadata — try id_to_course first, then any desc group
        c = id_to_course.get(cid)
        if not c:
            # This is an expanded ID — find the source course
            for norm_desc, ids in desc_to_ids.items():
                if cid in ids:
                    c = desc_to_course[norm_desc]
                    break
        if not c:
            continue

        pid = c.get("provider_id", "") or fallback_provider_id or ""
        title = c.get("title", "")
        key = (pid, title)
        if key in seen:
            continue
        seen.add(key)

        desc = c.get("description", "")
        desc_hash = hashlib.md5(desc.strip().encode()).hexdigest() if desc.strip() else None

        rows.append({
            "provider_id": pid,
            "title": title,
            "course_id": cid,
            "summary": ds,
            "search_document": fields.get("search_document", ""),
            "title_hash": title_hash(title),
            "description_hash": desc_hash,
            "approved": False,
            "pending_reason": "generated",
        })

    if not rows:
        return

    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/course_summaries",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=rows,
            timeout=30,
        )
        if r.ok:
            log.info(f"Upserted {len(rows)} rows to course_summaries")
        else:
            log.warning(f"course_summaries upsert failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"course_summaries upsert error: {e}")


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
            "bad_data": price <= 0,
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
