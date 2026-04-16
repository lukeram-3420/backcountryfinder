function utmUrl(url) {
  if (!url || url === '#') return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}utm_source=backcountryfinder&utm_medium=referral`;
}

function renderCards(courses, append=false){
  const grid=document.getElementById('card-grid');
  const wrap=document.getElementById('load-more-wrap');
  const count=document.getElementById('results-count');
  if(!grid)return;
  if(!courses||courses.length===0){
    if(!append){
      const noFiltersActive = !currentFilters.activity && !currentFilters.location && !currentFilters.provider;
      if (noFiltersActive) {
        grid.innerHTML=`<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon" style="font-size:52px;">🏔</div><h3>Updating course listings</h3><p>We're pulling in fresh data. Check back in about 45 minutes.</p><div class="status-pill"><span class="status-dot"></span><span>Scraper running now</span></div></div>`;
      } else {
        grid.innerHTML=`<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" stroke="#ccc" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" stroke="#ccc" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" stroke="#ccc" stroke-width="1.5" fill="none"/></svg></div><h3>no experiences found</h3><p>Try adjusting your filters.</p></div>`;
      }
      if(count)count.textContent='0 results';
    }
    if(wrap)wrap.style.display='none';
    return;
  }
  const cards=courses.map(c=>buildCard(c)).join('');
  if(append){grid.innerHTML+=cards;}else{grid.innerHTML=cards;}
  const showing=grid.querySelectorAll('.course-card').length;
  if(count)count.textContent=`${totalCount||showing} results`;
  if(wrap)wrap.style.display=showing<totalCount?'block':'none';
  addRemoveReadyListeners();
}

function buildCard(c) {
  const saved = isSaved(c.id);
  const spots = c.spots_remaining;
  const spotWord = spots === 1 ? 'spot' : 'spots';
  const availLabel = c.avail==='open'     ? 'Open'
    : c.avail==='critical' ? `${spots ?? '1-2'} ${spotWord} left`
    : c.avail==='low'      ? `${spots ?? '3-4'} ${spotWord} left`
    : 'Sold out';
  const provider = c.providers || {};
  const providerName = provider.name || c.provider_id || '';
  const rating = provider.rating ? `★ ${provider.rating}` : '';
  const location = c.location_canonical || c.location_raw || '';
  const activity = c.activity_canonical || c.activity_raw || c.activity || 'guided';
  const badge = c.badge_canonical || c.badge || ACTIVITY_LABELS[activity] || activity.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  const imgUrl = c.image_url || IMG[activity] || IMG.hiking;
  const bookingUrl = c.booking_url || '#';
  const safeTitle = (c.title||'').replace(/'/g,"\\'");

  // Serialize course data for click tracking — escape for safe inline use
  const courseJson = JSON.stringify({
    provider_id:        c.provider_id || '',
    activity_canonical: c.activity_canonical || '',
    activity_raw:       c.activity_raw || '',
    location_canonical: c.location_canonical || '',
    location_raw:       c.location_raw || '',
    price:              c.price || null,
    booking_url:        bookingUrl,
    providers:          { name: providerName }
  }).replace(/</g, '\\u003c').replace(/'/g, "\\'");

  return `<div class="course-card">
    <div class="card-img">
      <img src="${imgUrl}" alt="${c.title}" loading="lazy" onerror="this.src='${IMG[c.activity]||IMG.hiking}'">
      <div class="card-overlay">
        <div class="card-badge">${badge}</div>
        <div class="card-provider-tag">${providerName}</div>
      </div>
    </div>
    <div class="card-body">
      <div class="card-title">${c.title}</div>
      ${c.summary ? `<div class="card-summary">${c.summary}</div>` : ''}
      <div class="card-meta">
        ${c.custom_dates
          ? `<span style="color:var(--green-dark);font-weight:600;">Flexible dates</span>`
          : c.date_display
            ? `<span>${c.date_display}</span>`
            : ''
        }
        ${location?`<span class="sep">·</span><span>${location}</span>`:''}
        ${rating?`<span class="sep">·</span><span>${rating}</span>`:''}
      </div>
    </div>
    <div class="card-footer">
      <div class="price-block">
        <div class="card-price">$${c.price||'—'} <sub>CAD</sub></div>
        <div class="avail ${c.avail}">${availLabel}</div>
      </div>
      <div class="card-actions">
        <button class="save-btn ${saved?'saved remove-ready':''}" onclick="toggleSave('${c.id}')">
          ${saved
            ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" fill="#1a2e1a"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" fill="#1a2e1a"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" fill="#4ade80"/></svg>`
            : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" stroke="currentColor" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" stroke="currentColor" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`
          }
          <span class="save-label">my list</span>
        </button>
        ${c.custom_dates
          ?`<button class="book-btn" style="background:#f5f4f0;color:#1a2e1a;border:1px solid #c8c7c2;" onclick="openNotifyModal('${c.id}','${safeTitle}','${c.provider_id}','${c._queryID||''}')">Notify me 🔔</button>`
          :c.avail==='sold'
            ?`<button class="book-btn" style="background:#f5f4f0;color:#888;border:1px solid #c8c7c2;cursor:default;">Sold out</button>`
            :`<a class="book-btn" href="${utmUrl(bookingUrl)}" target="_blank" rel="noopener" onclick="logClick(JSON.parse('${courseJson}'));trackAlgoliaConversion('${c.id}','${c._queryID||''}','Course Booking Initiated');setTimeout(showToast,800)">Book Now <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:middle;margin-left:2px;"><path d="M7 17L17 7"/><path d="M7 7h10v10"/></svg></a>`
        }
      </div>
    </div>
    <div class="report-strip" onclick="openReport(this)" data-course-id="${c.id}" style="border-top:0.5px solid var(--color-border-tertiary);padding:7px 14px;display:flex;align-items:center;justify-content:center;cursor:pointer;background:none;width:100%;font-size:11px;color:var(--color-text-tertiary);opacity:0.35;font-family:inherit;">
      Something not right?
    </div>
    <div class="report-panel" style="display:none;border-top:0.5px solid var(--color-border-tertiary);background:var(--color-background-secondary);padding:12px 14px;pointer-events:auto;">
      <div style="font-size:11px;color:var(--color-text-tertiary);margin-bottom:9px;">What's wrong?</div>
      <div class="report-chips" style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;">
        <button class="chip" data-reason="button_broken" onclick="event.stopPropagation();selectChip(this)">Book button didn't work</button>
        <button class="chip" data-reason="wrong_date" onclick="event.stopPropagation();selectChip(this)">Wrong date</button>
        <button class="chip" data-reason="wrong_price" onclick="event.stopPropagation();selectChip(this)">Wrong price</button>
        <button class="chip" data-reason="sold_out" onclick="event.stopPropagation();selectChip(this)">Shows open but sold out</button>
        <button class="chip" data-reason="bad_description" onclick="event.stopPropagation();selectChip(this)">Bad description</button>
        <button class="chip" data-reason="other" onclick="event.stopPropagation();selectChip(this)">Other</button>
      </div>
      <textarea class="report-note" placeholder="Any extra detail (optional)…" style="display:none;width:100%;font-size:11px;padding:6px 8px;border-radius:6px;border:0.5px solid var(--color-border-secondary);background:var(--color-background-primary);color:var(--color-text-primary);resize:none;min-height:48px;margin-bottom:8px;font-family:inherit;"></textarea>
      <div style="display:flex;align-items:center;gap:8px;">
        <button class="btn-send" onclick="event.stopPropagation(); submitReport(this)" disabled style="font-size:10px;font-family:inherit;font-weight:500;padding:4px 12px;border-radius:20px;border:none;background:#4ade80;color:#0a1a0a;cursor:pointer;">Send</button>
        <button onclick="event.stopPropagation(); closeReport(this)" style="font-size:10px;font-family:inherit;background:none;border:none;color:var(--color-text-tertiary);cursor:pointer;">cancel</button>
      </div>
    </div>
  </div>`;
}

// Map Algolia hit → shape expected by buildCard()
function mapHit(hit) {
  return {
    id: hit.objectID,
    title: hit.title || '',
    summary: hit.summary || '',
    search_document: hit.search_document || '',
    date_display: hit.date_display || '',
    date_sort: hit.date_sort,
    custom_dates: hit.custom_dates || false,
    location_canonical: hit.location_canonical || '',
    location_raw: hit.location_raw || '',
    activity_canonical: null,
    activity_raw: hit.activity || '',
    activity: hit.activity || '',
    badge_canonical: null,
    badge: hit.badge || '',
    image_url: hit.image_url,
    price: hit.price,
    currency: hit.currency || 'CAD',
    avail: hit.avail || 'open',
    spots_remaining: hit.spots_remaining ?? null,
    booking_url: hit.booking_url || '#',
    booking_mode: hit.booking_mode || 'instant',
    provider_id: hit.provider_id || '',
    providers: {
      name: hit.provider_name || '',
      rating: hit.provider_rating || null,
      review_count: null,
    },
    // Algolia insights — auto-decorated by `insights: true` on the root config,
    // read here so click/conversion handlers can attribute events to the originating search.
    _queryID: hit.__queryID || null,
    _position: hit.__position || null,
  };
}
