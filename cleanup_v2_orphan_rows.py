#!/usr/bin/env python3
"""
cleanup_v2_orphan_rows.py — one-shot cleanup of V2-stable-id orphan rows.

V2 stable ids encode `date_sort` (`{provider}-{date_sort}-{title_hash}`).
When a provider transitions a course from "scheduled with dates" to
"inquiry-only / flex-date", the old V2 rows orphan in the DB:

  - The scraper writes a NEW row at the new V2 id ({provider}-flex-{hash})
    and never touches the old dated id again.
  - Time passes. The old row's `date_sort` is now in the past.
  - validate_provider.py flags it as past_date, escalates 24h later. The
    admin Flags tab Date escalations queue fills up with rows the provider
    can't fix because the listing is correctly turned off on their end.

This script identifies those orphans by `(provider_id, lower(title))`
group: if at least one ROW in the group is currently bookable
(active=true AND (custom_dates=true OR date_sort >= today)), then any
OTHER row in the group with date_sort < today is an orphan and gets
deleted.

`course_availability_log` and `course_price_log` are sacred per CLAUDE.md.
This script ONLY writes to `courses`. The intelligence logs are
append-only by policy and are never touched here.

Usage:
    python cleanup_v2_orphan_rows.py                       # all providers, write
    python cleanup_v2_orphan_rows.py --dry-run             # all providers, no write
    python cleanup_v2_orphan_rows.py --provider-id msaa    # one provider only
    python cleanup_v2_orphan_rows.py --provider-id msaa --dry-run

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY.

Open question to resolve before running: course_availability_log.course_id
is documented as "references courses.id". If that's a real Postgres FK
constraint with ON DELETE NO ACTION, the DELETE will fail. Verify with:

    SELECT conname, conrelid::regclass, confrelid::regclass
    FROM pg_constraint WHERE confrelid = 'public.courses'::regclass;

If FK exists, drop it first OR set --soft-mode flag (UPDATE instead of
DELETE — sets active=false + flag_reason='past_date_orphaned' to take
the row out of every read path while preserving FK integrity).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cleanup_v2_orphan_rows")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def fetch_courses(provider_id: Optional[str]) -> list:
    """Fetch every row in `courses` (or just one provider's). Paginates in
    1000-row windows because Supabase's server-side `max-rows` config caps
    a single response at 1000 regardless of the Range header. PostgREST
    `offset` + `limit` query params bypass this cleanly."""
    select = "id,provider_id,title,date_sort,custom_dates,active,auto_flagged,flag_reason"
    page_size = 1000
    offset = 0
    out: list = []
    while True:
        params = {
            "select": select,
            "order": "id.asc",
            "offset": str(offset),
            "limit": str(page_size),
        }
        if provider_id:
            params["provider_id"] = f"eq.{provider_id}"
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/courses",
            headers=_headers(),
            params=params,
        )
        r.raise_for_status()
        page = r.json()
        out.extend(page)
        log.info(f"  fetched page offset={offset} size={len(page)}")
        if len(page) < page_size:
            break
        offset += page_size
    return out


def delete_course(course_id: str) -> bool:
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/courses?id=eq.{requests.utils.quote(course_id, safe='')}",
        headers={**_headers(), "Prefer": "return=minimal"},
    )
    if not r.ok:
        log.error(f"  DELETE failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def soft_hide_course(course_id: str) -> bool:
    """Fallback when DELETE is blocked (FK constraint). Hides the row from
    every read path: active=false (frontend filter), auto_flagged=true with
    flag_reason='past_date_orphaned' (admin filters)."""
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/courses?id=eq.{requests.utils.quote(course_id, safe='')}",
        headers={**_headers(), "Prefer": "return=minimal"},
        json={
            "active": False,
            "auto_flagged": True,
            "flag_reason": "past_date_orphaned",
        },
    )
    if not r.ok:
        log.error(f"  PATCH failed {r.status_code}: {r.text[:300]}")
        return False
    return True


def parse_date(ds) -> Optional[date]:
    if not ds:
        return None
    try:
        return datetime.strptime(str(ds), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def find_orphans(courses: list, today: date) -> list[dict]:
    """Group rows by (provider_id, lower(title)). For each group, if any
    row is currently bookable, every other row with date_sort < today
    (and not custom_dates) is an orphan."""
    groups: dict = defaultdict(list)
    for c in courses:
        title = (c.get("title") or "").strip().lower()
        if not title:
            continue
        key = (c.get("provider_id") or "", title)
        groups[key].append(c)

    orphans: list[dict] = []
    for key, rows in groups.items():
        # Identify the "currently bookable" set in this group.
        currents: list[dict] = []
        for r in rows:
            if r.get("active") is not True:
                continue
            if r.get("custom_dates"):
                currents.append(r)
                continue
            d = parse_date(r.get("date_sort"))
            if d and d >= today:
                currents.append(r)
        if not currents:
            # No current sibling — leave the group alone. These are either
            # genuinely-stale provider listings (real escalations) or full
            # archive rows. Not our business here.
            continue

        # Mark past-dated rows that AREN'T in the current set as orphans.
        current_ids = {r.get("id") for r in currents}
        for r in rows:
            if r.get("id") in current_ids:
                continue
            if r.get("custom_dates"):
                continue  # never delete a flex row
            d = parse_date(r.get("date_sort"))
            if d is None or d >= today:
                continue  # only past-dated orphans
            orphans.append(r)
    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Identify orphans, do not write")
    parser.add_argument("--provider-id", default=None, help="Only act on one provider")
    parser.add_argument(
        "--soft-mode", action="store_true",
        help="UPDATE rows to active=false + flag_reason='past_date_orphaned' "
             "instead of DELETE. Use when course_availability_log.course_id is "
             "a real FK with ON DELETE NO ACTION.",
    )
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return 1

    today = date.today()
    log.info(f"Fetching courses{' for provider=' + args.provider_id if args.provider_id else ' (all providers)'}")
    courses = fetch_courses(args.provider_id)
    log.info(f"Fetched {len(courses)} courses")

    orphans = find_orphans(courses, today)
    log.info(f"Identified {len(orphans)} orphan row(s) to remove")
    if not orphans:
        return 0

    # Per-provider summary so the dry-run output is scannable.
    by_provider: dict = defaultdict(int)
    for o in orphans:
        by_provider[o.get("provider_id") or ""] += 1
    for pid, n in sorted(by_provider.items()):
        log.info(f"  {pid}: {n} orphan(s)")

    write_fn = soft_hide_course if args.soft_mode else delete_course
    action = "UPDATE→hidden" if args.soft_mode else "DELETE"

    failed = 0
    for idx, o in enumerate(orphans, 1):
        cid = o.get("id") or ""
        ds = o.get("date_sort") or "?"
        pid = o.get("provider_id") or "?"
        title = (o.get("title") or "").strip()
        log.info(f"  [{idx}/{len(orphans)}] {action} {pid} :: {title!r} (date_sort={ds}, id={cid})")
        if args.dry_run:
            continue
        ok = write_fn(cid)
        if not ok:
            failed += 1
        time.sleep(0.3)

    if args.dry_run:
        log.info("dry-run complete — no rows modified")
    else:
        log.info(f"complete — {len(orphans) - failed} succeeded, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
