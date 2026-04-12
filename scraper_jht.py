import os
import re
import hashlib
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from datetime import datetime
from anthropic import Anthropic

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

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_KEY   = os.environ.get("RESEND_API_KEY", "")
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

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

# ── Supabase helpers ──────────────────────────────────────────────────────────
def sb_upsert(table, rows):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS_SB, "Prefer": "resolution=merge-duplicates"},
        json=rows,
    )
    if not r.ok:
        print("ERROR upserting to Supabase:", r.status_code)
        print(r.text)
    r.raise_for_status()


def sb_patch(table, col, val, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{col}=eq.{val}",
        headers=HEADERS_SB,
        json=data,
    )
    r.raise_for_status()


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
def find_place_id(location_str):
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        return None
    city = location_str.split(",")[0].strip()
    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
        params={"input": city, "inputtype": "textquery", "fields": "place_id", "key": api_key},
    )
    candidates = r.json().get("candidates", [])
    return candidates[0]["place_id"] if candidates else None

# ── Date parsing ──────────────────────────────────────────────────────────────
def parse_dates_from_text(text):
    """
    Extract date ranges from patterns like:
      "Feb. 24-25th"  → ["2026-02-24", "2026-02-25"]
      "Feb. 17-20th"  → ["2026-02-17", "2026-02-20"]  (start date used)
      "Mar. 1-2"      → ["2026-03-01", "2026-03-02"]
    Returns list of (start_date_str, end_date_str, display_str) tuples.
    """
    results = []
    # Pattern: Month. D-Dth or Month D-D
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
        # Figure out year — if month already passed, use next year
        year = CURRENT_YEAR
        if month_num < datetime.today().month:
            year += 1
        start = f"{year}-{month_num:02d}-{day_start:02d}"
        end   = f"{year}-{month_num:02d}-{day_end:02d}"
        display = m.group(0)
        results.append((start, end, display))
    return results


def parse_spots(text):
    """Extract spots remaining from text like '4 spots available' or '3 SPOTS LEFT'."""
    m = re.search(r'(\d+)\s+spots?\s+(available|left|remaining)', text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_price(text):
    """Extract first dollar amount found, e.g. '$299' or '$349 + GST'."""
    m = re.search(r'\$(\d+)', text)
    return int(m.group(1)) if m else None


def has_no_availability(text):
    """Detect explicit 'no availability' language."""
    return bool(re.search(
        r'no availability|sold out|no dates|not available|cancelled',
        text, re.IGNORECASE,
    ))


def resolve_location_from_text(text, default_location):
    """Override location if McBride/Robson mentioned near a date."""
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

# ── Summary generation ────────────────────────────────────────────────────────
def generate_summary(title, description):
    client = Anthropic(api_key=ANTHROPIC_KEY)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content":
                f"Write a 1-sentence summary (max 20 words) for this outdoor course: '{title}'. "
                f"Context: {description[:300]}"
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        return description[:100] if description else title

# ── Page scraper ──────────────────────────────────────────────────────────────
def scrape_page(path, default_activity, default_location):
    url = WEBSITE + path
    session = requests_retry_session()
    r = session.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Get all text blocks — Squarespace puts content in <p>, <h2>, <h3>, <strong>
    # We walk section by section using headings as course delimiters
    courses = []
    full_text = soup.get_text(separator="\n")

    # Split on h2/h3 headings to get course sections
    headings = soup.find_all(["h2", "h3"])
    for heading in headings:
        title = heading.get_text(strip=True)
        if len(title) < 5 or title.lower() in ("rates:", "how to sign up:", "what's next?"):
            continue

        # Gather text from siblings until next heading
        section_text = title + "\n"
        for sib in heading.find_next_siblings():
            if sib.name in ("h2", "h3"):
                break
            section_text += sib.get_text(separator=" ", strip=True) + "\n"

        # Skip if no dates found and explicit no-availability
        dates = parse_dates_from_text(section_text)
        if not dates and has_no_availability(section_text):
            print(f"  ✗ {title} — no availability, skipping")
            continue
        if not dates:
            # No dates means on-request only — skip (no concrete upcoming date to show)
            continue

        price  = parse_price(section_text)
        spots  = parse_spots(section_text)
        location = resolve_location_from_text(section_text, default_location)

        # Determine activity from title
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
        summary = generate_summary(title, description)

        for (start, end, display) in dates:
            course_id = make_id(activity, start, title)
            booking_url = f"{BOOKING_BASE}?{UTM}"
            try:
                duration_days = (
                    datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")
                ).days + 1
            except Exception:
                duration_days = None

            courses.append({
                "id":                 course_id,
                "provider_id":        PROVIDER_ID,
                "title":              title,
                "activity":           activity,
                "activity_raw":       title,
                "activity_canonical": activity,
                "location_raw":       location,
                "location_canonical": location,
                "date_display":       display,
                "date_sort":          start,
                "duration_days":      duration_days,
                "price":              price,
                "spots_remaining":    spots,
                "avail":              avail_value(spots),
                "image_url":          None,
                "booking_url":        booking_url,
                "summary":            summary,
                "badge":              None,
                "badge_canonical":    None,
                "custom_dates":       False,
                "scraped_at":         datetime.utcnow().isoformat(),
                "active":             avail_value(spots) != "sold",
            })
            print(f"  ✓ {title} | {display} | {activity} | {location} | ${price} | {avail_value(spots)}")

    return courses

# ── Email summary ─────────────────────────────────────────────────────────────
def send_summary(upserted, skipped, errors):
    if not RESEND_KEY:
        return
    requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
        json={
            "from": "scraper@backcountryfinder.com",
            "to": ["luke@backcountryfinder.com"],
            "subject": f"JHT scraper — {upserted} courses upserted",
            "html": f"<h2>JHT Scraper Complete</h2><p>Upserted: {upserted} | Skipped: {skipped} | Errors: {errors}</p>",
        },
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"── {PROVIDER_NAME} scraper starting ──")

    # Upsert provider row
    place_id_jasper  = find_place_id("Jasper, AB")
    place_id_mcbride = find_place_id("McBride, BC")
    print(f"Place IDs — Jasper: {place_id_jasper} | McBride: {place_id_mcbride}")

    sb_upsert("providers", [{
        "id":       PROVIDER_ID,
        "name":     PROVIDER_NAME,
        "website":  WEBSITE,
        "location": "Jasper, AB",
        "active":   True,
    }])

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
        sb_upsert("courses", all_courses)
        print(f"\n✓ Upserted {len(all_courses)} courses")
    else:
        print("\n⚠ No courses found")

    send_summary(len(all_courses), 0, errors)
    print("── Done ──")

if __name__ == "__main__":
    main()
