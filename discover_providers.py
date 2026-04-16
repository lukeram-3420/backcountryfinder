#!/usr/bin/env python3
"""
discover_providers.py — Automated provider discovery for BackcountryFinder.

Runs weekly via GitHub Actions cron. Uses Claude Haiku with web_search to find
Canadian backcountry guide companies based on activity keywords, deduplicates
against known providers/pipeline/submissions, analyses new finds, and appends
them to provider_pipeline as candidates.

Usage:
    python discover_providers.py
    python discover_providers.py --dry-run   # search + dedup only, no inserts
"""

import os
import re
import json
import time
import logging
import argparse
from urllib.parse import urlparse

import requests

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discover_providers")

# ── Environment ──────────────────────────────────────────────────────────────

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_SERVICE_KEY", "")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
PLACES_API_URL = "https://maps.googleapis.com/maps/api/place"

# Query templates — {activity} and {region} are substituted
QUERY_TEMPLATES = [
    "{activity} guides {region} Canada",
    "{activity} courses {region} Canada book online",
    "{activity} tours adventures {region} Canada",
]

# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def sb_get(table, params=None):
    if params is None:
        params = {}
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=_sb_headers(), params=params)
    r.raise_for_status()
    return r.json()


def sb_insert_pipeline(row):
    """Insert a single row to provider_pipeline. Returns True on success, False on conflict."""
    headers = _sb_headers()
    headers["Prefer"] = "return=minimal"
    r = requests.post(f"{SUPABASE_URL}/rest/v1/provider_pipeline", headers=headers, json=row)
    if r.status_code == 409:
        log.info(f"  Pipeline conflict (already exists): {row.get('name', row.get('id', '?'))}")
        return False
    if not r.ok:
        log.error(f"  Pipeline insert error {r.status_code}: {r.text[:300]}")
        return False
    return True


# ── Domain normalization ─────────────────────────────────────────────────────

def normalize_domain(url):
    """Extract and normalize domain from a URL for dedup comparison."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.hostname or ""
        domain = domain.lower().replace("www.", "")
        return domain.rstrip("/")
    except Exception:
        return url.lower().strip("/")


# ── Load known domains for dedup ─────────────────────────────────────────────

def load_known_domains():
    """Load all domains from providers, provider_pipeline, and provider_submissions."""
    known = set()

    # Active + inactive providers
    providers = sb_get("providers", {"select": "website"})
    for p in providers:
        if p.get("website"):
            known.add(normalize_domain(p["website"]))

    # Pipeline candidates
    pipeline = sb_get("provider_pipeline", {"select": "website"})
    for p in pipeline:
        if p.get("website"):
            known.add(normalize_domain(p["website"]))

    # User submissions
    try:
        submissions = sb_get("provider_submissions", {"select": "website"})
        for s in submissions:
            if s.get("website"):
                known.add(normalize_domain(s["website"]))
    except Exception:
        log.info("No provider_submissions table or empty — skipping")

    log.info(f"Loaded {len(known)} known domains for dedup")
    return known


# ── Load discovery cloud ─────────────────────────────────────────────────────

def load_discovery_cloud():
    """Load active terms from discovery_cloud table.
    Returns {activity_terms: [str], location_terms: [str]}."""
    rows = sb_get("discovery_cloud", {"active": "eq.true", "select": "id,term,type,weight"})
    activity_terms = sorted(
        [r["term"] for r in rows if r["type"] == "activity"],
        key=lambda t: next((r["weight"] for r in rows if r["term"] == t), 0),
        reverse=True,
    )
    location_terms = sorted(
        [r["term"] for r in rows if r["type"] == "location"],
        key=lambda t: next((r["weight"] for r in rows if r["term"] == t), 0),
        reverse=True,
    )
    log.info(f"Loaded discovery cloud: {len(activity_terms)} activity + {len(location_terms)} location terms")
    return {"activity": activity_terms, "location": location_terms, "rows": rows}


def update_last_used(term_ids):
    """Stamp last_used_at on terms that were used in this run."""
    if not term_ids:
        return
    for term_id in term_ids:
        try:
            headers = _sb_headers()
            headers["Prefer"] = "return=minimal"
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/discovery_cloud?id=eq.{term_id}",
                headers=headers,
                json={"last_used_at": "now()"},
            )
        except Exception:
            pass


# ── Generate search queries ──────────────────────────────────────────────────

def generate_queries(cloud):
    """Generate search queries from discovery cloud: activity terms x location terms x templates."""
    activity_terms = cloud["activity"]
    location_terms = cloud["location"]
    if not activity_terms or not location_terms:
        log.warning("Discovery cloud is empty — run refresh_discovery_cloud.py first")
        return []

    queries = []
    used_term_ids = set()
    cloud_rows_by_term = {(r["term"].lower(), r["type"]): r for r in cloud["rows"]}

    for activity in activity_terms:
        for region in location_terms:
            # Rotate templates for variety across combinations
            template_idx = hash(activity + region) % len(QUERY_TEMPLATES)
            q = QUERY_TEMPLATES[template_idx].format(activity=activity, region=region)
            queries.append({"query": q, "activity": activity, "region": region})
            # Track which terms were used
            act_row = cloud_rows_by_term.get((activity.lower(), "activity"))
            loc_row = cloud_rows_by_term.get((region.lower(), "location"))
            if act_row:
                used_term_ids.add(act_row["id"])
            if loc_row:
                used_term_ids.add(loc_row["id"])

    log.info(f"Generated {len(queries)} search queries from {len(activity_terms)} activities x {len(location_terms)} locations")
    return queries, used_term_ids


# ── Haiku web search ─────────────────────────────────────────────────────────

SEARCH_SYSTEM_PROMPT = (
    "You are a research assistant for backcountryfinder.com, a Canadian outdoor adventure aggregator. "
    "Search the web for the given query and find outdoor guide companies, adventure tour operators, "
    "and backcountry course providers in Canada. "
    "For each company you find, extract their website URL and a brief note about what types of "
    "courses/trips they offer (e.g. 'backcountry skiing courses, avalanche training', "
    "'rock climbing guides, multi-pitch courses', 'guided hiking, glacier tours').\n\n"
    "Respond in JSON only, no preamble, no markdown:\n"
    '[{"url": "https://example.com", "name": "Company Name", "courses": "brief description of course types"}]\n\n'
    "Return an empty array [] if no relevant companies are found. "
    "Only include companies that offer bookable outdoor courses, guided trips, or adventure experiences. "
    "Exclude gear shops, tourism boards, travel blogs, review sites, and aggregators."
)


def haiku_web_search(query):
    """Call Claude Haiku with web_search tool to find providers for a query.
    Returns list of {url, name, courses} dicts."""
    if not ANTHROPIC_API_KEY:
        log.warning("No ANTHROPIC_API_KEY — skipping search")
        return []
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1024,
                "system": SEARCH_SYSTEM_PROMPT,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                "messages": [{"role": "user", "content": f"Search for: {query}"}],
            },
            timeout=60,
        )
        if not r.ok:
            log.warning(f"Haiku search API error {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        blocks = data.get("content", [])
        # Find text blocks containing JSON
        text = "\n".join(
            b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)
        ).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < 0:
            log.info(f"  No JSON array in response for: {query[:60]}")
            return []
        results = json.loads(text[start:end + 1])
        return [r for r in results if isinstance(r, dict) and r.get("url")]
    except Exception as e:
        log.warning(f"Haiku search failed for '{query[:60]}': {e}")
        return []


# ── Provider analysis (inline version of admin-analyse-provider) ─────────────

ANALYSE_SYSTEM_PROMPT = (
    "You are a scraper analyst for backcountryfinder.com, an outdoor adventure course aggregator in Canada. "
    "Given a provider website URL, fetch the site and identify:\n"
    "- name: the business name (exact name as shown on their site)\n"
    "- location: primary location in 'City, Province' format e.g. 'Squamish, BC' or 'Canmore, AB'. "
    "For multi-location providers use their primary/home location.\n"
    "- platform: their booking platform. Known values: rezdy, fareharbor, woocommerce, wordpress, squarespace, checkfront, custom, unknown\n"
    "- complexity: scraping complexity. low (static HTML or known platform API), medium (JS-rendered or iframe), high (complex custom system or requires Playwright)\n"
    "- priority: 1 (high value — multiple locations, popular area, well known), 2 (medium), 3 (low — single small operator)\n"
    "- notes: 1-2 sentences about the booking system and what to watch for when scraping. "
    "Include what types of courses/trips they offer (e.g. 'Offers backcountry skiing courses and avalanche training.').\n\n"
    "Respond in JSON only, no preamble, no markdown:\n"
    '{"name": "string", "location": "string", "platform": "string", "complexity": "string", "priority": 2, "notes": "string"}'
)


def analyse_provider(url):
    """Analyse a provider URL with Haiku + Google Places. Returns a pipeline row dict or None."""
    # Step 1 — Haiku analysis
    parsed = None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 400,
                "system": ANALYSE_SYSTEM_PROMPT,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": f"Analyse this outdoor adventure provider: {url}"}],
            },
            timeout=60,
        )
        if r.ok:
            data = r.json()
            blocks = data.get("content", [])
            text = "\n".join(
                b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)
            ).strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end >= 0:
                parsed = json.loads(text[start:end + 1])
    except Exception as e:
        log.warning(f"  Haiku analysis failed for {url}: {e}")

    # Fallback from URL if Haiku failed
    if not parsed:
        try:
            domain = urlparse(url).hostname.replace("www.", "")
            name = domain.split(".")[0].replace("-", " ").title()
        except Exception:
            name = url
        parsed = {"name": name, "location": None, "platform": "unknown",
                  "complexity": "low", "priority": 3, "notes": ""}

    name = (parsed.get("name") or "Unknown").strip()
    location = parsed.get("location") or None
    platform = (parsed.get("platform") or "unknown").lower()
    complexity = (parsed.get("complexity") or "low").lower()
    priority = int(parsed.get("priority", 3)) if str(parsed.get("priority", "")).isdigit() else 3
    notes = parsed.get("notes") or ""

    # Step 2 — Google Places lookup
    places = google_places_lookup(name, location)

    # Step 3 — Build slug ID
    slug = slugify(name)

    return {
        "id": slug,
        "name": name,
        "website": url,
        "location": location,
        "platform": platform,
        "complexity": complexity,
        "priority": priority,
        "notes": notes,
        "status": "candidate",
        "discovered_by": "auto",
        **places,
    }


def slugify(name):
    """Convert a provider name to a URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:50]  # cap length


# ── Google Places lookup (Python port of edge function logic) ────────────────

def name_similarity(a, b):
    """Character overlap similarity between two names (alphanumeric only)."""
    a = re.sub(r"[^a-z0-9]", "", a.lower())
    b = re.sub(r"[^a-z0-9]", "", b.lower())
    if not a or not b:
        return 0.0
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    matches = sum(1 for c in shorter if c in longer)
    return matches / len(longer)


NULL_PLACES = {"google_place_id": None, "rating": None, "review_count": None}


def google_places_lookup(name, location):
    """Look up Google Places for a provider. Returns {google_place_id, rating, review_count}."""
    if not GOOGLE_PLACES_API_KEY:
        return NULL_PLACES

    query = f"{name} {location or ''}".strip()
    try:
        r = requests.get(
            f"{PLACES_API_URL}/findplacefromtext/json",
            params={
                "input": query,
                "inputtype": "textquery",
                "fields": "place_id,rating,user_ratings_total,name",
                "key": GOOGLE_PLACES_API_KEY,
            },
            timeout=10,
        )
        if not r.ok:
            return NULL_PLACES
        candidate = (r.json().get("candidates") or [None])[0]
    except Exception:
        return NULL_PLACES

    if not candidate:
        return NULL_PLACES

    # Check 1 — name similarity
    places_name = candidate.get("name", "")
    sim = name_similarity(name, places_name)
    if sim < 0.4:
        log.info(f"  Places name mismatch: searched '{name}' got '{places_name}' (sim={sim:.2f}) — rejected")
        return NULL_PLACES

    # Check 2 — review count sanity (exclude chains)
    review_count = candidate.get("user_ratings_total", 0)
    if review_count > 2000:
        log.info(f"  Places review count too high ({review_count}) for '{name}' — rejected")
        return NULL_PLACES

    # Check 3 — duplicate place_id
    place_id = candidate.get("place_id")
    if place_id:
        try:
            existing = sb_get("provider_pipeline", {
                "select": "id,name",
                "google_place_id": f"eq.{place_id}",
            })
            conflict = next(
                (row for row in existing if row.get("name", "").lower().strip() != name.lower().strip()),
                None,
            )
            if conflict:
                log.info(f"  Places ID {place_id} already assigned to '{conflict['name']}' — rejected for '{name}'")
                return NULL_PLACES
        except Exception:
            pass  # Don't reject on infra failure

    return {
        "google_place_id": place_id,
        "rating": candidate.get("rating"),
        "review_count": candidate.get("user_ratings_total"),
    }


# ── Main discovery flow ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discover new backcountry providers")
    parser.add_argument("--dry-run", action="store_true", help="Search + dedup only, no inserts")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY must be set")
        return

    # 1. Load inputs
    cloud = load_discovery_cloud()
    known_domains = load_known_domains()

    # 2. Generate search queries
    result = generate_queries(cloud)
    if not result:
        return
    queries, used_term_ids = result

    # 3. Search and collect candidates
    raw_finds = {}  # domain -> {url, name, courses, query}
    for i, q in enumerate(queries):
        log.info(f"[{i + 1}/{len(queries)}] Searching: {q['query'][:70]}")
        results = haiku_web_search(q["query"])
        for r in results:
            domain = normalize_domain(r["url"])
            if not domain:
                continue
            # Skip known providers
            if domain in known_domains:
                continue
            # Skip non-company domains
            if any(skip in domain for skip in [
                "facebook.com", "instagram.com", "youtube.com", "twitter.com",
                "linkedin.com", "tripadvisor.com", "yelp.com", "google.com",
                "wikipedia.org", "alltrails.com", "backcountryfinder.com",
                "reddit.com", "tiktok.com", "eventbrite.com",
            ]):
                continue
            # First find wins — keep the richest info
            if domain not in raw_finds:
                raw_finds[domain] = {
                    "url": r["url"],
                    "name": r.get("name", ""),
                    "courses": r.get("courses", ""),
                    "query": q["query"],
                }
        # Rate limit between searches
        time.sleep(1.5)

    log.info(f"Found {len(raw_finds)} new unique domains after dedup")

    if args.dry_run:
        log.info("=== DRY RUN — would analyse and insert these: ===")
        for domain, info in sorted(raw_finds.items()):
            log.info(f"  {domain} — {info['name']} — courses: {info['courses']}")
        return

    # 4. Analyse and insert each new find
    inserted = 0
    for domain, info in sorted(raw_finds.items()):
        log.info(f"Analysing: {info['url']} ({info['name']})")
        row = analyse_provider(info["url"])
        if not row:
            continue

        # Append course types from the search phase to notes if not already covered
        if info.get("courses"):
            search_courses = info["courses"].strip()
            if search_courses and search_courses.lower() not in (row.get("notes") or "").lower():
                existing_notes = (row.get("notes") or "").rstrip(".")
                row["notes"] = f"{existing_notes}. Courses observed: {search_courses}." if existing_notes else f"Courses observed: {search_courses}."

        # Record which query found this provider
        row["discovery_query"] = info["query"]

        if sb_insert_pipeline(row):
            inserted += 1
            log.info(f"  Inserted: {row['name']} ({row['id']}) — {row.get('location', '?')}")
            # Add domain to known set so later queries don't re-process
            known_domains.add(domain)

        # Rate limit between analysis calls
        time.sleep(2)

    # 5. Stamp last_used_at on discovery cloud terms
    update_last_used(used_term_ids)

    log.info(f"Discovery complete: {len(raw_finds)} candidates found, {inserted} new rows inserted")


if __name__ == "__main__":
    main()
