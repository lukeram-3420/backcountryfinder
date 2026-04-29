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

# Replica indexes power the price-sort toggle on the search page. Replicas
# auto-mirror the primary's records — only customRanking differs. Synonyms
# don't auto-replicate, so configure_index() pushes them to all three.
REPLICA_PRICE_ASC = f"{ALGOLIA_INDEX_NAME}_price_asc"
REPLICA_PRICE_DESC = f"{ALGOLIA_INDEX_NAME}_price_desc"

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


def _title_hash_from_id(course_id):
    """V2 stable id format `{provider}-{date_sort}-{title_hash_8}` (or
    `{provider}-flex-{title_hash_8}`). The title hash is always the last 8
    chars. Returns None if the id is too short to slice safely."""
    if not course_id or len(course_id) < 8:
        return None
    return course_id[-8:]


def _group_key(course):
    """Group identity: (provider_id, title_hash). Falls back to
    (provider_id, lower-stripped title) when title_hash can't be derived."""
    pid = course.get("provider_id") or ""
    th = _title_hash_from_id(course.get("id"))
    if th:
        return (pid, th)
    title = (course.get("title") or "").strip().lower()
    return (pid, f"title:{title}")


def _build_session(course):
    """Per-date entry for a group's `dates[]` array."""
    return {
        "id":              course.get("id"),
        "date_sort":       date_to_timestamp(course.get("date_sort")),
        "date_display":    course.get("date_display") or "",
        "price":           course.get("price"),
        "avail":           course.get("avail") or "open",
        "spots_remaining": course.get("spots_remaining"),
        "booking_url":     course.get("booking_url") or "",
    }


def group_courses_for_algolia(courses):
    """Group per-(course,date) Supabase rows into one Algolia record per
    (provider_id, title_hash). Each record carries:

    - card-level fields (title, summary, location, image, provider, etc.)
      taken from the head row (the next-upcoming-date — i.e. the row with
      the smallest date_sort after grouping).
    - `next_date_sort` (scalar) for `customRanking: asc(next_date_sort)` and
      the frontend's date numericFilter.
    - `next_date_display` (string) for the primary session row.
    - `price_min` (smallest positive price across sessions in the group).
    - `price_has_variations` set true if EITHER any session has it set OR if
      the sessions show ≥2 distinct positive prices.
    - `dates[]` array of per-session entries (id, date_sort, date_display,
      price, avail, spots_remaining, booking_url) sorted ascending by
      date_sort. Used by the card to render the multi-date affordance.

    `objectID` is `{provider_id}-{title_hash}` — flat, no date segment, so
    the search grid no longer indexes the same course as N records.
    """
    buckets = {}
    for c in courses:
        key = _group_key(c)
        buckets.setdefault(key, []).append(c)

    records = []
    for (pid, th), items in buckets.items():
        # Sort by date_sort asc (sessions with no date land at the end via
        # FLEX_DATE_TIMESTAMP, mirroring the existing single-record behaviour).
        items.sort(key=lambda c: date_to_timestamp(c.get("date_sort")))
        head = items[0]
        provider = head.get("providers") or {}

        sessions = [_build_session(c) for c in items]
        positive_prices = [c.get("price") for c in items if isinstance(c.get("price"), (int, float)) and c.get("price") > 0]
        price_min = min(positive_prices) if positive_prices else head.get("price")
        distinct_prices = {p for p in positive_prices}
        price_has_variations = (
            any((c.get("price_has_variations") or False) for c in items)
            or len(distinct_prices) >= 2
        )

        record = {
            "objectID":             f"{pid}-{th}",
            "id":                   head.get("id"),
            "title_hash":           th if th and not th.startswith("title:") else None,
            "title":                head.get("title"),
            "search_document":      head.get("search_document"),
            "summary":              head.get("summary"),
            "location_canonical":   head.get("location_canonical"),
            "location_raw":         head.get("location_raw"),
            "duration_days":        head.get("duration_days"),
            "currency":             head.get("currency"),
            "image_url":            head.get("image_url"),
            "booking_mode":         head.get("booking_mode"),
            "custom_dates":         head.get("custom_dates"),
            "price_min":            price_min,
            "price_has_variations": price_has_variations,
            "next_date_sort":       sessions[0]["date_sort"],
            "next_date_display":    sessions[0]["date_display"],
            "dates":                sessions,
            "provider_id":          head.get("provider_id"),
            "provider_name":        provider.get("name"),
            "provider_rating":      provider.get("rating"),
            "provider_logo_url":    provider.get("logo_url"),
        }
        # Omit null/None top-level scalars — Algolia handles missing fields gracefully
        records.append({k: v for k, v in record.items() if v is not None})
    return records


# ── Index configuration ──────────────────────────────────────────────────────

def configure_index(client):
    """Set searchable attributes, facets, ranking, and synonyms.

    Configures the primary index with replicas declared, then sets each
    replica's customRanking so the price-sort toggle on the search page
    can swap indexes via search.helper.setIndex(). Synonyms are pushed
    to every index — Algolia replicas don't auto-mirror synonyms.
    """
    log.info("Configuring primary index settings...")

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
                "location_canonical",
                "filterOnly(provider_name)",
                "filterOnly(provider_id)",
                "filterOnly(booking_mode)",
                "filterOnly(avail)",
            ],
            "customRanking": [
                "asc(next_date_sort)",
            ],
            "replicas": [REPLICA_PRICE_ASC, REPLICA_PRICE_DESC],
        },
    )

    # Replicas inherit searchable attributes + faceting from the primary on
    # creation. We only override customRanking. price_min is omitted on
    # priceless records (algolia_sync.py strips None values) — Algolia
    # deprioritizes records missing a customRanking attribute, so they sort
    # to the end on asc and to the start on desc after priced records.
    log.info(f"Configuring replica {REPLICA_PRICE_ASC}...")
    client.set_settings(
        index_name=REPLICA_PRICE_ASC,
        index_settings={
            "customRanking": ["asc(price_min)", "asc(next_date_sort)"],
        },
    )
    log.info(f"Configuring replica {REPLICA_PRICE_DESC}...")
    client.set_settings(
        index_name=REPLICA_PRICE_DESC,
        index_settings={
            "customRanking": ["desc(price_min)", "asc(next_date_sort)"],
        },
    )
    log.info("Index + replica settings configured")

    for idx in (ALGOLIA_INDEX_NAME, REPLICA_PRICE_ASC, REPLICA_PRICE_DESC):
        log.info(f"Pushing {len(SYNONYMS)} synonym rules to {idx}...")
        client.save_synonyms(
            index_name=idx,
            synonym_hit=SYNONYMS,
            replace_existing_synonyms=True,
        )
    log.info("Synonyms configured on primary + replicas")


# ── Record push ──────────────────────────────────────────────────────────────

def push_records(client, records, dry_run=False):
    """Replace all records in Algolia index atomically.
    Uses replace_all_objects which swaps a temp index into place —
    stale records from previous syncs are automatically removed."""

    if dry_run:
        log.info(f"DRY RUN — would push {len(records)} records (full replace)")
        for r in records[:3]:
            ds = r.get("dates") or []
            log.info(f"  Sample: {r.get('objectID')} | {r.get('title')} | "
                     f"location={r.get('location_canonical')} | "
                     f"price_min={r.get('price_min')} | sessions={len(ds)} | "
                     f"next_date={r.get('next_date_display')} | "
                     f"search_doc={'yes' if r.get('search_document') else 'no'}")
        if len(records) > 3:
            log.info(f"  ... and {len(records) - 3} more")
        return

    log.info(f"Replacing all records in {ALGOLIA_INDEX_NAME} ({len(records)} records)...")
    client.replace_all_objects(index_name=ALGOLIA_INDEX_NAME, objects=records)
    log.info(f"All {len(records)} records pushed to {ALGOLIA_INDEX_NAME} (full replace)")


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

    # 1. Fetch courses (one Supabase row per (course, date))
    courses = fetch_courses()
    if not courses:
        log.warning("No V2 courses found — nothing to sync")
        return

    # 2. Group courses by (provider_id, title_hash) and build per-group
    #    Algolia records. Cuts ~5x record count vs. one-record-per-date.
    records = group_courses_for_algolia(courses)
    log.info(f"Grouped {len(courses)} per-date Supabase rows → {len(records)} Algolia records")

    # Stats
    with_search_doc = sum(1 for r in records if r.get("search_document"))
    with_price = sum(1 for r in records if r.get("price_min"))
    with_location = sum(1 for r in records if r.get("location_canonical"))
    total_sessions = sum(len(r.get("dates") or []) for r in records)
    multi_date = sum(1 for r in records if len(r.get("dates") or []) > 1)
    log.info(f"  search_document: {with_search_doc}/{len(records)}")
    log.info(f"  price_min:        {with_price}/{len(records)}")
    log.info(f"  location_canonical: {with_location}/{len(records)}")
    log.info(f"  multi-date courses: {multi_date}/{len(records)}  (total sessions across all groups: {total_sessions})")

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
