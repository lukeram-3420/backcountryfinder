#!/usr/bin/env python3
"""
backfill_platforms.py — one-shot backfill of booking platform for existing
providers + pipeline rows using the same HTML signature detection as
discover_providers.py.

Scope:
  - `providers` rows where `booking_platform` is NULL or 'unknown'
     → PATCH providers.booking_platform
  - `provider_pipeline` rows where `platform` is NULL, empty, 'unknown',
     or 'custom' → PATCH provider_pipeline.platform
     (We re-detect 'custom' rows because Haiku's old web_search guess
      defaulted to 'custom' a lot — signature matching beats that guess.)

Column-name indirection lives in PLATFORM_COLUMN. Safe to re-run — rows
already resolved to a concrete platform are skipped.

Usage:
    python backfill_platforms.py              # do it
    python backfill_platforms.py --dry-run    # print only, no writes
    python backfill_platforms.py --table providers      # only providers
    python backfill_platforms.py --table provider_pipeline  # only pipeline

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY.
"""

import os
import sys
import time
import logging
import argparse

import requests

# Reuse the canonical signature table + detect function from discovery.
from discover_providers import detect_platform

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_platforms")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

PLATFORM_COLUMN = {
    "providers":         "booking_platform",
    "provider_pipeline": "platform",
}

WEBSITE_COLUMN = {
    "providers":         "website",
    "provider_pipeline": "website",
}

# Values that trigger re-detection. 'custom' is included for pipeline rows
# because the old Haiku web_search path defaulted to it on uncertainty.
NEEDS_DETECTION = {None, "", "unknown", "custom"}


def _headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def fetch_rows(table: str) -> list:
    col = PLATFORM_COLUMN[table]
    web = WEBSITE_COLUMN[table]
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**_headers(), "Range": "0-9999"},
        params={"select": f"id,name,{web},{col}"},
    )
    r.raise_for_status()
    return r.json()


def patch_platform(table: str, row_id: str, value: str) -> None:
    col = PLATFORM_COLUMN[table]
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{requests.utils.quote(row_id, safe='')}",
        headers={**_headers(), "Prefer": "return=minimal"},
        json={col: value},
    )
    if not r.ok:
        log.error(f"  PATCH failed {r.status_code}: {r.text[:200]}")
        return
    log.info(f"  {table}.{col}={value} for id={row_id}")


def run(table: str, dry_run: bool) -> None:
    col = PLATFORM_COLUMN[table]
    web = WEBSITE_COLUMN[table]
    log.info(f"── {table} ──────────────────────────────")
    rows = fetch_rows(table)
    candidates = [r for r in rows if r.get(col) in NEEDS_DETECTION]
    log.info(f"  {len(rows)} total, {len(candidates)} need detection")

    skipped_no_url = 0
    resolved = 0
    still_unknown = 0
    for idx, row in enumerate(candidates, 1):
        name = row.get("name") or row.get("id")
        url = row.get(web)
        current = row.get(col) or "—"
        if not url:
            log.info(f"  [{idx}/{len(candidates)}] {name}: no website, skipping")
            skipped_no_url += 1
            continue

        platform, evidence = detect_platform(url)
        if platform == "unknown":
            log.info(f"  [{idx}/{len(candidates)}] {name} ({url}): unknown (no signature matched)")
            still_unknown += 1
        else:
            log.info(f"  [{idx}/{len(candidates)}] {name} ({url}): {current} → {platform} (matched '{evidence}')")
            resolved += 1

        if not dry_run:
            # Always write the result — even 'unknown' — so the column reflects
            # "we tried." This matches admin-detect-platform edge-fn behaviour
            # and lets the UI stop showing a stale pre-detection value.
            patch_platform(table, row["id"], platform)
            time.sleep(0.3)  # gentle on both Supabase and the provider sites

    log.info(f"  resolved: {resolved}, still unknown: {still_unknown}, skipped (no url): {skipped_no_url}")


def main():
    parser = argparse.ArgumentParser(description="Backfill booking_platform / platform via HTML signature detection")
    parser.add_argument("--dry-run", action="store_true", help="Print detections, do not write")
    parser.add_argument("--table", choices=["providers", "provider_pipeline", "both"], default="both")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        sys.exit(1)

    tables = ["providers", "provider_pipeline"] if args.table == "both" else [args.table]
    for t in tables:
        run(t, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
