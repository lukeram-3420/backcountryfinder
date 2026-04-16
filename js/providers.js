// ── PROVIDERS PAGE ──
async function loadProviders() {
  const grid = document.getElementById('provider-cards');
  if (!grid) return;
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/providers?select=id,name,website,location,rating,review_count,google_place_id,logo_url&active=eq.true&order=name.asc`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` } }
    );
    const providers = await res.json();
    if (!providers.length) {
      grid.innerHTML = '<p style="color:var(--text-tertiary);font-size:13px;">No providers listed yet.</p>';
      return;
    }
    const coursesRes = await fetch(
      `${SUPABASE_URL}/rest/v1/provider_activities?select=provider_id,activity_canonical`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` } }
    );
    const courses = await coursesRes.json();
    const activityMap = {};
    courses.forEach(c => {
      if (!activityMap[c.provider_id]) activityMap[c.provider_id] = new Set();
      if (c.activity_canonical) activityMap[c.provider_id].add(c.activity_canonical);
    });
    grid.innerHTML = providers.map(p => {
      const activities = [...(activityMap[p.id] || [])];
      const tags = activities.map(a => `<span class="provider-tag">${ACTIVITY_LABELS[a] || a}</span>`).join('');
      const reviewsUrl = p.google_place_id ? `https://search.google.com/local/reviews?placeid=${p.google_place_id}` : null;
      const rating = p.rating
        ? reviewsUrl
          ? `<a href="${reviewsUrl}" target="_blank" rel="noopener" class="provider-card-rating" style="display:block;text-decoration:none;margin-bottom:6px;">★ ${p.rating}${p.review_count ? ` · ${p.review_count}+ reviews` : ''}</a>`
          : `<div class="provider-card-rating">★ ${p.rating}${p.review_count ? ` · ${p.review_count}+ reviews` : ''}</div>`
        : '';
      const website = p.website ? `<a href="${p.website}" target="_blank" rel="noopener" class="provider-card-link">visit website ↗</a>` : '';
      const logo = p.logo_url
        ? `<div class="provider-card-logo"><img src="${p.logo_url}" alt="${p.name} logo" loading="lazy"></div>`
        : `<div class="provider-card-logo" style="background:#1a2e1a;"><span style="color:#4ade80;font-weight:800;font-size:16px;letter-spacing:-0.3px;">${p.name}</span></div>`;
      return `
        <div class="provider-card" style="cursor:pointer;" onclick="setProviderFilter('${p.id}', '${p.name.replace(/'/g, "\\'")}')">
          ${logo}
          <div class="provider-card-body">
            <div class="provider-card-name">${p.name}</div>
            <div class="provider-card-loc">${p.location || ''}</div>
            ${rating}
            ${website}
            ${tags ? `<div class="provider-card-tags">${tags}</div>` : ''}
          </div>
        </div>`;
    }).join('');
  } catch(e) {
    console.error('Failed to load providers:', e);
    grid.innerHTML = '<p style="color:var(--text-tertiary);font-size:13px;">Could not load providers.</p>';
  }
}
