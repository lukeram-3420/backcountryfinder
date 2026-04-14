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

const SYSTEM_PROMPT =
  "You are a scraper analyst for backcountryfinder.com, an outdoor adventure course aggregator in Canada. " +
  "Given a provider website URL, fetch the site and identify:\n" +
  "- name: the business name (exact name as shown on their site)\n" +
  "- location: primary location in 'City, Province' format e.g. 'Squamish, BC' or 'Canmore, AB'. " +
  "For multi-location providers use their primary/home location.\n" +
  "- platform: their booking platform. Known values: rezdy, fareharbor, woocommerce, wordpress, squarespace, checkfront, custom, unknown\n" +
  "- complexity: scraping complexity. low (static HTML or known platform API), medium (JS-rendered or iframe), high (complex custom system or requires Playwright)\n" +
  "- priority: 1 (high value — multiple locations, popular area, well known), 2 (medium), 3 (low — single small operator)\n" +
  "- notes: 1-2 sentences about the booking system and what to watch for when scraping\n\n" +
  "Respond in JSON only, no preamble, no markdown:\n" +
  "{name: string, location: string, platform: string, complexity: string, priority: number, notes: string}";

function fallbackFromUrl(url: string) {
  let domain = "";
  try {
    domain = new URL(url).hostname.replace(/^www\./, "");
  } catch {
    domain = String(url);
  }
  const name = domain.split(".")[0] || domain;
  return {
    name: name.charAt(0).toUpperCase() + name.slice(1),
    location: null,
    platform: "unknown",
    complexity: "low",
    priority: 3,
    notes: "",
  };
}

async function analyseWithHaiku(url: string) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": Deno.env.get("ANTHROPIC_API_KEY")!,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: CLAUDE_MODEL,
      max_tokens: 400,
      system: SYSTEM_PROMPT,
      tools: [{ type: "web_search_20250305", name: "web_search" }],
      messages: [
        { role: "user", content: `Analyse this outdoor adventure provider: ${url}` },
      ],
    }),
  });
  if (!res.ok) throw new Error(`Claude API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  // Find the last text block (after any tool_use blocks)
  const blocks = Array.isArray(data?.content) ? data.content : [];
  const text = blocks
    .filter((b: { type?: string; text?: string }) => b.type === "text" && typeof b.text === "string")
    .map((b: { text: string }) => b.text)
    .join("\n")
    .trim();
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < 0) throw new Error(`No JSON in Claude response: ${text.slice(0, 200)}`);
  return JSON.parse(text.slice(start, end + 1));
}

function nameSimilarity(a: string, b: string): number {
  a = a.toLowerCase().replace(/[^a-z0-9]/g, "");
  b = b.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!a.length || !b.length) return 0;
  const longer = a.length > b.length ? a : b;
  const shorter = a.length > b.length ? b : a;
  let matches = 0;
  for (const char of shorter) {
    if (longer.includes(char)) matches++;
  }
  return matches / longer.length;
}

const NULL_PLACES = { google_place_id: null, rating: null, review_count: null };

async function googlePlacesLookup(name: string, location: string | null) {
  const apiKey = Deno.env.get("GOOGLE_PLACES_API_KEY");
  if (!apiKey) return NULL_PLACES;
  const query = `${name} ${location || ""}`.trim();
  const url =
    `https://maps.googleapis.com/maps/api/place/findplacefromtext/json` +
    `?input=${encodeURIComponent(query)}` +
    `&inputtype=textquery` +
    `&fields=place_id,rating,user_ratings_total,name` +
    `&key=${apiKey}`;
  let candidate: { place_id?: string; rating?: number; user_ratings_total?: number; name?: string } | null = null;
  try {
    const r = await fetch(url);
    if (!r.ok) return NULL_PLACES;
    const data = await r.json();
    candidate = data?.candidates?.[0] ?? null;
  } catch {
    return NULL_PLACES;
  }
  if (!candidate) return NULL_PLACES;

  // Check 1 — name similarity
  const placesName = candidate.name || "";
  const sim = nameSimilarity(name, placesName);
  if (sim < 0.4) {
    console.log(`Places name mismatch: searched '${name}' got '${placesName}' — rejected`);
    return NULL_PLACES;
  }

  // Check 2 — review count sanity
  const reviewCount = candidate.user_ratings_total ?? 0;
  if (reviewCount > 2000) {
    console.log(`Places review count suspiciously high (${reviewCount}) for '${name}' — rejected`);
    return NULL_PLACES;
  }

  // Check 3 — duplicate place_id in provider_pipeline
  const placeId = candidate.place_id;
  if (placeId) {
    try {
      const supaUrl = Deno.env.get("SUPABASE_URL")!;
      const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
      const dupRes = await fetch(
        `${supaUrl}/rest/v1/provider_pipeline?google_place_id=eq.${encodeURIComponent(placeId)}&select=id,name`,
        {
          headers: {
            apikey: serviceKey,
            Authorization: `Bearer ${serviceKey}`,
            "Content-Type": "application/json",
          },
        },
      );
      if (dupRes.ok) {
        const existing = await dupRes.json();
        const conflict = Array.isArray(existing) && existing.find((row: { name?: string }) =>
          (row.name || "").toLowerCase().trim() !== name.toLowerCase().trim()
        );
        if (conflict) {
          console.log(`Places ID ${placeId} already assigned to '${conflict.name}' — rejected for '${name}'`);
          return NULL_PLACES;
        }
      }
    } catch (e) {
      console.error("duplicate place_id check failed", e);
      // Don't reject on infrastructure failure — proceed with the result
    }
  }

  return {
    google_place_id: placeId ?? null,
    rating: candidate.rating ?? null,
    review_count: candidate.user_ratings_total ?? null,
  };
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

    const { url } = await req.json();
    if (!url || typeof url !== "string") return json({ error: "url required" }, 400);

    let parsed: {
      name?: string; location?: string | null; platform?: string;
      complexity?: string; priority?: number; notes?: string;
    };
    try {
      parsed = await analyseWithHaiku(url);
    } catch (e) {
      console.error("Haiku analyse failed", e);
      parsed = fallbackFromUrl(url);
    }

    const name = (parsed.name || fallbackFromUrl(url).name).trim();
    const location = (parsed.location || null) as string | null;
    const platform = (parsed.platform || "unknown").toLowerCase();
    const complexity = (parsed.complexity || "low").toLowerCase();
    const priority = Number.isFinite(Number(parsed.priority)) ? Number(parsed.priority) : 3;
    const notes = parsed.notes || "";

    const places = await googlePlacesLookup(name, location);

    const result = {
      name, location, platform, complexity, priority, notes,
      ...places,
    };

    await supabase.from("admin_log").insert({
      user_email: userEmail,
      action: "analyse_provider",
      detail: { url, name_detected: result.name, platform_detected: result.platform },
    });

    return json(result);
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
