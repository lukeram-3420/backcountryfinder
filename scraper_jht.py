import os
import re
import hashlib
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from datetime import datetime

from scraper_utils import (
    log_availability_change, log_price_change,
    stable_id_v2,
    sb_upsert, sb_patch, send_email,
    generate_summaries_batch,
    SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY, ANTHROPIC_API_KEY,
    GOOGLE_PLACES_API_KEY,
)

# ── Config ────────────────────────────────────────────────────────────────────
PROVIDER_ID   = "jht"
PROVIDER_NAME = "Jasper Hikes & Tours"
WEBSITE       = "https://www.jasperhikesandtours.ca"
BOOKING_BASE  = "https://rockyadventures.checkfront.com/reserve/"
UTM           = "utm_source=backcountryfinder&utm_medium=referral"

# Pages to scrape: (url, default_activity, default_location)
PAGES = [
    ("/ast1-course",         "mountaineering", "McBride, BC"),
    ("/ski-courses",         "skiing",         "McBride, BC"),
    ("/jasper-ski-tours",    "skiing",         "Jasper, AB"),
    ("/mcbride-sled-skiing", "skiing",         "McBride, BC"),
    ("/snowshoeing",         "snowshoeing",    "Jasper, AB"),
    ("/summer-hikes",        "hiking",         "Jasper, AB"),
    ("/winter-tours",        "guided",         "Jasper, AB"),
    ("/wildlife-tours",      "guided",         "Jasper, AB"),
]

HEADERS_SB = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates",
}

CURRENT_YEAR = datetime.today().year

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# ── Retry session ────────────────────────────────────────────────────────────
def requests_retry_session(
    retries=3,
    backoff_factor=0.5,
    status_forcelist=(500, 502, 503, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# ── Google Places ─────────────────────────────────────────────────────────────
def find_place_details(location_str):
    api_key = GOOGLE_PLACES_API_KEY
    if not api_key:
        return None, None, None
    city = location_str.split(",")[0].strip()
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
        params={
            "input": city,
            "inputtype": "textquery",
            "fields": "place_id,rating,user_ratings_total",
            "key": api_key,
        },
    )
    candidates = r.json().get("candidates", [])
    if not candidates:
        return None, None, None
    c = candidates[0]
    return c.get("place_id"), c.get("rating"), c.get("user_ratings_total")


def find_place_id_jht(location_str):
    place_id, _, _ = find_place_details(location_str)
    return place_id

# ── Date parsing ──────────────────────────────────────────────────────────────
def parse_dates_from_text(text):
    results = []
    pattern = re.compile(
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?' \
        r'\s+' \
        r'(\d{1,2})[-–](\d{1,2})(?:st|nd|rd|th)?',
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        month_str = m.group(1).lower()[:3]
        month_num = MONTH_MAP.get(month_str)
        if not month_num:
            continue
        day_start = int(m.group(2))
        day_end   = int(m.group(3))
        year = CURRENT_YEAR
        if month_num < datetime.today().month:
            year += 1
        start = f"{year}-{month_num:02d}-{day_start:02d}"
        end   = f"{year}-{month_num:02d}-{day_end:02d}"
        display = m.group(0)
        results.append((start, end, display))
    return results


def parse_spots(text):
    m = re.search(r'(\d+)\s+spots?\s+(available|left|remaining)', text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_price(text):
    m = re.search(r'\$(\d+)', text)
    return int(m.group(1)) if m and int(m.group(1)) >= 50 else None


def clean_title(title):
    title = title.strip()
    title = re.sub(
        r'^(?:[A-Za-z]{3}\.\s*\d{1,2}(?:[-–]\d{1,2})(?:st|nd|rd|th)?,?\s*\d{4})?:\s*',
        '',
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r':\s*$', '', title).strip()
    return title


def is_body_heading(title):
    t = title.strip().lower()
    if not t:
        return True
    body_prefixes = (
        'this is', 'join us', 'for a limited', 'for a limited time',
        'group sizes', 'group size', 'we do', 'you provide', 'we provide',
        'why choose us', 'private rates', 'group rates', 'rates:',
        'current events:', 'important notes:', 'frequently asked questions',
        'available', 'explore the', 'ski among', 'available 2026',
    )
    if t.startswith(body_prefixes):
        return True
    if 'current availability' in t or 'rates:' in t or '©2025' in t:
        return True
    return False


def is_full(text):
    return bool(re.search(r'\bfull\b|full!', text, re.IGNORECASE))


def has_no_availability(text):
    return bool(re.search(
        r'no availability|sold out|no dates|not available|cancelled',
        text, re.IGNORECASE,
    ))


def resolve_location_from_text(text, default_location):
    if re.search(r'mcbride|robson', text, re.IGNORECASE):
        return "McBride, BC"
    if re.search(r'jasper', text, re.IGNORECASE):
        return "Jasper, AB"
    return default_location

# ── Stable ID ─────────────────────────────────────────────────────────────────
def make_id(activity, date_str, title):
    date_sort = date_str.replace("-", "")
    h = hashlib.md5(title.encode()).hexdigest()[:6]
    return f"{PROVIDER_ID}-{activity}-{date_sort}-{h}"

# ── Availability value ────────────────────────────────────────────────────────
def avail_value(spots):
    if spots is None:
        return "open"
    if spots == 0:
        return "sold"
    if spots <= 2:
        return "critical"
    if spots <= 4:
        return "low"
    return "open"

# ── Page scraper ──────────────────────────────────────────────────────────────
def scrape_page(path, default_activity, default_location):
    url = WEBSITE + path
    session = requests_retry_session()
    r = session.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    courses = []
    full_text = soup.get_text(separator="\n")

    headings = soup.find_all(["h1", "h2", "h3"])
    sections = []
    for heading in headings:
        raw_title = heading.get_text(strip=True)
        if len(raw_title) < 5 or raw_title.lower() in ("rates:", "how to sign up:", "what's next?"):
            continue

        section_text = raw_title + "\n"
        for sib in heading.find_next_siblings():
            if sib.name in ("h1", "h2", "h3"):
                break
            section_text += sib.get_text(separator=" ", strip=True) + "\n"

        if is_body_heading(raw_title):
            if sections:
                sections[-1]["text"] += section_text
            continue

        sections.append({"title": raw_title, "text": section_text})

    for section in sections:
        title = clean_title(section["title"])
        title = re.sub(r'^[\w]+\.\s+\d{1,2}[-–]\d{1,2}(?:th|st|nd|rd)?,?\s+\d{4}:\s*', '', title).strip().rstrip(':')
        section_text = section["text"]

        if len(title) < 8 or title.lower().startswith("this is a") or title.lower() in ("rates:", "how to sign up:", "what's next?"):
            continue

        dates = parse_dates_from_text(section_text)
        if not dates and has_no_availability(section_text):
            print(f"  ✗ {title} — no availability, skipping")
            continue
        if not dates:
            continue

        price  = parse_price(section_text)
        spots  = parse_spots(section_text)
        location = resolve_location_from_text(section_text, default_location)
        sold   = is_full(title) or is_full(section_text)

        activity = default_activity
        t = title.lower()
        if "ast 1" in t or "ast1" in t:
            activity = "mountaineering"
        elif "ast 2" in t or "ast2" in t:
            activity = "mountaineering"
        elif "snowshoe" in t:
            activity = "snowshoeing"
        elif "hike" in t or "hiking" in t:
            activity = "hiking"
        elif "wildlife" in t:
            activity = "guided"
        elif "ski" in t or "sled" in t or "powder" in t or "backcountry ski" in t:
            activity = "skiing"
        elif "ice walk" in t or "canyon" in t:
            activity = "guided"

        description = re.sub(r'\s+', ' ', section_text).strip()

        for (start, end, display) in dates:
            course_id = stable_id_v2(PROVIDER_ID, start, title)
            booking_url = f"{BOOKING_BASE}?{UTM}"
            try:
                duration_days = (
                    datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")
                ).days + 1
            except Exception:
                duration_days = None

            if re.search(r'\bfull\b', title + ' ' + section_text, re.IGNORECASE):
                avail = "sold"
                active = False
            else:
                avail = avail_value(spots)
                active = True
            courses.append({
                "id":                 course_id,
                "provider_id":        PROVIDER_ID,
                "title":              title,
                "activity":           activity,
                "activity_raw":       title,
                "activity_canonical": None,  # V2: null hides from V1 frontend
                "location_raw":       location,
                "location_canonical": location,
                "date_display":       display,
                "date_sort":          start,
                "duration_days":      duration_days,
                "price":              price,
                "spots_remaining":    spots,
                "avail":              avail,
                "image_url":          None,
                "booking_url":        booking_url,
                "summary":            "",
                "description":        description,
                "badge":              None,
                "badge_canonical":    None,
                "custom_dates":       False,
                "scraped_at":         datetime.utcnow().isoformat(),
                "active":             active,
            })
            print(f"  ✓ {title} | {display} | {activity} | {location} | ${price} | {avail_value(spots)}")

    return courses

# ── Email summary ─────────────────────────────────────────────────────────────
def send_summary(upserted, skipped, errors):
    if not RESEND_API_KEY:
        return
    send_email(
        f"JHT scraper — {upserted} courses upserted",
        f"<h2>JHT Scraper Complete</h2><p>Upserted: {upserted} | Skipped: {skipped} | Errors: {errors}</p>",
        to="luke@backcountryfinder.com",
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"── {PROVIDER_NAME} scraper starting ──")

    place_id_jasper, rating, review_count = find_place_details("Jasper, AB")
    place_id_mcbride = find_place_id_jht("McBride, BC")
    print(f"Place IDs — Jasper: {place_id_jasper} | McBride: {place_id_mcbride}")

    sb_upsert("providers", [{
        "id":       PROVIDER_ID,
        "name":     PROVIDER_NAME,
        "website":  WEBSITE,
        "location": "Jasper, AB",
        "active":   True,
    }])

    if rating:
        sb_patch("providers", f"id=eq.{PROVIDER_ID}", {
            "google_place_id": place_id_jasper,
            "rating": rating,
            "review_count": review_count,
        })

    all_courses = []
    errors = 0

    for (path, activity, location) in PAGES:
        print(f"\nScraping {path}...")
        try:
            courses = scrape_page(path, activity, location)
            all_courses.extend(courses)
        except Exception as e:
            print(f"  ERROR on {path}: {e}")
            errors += 1

    if all_courses:
        seen = set()
        deduped = []
        for c in all_courses:
            key = (c['title'], c['date_sort'])
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        all_courses = deduped

        # Batch summaries — deduplicate by title
        seen_titles = {}
        unique_inputs = []
        for c in all_courses:
            if c.get("description") and c["title"] not in seen_titles:
                seen_titles[c["title"]] = c["id"]
                unique_inputs.append({"id": c["id"], "title": c["title"], "description": c.get("description", ""), "provider": PROVIDER_NAME, "activity": c.get("activity", "")})
        if unique_inputs:
            summaries = generate_summaries_batch(unique_inputs, provider_id=PROVIDER_ID)
            title_to_summary = {}
            for c in unique_inputs:
                result = summaries.get(c["id"], {})
                title_to_summary[c["title"]] = result if isinstance(result, dict) else {"summary": result, "search_document": ""}
            for c in all_courses:
                result = title_to_summary.get(c["title"], {})
                c["summary"] = result.get("summary", "") if isinstance(result, dict) else result
                c["search_document"] = result.get("search_document", "") if isinstance(result, dict) else ""

        # Strip description before upsert (not a courses column)
        for c in all_courses:
            c.pop("description", None)

        sb_upsert("courses", all_courses)
        # Log intelligence (V2 — append-only, change-detected)
        for c in all_courses:
            log_availability_change(c)
            log_price_change(c)
        print(f"\n✓ Upserted {len(all_courses)} courses")
    else:
        print("\n⚠ No courses found")

    # EMAILS OFF
    # send_summary(len(all_courses), 0, errors)
    print("── Done ──")

if __name__ == "__main__":
    main()
