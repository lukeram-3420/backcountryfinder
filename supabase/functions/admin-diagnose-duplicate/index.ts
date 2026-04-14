import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ADMIN_EMAIL = "luke@backcountryfinder.com";
const CLAUDE_MODEL = "claude-haiku-4-5-20251001";
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders },
  });
}

const SYSTEM_PROMPT =
  "You are a scraper health analyst for backcountryfinder.com. " +
  "You are diagnosing duplicate course rows in a Supabase database. " +
  "A duplicate means two or more rows share the same title and date_sort. " +
  "Your job is to determine if they are genuine duplicates (identical data, " +
  "safe to whitelist) or stable ID collisions (different data, needs scraper fix). " +
  "Respond in JSON only: {verdict: 'whitelist'|'fix_scraper', reason: string, " +
  "claude_code_prompt: string|null} " +
  "claude_code_prompt should be null for whitelist verdicts. For fix_scraper, " +
  "write a precise Claude Code instruction (2-3 sentences) describing exactly " +
  "which scraper file to look at, what the stable ID collision is, and what " +
  "field differs between the rows.";

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { status: 200, headers: corsHeaders });

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );

    const authHeader = req.headers.get("Authorization") || "";
    const token = authHeader.replace(/^Bearer\s+/i, "");
    if (!token) return json({ error: "Missing auth token" }, 401);

    const { data: userData, error: userErr } = await supabase.auth.getUser(token);
    if (userErr || !userData?.user || userData.user.email !== ADMIN_EMAIL) {
      return json({ error: "Unauthorized" }, 401);
    }
    const userEmail = userData.user.email;

    const { courses } = await req.json();
    if (!Array.isArray(courses) || courses.length === 0) {
      return json({ error: "courses array required" }, 400);
    }

    const anthropicRes = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")!,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 400,
        system: SYSTEM_PROMPT,
        messages: [
          { role: "user", content: `Here are the duplicate rows: ${JSON.stringify(courses)}` },
        ],
      }),
    });

    if (!anthropicRes.ok) {
      const errText = await anthropicRes.text();
      return json({ error: `Claude API error ${anthropicRes.status}: ${errText.slice(0, 200)}` }, 500);
    }

    const anthropicData = await anthropicRes.json();
    const raw = (anthropicData?.content?.[0]?.text || "").trim();

    let parsed: { verdict?: string; reason?: string; claude_code_prompt?: string | null } = {};
    try {
      const jsonStart = raw.indexOf("{");
      const jsonEnd = raw.lastIndexOf("}");
      parsed = JSON.parse(raw.slice(jsonStart, jsonEnd + 1));
    } catch {
      return json({ error: `Could not parse Claude response: ${raw.slice(0, 200)}` }, 500);
    }

    const verdict = parsed.verdict === "whitelist" ? "whitelist" : "fix_scraper";
    const reason = parsed.reason || "";
    const claude_code_prompt = verdict === "whitelist" ? null : (parsed.claude_code_prompt || null);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "diagnose_duplicate",
      detail: { count: courses.length, sample_title: courses[0]?.title, verdict },
    });

    return json({ verdict, reason, claude_code_prompt });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
