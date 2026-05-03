#!/usr/bin/env python3
"""
generate_progression_pages.py — Render static progression pages.

Phase 1 scope (see brief): builds one HTML file per active progression in
`provider_progressions`, plus a sitemap entry. Bundle math is computed live
from current `courses.price` values. Forms are inert (Phase 2 wires them);
FAQ list renders the empty-state card (Phase 3b populates it).

Usage:
    python generate_progression_pages.py [--dry-run]

Env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY (re-uses the same constants as
`scraper_utils.py`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

# ── Config ───────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
SITEMAP_PATH = ROOT / "sitemap.xml"
SITE_BASE = "https://backcountryfinder.com"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("generate_progression_pages")


# ── Supabase ─────────────────────────────────────────────────────────────────

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def sb_get(table: str, params: Optional[dict] = None) -> list:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        params=params or {},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


# ── Difficulty mapping ───────────────────────────────────────────────────────

DIFFICULTY = {
    1: ("● ○ ○ ○ ○", "Beginner",     "prog-badge-difficulty-low"),
    2: ("● ● ○ ○ ○", "Novice",       "prog-badge-difficulty-low"),
    3: ("● ● ● ○ ○", "Intermediate", "prog-badge-difficulty-mid"),
    4: ("● ● ● ● ○", "Advanced",     "prog-badge-difficulty-mid"),
    5: ("● ● ● ● ●", "Expert",       "prog-badge-difficulty-high"),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def utm_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    if "utm_source=" in url:
        return url
    return f"{url}{sep}utm_source=backcountryfinder&utm_medium=referral"


def fetch_provider(provider_id: str) -> Optional[dict]:
    rows = sb_get("providers", {
        "id": f"eq.{provider_id}",
        "select": "id,name,website,location,rating,review_count,google_place_id,logo_url,certifications",
        "limit": 1,
    })
    return rows[0] if rows else None


def fetch_courses_for_titles(provider_id: str, titles: list[str]) -> dict[str, list[dict]]:
    """Return {lower(title): [course rows...]} for all sessions matching the
    given (provider_id, title) pairs. Filters out flagged/inactive rows so
    pricing reflects what the live site would offer."""
    if not titles:
        return {}
    in_clause = ",".join(f'"{t.replace(chr(34), chr(92)+chr(34))}"' for t in titles)
    rows = sb_get("courses", {
        "provider_id": f"eq.{provider_id}",
        "title": f"in.({in_clause})",
        "active": "eq.true",
        "flagged": "not.is.true",
        "auto_flagged": "not.is.true",
        "select": "id,title,price,duration_days,image_url,booking_url,location_canonical,location_raw,custom_dates,date_sort,avail,summary,currency",
        "limit": "1000",
    })
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["title"].strip().lower()].append(row)
    return grouped


def pick_representative(sessions: list[dict]) -> Optional[dict]:
    """Pick the course session that drives display: cheapest positive price,
    breaking ties on earliest date_sort. Falls back to first row if none have
    a price."""
    if not sessions:
        return None
    priced = [s for s in sessions if (s.get("price") or 0) > 0]
    if priced:
        return min(priced, key=lambda s: (s["price"], s.get("date_sort") or "9999-12-31"))
    return sessions[0]


def render_meta_line(rep: dict) -> str:
    parts = []
    duration = rep.get("duration_days")
    if duration:
        days = int(duration) if float(duration).is_integer() else duration
        parts.append(f"{days} day{'s' if duration and float(duration) != 1 else ''}")
    location = rep.get("location_canonical") or rep.get("location_raw")
    if location:
        parts.append(location)
    price = rep.get("price")
    if price and price > 0:
        parts.append(f"From ${int(price):,}")
    return " · ".join(parts) if parts else "Details on request"


def provider_tile_label(provider: dict) -> str:
    pid = provider["id"].upper().replace("-", " ")
    if len(pid) <= 5:
        return pid
    parts = pid.split()
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:5]
    return pid[:4]


def provider_short_name(provider: dict) -> str:
    """Use tile-label-ish acronym as a short reference inside copy. Falls back
    to the full name if no obvious acronym fits."""
    label = provider_tile_label(provider)
    return label if 2 <= len(label) <= 6 else provider["name"]


def provider_cert_line(provider: dict) -> str:
    parts = []
    if provider.get("certifications"):
        parts.append(provider["certifications"])
    if provider.get("location"):
        parts.append(provider["location"])
    return " · ".join(parts) if parts else "Backcountry guide service"


# ── Bundle math ──────────────────────────────────────────────────────────────

def compute_bundle_math(steps: list[dict], skills_pct: float, full_pct: float) -> dict:
    capstone_idx = next((i for i, s in enumerate(steps) if s["is_capstone"]), len(steps) - 1)
    skills_steps = [s for i, s in enumerate(steps) if i != capstone_idx]

    skills_total = sum(int(s["price"]) for s in skills_steps if s.get("price"))
    full_total = sum(int(s["price"]) for s in steps if s.get("price"))

    skills_price = round(skills_total * (1 - skills_pct / 100)) if skills_total else 0
    full_price = round(full_total * (1 - full_pct / 100)) if full_total else 0

    return {
        "skills_bundle_individual_total": skills_total,
        "skills_bundle_price": skills_price,
        "skills_bundle_step_count": len(skills_steps),
        "full_path_individual_total": full_total,
        "full_path_price": full_price,
        "full_path_savings": max(0, full_total - full_price),
    }


# ── Schema.org JSON-LD ───────────────────────────────────────────────────────

def build_breadcrumb(provider: dict, progression: dict, canonical_url: str) -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE_BASE},
            {"@type": "ListItem", "position": 2, "name": provider["name"], "item": f"{SITE_BASE}/{provider['id']}"},
            {"@type": "ListItem", "position": 3, "name": progression["title"], "item": canonical_url},
        ],
    }, indent=2)


def build_howto(progression: dict, steps: list[dict]) -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": progression["title"],
        "description": progression["hero_blurb"],
        "totalTime": "P18M",
        "step": [
            {
                "@type": "HowToStep",
                "position": s["step_number"],
                "name": f"{s['rung_label']}: {s['course_title']}",
                "text": s.get("summary") or s["course_title"],
            }
            for s in steps
        ],
    }, indent=2)


def build_course_schema(provider: dict, step: dict) -> str:
    offer = None
    if step.get("price") and step["price"] > 0:
        offer = {
            "@type": "Offer",
            "price": str(int(step["price"])),
            "priceCurrency": step.get("currency") or "CAD",
            "availability": "https://schema.org/InStock",
            "url": step.get("booking_url"),
        }
    block = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": step["course_title"],
        "description": step.get("summary") or step["course_title"],
        "provider": {
            "@type": "Organization",
            "name": provider["name"],
            "url": provider.get("website"),
        },
        "educationalLevel": DIFFICULTY[step["difficulty_level"]][1],
    }
    if offer:
        block["offers"] = offer
    return json.dumps(block, indent=2)


def build_faq_empty() -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [],
    }, indent=2)


# ── Page assembly ────────────────────────────────────────────────────────────

def assemble_page(env: Environment, progression: dict) -> tuple[str, str]:
    provider = fetch_provider(progression["provider_id"])
    if not provider:
        raise RuntimeError(f"Provider not found: {progression['provider_id']}")

    raw_steps = sb_get("progression_steps", {
        "progression_id": f"eq.{progression['id']}",
        "select": "*",
        "order": "step_number.asc",
    })
    if not raw_steps:
        raise RuntimeError(f"No steps for progression {progression['id']}")

    titles = [s["course_title"] for s in raw_steps]
    courses_by_title = fetch_courses_for_titles(provider["id"], titles)

    enriched_steps: list[dict] = []
    for raw in raw_steps:
        sessions = courses_by_title.get(raw["course_title"].strip().lower(), [])
        rep = pick_representative(sessions) or {}
        dots, label, badge_class = DIFFICULTY[raw["difficulty_level"]]
        enriched_steps.append({
            **raw,
            "course_title": raw["course_title"],
            "price": int(rep["price"]) if rep.get("price") else None,
            "duration_days": rep.get("duration_days"),
            "image_url": rep.get("image_url"),
            "booking_url": utm_url(rep.get("booking_url")),
            "learn_more_url": utm_url(rep.get("booking_url")),
            "summary": rep.get("summary"),
            "currency": rep.get("currency") or "CAD",
            "meta_line": render_meta_line(rep),
            "difficulty_dots": dots,
            "difficulty_label": label,
            "difficulty_class": badge_class,
        })

    capstone = next((s for s in enriched_steps if s["is_capstone"]), enriched_steps[-1])
    hero_image_url = None
    if progression.get("hero_course_title"):
        hero_sessions = courses_by_title.get(progression["hero_course_title"].strip().lower(), [])
        hero_rep = pick_representative(hero_sessions)
        if hero_rep:
            hero_image_url = hero_rep.get("image_url")
    if not hero_image_url:
        hero_image_url = capstone.get("image_url")

    bundle_math = compute_bundle_math(
        enriched_steps,
        float(progression.get("skills_bundle_discount_pct") or 0),
        float(progression.get("full_path_discount_pct") or 0),
    )
    show_bundle = (
        (progression.get("skills_bundle_discount_pct") or 0) > 0
        or (progression.get("full_path_discount_pct") or 0) > 0
    )

    total_days = sum(int(s["duration_days"]) for s in enriched_steps if s.get("duration_days"))

    canonical_url = f"{SITE_BASE}/{provider['id']}/{progression['slug']}"
    season_label = (progression.get("season") or "summer").capitalize()

    short_name = provider_short_name(provider)
    rating_display = f"{provider['rating']:.1f}" if provider.get("rating") else "—"
    review_count_display = (
        f"{int(provider['review_count'])} Google reviews"
        if provider.get("review_count") else "no reviews yet"
    )
    reviews_url = (
        f"https://search.google.com/local/reviews?placeid={provider['google_place_id']}"
        if provider.get("google_place_id") else None
    )

    bundle_labels = {
        "skills": f"Send skills bundle inquiry to {short_name}",
        "full":   f"Send full path inquiry to {short_name}",
    }
    default_bundle_cta = (
        bundle_labels["full"]
        if (progression.get("full_path_discount_pct") or 0) > 0
        else bundle_labels["skills"]
    )

    json_ld_courses = [build_course_schema(provider, s) for s in enriched_steps]

    ctx = {
        "provider": {
            **provider,
            "tile_label": provider_tile_label(provider),
            "name_short": short_name,
            "cert_line": provider_cert_line(provider),
            "rating_display": rating_display,
            "review_count_display": review_count_display,
            "reviews_url": reviews_url,
        },
        "progression": progression,
        "steps": enriched_steps,
        "season_label": season_label,
        "hero_image_url": hero_image_url,
        "hero_pill": f"Capstone: {capstone['course_title'].split(' ')[0]}" if capstone else None,
        "total_days": total_days,
        "progression_duration": "12-18",
        "show_bundle": show_bundle,
        "default_bundle_cta": default_bundle_cta,
        "bundle_labels_json": json.dumps(bundle_labels),
        "canonical_url": canonical_url,
        "meta_title": f"{progression['title']} — {provider['name']}",
        "meta_description": progression["hero_blurb"][:155],
        "json_ld_breadcrumb": build_breadcrumb(provider, progression, canonical_url),
        "json_ld_howto": build_howto(progression, enriched_steps),
        "json_ld_courses": json_ld_courses,
        "json_ld_faq": build_faq_empty(),
        **bundle_math,
    }

    template = env.get_template("progression.html.j2")
    html = template.render(**ctx)

    output_path = ROOT / provider["id"] / progression["slug"] / "index.html"
    return str(output_path), html


# ── Sitemap ──────────────────────────────────────────────────────────────────

def write_sitemap(canonical_urls: list[str]) -> None:
    base_urls = [SITE_BASE + "/"]
    all_urls = base_urls + canonical_urls
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in all_urls:
        parts.append(f"  <url><loc>{url}</loc></url>")
    parts.append("</urlset>")
    SITEMAP_PATH.write_text("\n".join(parts) + "\n", encoding="utf-8")
    log.info(f"Wrote sitemap with {len(all_urls)} URL(s) → {SITEMAP_PATH}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Render but do not write files.")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        log.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
        return 1

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )

    try:
        progressions = sb_get("provider_progressions", {
            "active": "eq.true",
            "select": "*",
            "order": "provider_id.asc,slug.asc",
        })
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            log.warning("provider_progressions table missing — run progressions_schema.sql first.")
            return 0
        raise

    if not progressions:
        log.info("No active progressions to render.")
        write_sitemap([])
        return 0

    canonical_urls: list[str] = []
    for prog in progressions:
        try:
            output_path, html = assemble_page(env, prog)
        except Exception as e:
            log.error(f"Failed to render {prog['provider_id']}/{prog['slug']}: {e}")
            continue
        canonical_urls.append(f"{SITE_BASE}/{prog['provider_id']}/{prog['slug']}")
        if args.dry_run:
            log.info(f"[dry-run] Would write {output_path} ({len(html):,} bytes)")
            continue
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")
        log.info(f"Wrote {output_path} ({len(html):,} bytes)")

    if not args.dry_run:
        write_sitemap(canonical_urls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
