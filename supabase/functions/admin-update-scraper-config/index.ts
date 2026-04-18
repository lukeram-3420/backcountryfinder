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

// Whitelist — these are the only keys the Activity Tracking tab is allowed
// to write. Keep narrow: unknown keys get rejected rather than silently
// stored, so a typo in the UI can't create a bogus row that later misleads
// scrapers or debugging.
const ALLOWED_KEYS = new Set([
  "extended_lookahead_days",
  "immediate_lookahead_days",
]);

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

    const { key, value } = await req.json();

    if (typeof key !== "string" || !ALLOWED_KEYS.has(key)) {
      return json({ error: `Unknown config key ${key}` }, 400);
    }
    // Accept numeric or string input; coerce to int, validate range.
    const n = Number(value);
    if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1 || n > 730) {
      return json({ error: `value must be an integer in [1,730]` }, 400);
    }

    const { error } = await supabase
      .from("scraper_config")
      .upsert(
        { key, value: String(n), updated_at: new Date().toISOString() },
        { onConflict: "key" },
      );
    if (error) return json({ error: `upsert failed: ${error.message}` }, 500);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "update_scraper_config",
      detail: { key, value: n },
    });

    return json({ success: true, key, value: n });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
