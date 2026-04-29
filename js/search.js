let _searchTimer = null;
function debouncedSearch() {
  // Legacy stub — kept so any remaining onchange="debouncedSearch()" doesn't throw
  clearTimeout(_searchTimer);
}

// ── ALGOLIA INSIGHTS (events) ──
// Anonymous persistent userToken stored in localStorage so Algolia can attribute
// clicks/conversions back to a search session (enables CTR + future personalisation).
function initAlgoliaInsights() {
  if (typeof aa !== 'function') return;
  try {
    aa('init', { appId: ALGOLIA_APP_ID, apiKey: ALGOLIA_SEARCH_KEY });
    let token = localStorage.getItem('bcf_algolia_user');
    if (!token) {
      token = (crypto && crypto.randomUUID) ? crypto.randomUUID() : `u_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      localStorage.setItem('bcf_algolia_user', token);
    }
    aa('setUserToken', token);
  } catch(e) { /* never block UI */ }
}

function trackAlgoliaClick(objectID, queryID, position, eventName) {
  if (typeof aa !== 'function' || !objectID) return;
  try {
    const payload = { eventName, index: ALGOLIA_INDEX, objectIDs: [objectID] };
    if (queryID && position) {
      aa('clickedObjectIDsAfterSearch', { ...payload, queryID, positions: [position] });
    } else {
      aa('clickedObjectIDs', payload);
    }
  } catch(e) {}
}

function trackAlgoliaConversion(objectID, queryID, eventName) {
  if (typeof aa !== 'function' || !objectID) return;
  try {
    const payload = { eventName, index: ALGOLIA_INDEX, objectIDs: [objectID] };
    if (queryID) {
      aa('convertedObjectIDsAfterSearch', { ...payload, queryID });
    } else {
      aa('convertedObjectIDs', payload);
    }
  } catch(e) {}
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
let customSortBy;
let _algoliaShowMore = null;
var _configRefine = null;
// Refine function exposed by connectSortBy. cyclePriceSort() calls it to
// swap the active Algolia index between primary and price replicas.
// connectSortBy is the canonical InstantSearch v4 way to swap indexes —
// search.helper.setIndex() is overridden on the next render cycle.
let _sortByRefine = null;
// Tracks the currently-active Algolia index name so cyclePriceSort() knows
// what to swap to. Set in initSearchEager() once ALGOLIA_INDEX is in scope.
let _currentSortIndex = null;

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
    // Algolia records are now grouped per course with a scalar `next_date_sort`
    // (the smallest upcoming date_sort across all sessions in the group). Filter
    // on that so a course with any matching session passes the filter.
    config.numericFilters = [`next_date_sort>=${ts}`];
  }
  // Facet filters — combine provider deep link + location dropdown
  const facetFilters = [];
  if (currentFilters.provider) facetFilters.push(`provider_id:${currentFilters.provider}`);
  const locEl = document.getElementById('search-location');
  const locVal = locEl ? locEl.value : '';
  if (locVal) facetFilters.push(`location_canonical:${locVal}`);
  if (facetFilters.length) config.facetFilters = facetFilters;
  if (_configRefine) _configRefine(config);
}

// Populate the Location dropdown with every canonical location currently in
// the Algolia index, sorted A→Z with course counts. One facet query on init
// keeps this in sync with what the search grid will return without round-
// tripping Supabase.
async function populateLocationDropdown() {
  const sel = document.getElementById('search-location');
  if (!sel || !searchClient) return;
  try {
    const result = await searchClient.search([{
      indexName: ALGOLIA_INDEX,
      params: { facets: ['location_canonical'], hitsPerPage: 0, query: '' },
    }]);
    const facets = (result.results && result.results[0] && result.results[0].facets && result.results[0].facets.location_canonical) || {};
    const sorted = Object.keys(facets).sort();
    const prev = sel.value;
    sel.innerHTML = '<option value="">Anywhere</option>' +
      sorted.map(loc => `<option value="${loc}">${loc} (${facets[loc]})</option>`).join('');
    if (prev) sel.value = prev;
  } catch (e) { /* leave the "Anywhere" placeholder in place on failure */ }
}

// Three-state cycle: off (primary, asc(next_date_sort)) → asc → desc → off.
// Swaps the active Algolia index via search.helper.setIndex(); replicas
// inherit records and faceting from the primary so existing facetFilters /
// numericFilters keep applying through the swap.
function cyclePriceSort() {
  const btn = document.getElementById('sort-price');
  if (!btn || !_sortByRefine) return;
  const next = ({ off: 'asc', asc: 'desc', desc: 'off' })[btn.dataset.state || 'off'];
  btn.dataset.state = next;
  if (next === 'off') {
    btn.textContent = 'Price';
    btn.classList.remove('active');
    _currentSortIndex = ALGOLIA_INDEX;
  } else if (next === 'asc') {
    btn.textContent = 'Price ↑';
    btn.classList.add('active');
    _currentSortIndex = `${ALGOLIA_INDEX}_price_asc`;
  } else {
    btn.textContent = 'Price ↓';
    btn.classList.add('active');
    _currentSortIndex = `${ALGOLIA_INDEX}_price_desc`;
  }
  _sortByRefine(_currentSortIndex);
}

function initSearch() {
  // SEO landing pages render server-rendered static cards and must not let Algolia
  // hydrate on page load — doing so causes Cumulative Layout Shift, which directly
  // harms ranking. See CLAUDE.md → "The Algolia hydration pattern (CLS critical)".
  if (document.body && document.body.dataset.seoPage === 'true') {
    initSearchLazy();
    return;
  }
  initSearchEager();
}

function _composeSeoQuery() {
  // Body-level filters take precedence in this order:
  //   data-filter-query   → used verbatim
  //   data-filter-activity + data-filter-location → joined with a space
  //   one of activity/location alone → that single value
  const ds = document.body.dataset;
  if (ds.filterQuery) return ds.filterQuery;
  return [ds.filterActivity, ds.filterLocation].filter(Boolean).join(' ');
}

function initSearchLazy() {
  const queryInput = document.getElementById('search-query');
  const dateInput  = document.getElementById('search-date');
  const locInput   = document.getElementById('search-location');
  const sortPrice  = document.getElementById('sort-price');
  const loadMore   = document.querySelector('.load-more-btn');
  const ds = document.body.dataset;

  // Hydrate visible inputs from data-filter-* so the search UI matches the static cards
  // before Algolia takes over. These values are read by initSearchEager() on activation.
  const seoQuery = _composeSeoQuery();
  if (queryInput && seoQuery) queryInput.value = seoQuery;
  if (dateInput && ds.filterDate) {
    dateInput.value = ds.filterDate;
    updateDateChip();
  }
  if (ds.filterProvider) currentFilters.provider = ds.filterProvider;

  // The static load-more button (if any) has an inline onclick="loadMore()" carried
  // over from the legacy V1 markup; loadMore() is undefined under V2, so neutralise it
  // before any user click can throw a ReferenceError.
  if (loadMore) loadMore.removeAttribute('onclick');

  let activated = false;
  const activate = () => {
    if (activated) return;
    activated = true;
    initSearchEager();
  };

  // Any meaningful interaction with the filter UI flips us into live-search mode.
  // { once: true } removes the listener after firing so we never double-init Algolia.
  if (queryInput) {
    queryInput.addEventListener('focus', activate, { once: true });
    queryInput.addEventListener('input', activate, { once: true });
  }
  if (dateInput) {
    dateInput.addEventListener('focus',  activate, { once: true });
    dateInput.addEventListener('change', activate, { once: true });
  }
  if (locInput) {
    locInput.addEventListener('focus',  activate, { once: true });
    locInput.addEventListener('change', activate, { once: true });
  }
  if (sortPrice) sortPrice.addEventListener('click', activate, { once: true });
  if (loadMore) loadMore.addEventListener('click', activate, { once: true });
}

function initSearchEager() {
  // Instantiate Algolia client + connectors now that the body script has defined the constants
  initAlgoliaInsights();
  searchClient = algoliasearch(ALGOLIA_APP_ID, ALGOLIA_SEARCH_KEY);
  _currentSortIndex = ALGOLIA_INDEX;
  search = instantsearch({
    indexName: ALGOLIA_INDEX,
    searchClient,
    routing: false,
    insights: true,  // auto-fires viewedObjectIDsAfterSearch and decorates hits with __queryID / __position
  });

  // Populate the Location dropdown from Algolia's facet counts.
  // Fire-and-forget — the search grid hydrates independently.
  populateLocationDropdown();
  const locInput = document.getElementById('search-location');
  if (locInput) {
    locInput.addEventListener('change', applyConfigFilters);
  }

  customSearchBox = instantsearch.connectors.connectSearchBox(
    ({ refine }, isFirstRender) => {
      if (isFirstRender) {
        const input = document.getElementById('search-query');
        let timer;
        input.addEventListener('input', () => {
          clearTimeout(timer);
          timer = setTimeout(() => refine(input.value), 300);
        });
        // SEO pages pre-populate #search-query via data-filter-query before
        // initSearchEager() runs. Algolia's connectSearchBox only calls refine()
        // on input events, so a hydrated value would otherwise sit in the input
        // without filtering results. Push it into Algolia state on first render.
        if (input.value) refine(input.value);
      }
    }
  );

  customInfiniteHits = instantsearch.connectors.connectInfiniteHits(
    ({ hits, showMore, isLastPage, results }, isFirstRender) => {
      const wrap = document.getElementById('load-more-wrap');

      const mapped = hits.map(mapHit);
      currentCourses = mapped;
      totalCount = results ? results.nbHits : mapped.length;
      _algoliaShowMore = showMore;

      // renderCards() (in js/cards.js) owns the grid render: groups per-session
      // hits by (provider_id, title_hash) into multi-date cards, writes the
      // empty-state markup, and updates #results-count + addRemoveReadyListeners.
      renderCards(mapped, false);
      if (wrap) wrap.style.display = mapped.length === 0 ? 'none' : (isLastPage ? 'none' : 'block');
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

  customSortBy = instantsearch.connectors.connectSortBy(
    ({ refine }, isFirstRender) => {
      _sortByRefine = refine;
    }
  );

  // Set default "from" date to tomorrow — but don't overwrite a value already
  // set by initSearchLazy() from data-filter-date on an SEO page.
  const dateInput = document.getElementById('search-date');
  if (!dateInput.value) {
    const tmrw = new Date();
    tmrw.setDate(tmrw.getDate() + 1);
    dateInput.value = tmrw.toISOString().split('T')[0];
  }
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
    customSortBy({
      // First item must equal the primary index — connectSortBy uses
      // it as the "default" / off state. Replicas inherit records and
      // faceting from the primary; only customRanking differs.
      items: [
        { value: ALGOLIA_INDEX,                       label: 'Default' },
        { value: `${ALGOLIA_INDEX}_price_asc`,        label: 'Price ↑' },
        { value: `${ALGOLIA_INDEX}_price_desc`,       label: 'Price ↓' },
      ],
    }),
  ]);
  search.start();

  // Apply initial date + provider filters after start
  applyConfigFilters();
}
