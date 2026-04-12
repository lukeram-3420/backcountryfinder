import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY");
const FROM_EMAIL = "hello@backcountryfinder.com";
const NOTIFY_EMAIL = "hello@backcountryfinder.com";
const SITE_URL = "https://backcountryfinder.com";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function emailWrapper(body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f8faf8;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8faf8;padding:24px 16px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
        <tr>
          <td style="background:#1a2e1a;border-radius:10px 10px 0 0;padding:24px 32px;text-align:center;">
            <p style="margin:0;font-size:20px;color:#ffffff;font-family:Georgia,serif;letter-spacing:-0.3px;">
              backcountry<span style="color:#4ade80;font-style:italic;">finder</span>
            </p>
          </td>
        </tr>
        <tr>
          <td style="background:#ffffff;border-radius:0 0 10px 10px;padding:28px 32px;">
            ${body}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 0;text-align:center;">
            <p style="margin:0;font-size:11px;color:#888;">
              <a href="${SITE_URL}" style="color:#1a2e1a;text-decoration:none;">backcountryfinder.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
}

function fieldRow(label: string, value: string): string {
  if (!value) return "";
  return `
    <tr>
      <td style="padding:6px 0;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:0.4px;width:140px;vertical-align:top;">${label}</td>
      <td style="padding:6px 0;font-size:14px;color:#1a1a1a;vertical-align:top;">${value}</td>
    </tr>`;
}

function buildNotifyEmail(type: string, data: Record<string, string>): string {
  const isListed = type === "get_listed";
  const tag = isListed ? "Get listed" : "Suggest a provider";
  const colour = isListed ? "#1a2e1a" : "#2d5a2d";

  const rows = isListed
    ? `${fieldRow("Provider", data.school_name)}
       ${fieldRow("Website", data.website)}
       ${fieldRow("Contact name", data.contact_name)}
       ${fieldRow("Contact email", data.contact_email)}
       ${fieldRow("Notes", data.notes)}`
    : `${fieldRow("Provider", data.school_name)}
       ${fieldRow("Website", data.website)}
       ${fieldRow("Talk to", data.contact_at_provider)}
       ${fieldRow("Submitted by", data.submitter_name)}
       ${fieldRow("Their email", data.contact_email)}
       ${fieldRow("Notes", data.notes)}`;

  const body = `
    <div style="margin-bottom:20px;">
      <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:#4ade80;background:${colour};display:inline-block;padding:3px 12px;border-radius:20px;">${tag}</span>
    </div>
    <h2 style="margin:0 0 20px;font-size:20px;font-weight:700;color:#1a1a1a;letter-spacing:-0.3px;">
      ${isListed ? "New listing request" : "New provider suggestion"}
    </h2>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #f0f0f0;padding-top:16px;">
      ${rows}
    </table>
    <div style="margin-top:24px;padding-top:20px;border-top:1px solid #f0f0f0;">
      <a href="${SITE_URL}" style="font-size:12px;color:#1a2e1a;font-weight:600;text-decoration:none;">view backcountryfinder.com →</a>
    </div>`;

  return emailWrapper(body);
}

function buildConfirmEmail(type: string, data: Record<string, string>): string {
  const isListed = type === "get_listed";
  const name = isListed ? data.contact_name : data.submitter_name;
  const firstName = name ? name.split(" ")[0] : "there";

  const body = isListed
    ? `
      <h2 style="margin:0 0 12px;font-size:20px;font-weight:700;color:#1a1a1a;letter-spacing:-0.3px;">Thanks ${firstName} — we'll be in touch.</h2>
      <p style="margin:0 0 16px;font-size:14px;color:#555;line-height:1.7;">
        We've received your listing request for <strong style="color:#1a2e1a;">${data.school_name}</strong>. 
        We'll review it and get back to you at ${data.contact_email} within 48 hours.
      </p>
      <p style="margin:0 0 24px;font-size:14px;color:#555;line-height:1.7;">
        In the meantime, feel free to browse what's already listed on BackcountryFinder.
      </p>
      <a href="${SITE_URL}" style="display:inline-block;background:#1a2e1a;color:#ffffff;font-size:13px;font-weight:700;padding:11px 24px;border-radius:8px;text-decoration:none;">visit backcountryfinder →</a>`
    : `
      <h2 style="margin:0 0 12px;font-size:20px;font-weight:700;color:#1a1a1a;letter-spacing:-0.3px;">Thanks ${firstName} — great tip.</h2>
      <p style="margin:0 0 16px;font-size:14px;color:#555;line-height:1.7;">
        We'll reach out to <strong style="color:#1a2e1a;">${data.school_name}</strong> and work on getting them listed on BackcountryFinder.
      </p>
      <p style="margin:0 0 24px;font-size:14px;color:#555;line-height:1.7;">
        We'll let you know when they're live. Thanks for helping make BackcountryFinder better.
      </p>
      <a href="${SITE_URL}" style="display:inline-block;background:#1a2e1a;color:#ffffff;font-size:13px;font-weight:700;padding:11px 24px;border-radius:8px;text-decoration:none;">visit backcountryfinder →</a>`;

  return emailWrapper(body);
}

async function sendEmail(to: string, subject: string, html: string): Promise<void> {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: `BackcountryFinder <${FROM_EMAIL}>`,
      to: [to],
      subject,
      html,
    }),
  });
  const body = await res.text();
  console.log(`Email to ${to}: ${res.status} — ${body}`);
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const data = await req.json();
    const { type } = data;

    if (!type || !data.school_name) {
      return new Response(JSON.stringify({ error: "Missing required fields" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    console.log(`Processing ${type} submission for: ${data.school_name}`);

    // Send notification to luke
    const notifySubject = type === "get_listed"
      ? `New listing request — ${data.school_name}`
      : `New provider suggestion — ${data.school_name}`;

    await sendEmail(NOTIFY_EMAIL, notifySubject, buildNotifyEmail(type, data));

    // Send confirmation to submitter if they provided an email
    if (data.contact_email && data.contact_email.includes("@")) {
      const confirmSubject = type === "get_listed"
        ? `We got your request — BackcountryFinder`
        : `Thanks for the tip — BackcountryFinder`;

      await sendEmail(data.contact_email, confirmSubject, buildConfirmEmail(type, data));
    }

    return new Response(JSON.stringify({ success: true }), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });

  } catch (err) {
    console.error("Error:", err.message);
    return new Response(JSON.stringify({ error: err.message }), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
