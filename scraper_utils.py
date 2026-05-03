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
  Activity:  activity_key, upsert_activity_control, bulk_upsert_activity_controls,
             load_activity_controls, load_lookahead_windows
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
    """POST rows to a Supabase table with merge-duplicates upsert semantics.

    PostgREST (PGRST102) rejects bulk payloads whose rows have differing keysets.
    Scrapers intentionally omit optional keys (notably `location_canonical` when
    normalise_location() returns None — see CLAUDE.md "Never pass
    location_canonical: None to a courses upsert"). We preserve that contract by
    grouping rows by keyset and POSTing one request per group.
    """
    if not rows:
        return
    groups: dict = {}
    for row in rows:
        key = tuple(sorted(row.keys()))
        groups.setdefault(key, []).append(row)
    total = 0
    for batch in groups.values():
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_sb_headers(),
            json=batch,
        )
        if not r.ok:
            log.error(f"Supabase upsert error {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        total += len(batch)
    log.info(f"Upserted {total} rows to {table} in {len(groups)} keyset batch(es)")


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


def _load_cached_summaries(provider_ids: list) -> dict:
    """Bulk-fetch existing course_summaries rows for the given providers.

    Returns {(provider_id, title): {summary, search_document, description_hash}}.
    Used by generate_summaries_batch to skip Haiku when an existing summary's
    description_hash still matches the current description.
    """
    if not provider_ids or not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    pid_list = ",".join(sorted({p for p in provider_ids if p}))
    if not pid_list:
        return {}
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/course_summaries",
            headers={
                **_sb_headers(),
                "Range": "0-49999",
                "Range-Unit": "items",
            },
            params={
                "provider_id": f"in.({pid_list})",
                "select": "provider_id,title,summary,search_document,description_hash",
            },
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        log.warning(f"Summary cache lookup failed ({e}) — will regenerate all summaries this run")
        return {}
    cache = {}
    for row in rows:
        pid = row.get("provider_id") or ""
        title = row.get("title") or ""
        if not pid or not title:
            continue
        cache[(pid, title)] = {
            "summary": (row.get("summary") or "").strip(),
            "search_document": row.get("search_document") or "",
            "description_hash": row.get("description_hash") or "",
        }
    return cache


def generate_summaries_batch(courses: list, provider_id: str = None) -> dict:
    """
    Batch-generate two-field summaries via Claude Haiku (Phase 1 V2):
      - display_summary: 2 sentences for the course card (user-facing)
      - search_document: keyword-rich text for Algolia (never shown to users)

    courses: list of dicts with keys: id, title, description, provider.
    provider_id: optional — used for course_summaries upsert. If not provided,
      falls back to each course dict's "provider_id" key.

    Returns {course_id: {"summary": str, "search_document": str}}.
    Internally upserts both fields to course_summaries table.

    Caches via course_summaries.description_hash — courses whose (provider_id, title)
    already has a row with a matching description_hash and a non-empty summary are
    served from cache without calling Haiku.
    """
    if not ANTHROPIC_API_KEY:
        return {}
    to_summarise = [c for c in courses if c.get("description", "").strip()]
    if not to_summarise:
        return {}

    # ── Preflight cache check: skip Haiku for unchanged descriptions ──
    cache_provider_ids = list({
        (c.get("provider_id") or provider_id or "")
        for c in to_summarise
    })
    cache = _load_cached_summaries(cache_provider_ids)

    cached_results = {}        # {course_id: {"display_summary": str, "search_document": str}}
    cached_id_to_title = {}    # {course_id: title} for bleed detection
    needs_haiku = []
    for c in to_summarise:
        pid = c.get("provider_id") or provider_id or ""
        title = c.get("title") or ""
        desc = c.get("description") or ""
        desc_hash = hashlib.md5(desc.strip().encode()).hexdigest() if desc.strip() else None
        cached = cache.get((pid, title))
        if cached and desc_hash and cached["description_hash"] == desc_hash and cached["summary"]:
            cached_results[c["id"]] = {
                "display_summary": cached["summary"],
                "search_document": cached["search_document"],
            }
            cached_id_to_title[c["id"]] = title
        else:
            needs_haiku.append(c)

    log.info(f"Summary cache: {len(cached_results)} hits, {len(needs_haiku)} need Haiku")

    if not needs_haiku:
        return {cid: {"summary": fields["display_summary"],
                      "search_document": fields.get("search_document", "")}
                for cid, fields in cached_results.items()}

    to_summarise = needs_haiku

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
    # Cached entries are added FIRST so they always claim ids[0] in any collision
    # group — the regen loop only touches ids[1:], so cached summaries are
    # preserved and only fresh ones get regenerated against them.
    summary_to_ids = {}
    for cid, fields in cached_results.items():
        s = fields["display_summary"].strip()
        if s:
            summary_to_ids.setdefault(s, []).append(cid)
    for cid, fields in results.items():
        s = fields["display_summary"].strip()
        if s:
            summary_to_ids.setdefault(s, []).append(cid)

    bleed_id_to_title = {cid: c["title"] for cid, c in id_to_course.items()}
    bleed_id_to_title.update(cached_id_to_title)

    for summary_text, ids in summary_to_ids.items():
        if len(ids) <= 1:
            continue
        titles = {bleed_id_to_title[cid] for cid in ids if cid in bleed_id_to_title}
        if len(titles) <= 1:
            continue

        log.warning(f"Duplicate summary bleed across {len(titles)} titles with different descriptions — regenerating")
        all_summaries = set(f["display_summary"] for f in results.values())
        all_summaries.update(f["display_summary"] for f in cached_results.values())
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
    # Only fresh entries are upserted — cached rows are already correct in the table.
    _upsert_course_summaries(expanded, id_to_course, desc_to_ids, desc_to_course, provider_id)

    # Return {course_id: {"summary": str, "search_document": str}} — fresh + cached merged
    merged = {cid: {"summary": fields["display_summary"],
                    "search_document": fields.get("search_document", "")}
              for cid, fields in expanded.items()}
    for cid, fields in cached_results.items():
        merged[cid] = {"summary": fields["display_summary"],
                       "search_document": fields.get("search_document", "")}
    return merged


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
        if not pid or not title:
            log.warning(f"Skipping course_summaries upsert with empty provider_id or title (id={c.get('id', '?')})")
            continue
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
            f"{SUPABASE_URL}/rest/v1/course_summaries?on_conflict=provider_id,title",
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


def detect_checkfront_spot_counts(item_cal: dict) -> bool:
    """Per-item probe of a Checkfront /api/3.0/item/cal response.

    Checkfront's calendar endpoint can return either integer spot counts
    (e.g. 5 = 5 spots remaining) OR a binary availability flag (1 = available,
    0 = not), depending on how the tenant has configured each item. Trusting
    a global probe across all items is wrong: a tenant may have spot tracking
    enabled for some items and disabled for others, so a value of 1 on a
    binary-flag item gets misread as "1 spot left" if any other item in the
    catalog ever returned >=2.

    Pass in a single item's cal dict (one entry per date_key) and this returns
    True only when at least one of THIS item's values is >1 — at which point
    integer interpretation is safe for the whole item. False otherwise: the
    caller should set spots_remaining=None on every date for this item, which
    spots_to_avail() will translate to 'open'.

    Reference bug: pre-fix, AAA's "Rock Climbing: Beginner" reported "1 spot
    left" on every date because the global probe flipped to True from a
    different multi-spot product elsewhere in the catalog.
    """
    for v in (item_cal or {}).values():
        try:
            if int(v) > 1:
                return True
        except (ValueError, TypeError):
            continue
    return False


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
    Optional: currency (defaults to 'CAD'), price_tier (Zaui — which tier
    the displayed price was resolved from, e.g. 'adults' / 'seniors' /
    'inferred_min'; recorded so Phase 5 velocity signals stay
    apples-to-apples across runs).
    """
    pid = course.get("provider_id")
    price = course.get("price")
    if not pid or price is None:
        return
    th = title_hash(course.get("title", ""))
    ds = course.get("date_sort")
    currency = course.get("currency", "CAD")
    price_tier = course.get("price_tier")

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

    payload = {
        "provider_id": pid,
        "title_hash": th,
        "date_sort": ds,
        "price": price,
        "currency": currency,
        "bad_data": price <= 0,
    }
    if price_tier:
        payload["price_tier"] = price_tier
    try:
        sb_insert("course_price_log", payload)
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


# ── URL drift detection (for scrapers with hardcoded URL lists) ──────────────

def detect_url_drift(
    provider_id: str,
    homepage_url: str,
    known_urls,
    url_pattern,
    exclude_pattern=None,
    user_agent: str = None,
) -> int:
    """Probe a provider homepage for program URLs not in the scraper's
    known list. Inserts findings into provider_url_drift (idempotent via
    unique constraint on (provider_id, url)). Returns count of NEW URLs
    detected this run.

    Used by scrapers with hardcoded URL lists (yamnuska, cloud-nine) to
    surface provider-added programs for admin review without auto-adding.
    Auto-discovery scrapers don't need this.

    Args:
        provider_id:     'yamnuska', 'cloud-nine-guides', etc.
        homepage_url:    URL to fetch and scan
        known_urls:      iterable of URLs the scraper already covers
                         (hardcoded list ± URLs collected this run)
        url_pattern:     compiled regex; href must MATCH (re.search)
        exclude_pattern: compiled regex; href that matches this is REJECTED
        user_agent:      override default browser UA
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("BeautifulSoup not installed — skipping URL drift check")
        return 0

    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        r = requests.get(homepage_url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"URL drift probe failed for {provider_id} @ {homepage_url}: {e}")
        return 0

    soup = BeautifulSoup(r.text, "html.parser")
    known_norm = {_normalise_url(u) for u in (known_urls or [])}
    found_new: dict = {}  # normalised url → link text (first seen)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(homepage_url, href)
        if not href.startswith("http"):
            continue
        if not url_pattern.search(href):
            continue
        if exclude_pattern and exclude_pattern.search(href):
            continue
        norm = _normalise_url(href)
        if norm in known_norm or norm in found_new:
            continue
        link_text = (a.get_text(strip=True) or "")[:200]
        found_new[norm] = link_text

    if not found_new:
        log.info(f"URL drift: 0 new URLs at {homepage_url}")
        return 0

    rows = [{
        "provider_id": provider_id,
        "url":         url,
        "link_text":   text or None,
    } for url, text in found_new.items()]

    try:
        # Use POST with on_conflict so existing entries are silently skipped.
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/provider_url_drift?on_conflict=provider_id,url",
            headers={
                "apikey":        SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  "application/json",
                "Prefer":        "resolution=ignore-duplicates,return=minimal",
            },
            json=rows,
            timeout=20,
        )
        if not r.ok:
            log.warning(f"provider_url_drift upsert failed {r.status_code}: {r.text[:200]}")
        else:
            log.info(f"URL drift: {len(rows)} new URL(s) detected at {homepage_url}")
    except Exception as e:
        log.warning(f"provider_url_drift upsert error: {e}")
    return len(rows)


def _normalise_url(url: str) -> str:
    """Normalise a URL for set comparison: strip trailing slash, query, fragment."""
    if not url:
        return ""
    from urllib.parse import urlparse
    p = urlparse(url)
    path = (p.path or "").rstrip("/")
    return f"{p.scheme}://{p.netloc.lower()}{path}"


# ── Rezdy storefront helpers ─────────────────────────────────────────────────

_REZDY_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _fetch_html(url: str, user_agent: str = None) -> Optional[str]:
    """Best-effort HTML fetch with browser-shaped headers. Returns None on failure."""
    headers = dict(_REZDY_BROWSER_HEADERS)
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning(f"Fetch failed @ {url}: {e}")
        return None


def discover_rezdy_catalogs(
    storefront_url: str,
    extra_pages: list = None,
    user_agent: str = None,
) -> list:
    """Fetch a Rezdy storefront homepage (and optional extra pages) and
    return every catalog slug discovered, deduped, in first-seen order.

    Catalog matches are extracted from <a href>, <iframe src>, and the raw
    HTML body — covers data attributes, inline scripts, and iframe-embedded
    catalogs on provider marketing sites. Returns slugs like
    'catalog/315469/luxury-experiences'. Empty list on total fetch failure.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("BeautifulSoup not installed — cannot discover Rezdy catalogs")
        return []

    pages = [storefront_url] + list(extra_pages or [])
    catalog_re = re.compile(r"/catalog/(\d+)/([a-z0-9\-]+)", re.I)
    seen: set = set()
    slugs: list = []

    for page_url in pages:
        html = _fetch_html(page_url, user_agent=user_agent)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        candidates: list = []
        for a in soup.find_all("a", href=True):
            candidates.append(a["href"])
        for f in soup.find_all("iframe", src=True):
            candidates.append(f["src"])
        # Also scan raw HTML for catalog URLs inside scripts / data attrs.
        candidates.append(html)
        for c in candidates:
            for m in catalog_re.finditer(c):
                slug = f"catalog/{m.group(1)}/{m.group(2).lower()}"
                if slug in seen:
                    continue
                seen.add(slug)
                slugs.append(slug)

    log.info(f"Rezdy catalog discovery: {len(slugs)} catalog(s) across {len(pages)} page(s)")
    return slugs


def discover_rezdy_products(
    pages: list,
    rezdy_domain: str,
    user_agent: str = None,
) -> list:
    """Fetch each page and extract direct Rezdy product URLs (not catalogs)
    that match the given rezdy_domain. Returns full URLs like
    'https://msaa.rezdy.com/21189/ski-mountaineering-course', deduped, in
    first-seen order.

    Used to capture orphan Rezdy products that are linked from a provider's
    marketing site but not shelved in any catalog. Excludes /catalog/ paths
    — those are handled by discover_rezdy_catalogs.
    """
    if not pages or not rezdy_domain:
        return []

    # Match host/{numeric_id}/{slug} but NOT /catalog/{id}/{slug}.
    product_re = re.compile(
        rf"https?://{re.escape(rezdy_domain)}/(?!catalog/)(\d+)/([a-z0-9\-]+)",
        re.I,
    )
    seen: set = set()
    urls: list = []

    for page_url in pages:
        html = _fetch_html(page_url, user_agent=user_agent)
        if not html:
            continue
        for m in product_re.finditer(html):
            full_url = f"https://{rezdy_domain}/{m.group(1)}/{m.group(2).lower()}"
            if full_url in seen:
                continue
            seen.add(full_url)
            urls.append(full_url)

    log.info(f"Rezdy product discovery: {len(urls)} product URL(s) across {len(pages)} page(s)")
    return urls


def fetch_rezdy_calendar_products(
    storefront_url: str,
    category_id: int,
    referer: str = None,
    user_agent: str = None,
) -> list:
    """Fetch a Rezdy productsMonthlyCalendar page for a category and
    extract direct product URLs from the embedded session HTML.

    Endpoint: {storefront_url}/productsMonthlyCalendar/{category_id}

    Returns unique full Rezdy product URLs like
    'https://msaa.rezdy.com/21189/ski-mountaineering-course'. Used to
    capture orphan products that aren't shelved in any storefront catalog.

    The endpoint is gated by Rezdy's referer/origin allowlist on some
    tenants — pass the marketing-site `referer` so Rezdy treats the
    request as coming from an authorised embed. Returns [] on any failure.
    """
    storefront = storefront_url.rstrip("/")
    url = f"{storefront}/productsMonthlyCalendar/{category_id}"

    # Rezdy's widget endpoint enforces a referer allowlist on many tenants.
    # Browser-shaped fetch headers + a referer matching the marketing site
    # is the same shape the calendar widget uses.
    headers = dict(_REZDY_BROWSER_HEADERS)
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
        # Origin is the scheme+host of the referer.
        try:
            from urllib.parse import urlparse
            p = urlparse(referer)
            if p.scheme and p.netloc:
                headers["Origin"] = f"{p.scheme}://{p.netloc}"
        except Exception:
            pass

    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning(f"Rezdy calendar fetch failed @ {url}: {e}")
        return []

    # The calendar response embeds session links as JSON-escaped HTML:
    #   <a href='/chooseQuantity?productId={id}&...'>
    #     <strong>{time}</strong> {Title}<\/a>
    # The bare /{id} URL 404's on Rezdy — it requires the slug to render the
    # canonical product page. So we extract (productId, title) pairs from the
    # calendar markup and slugify the title to construct /{id}/{slug} URLs
    # that the existing scrape_rezdy_product_page() can render.
    from urllib.parse import urlparse
    storefront_host = urlparse(storefront).netloc

    # Match productId then advance to the </strong> after the time prefix and
    # capture the visible title. Tolerates JSON-escaped close tags (<\/strong>,
    # <\/a>) the calendar response uses inside JS string literals.
    session_re = re.compile(
        r"productId=(\d+).*?<\\?/strong>\s*([^<\\]+?)\s*(?:<|\\)",
        re.I | re.S,
    )

    def _slugify(s: str) -> str:
        s = s.lower().replace("&", "and")
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"\s+", "-", s.strip())
        return re.sub(r"-+", "-", s)

    seen: set = set()
    urls: list = []
    for m in session_re.finditer(html):
        pid = m.group(1)
        title = m.group(2).strip()
        slug = _slugify(title)
        if not slug:
            continue
        full_url = f"https://{storefront_host}/{pid}/{slug}"
        if full_url in seen:
            continue
        seen.add(full_url)
        urls.append(full_url)

    log.info(f"Rezdy calendar @ category {category_id}: {len(urls)} product URL(s)")
    return urls


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


# ── Activity tracking ────────────────────────────────────────────────────────
# activity_controls is the persistent catalogue of every (provider, activity)
# pair any scraper has ever seen. The admin "Activity Tracking" tab writes
# `visible` (exclude) and `tracking_mode` (Zaui lookahead window) to it.
#
# Scraper contract on every run:
#   1. upsert_activity_control(...) — idempotent, stamps last_seen_at=now().
#      Never touches visible/tracking_mode on existing rows — admin-owned.
#   2. load_activity_controls(provider_id) — one query per run, returns a
#      dict keyed by activity_key so the per-activity loop is O(1).
#   3. if not ctrl['visible']: continue  — before any detail fetch / summary
#      generation / row emit.
#   4. (Zaui only) lookahead_days = windows['extended' if tm=='extended'
#      else 'immediate'] from load_lookahead_windows().

def activity_key(platform: str, upstream_id=None, title: str = "") -> str:
    """Unified prefixed dedup key for activity_controls.

    `zaui:{id}` for Zaui tenants (stable numeric upstream id), falls back
    to `title:{title_hash_8}` for everything else (WP, Squarespace, HTML).
    """
    if upstream_id not in (None, "", 0):
        return f"{platform or 'zaui'}:{upstream_id}"
    return f"title:{title_hash(title)}"


def upsert_activity_control(
    provider_id: str,
    activity_key_: str,
    title: str,
    *,
    upstream_id=None,
    title_hash_: Optional[str] = None,
    platform: Optional[str] = None,
) -> None:
    """Idempotent upsert into activity_controls.

    Writes title/upstream_id/title_hash/platform/last_seen_at every run so
    the admin can see the freshest metadata. Critically, does NOT write
    `visible` or `tracking_mode` — those are admin-owned. PostgREST's
    merge-duplicates upsert preserves existing columns that aren't in the
    payload, which is exactly what we want.
    """
    if not provider_id or not activity_key_ or not title:
        return
    payload = {
        "provider_id":  provider_id,
        "activity_key": activity_key_,
        "title":        title,
        "last_seen_at": datetime.utcnow().isoformat(),
        "updated_at":   datetime.utcnow().isoformat(),
    }
    if upstream_id not in (None, ""):
        payload["upstream_id"] = str(upstream_id)
    if title_hash_ is not None:
        payload["title_hash"] = title_hash_
    if platform:
        payload["platform"] = platform
    # Merge-duplicates upsert on (provider_id, activity_key). First-write sets
    # defaults (visible=true, tracking_mode='immediate'); subsequent writes
    # only refresh the metadata columns above.
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/activity_controls",
            headers={
                "apikey":        SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  "application/json",
                "Prefer":        "resolution=merge-duplicates,return=minimal",
            },
            json=[payload],
            params={"on_conflict": "provider_id,activity_key"},
            timeout=10,
        )
        if not r.ok:
            log.warning(f"activity_controls upsert failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"activity_controls upsert error: {e}")


def bulk_upsert_activity_controls(rows: list) -> None:
    """Batch version of upsert_activity_control. Use when a scraper has
    dozens-to-thousands of activities (Zaui tenants) and N single POSTs
    would add minutes of latency. Each row must have provider_id,
    activity_key, and title at minimum.
    """
    if not rows:
        return
    now = datetime.utcnow().isoformat()
    payload = []
    for r in rows:
        if not r.get("provider_id") or not r.get("activity_key") or not r.get("title"):
            continue
        payload.append({
            "provider_id":  r["provider_id"],
            "activity_key": r["activity_key"],
            "title":        r["title"],
            **({"upstream_id": str(r["upstream_id"])} if r.get("upstream_id") not in (None, "") else {}),
            **({"title_hash":  r["title_hash"]}         if r.get("title_hash")  else {}),
            **({"platform":    r["platform"]}           if r.get("platform")    else {}),
            "last_seen_at": now,
            "updated_at":   now,
        })
    if not payload:
        return
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/activity_controls",
            headers={
                "apikey":        SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  "application/json",
                "Prefer":        "resolution=merge-duplicates,return=minimal",
            },
            json=payload,
            params={"on_conflict": "provider_id,activity_key"},
            timeout=30,
        )
        if not r.ok:
            log.warning(f"bulk activity_controls upsert failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"bulk activity_controls upsert error: {e}")


def load_activity_controls(provider_id: str) -> dict:
    """Fetch every activity_controls row for a provider.

    Returns {activity_key: {'visible': bool, 'tracking_mode': str}}. One
    query per scraper run. Missing key in the dict is treated as
    visible=true, tracking_mode='immediate' (defaults for first-seen rows).
    """
    if not provider_id:
        return {}
    try:
        rows = sb_get("activity_controls", {
            "select":      "activity_key,visible,tracking_mode",
            "provider_id": f"eq.{provider_id}",
            "limit":       "10000",
        })
    except Exception as e:
        log.warning(f"load_activity_controls failed for {provider_id}: {e}")
        return {}
    return {
        r["activity_key"]: {
            "visible":       r.get("visible") is not False,
            "tracking_mode": r.get("tracking_mode") or "immediate",
        }
        for r in rows
    }


def load_lookahead_windows() -> dict:
    """Read scraper_config. Returns {'extended': int, 'immediate': int}.
    Baked-in defaults (180/14) if rows are missing or values unparseable.
    Used by Zaui scrapers to pick per-activity availability-walk length.
    """
    out = {"extended": 180, "immediate": 14}
    try:
        rows = sb_get("scraper_config", {
            "select": "key,value",
            "key":    "in.(extended_lookahead_days,immediate_lookahead_days)",
        })
    except Exception as e:
        log.warning(f"load_lookahead_windows failed: {e}")
        return out
    for r in rows:
        k = r.get("key")
        v = r.get("value")
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n < 1 or n > 730:
            continue
        if k == "extended_lookahead_days":
            out["extended"] = n
        elif k == "immediate_lookahead_days":
            out["immediate"] = n
    return out
