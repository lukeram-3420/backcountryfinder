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

type RowPatch = {
  provider_id: string;
  activity_key: string;
  visible?: boolean;
  tracking_mode?: "immediate" | "extended";
};

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

    // Body: { rows: RowPatch[] }  — single-row requests set rows.length == 1.
    // Each row must carry provider_id + activity_key (the unique key); at
    // least one of `visible` or `tracking_mode` must be present.
    const body = await req.json();
    const rows: RowPatch[] = Array.isArray(body?.rows) ? body.rows : [];
    if (!rows.length) return json({ error: "No rows supplied" }, 400);

    let changed = 0;
    let coursesAffected = 0;
    const errors: string[] = [];

    for (const r of rows) {
      if (!r.provider_id || !r.activity_key) {
        errors.push(`missing provider_id/activity_key on row`);
        continue;
      }
      const patch: Record<string, unknown> = {
        updated_at: new Date().toISOString(),
      };
      if (typeof r.visible === "boolean") patch.visible = r.visible;
      if (r.tracking_mode !== undefined) {
        if (r.tracking_mode !== "immediate" && r.tracking_mode !== "extended") {
          errors.push(`invalid tracking_mode ${r.tracking_mode} on ${r.activity_key}`);
          continue;
        }
        patch.tracking_mode = r.tracking_mode;
      }
      // If neither visible nor tracking_mode was set, this row is a no-op —
      // skip it instead of silently bumping updated_at.
      if (!("visible" in patch) && !("tracking_mode" in patch)) {
        errors.push(`no-op row for ${r.activity_key}`);
        continue;
      }

      const { error } = await supabase
        .from("activity_controls")
        .update(patch)
        .eq("provider_id", r.provider_id)
        .eq("activity_key", r.activity_key);

      if (error) {
        errors.push(`${r.activity_key}: ${error.message}`);
        continue;
      }
      changed++;

      // Cascade visible → courses.active so the frontend hides / re-shows
      // immediately instead of waiting for the next scraper run. Match by
      // (provider_id, title) — we read the latest title from activity_controls
      // because the admin may have flipped visible on a row whose title has
      // since changed upstream.
      //   OFF → hide everything for this title.
      //   ON  → restore everything for this title EXCEPT avail='sold' (mirrors
      //         the admin-toggle-provider semantics: sold/notify-me rows stay
      //         hidden even when the provider-level toggle flips back on).
      if (typeof r.visible === "boolean") {
        const { data: ctrlRow } = await supabase
          .from("activity_controls")
          .select("title")
          .eq("provider_id", r.provider_id)
          .eq("activity_key", r.activity_key)
          .maybeSingle();
        const title = ctrlRow?.title;
        if (title) {
          let q = supabase
            .from("courses")
            .update({ active: r.visible })
            .eq("provider_id", r.provider_id)
            .eq("title", title);
          if (r.visible) q = q.neq("avail", "sold");
          const { data: updated, error: cascadeErr } = await q.select("id");
          if (cascadeErr) {
            errors.push(`${r.activity_key}: course cascade failed — ${cascadeErr.message}`);
          } else {
            coursesAffected += updated?.length || 0;
          }
        }
      }
    }

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "toggle_activity_control",
      detail: {
        requested: rows.length,
        changed,
        courses_affected: coursesAffected,
        errors: errors.length ? errors.slice(0, 10) : undefined,
      },
    });

    return json({ success: errors.length === 0, changed, courses_affected: coursesAffected, errors });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
