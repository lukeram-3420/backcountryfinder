# design-spec/

Ground-truth HTML files for progression pages. **Reproduce these exactly** — they encode every layout, spacing, color, and typography decision made during the design phase. Treat them as the spec, not as suggestions.

## Files

| File | Purpose | When to reference |
|---|---|---|
| `progression.css` | All progression-page-specific styles. Imports from existing `index.html` design tokens (`--green-dark`, `--green-light`, `--radius`, etc.). | Always — this is the stylesheet the production page imports. |
| `progression-desktop.html` | Full desktop render of an MSAA Summer Progression page. Step 2 is shown expanded so you can see the Details panel pattern. | Phase 1 — building `generate_progression_pages.py` template. |
| `progression-mobile.html` | Same DOM, rendered inside a 380px frame so the mobile breakpoint behavior is visible. CSS-only difference — the markup is identical to desktop. | Phase 1 — verifying mobile breakpoint at 480px. |
| `progression-faq-populated.html` | What the FAQ section looks like once Phase 3b populates it from `progression_questions`. Shows editorial answers, MSAA-answered with badge, and the Ask MSAA card. | Phase 3b — building the FAQ rendering path. Phase 1 only renders the empty-state version. |

## Key decisions baked into these files

1. **Capstone is dark green, not purple.** The visualizer mockups during design used purple as a design-system default. The real palette is the existing `--green-dark` / `--green-light` system. The capstone rung uses dark green border + green-light fill — visually distinct from standard rungs without being off-brand.

2. **Bundle CTA uses the same dark-green-on-green-accent treatment as the nav and search-go button.** This matches the existing site language.

3. **Difficulty level 5 (Expert) uses a one-off red tone** (`#fcebeb` bg / `#791f1f` text). Not in the global tokens — defined in `progression.css` only because it's used in exactly one place. If/when it appears elsewhere, promote to a token.

4. **All Q&As are in the rendered HTML at page load**, even when collapsed. Crawlers and LLMs need the text immediately. Collapsed state = `display: none` via `data-expanded="false"`, NOT lazy-loaded via JS-on-click.

5. **Mobile breakpoint at 480px.** Below this:
   - Page goes full-width (rounded corners removed)
   - Hero shrinks from 280px to 220px, title from 32px to 22px
   - Stat row becomes a 3-column card grid
   - Course rungs go full-width — number+title at top, button stacks below
   - Bundle cards stack 1-column instead of 2-column grid
   - Form name+email collapse to single column
   - "Collapse all" button drops below the FAQ heading

6. **Bundle radio is the whole card**, not just the radio dot. The radio dot is decoration. Click handler lives on `.prog-bundle-card`.

7. **The bundle CTA text auto-rewrites based on selection.** "Send full path inquiry to MSAA" or "Send skills bundle inquiry to MSAA". JS is in the inline `<script>` block at the bottom of the desktop file.

## What's a placeholder vs. what's real

In these design-spec files, all data is hardcoded for visual reference:

- Hero image URL → Unsplash placeholder. **Production pulls from `hero_course.image_url`** (or capstone step's course as fallback).
- Course titles, prices, durations, locations → MSAA's expected curriculum. **Production pulls from `courses` table** joined via `progression_steps`.
- Bundle math (`$1,602`, `$3,974`, `$701`) → computed from sum of course prices × discount %. **Production computes live in build script.**
- "★ 4.9 · 312 Google reviews" → hardcoded. **A later phase wires the real Google Places API fetch with weekly Supabase caching.**
- Practice gap text ("2-3 MONTHS PRACTICE") → from `progression_steps.practice_gap_text`.
- Difficulty dot rendering → derived from `progression_steps.difficulty_level` (1-5):
  - 1 → `● ○ ○ ○ ○ Beginner` with `prog-badge-difficulty-low`
  - 2 → `● ● ○ ○ ○ Novice` with `prog-badge-difficulty-low`
  - 3 → `● ● ● ○ ○ Intermediate` with `prog-badge-difficulty-mid`
  - 4 → `● ● ● ● ○ Advanced` with `prog-badge-difficulty-mid`
  - 5 → `● ● ● ● ● Expert` with `prog-badge-difficulty-high`

## What Phase 1 ships vs. defers

**Phase 1 ships:**
- Schema + build script
- Full visual reproduction of `progression-desktop.html` and `progression-mobile.html`
- Schema.org JSON-LD: BreadcrumbList, HowTo, Course-per-step, FAQPage (empty array)
- Working Details ▾/▴ toggle on rungs
- Working bundle card radio selection (CTA text rewrites)
- Working FAQ Collapse all toggle (no-op since list is empty)
- Sitemap.xml entry
- Internal link from `/msaa` provider page

**Phase 1 renders but stubs:**
- Bundle inquiry form — visible, fields work, submit is a no-op (`onsubmit="return false;"`)
- Ask MSAA form — same treatment
- FAQ list — renders the empty-state card from `progression-desktop.html`, not the populated state from `progression-faq-populated.html`

**Phase 1 explicitly does NOT include:**
- Edge functions / Resend integration
- Supabase form submission writes
- Google reviews API fetch (hardcoded "★ 4.9" for now)
- Algolia indexing of progression pages
- Homepage link to progression pages (deferred per SEO consultant)

## Token mapping reference

| Designed for | Maps to existing token | Hex |
|---|---|---|
| Capstone border + accent | `--green-dark` | `#1a2e1a` |
| Capstone fill, badge low difficulty | `--green-light` | `#eaf3de` |
| Brand accent (CTA text on dark, savings strong) | `--green-accent` | `#4ade80` |
| Mid difficulty (3-4) bg/text | `--amber-light` / `--amber-dark` | `#faeeda` / `#854f0b` |
| Expert (5) bg/text | one-off | `#fcebeb` / `#791f1f` |
| Card surface | `--bg-card` | `#ffffff` |
| Page surface | `--bg` | `#f8faf8` |
| Subdued surface (gear card, ask context box) | `--bg-secondary` | `#f2f4f2` |
| Borders | `--border` / `--border-mid` | `rgba(0,0,0,0.08)` / `rgba(0,0,0,0.13)` |
| Radius (cards, hero) | `--radius` | `12px` |
| Radius (small surfaces, inputs, gear card) | `--radius-sm` | `8px` |
| Radius (pills, badges, button shapes) | `--radius-pill` | `24px` |

## How to verify your build

After implementing Phase 1, the production page should be visually identical to opening `progression-desktop.html` directly in a browser (with real data substituted). Diff check:

1. Open `design-spec/progression-desktop.html` in Chrome
2. Open the production `/msaa/summer-progression` page in another tab
3. Compare side by side at full width (1200px+) — should be pixel-equivalent except for content differences
4. Resize both to 380px width — both should switch to mobile layout at 480px breakpoint
5. Click Details ▾ on any rung — both should expand the same way
6. Click the unselected bundle card — both should swap selection and rewrite the CTA text
