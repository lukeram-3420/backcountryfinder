#!/usr/bin/env python3
"""
BackcountryFinder housekeeping — Claude reviews mapping tables and sends tidy-up report.
Run on demand via GitHub Actions.
"""

import os
import json
import requests
import logging
from datetime import datetime

SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY    = os.environ["RESEND_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTIFY_EMAIL      = "luke@backcountryfinder.com"
FROM_EMAIL        = "luke@backcountryfinder.com"
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def sb_get(table, params={}):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        params=params
    )
    r.raise_for_status()
    return r.json()


def claude_review(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": CLAUDE_MODEL, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]},
        timeout=30
    )
    return r.json()["content"][0]["text"].strip()


def send_email(subject, html):
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": f"BackcountryFinder <{FROM_EMAIL}>", "to": [NOTIFY_EMAIL], "subject": subject, "html": html}
    )
    log.info(f"Email sent: {r.status_code}")


def main():
    log.info("=== BackcountryFinder housekeeping starting ===")

    # Load all mapping tables
    activity_mappings  = sb_get("activity_mappings",  {"select": "id,title_contains,activity"})
    location_mappings  = sb_get("location_mappings",  {"select": "id,location_raw,location_canonical"})
    location_flags     = sb_get("location_flags",     {"select": "id,location_raw,provider_id,course_title,resolved,created_at", "resolved": "eq.false"})

    log.info(f"Loaded {len(activity_mappings)} activity mappings, {len(location_mappings)} location mappings, {len(location_flags)} unresolved flags")

    # Ask Claude to review
    prompt = f"""You are reviewing the mapping tables for BackcountryFinder, a backcountry outdoor experience aggregator in BC, Canada.

ACTIVITY MAPPINGS (maps course title keywords to activity types):
{json.dumps(activity_mappings, indent=2)}

LOCATION MAPPINGS (maps raw scraped location strings to canonical locations):
{json.dumps(location_mappings, indent=2)}

UNRESOLVED LOCATION FLAGS (locations the scraper couldn't normalise):
{json.dumps(location_flags, indent=2)}

Please review these tables and identify:

1. DUPLICATES — entries that map to the same thing and could be merged
2. INCONSISTENCIES — similar raw strings mapping to different canonicals when they should be the same
3. SUGGESTED FIXES — for unresolved location flags, suggest what canonical location they should map to
4. CLEANUP — any entries that look wrong, redundant, or could be improved
5. GENERAL OBSERVATIONS — anything else worth noting

Format your response as clear sections with bullet points. Be specific — include the actual values from the tables.
At the end, provide a SHORT SUMMARY of the most important actions to take."""

    log.info("Asking Claude to review mapping tables...")
    review = claude_review(prompt)
    log.info("Claude review complete")

    # Build email
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;">
      <div style="background:#1a2e1a;padding:20px 28px;border-radius:10px 10px 0 0;">
        <p style="margin:0;font-size:18px;color:#fff;font-family:Georgia,serif;">
          backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
        </p>
      </div>
      <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e8e8e8;border-top:none;">
        <p style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:#1a2e1a;display:inline-block;padding:3px 10px;border-radius:20px;margin-bottom:14px;">mapping table review</p>
        <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 6px;letter-spacing:-0.3px;">Housekeeping Report</h2>
        <p style="font-size:12px;color:#888;margin:0 0 20px;">Run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

        <div style="background:#f8f8f8;border-radius:8px;padding:16px 20px;margin-bottom:16px;font-size:13px;color:#555;">
          <strong>Tables reviewed:</strong> {len(activity_mappings)} activity mappings · {len(location_mappings)} location mappings · {len(location_flags)} unresolved flags
        </div>

        <div style="font-size:14px;color:#333;line-height:1.8;white-space:pre-wrap;">{review}</div>

        <div style="margin-top:24px;padding-top:16px;border-top:1px solid #f0f0f0;">
          <p style="font-size:12px;color:#888;">Fix mappings in Supabase → Table Editor, then trigger a scraper run to apply changes.</p>
        </div>
      </div>
    </div>"""

    # EMAILS OFF
    # send_email(f"Housekeeping report — {datetime.utcnow().strftime('%b %d, %Y')}", html)
    log.info("=== Housekeeping complete ===")


if __name__ == "__main__":
    main()
