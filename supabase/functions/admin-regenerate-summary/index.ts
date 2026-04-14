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

    // Call Claude Haiku for a fresh summary
    const prompt =
      `Write a 2-sentence summary for '${title}'. ` +
      `The summary MUST start with '${title}'. Be specific to this course only. ` +
      `Description: ${String(description || "").slice(0, 400)}. ` +
      `Return only the summary text, no JSON.`;

    const anthropicRes = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")!,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: CLAUDE_MODEL,
        max_tokens: 200,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    if (!anthropicRes.ok) {
      const errText = await anthropicRes.text();
      return json({ error: `Claude API error ${anthropicRes.status}: ${errText.slice(0, 200)}` }, 500);
    }

    const anthropicData = await anthropicRes.json();
    const newSummary = (anthropicData?.content?.[0]?.text || "").trim();

    await supabase
      .from("course_summaries")
      .update({
        summary: newSummary,
        approved: false,
        pending_reason: "regenerated",
      })
      .eq("provider_id", provider_id)
      .eq("title", title);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "regenerate_summary",
      detail: { provider_id, title },
    });

    return json({ success: true, summary: newSummary });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
