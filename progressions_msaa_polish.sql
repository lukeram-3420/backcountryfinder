-- Progression pages — Phase 2.5 polish: copy + provider metadata refresh.
--
-- Three text-only updates layered on top of progressions_msaa_real_catalog.sql:
--   1. provider_progressions.hero_blurb — tighter wording, "trad lead climber"
--      replaces "lead climber" since the progression's capstone (Conquer The
--      Chief) is a trad multipitch route.
--   2. provider_progressions.provider_blurb — full company name on first
--      reference ("Mountain Skills Academy") instead of the MSAA acronym.
--   3. providers.certifications — set to 'IFMGA, ACMG' so the cert line on
--      every page that reads the providers row (progression page provider
--      block, future provider hub page) surfaces it.
--
-- Idempotent. Safe to re-run. Does not touch step rows, FAQ items, or the
-- title/subtitle/hero_course_title (those are owned by
-- progressions_msaa_real_catalog.sql).

-- 1. Hero blurb: tighter copy, ends on "trad lead climber".
UPDATE provider_progressions
SET hero_blurb = 'From your first day on real rock to a multipitch route up the Stawamus Chief in Squamish. Six courses, building one skill at a time, designed to safely take you from gym climber to confident trad lead climber.',
    updated_at = now()
WHERE provider_id = 'msaa'
  AND slug        = 'summer-progression';

-- 2. Provider blurb: spell out "Mountain Skills Academy" on first reference,
--    and surface the IFMGA/ACMG certifications inline (they are the strongest
--    trust signal for a guided-mountaineering progression).
UPDATE provider_progressions
SET provider_blurb = 'Operating since 2003, Mountain Skills Academy runs courses across Squamish and Whistler with a roster of IFMGA and ACMG certified mountain guides who tailor instruction to your pace.',
    updated_at = now()
WHERE provider_id = 'msaa'
  AND slug        = 'summer-progression';

-- 3. Certifications field on the providers row. Build script renders this in
--    the provider cert line on the progression page (and any future provider
--    hub page that reads the same row). Always set — overwrites any earlier
--    value so the source of truth stays unified.
UPDATE providers
SET certifications = 'IFMGA, ACMG'
WHERE id = 'msaa';
