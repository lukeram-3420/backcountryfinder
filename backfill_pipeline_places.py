#!/usr/bin/env python3
# One-time backfill — run once then delete or archive.
# Usage: python backfill_pipeline_places.py
"""
Fills google_place_id / rating / review_count on every provider_pipeline
row that currently has google_place_id IS NULL, using the Google Places
findplacefromtext API.

Reads SUPABASE_URL, SUPABASE_SERVICE_KEY, GOOGLE_PLACES_API_KEY from env.
Standalone — no scraper_utils import.
"""

import os
import sys
import time
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PLACES_KEY   = os.environ["GOOGLE_PLACES_API_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def fetch_pipeline_rows() -> list:
    url = f"{SUPABASE_URL}/rest/v1/provider_pipeline"
    params = {
        "select": "id,name,location",
        "google_place_id": "is.null",
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def places_lookup(name: str, location: str) -> dict:
    query = f"{name} {location or ''}".strip()
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = {
        "input": query,
        "inputtype": "textquery",
        "fields": "place_id,rating,user_ratings_total",
        "key": PLACES_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return {}
    c = candidates[0]
    return {
        "google_place_id": c.get("place_id"),
        "rating": c.get("rating"),
        "review_count": c.get("user_ratings_total"),
    }


def patch_pipeline(row_id, payload: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/provider_pipeline"
    params = {"id": f"eq.{row_id}"}
    headers = {**HEADERS, "Prefer": "return=minimal"}
    r = requests.patch(url, headers=headers, params=params, json=payload, timeout=30)
    r.raise_for_status()


def main():
    rows = fetch_pipeline_rows()
    print(f"Found {len(rows)} pipeline rows missing google_place_id")
    found = not_found = errors = 0

    for row in rows:
        name = row.get("name") or ""
        location = row.get("location") or ""
        try:
            places = places_lookup(name, location)
        except Exception as e:
            print(f"⚠ Places lookup error for {name!r}: {e}")
            errors += 1
            time.sleep(0.3)
            continue

        if not places.get("google_place_id"):
            print(f"⚠ No Places result for: {name}")
            not_found += 1
            time.sleep(0.3)
            continue

        try:
            patch_pipeline(row["id"], places)
        except Exception as e:
            print(f"⚠ PATCH error for {name!r}: {e}")
            errors += 1
            time.sleep(0.3)
            continue

        rating = places.get("rating")
        rcount = places.get("review_count")
        rating_str = f"★ {rating} ({rcount})" if rating is not None else "★ —"
        print(f"✅ {name} → {places['google_place_id']} {rating_str}")
        found += 1
        time.sleep(0.3)

    print(f"Done — {found} found, {not_found} not found, {errors} errors")


if __name__ == "__main__":
    sys.exit(main())
