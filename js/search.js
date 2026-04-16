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
const searchClient = algoliasearch(ALGOLIA_APP_ID, ALGOLIA_SEARCH_KEY);
const search = instantsearch({
  indexName: ALGOLIA_INDEX,
  searchClient,
  routing: false,
});

// ── Search box connector ──
const customSearchBox = instantsearch.connectors.connectSearchBox(
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

// ── Infinite hits connector (card grid + load more) ──
let _algoliaShowMore = null;
const customInfiniteHits = instantsearch.connectors.connectInfiniteHits(
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

// ── Date + provider filter via configure connector ──
var _configRefine = null;
const customConfigure = instantsearch.connectors.connectConfigure(
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
