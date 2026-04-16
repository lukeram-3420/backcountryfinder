#!/usr/bin/env python3
"""
algolia_sync.py — Push V2 courses from Supabase to Algolia index.

Reads all active, non-flagged V2 courses with provider data, maps to Algolia
records, configures index settings (searchable attributes, facets, ranking,
synonyms), and pushes via save_objects (upsert by objectID).

Usage:
    python algolia_sync.py                    # full sync + configure settings
    python algolia_sync.py --dry-run          # log records, no push
    python algolia_sync.py --skip-settings    # push records only, skip config

Env vars:
    SUPABASE_URL, SUPABASE_SERVICE_KEY, ALGOLIA_APP_ID, ALGOLIA_ADMIN_KEY,
    ALGOLIA_INDEX_NAME (default: courses_v2)
"""

import os
import logging
import argparse
from datetime import datetime

import requests
from algoliasearch.search.client import SearchClientSync

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("algolia_sync")

# ── Environment ──────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID", "")
ALGOLIA_ADMIN_KEY = os.environ.get("ALGOLIA_ADMIN_KEY", "")
ALGOLIA_INDEX_NAME = os.environ.get("ALGOLIA_INDEX_NAME", "courses_v2")

# Far-future timestamp for flex-date courses (2100-01-01) — sorts to end
FLEX_DATE_TIMESTAMP = 4102444800

# ── Synonyms ─────────────────────────────────────────────────────────────────

SYNONYMS = [
    {"objectID": "ski-synonyms", "type": "synonym",
     "synonyms": ["skiing", "backcountry skiing", "ski touring", "splitboarding"]},
    {"objectID": "climb-synonyms", "type": "synonym",
     "synonyms": ["climbing", "rock climbing", "sport climbing", "trad climbing"]},
    {"objectID": "hike-synonyms", "type": "synonym",
     "synonyms": ["hiking", "backpacking", "trekking"]},
    {"objectID": "mountaineering-synonyms", "type": "synonym",
     "synonyms": ["mountaineering", "alpine climbing", "glacier travel"]},
    {"objectID": "avalanche-synonyms", "type": "synonym",
     "synonyms": ["avalanche safety", "avalanche course", "AST", "AST 1", "AST 2", "avy"]},
    {"objectID": "bc-synonyms", "type": "synonym",
     "synonyms": ["BC", "British Columbia"]},
    {"objectID": "ab-synonyms", "type": "synonym",
     "synonyms": ["AB", "Alberta"]},
]

# ── Supabase helpers ─────────────────────────────────────────────────────────

def fetch_courses():
    """Fetch all active, non-flagged V2 courses with provider join.
    Paginates in chunks of 1000 to avoid PostgREST default limit."""
    all_courses = []
    offset = 0
    PAGE_SIZE = 1000

    while True:
        params = {
            "select": "*,providers(name,rating,logo_url)",
            "active": "eq.true",
            "flagged": "not.is.true",
            "auto_flagged": "not.is.true",
            "activity_canonical": "is.null",
            "limit": str(PAGE_SIZE),
            "offset": str(offset),
            "order": "id",
        }
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/courses",
            headers=headers,
            params=params,
        )
        r.raise_for_status()
        page = r.json()
        all_courses.extend(page)
        log.info(f"  Fetched page {offset // PAGE_SIZE + 1}: {len(page)} rows")

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    log.info(f"Fetched {len(all_courses)} V2 courses from Supabase")
    return all_courses


# ── Record mapping ───────────────────────────────────────────────────────────

def date_to_timestamp(date_str):
    """Convert YYYY-MM-DD to unix timestamp. Returns FLEX_DATE_TIMESTAMP for null/invalid."""
    if not date_str:
        return FLEX_DATE_TIMESTAMP
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
    except (ValueError, TypeError):
        return FLEX_DATE_TIMESTAMP


def map_record(course):
    """Transform a Supabase course row into an Algolia record."""
    provider = course.get("providers") or {}

    record = {
        "objectID": course["id"],
        "title": course.get("title"),
        "search_document": course.get("search_document"),
        "summary": course.get("summary"),
        "activity": course.get("activity"),
        "location_canonical": course.get("location_canonical"),
        "location_raw": course.get("location_raw"),
        "date_sort": date_to_timestamp(course.get("date_sort")),
        "date_display": course.get("date_display"),
        "duration_days": course.get("duration_days"),
        "price": course.get("price"),
        "currency": course.get("currency"),
        "avail": course.get("avail"),
        "badge": course.get("badge"),
        "image_url": course.get("image_url"),
        "booking_url": course.get("booking_url"),
        "custom_dates": course.get("custom_dates"),
        "provider_id": course.get("provider_id"),
        "provider_name": provider.get("name"),
        "provider_rating": provider.get("rating"),
        "provider_logo_url": provider.get("logo_url"),
    }

    # Omit null values — Algolia handles missing fields gracefully
    return {k: v for k, v in record.items() if v is not None}


# ── Index configuration ──────────────────────────────────────────────────────

def configure_index(client):
    """Set searchable attributes, facets, ranking, and synonyms."""
    log.info("Configuring index settings...")

    client.set_settings(
        index_name=ALGOLIA_INDEX_NAME,
        index_settings={
            "searchableAttributes": [
                "title",
                "search_document",
                "provider_name",
                "location_canonical",
            ],
            "attributesForFaceting": [
                "filterOnly(activity)",
                "filterOnly(location_canonical)",
                "filterOnly(provider_name)",
                "filterOnly(avail)",
            ],
            "customRanking": [
                "asc(date_sort)",
            ],
        },
    )
    log.info("Index settings configured")

    log.info(f"Pushing {len(SYNONYMS)} synonym rules...")
    client.save_synonyms(
        index_name=ALGOLIA_INDEX_NAME,
        synonym_hit=SYNONYMS,
        replace_existing_synonyms=True,
    )
    log.info("Synonyms configured")


# ── Record push ──────────────────────────────────────────────────────────────

def push_records(client, records, dry_run=False):
    """Push records to Algolia in batches of 1000."""
    BATCH_SIZE = 1000

    if dry_run:
        log.info(f"DRY RUN — would push {len(records)} records")
        # Log a sample
        for r in records[:3]:
            log.info(f"  Sample: {r.get('objectID')} | {r.get('title')} | "
                     f"activity={r.get('activity')} | location={r.get('location_canonical')} | "
                     f"price={r.get('price')} | search_doc={'yes' if r.get('search_document') else 'no'}")
        if len(records) > 3:
            log.info(f"  ... and {len(records) - 3} more")
        return

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(records) + BATCH_SIZE - 1) // BATCH_SIZE
        client.save_objects(index_name=ALGOLIA_INDEX_NAME, objects=batch)
        log.info(f"Pushed batch {batch_num}/{total_batches} ({len(batch)} records)")

    log.info(f"All {len(records)} records pushed to {ALGOLIA_INDEX_NAME}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync V2 courses to Algolia")
    parser.add_argument("--dry-run", action="store_true", help="Log records, no push")
    parser.add_argument("--skip-settings", action="store_true", help="Skip index config, just push records")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return
    if not args.dry_run and (not ALGOLIA_APP_ID or not ALGOLIA_ADMIN_KEY):
        log.error("ALGOLIA_APP_ID and ALGOLIA_ADMIN_KEY must be set")
        return

    # 1. Fetch courses
    courses = fetch_courses()
    if not courses:
        log.warning("No V2 courses found — nothing to sync")
        return

    # 2. Map to Algolia records
    records = [map_record(c) for c in courses]
    log.info(f"Mapped {len(records)} Algolia records")

    # Stats
    with_search_doc = sum(1 for r in records if r.get("search_document"))
    with_price = sum(1 for r in records if r.get("price"))
    with_location = sum(1 for r in records if r.get("location_canonical"))
    log.info(f"  search_document: {with_search_doc}/{len(records)}")
    log.info(f"  price: {with_price}/{len(records)}")
    log.info(f"  location_canonical: {with_location}/{len(records)}")

    if args.dry_run:
        push_records(None, records, dry_run=True)
        return

    # 3. Initialize Algolia client
    client = SearchClientSync(ALGOLIA_APP_ID, ALGOLIA_ADMIN_KEY)

    # 4. Configure index settings
    if not args.skip_settings:
        configure_index(client)

    # 5. Push records
    push_records(client, records)

    log.info("Algolia sync complete")


if __name__ == "__main__":
    main()
