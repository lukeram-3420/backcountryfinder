#!/usr/bin/env python3
"""
bootstrap_summaries.py — One-time migration to seed course_summaries table.

Copies existing summaries from the courses table into course_summaries,
keyed by (provider_id, title). Uses the current summary text as a proxy
for description_hash since original descriptions are not stored.

Safe to re-run: uses insert with conflict tolerance on (provider_id, title).

Usage:
    python bootstrap_summaries.py
"""

import hashlib
import logging

import requests

from scraper_utils import sb_get, SUPABASE_URL, SUPABASE_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def insert_summary(row: dict) -> str:
    """
    Insert a single row into course_summaries.
    Returns: "inserted", "skipped" (conflict), or "error".
    """
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/course_summaries",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        },
        json=row,
    )
    if r.ok:
        return "inserted"
    # 409 = conflict (unique constraint hit) — treat as skipped
    if r.status_code == 409:
        return "skipped"
    # Some PostgREST configs return 400 with a duplicate-key message
    if "duplicate key" in (r.text or "").lower() or "unique constraint" in (r.text or "").lower():
        return "skipped"
    log.warning(f"Insert failed {r.status_code}: {r.text[:200]}")
    return "error"


def main():
    log.info("── Bootstrap course_summaries ──")

    # 1. Fetch all courses with a non-empty summary
    courses = sb_get("courses", {
        "select": "id,provider_id,title,summary",
        "summary": "not.is.null",
    })
    # PostgREST's not.is.null excludes true nulls but may include empty strings
    with_summary = [c for c in courses if (c.get("summary") or "").strip()]
    log.info(f"Total courses with summaries: {len(with_summary)}")

    # 2. Deduplicate by (provider_id, title) — keep first occurrence
    seen = set()
    unique_rows = []
    for c in with_summary:
        key = (c["provider_id"], c["title"])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(c)
    log.info(f"Unique (provider_id, title) combinations: {len(unique_rows)}")

    # 3. Insert into course_summaries
    inserted = 0
    skipped = 0
    errors = 0
    from datetime import datetime
    now_iso = datetime.utcnow().isoformat()

    for c in unique_rows:
        summary = c["summary"].strip()
        desc_hash = hashlib.md5(summary.encode()).hexdigest()
        row = {
            "provider_id":      c["provider_id"],
            "title":            c["title"],
            "course_id":        c["id"],
            "summary":          summary,
            "description_hash": desc_hash,
            "approved":         True,
            "approved_at":      now_iso,
            "pending_reason":   None,
        }
        result = insert_summary(row)
        if result == "inserted":
            inserted += 1
        elif result == "skipped":
            skipped += 1
        else:
            errors += 1

    log.info(f"── Bootstrap complete: {inserted} inserted · {skipped} skipped (already exist) · {errors} errors ──")


if __name__ == "__main__":
    main()
