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

    const { course_id } = await req.json();

    // Fetch flagged_reason to validate — only clear for button_broken or other
    const { data: courseRow } = await supabase
      .from("courses")
      .select("flagged_reason")
      .eq("id", course_id)
      .single();

    const reason = courseRow?.flagged_reason;
    if (reason !== "button_broken" && reason !== "other") {
      return json({ error: `Cannot resolve flag with reason '${reason}' — only button_broken and other are manually resolvable` }, 400);
    }

    await supabase
      .from("courses")
      .update({ flagged: false, flagged_reason: null, flagged_note: null })
      .eq("id", course_id);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "resolve_flag",
      detail: { course_id, flagged_reason: reason },
    });

    return json({ success: true });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
