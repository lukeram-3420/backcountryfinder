// ── SAVED / CAIRN ──
// Saved items shape: [{ id: string, date_sort: string|null }, ...]
// Legacy localStorage may contain bare strings; getSaved() migrates on read.
//
// IMPORTANT: date_sort comes into this module from two places: (1) inline
// onclick attributes (always strings) and (2) JS calls from buildCard which
// pass session.date_sort (always a number from the unix timestamp). To avoid
// strict-equality false-negatives on lookup, we normalize every date_sort to
// a string-or-null at the boundary via _dsKey() before comparing or storing.
function _dsKey(v){
  if(v == null) return null;
  const s = String(v).trim();
  return s === '' ? null : s;
}
function getSaved(){
  try{
    const raw = JSON.parse(localStorage.getItem('bcf_saved')||'[]');
    if(!Array.isArray(raw)) return [];
    return raw
      .map(item => typeof item === 'string' ? {id: item, date_sort: null} : item)
      .filter(item => item && item.id)
      .map(item => ({id: String(item.id), date_sort: _dsKey(item.date_sort)}));
  }catch(e){return[];}
}
function setSaved(arr){try{localStorage.setItem('bcf_saved',JSON.stringify(arr));}catch(e){}}
function savedIds(){return getSaved().map(item=>item.id);}
function isSaved(id, date_sort){
  const dsParam = date_sort === undefined ? null : _dsKey(date_sort);
  const list = getSaved();
  if(dsParam === null){
    // Whole-course check: any saved entry with this id matches.
    return list.some(item => item.id === id);
  }
  return list.some(item => item.id === id && item.date_sort === dsParam);
}

// SHARED COURSES
function getSharedIds(){const p=new URLSearchParams(window.location.search);const s=p.get('shared');return s?s.split(',').filter(Boolean):[];}
function saveSharedCourses(){dismissSharedBanner();renderSaved();showPage('saved');}
function dismissSharedBanner(){const b=document.getElementById('shared-banner');if(b)b.style.display='none';}

// SHARE — popover UI lives in /js/share-widget.js (module-level singleton).
// This wrapper composes the saved-list-specific URL/title and hands them
// to openSharePopover().
function toggleSavedShare(e){
  e.stopPropagation();e.preventDefault();
  const popover=document.getElementById('saved-share-popover');
  if(!popover) return;
  const ids=savedIds().join(',');
  const shareUrl=`https://backcountryfinder.com/?shared=${ids}`;
  window.openSharePopover({
    popoverEl: popover,
    anchorEl: e.currentTarget,
    shareUrl: shareUrl,
    title: 'Backcountry experiences',
    headerText: 'share this list',
  });
}
function closeAllPopovers(){window.closeAllSharePopovers && window.closeAllSharePopovers();}

function clearMyList(){
  if(!confirm('Clear your entire saved list?')) return;
  setSaved([]);
  renderSaved();
  if(currentCourses.length>0) renderCards(currentCourses, false);
}

function toggleSave(id, date_sort){
  const ds = _dsKey(date_sort);
  const wasSaved = isSaved(id, ds);
  let s = getSaved();
  if(wasSaved){
    s = s.filter(item => !(item.id === id && item.date_sort === ds));
  } else {
    s.push({id: String(id), date_sort: ds});
  }
  setSaved(s);
  renderSaved();
  // Re-render the matching card so save-button visuals update. Preserve expanded state.
  const dsAttr = ds == null ? '' : ds;
  const btn = document.querySelector(`.save-btn[data-save-id="${id}"][data-save-date="${dsAttr}"]`);
  if(btn){
    const card = btn.closest('.course-card');
    if(card){
      const expanded = card.dataset.expanded === 'true';
      const groupKey = card.dataset.groupKey;
      const synthetic = groupKey ? rebuildSyntheticForKey(groupKey) : null;
      if(synthetic){
        const tmp = document.createElement('div');
        tmp.innerHTML = buildCard(synthetic);
        const newCard = tmp.firstElementChild;
        if(expanded && newCard) newCard.dataset.expanded = 'true';
        card.replaceWith(newCard);
        // If expanded was true, re-open the session list on the new card
        if(expanded && newCard){
          const list = newCard.querySelector('.session-list-expanded');
          const moreBtn = newCard.querySelector('.more-dates-btn');
          if(list && moreBtn){
            list.style.display = 'block';
            moreBtn.textContent = 'Hide dates ▴';
          }
        }
        addRemoveReadyListeners();
      } else {
        // Fallback (My List path): rebuildSyntheticForKey returns null because
        // currentCourses holds search-page state, not saved-page state. Just
        // toggle the button class + svg in place — renderSaved() (above) has
        // already re-rendered the My List grid, so visual sync is preserved.
        const nowSaved = !wasSaved;
        if(nowSaved){
          btn.classList.add('saved', 'remove-ready');
        } else {
          btn.classList.remove('saved', 'remove-ready');
        }
        btn.innerHTML = `${_saveSvg(nowSaved)}<span class="save-label">my list</span>`;
        addRemoveReadyListeners();
      }
    }
  }
  if(!wasSaved){
    showMicroToast();
    requestAnimationFrame(()=>{
      const newBtn=document.querySelector(`.save-btn.saved[data-save-id="${id}"][data-save-date="${dsAttr}"]`);
      if(newBtn){
        newBtn.classList.remove('remove-ready');
        newBtn.addEventListener('mouseleave',function onLeave(){
          newBtn.classList.add('remove-ready');
          newBtn.removeEventListener('mouseleave',onLeave);
        },{once:true});
      }
    });
  }
}

async function renderSaved(){
  const saved=getSaved();
  const empty=document.getElementById('saved-empty');
  const cards=document.getElementById('saved-cards');
  const toolbar=document.getElementById('saved-toolbar-wrap');
  const count=document.getElementById('saved-toolbar-count');
  if(saved.length===0){empty.style.display='flex';cards.style.display='none';if(toolbar)toolbar.style.display='none';return;}
  empty.style.display='none';cards.style.display='grid';
  if(toolbar)toolbar.style.display='block';
  if(count)count.textContent=`${saved.length} saved`;
  try {
    const uniqueIds = [...new Set(saved.map(item=>item.id))];
    const orFilter = uniqueIds.map(id=>`id.eq.${id}`).join(',');
    const res=await fetch(
      `${SUPABASE_URL}/rest/v1/courses?select=*,providers(name,rating,review_count)&or=(${orFilter})&flagged=not.is.true&auto_flagged=not.is.true`,
      {headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}}
    );
    const savedCourses=await res.json();
    const validIds = new Set(savedCourses.map(c=>c.id));
    const cleanedSaved = saved.filter(item=>validIds.has(item.id));
    if(cleanedSaved.length !== saved.length){
      setSaved(cleanedSaved);
      if(count) count.textContent=`${cleanedSaved.length} saved`;
      if(cleanedSaved.length===0){empty.style.display='flex';cards.style.display='none';if(toolbar)toolbar.style.display='none';return;}
    }
    // One card per saved entry — saving feels per-date, so the saved list
    // shows each saved (id, date_sort) as its own tile in save order.
    // Iterate `cleanedSaved` (or `saved` if nothing was cleaned) to preserve
    // the user's save order rather than Supabase's response order.
    const orderedSaved = (cleanedSaved && cleanedSaved.length === saved.length) ? saved : cleanedSaved;
    const byId = new Map(savedCourses.map(c => [c.id, c]));
    const tiles = [];
    for (const item of orderedSaved) {
      const row = byId.get(item.id);
      if (!row) continue;
      // groupCoursesForCards on a 1-element array yields a single synthetic
      // card with a single session — exactly what we want per saved entry.
      const groupedSingle = groupCoursesForCards([mapSupabaseRow(row)]);
      if (groupedSingle.length > 0) tiles.push(groupedSingle[0]);
    }
    cards.innerHTML = tiles.map(t => buildCard(t)).join('');
    addRemoveReadyListeners();
  } catch(e) {
    cards.innerHTML='<p style="padding:20px;color:var(--text-tertiary);font-size:13px;">Could not load saved courses.</p>';
  }
}

function initSharedCourses(){
  const ids=getSharedIds();if(ids.length===0)return;
  let saved=getSaved();
  const existingIds = new Set(saved.map(item=>item.id));
  ids.forEach(id=>{if(!existingIds.has(id)) saved.push({id, date_sort: null});});
  setSaved(saved);
  const banner=document.getElementById('shared-banner');
  const title=document.getElementById('shared-banner-title');
  const sub=document.getElementById('shared-banner-sub');
  if(banner&&title&&sub){title.textContent=`${ids.length} experience${ids.length!==1?'s':''} saved to your list`;sub.textContent='Someone shared these with you — view them in your saved tab.';banner.style.display='block';}
  if(window.history&&window.history.replaceState)window.history.replaceState({},'',window.location.pathname);
}

async function openEmailListModal(){
  const saved=getSaved();
  if(saved.length===0)return;
  const idSet=new Set(saved.map(item=>item.id));
  let courses=currentCourses.filter(c=>idSet.has(c.id));
  if(courses.length===0){
    try{
      const orFilter=[...idSet].map(id=>`id.eq.${id}`).join(',');
      const res=await fetch(`${SUPABASE_URL}/rest/v1/courses?select=*,providers(name)&or=(${orFilter})&flagged=not.is.true&auto_flagged=not.is.true`,{headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}});
      courses=await res.json();
    }catch(e){courses=[];}
  }
  if(courses.length===0)return;
  document.getElementById('elm-courses-preview').innerHTML=courses.map(c=>`<div class="elm-course-line">🏔 ${c.title.split('—')[0].trim()}${c.date_display?' · '+c.date_display:''}${c.price?' · $'+c.price:''}</div>`).join('');
  document.getElementById('elm-email').value='';
  document.getElementById('elm-form-content').style.display='block';
  document.getElementById('elm-success').style.display='none';
  document.getElementById('email-list-modal').classList.add('active');
}

async function submitEmailList(){
  const email=document.getElementById('elm-email').value.trim();
  const optIn=document.getElementById('elm-optin').checked;
  if(!email||!email.includes('@')){document.getElementById('elm-email').style.borderColor='#e24b4a';document.getElementById('elm-email').focus();return;}
  document.getElementById('elm-email').style.borderColor='';
  const btn=document.querySelector('.elm-submit');btn.textContent='sending...';btn.disabled=true;
  const saved=getSaved();
  const idSet=new Set(saved.map(item=>item.id));
  let rawCourses=currentCourses.filter(c=>idSet.has(c.id));
  if(rawCourses.length===0){
    try{
      const orFilter=[...idSet].map(id=>`id.eq.${id}`).join(',');
      const res=await fetch(`${SUPABASE_URL}/rest/v1/courses?select=*,providers(name,rating)&or=(${orFilter})&flagged=not.is.true&auto_flagged=not.is.true`,{headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}});
      rawCourses=await res.json();
    }catch(e){rawCourses=[];}
  }
  const courses=rawCourses.map(c=>({
    id:          c.id,
    title:       c.title,
    provider:    (c.providers&&c.providers.name)||c.provider_id||'',
    badge:       c.badge_canonical||c.badge||'',
    date:        c.date_display||'Flexible dates',
    location:    c.location_canonical||c.location_raw||'',
    price:       c.price||0,
    avail:       c.avail||'open',
    spots:       c.spots_remaining,
    rating:      (c.providers&&c.providers.rating)||'',
    url:         c.booking_url||'',
  }));
  try{await fetch(SUPABASE_EDGE_URL,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${SUPABASE_KEY}`,'apikey':SUPABASE_KEY},body:JSON.stringify({email,courses,optIn})});}catch(e){}
  document.getElementById('elm-form-content').style.display='none';
  document.getElementById('elm-success').style.display='block';
  setTimeout(closeEmailListModal,3500);
}
