let _searchTimer = null;
function debouncedSearch() {
  // Legacy stub — kept so any remaining onchange="debouncedSearch()" doesn't throw
  clearTimeout(_searchTimer);
}

// V1 SUPABASE SEARCH FUNCTIONS — commented out, replaced by Algolia connectors above.
// Kept for rollback if needed. Delete once Algolia is confirmed working.
/*
function getCached() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const { data, ts, filters, sort } = JSON.parse(raw);
    if (Date.now() - ts > CACHE_TTL) return null;
    if (JSON.stringify(filters) !== JSON.stringify(currentFilters)) return null;
    if (sort !== currentSort) return null;
    return data;
  } catch(e) { return null; }
}

function setCache(data) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      data, ts: Date.now(),
      filters: currentFilters,
      sort: currentSort
    }));
  } catch(e) {}
}

async function fetchCourses(page = 0, append = false) {
  if (isLoading) return;
  isLoading = true;

  const cached = page === 0 ? getCached() : null;
  if (cached) {
    currentCourses = cached;
    renderCards(cached, false);
    isLoading = false;
    fetchFromSupabase(page, append, true);
    return;
  }

  if (!append) showSkeleton();
  await fetchFromSupabase(page, append, false);
}

async function fetchFromSupabase(page = 0, append = false, background = false) {
  try {
    const from = page * PAGE_SIZE;
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const defaultDate = tomorrow.toISOString().split('T')[0];

    const filters = ['active=eq.true', 'flagged=not.is.true', 'auto_flagged=not.is.true'];
    if (currentFilters.activity) filters.push(`activity_canonical=eq.${encodeURIComponent(currentFilters.activity)}`);
    if (currentFilters.provider) filters.push(`provider_id=eq.${encodeURIComponent(currentFilters.provider)}`);
    if (currentFilters.location) {
      filters.push(`location_canonical=eq.${encodeURIComponent(currentFilters.location)}`);
    }
    if (currentFilters.date) {
      filters.push(`or=(date_sort.gte.${currentFilters.date},custom_dates.eq.true)`);
    }

    let order = 'order=date_sort.asc.nullslast';
    if (currentSort === 'price')        order = 'order=price.asc';
    if (currentSort === 'availability') order = 'order=avail.asc';

    const queryString = filters.join('&');
    const url = `${SUPABASE_URL}/rest/v1/courses?select=*,providers(name,rating,review_count)&${queryString}&${order}&limit=${PAGE_SIZE}&offset=${from}`;

    const res = await fetch(url, {
      headers: {
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Prefer': 'count=exact',
      }
    });

    if (!res.ok) throw new Error(`Supabase error: ${res.status}`);

    const contentRange = res.headers.get('content-range');
    if (contentRange) {
      const match = contentRange.match(/\/(\d+)$/);
      if (match) totalCount = parseInt(match[1]);
    }

    const courses = await res.json();

    if (append) {
      currentCourses = [...currentCourses, ...courses];
    } else {
      currentCourses = courses;
      if (!background) setCache(courses);
    }

    renderCards(courses, append);

  } catch(err) {
    console.error('Fetch error:', err);
    if (!background) showError();
  } finally {
    isLoading = false;
    if (!append) hideSkeleton();
  }
}
*/

// V1 ACTIVITY/LOCATION DROPDOWN FUNCTIONS — commented out, replaced by Algolia connectors
/*
async function loadActivitiesDropdown() {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/courses?select=activity_canonical&active=eq.true&flagged=not.is.true&auto_flagged=not.is.true`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` } }
    );
    const rows = await res.json();
    const activities = new Set();
    rows.forEach(r => { if (r.activity_canonical) activities.add(r.activity_canonical); });
    const select = document.getElementById('search-activity');
    const currentVal = select.value || currentFilters.activity;
    select.innerHTML = '<option value="">Everything backcountry</option>' +
      [...activities].sort().map(a => `<option value="${a}">${ACTIVITY_LABELS[a] || a}</option>`).join('');
    if (currentVal) select.value = currentVal;
  } catch(e) {}
}

async function loadLocationsDropdown() {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/location_mappings?select=location_canonical`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` } }
    );
    const rows = await res.json();
    const locations = new Set();
    rows.forEach(r => {
      if (r.location_canonical) locations.add(r.location_canonical);
    });
    const select = document.getElementById('search-location');
    const currentVal = select.value || currentFilters.location;
    select.innerHTML = '<option value="">Anywhere</option>' +
      [...locations].sort().map(l => `<option value="${l}">${l}</option>`).join('');
    if (currentVal) select.value = currentVal;
  } catch(e) {}
}

async function updateLocationsForActivity(activity) {
  if (!activity) { loadLocationsDropdown(); return; }
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/courses?select=location_canonical&active=eq.true&flagged=not.is.true&auto_flagged=not.is.true&activity_canonical=eq.${encodeURIComponent(activity)}`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}`, 'Range': '0-9999' } }
    );
    const rows = await res.json();
    const locations = new Set();
    rows.forEach(r => { if (r.location_canonical) locations.add(r.location_canonical); });
    const select = document.getElementById('search-location');
    const sorted = [...locations].sort();
    select.innerHTML = '<option value="">Anywhere</option>' +
      sorted.map(l => `<option value="${l}">${l}</option>`).join('');
    if (currentFilters.location && !sorted.includes(currentFilters.location)) {
      currentFilters.location = '';
      select.value = '';
    } else if (currentFilters.location) {
      select.value = currentFilters.location;
    }
  } catch(e) { loadLocationsDropdown(); }
}
*/

// V1 SEARCH HELPERS — commented out, replaced by Algolia connectors
/*
function loadMore(){
  currentPage++;
  fetchCourses(currentPage, true);
}
function sortBy(el,val){
  document.querySelectorAll('.sort-btn').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  currentSort=val;
  currentPage=0;
  fetchCourses(0, false);
}
*/

// V1 runSearch — commented out, replaced by Algolia connectors
/*
function runSearch(){
  const prevActivity = currentFilters.activity;
  currentFilters.activity = document.getElementById('search-activity').value;
  currentFilters.location = document.getElementById('search-location').value;
  currentFilters.date = document.getElementById('search-date').value;
  currentPage=0;
  fetchCourses(0, false);
  if (currentFilters.activity !== prevActivity) {
    updateLocationsForActivity(currentFilters.activity);
  }
  if (currentFilters.activity) document.getElementById('search-activity').value = currentFilters.activity;
  if (currentFilters.location) document.getElementById('search-location').value = currentFilters.location;
}
*/

function setProviderFilter(providerId, providerName) {
  currentFilters.provider = providerId;
  const chip = document.getElementById('provider-filter-chip');
  const label = document.getElementById('provider-filter-label');
  if (chip && label) {
    label.textContent = `Viewing: ${providerName}`;
    chip.style.display = 'inline-flex';
  }
  if (window.history && window.history.replaceState)
    window.history.replaceState({}, '', `?provider=${providerId}`);
  showPage('search');
  applyConfigFilters();
}

function clearProviderFilter() {
  currentFilters.provider = '';
  const chip = document.getElementById('provider-filter-chip');
  if (chip) chip.style.display = 'none';
  applyConfigFilters();
  if (window.history && window.history.replaceState)
    window.history.replaceState({}, '', window.location.pathname);
}

function initProviderFilter() {
  const params = new URLSearchParams(window.location.search);
  const provider = params.get('provider');
  if (!provider) return;
  currentFilters.provider = provider;
  const chip = document.getElementById('provider-filter-chip');
  const label = document.getElementById('provider-filter-label');
  if (chip && label) {
    label.textContent = `Viewing: ${provider}`;
    chip.style.display = 'inline-flex';
  }
  fetch(`${SUPABASE_URL}/rest/v1/providers?select=name&id=eq.${provider}`,
    {headers: {'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}`}})
    .then(r => r.json())
    .then(rows => {
      if (rows[0] && label) label.textContent = `Viewing: ${rows[0].name}`;
    });
}

// ── ALGOLIA INSTANTSEARCH ──
// These are populated by initSearch() on DOMContentLoaded, not at script-load time —
// Algolia constants (ALGOLIA_APP_ID etc.) live in index.html's body script and are only
// defined after all head-loaded module files have finished executing.
let searchClient;
let search;
let customSearchBox;
let customInfiniteHits;
let customConfigure;
let _algoliaShowMore = null;
var _configRefine = null;

function updateDateChip() {
  const dateInput = document.getElementById('search-date');
  dateInput.classList.toggle('has-value', !!dateInput.value);
}

function clearDateFilter() {
  document.getElementById('search-date').value = '';
  updateDateChip();
  applyConfigFilters();
}

function applyConfigFilters() {
  const config = {};
  // Date filter
  const dateVal = document.getElementById('search-date').value;
  if (dateVal) {
    const ts = Math.floor(new Date(dateVal).getTime() / 1000);
    config.numericFilters = [`date_sort>=${ts}`];
  }
  // Provider filter
  if (currentFilters.provider) {
    config.facetFilters = [`provider_id:${currentFilters.provider}`];
  }
  if (_configRefine) _configRefine(config);
}

function initSearch() {
  // Instantiate Algolia client + connectors now that the body script has defined the constants
  searchClient = algoliasearch(ALGOLIA_APP_ID, ALGOLIA_SEARCH_KEY);
  search = instantsearch({
    indexName: ALGOLIA_INDEX,
    searchClient,
    routing: false,
  });

  customSearchBox = instantsearch.connectors.connectSearchBox(
    ({ refine }, isFirstRender) => {
      if (isFirstRender) {
        const input = document.getElementById('search-query');
        let timer;
        input.addEventListener('input', () => {
          clearTimeout(timer);
          timer = setTimeout(() => refine(input.value), 300);
        });
      }
    }
  );

  customInfiniteHits = instantsearch.connectors.connectInfiniteHits(
    ({ hits, showMore, isLastPage, results }, isFirstRender) => {
      const grid = document.getElementById('card-grid');
      const wrap = document.getElementById('load-more-wrap');
      const count = document.getElementById('results-count');

      const mapped = hits.map(mapHit);
      currentCourses = mapped;
      totalCount = results ? results.nbHits : mapped.length;
      _algoliaShowMore = showMore;

      if (mapped.length === 0) {
        const noFiltersActive = !document.getElementById('search-query').value
          && !currentFilters.provider;
        if (noFiltersActive) {
          grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon" style="font-size:52px;">🏔</div><h3>Updating course listings</h3><p>We're pulling in fresh data. Check back in about 45 minutes.</p><div class="status-pill"><span class="status-dot"></span><span>Scraper running now</span></div></div>`;
        } else {
          grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="20" rx="6" ry="2.5" stroke="#ccc" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="14.5" rx="4.5" ry="2" stroke="#ccc" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="9.5" rx="3" ry="1.8" stroke="#ccc" stroke-width="1.5" fill="none"/></svg></div><h3>no experiences found</h3><p>Try adjusting your filters.</p></div>`;
        }
        if (count) count.textContent = '0 results';
        if (wrap) wrap.style.display = 'none';
        return;
      }

      grid.innerHTML = mapped.map(c => buildCard(c)).join('');
      if (count) count.textContent = `${totalCount} results`;
      if (wrap) wrap.style.display = isLastPage ? 'none' : 'block';
      addRemoveReadyListeners();
    }
  );

  customConfigure = instantsearch.connectors.connectConfigure(
    ({ refine }, isFirstRender) => {
      _configRefine = refine;
      if (isFirstRender) {
        const dateInput = document.getElementById('search-date');
        dateInput.addEventListener('change', () => {
          updateDateChip();
          applyConfigFilters();
        });
      }
    }
  );

  // Set default "from" date to tomorrow
  const tmrw = new Date();
  tmrw.setDate(tmrw.getDate() + 1);
  const dateInput = document.getElementById('search-date');
  dateInput.value = tmrw.toISOString().split('T')[0];
  updateDateChip();

  // Parse provider deep link before starting search
  initProviderFilter();

  // Wire load more button to Algolia's showMore
  const loadMoreBtn = document.querySelector('.load-more-btn');
  if (loadMoreBtn) {
    loadMoreBtn.removeAttribute('onclick');
    loadMoreBtn.addEventListener('click', () => { if (_algoliaShowMore) _algoliaShowMore(); });
  }

  // Start Algolia InstantSearch
  search.addWidgets([
    customSearchBox({}),
    customInfiniteHits({ showPrevious: false }),
    customConfigure({
      searchParameters: {
        hitsPerPage: 12,
      },
    }),
  ]);
  search.start();

  // Apply initial date + provider filters after start
  applyConfigFilters();
}
