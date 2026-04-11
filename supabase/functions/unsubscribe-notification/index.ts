import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const SUPABASE_URL = "https://owzrztaguehebkatnatc.supabase.co";
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_KEY") ?? "";

function htmlPage(title: string, message: string, success: boolean): string {
  const color = success ? "#4ade80" : "#e24b4a";
  const bg = success ? "#eaf3de" : "#fcebeb";
  const border = success ? "#c0dd97" : "#f09595";
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${title} — BackcountryFinder</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #f5f4f0; font-family: Arial, sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
    .card { background: #fff; border-radius: 12px; max-width: 480px; width: 100%; overflow: hidden; }
    .header { background: #1a2e1a; padding: 28px 32px; text-align: center; }
    .logo { font-size: 22px; color: #fff; font-family: Georgia, serif; letter-spacing: -0.3px; }
    .logo span { color: #7ec87e; font-style: italic; }
    .body { padding: 32px; }
    .alert { background: ${bg}; border: 1px solid ${border}; border-radius: 10px; padding: 20px; margin-bottom: 20px; text-align: center; }
    .alert-icon { font-size: 32px; margin-bottom: 12px; }
    .alert-title { font-size: 18px; font-weight: 700; color: #1a2e1a; margin-bottom: 6px; }
    .alert-msg { font-size: 14px; color: #555; line-height: 1.6; }
    .cta { text-align: center; }
    .cta a { background: #1a2e1a; color: #fff; font-size: 13px; font-weight: 500; padding: 11px 24px; border-radius: 6px; text-decoration: none; display: inline-block; }
    .footer { background: #1a2e1a; padding: 16px 32px; text-align: center; }
    .footer p { font-size: 11px; color: rgba(255,255,255,0.4); }
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="logo">backcountry<span>finder</span></div>
    </div>
    <div class="body">
      <div class="alert">
        <div class="alert-title">${title}</div>
        <div class="alert-msg">${message}</div>
      </div>
      <div class="cta">
        <a href="https://backcountryfinder.com">Browse courses →</a>
      </div>
    </div>
    <div class="footer">
      <p>backcountryfinder.com</p>
    </div>
  </div>
</body>
</html>`;
}

serve(async (req) => {
  const url = new URL(req.url);
  const id = url.searchParams.get("id");

  if (!id) {
    return new Response(
      htmlPage("Invalid link", "This unsubscribe link is invalid or has already been used.", false),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/notifications?id=eq.${id}`,
      {
        method: "DELETE",
        headers: {
          "apikey": SUPABASE_SERVICE_KEY,
          "Authorization": `Bearer ${SUPABASE_SERVICE_KEY}`,
          "Content-Type": "application/json",
        },
      }
    );

    if (!res.ok) {
      throw new Error(`Supabase error: ${res.status}`);
    }

    return new Response(
      htmlPage(
        "You've been unsubscribed",
        "You won't receive any more notifications about this course. You can always sign up again from the course listing.",
        true
      ),
      { status: 200, headers: { "Content-Type": "text/html" } }
    );

  } catch (err) {
    return new Response(
      htmlPage("Something went wrong", "We couldn't process your unsubscribe request. Please try again or email luke@backcountryfinder.com.", false),
      { status: 500, headers: { "Content-Type": "text/html" } }
    );
  }
});
