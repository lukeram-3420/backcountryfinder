#!/usr/bin/env python3
"""Shared Zaui platform helpers for any backcountryfinder scraper targeting a
Zaui tenant (vendor booking system at {slug}.zaui.net/booking).

All endpoints exposed here are anonymously accessible — Zaui only requires
CSRF + cookies for mutating calls, not for the read-only catalogue / pricing /
availability endpoints the scrapers use.

Rate-limited: 0.5s minimum interval between any two GETs from this module.
"""

import datetime
import logging
import time
from typing import Iterable, Optional

import requests

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MIN_INTERVAL_SECONDS = 0.5
_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_last_request_ts = {"t": 0.0}

log = logging.getLogger(__name__)


def _throttle():
    elapsed = time.time() - _last_request_ts["t"]
    if elapsed < MIN_INTERVAL_SECONDS:
        time.sleep(MIN_INTERVAL_SECONDS - elapsed)


def zaui_get(tenant_slug: str, portal_id: int, endpoint: str, params: Optional[dict] = None) -> dict:
    """GET /booking/api/{endpoint} with rate-limiting + portal param injection.

    Returns parsed JSON. Raises on non-2xx.
    """
    _throttle()
    url = f"https://{tenant_slug}.zaui.net/booking/api/{endpoint}"
    merged = dict(params or {})
    merged.setdefault("portal", portal_id)
    r = requests.get(
        url,
        params=merged,
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    _last_request_ts["t"] = time.time()
    r.raise_for_status()
    return r.json()


def fetch_categories(tenant_slug: str, portal_id: int,
                     exclude_names: Iterable[str] = ("Rentals",)) -> list:
    """Return categories with totalActivities > 0, excluding any whose name
    exactly matches an entry in `exclude_names`.
    """
    today = datetime.date.today().isoformat()
    data = zaui_get(tenant_slug, portal_id, "activity/categories", {"date": today})
    cats = data.get("data") or []
    exclude = set(exclude_names)
    out = []
    for c in cats:
        name = (c.get("name") or "").strip()
        if name in exclude:
            continue
        if (c.get("totalActivities") or 0) <= 0:
            continue
        out.append(c)
    return out


def fetch_activity_list(tenant_slug: str, portal_id: int, category_id: int) -> list:
    """Full activity list for a category. Each activity includes description,
    listPrice, price (per-pax dict), image, availability (weekly template),
    durationDays, etc.
    """
    today = datetime.date.today().isoformat()
    data = zaui_get(
        tenant_slug, portal_id, "activity/list",
        {"category": category_id, "date": today},
    )
    return data.get("data") or []


def fetch_unavailability(tenant_slug: str, portal_id: int, activity_id: int,
                         date, days: int = 7) -> list:
    """Blackout dates (unavailable) for a 7-day window starting `date`.
    Returns list of YYYY-MM-DD strings. Empty list = every day in the window
    is bookable (subject to the activity's weekly template).
    """
    if isinstance(date, datetime.date):
        date = date.isoformat()
    data = zaui_get(
        tenant_slug, portal_id, "activity/fetchUnavailability",
        {"id": activity_id, "date": date, "daysInTheFuture": days},
    )
    return data.get("data") or []


def compute_bookable_dates(activity: dict, unavailability,
                           start_date: Optional[datetime.date] = None,
                           end_date: Optional[datetime.date] = None) -> list:
    """Derive concrete bookable dates from the activity's weekly template
    minus the given blackout set.

    Args:
        activity: one entry from fetch_activity_list() — uses .availability[].
        unavailability: iterable of YYYY-MM-DD strings representing blackout days.
        start_date: inclusive lower bound. Defaults to today.
        end_date:   inclusive upper bound. Defaults to start_date + 5 years.

    Returns sorted list of datetime.date objects.
    """
    black = unavailability if isinstance(unavailability, set) else set(unavailability)
    today = datetime.date.today()
    lo = start_date or today
    hi = end_date or (today + datetime.timedelta(days=365 * 5))

    out = set()
    templates = activity.get("availability") or []
    for tpl in templates:
        try:
            t_from = datetime.date.fromisoformat((tpl.get("from") or "")[:10])
            t_to = datetime.date.fromisoformat((tpl.get("to") or "")[:10])
        except (TypeError, ValueError):
            continue
        days_map = tpl.get("days") or {}
        s = max(t_from, lo)
        e = min(t_to, hi)
        cur = s
        while cur <= e:
            day_name = _DAY_NAMES[cur.weekday()]
            if days_map.get(day_name) and cur.isoformat() not in black:
                out.add(cur)
            cur += datetime.timedelta(days=1)
    return sorted(out)


def get_activity_group(all_activities: list, group: int, total_groups: int = 4) -> list:
    """Split activity list into `total_groups` interleaved groups (sorted by
    activity id for stability) and return the group matching `group` (0-based).

    Interleaving (activities[group::total_groups]) spreads evenly across runs
    so a newly-added activity doesn't cluster into one group.
    """
    group = int(group) % total_groups
    ordered = sorted(all_activities, key=lambda a: a.get("id", 0))
    return ordered[group::total_groups]
