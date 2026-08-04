/* ───────── EMAIL CAMPAIGN ───────── */
const addrEl=document.getElementById('gmail-addr'), passEl=document.getElementById('gmail-pass'), linkEl=document.getElementById('form-link');
// Manual-emails section
const manualEnable=document.getElementById('manual-enable'), manualFields=document.getElementById('manual-fields'),
      manualEmailsEl=document.getElementById('manual-emails'), manualCountEl=document.getElementById('manual-count');
function parseManualEmails(){
  if(!manualEmailsEl) return [];
  const raw=manualEmailsEl.value.split(/[\s,;]+/).map(s=>s.trim().toLowerCase()).filter(Boolean);
  const seen=new Set(), out=[];
  raw.forEach(e=>{ if(e.includes('@')&&!seen.has(e)){ seen.add(e); out.push(e); } });
  return out;
}
function manualActive(){ return manualEnable && manualEnable.checked; }
const sendBtn=document.getElementById('send-btn'), sendInfo=document.getElementById('send-info');
const previewBtn=document.getElementById('preview-toggle'), previewBox=document.getElementById('email-preview');
/* time-tracking controls — off by default, persisted server-side (survives restarts/reloads) */
const trackEnable=document.getElementById('track-enable'), trackFields=document.getElementById('track-fields');
const trackBaseEl=document.getElementById('track-base'), trackEntryEl=document.getElementById('track-entry');
// Inject the slider knob style (still used by the manual-emails toggle).
(function(){
  const style=document.createElement('style');
  style.textContent='.slider:before{content:"";position:absolute;height:15px;width:15px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.25s;transform:var(--knob,translateX(0))}';
  document.head.appendChild(style);
})();
// Default the public base URL to the current origin if the user hasn't set one.
if(trackBaseEl && !trackBaseEl.value) trackBaseEl.value = window.location.origin;
function validEmails(){ return candidates.filter(c=>c.email && c.email.includes('@')).length; }
function refreshEmailCount(){ const n=validEmails(); if(!addrEl.value||!passEl.value||!linkEl.value) sendInfo.textContent=n>0?`${n} candidate${n>1?'s':''} ready — fill in all fields`:'Parse CVs first, then fill in all fields'; validateEmailForm(); }
function validateEmailForm(){ const recipients = manualActive()? parseManualEmails().length : validEmails(); const ok=addrEl.value.includes('@')&&passEl.value.length>=8&&linkEl.value.startsWith('http')&&recipients>0; sendBtn.disabled=!ok; if(ok)sendInfo.textContent=`${recipients} email${recipients>1?'s':''} will be sent`; }
[addrEl,passEl,linkEl].forEach(el=>el.addEventListener('input',validateEmailForm));
addrEl.addEventListener('blur', () => { if (addrEl.value.trim()) clearFieldError(addrEl); else showFieldError(addrEl, 'Enter the sender Gmail address.'); });
passEl.addEventListener('blur', () => { if (passEl.value.trim()) clearFieldError(passEl); else showFieldError(passEl, 'Enter the Gmail App Password.'); });
linkEl.addEventListener('blur', () => { if (linkEl.value.trim()) clearFieldError(linkEl); else showFieldError(linkEl, 'Enter the Google Forms assessment link.'); });
// Manual-emails runtime wiring (placed here so sendBtn/validateEmailForm exist)
if(manualEnable){
  const _syncManualUI=function(){
    manualFields.classList.toggle('hidden',!manualEnable.checked);
    const knob=manualEnable.closest('.switch').querySelector('.slider');
    if(knob){ knob.style.setProperty('--knob', manualEnable.checked?'translateX(17px)':'translateX(0)'); knob.style.background=manualEnable.checked?'#60a5fa':'rgba(255,255,255,0.15)'; }
    validateEmailForm();
  };
  manualEnable.addEventListener('change',_syncManualUI);
  _syncManualUI();
}
// Form-tracking runtime wiring — off by default, persisted server-side.
if(trackEnable){
  const _syncTrackUI=function(){
    trackFields.classList.toggle('hidden',!trackEnable.checked);
    const knob=trackEnable.closest('.switch').querySelector('.slider');
    if(knob){ knob.style.setProperty('--knob', trackEnable.checked?'translateX(17px)':'translateX(0)'); knob.style.background=trackEnable.checked?'#60a5fa':'rgba(255,255,255,0.15)'; }
  };
  trackEnable.addEventListener('change', async () => {
    _syncTrackUI();
    try {
      await fetch('/api/tracking/enabled', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled: trackEnable.checked})});
    } catch(e){ console.error('Failed to save tracking toggle:', e); }
  });
  // Load the persisted choice so it survives restarts/reloads (defaults to
  // off — trackEnable.checked is already false from the unchecked HTML).
  fetch('/api/tracking/enabled').then(r=>r.json()).then(d=>{
    trackEnable.checked = !!d.enabled;
    _syncTrackUI();
  }).catch(()=>{ _syncTrackUI(); });
}
if(manualEmailsEl){
  manualEmailsEl.addEventListener('input',()=>{
    const n=parseManualEmails().length;
    manualCountEl.textContent = n? `${n} valid email${n>1?'s':''} will be sent` : 'No emails added yet';
    validateEmailForm();
  });
}
previewBtn.addEventListener('click',()=>{previewBox.classList.toggle('hidden');});
sendBtn.addEventListener('click', async () => {
  const gmail_address=addrEl.value.trim(), app_password=passEl.value.trim(), form_link=linkEl.value.trim();
  const requiredFields = [
    { input: addrEl, message: 'Enter the sender Gmail address.' },
    { input: passEl, message: 'Enter the Gmail App Password.' },
    { input: linkEl, message: 'Enter the Google Forms assessment link.' },
  ];
  if (manualActive()) {
    requiredFields.push({ input: manualEmailsEl, message: 'Add at least one valid email, or turn off manual mode.',
                          test: () => parseManualEmails().length > 0 });
  }
  if (trackEnable && trackEnable.checked) {
    requiredFields.push({ input: trackBaseEl, message: 'Enter the public URL where this server is reachable.' });
  }
  if (!validateRequired(requiredFields)) return;

  const payload={gmail_address,app_password,form_link};
  if(manualActive()){
    const list=parseManualEmails();
    payload.manual_emails=list.join('\n');
  }
  if(trackEnable && trackEnable.checked){
    payload.tracking_base_url=(trackBaseEl.value.trim()||window.location.origin);
    const entry=trackEntryEl.value.trim();
    if(entry) payload.email_entry_id=entry;
  }
  sendBtn.disabled=true;
  sendBtn.innerHTML=`<svg width="15" height="15" viewBox="0 0 15 15" fill="none" style="animation:spin 0.9s linear infinite;flex-shrink:0"><circle cx="7.5" cy="7.5" r="5.5" stroke="currentColor" stroke-width="2" stroke-dasharray="17 7" stroke-linecap="round"/></svg> Sending…`;
  rail.classList.add('on');
  try {
    const res=await fetch('/api/send-emails',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok)throw new Error(errDetail(data, res.status));
    document.getElementById('er-sent').textContent=`${data.sent} sent`;
    document.getElementById('er-fail').textContent=`${data.failed} failed`;
    document.getElementById('er-skip').textContent=`${data.skipped_no_email} skipped`;
    const rows=document.getElementById('er-rows'); rows.innerHTML='';
    (data.results||[]).forEach((r,i)=>{const div=document.createElement('div');div.className='email-result-row';div.style.animationDelay=`${i*30}ms`;div.innerHTML=`<div class="er-name">${r.name||'—'}</div><div class="er-email">${r.email}</div><span class="er-badge ${r.status==='sent'?'sent':'fail'}">${r.status==='sent'?'✓ Sent':'✗ Failed'}</span>`;rows.appendChild(div);});
    document.getElementById('email-results').classList.remove('hidden');
    const trackMsg=(data.tracked>0)?` · ${data.tracked} tracked ⏱`:'';
    toast(`Campaign complete — ${data.sent} sent, ${data.failed} failed${trackMsg}`, data.failed===0?'ok':'inf');
  } catch(err){ toast(`Email error: ${err.message}`, 'err'); }
  finally { rail.classList.remove('on'); validateEmailForm(); sendBtn.innerHTML=`<svg width="15" height="15" viewBox="0 0 15 15" fill="none"><path d="M1 7.5L14 1l-4 13-3-5.5L1 7.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg> Send Campaign`; }
});
refreshEmailCount();
