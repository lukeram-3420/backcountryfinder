// admin-save-summary
// Initiative 3 — admin save of a corrected summary from the Summary Review tab.
// Writes the edited text to courses.summary + courses.search_document,
// clears any outstanding auto_flag or user flag on the course, and records
// a (provider_id, md5(summary)) row in validator_summary_exceptions so the
// validator's bleed check skips this text on future runs.
//
// Input: { course_id, summary, search_document?, reason }
//   reason in {'summary_bleed' | 'bad_description' | 'generation_failed'}
//
// Output: { ok: true, summary_hash }
import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { crypto } from "https://deno.land/std@0.168.0/crypto/mod.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ADMIN_EMAIL = "luke@backcountryfinder.com";
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

async function md5Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("MD5", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { status: 200, headers: corsHeaders });

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    const authHeader = req.headers.get("Authorization") || "";
    const token = authHeader.replace(/^Bearer\s+/i, "");
    if (!token) return json({ error: "Missing auth token" }, 401);

    const { data: userData, error: userErr } = await supabase.auth.getUser(token);
    if (userErr || !userData?.user || userData.user.email !== ADMIN_EMAIL) {
      return json({ error: "Unauthorized" }, 401);
    }
    const userEmail = userData.user.email;

    const {
      course_id,
      summary,
      search_document,
      reason,
    } = await req.json();

    if (!course_id || !summary || !reason) {
      return json({ error: "course_id, summary, and reason are required" }, 400);
    }
    const allowedReasons = ["summary_bleed", "bad_description", "generation_failed"];
    if (!allowedReasons.includes(reason)) {
      return json({ error: `reason must be one of ${allowedReasons.join(", ")}` }, 400);
    }

    const trimmedSummary = String(summary).trim();
    if (!trimmedSummary) {
      return json({ error: "summary cannot be empty" }, 400);
    }
    const summary_hash = await md5Hex(trimmedSummary);

    // Fetch provider_id for the course so the exception row is keyed correctly.
    const { data: courseRows, error: fetchErr } = await supabase
      .from("courses")
      .select("provider_id")
      .eq("id", course_id)
      .limit(1);
    if (fetchErr) return json({ error: fetchErr.message }, 500);
    const provider_id: string | undefined = courseRows?.[0]?.provider_id;
    if (!provider_id) return json({ error: "course not found" }, 404);

    // Patch courses: write text + clear every flag the admin might be acknowledging.
    const coursePatch: Record<string, unknown> = {
      summary: trimmedSummary,
      auto_flagged: false,
      flag_reason: null,
      flagged: false,
      flagged_reason: null,
      flagged_note: null,
    };
    if (search_document !== undefined && search_document !== null) {
      coursePatch.search_document = String(search_document);
    }
    const { error: patchErr } = await supabase
      .from("courses")
      .update(coursePatch)
      .eq("id", course_id);
    if (patchErr) return json({ error: patchErr.message }, 500);

    // Record the exception. Unique key is (provider_id, summary_hash), so a
    // repeat save for the same text is a no-op; treat any conflict as success.
    const { error: insertErr } = await supabase
      .from("validator_summary_exceptions")
      .insert({
        provider_id,
        summary_hash,
        course_id,
        reason,
      });
    if (insertErr) {
      const msg = (insertErr as { message?: string }).message || "";
      const code = (insertErr as { code?: string }).code || "";
      const isDupe = code === "23505" || /duplicate key/i.test(msg);
      if (!isDupe) return json({ error: msg || "exception insert failed" }, 500);
    }

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "save_summary",
      detail: { course_id, provider_id, reason, summary_hash },
    });

    return json({ ok: true, summary_hash });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
