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

    const { provider_id, title, description } = await req.json();

    // Fetch location from a matching course for richer context
    const { data: courseRow } = await supabase
      .from("courses")
      .select("location_canonical")
      .eq("provider_id", provider_id)
      .eq("title", title)
      .limit(1)
      .single();
    const location = courseRow?.location_canonical || "";

    // Call Claude Haiku for two-field summary (Phase 1 V2)
    const prompt =
      `Given this course, generate two outputs:\n\n` +
      `1. display_summary: 2 sentences for the course card. ` +
      `Do not repeat the title or location (shown separately on card). ` +
      `Focus on the experience, what participants learn, who it's for. ` +
      `Use plain language, no marketing fluff. Do not use the word "perfect". Write in third person.\n\n` +
      `2. search_document: Comprehensive keyword text for Algolia search indexing. ` +
      `Include: title, location, certification body, skill level, ` +
      `terrain type, equipment, synonyms, all relevant search terms. ` +
      `Write as space-separated keywords, not sentences. Never shown to users.\n\n` +
      `Title: ${title}\nLocation: ${location}\n` +
      `Description: ${String(description || "").slice(0, 400)}\n\n` +
      `Respond with valid JSON only:\n{"display_summary": "...", "search_document": "..."}`;

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
        messages: [{ role: "user", content: prompt }],
      }),
    });

    if (!anthropicRes.ok) {
      const errText = await anthropicRes.text();
      return json({ error: `Claude API error ${anthropicRes.status}: ${errText.slice(0, 200)}` }, 500);
    }

    const anthropicData = await anthropicRes.json();
    const rawText = (anthropicData?.content?.[0]?.text || "").trim();
    let newSummary = "";
    let newSearchDoc = "";
    try {
      const parsed = JSON.parse(rawText);
      newSummary = (parsed.display_summary || "").trim();
      newSearchDoc = (parsed.search_document || "").trim();
    } catch {
      // Fallback: treat entire response as display_summary (backward compat)
      newSummary = rawText;
    }

    const updatePayload: Record<string, unknown> = {
      summary: newSummary,
      approved: false,
      pending_reason: "regenerated",
    };
    if (newSearchDoc) {
      updatePayload.search_document = newSearchDoc;
    }

    await supabase
      .from("course_summaries")
      .update(updatePayload)
      .eq("provider_id", provider_id)
      .eq("title", title);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "regenerate_summary",
      detail: { provider_id, title },
    });

    return json({ success: true, summary: newSummary, search_document: newSearchDoc });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
