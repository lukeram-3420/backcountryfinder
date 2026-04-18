#!/usr/bin/env python3
"""seed_activity_controls.py — one-shot seed from historical EXCLUDE_TITLES.

Inserts a `visible=false` row into `activity_controls` for every hardcoded
title a scraper used to drop. Once this has been run and verified, the
`EXCLUDE_TITLES` constants and their call sites can be deleted from the
scraper files — `load_activity_controls(provider_id)` replaces them at
runtime.

Safe to re-run — uses UPSERT on `(provider_id, activity_key)`. Second run
refreshes the visible=false decision but leaves admin-driven toggles
untouched if they've changed the row since (merge-duplicates overwrites
only the columns in the payload).

Dry-run: `python seed_activity_controls.py --dry-run`
"""

import argparse
import sys

import requests

from scraper_utils import (
    SUPABASE_URL, SUPABASE_KEY,
    activity_key, title_hash,
)


# Per-provider historical EXCLUDE_TITLES — mirrors the constants in each
# scraper at the time of migration. Lowercased. These are seeded as
# visible=false rows so the first post-cutover scraper run silently skips
# them just like EXCLUDE_TITLES did.
SEED = {
    "altus": [
        "altus mtn club",
        "altus mountain club",
    ],
    "vibe-backcountry": [
        "gift card",
        "gift certificate",
        "deposit",
        "membership",
        "custom trip",
    ],
    "girth-hitch-guiding": [
        "gift card",
        "gift certificate",
        "deposit",
        "membership",
        "custom trip",
    ],
    "cloud-nine-guides": [
        "gift card",
        "gift certificate",
        "deposit",
        "membership",
        "merchandise",
    ],
    "bow-valley-canyon-tours": [
        "gift card",
        "gift certificate",
        "deposit",
        "membership",
        "merchandise",
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print rows without writing")
    args = ap.parse_args()

    rows = []
    for provider_id, titles in SEED.items():
        for raw in titles:
            t = raw.strip()
            if not t:
                continue
            rows.append({
                "provider_id":  provider_id,
                "activity_key": activity_key("title", None, t),
                "title":        t,
                "title_hash":   title_hash(t),
                "visible":      False,
            })

    if args.dry_run:
        for r in rows:
            print(f"  {r['provider_id']:<25} {r['activity_key']:<20} visible=false  {r['title']!r}")
        print(f"\nWould seed {len(rows)} activity_controls rows across {len(SEED)} providers.")
        return 0

    if not rows:
        print("No rows to seed.")
        return 0

    # Explicit on_conflict — activity_controls.id is bigserial; the real
    # dedup key is (provider_id, activity_key). Without this, PostgREST's
    # merge-duplicates would conflict on the primary key and insert
    # duplicates on every re-run.
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/activity_controls",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "resolution=merge-duplicates,return=minimal",
        },
        json=rows,
        params={"on_conflict": "provider_id,activity_key"},
        timeout=20,
    )
    if not r.ok:
        print(f"ERROR: {r.status_code} {r.text[:500]}", file=sys.stderr)
        return 1
    print(f"Seeded {len(rows)} activity_controls rows across {len(SEED)} providers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
