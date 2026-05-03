-- Progression pages — Phase 1 schema
--
-- Two tables driving the static progression page renderer
-- (see generate_progression_pages.py).
--
-- DEVIATIONS FROM PHASE 1 BRIEF
-- -----------------------------
-- 1. The brief specified `uuid REFERENCES providers(id)` / `uuid REFERENCES
--    courses(id)`. In reality both `providers.id` and `courses.id` are `text`
--    (providers.id is a short slug like 'msaa'; courses.id is a V2 stable id
--    like 'msaa-2026-04-15-a1b2c3d4'). A `uuid` FK column would not be
--    type-compatible. We use `text` for the provider FK and reference courses
--    by (provider_id, course_title) instead of by courses.id, because:
--      - V2 course IDs encode date_sort, so a single MSAA course title has
--        many session rows and the IDs churn as dates pass.
--      - The build script aggregates all sessions matching (provider_id, title)
--        and uses the cheapest active session for price / image / duration.
--    The brief's intent ("course titles, prices, durations from existing
--    courses table") is preserved; only the join key changes.
-- 2. The brief's seed used `WHERE id_slug = 'msaa'`; there is no `id_slug`
--    column. Replaced with `'msaa'` literal since `providers.id` is the slug.

CREATE TABLE IF NOT EXISTS provider_progressions (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id                 text NOT NULL REFERENCES providers(id),
  slug                        text NOT NULL,
  title                       text NOT NULL,
  subtitle                    text,
  hero_blurb                  text NOT NULL,
  provider_blurb              text NOT NULL,
  hero_course_title           text,
  skills_bundle_discount_pct  numeric NOT NULL DEFAULT 0,
  full_path_discount_pct      numeric NOT NULL DEFAULT 0,
  season                      text NOT NULL CHECK (season IN ('summer','winter')),
  active                      boolean NOT NULL DEFAULT false,
  created_at                  timestamptz NOT NULL DEFAULT now(),
  updated_at                  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider_id, slug)
);

CREATE TABLE IF NOT EXISTS progression_steps (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  progression_id      uuid NOT NULL REFERENCES provider_progressions(id) ON DELETE CASCADE,
  step_number         int NOT NULL,
  course_title        text NOT NULL,
  rung_label          text NOT NULL,
  is_capstone         boolean NOT NULL DEFAULT false,
  practice_gap_text   text,
  difficulty_level    int NOT NULL CHECK (difficulty_level BETWEEN 1 AND 5),
  prerequisites_text  text,
  gear_text           text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (progression_id, step_number)
);

CREATE INDEX IF NOT EXISTS idx_provider_progressions_active
  ON provider_progressions (provider_id) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_progression_steps_order
  ON progression_steps (progression_id, step_number);

-- ────────────────────────────────────────────────────────────────────────────
-- Seed: MSAA Summer Progression
-- ────────────────────────────────────────────────────────────────────────────
-- Inserted as active=false until Luke confirms with MSAA. Toggle to true to
-- preview locally; flip back to false before pitching MSAA.

INSERT INTO provider_progressions (
  provider_id, slug, title, subtitle, hero_blurb, provider_blurb,
  hero_course_title, skills_bundle_discount_pct, full_path_discount_pct,
  season, active
) VALUES (
  'msaa',
  'summer-progression',
  'How to become an alpine climber',
  'Gym to Bugaboos in five courses',
  'From your first day on real rock to leading a multipitch route in the Bugaboos. Five courses, building one skill at a time, designed to take you from gym climber to self-sufficient alpine climber.',
  'Operating since 2003, MSAA runs courses across Squamish, Whistler, and the Bugaboos with a roster of certified mountain guides who tailor instruction to your pace.',
  NULL,
  10,
  15,
  'summer',
  false
)
ON CONFLICT (provider_id, slug) DO NOTHING;

-- Steps. course_title strings must match `courses.title` exactly (build script
-- joins on (provider_id, lower(title)) for case-tolerance). Update titles as
-- MSAA confirms their final curriculum naming.
WITH p AS (
  SELECT id FROM provider_progressions WHERE provider_id = 'msaa' AND slug = 'summer-progression'
)
INSERT INTO progression_steps (
  progression_id, step_number, course_title, rung_label,
  is_capstone, practice_gap_text, difficulty_level, prerequisites_text, gear_text
)
SELECT p.id, s.step_number, s.course_title, s.rung_label,
       s.is_capstone, s.practice_gap_text, s.difficulty_level, s.prerequisites_text, s.gear_text
FROM p, (VALUES
  (1, 'Intro to outdoor rock climbing',     'Foundation',         false, NULL,                     1, NULL,            'Climbing shoes — everything else provided.'),
  (2, 'Anchor building & top-rope setup',   'Skill builder',      false, '2-3 MONTHS PRACTICE',    2, 'After: Step 1', 'Harness, climbing shoes, helmet. Personal anchor system if you have one.'),
  (3, 'Intro to sport lead climbing',       'Lead climbing',      false, '3-6 MONTHS PRACTICE',    3, 'After: Steps 1-2', 'Harness, climbing shoes, helmet.'),
  (4, 'Intro to trad & multipitch climbing','Trad & multipitch',  false, '3-6 MONTHS PRACTICE',    4, 'After: Steps 1-3', 'Harness, climbing shoes, helmet, trad rack if you have one.'),
  (5, 'Bugaboos alpine climbing camp',      'Capstone',           true,  '6-12 MONTHS PRACTICE',   5, 'After: Steps 1-4', 'Full alpine kit. See course page for the complete list — MSAA can also help advise.')
) AS s(step_number, course_title, rung_label, is_capstone, practice_gap_text, difficulty_level, prerequisites_text, gear_text)
ON CONFLICT (progression_id, step_number) DO NOTHING;
