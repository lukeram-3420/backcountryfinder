async function logClick(course) {
  try {
    await fetch(`${SUPABASE_URL}/rest/v1/click_events`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify({
        provider_name: (course.providers && course.providers.name) || course.provider_id || '',
        course_type:   course.activity_canonical || course.activity_raw || '',
        location:      course.location_canonical || course.location_raw || '',
        price:         course.price || null,
        booking_url:   course.booking_url || '',
        session_id:    SESSION_ID
      })
    });
  } catch(e) { /* non-blocking — never interrupt the user */ }
}

async function loadActivityLabels() {
  try {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/activity_labels?select=activity,label`,
      { headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` } }
    );
    const rows = await res.json();
    rows.forEach(r => { ACTIVITY_LABELS[r.activity] = r.label; });
  } catch(e) { console.warn('Could not load activity labels:', e); }
}

function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(n=>n.classList.remove('active'));
  document.querySelectorAll('.mnav-item').forEach(n=>n.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  const nl=document.getElementById('nav-'+name);
  const ml=document.getElementById('mnav-'+name);
  if(nl)nl.classList.add('active');
  if(ml)ml.classList.add('active');
  if(name==='saved')renderSaved();
  if(name==='providers')loadProviders();
  window.scrollTo(0,0);
}

// NOTIFY MODAL
let _notifyProvider = '';
let _notifyCourse = '';

function openNotifyModal(idOrProvider, courseTitle, providerId) {
  _notifyProvider = providerId || idOrProvider;
  _notifyCourse = courseTitle;
  document.getElementById('notify-course-name').textContent = courseTitle;
  document.getElementById('notify-email').value = '';
  document.getElementById('notify-form-content').style.display = 'block';
  document.getElementById('notify-success').style.display = 'none';
  document.getElementById('notify-modal').classList.add('active');
}
function closeNotifyModal(e) {
  if (!e || e.target === document.getElementById('notify-modal'))
    document.getElementById('notify-modal').classList.remove('active');
}
async function submitNotify() {
  const email = document.getElementById('notify-email').value.trim();
  if (!email || !email.includes('@')) {
    document.getElementById('notify-email').style.borderColor = '#e24b4a';
    document.getElementById('notify-email').focus();
    return;
  }
  document.getElementById('notify-email').style.borderColor = '';
  try {
    await fetch(`${SUPABASE_URL}/rest/v1/notifications`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify({
        email,
        provider_id: _notifyProvider,
        course_title: _notifyCourse,
      })
    });
    fetch('https://owzrztaguehebkatnatc.supabase.co/functions/v1/notify-signup-confirmation', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, course_title: _notifyCourse, provider_name: ''})
    });
  } catch(e) {}
  document.getElementById('notify-form-content').style.display = 'none';
  document.getElementById('notify-success').style.display = 'block';
  setTimeout(closeNotifyModal, 2500);
}

function closeEmailListModal(e){if(!e||e.target===document.getElementById('email-list-modal'))document.getElementById('email-list-modal').classList.remove('active');}
async function saveEmail(email,courseTitle,signupType){
  if(!email||!email.includes('@'))return false;
  try{const res=await fetch(`${SUPABASE_URL}/rest/v1/email_signups`,{method:'POST',headers:{'Content-Type':'application/json','apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`,'Prefer':'return=minimal'},body:JSON.stringify({email,course_title:courseTitle||null,signup_type:signupType})});return res.ok;}catch(e){return false;}
}

// PROVIDER MODAL
function openProviderModal(tab){
  switchProviderTab(tab||'suggest');
  ['suggest-form-content','listed-form-content'].forEach(id=>{const el=document.getElementById(id);if(el)el.style.display='block';});
  ['suggest-success','listed-success'].forEach(id=>{const el=document.getElementById(id);if(el)el.style.display='none';});
  ['suggest-school','suggest-website','suggest-contact-at-provider','suggest-submitter-name','suggest-email','suggest-notes','listed-school','listed-website','listed-name','listed-email','listed-notes'].forEach(id=>{const el=document.getElementById(id);if(el)el.value='';});
  document.getElementById('provider-modal').classList.add('active');
}
function closeProviderModal(e){if(!e||e.target===document.getElementById('provider-modal'))document.getElementById('provider-modal').classList.remove('active');}
function switchProviderTab(tab){document.querySelectorAll('.pmodal-tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.pmodal-form').forEach(f=>f.classList.remove('active'));document.getElementById('tab-'+tab).classList.add('active');document.getElementById('form-'+tab).classList.add('active');}
async function submitSuggest(){
  const school=document.getElementById('suggest-school').value.trim();
  const submitterName=document.getElementById('suggest-submitter-name').value.trim();
  const email=document.getElementById('suggest-email').value.trim();
  if(!school){document.getElementById('suggest-school').focus();return;}
  if(!submitterName){document.getElementById('suggest-submitter-name').focus();return;}
  if(!email||!email.includes('@')){document.getElementById('suggest-email').focus();return;}
  const data={
    type:'suggest',
    submission_type:'suggest',
    school_name:school,
    website:document.getElementById('suggest-website').value.trim()||null,
    contact_at_provider:document.getElementById('suggest-contact-at-provider').value.trim()||null,
    submitter_name:submitterName,
    contact_email:email,
    notes:document.getElementById('suggest-notes').value.trim()||null
  };
  await Promise.all([
    fetch(`${SUPABASE_URL}/rest/v1/provider_submissions`,{method:'POST',headers:{'Content-Type':'application/json','apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`,'Prefer':'return=minimal'},body:JSON.stringify({submission_type:'suggest',school_name:school,website:data.website,contact_email:email,notes:data.notes})}),
    fetch(NOTIFY_URL,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${SUPABASE_KEY}`,'apikey':SUPABASE_KEY},body:JSON.stringify(data)})
  ]);
  document.getElementById('suggest-form-content').style.display='none';document.getElementById('suggest-success').style.display='block';setTimeout(closeProviderModal,3000);
}
async function submitGetListed(){
  const school=document.getElementById('listed-school').value.trim();
  const email=document.getElementById('listed-email').value.trim();
  const name=document.getElementById('listed-name').value.trim();
  if(!school){document.getElementById('listed-school').focus();return;}
  if(!email||!email.includes('@')){document.getElementById('listed-email').focus();return;}
  const data={
    type:'get_listed',
    submission_type:'get_listed',
    school_name:school,
    website:document.getElementById('listed-website').value.trim()||null,
    contact_name:name||null,
    contact_email:email,
    notes:document.getElementById('listed-notes').value.trim()||null
  };
  await Promise.all([
    fetch(`${SUPABASE_URL}/rest/v1/provider_submissions`,{method:'POST',headers:{'Content-Type':'application/json','apikey':SUPABASE_KEY,'Authorization':`Bearer ${SUPABASE_KEY}`,'Prefer':'return=minimal'},body:JSON.stringify({submission_type:'get_listed',school_name:school,website:data.website,contact_name:name||null,contact_email:email,notes:data.notes})}),
    fetch(NOTIFY_URL,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${SUPABASE_KEY}`,'apikey':SUPABASE_KEY},body:JSON.stringify(data)})
  ]);
  document.getElementById('listed-form-content').style.display='none';document.getElementById('listed-success').style.display='block';setTimeout(closeProviderModal,3500);
}

// TOAST
let toastTimer=null;
function showToast(){clearTimeout(toastTimer);const toast=document.getElementById('book-toast');toast.innerHTML=`<div class="toast-top"><div><div class="toast-title">Heading to book?</div><div class="toast-sub">Get new experience alerts — no spam.</div></div><button class="toast-close" onclick="closeToast()">×</button></div><div class="toast-row"><input class="toast-input" type="email" id="toast-email" placeholder="your@email.com"><button class="toast-btn" onclick="submitToast()">yes please</button></div><button class="toast-dismiss" onclick="closeToast()">no thanks</button>`;toast.classList.add('active');toastTimer=setTimeout(closeToast,12000);}
function closeToast(){document.getElementById('book-toast').classList.remove('active');clearTimeout(toastTimer);}
async function submitToast(){const emailEl=document.getElementById('toast-email');if(!emailEl)return;const email=emailEl.value.trim();if(!email||!email.includes('@')){emailEl.style.borderColor='#e24b4a';emailEl.focus();return;}emailEl.style.borderColor='';await saveEmail(email,null,'book_now_toast');document.getElementById('book-toast').innerHTML=`<div style="text-align:center;padding:4px 0;"><div style="font-size:24px;margin-bottom:6px;">✓</div><div style="font-size:13px;font-weight:700;">You're on the list</div><div style="font-size:12px;color:var(--text-tertiary);margin-top:4px;font-weight:500;">We'll send you new experience alerts.</div></div>`;setTimeout(closeToast,2500);}

// MICRO TOAST
let microToastTimer = null;
function showMicroToast() {
  const toast = document.getElementById('micro-toast');
  if (!toast) return;
  clearTimeout(microToastTimer);
  toast.classList.add('show');
  microToastTimer = setTimeout(() => toast.classList.remove('show'), 3000);
}
