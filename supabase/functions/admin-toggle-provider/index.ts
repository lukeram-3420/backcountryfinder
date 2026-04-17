import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
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

// Mirror of domainOf() in admin.html — normalises a website URL into the
// domain key that the Pipeline tab uses for "already live" matching.
// lowercase → strip protocol → strip www. → strip trailing slash.
function domainOf(website: string | null | undefined): string {
  if (!website) return "";
  try {
    return new URL(website).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return String(website).toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "");
  }
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

    const { provider_id, active } = await req.json();
    const activeBool = Boolean(active);

    await supabase
      .from("providers")
      .update({ active: activeBool })
      .eq("id", provider_id);

    // Cascade to courses:
    //   Toggle OFF → set every provider row to active=false
    //   Toggle ON  → restore active=true ONLY where avail != 'sold',
    //                so sold-out / notify-me rows stay hidden
    let updateQuery = supabase
      .from("courses")
      .update({ active: activeBool })
      .eq("provider_id", provider_id);
    if (activeBool) {
      updateQuery = updateQuery.neq("avail", "sold");
    }
    const { data: updatedCourses, error: cascadeErr } = await updateQuery.select("id");
    if (cascadeErr) {
      return json({ error: `Course cascade failed: ${cascadeErr.message}` }, 500);
    }
    const coursesUpdated = updatedCourses?.length || 0;

    // On ON: flip any matching provider_pipeline rows to status='live' so the
    // Pipeline tab no longer shows the candidate and so discover_providers.py
    // has a clean signal on the next weekly run (it already skips known
    // domains, but an explicit status='live' is honest + human-readable).
    // Match strategy mirrors the client-side hide in admin.html: domain
    // (normalised) OR lowercase name. On OFF we intentionally do NOTHING to
    // the pipeline — once a provider has been onboarded it stays "live" in
    // the pipeline even if temporarily disabled, so we don't resurrect stale
    // candidates on the next discover run.
    let pipelineUpdated = 0;
    if (activeBool) {
      const { data: provider } = await supabase
        .from("providers")
        .select("name, website")
        .eq("id", provider_id)
        .maybeSingle();
      const targetDomain = domainOf(provider?.website);
      const targetName = (provider?.name || "").toLowerCase().trim();
      if (targetDomain || targetName) {
        const { data: candidates } = await supabase
          .from("provider_pipeline")
          .select("id, website, name, status")
          .neq("status", "live")  // skip rows already marked live — no-op
          .neq("status", "skip"); // leave admin-skipped rows alone
        const matchIds = (candidates || [])
          .filter(r => {
            const d = domainOf(r.website);
            const n = (r.name || "").toLowerCase().trim();
            return (targetDomain && d && d === targetDomain) ||
                   (targetName && n && n === targetName);
          })
          .map(r => r.id);
        if (matchIds.length > 0) {
          const { error: pipelineErr } = await supabase
            .from("provider_pipeline")
            .update({ status: "live" })
            .in("id", matchIds);
          if (pipelineErr) {
            console.error("pipeline status update failed", pipelineErr);
          } else {
            pipelineUpdated = matchIds.length;
          }
        }
      }
    }

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "toggle_provider",
      detail: { provider_id, active: activeBool, courses_updated: coursesUpdated, pipeline_marked_live: pipelineUpdated },
    });

    return json({ success: true, courses_updated: coursesUpdated, pipeline_marked_live: pipelineUpdated });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
