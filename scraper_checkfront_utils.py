"""
Shared helpers for Checkfront-platform scrapers.

Mirrors the scraper_zaui_utils.py pattern — the API/parsing layer is
extracted into this module; per-tenant scraper_*.py files contain only
provider config + main() orchestration.

Public API surface — no credentials required for any of these endpoints.
The rated request path (`fetch_rated_item` + `parse_rated_price` +
`parse_rated_per_date_stock`) is the recommended source for prices and
per-date stock counts. The unrated `/item/cal` calendar endpoint exposes
only binary availability (`1` if available) on the public API; the rated
per-item endpoint exposes full integer counts. See the Checkfront row of
the "Velocity signal granularity ceiling" table in CLAUDE.md for the
complete background.

Endpoints covered:
  GET /api/3.0/item                                    full catalogue
  GET /api/3.0/item/cal?item_id[]=…&start_date=…       calendar (binary)
  GET /api/3.0/item/{id}?start_date=…&end_date=…       rated (price+stock)

Reference: https://api.checkfront.com/ref/item.html
Sample rated response: https://github.com/Checkfront/API/blob/master/docs/examples/response/item-rated.json
"""

from __future__ import annotations

import re
import time
import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

# Default header sent by the existing Girth Hitch + AAA scrapers. Some
# tenants reject requests without it; harmless for tenants that don't.
DEFAULT_HEADERS = {"X-On-Behalf": "Off"}

DEFAULT_TIMEOUT_S = 15
RETRY_BACKOFFS_S = (2, 4, 8)  # exponential backoff between the 3 attempts


def cf_get(tenant_base: str, endpoint: str, params=None, headers=None,
           attempts: int = 3) -> dict:
    """GET a Checkfront endpoint with retry-on-5xx.

    Checkfront's `/item/cal` (and occasionally `/item/{id}` rated) returns
    500 on otherwise-valid requests — usually transient. Default policy is
    3 attempts with exponential backoff (2s, 4s) between them. 4xx
    responses still raise immediately (no point retrying a bad request).

    The `attempts` knob lets non-critical paths fast-fail. The rated-fetch
    helper (`fetch_rated_item`) defaults to `attempts=1` — a price-fetch
    failure is acceptable (caller falls back to catalog price=None) and
    not worth 14s of retry budget per failed item on a flaky tenant. The
    calendar path (`fetch_calendar`) keeps the default 3 attempts because
    date-discovery failures are more costly downstream.

    Args:
        tenant_base: e.g. "https://girth-hitch-guiding.checkfront.com/api/3.0"
        endpoint:    relative path, e.g. "item" or "item/cal" or "item/123"
        params:      query string params dict (Checkfront uses `item_id[]`
                     for arrays — pass as a list value)
        headers:     defaults to DEFAULT_HEADERS
        attempts:    total attempts (1 = single try, no retries; default 3
                     = first + 2 retries with backoffs RETRY_BACKOFFS_S)

    Returns:
        Parsed JSON response.

    Raises:
        requests.HTTPError on persistent 5xx or any 4xx after retries.
        requests.RequestException on persistent network errors.
    """
    headers = headers if headers is not None else DEFAULT_HEADERS
    last_err: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            r = requests.get(
                f"{tenant_base}/{endpoint}",
                params=params,
                headers=headers,
                timeout=DEFAULT_TIMEOUT_S,
            )
            if 500 <= r.status_code < 600:
                last_err = requests.HTTPError(
                    f"{r.status_code} {r.reason} for {r.url}", response=r
                )
                if attempt < attempts - 1:
                    backoff = RETRY_BACKOFFS_S[min(attempt, len(RETRY_BACKOFFS_S) - 1)]
                    print(f"  Checkfront {r.status_code} on {endpoint} — retry {attempt + 1}/{attempts - 1} in {backoff}s")
                    time.sleep(backoff)
                    continue
                raise last_err
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_err = e
            if attempt < attempts - 1:
                backoff = RETRY_BACKOFFS_S[min(attempt, len(RETRY_BACKOFFS_S) - 1)]
                print(f"  Checkfront request error on {endpoint} ({e}) — retry {attempt + 1}/{attempts - 1} in {backoff}s")
                time.sleep(backoff)
                continue
            raise
    # Unreachable — both branches above either return or raise on the
    # final attempt — but keeps the contract explicit for static analysis.
    raise last_err if last_err else RuntimeError(f"cf_get exhausted retries on {endpoint}")


def fetch_catalog(tenant_base: str, headers=None) -> dict:
    """GET /api/3.0/item — full item catalogue for the tenant.

    Returns the items dict keyed by item_id. Also prints the set of
    distinct categories observed (useful when first onboarding a tenant —
    drives the EXCLUDE_CATEGORIES / KEEP_CATEGORIES configuration).
    """
    data = cf_get(tenant_base, "item", headers=headers)
    items = data.get("items", {}) or {}
    cats = sorted({(item.get("category") or "none") for item in items.values()})
    print(f"  Categories found: {cats}")
    return items


def fetch_calendar(
    tenant_base: str,
    item_ids: list,
    start: str,
    end: str,
    headers=None,
    batch_size: int = 5,
) -> dict:
    """GET /api/3.0/item/cal with batch + per-item fallback.

    Some Checkfront tenants 500 on batch lists larger than ~5 items.
    When a batch fails after cf_get's retry-on-5xx, fall back to per-item
    queries for that batch — one or more specific item_ids is poisoning
    the batch, and we don't want one bad item to take out the whole run.
    Individual failures are logged + skipped.

    Returns: {item_id: {YYYYMMDD: int}} where the int is binary on the
    public API (1=available, 0=not). Use `detect_checkfront_spot_counts`
    from scraper_utils to identify per-item which tenants happen to leak
    real counts here.
    """
    result: dict = {}
    failed_items: list = []
    for i in range(0, len(item_ids), batch_size):
        batch = item_ids[i:i + batch_size]
        params = {
            "item_id[]": batch,
            "start_date": start,
            "end_date":   end,
        }
        try:
            data = cf_get(tenant_base, "item/cal", params=params, headers=headers)
            result.update(data.get("items", {}) or {})
            continue
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"  Batch {batch} failed ({status}); falling back to per-item")
        # Per-item fallback — at least the healthy items in the batch get through.
        for iid in batch:
            try:
                data = cf_get(tenant_base, "item/cal", params={
                    "item_id[]": [iid],
                    "start_date": start,
                    "end_date":   end,
                }, headers=headers)
                result.update(data.get("items", {}) or {})
            except Exception as e:
                failed_items.append(iid)
                print(f"    item {iid}: {e}")
    if failed_items:
        print(f"  Skipped {len(failed_items)} item(s) that 500'd individually: {failed_items}")
    return result


def fetch_rated_item(
    tenant_base: str,
    item_id,
    start: str,
    end: str,
    headers=None,
    attempts: int = 1,
) -> dict:
    """GET /api/3.0/item/{id}?start_date=…&end_date=… — rated response.

    The rated response includes the price under
    `item.rate.summary.price.total` (string like "$130.00") and per-date
    stock counts under `item.rate.dates.{YYYYMMDD}.stock.{A,T}` (A =
    available, T = total capacity).

    Returns the full response dict. Use `parse_rated_price` and
    `parse_rated_per_date_stock` to extract the useful fields.

    Date format: YYYYMMDD (no hyphens), matching what /item/cal expects.

    Default `attempts=1` (single try, no retries). Rated failures are
    non-critical — the scraper falls back to catalog price (often None
    for these tenants). Burning the full 14s retry budget per 500 on a
    flaky Checkfront tenant blows up wall-time without recovering enough
    data to justify it. Override via `attempts=` if a particular tenant
    proves more reliable on this endpoint than on `/item/cal`.

    PERFORMANCE NOTE: On flaky / slow tenants (e.g. girth-hitch-guiding)
    a wide date range (180 days) frequently times out at 15s because
    Checkfront has to compute rates across every date × every customer
    type combination. If you hit timeouts on this endpoint, prefer
    `fetch_rated_price_sampled` instead — it spot-checks 4 dates with
    1-day windows and runs ~30-50x faster per call.
    """
    return cf_get(tenant_base, f"item/{item_id}", params={
        "start_date": start,
        "end_date":   end,
    }, headers=headers, attempts=attempts)


def fetch_rated_price_sampled(
    tenant_base: str,
    item_id,
    start_iso: str,
    sample_offsets_days=(0, 30, 90, 150),
    lookahead_days: int = 180,
    headers=None,
    attempts: int = 1,
    guests: int = 1,
) -> dict:
    """Spot-check rated prices at multiple date offsets to avoid the
    full-window timeout problem on slow tenants.

    Each sample is a 1-day rated request (`start_date == end_date`) which
    Checkfront can compute in a fraction of a second — vs ~15s+ for a
    180-day window. Per-rate-plan disambiguation is forced via
    `param[guests]=N` so Checkfront doesn't try every customer-type
    combination.

    First-failure short-circuit: if the FIRST sample fails (timeout or
    5xx after attempts exhausted), the remaining samples are skipped for
    this item. Tenants that timeout on one sample almost always timeout
    on all of them; no point burning the full retry budget.

    Args:
        tenant_base:        e.g. "https://x.checkfront.com/api/3.0"
        item_id:            Checkfront item id
        start_iso:          start date as YYYYMMDD (today, usually)
        sample_offsets_days: tuple of day offsets from start_iso to sample.
                            Default (0, 30, 90, 150) covers seasonal
                            transitions for most outdoor providers.
        lookahead_days:     skip offsets > this; defensively bounds samples
                            to the catalogue's actual lookahead window.
        headers:            defaults to DEFAULT_HEADERS via cf_get
        attempts:           per-sample attempts (default 1, fast-fail)
        guests:             passed as `param[guests]=N`. 1 is universal;
                            override for tenants whose minimum group is >1.

    Returns:
        {YYYYMMDD: price_int_or_None}. Empty dict if the first sample
        failed and triggered the short-circuit.

        Stable price across samples → caller can use any value.
        Differing prices → caller decides how to handle (variance warning
        + pick first sample is the recommended pattern).
    """
    import datetime as _dt
    try:
        start_date = _dt.datetime.strptime(start_iso, "%Y%m%d").date()
    except ValueError as e:
        log.error(f"fetch_rated_price_sampled: invalid start_iso {start_iso!r}: {e}")
        return {}

    samples: dict = {}
    for offset in sample_offsets_days:
        if offset >= lookahead_days:
            continue
        sample_iso = (start_date + _dt.timedelta(days=offset)).strftime("%Y%m%d")
        try:
            data = cf_get(tenant_base, f"item/{item_id}", params={
                "start_date":     sample_iso,
                "end_date":       sample_iso,
                "param[guests]":  str(guests),
            }, headers=headers, attempts=attempts)
            samples[sample_iso] = parse_rated_price(data)
        except Exception as e:
            if not samples:
                # First-sample failure → skip remaining samples for this item
                log.info(f"item {item_id}: first sample failed ({e}); skipping further samples")
                return {}
            # Some samples succeeded; record this failure but keep going
            samples[sample_iso] = None
    return samples


# Matches "$130.00" / "130.00" / "1,234.50" — captures the numeric portion.
_PRICE_NUMERIC_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_rated_price(item_data: dict) -> Optional[int]:
    """Extract integer price from a rated /item/{id} response.

    Path: item.rate.summary.price.total. Value is a string like "$130.00".
    Strips the currency symbol and thousands separators, rounds to int.

    Returns None if the path is missing or unparseable.
    """
    rate = (item_data.get("item") or {}).get("rate") or {}
    summary = rate.get("summary") or {}
    price_obj = summary.get("price") or {}
    total = price_obj.get("total")
    if total is None:
        return None
    s = str(total).replace(",", "")
    m = _PRICE_NUMERIC_RE.search(s)
    if not m:
        return None
    try:
        return int(round(float(m.group(0).replace(",", ""))))
    except (ValueError, TypeError):
        return None


def parse_rated_per_date_stock(item_data: dict) -> dict:
    """Extract {YYYYMMDD: {available: int|None, total: int|None}} from a
    rated /item/{id} response.

    Path: item.rate.dates.{YYYYMMDD}.stock.{A,T}. `A` is the seats still
    available; `T` is the total capacity for that date.

    Returns {} when the path is missing.
    """
    rate = (item_data.get("item") or {}).get("rate") or {}
    dates = rate.get("dates") or {}
    out: dict = {}
    for date_key, stock_info in dates.items():
        stock = (stock_info or {}).get("stock") or {}
        try:
            available = int(stock["A"]) if stock.get("A") is not None else None
        except (ValueError, TypeError):
            available = None
        try:
            total = int(stock["T"]) if stock.get("T") is not None else None
        except (ValueError, TypeError):
            total = None
        out[date_key] = {"available": available, "total": total}
    return out
