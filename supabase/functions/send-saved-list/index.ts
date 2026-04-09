import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY");
const FROM_EMAIL = "luke@backcountryfinder.com";
const SITE_URL = "https://backcountryfinder.com";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

interface Course {
  id: number;
  title: string;
  provider: string;
  badge: string;
  date: string;
  location: string;
  price: number;
  avail: string;
  rating: string;
  url: string;
}

function availLabel(avail: string): string {
  if (avail === "open") return "Open";
  if (avail === "low") return "Few spots left";
  return "Sold out";
}

function availColor(avail: string): string {
  if (avail === "open") return "#3b6d11";
  if (avail === "low") return "#854f0b";
  return "#a32d2d";
}

function availBg(avail: string): string {
  if (avail === "open") return "#eaf3de";
  if (avail === "low") return "#faeeda";
  return "#fcebeb";
}

function courseEmoji(badge: string): string {
  if (badge.toLowerCase().includes("ski") || badge.toLowerCase().includes("ast")) return "🎿";
  if (badge.toLowerCase().includes("climb")) return "🧗";
  if (badge.toLowerCase().includes("mountain") || badge.toLowerCase().includes("glacier")) return "🏔";
  if (badge.toLowerCase().includes("bik")) return "🚵";
  if (badge.toLowerCase().includes("hik")) return "🥾";
  return "🏔";
}

function buildCourseRow(course: Course): string {
  const bookUrl = `${course.url}&utm_source=backcountryfinder_email&utm_medium=email`;
  return `
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;">
      <tr>
        <td style="padding:0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;border:1px solid #e8e7e3;overflow:hidden;">
            <tr>
              <td width="5" style="background:#1a2e1a;border-radius:10px 0 0 10px;">&nbsp;</td>
              <td style="padding:14px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td>
                      <span style="font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;color:#7ec87e;background:#1a2e1a;padding:2px 8px;border-radius:20px;font-family:Arial,sans-serif;">${course.badge}</span>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding-top:8px;">
                      <p style="margin:0;font-size:15px;font-weight:600;color:#1a1a1a;font-family:Arial,sans-serif;line-height:1.3;">${course.title}</p>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding-top:4px;padding-bottom:10px;">
                      <p style="margin:0;font-size:12px;color:#777777;font-family:Arial,sans-serif;line-height:1.5;">
                        ${course.date} &nbsp;·&nbsp; ${course.location} &nbsp;·&nbsp; ${course.provider} &nbsp;·&nbsp; ★ ${course.rating}
                      </p>
                    </td>
                  </tr>
                  <tr>
                    <td style="border-top:1px solid #f0efeb;padding-top:10px;">
                      <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                          <td>
                            <p style="margin:0;font-size:18px;font-weight:600;color:#1a1a1a;font-family:Arial,sans-serif;">\$${course.price} <span style="font-size:11px;color:#888;font-weight:400;">CAD</span></p>
                            <span style="font-size:10px;font-weight:500;color:${availColor(course.avail)};background:${availBg(course.avail)};padding:2px 8px;border-radius:20px;font-family:Arial,sans-serif;">${availLabel(course.avail)}</span>
                          </td>
                          <td align="right">
                            <a href="${bookUrl}" style="background:#1a2e1a;color:#ffffff;font-size:12px;font-weight:500;padding:9px 18px;border-radius:6px;text-decoration:none;font-family:Arial,sans-serif;display:inline-block;">Book Now →</a>
                          </td>
                        </tr>
                      </table>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>`;
}

function buildShareSection(courses: Course[], sharedIds: string): string {
  const shareUrl = `${SITE_URL}/?shared=${sharedIds}`;
  const waText = encodeURIComponent(
    `Hey — found some great backcountry courses on BackcountryFinder. Opens with them already saved for you 👇\n${shareUrl}`
  );
  const smsText = encodeURIComponent(
    `Check out these backcountry courses I found — ${shareUrl}`
  );

  return `
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 0;">
      <tr>
        <td style="background:#eaf3de;border-radius:10px;padding:18px 20px;border:1px solid #c0dd97;">
          <p style="margin:0 0 4px;font-size:14px;font-weight:600;color:#1a2e1a;font-family:Arial,sans-serif;">Share this list with a friend</p>
          <p style="margin:0 0 14px;font-size:12px;color:#3b6d11;font-family:Arial,sans-serif;">They'll see your saved courses when they open the link.</p>
          <table cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding-right:8px;">
                <a href="https://wa.me/?text=${waText}" style="background:#25D366;color:#ffffff;font-size:12px;font-weight:500;padding:8px 16px;border-radius:6px;text-decoration:none;font-family:Arial,sans-serif;display:inline-block;">WhatsApp</a>
              </td>
              <td style="padding-right:8px;">
                <a href="sms:?body=${smsText}" style="background:#1a2e1a;color:#7ec87e;font-size:12px;font-weight:500;padding:8px 16px;border-radius:6px;text-decoration:none;font-family:Arial,sans-serif;display:inline-block;">iMessage</a>
              </td>
              <td>
                <a href="${shareUrl}" style="background:#ffffff;color:#1a2e1a;font-size:12px;font-weight:500;padding:8px 16px;border-radius:6px;text-decoration:none;font-family:Arial,sans-serif;display:inline-block;border:1px solid #1a2e1a;">copy link</a>
              </td>
            </tr>
          </table>
          <p style="margin:12px 0 0;font-size:11px;color:#639922;font-family:Arial,sans-serif;">or just forward this email →</p>
        </td>
      </tr>
    </table>`;
}

function buildEmail(courses: Course[], sharedIds: string): string {
  const courseRows = courses.map(buildCourseRow).join("");
  const shareSection = buildShareSection(courses, sharedIds);
  const shareUrl = `${SITE_URL}/?shared=${sharedIds}`;

  return `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your saved courses from BackcountryFinder</title>
</head>
<body style="margin:0;padding:0;background:#f5f4f0;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4f0;padding:24px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

          <!-- HEADER -->
          <tr>
            <td style="background:#1a2e1a;border-radius:10px 10px 0 0;padding:28px 32px;text-align:center;">
              <p style="margin:0 0 6px;font-size:24px;color:#ffffff;font-family:Georgia,serif;letter-spacing:-0.3px;">backcountry<span style="color:#7ec87e;font-style:italic;">finder</span></p>
              <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.45);">Find your course. Find your line.</p>
            </td>
          </tr>

          <!-- INTRO -->
          <tr>
            <td style="background:#ffffff;padding:24px 32px 8px;">
              <p style="margin:0;font-size:14px;color:#444444;line-height:1.7;">
                Hey — here are the <strong style="color:#1a2e1a;">${courses.length} course${courses.length !== 1 ? "s" : ""} you saved</strong> on BackcountryFinder. Book directly with the provider — no middleman, no markup.
              </p>
            </td>
          </tr>

          <!-- COURSES -->
          <tr>
            <td style="background:#ffffff;padding:12px 32px;">
              ${courseRows}
              ${shareSection}
            </td>
          </tr>

          <!-- DIVIDER -->
          <tr>
            <td style="background:#ffffff;padding:0 32px;">
              <hr style="border:none;border-top:1px solid #f0efeb;margin:8px 0;">
            </td>
          </tr>

          <!-- CTA -->
          <tr>
            <td style="background:#ffffff;padding:20px 32px;text-align:center;">
              <p style="margin:0 0 12px;font-size:13px;color:#666666;">Looking for more? We update course availability every 6 hours.</p>
              <a href="${SITE_URL}" style="background:#1a2e1a;color:#ffffff;font-size:13px;font-weight:500;padding:11px 24px;border-radius:6px;text-decoration:none;display:inline-block;">find more courses →</a>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background:#1a2e1a;border-radius:0 0 10px 10px;padding:20px 32px;text-align:center;">
              <p style="margin:0 0 8px;font-size:14px;color:rgba(255,255,255,0.6);font-family:Georgia,serif;">backcountry<span style="color:#7ec87e;">finder</span></p>
              <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.3);line-height:1.8;">
                <a href="${SITE_URL}" style="color:rgba(255,255,255,0.45);text-decoration:none;">backcountryfinder.com</a> &nbsp;·&nbsp;
                <a href="mailto:luke@backcountryfinder.com" style="color:rgba(255,255,255,0.45);text-decoration:none;">luke@backcountryfinder.com</a>
                <br>You received this because you requested your saved list.
                <br><a href="#" style="color:rgba(255,255,255,0.35);text-decoration:none;">unsubscribe</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const { email, courses, optIn } = await req.json();

    if (!email || !email.includes("@")) {
      return new Response(JSON.stringify({ error: "Invalid email" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (!courses || courses.length === 0) {
      return new Response(JSON.stringify({ error: "No courses provided" }), {
        status: 400,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    const sharedIds = courses.map((c: Course) => c.id).join(",");
    const html = buildEmail(courses, sharedIds);

    const resendRes = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: `BackcountryFinder <${FROM_EMAIL}>`,
        to: [email],
        subject: `Your ${courses.length} saved course${courses.length !== 1 ? "s" : ""} — BackcountryFinder`,
        html,
      }),
    });

    if (!resendRes.ok) {
      const err = await resendRes.text();
      throw new Error(`Resend error: ${err}`);
    }

    // Save to Supabase if opt-in
    if (optIn) {
      const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
      const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
      await fetch(`${supabaseUrl}/rest/v1/email_signups`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "apikey": supabaseKey,
          "Authorization": `Bearer ${supabaseKey}`,
          "Prefer": "return=minimal",
        },
        body: JSON.stringify({
          email,
          signup_type: "saved_list",
          course_title: null,
        }),
      });
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
