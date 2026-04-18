To: [Canadian Wilderness School Expeditions / Bow Valley Canyon Tours]
Subject: Quick ask — enabling Checkfront Public API for Bow Valley Canyon Tours listings

Hi [name],

I run BackcountryFinder (backcountryfinder.com), a free discovery site that aggregates Canadian guide-company listings so backcountry folks can find and book trips from operators like yours. We send click-through traffic to your booking widget with UTM tags, no commissions, no middleman — we just want people to find your tours and book direct.

We have Bow Valley Canyon Tours listed, but we're running into a data-quality issue I'm hoping you can help us solve with a one-time setting change on your end.

**The issue:** Your Checkfront tenant (canadian-wilderness-school-expeditions.checkfront.com) currently has the Public API disabled. Without it, we can't see which of your tours are in-season, sold out, or have dates available — so every Bow Valley listing on our site shows a generic "Check dates" button regardless of actual availability. That's a worse user experience for your customers, and it means sold-out or out-of-season tours still send clicks to your booking page.

**The ask:** If you could flip on the Public API, we'd be able to show real availability, real dates, and accurate "Book Now" / "Sold Out" / seasonal states for every tour.

Toggle location in your Checkfront admin:
**Settings → API → Public API → Enable**

That's it — no integration work on your end, no extra fees from Checkfront, and nothing changes for your existing booking flow. It just exposes a read-only JSON endpoint that we (and any other directory site) can use to show accurate availability.

We already do this for several other Checkfront-based operators (Alpine Air Adventures, Girth Hitch Guiding, etc.) and it works seamlessly.

Happy to hop on a quick call if any questions, or if you'd rather I send this to whoever manages your Checkfront account directly, just let me know the right contact.

Thanks,
Luke
luke@backcountryfinder.com
backcountryfinder.com
