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

# Zaui's categories/list endpoints filter by the probe date's totalActivities,
# which hides any category whose next booking is outside the probe window. We
# probe today + 45d + 135d by default so seasonal categories (e.g. via ferrata
# Jun-Oct, tubing Dec-Mar) surface regardless of which month we run in.
# Callers that only care about today can pass date_offsets=(0,) explicitly.
DEFAULT_DATE_OFFSETS = (0, 45, 135)

_last_request_ts = {"t": 0.0}

log = logging.getLogger(__name__)


# ─── Shared catalogue filtering ─────────────────────────────────────────────
# Zaui tenants frequently carry hotels, transfers, rentals, merchandise, and
# gift cards alongside guided experiences. These defaults centralize the
# filter so every Zaui scraper drops the noise consistently.

DEFAULT_EXCLUDE_TITLES = [
    # Non-bookable / admin products
    "gift card", "gift certificate",
    "deposit",
    "membership",
    # Equipment — covers "Bike Rental", "E-Bike Rental", "Ski Rental",
    # "Urban (Sausalito) E-Bike Rental" via substring match.
    "rental",
    # Ski-area adjuncts
    "season pass", "lift ticket",
    # Retail / upsells
    "merchandise",
    "package add-on",
]

DEFAULT_HOTEL_KEYWORDS = [
    "hotel", "resort", "inn", "chalet", "suites",
    "condo", "apartment",
    # Intentionally NOT "lodge" — many backcountry operators use "Lodge"
    # in legitimate guided-experience names (Bugaboo Lodge etc.).
]

DEFAULT_TRANSPORT_KEYWORDS = [
    "airport to", "airport transfer",
    "shuttle to",
]

# Zaui category names we never want in the experience catalogue.
# Matched case-insensitively against `category.name`.
DEFAULT_EXCLUDE_CATEGORIES = {
    "rentals",
    "accommodation", "accommodations",
    "lodging",
    "transportation", "transfers", "shuttles",
    "merchandise",
    "add-ons", "add ons",
}


def is_experience_product(
    title: str,
    category_name: str = "",
    extra_exclude_titles: Optional[list] = None,
) -> bool:
    """True if this Zaui catalogue row is a guided experience worth scraping.

    Filters out hotels, airport transfers, equipment rentals, merchandise,
    gift cards, and anything in an excluded category. All matches are
    case-insensitive substring checks against the title; category match is
    case-insensitive equality.

    Per-provider scrapers may pass `extra_exclude_titles` for tenant-specific
    products not covered by the shared defaults.
    """
    t = (title or "").lower()
    cat = (category_name or "").lower()

    excludes = DEFAULT_EXCLUDE_TITLES + (extra_exclude_titles or [])
    if any(excl in t for excl in excludes):
        return False
    if any(kw in t for kw in DEFAULT_HOTEL_KEYWORDS):
        return False
    if any(kw in t for kw in DEFAULT_TRANSPORT_KEYWORDS):
        return False
    if cat and cat in DEFAULT_EXCLUDE_CATEGORIES:
        return False
    return True


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
                     exclude_names: Iterable[str] = (),
                     date_offsets: Iterable[int] = DEFAULT_DATE_OFFSETS) -> list:
    """Return categories with totalActivities > 0 on any probed date.

    By default drops `DEFAULT_EXCLUDE_CATEGORIES` (rentals / accommodation /
    lodging / transportation / transfers / shuttles / merchandise / add-ons)
    case-insensitively. Callers may pass `exclude_names` to EXTEND (not
    replace) the default set with tenant-specific names.

    Probes today + each offset (in days) and unions the results, deduped by
    category id. Default offsets cover ~6 months forward so seasonal categories
    surface in shoulder seasons too.
    """
    today = datetime.date.today()
    exclude_lower = set(DEFAULT_EXCLUDE_CATEGORIES)
    if exclude_names:
        exclude_lower.update(n.lower() for n in exclude_names)
    by_id: dict = {}
    for offset in date_offsets:
        probe = (today + datetime.timedelta(days=int(offset))).isoformat()
        try:
            data = zaui_get(tenant_slug, portal_id, "activity/categories", {"date": probe})
        except Exception as e:
            log.warning(f"activity/categories failed for probe date {probe}: {e}")
            continue
        for c in data.get("data") or []:
            name = (c.get("name") or "").strip()
            if name.lower() in exclude_lower:
                continue
            if (c.get("totalActivities") or 0) <= 0:
                continue
            cid = c.get("id")
            if cid is not None and cid not in by_id:
                by_id[cid] = c
    return list(by_id.values())


def fetch_activity_list(tenant_slug: str, portal_id: int, category_id: int,
                        date_offsets: Iterable[int] = DEFAULT_DATE_OFFSETS) -> list:
    """Full activity list for a category. Each activity includes description,
    listPrice, price (per-pax dict), image, availability (weekly template),
    durationDays, etc.

    Probes today + each offset (in days) and unions the results, deduped by
    activity id. Default offsets cover ~6 months forward so seasonal
    activities surface in shoulder seasons too.
    """
    today = datetime.date.today()
    by_id: dict = {}
    for offset in date_offsets:
        probe = (today + datetime.timedelta(days=int(offset))).isoformat()
        try:
            data = zaui_get(
                tenant_slug, portal_id, "activity/list",
                {"category": category_id, "date": probe},
            )
        except Exception as e:
            log.warning(f"activity/list failed for cat {category_id} probe date {probe}: {e}")
            continue
        for a in data.get("data") or []:
            aid = a.get("id")
            if aid is not None and aid not in by_id:
                by_id[aid] = a
    return list(by_id.values())


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
