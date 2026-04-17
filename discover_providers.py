#!/usr/bin/env python3
"""
discover_providers.py — Automated provider discovery for BackcountryFinder.

Runs weekly via GitHub Actions cron. Uses Claude Haiku with web_search to find
Canadian backcountry guide companies, applies tiered cost controls, learns skip
patterns from pipeline history, and appends candidates to provider_pipeline.

Usage:
    python discover_providers.py
    python discover_providers.py --dry-run
    python discover_providers.py --dry-run --max-candidates 10
    python discover_providers.py --max-queries 50 --max-candidates 30
"""

import os
import re
import json
import time
import logging
import argparse
from collections import Counter
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

# Haiku cost estimates (USD) for logging
COST_SEARCH_CALL = 0.001   # ~$0.001 per web_search call
COST_ANALYSIS_CALL = 0.005  # ~$0.005 per analysis call (web_search + longer output)

# Query templates — {activity} and {region} are substituted
QUERY_TEMPLATES = [
    "{activity} guides {region} Canada",
    "{activity} courses {region} Canada book online",
    "{activity} tours adventures {region} Canada",
]

# ── Tier 1: Free string filters (post-search, pre-analysis) ─────────────────

# Domains to always skip — social media, aggregators, travel platforms
SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "youtube.com", "twitter.com",
    "linkedin.com", "tripadvisor.com", "yelp.com", "google.com",
    "wikipedia.org", "alltrails.com", "backcountryfinder.com",
    "reddit.com", "tiktok.com", "eventbrite.com",
    "57hours.com", "10adventures.com", "backroads.com",
    "viator.com", "getyourguide.com",
    "expedia.com", "booking.com", "airbnb.com",
}

# URL/domain keywords that indicate non-guide companies
# Checked against domain + URL path ONLY, never against provider name
SKIP_URL_KEYWORDS = [
    "shop", "store", "lodge", "firearms", "wildlife-federation",
    "university", "association", "federation",
    "directory", "listing", "resort", "hotel", "hostel",
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
    headers = _sb_headers()
    headers["Range"] = "0-49999"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, params=params)
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


def sb_increment_cloud(term_id, field):
    """Increment hit_count or skip_count on a discovery_cloud term.
    Uses raw SQL via RPC to do atomic increment. Falls back to read-then-write."""
    try:
        headers = _sb_headers()
        headers["Prefer"] = "return=minimal"
        # Read current value
        rows = sb_get("discovery_cloud", {"id": f"eq.{term_id}", "select": f"id,{field}"})
        if not rows:
            return
        current = rows[0].get(field) or 0
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/discovery_cloud?id=eq.{term_id}",
            headers=headers,
            json={field: current + 1},
        )
    except Exception:
        pass  # Non-critical — don't fail the run over stats


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

    providers = sb_get("providers", {"select": "website"})
    for p in providers:
        if p.get("website"):
            known.add(normalize_domain(p["website"]))

    pipeline = sb_get("provider_pipeline", {"select": "website"})
    for p in pipeline:
        if p.get("website"):
            known.add(normalize_domain(p["website"]))

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
    """Load active terms from discovery_cloud table."""
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


# ── Part 3: Skip pattern learning ───────────────────────────────────────────

def load_skip_patterns():
    """Learn skip patterns from provider_pipeline rows with status='skip'.
    Returns {domains: set, keywords: set}."""
    try:
        skip_rows = sb_get("provider_pipeline", {
            "status": "eq.skip",
            "select": "website,notes",
        })
    except Exception:
        log.info("Could not load skip patterns — continuing without")
        return {"domains": set(), "keywords": set()}

    # Extract domains
    domains = set()
    for r in skip_rows:
        if r.get("website"):
            domains.add(normalize_domain(r["website"]))

    # Extract keywords from notes — require 2+ skip rows to mention same keyword
    # Minimum 4 chars to filter noise
    word_counts = Counter()
    for r in skip_rows:
        notes = (r.get("notes") or "").lower()
        words = set(re.findall(r"[a-z]{4,}", notes))
        for w in words:
            word_counts[w] += 1

    # Only keywords appearing in 2+ skip rows become patterns
    keywords = {w for w, count in word_counts.items() if count >= 2}

    # Remove common words that would cause false positives
    false_positive_words = {
        "this", "that", "with", "from", "their", "they", "have", "been",
        "some", "also", "more", "most", "very", "about", "offers",
        "website", "booking", "courses", "trips", "guides", "company",
        "adventure", "outdoor", "backcountry", "mountain", "canada",
        "platform", "custom", "wordpress", "squarespace",
        "academy", "school", "training", "institute",
    }
    keywords -= false_positive_words

    log.info(f"Loaded skip patterns: {len(domains)} domains, {len(keywords)} keywords from {len(skip_rows)} skip rows")
    if keywords:
        log.info(f"  Skip keywords: {sorted(keywords)}")
    return {"domains": domains, "keywords": keywords}


def matches_skip_pattern(url, name, skip_patterns):
    """Check if a candidate matches learned skip patterns.
    URL keywords checked against domain only, never provider name.
    Returns (matched: bool, reason: str or None)."""
    domain = normalize_domain(url)

    # Check domain against skip pattern domains
    if domain in skip_patterns["domains"]:
        return True, "known skip domain"

    # Check URL/domain against skip keywords (domain + path only, not name)
    url_text = url.lower()
    for keyword in skip_patterns["keywords"]:
        if keyword in domain or keyword in url_text:
            return True, f"skip keyword: {keyword}"

    return False, None


# ── Tier 1: String-based filtering ──────────────────────────────────────────

def tier1_filter(url, known_domains):
    """Tier 1 — free string checks. Returns (pass: bool, reason: str or None)."""
    domain = normalize_domain(url)

    if not domain:
        return False, "empty domain"

    if domain in known_domains:
        return False, "known provider"

    if any(skip in domain for skip in SKIP_DOMAINS):
        return False, "skip domain"

    # Check URL keywords against domain only (never provider name)
    for kw in SKIP_URL_KEYWORDS:
        if kw in domain:
            return False, f"url keyword: {kw}"

    return True, None


# ── Generate search queries ──────────────────────────────────────────────────

def generate_queries(cloud, max_queries):
    """Generate search queries from discovery cloud, capped at max_queries."""
    activity_terms = cloud["activity"]
    location_terms = cloud["location"]
    if not activity_terms or not location_terms:
        log.warning("Discovery cloud is empty — run refresh_discovery_cloud.py first")
        return [], set()

    queries = []
    used_term_ids = set()
    cloud_rows_by_term = {(r["term"].lower(), r["type"]): r for r in cloud["rows"]}

    for activity in activity_terms:
        for region in location_terms:
            template_idx = hash(activity + region) % len(QUERY_TEMPLATES)
            q = QUERY_TEMPLATES[template_idx].format(activity=activity, region=region)
            queries.append({"query": q, "activity": activity, "region": region})
            act_row = cloud_rows_by_term.get((activity.lower(), "activity"))
            loc_row = cloud_rows_by_term.get((region.lower(), "location"))
            if act_row:
                used_term_ids.add(act_row["id"])
            if loc_row:
                used_term_ids.add(loc_row["id"])

    total_possible = len(queries)
    if len(queries) > max_queries:
        queries = queries[:max_queries]
        log.info(f"Generated {total_possible} possible queries, capped to {max_queries}")
    else:
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


# ── Platform detection via HTML signatures ──────────────────────────────────
#
# Fetches the provider homepage and scans for known booking-platform
# fingerprints. Mirrored in supabase/functions/admin-analyse-provider/index.ts
# — keep the two signature tables in sync when adding a platform.

# Ordered: first match wins. Each entry is (platform_id, list_of_regex_patterns).
# Patterns are matched against raw HTML with re.IGNORECASE.
PLATFORM_SIGNATURES = [
    ("rezdy",       [r"\.rezdy\.com", r"rezdy-online-booking", r"rezdy-modal"]),
    ("checkfront",  [r"\.checkfront\.com", r"ChfHost", r"checkfront-booking"]),
    ("zaui",        [r"\.zaui\.net", r"zaui\.js"]),
    ("fareharbor",  [r"fareharbor\.com", r"fh-iframe", r"fareharbor-dock"]),
    ("bokun",       [r"bokun\.io", r"bokunwidget", r"bokun-widget"]),
    ("peek",        [r"book\.peek\.com", r"peek-booking"]),
    ("thinkific",   [r"thinkific\.com", r"<meta[^>]+thinkific"]),
    ("shopify",     [r"cdn\.shopify\.com", r"Shopify\.theme", r"myshopify\.com"]),
    ("wix",         [r"static\.wixstatic\.com", r"wix-viewer", r"<meta[^>]+wix"]),
    ("squarespace", [r"static1\.squarespace\.com", r"Static\.SQUARESPACE_CONTEXT", r"squarespace\.com"]),
    ("woocommerce", [r"wp-content/plugins/woocommerce", r"<body[^>]+woocommerce", r"wc-ajax"]),
    ("wordpress",   [r"wp-content/", r"wp-includes/", r"<meta[^>]+WordPress"]),
]

PLATFORM_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BackcountryFinderBot/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}


def detect_platform(url: str, timeout: int = 8) -> tuple[str, str]:
    """Fetch the provider's homepage and match against PLATFORM_SIGNATURES.

    Returns (platform_id, evidence) where evidence is the first matched
    pattern (for logging / notes) or empty string on unknown/failure.
    Never raises — on any fetch error returns ('unknown', '').
    """
    try:
        r = requests.get(url, headers=PLATFORM_FETCH_HEADERS, timeout=timeout, allow_redirects=True)
        if not r.ok:
            return ("unknown", "")
        html = r.text
    except Exception as e:
        log.info(f"  platform fetch failed for {url}: {type(e).__name__}")
        return ("unknown", "")

    for platform_id, patterns in PLATFORM_SIGNATURES:
        for pat in patterns:
            if re.search(pat, html, re.IGNORECASE):
                return (platform_id, pat)
    return ("unknown", "")


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
    haiku_platform = (parsed.get("platform") or "unknown").lower()
    complexity = (parsed.get("complexity") or "low").lower()
    priority = int(parsed.get("priority", 3)) if str(parsed.get("priority", "")).isdigit() else 3
    notes = parsed.get("notes") or ""

    # Ground-truth platform detection: fetch the homepage and signature-match.
    # Haiku's web_search guess is unreliable because it infers from search
    # snippets rather than page HTML. Use detection when it finds a match;
    # fall back to Haiku's guess only on 'unknown'.
    detected_platform, evidence = detect_platform(url)
    if detected_platform != "unknown":
        platform = detected_platform
        if evidence:
            log.info(f"  platform detected: {platform} (matched '{evidence}')")
    else:
        platform = haiku_platform
        log.info(f"  platform detection inconclusive; using Haiku guess: {platform}")

    places = google_places_lookup(name, location)

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
    return s[:50]


# ── Google Places lookup (null-safe review_count) ────────────────────────────

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
    """Look up Google Places for a provider.
    Returns {google_place_id, rating, review_count, _low_reviews: bool}."""
    if not GOOGLE_PLACES_API_KEY:
        return {**NULL_PLACES, "_low_reviews": False}

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
            return {**NULL_PLACES, "_low_reviews": False}
        candidate = (r.json().get("candidates") or [None])[0]
    except Exception:
        return {**NULL_PLACES, "_low_reviews": False}

    if not candidate:
        return {**NULL_PLACES, "_low_reviews": False}

    # Check 1 — name similarity
    places_name = candidate.get("name", "")
    sim = name_similarity(name, places_name)
    if sim < 0.4:
        log.info(f"  Places name mismatch: searched '{name}' got '{places_name}' (sim={sim:.2f}) — rejected")
        return {**NULL_PLACES, "_low_reviews": False}

    # Check 2 — review count sanity (exclude chains, null-safe)
    review_count = candidate.get("user_ratings_total")  # None if not returned
    if review_count is not None and review_count > 2000:
        log.info(f"  Places review count too high ({review_count}) for '{name}' — rejected")
        return {**NULL_PLACES, "_low_reviews": False}

    # Soft signal: low review count (not a hard skip)
    low_reviews = False
    if review_count is not None and review_count < 5:
        low_reviews = True
        log.info(f"  Low review count ({review_count}) for '{name}' — flagged as low priority")

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
                return {**NULL_PLACES, "_low_reviews": False}
        except Exception:
            pass

    return {
        "google_place_id": place_id,
        "rating": candidate.get("rating"),
        "review_count": review_count,
        "_low_reviews": low_reviews,
    }


# ── Main discovery flow ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discover new backcountry providers")
    parser.add_argument("--dry-run", action="store_true", help="Search + dedup only, no inserts")
    parser.add_argument("--max-queries", type=int, default=100, help="Max search queries to fire (default 100)")
    parser.add_argument("--max-candidates", type=int, default=50, help="Max candidates to analyse (default 50)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        return
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY must be set")
        return

    # Cost tracking
    costs = {"search": 0.0, "analysis": 0.0}
    filter_stats = {"tier1": 0, "tier2": 0, "tier3_known": 0}

    # ── Step 1: Load inputs ──────────────────────────────────────────────
    cloud = load_discovery_cloud()
    known_domains = load_known_domains()
    skip_patterns = load_skip_patterns()

    # Build term lookup for hit/skip tracking
    cloud_rows_by_term = {(r["term"].lower(), r["type"]): r for r in cloud["rows"]}

    def term_ids_for_query(q):
        """Get cloud term IDs that contributed to a query."""
        ids = set()
        act_row = cloud_rows_by_term.get((q["activity"].lower(), "activity"))
        loc_row = cloud_rows_by_term.get((q["region"].lower(), "location"))
        if act_row:
            ids.add(act_row["id"])
        if loc_row:
            ids.add(loc_row["id"])
        return ids

    # ── Step 2: Generate + cap search queries ────────────────────────────
    queries, used_term_ids = generate_queries(cloud, args.max_queries)
    if not queries:
        return

    # ── Step 3: Search phase (Tier 3 — Haiku web_search calls) ───────────
    # Each query returns a list of {url, name, courses} candidates
    raw_finds = {}  # domain -> {url, name, courses, query_obj, _term_ids}

    for i, q in enumerate(queries):
        log.info(f"[{i + 1}/{len(queries)}] Searching: {q['query'][:70]}")
        results = haiku_web_search(q["query"])
        costs["search"] += COST_SEARCH_CALL
        q_term_ids = term_ids_for_query(q)

        for r in results:
            url = r.get("url", "")
            domain = normalize_domain(url)

            # Tier 1 — free string filter
            passed, reason = tier1_filter(url, known_domains)
            if not passed:
                filter_stats["tier1"] += 1
                # Increment skip_count on the terms that produced this filtered candidate
                for tid in q_term_ids:
                    sb_increment_cloud(tid, "skip_count")
                continue

            # Tier 2 — skip pattern learning
            matched, skip_reason = matches_skip_pattern(url, r.get("name", ""), skip_patterns)
            if matched:
                filter_stats["tier2"] += 1
                log.info(f"  Skipping \"{r.get('name', domain)}\" — {skip_reason}")
                for tid in q_term_ids:
                    sb_increment_cloud(tid, "skip_count")
                continue

            # Passed Tier 1 + 2 — collect as candidate
            if domain not in raw_finds:
                raw_finds[domain] = {
                    "url": url,
                    "name": r.get("name", ""),
                    "courses": r.get("courses", ""),
                    "query": q["query"],
                    "_term_ids": q_term_ids,
                }
            else:
                # Merge term IDs from additional queries that found the same domain
                raw_finds[domain]["_term_ids"] |= q_term_ids

        # Also count known-domain hits that were filtered in the dedup check
        for r in results:
            domain = normalize_domain(r.get("url", ""))
            if domain and domain in known_domains:
                filter_stats["tier3_known"] += 1

        time.sleep(1.5)

    log.info(f"Found {len(raw_finds)} new unique domains after dedup")

    # ── Cost + filter summary after search phase ─────────────────────────
    log.info(f"Tier 1 (post-search string filter): eliminated {filter_stats['tier1']} candidates (free)")
    log.info(f"Tier 2 (skip patterns): eliminated {filter_stats['tier2']} candidates (free)")
    log.info(f"Tier 3 (search phase): {len(queries)} queries fired (~${costs['search']:.2f})")

    if args.dry_run:
        log.info("=== DRY RUN — would analyse and insert these: ===")
        for domain, info in sorted(raw_finds.items()):
            log.info(f"  {domain} — {info['name']} — courses: {info['courses']}")
        log.info(f"Total estimated cost (search only): ~${costs['search']:.2f}")
        return

    # ── Step 4: Analysis phase (Tier 4 — expensive) ─────────────────────
    # Sort candidates: all get analysed but capped at max_candidates
    candidates = sorted(raw_finds.items())
    if len(candidates) > args.max_candidates:
        log.info(f"Capping analysis to {args.max_candidates} candidates (from {len(candidates)})")
        candidates = candidates[:args.max_candidates]

    # First pass: analyse all candidates, tag with _discovery_priority
    analysed = []
    for domain, info in candidates:
        log.info(f"Analysing: {info['url']} ({info['name']})")
        row = analyse_provider(info["url"])
        costs["analysis"] += COST_ANALYSIS_CALL
        if not row:
            continue

        # Tag discovery priority based on review count
        low_reviews = row.pop("_low_reviews", False)
        row["_discovery_priority"] = "low" if low_reviews else "normal"
        row["_domain"] = domain
        row["_info"] = info

        # Increment hit_count on contributing terms
        for tid in info.get("_term_ids", set()):
            sb_increment_cloud(tid, "hit_count")

        analysed.append(row)
        time.sleep(2)

    # Sort: normal priority first, then low priority
    analysed.sort(key=lambda r: (0 if r["_discovery_priority"] == "normal" else 1))

    # Insert
    inserted = 0
    for row in analysed:
        info = row.pop("_info")
        domain = row.pop("_domain")
        dp = row.pop("_discovery_priority")

        # Append course types from search phase to notes
        if info.get("courses"):
            search_courses = info["courses"].strip()
            if search_courses and search_courses.lower() not in (row.get("notes") or "").lower():
                existing_notes = (row.get("notes") or "").rstrip(".")
                row["notes"] = f"{existing_notes}. Courses observed: {search_courses}." if existing_notes else f"Courses observed: {search_courses}."

        row["discovery_query"] = info["query"]

        if sb_insert_pipeline(row):
            inserted += 1
            priority_tag = f" [low priority]" if dp == "low" else ""
            log.info(f"  Inserted: {row['name']} ({row['id']}) — {row.get('location', '?')}{priority_tag}")
            known_domains.add(domain)

    # ── Step 5: Final bookkeeping ────────────────────────────────────────
    update_last_used(used_term_ids)

    # Cost summary
    total_cost = costs["search"] + costs["analysis"]
    log.info(f"Tier 4 (analysis): {len(analysed)} candidates analysed (~${costs['analysis']:.2f})")
    log.info(f"Total estimated cost: ~${total_cost:.2f}")
    log.info(f"Discovery complete: {len(raw_finds)} candidates found, {len(analysed)} analysed, {inserted} new rows inserted")


if __name__ == "__main__":
    main()
