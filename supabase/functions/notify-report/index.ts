import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const REASON_LABELS: Record<string, string> = {
  button_broken:   "Book button didn't work",
  wrong_date:      "Wrong date",
  wrong_price:     "Wrong price",
  sold_out:        "Shows open but sold out",
  bad_description: "Bad description",
  other:           "Other",
};

serve(async (req) => {
  try {
    const { course_id, reason, note, session_id } = await req.json();

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_KEY")!
    );

    // 1. Insert into reports log
    await supabase.from("reports").insert({
      course_id,
      session_id,
      reason,
      note: note || null,
    });

    // 2. Fetch course details for the email
    const { data: course } = await supabase
      .from("courses")
      .select("id, title, provider_id, booking_url, start_date")
      .eq("id", course_id)
      .single();

    // 3. Flag the course row
    await supabase
      .from("courses")
      .update({
        flagged: true,
        flagged_reason: reason,
      })
      .eq("id", course_id);

    // 4. Send email via Resend
    const reasonLabel = REASON_LABELS[reason] ?? reason;
    const courseTitle = course?.title ?? course_id;
    const providerID  = course?.provider_id ?? "unknown";
    const startDate   = course?.start_date ?? "unknown date";
    const bookingUrl  = course?.booking_url ?? "—";

    await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${Deno.env.get("RESEND_API_KEY")}`,
      },
      body: JSON.stringify({
        from:    "BackcountryFinder <alerts@backcountryfinder.com>",
        to:      "luke@backcountryfinder.com",
        subject: `[Report] ${courseTitle} — ${reasonLabel}`,
        html: `
          <p><strong>Course:</strong> ${courseTitle}</p>
          <p><strong>Provider:</strong> ${providerID}</p>
          <p><strong>Date:</strong> ${startDate}</p>
          <p><strong>Course ID:</strong> <code>${course_id}</code></p>
          <p><strong>Reason:</strong> ${reasonLabel}</p>
          ${note ? `<p><strong>Note:</strong> ${note}</p>` : ""}
          <p><strong>Booking URL:</strong> <a href="${bookingUrl}">${bookingUrl}</a></p>
          <hr/>
          <p><a href="https://supabase.com/dashboard/project/owzrztaguehebkatnatc/editor">View in Supabase</a></p>
        `,
      }),
    });

    return new Response(JSON.stringify({ ok: true }), { status: 200 });

  } catch (err) {
    console.error(err);
    return new Response(JSON.stringify({ error: String(err) }), { status: 500 });
  }
});
