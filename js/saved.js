// ── SAVED / CAIRN ──
// Saved items shape: [{ id: string, date_sort: string|null }, ...]
// Legacy localStorage may contain bare strings; getSaved() migrates on read.
function getSaved(){
  try{
    const raw = JSON.parse(localStorage.getItem('bcf_saved')||'[]');
    if(!Array.isArray(raw)) return [];
    return raw.map(item => typeof item === 'string' ? {id: item, date_sort: null} : item)
              .filter(item => item && item.id);
  }catch(e){return[];}
}
function setSaved(arr){try{localStorage.setItem('bcf_saved',JSON.stringify(arr));}catch(e){}}
function savedIds(){return getSaved().map(item=>item.id);}
function isSaved(id, date_sort){
  if(date_sort === undefined) date_sort = null;
  const list = getSaved();
  if(date_sort === null){
    // Whole-course check: any saved entry with this id matches
    return list.some(item => item.id === id);
  }
  return list.some(item => item.id === id && item.date_sort === date_sort);
}

// SHARED COURSES
function getSharedIds(){const p=new URLSearchParams(window.location.search);const s=p.get('shared');return s?s.split(',').filter(Boolean):[];}
function saveSharedCourses(){dismissSharedBanner();renderSaved();showPage('saved');}
function dismissSharedBanner(){const b=document.getElementById('shared-banner');if(b)b.style.display='none';}

// SHARE
function buildSharePopoverHTML(courseId,isMultiple){
  const idList=isMultiple?savedIds():[String(courseId)];
  const ids=idList.join(',');
  const shareUrl=`https://backcountryfinder.com/?shared=${ids}`;
  const idSet=new Set(idList);
  const courses=isMultiple?currentCourses.filter(c=>idSet.has(c.id)):currentCourses.filter(c=>c.id===courseId);
  const waMsg=encodeURIComponent(`These courses and dates work for me, take a look — ${shareUrl}`);
  const smsMsg=encodeURIComponent(`These courses and dates work for me, take a look — ${shareUrl}`);
  const canNative=typeof navigator.share==='function';
  const safeTitle=(isMultiple?'Backcountry experiences':(courses[0]?.title||'')).replace(/"/g,'&quot;');
  return`<div class="share-popover-title">${isMultiple?'share this list':'share this experience'}</div>
    <div class="share-popover-btns">
      <a class="sp-btn sp-btn-wa" href="https://wa.me/?text=${waMsg}" target="_blank" rel="noopener"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/><path d="M12 0C5.373 0 0 5.373 0 12c0 2.136.564 4.14 1.548 5.871L0 24l6.335-1.521A11.934 11.934 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.818a9.818 9.818 0 01-5.006-1.369l-.36-.214-3.732.895.944-3.617-.235-.374A9.818 9.818 0 012.182 12C2.182 6.57 6.57 2.182 12 2.182S21.818 6.57 21.818 12 17.43 21.818 12 21.818z"/></svg>WhatsApp</a>
      <a class="sp-btn sp-btn-im" href="sms:&body=${smsMsg}"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 4.925 0 11c0 3.39 1.643 6.425 4.219 8.399L3 24l4.797-2.561A13.03 13.03 0 0012 22c6.627 0 12-4.925 12-11S18.627 0 12 0z"/></svg>iMessage</a>
      ${canNative?`<button class="sp-btn sp-btn-native" data-native-share="${shareUrl}" data-native-title="${safeTitle}">share via...</button>`:''}
      <button class="sp-btn sp-btn-copy" data-copy-url="${shareUrl}">copy link</button>
    </div>
    <div class="sp-url">${shareUrl}</div>`;}

function positionPopover(popover,btn){
  const rect=btn.getBoundingClientRect();
  const popW=Math.min(230,window.innerWidth-32);
  popover.style.width=popW+'px';
  let left=rect.right-popW;
  if(left<8)left=8;
  if(left+popW>window.innerWidth-8)left=window.innerWidth-popW-8;
  popover.style.left=left+'px';popover.style.right='auto';
  const spaceBelow=window.innerHeight-rect.bottom;
  if(spaceBelow>=220){popover.style.top=(rect.bottom+6)+'px';popover.style.bottom='auto';}
  else{popover.style.bottom=(window.innerHeight-rect.top+6)+'px';popover.style.top='auto';}
}

function toggleSavedShare(e){
  e.stopPropagation();e.preventDefault();
  const popover=document.getElementById('saved-share-popover');
  const wasOpen=popover?.classList.contains('active');
  closeAllPopovers();
  if(!wasOpen&&popover){popover.innerHTML=buildSharePopoverHTML(null,true);positionPopover(popover,e.currentTarget);popover.classList.add('active');}
}

function closeAllPopovers(){document.querySelectorAll('.share-popover').forEach(p=>p.classList.remove('active'));}

document.addEventListener('click',function(e){
  const copyBtn=e.target.closest('[data-copy-url]');
  if(copyBtn){e.stopPropagation();copyShareLink(copyBtn.getAttribute('data-copy-url'),copyBtn);return;}
  const nativeBtn=e.target.closest('[data-native-share]');
  if(nativeBtn){e.stopPropagation();nativeShare(nativeBtn.getAttribute('data-native-share'),nativeBtn.getAttribute('data-native-title'));return;}
  if(!e.target.closest('.share-popover'))closeAllPopovers();
});

async function copyShareLink(url,btn){try{await navigator.clipboard.writeText(url);const orig=btn.textContent;btn.textContent='copied!';setTimeout(()=>{btn.textContent=orig;},2000);}catch(e){prompt('Copy this link:',url);}}
async function nativeShare(url,title){try{await navigator.share({title:title||'BackcountryFinder',url});}catch(e){}}

function clearMyList(){
  if(!confirm('Clear your entire saved list?')) return;
  setSaved([]);
  renderSaved();
  if(currentCourses.length>0) renderCards(currentCourses, false);
}

function toggleSave(id, date_sort){
  if(date_sort === undefined) date_sort = null;
  const wasSaved = isSaved(id, date_sort);
  let s = getSaved();
  if(wasSaved){
    s = s.filter(item => !(item.id === id && item.date_sort === date_sort));
  } else {
    s.push({id, date_sort});
  }
  setSaved(s);
  renderSaved();
  // Re-render the card so save button visuals update. Preserve expanded state.
  const dsAttr = date_sort == null ? '' : String(date_sort);
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
            const n = parseInt(list.dataset.count || '0', 10);
            moreBtn.textContent = 'Hide dates ▴';
          }
        }
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
    // Group saved courses by (provider_id, title_hash) so the saved list mirrors
    // the search grid: one card with multiple saved sessions, not one per session.
    const groups = groupCoursesForCards(savedCourses.map(mapSupabaseRow));
    cards.innerHTML = groups.map(g => buildCard(g)).join('');
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
