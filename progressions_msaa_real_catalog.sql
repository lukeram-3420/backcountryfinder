-- Progression pages — Phase 2.5 followup: align MSAA seed with real catalog.
--
-- The Phase 1 seed targeted a hypothetical "gym to Bugaboos" alpine climbing
-- progression. MSAA's actual Rezdy catalog covers Squamish/Whistler rock
-- climbing only — no Bugaboos camp, no standalone "anchor building" course.
-- This migration rewrites the seed to match what's bookable today:
--
--   1. Rock Climbing Taster              (Foundation)
--   2. Intro to Outdoor Rock 2 Day       (Skill builder — anchors folded in)
--   3. Intro to Sport Climbing & Leading (Lead climbing)
--   4. Trad Lead & Progression           (Trad)
--   5. Intro to Multi-Pitch Climbing     (Multipitch)
--   6. Conquer the Chief                 (Capstone — Stawamus Chief route)
--
-- Bundle math is computed live at build time from current `courses.price`,
-- so totals/savings don't need recalculation here — they refresh on the
-- next `generate_progression_pages.py` run.
--
-- Title casing must match `courses.title` exactly (build script joins on
-- lowercase title for case-tolerance, but exact ampersand/spelling matters
-- elsewhere). All six titles verified against the May 2026 MSAA scrape.
--
-- Idempotent: safe to re-run. Step replacement uses DELETE+INSERT inside
-- one statement window so the build script never sees a partial set.

-- 1. Update progression header (title, subtitles, blurbs, hero image source)
UPDATE provider_progressions
SET title              = 'How to become an outdoor rock climber',
    subtitle           = 'Gym to the Stawamus Chief in six courses',
    hero_blurb         = 'From your first day on real rock to leading a multipitch route up the Stawamus Chief in Squamish. Six courses, building one skill at a time, designed to take you from gym climber to confident outdoor lead climber.',
    provider_blurb     = 'Operating since 2003, MSAA runs courses across Squamish and Whistler with a roster of certified mountain guides who tailor instruction to your pace.',
    hero_course_title  = 'Conquer the Chief',
    updated_at         = now()
WHERE provider_id = 'msaa'
  AND slug        = 'summer-progression';

-- 2. Replace step rows (5 → 6)
WITH p AS (
  SELECT id FROM provider_progressions
  WHERE provider_id = 'msaa' AND slug = 'summer-progression'
),
del AS (
  DELETE FROM progression_steps
  WHERE progression_id IN (SELECT id FROM p)
  RETURNING 1
)
INSERT INTO progression_steps (
  progression_id, step_number, course_title, rung_label,
  is_capstone, practice_gap_text, difficulty_level, prerequisites_text, gear_text
)
SELECT p.id, s.step_number, s.course_title, s.rung_label,
       s.is_capstone, s.practice_gap_text, s.difficulty_level, s.prerequisites_text, s.gear_text
FROM p,
     (SELECT count(*) FROM del) _force_eval,
     (VALUES
  (1, 'Rock Climbing Taster',              'Foundation',        false, NULL,                    1, NULL,               'Climbing shoes — everything else provided.'),
  (2, 'Intro to Outdoor Rock 2 Day',       'Skill builder',     false, '2-3 MONTHS PRACTICE',   2, 'After: Step 1',    'Harness, climbing shoes, helmet. Personal anchor system if you have one.'),
  (3, 'Intro to Sport Climbing & Leading', 'Lead climbing',     false, '3-6 MONTHS PRACTICE',   3, 'After: Steps 1-2', 'Harness, climbing shoes, helmet.'),
  (4, 'Trad Lead & Progression',           'Trad',              false, '3-6 MONTHS PRACTICE',   4, 'After: Steps 1-3', 'Harness, climbing shoes, helmet, trad rack if you have one.'),
  (5, 'Intro to Multi-Pitch Climbing',     'Multipitch',        false, '3-6 MONTHS PRACTICE',   4, 'After: Steps 1-4', 'Full lead rack and a partner you climb with regularly.'),
  (6, 'Conquer the Chief',                 'Capstone',          true,  '6-12 MONTHS PRACTICE',  5, 'After: Steps 1-5', 'Full lead rack. See course page for the complete list — MSAA can also help advise.')
) AS s(step_number, course_title, rung_label, is_capstone, practice_gap_text, difficulty_level, prerequisites_text, gear_text);

-- 3. Rewrite FAQ items that referenced the Bugaboos / alpine progression.
--    Items #2, #3, #5, #6 carry over unchanged. Items #1 and #4 are rewritten
--    to reference the Stawamus Chief capstone. Item #7 (over-50 fitness) loses
--    its Bugaboos line and now references multipitch generally.
UPDATE provider_progressions
SET faq_items = '[
  {
    "question": "How long does it take to go from gym climber to leading on the Stawamus Chief?",
    "answer": "<p>Most people complete this progression over 12 to 18 months, depending on how often they get outside between courses and how quickly they accumulate mileage. The six courses themselves only take a couple of weeks of instruction — the real work happens in the practice gaps between them, where you build the muscle memory and judgement that the next course assumes.</p><p>Compressed timelines are possible but not ideal. MSAA recommends spending at least 2 to 3 months top-roping outdoors before jumping into anchor building, and 6 to 12 months of trad and multipitch experience before tackling Conquer the Chief.</p>",
    "source": "editorial",
    "display_order": 1
  },
  {
    "question": "Do I need to take all six courses with MSAA?",
    "answer": "<p>No, but there are real benefits to staying with one provider. MSAA''s progression is designed so each course assumes the skills and language taught in the previous one — guides build on what the last guide covered instead of re-teaching baseline material.</p><p>If you do mix providers, expect some overlap on the first day of each course as a new guide assesses your skills. Not a bad thing, just less efficient than a continuous progression. The bundle pricing also disappears if you mix.</p>",
    "source": "editorial",
    "display_order": 2
  },
  {
    "question": "What''s the difference between sport lead and trad climbing?",
    "answer": "<p>Sport lead climbing uses pre-placed bolts in the rock that you clip your rope to as you climb. The protection is fixed — you focus on movement, clipping technique, and managing falls.</p><p>Trad climbing means placing your own removable protection (cams, nuts, slings) into cracks and features as you go. It''s a bigger skill set: gear placement, anchor building, route reading. Most multipitch climbing on the Stawamus Chief is trad, which is why this progression includes both.</p>",
    "source": "editorial",
    "display_order": 3
  },
  {
    "question": "What climbing fitness do I need for Conquer the Chief?",
    "answer": "<p>You''ll want to comfortably top-rope 5.8 trad cracks outside while wearing a small pack, and be able to sustain a full day of moving on rock — the Chief is roughly 8 to 10 pitches and a long descent. The climbing isn''t technically extreme on the trade routes, but the cumulative load of approach, climbing, and walk-off is real.</p><p>If you can finish a full Squamish multipitch day on a 5.7-5.8 like Snake or Skywalker and still have something in the tank for the descent, you''re in the right zone. Trail running, weighted hiking, and outdoor climbing days are the best preparation in the months leading up.</p>",
    "source": "editorial",
    "display_order": 4
  },
  {
    "question": "What gear should I buy versus rent?",
    "answer": "<p><strong>Buy:</strong> harness, helmet, climbing shoes. These are personal-fit items you''ll use across every course and beyond.</p><p><strong>Rent or borrow:</strong> trad rack, double ropes, multipitch gear. These are expensive, progression-specific, and rentable from MEC, Squamish Adventure Centre, or directly through MSAA.</p><p>One thing worth buying earlier than you''d expect: a personal anchor system. Cheap, lasts forever, and you''ll reach for it in every course from anchor building onward.</p>",
    "source": "editorial",
    "display_order": 5
  },
  {
    "question": "Can I book the courses individually instead of as a bundle?",
    "answer": "<p>Absolutely. Every course on this progression is also available as a standalone booking through MSAA — the bundle just gets you a discount for committing to the full path upfront.</p><p>Booking individually makes sense if you''re not sure how far you want to go yet, or if your fitness or schedule might change. The trade-off is the bundle pricing and the certainty of a guided path. Most people who finish the full progression say they wish they''d committed sooner.</p>",
    "source": "editorial",
    "display_order": 6
  },
  {
    "question": "Is this progression suitable for someone over 50?",
    "answer": "<p>Yes. MSAA regularly works with climbers in their 50s, 60s, and beyond. Fitness matters more than age, especially for a long multipitch day on the Chief — but the technical skills don''t get harder with age, just sometimes less natural-feeling.</p><p>If you''re starting later, expect to lean harder on the practice gaps between courses. Mileage on outdoor rock at 50 is just as valuable as mileage at 25 — there''s no shortcut around it for any age.</p>",
    "source": "editorial",
    "display_order": 7
  }
]'::jsonb,
    updated_at = now()
WHERE provider_id = 'msaa'
  AND slug        = 'summer-progression';
