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

// ── Platform detection via HTML signatures ────────────────────────────────
// Mirrored in discover_providers.py `PLATFORM_SIGNATURES` and
// admin-analyse-provider/index.ts. Keep all three in sync when adding a
// platform. First match wins.
const PLATFORM_SIGNATURES: Array<[string, RegExp[]]> = [
  ["rezdy",       [/\.rezdy\.com/i, /rezdy-online-booking/i, /rezdy-modal/i]],
  ["checkfront",  [/\.checkfront\.com/i, /ChfHost/i, /checkfront-booking/i]],
  ["zaui",        [/\.zaui\.net/i, /zaui\.js/i]],
  ["fareharbor",  [/fareharbor\.com/i, /fh-iframe/i, /fareharbor-dock/i]],
  ["bokun",       [/bokun\.io/i, /bokunwidget/i, /bokun-widget/i]],
  ["peek",        [/book\.peek\.com/i, /peek-booking/i]],
  ["thinkific",   [/thinkific\.com/i, /<meta[^>]+thinkific/i]],
  ["shopify",     [/cdn\.shopify\.com/i, /Shopify\.theme/i, /myshopify\.com/i]],
  ["wix",         [/static\.wixstatic\.com/i, /wix-viewer/i, /<meta[^>]+wix/i]],
  ["squarespace", [/static1\.squarespace\.com/i, /Static\.SQUARESPACE_CONTEXT/i, /squarespace\.com/i]],
  ["woocommerce", [/wp-content\/plugins\/woocommerce/i, /<body[^>]+woocommerce/i, /wc-ajax/i]],
  ["wordpress",   [/wp-content\//i, /wp-includes\//i, /<meta[^>]+WordPress/i]],
];

async function detectPlatform(url: string): Promise<{ platform: string; evidence: string }> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const r = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; BackcountryFinderBot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
      },
      redirect: "follow",
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (!r.ok) return { platform: "unknown", evidence: "" };
    const html = await r.text();
    for (const [platformId, patterns] of PLATFORM_SIGNATURES) {
      for (const pat of patterns) {
        if (pat.test(html)) return { platform: platformId, evidence: pat.source };
      }
    }
    return { platform: "unknown", evidence: "" };
  } catch (e) {
    console.log(`platform fetch failed for ${url}: ${e instanceof Error ? e.name : "err"}`);
    return { platform: "unknown", evidence: "" };
  }
}

// Column-name indirection: providers.booking_platform vs
// provider_pipeline.platform. Keep this mapping authoritative — callers pass
// the table name only and get the right column written.
const PLATFORM_COLUMN: Record<string, string> = {
  providers: "booking_platform",
  provider_pipeline: "platform",
};

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

    const { table, id, url } = await req.json();
    const column = PLATFORM_COLUMN[String(table)];
    if (!column) return json({ error: "invalid table" }, 400);
    if (!id || typeof id !== "string") return json({ error: "id required" }, 400);
    if (!url || typeof url !== "string") return json({ error: "url required" }, 400);

    const detection = await detectPlatform(url);

    // PATCH the target row with the detected platform. For an 'unknown'
    // result we still write the column so the UI reflects "we tried and
    // nothing matched" rather than leaving a stale old value in place.
    const { error: patchErr } = await supabase
      .from(table)
      .update({ [column]: detection.platform })
      .eq("id", id);
    if (patchErr) return json({ error: `patch failed: ${patchErr.message}` }, 500);

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "detect_platform",
      detail: {
        table,
        id,
        url,
        platform: detection.platform,
        evidence: detection.evidence,
      },
    });

    return json({ platform: detection.platform, evidence: detection.evidence });
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
