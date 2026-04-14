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

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "toggle_provider",
      detail: { provider_id, active: activeBool, courses_updated: coursesUpdated },
    });

    return json({ success: true, courses_updated: coursesUpdated });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
