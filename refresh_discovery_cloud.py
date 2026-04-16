#!/usr/bin/env python3
"""
refresh_discovery_cloud.py — Rebuild discovery_cloud terms from live course/provider data.

Scans course titles for common activity-related bigrams and extracts distinct
regions from provider locations + location_mappings. Upserts auto terms,
never overwrites admin-deactivated terms or manual entries.

Usage:
    python refresh_discovery_cloud.py
    python refresh_discovery_cloud.py --dry-run   # print terms, no writes
"""

import os
import re
import logging
import argparse
from collections import Counter

import requests

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh_cloud")

# ── Environment ──────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── Stopwords for bigram extraction ──────────────────────────────────────────

# Filler bigrams that appear in course titles but make bad search terms
STOP_BIGRAMS = {
    "1 2", "2 3", "3 4", "day 1", "day 2", "day 3", "day 4",
    "part 1", "part 2", "part 3", "level 1", "level 2", "level 3",
    "and the", "with a", "for the", "in the", "of the", "on the",
    "to the", "at the", "from the", "is a", "a full", "the best",
    "our most", "this is", "we offer", "you will", "will be",
    "per person", "per group", "full day", "half day",
}

# Single words to skip when forming bigrams
STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
    "is", "it", "by", "with", "from", "as", "be", "was", "are", "been",
    "this", "that", "your", "our", "their", "my", "its", "not", "but",
    "all", "can", "will", "has", "had", "have", "do", "does", "did",
    "just", "very", "most", "more", "also", "each", "every", "any",
    "day", "days", "night", "nights", "hour", "hours", "week",
    "new", "per", "via", "no", "yes",
}


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def sb_get(table, params=None):
    if params is None:
        params = {}
    headers = _sb_headers()
    headers["Range"] = "0-49999"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def sb_upsert_cloud(rows):
    """Upsert rows to discovery_cloud. Conflict on (lower(term), type)."""
    if not rows:
        return
    headers = _sb_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    # Upsert in batches of 100
    for i in range(0, len(rows), 100):
        batch = rows[i:i+100]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/discovery_cloud",
            headers=headers,
            json=batch,
        )
        if not r.ok:
            log.error(f"Upsert error {r.status_code}: {r.text[:300]}")


# ── Extract activity bigrams from course titles ─────────────────────────────

def tokenize(text):
    """Lowercase, strip non-alpha, split into words."""
    text = re.sub(r"[^a-z\s]", " ", text.lower())
    return [w for w in text.split() if w and w not in STOP_WORDS and len(w) > 1]


def extract_bigrams(titles):
    """Extract bigrams from course titles, count across unique provider-title pairs.
    Returns Counter of bigram -> number of distinct providers using it."""
    bigram_providers = {}  # bigram -> set of provider_ids

    for provider_id, title in titles:
        words = tokenize(title)
        seen = set()
        for i in range(len(words) - 1):
            bg = f"{words[i]} {words[i+1]}"
            if bg in STOP_BIGRAMS or bg in seen:
                continue
            seen.add(bg)
            if bg not in bigram_providers:
                bigram_providers[bg] = set()
            bigram_providers[bg].add(provider_id)

    # Only keep bigrams that appear across 2+ providers
    return Counter({bg: len(pids) for bg, pids in bigram_providers.items() if len(pids) >= 2})


def extract_single_keywords(titles):
    """Extract high-signal single keywords (activity-like nouns) that appear across 3+ providers."""
    # Activity-related keywords worth searching for as standalone terms
    activity_keywords = {
        "skiing", "climbing", "mountaineering", "hiking", "biking", "fishing",
        "snowshoeing", "rappelling", "scrambling", "trekking", "kayaking",
        "canoeing", "rafting", "canyoning", "paragliding", "splitboarding",
        "backcountry", "alpine", "glacier", "avalanche", "ski", "climb",
        "summit", "ridge", "traverse", "expedition", "touring", "telemark",
        "ice", "rock", "multi-pitch", "bouldering", "ferrata",
    }
    kw_providers = {}
    for provider_id, title in titles:
        words = set(tokenize(title))
        for w in words:
            if w in activity_keywords:
                if w not in kw_providers:
                    kw_providers[w] = set()
                kw_providers[w].add(provider_id)

    return Counter({kw: len(pids) for kw, pids in kw_providers.items() if len(pids) >= 3})


# ── Extract location terms ──────────────────────────────────────────────────

# Province abbreviation -> full name for search queries
PROVINCE_NAMES = {
    "AB": "Alberta", "BC": "British Columbia", "SK": "Saskatchewan",
    "MB": "Manitoba", "ON": "Ontario", "QC": "Quebec", "NB": "New Brunswick",
    "NS": "Nova Scotia", "PE": "Prince Edward Island", "NL": "Newfoundland",
    "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut",
}

# Base regions always included regardless of data
BASE_REGIONS = {"British Columbia", "Alberta", "Canadian Rockies"}


def extract_location_terms(providers, location_mappings):
    """Extract distinct provinces and notable areas from provider/location data."""
    terms = {}  # term -> weight (number of providers/mappings in that area)

    # Always include base regions
    for region in BASE_REGIONS:
        terms[region] = terms.get(region, 0) + 1

    # Extract provinces from provider locations ("Canmore, AB" -> "Alberta")
    for p in providers:
        loc = (p.get("location") or "").strip()
        if not loc:
            continue
        parts = [x.strip() for x in loc.split(",")]
        if len(parts) >= 2:
            province_abbr = parts[-1].upper().strip()
            province_full = PROVINCE_NAMES.get(province_abbr)
            if province_full:
                terms[province_full] = terms.get(province_full, 0) + 1
            # Also add the city/area as a location term if it appears for 2+ providers
            area = parts[0].strip()
            if area:
                terms[area] = terms.get(area, 0) + 1

    # Extract provinces from location_mappings canonical values
    for m in location_mappings:
        canonical = (m.get("location_canonical") or "").strip()
        if not canonical:
            continue
        parts = [x.strip() for x in canonical.split(",")]
        if len(parts) >= 2:
            province_abbr = parts[-1].upper().strip()
            province_full = PROVINCE_NAMES.get(province_abbr)
            if province_full:
                terms[province_full] = terms.get(province_full, 0) + 1

    # Filter: provinces always kept, areas need weight >= 2
    result = {}
    for term, weight in terms.items():
        if term in PROVINCE_NAMES.values() or term in BASE_REGIONS:
            result[term] = weight
        elif weight >= 2:
            result[term] = weight

    return result


# ── Load existing cloud state ────────────────────────────────────────────────

def load_existing_cloud():
    """Load current discovery_cloud rows. Returns {(lower_term, type): row}."""
    rows = sb_get("discovery_cloud")
    return {(r["term"].lower(), r["type"]): r for r in rows}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Refresh discovery cloud terms")
    parser.add_argument("--dry-run", action="store_true", help="Print terms, no writes")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return

    # 1. Load data
    log.info("Loading courses...")
    courses = sb_get("courses", {"select": "title,provider_id", "active": "eq.true"})
    titles = [(c["provider_id"], c["title"]) for c in courses if c.get("title") and c.get("provider_id")]
    log.info(f"  {len(titles)} active courses")

    log.info("Loading providers...")
    providers = sb_get("providers", {"select": "id,location"})
    log.info(f"  {len(providers)} providers")

    log.info("Loading location mappings...")
    location_mappings = sb_get("location_mappings", {"select": "location_canonical"})
    log.info(f"  {len(location_mappings)} location mappings")

    # 2. Extract terms
    log.info("Extracting activity bigrams...")
    bigrams = extract_bigrams(titles)
    log.info(f"  {len(bigrams)} bigrams across 2+ providers")

    log.info("Extracting activity keywords...")
    keywords = extract_single_keywords(titles)
    log.info(f"  {len(keywords)} single keywords across 3+ providers")

    log.info("Extracting location terms...")
    locations = extract_location_terms(providers, location_mappings)
    log.info(f"  {len(locations)} location terms")

    # 3. Merge activity terms (bigrams + keywords)
    activity_terms = {}
    for term, weight in bigrams.items():
        activity_terms[term] = weight
    for term, weight in keywords.items():
        # Keywords get a boost since they're high-signal
        activity_terms[term] = max(activity_terms.get(term, 0), weight)

    if args.dry_run:
        log.info("=== DRY RUN ===")
        log.info(f"\nActivity terms ({len(activity_terms)}):")
        for term, weight in sorted(activity_terms.items(), key=lambda x: -x[1]):
            log.info(f"  {weight:3d}  {term}")
        log.info(f"\nLocation terms ({len(locations)}):")
        for term, weight in sorted(locations.items(), key=lambda x: -x[1]):
            log.info(f"  {weight:3d}  {term}")
        return

    # 4. Load existing cloud to respect admin deactivations
    existing = load_existing_cloud()

    # 5. Build upsert rows — skip manual entries and respect active=false
    upsert_rows = []

    for term, weight in activity_terms.items():
        key = (term.lower(), "activity")
        existing_row = existing.get(key)
        # Never overwrite manual entries
        if existing_row and existing_row.get("source") == "manual":
            continue
        row = {"term": term, "type": "activity", "weight": weight, "source": "auto"}
        # Preserve admin's active=false decision
        if existing_row and existing_row.get("active") is False:
            row["active"] = False
        else:
            row["active"] = True
        upsert_rows.append(row)

    for term, weight in locations.items():
        key = (term.lower(), "location")
        existing_row = existing.get(key)
        if existing_row and existing_row.get("source") == "manual":
            continue
        row = {"term": term, "type": "location", "weight": weight, "source": "auto"}
        if existing_row and existing_row.get("active") is False:
            row["active"] = False
        else:
            row["active"] = True
        upsert_rows.append(row)

    # 6. Upsert
    log.info(f"Upserting {len(upsert_rows)} cloud terms...")
    sb_upsert_cloud(upsert_rows)
    log.info(f"Done: {len(activity_terms)} activity + {len(locations)} location terms refreshed")


if __name__ == "__main__":
    main()
