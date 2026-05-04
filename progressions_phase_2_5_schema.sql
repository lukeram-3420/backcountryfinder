-- Progression pages — Phase 2.5: editorial FAQ seed
--
-- Adds `faq_items jsonb` to `provider_progressions` and seeds MSAA's row with
-- 7 editorial Q&As. Build script renders these inline; FAQPage JSON-LD picks
-- them up automatically.
--
-- Schema is generic across providers — `source` is 'editorial' | 'provider'
-- (not provider-specific). The "Answered by {provider}" badge text is
-- derived from the joined providers row at render time.
--
-- Item shape:
--   {
--     question      text,
--     answer        html (paragraphs, occasional <strong>),
--     source        'editorial' | 'provider',
--     reviewed_date ISO date string, only when source='provider',
--     display_order 1-indexed int
--   }
--
-- Idempotent: safe to re-run. The ALTER is gated by IF NOT EXISTS; the seed
-- UPDATE only writes when faq_items is the empty default so a manual edit in
-- Supabase Studio is preserved on re-runs.

ALTER TABLE provider_progressions
  ADD COLUMN IF NOT EXISTS faq_items jsonb NOT NULL DEFAULT '[]'::jsonb;

UPDATE provider_progressions
SET faq_items = '[
  {
    "question": "How long does it take to go from gym climber to alpine climber?",
    "answer": "<p>Most people complete this progression over 12 to 18 months, depending on how often they get outside between courses and how quickly they accumulate mileage. The five courses themselves only take 13 days of instruction — the real work happens in the practice gaps between them, where you build the muscle memory and judgement that the next course assumes.</p><p>Compressed timelines are possible but not ideal. MSAA recommends spending at least 2 to 3 months top-roping outdoors before jumping into anchor building, and 6 to 12 months of trad multipitch experience before the Bugaboos capstone.</p>",
    "source": "editorial",
    "display_order": 1
  },
  {
    "question": "Do I need to take all five courses with MSAA?",
    "answer": "<p>No, but there are real benefits to staying with one provider. MSAA''s progression is designed so each course assumes the skills and language taught in the previous one — guides build on what the last guide covered instead of re-teaching baseline material.</p><p>If you do mix providers, expect some overlap on the first day of each course as a new guide assesses your skills. Not a bad thing, just less efficient than a continuous progression. The bundle pricing also disappears if you mix.</p>",
    "source": "editorial",
    "display_order": 2
  },
  {
    "question": "What''s the difference between sport lead and trad climbing?",
    "answer": "<p>Sport lead climbing uses pre-placed bolts in the rock that you clip your rope to as you climb. The protection is fixed — you focus on movement, clipping technique, and managing falls.</p><p>Trad climbing means placing your own removable protection (cams, nuts, slings) into cracks and features as you go. It''s a bigger skill set: gear placement, anchor building, route reading. Most outdoor climbing in the BC alpine is trad, which is why this progression includes both.</p>",
    "source": "editorial",
    "display_order": 3
  },
  {
    "question": "What climbing fitness do I need for the Bugaboos capstone?",
    "answer": "<p>You''ll want to comfortably top-rope 5.8 trad cracks outside while wearing a small pack, and be able to sustain 6 to 8 hours of moving over alpine terrain on consecutive days. The Bugaboos involve glacier approaches, scrambling on talus and moraine, and multi-pitch granite — none of it is technically extreme, but the cumulative load is real.</p><p>If you can finish a 25km mountain hike with 1,500m of elevation gain and still have something left in the tank, you''re in the right zone. Trail running, weighted hiking, and outdoor climbing days are the best preparation in the months leading up to camp.</p>",
    "source": "editorial",
    "display_order": 4
  },
  {
    "question": "What gear should I buy versus rent?",
    "answer": "<p><strong>Buy:</strong> harness, helmet, climbing shoes. These are personal-fit items you''ll use across every course and beyond.</p><p><strong>Rent or borrow:</strong> trad rack, double ropes, alpine boots, glacier gear (crampons, ice axe, harness with belay loops rated for glacier travel). These are expensive, progression-specific, and rentable from MEC, Squamish Adventure Centre, or directly through MSAA.</p><p>One thing worth buying earlier than you''d expect: a personal anchor system. Cheap, lasts forever, and you''ll reach for it in every course from anchor building onward.</p>",
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
    "answer": "<p>Yes. MSAA regularly works with climbers in their 50s, 60s, and beyond. Fitness matters more than age, especially for the Bugaboos capstone — but the technical skills don''t get harder with age, just sometimes less natural-feeling.</p><p>If you''re starting later, expect to lean harder on the practice gaps between courses. Mileage on outdoor rock at 50 is just as valuable as mileage at 25 — there''s no shortcut around it for any age.</p>",
    "source": "editorial",
    "display_order": 7
  }
]'::jsonb
WHERE provider_id = 'msaa'
  AND slug = 'summer-progression'
  AND faq_items = '[]'::jsonb;
