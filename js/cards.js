function utmUrl(url) {
  if (!url || url === '#') return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}utm_source=backcountryfinder&utm_medium=referral`;
}

// Visible-session cap: primary + this many expanded rows.
const SESSION_VISIBLE_CAP = 4;
const SESSION_EXPANDED_CAP = SESSION_VISIBLE_CAP - 1;

// ── HIT NORMALISATION ──
// Map an Algolia hit to the per-session shape used internally.
function mapHit(hit) {
  // V2 stable id format is `{provider}-{date_sort}-{title_hash_8}` or
  // `{provider}-flex-{title_hash_8}`. The hash is always the last 8 chars.
  // algolia_sync.py doesn't currently emit title_hash as a top-level field,
  // so we derive it from objectID. Falls back to null on malformed ids.
  const objId = hit.objectID || '';
  const titleHashFromId = objId.length >= 8 ? objId.slice(-8) : null;
  return {
    id: objId,
    title: hit.title || '',
    title_hash: hit.title_hash || titleHashFromId,
    summary: hit.summary || '',
    search_document: hit.search_document || '',
    date_display: hit.date_display || '',
    date_sort: hit.date_sort,
    custom_dates: hit.custom_dates || false,
    location_canonical: hit.location_canonical || '',
    location_raw: hit.location_raw || '',
    image_url: hit.image_url,
    duration_days: hit.duration_days ?? null,
    price: hit.price,
    price_has_variations: hit.price_has_variations || false,
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
      logo_url: hit.provider_logo_url || null,
    },
    _queryID: hit.__queryID || null,
    _position: hit.__position || null,
  };
}

// Map a Supabase row (from the saved-list / shared-list direct REST queries)
// to the same shape as mapHit's output, so buildCard sees a consistent input.
function mapSupabaseRow(row) {
  const objId = row.id || '';
  const titleHashFromId = objId.length >= 8 ? objId.slice(-8) : null;
  const ds = row.date_sort;
  // date_sort in Supabase is 'YYYY-MM-DD'; convert to unix-seconds-like number
  // for sorting consistency with the Algolia path. Falls back to far-future for null.
  let dateSortNum = 4102444800; // 2100-01-01
  if (ds) {
    const t = Date.parse(ds + 'T00:00:00Z');
    if (!isNaN(t)) dateSortNum = Math.floor(t / 1000);
  }
  return {
    id: objId,
    title: row.title || '',
    title_hash: titleHashFromId,
    summary: row.summary || '',
    search_document: row.search_document || '',
    date_display: row.date_display || '',
    date_sort: dateSortNum,
    custom_dates: row.custom_dates || false,
    location_canonical: row.location_canonical || '',
    location_raw: row.location_raw || '',
    image_url: row.image_url,
    duration_days: row.duration_days ?? null,
    price: row.price,
    price_has_variations: row.price_has_variations || false,
    currency: row.currency || 'CAD',
    avail: row.avail || 'open',
    spots_remaining: row.spots_remaining ?? null,
    booking_url: row.booking_url || '#',
    booking_mode: row.booking_mode || 'instant',
    provider_id: row.provider_id || '',
    providers: {
      name: (row.providers && row.providers.name) || '',
      rating: (row.providers && row.providers.rating) || null,
      review_count: (row.providers && row.providers.review_count) || null,
      logo_url: (row.providers && row.providers.logo_url) || null,
    },
    _queryID: null,
    _position: null,
  };
}

// ── GROUPING ──
// Group an array of per-session course objects into synthetic card objects,
// one per unique (provider_id, title_hash) — falling back to (provider_id, title)
// when title_hash is missing.
function _groupKey(c) {
  const pid = c.provider_id || '';
  if (c.title_hash) return `${pid}::${c.title_hash}`;
  return `${pid}::title:${(c.title || '').toLowerCase().trim()}`;
}

function groupCoursesForCards(courses) {
  if (!Array.isArray(courses)) return [];
  // Insertion-ordered map preserves Algolia's relevance ranking from the input.
  const buckets = new Map();
  for (const c of courses) {
    const key = _groupKey(c);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(c);
  }
  return [...buckets.entries()].map(([key, items]) => {
    items.sort((a, b) => (a.date_sort ?? Infinity) - (b.date_sort ?? Infinity));
    const head = items[0];
    const sessions = items.map(c => ({
      id: c.id,
      date_display: c.date_display,
      date_sort: c.date_sort,
      price: c.price,
      avail: c.avail,
      spots_remaining: c.spots_remaining,
      booking_url: c.booking_url,
      custom_dates: c.custom_dates,
      booking_mode: c.booking_mode,
      _queryID: c._queryID,
      _position: c._position,
    }));
    const positivePrices = items.map(c => c.price).filter(p => typeof p === 'number' && p > 0);
    const price_min = positivePrices.length ? Math.min(...positivePrices) : (head.price ?? null);
    return {
      id: head.id,
      provider_id: head.provider_id,
      title: head.title,
      title_hash: head.title_hash,
      _group_key: key,
      summary: head.summary,
      search_document: head.search_document,
      location_canonical: head.location_canonical,
      location_raw: head.location_raw,
      duration_days: head.duration_days,
      image_url: head.image_url,
      booking_mode: head.booking_mode,
      providers: head.providers,
      _queryID: head._queryID,
      _position: head._position,
      price_has_variations: head.price_has_variations || items.length > 1,
      price_min: price_min,
      sessions: sessions,
      has_more_sessions: items.length > SESSION_VISIBLE_CAP,
      velocity_fill_pct: null,
      velocity_days_to_book: null,
    };
  });
}

// Re-build a synthetic group for a given groupKey by looking up matching
// per-session entries in the live currentCourses array. Used by toggleSave
// to re-render a single card after a save state change.
function rebuildSyntheticForKey(groupKey) {
  if (!Array.isArray(currentCourses)) return null;
  const matching = currentCourses.filter(c => _groupKey(c) === groupKey);
  if (matching.length === 0) return null;
  const groups = groupCoursesForCards(matching);
  return groups[0] || null;
}

// ── RENDER ──
function renderCards(courses, append=false){
  const grid=document.getElementById('card-grid');
  const wrap=document.getElementById('load-more-wrap');
  const count=document.getElementById('results-count');
  if(!grid)return;
  if(!courses||courses.length===0){
    if(!append){
      const queryEl=document.getElementById('search-query');
      const queryVal=queryEl?queryEl.value:'';
      const noFiltersActive = !queryVal && !currentFilters.provider;
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
  const groups = groupCoursesForCards(courses);
  const cardsHtml = groups.map(g => buildCard(g)).join('');
  if(append){grid.innerHTML+=cardsHtml;}else{grid.innerHTML=cardsHtml;}
  const showing = grid.querySelectorAll('.course-card').length;
  if(count) count.textContent = `${totalCount||showing} results`;
  if(wrap) wrap.style.display = (totalCount && showing < totalCount) ? 'block' : 'none';
  addRemoveReadyListeners();
}

// ── CARD HTML HELPERS ──
function _saveSvg(saved) {
  return saved
    ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" fill="#1a2e1a"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" fill="#1a2e1a"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" fill="#4ade80"/></svg>`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" stroke="currentColor" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" stroke="currentColor" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>`;
}

function _availLabelChip(avail) {
  if (avail === 'sold') return { cls: 'sold', label: 'Sold out' };
  if (avail === 'critical') return { cls: 'critical', label: 'Almost full' };
  if (avail === 'low') return { cls: 'low', label: 'Low availability' };
  return { cls: 'open', label: 'Open' };
}

function _spotsText(s) {
  if (s == null) return '';
  const word = s === 1 ? 'spot' : 'spots';
  return `${s} ${word} left`;
}

function _bookingClickAttrs(courseCtx, sessionId, queryID, includeToast) {
  const courseJson = JSON.stringify(courseCtx).replace(/</g, '\\u003c').replace(/'/g, "\\'");
  const toast = includeToast ? ';setTimeout(showToast,800)' : '';
  return `onclick="logClick(JSON.parse('${courseJson}'));trackAlgoliaConversion('${sessionId}','${queryID||''}','Course Booking Initiated')${toast}"`;
}

function _sessionRow(c, session, isPrimary) {
  const saved = isSaved(session.id, session.date_sort);
  const dsAttr = session.date_sort == null ? '' : String(session.date_sort);
  const chip = _availLabelChip(session.avail);
  const spots = _spotsText(session.spots_remaining);
  const bookingUrl = session.booking_url || '#';

  const courseCtx = {
    provider_id:        c.provider_id || '',
    location_canonical: c.location_canonical || '',
    location_raw:       c.location_raw || '',
    price:              session.price ?? c.price_min ?? null,
    booking_url:        bookingUrl,
    providers:          { name: (c.providers && c.providers.name) || '' }
  };

  // Booking control — Notify Me for sold/custom-non-instant; Book ↗ otherwise
  const safeTitle = (c.title || '').replace(/'/g, "\\'");
  let bookingControl;
  if (session.custom_dates && c.booking_mode !== 'instant') {
    bookingControl = `<button class="book-btn ${isPrimary ? '' : 'book-btn-sm'}" style="background:#f5f4f0;color:#1a2e1a;border:1px solid #c8c7c2;" onclick="openNotifyModal('${session.id}','${safeTitle}','${c.provider_id}','${session._queryID||''}')">Notify me 🔔</button>`;
  } else if (session.avail === 'sold') {
    bookingControl = `<button class="book-btn ${isPrimary ? '' : 'book-btn-sm'}" style="background:#f5f4f0;color:#888;border:1px solid #c8c7c2;cursor:default;">Sold out</button>`;
  } else {
    const arrow = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:middle;margin-left:2px;"><path d="M7 17L17 7"/><path d="M7 7h10v10"/></svg>`;
    const label = isPrimary ? 'Book Now' : 'Book';
    bookingControl = `<a class="book-btn ${isPrimary ? '' : 'book-btn-sm'}" href="${utmUrl(bookingUrl)}" target="_blank" rel="noopener" ${_bookingClickAttrs(courseCtx, session.id, session._queryID, isPrimary)}>${label} ${arrow}</a>`;
  }

  // Save button — primary has icon + text + chip; expanded session is icon-only
  const saveAttrs = `data-save-id="${session.id}" data-save-date="${dsAttr}"`;
  const saveOnclick = `onclick="toggleSave('${session.id}', '${dsAttr}')"`;
  const saveBtn = isPrimary
    ? `<button class="save-btn ${saved ? 'saved remove-ready' : ''}" ${saveAttrs} ${saveOnclick}>${_saveSvg(saved)}<span class="save-label">my list</span></button>`
    : `<button class="save-btn save-btn-icon ${saved ? 'saved remove-ready' : ''}" ${saveAttrs} ${saveOnclick} aria-label="Save to my list">${_saveSvg(saved)}</button>`;

  const dateLabel = session.date_display || (session.custom_dates ? 'Flexible dates' : '');

  if (isPrimary) {
    return `
      <div class="session-row session-row-primary">
        <div class="session-row-top">
          <div class="session-date">${dateLabel}</div>
          <div class="session-actions">${saveBtn}${bookingControl}</div>
        </div>
        <div class="session-row-meta">
          <span class="avail ${chip.cls}">${chip.label}</span>
          ${spots ? `<span class="session-spots">${spots}</span>` : ''}
        </div>
      </div>`;
  }
  return `
    <div class="session-row session-row-expanded">
      <div class="session-row-left">
        <div class="session-date session-date-sm">${dateLabel}</div>
        <div class="session-row-meta">
          <span class="avail ${chip.cls}">${chip.label}</span>
          ${spots ? `<span class="session-spots">${spots}</span>` : ''}
        </div>
      </div>
      <div class="session-actions">${saveBtn}${bookingControl}</div>
    </div>`;
}

function _velocityWidget(c) {
  if (c.velocity_fill_pct == null) {
    return `<div class="velocity-widget" style="display:none;"></div>`;
  }
  const pct = Math.max(0, Math.min(100, c.velocity_fill_pct));
  const days = c.velocity_days_to_book;
  const isAlmost = pct >= 80;
  const cls = isAlmost ? 'almost' : 'filling';
  const headline = isAlmost ? '⚡ Almost gone' : '🔥 Filling fast';
  const sub = isAlmost
    ? 'Only a few spots historically remain'
    : (days != null ? `Books ~${days} days before start` : 'Filling faster than average');
  return `<div class="velocity-widget ${cls}">
    <div class="velocity-bar"><div class="velocity-bar-fill" style="width:${pct}%"></div></div>
    <div class="velocity-headline">${headline}</div>
    <div class="velocity-sub">${sub}</div>
  </div>`;
}

// ── BUILD CARD ──
// Receives a synthetic group object built by groupCoursesForCards().
function buildCard(c) {
  if (!c) return '';
  // Defensive: if a per-session object slips through (e.g. legacy callers),
  // wrap it in a 1-session group so the card still renders.
  if (!c.sessions) {
    c = groupCoursesForCards([c])[0];
    if (!c) return '';
  }

  const sessions = c.sessions || [];
  const primary = sessions[0];
  if (!primary) return '';

  const provider = c.providers || {};
  const providerName = provider.name || c.provider_id || '';
  const rating = provider.rating ? `★ ${provider.rating}` : '';
  const location = c.location_canonical || c.location_raw || '';
  const imgUrl = c.image_url || FALLBACK_IMG;

  const duration = c.duration_days;
  const durationLabel = (duration != null && duration > 0)
    ? `${duration} day${duration === 1 ? '' : 's'}`
    : '';
  const metaParts = [];
  if (location) metaParts.push(location);
  if (durationLabel) metaParts.push(durationLabel);
  const metaLine = metaParts.join(' · ');

  // Price block
  const priceVal = c.price_min ?? primary.price;
  const priceLabel = c.price_has_variations
    ? `<div class="card-price-from">FROM</div>`
    : '';
  const priceAmount = priceVal != null
    ? `<div class="card-price">$${priceVal} <sub>CAD</sub></div>`
    : `<div class="card-price">— <sub>CAD</sub></div>`;

  const showVelocity = c.velocity_fill_pct != null;

  // Expanded session list (sessions[1]..sessions[SESSION_VISIBLE_CAP-1])
  const expandedSessions = sessions.slice(1, SESSION_VISIBLE_CAP);
  const moreCount = sessions.length - 1;
  const expandedHtml = expandedSessions.map(s => _sessionRow(c, s, false)).join('');

  const moreBtnHtml = sessions.length > 1
    ? `<button class="more-dates-btn" onclick="toggleSessionList(this)" data-collapsed-label="+${moreCount} more dates ▾">+${moreCount} more dates ▾</button>`
    : '';

  const hintRowHtml = c.has_more_sessions
    ? `<div class="more-dates-hint"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:middle;margin-right:5px;"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>More dates available — adjust the date filter above</div>`
    : '';

  return `<div class="course-card" data-group-key="${c._group_key || ''}" data-expanded="false">
    <div class="card-img">
      <img src="${imgUrl}" alt="${c.title}" loading="lazy" onerror="this.src='${FALLBACK_IMG}'">
      <div class="card-overlay">
        <div class="card-provider-tag">${providerName}</div>
        ${rating ? `<div class="card-rating-tag">${rating}</div>` : ''}
      </div>
    </div>
    <div class="card-body">
      <div class="card-title">${c.title}</div>
      ${c.summary ? `<div class="card-summary">${c.summary}</div>` : ''}
      ${metaLine ? `<div class="card-meta-line">${metaLine}</div>` : ''}
      <div class="card-price-row${showVelocity ? ' has-velocity' : ''}">
        <div class="card-price-block">
          ${priceLabel}
          ${priceAmount}
        </div>
        ${showVelocity ? `<div class="card-price-divider"></div>` : ''}
        ${_velocityWidget(c)}
      </div>
    </div>
    <div class="card-session-divider"></div>
    <div class="card-sessions">
      ${_sessionRow(c, primary, true)}
      ${moreBtnHtml}
      <div class="session-list-expanded" data-count="${moreCount}" style="display:none;">
        ${expandedHtml}
        ${hintRowHtml}
      </div>
    </div>
    <div class="report-strip" onclick="openReport(this)" data-course-id="${c.id}" style="border-top:0.5px solid var(--border);padding:7px 14px;display:flex;align-items:center;justify-content:center;cursor:pointer;background:none;width:100%;font-size:11px;color:var(--text-tertiary);opacity:0.35;font-family:inherit;">
      Something not right?
    </div>
    <div class="report-panel" style="display:none;border-top:0.5px solid var(--border);background:var(--bg-secondary);padding:12px 14px;pointer-events:auto;">
      <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:9px;">What's wrong?</div>
      <div class="report-chips" style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;">
        <button class="chip" data-reason="button_broken" onclick="event.stopPropagation();selectChip(this)">Book button didn't work</button>
        <button class="chip" data-reason="wrong_date" onclick="event.stopPropagation();selectChip(this)">Wrong date</button>
        <button class="chip" data-reason="wrong_price" onclick="event.stopPropagation();selectChip(this)">Wrong price</button>
        <button class="chip" data-reason="sold_out" onclick="event.stopPropagation();selectChip(this)">Shows open but sold out</button>
        <button class="chip" data-reason="bad_description" onclick="event.stopPropagation();selectChip(this)">Bad description</button>
        <button class="chip" data-reason="other" onclick="event.stopPropagation();selectChip(this)">Other</button>
      </div>
      <textarea class="report-note" placeholder="Any extra detail (optional)…" style="display:none;width:100%;font-size:11px;padding:6px 8px;border-radius:6px;border:0.5px solid var(--border-mid);background:var(--bg-card);color:var(--text-primary);resize:none;min-height:48px;margin-bottom:8px;font-family:inherit;"></textarea>
      <div style="display:flex;align-items:center;gap:8px;">
        <button class="btn-send" onclick="event.stopPropagation(); submitReport(this)" disabled style="font-size:10px;font-family:inherit;font-weight:500;padding:4px 12px;border-radius:20px;border:none;background:#4ade80;color:#0a1a0a;cursor:pointer;">Send</button>
        <button onclick="event.stopPropagation(); closeReport(this)" style="font-size:10px;font-family:inherit;background:none;border:none;color:var(--text-tertiary);cursor:pointer;">cancel</button>
      </div>
    </div>
  </div>`;
}

// ── EXPAND / COLLAPSE SESSION LIST ──
function toggleSessionList(btn) {
  const card = btn.closest('.course-card');
  if (!card) return;
  const list = card.querySelector('.session-list-expanded');
  if (!list) return;
  const isOpen = list.style.display === 'block';
  list.style.display = isOpen ? 'none' : 'block';
  card.dataset.expanded = isOpen ? 'false' : 'true';
  if (isOpen) {
    btn.textContent = btn.dataset.collapsedLabel || '+more dates ▾';
  } else {
    btn.textContent = 'Hide dates ▴';
  }
}
