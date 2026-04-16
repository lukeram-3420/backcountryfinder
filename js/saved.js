function toggleSave(id){
  const wasUnsaved = !isSaved(id);
  let s=getSaved();s=s.includes(id)?s.filter(x=>x!==id):[...s,id];setSaved(s);
  renderSaved();
  const btn = document.querySelector(`[onclick="toggleSave('${id}')"]`);
  if (btn) {
    const card = btn.closest('.course-card');
    if (card) {
      const course = currentCourses.find(c => c.id === id);
      if (course) card.outerHTML = buildCard(course);
    }
  }
  if(wasUnsaved){
    showMicroToast();
    requestAnimationFrame(()=>{
      const newBtn=document.querySelector(`[onclick="toggleSave('${id}')"]`);
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
    const ids=saved.map(id=>`id.eq.${id}`).join(',');
    const res=await fetch(
      `${SUPABASE_URL}/rest/v1/courses?select=*,providers(name,rating,review_count)&or=(${ids})&flagged=not.is.true&auto_flagged=not.is.true`,
      {headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}}
    );
    const savedCourses=await res.json();
    const validIds = savedCourses.map(c=>c.id);
    const cleanedSaved = saved.filter(id=>validIds.includes(id));
    if(cleanedSaved.length !== saved.length) {
      setSaved(cleanedSaved);
      if(count) count.textContent=`${cleanedSaved.length} saved`;
      if(cleanedSaved.length===0){empty.style.display='flex';cards.style.display='none';if(toolbar)toolbar.style.display='none';return;}
    }
    cards.innerHTML=savedCourses.map(c=>buildCard(c)).join('');
    addRemoveReadyListeners();
  } catch(e) {
    cards.innerHTML='<p style="padding:20px;color:var(--text-tertiary);font-size:13px;">Could not load saved courses.</p>';
  }
}

function initSharedCourses(){
  const ids=getSharedIds();if(ids.length===0)return;
  let saved=getSaved();ids.forEach(id=>{if(!saved.includes(id))saved.push(id);});setSaved(saved);
  const banner=document.getElementById('shared-banner');
  const title=document.getElementById('shared-banner-title');
  const sub=document.getElementById('shared-banner-sub');
  if(banner&&title&&sub){title.textContent=`${ids.length} experience${ids.length!==1?'s':''} saved to your list`;sub.textContent='Someone shared these with you — view them in your saved tab.';banner.style.display='block';}
  if(window.history&&window.history.replaceState)window.history.replaceState({},'',window.location.pathname);
}

async function openEmailListModal(){
  const saved=getSaved();
  if(saved.length===0)return;
  let courses=currentCourses.filter(c=>saved.includes(c.id));
  if(courses.length===0){
    try{
      const ids=saved.map(id=>`id.eq.${id}`).join(',');
      const res=await fetch(`${SUPABASE_URL}/rest/v1/courses?select=*,providers(name)&or=(${ids})&flagged=not.is.true&auto_flagged=not.is.true`,{headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}});
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
  let rawCourses=currentCourses.filter(c=>saved.includes(c.id));
  if(rawCourses.length===0){
    try{
      const ids=saved.map(id=>`id.eq.${id}`).join(',');
      const res=await fetch(`${SUPABASE_URL}/rest/v1/courses?select=*,providers(name,rating)&or=(${ids})&flagged=not.is.true&auto_flagged=not.is.true`,{headers:{'apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`}});
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
