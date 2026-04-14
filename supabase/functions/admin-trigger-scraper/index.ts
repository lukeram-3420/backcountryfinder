import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ADMIN_EMAIL = "luke@backcountryfinder.com";
const GITHUB_OWNER = "lukeram-3420";
const GITHUB_REPO  = "backcountryfinder";

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

    const { workflow_id, inputs } = await req.json();

    const ghToken = Deno.env.get("GITHUB_TOKEN");
    if (!ghToken) return json({ error: "Missing GITHUB_TOKEN secret" }, 500);

    const dispatchBody: { ref: string; inputs?: Record<string, string> } = { ref: "main" };
    if (inputs && typeof inputs === "object") {
      dispatchBody.inputs = inputs;
    }

    const ghRes = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${workflow_id}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${ghToken}`,
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(dispatchBody),
      }
    );

    if (!ghRes.ok) {
      const errText = await ghRes.text();
      return json({ error: `GitHub API error ${ghRes.status}: ${errText.slice(0, 200)}` }, 500);
    }

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "trigger_scraper",
      detail: { workflow_id },
    });

    return json({ success: true });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
