import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY");
const FROM_EMAIL = "BackcountryFinder <hello@backcountryfinder.com>";
const SITE_URL = "https://backcountryfinder.com";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const { email, course_title, provider_name } = await req.json();

    if (!email || !course_title) {
      return new Response(JSON.stringify({ error: "Missing required fields" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4f0;padding:24px 16px;">
  <tr><td align="center">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

    <tr><td style="background:#1a2e1a;border-radius:10px 10px 0 0;padding:28px 32px;text-align:center;">
      <p style="margin:0 0 4px;font-size:22px;color:#fff;font-family:Georgia,serif;letter-spacing:-0.3px;">backcountry<span style="color:#7ec87e;font-style:italic;">finder</span></p>
      <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.4);">Find your course. Find your line.</p>
    </td></tr>

    <tr><td style="background:#fff;padding:32px 32px 8px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#eaf3de;border:1px solid #c0dd97;border-radius:10px;padding:20px;margin-bottom:24px;">
        <tr><td>
          <p style="margin:0 0 4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#3b6d11;">You're on the list</p>
          <p style="margin:0 0 4px;font-size:18px;font-weight:700;color:#1a2e1a;">We'll let you know when dates drop</p>
          <p style="margin:0;font-size:13px;color:#639922;font-weight:600;">${course_title}${provider_name ? ` · ${provider_name}` : ''}</p>
        </td></tr>
      </table>

      <p style="margin:0 0 16px;font-size:14px;color:#444;line-height:1.7;">
        As soon as this course opens for booking, you'll be the first to know. We'll send you a direct link straight to the booking page — no hunting around required.
      </p>
      <p style="margin:0 0 24px;font-size:14px;color:#444;line-height:1.7;">
        In the meantime, there are plenty of other courses open right now:
      </p>
    </td></tr>

    <tr><td style="background:#fff;padding:0 32px 24px;text-align:center;">
      <a href="${SITE_URL}" style="background:#1a2e1a;color:#fff;font-size:13px;font-weight:500;padding:12px 28px;border-radius:6px;text-decoration:none;display:inline-block;">browse open courses →</a>
    </td></tr>

    <tr><td style="background:#fff;padding:0 32px;">
      <hr style="border:none;border-top:1px solid #f0efeb;margin:0;">
    </td></tr>

    <tr><td style="background:#fff;padding:20px 32px;">
      <p style="margin:0;font-size:12px;color:#888;line-height:1.6;">
        No spam — just one email when this course opens. If you didn't sign up for this, you can safely ignore it.
      </p>
    </td></tr>

    <tr><td style="background:#1a2e1a;border-radius:0 0 10px 10px;padding:20px 32px;text-align:center;">
      <p style="margin:0 0 6px;font-size:13px;color:rgba(255,255,255,0.6);font-family:Georgia,serif;">backcountry<span style="color:#7ec87e;">finder</span></p>
      <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.3);line-height:1.8;">
        <a href="${SITE_URL}" style="color:rgba(255,255,255,0.4);text-decoration:none;">backcountryfinder.com</a> &nbsp;·&nbsp;
        <a href="mailto:hello@backcountryfinder.com" style="color:rgba(255,255,255,0.4);text-decoration:none;">hello@backcountryfinder.com</a>
      </p>
    </td></tr>

  </table>
  </td></tr>
</table>
</body>
</html>`;

    const resendRes = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: FROM_EMAIL,
        to: [email],
        subject: `You're on the list — ${course_title}`,
        html,
      }),
    });

    if (!resendRes.ok) {
      const err = await resendRes.text();
      throw new Error(`Resend error: ${err}`);
    }

    return new Response(JSON.stringify({ success: true }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });

  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
