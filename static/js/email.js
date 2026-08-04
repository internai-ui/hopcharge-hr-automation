/* ───────── EMAIL CAMPAIGN ───────── */
const addrEl=document.getElementById('gmail-addr'), passEl=document.getElementById('gmail-pass'), linkEl=document.getElementById('form-link');

/* ── Connect Gmail (OAuth) — App Password stays available as a fallback ── */
const gmailStatusPill=document.getElementById('gmail-status-pill'), gmailStatusDetail=document.getElementById('gmail-status-detail');
const gmailConnectBtn=document.getElementById('gmail-connect-btn'), gmailDisconnectBtn=document.getElementById('gmail-disconnect-btn');
const gmailSettingsToggle=document.getElementById('gmail-oauth-settings-toggle'), gmailSettingsBox=document.getElementById('gmail-oauth-settings');
const gmailClientIdEl=document.getElementById('gmail-oauth-client-id'), gmailClientSecretEl=document.getElementById('gmail-oauth-client-secret');
const gmailOAuthSaveBtn=document.getElementById('gmail-oauth-save-btn'), gmailOAuthModeHint=document.getElementById('gmail-oauth-mode-hint');
const apppassEnable=document.getElementById('apppass-enable'), apppassFields=document.getElementById('apppass-fields');
let _gmailConnected=false, _gmailClientConfigured=false;

function _syncApppassUI(){
  const on = apppassEnable && apppassEnable.checked;
  apppassFields.classList.toggle('hidden', !on);
  const knob=apppassEnable.closest('.switch').querySelector('.slider');
  if(knob){ knob.style.setProperty('--knob', on?'translateX(17px)':'translateX(0)'); knob.style.background=on?'#60a5fa':'rgba(255,255,255,0.15)'; }
  validateEmailForm();
}
if(apppassEnable) apppassEnable.addEventListener('change', _syncApppassUI);

if(gmailSettingsToggle) gmailSettingsToggle.addEventListener('click', ()=> gmailSettingsBox.classList.toggle('hidden'));

window.refreshGmailStatus = async function(){
  try{
    const res = await fetch('/api/gmail-oauth/status');
    const d = await res.json();
    _gmailConnected = !!d.connected;
    _gmailClientConfigured = !!d.client_configured;

    // A client-secret JSON dropped into credentials/ always wins over a
    // manually pasted one (see gmail_oauth.py's _active_client()) — when
    // that's the case, the paste fields are moot, so hide them and just
    // say where the client came from.
    const fromFile = d.client_source === 'file';
    if(gmailSettingsToggle) gmailSettingsToggle.classList.toggle('hidden', fromFile);
    if(gmailSettingsBox && fromFile) gmailSettingsBox.classList.add('hidden');
    if(gmailClientIdEl && !gmailClientIdEl.value && d.client_id && !fromFile) gmailClientIdEl.value = d.client_id;
    if(gmailOAuthModeHint){
      gmailOAuthModeHint.textContent = fromFile
        ? `Loaded automatically from credentials/${d.client_source_file} (deployment mode: ${d.deployment_mode}).`
        : `Deployment mode: ${d.deployment_mode || 'web'}. Drop the client-secret JSON Google gives you into credentials/, or paste it below.`;
    }

    if(d.connected){
      gmailStatusPill.textContent = 'Connected';
      gmailStatusPill.style.background = 'rgba(52,211,153,0.15)'; gmailStatusPill.style.color = '#34d399';
      gmailStatusDetail.textContent = d.connected_email ? `Sending as ${d.connected_email}` : 'Connected';
      gmailConnectBtn.classList.add('hidden');
      gmailDisconnectBtn.classList.remove('hidden');
      // Collapse the App Password fallback once OAuth is live — the user can still
      // switch it back on manually via the toggle.
      if(apppassEnable && !apppassEnable._userTouched) apppassEnable.checked = false;
    } else {
      gmailStatusPill.textContent = d.client_configured ? 'Not connected' : 'Not set up';
      gmailStatusPill.style.background = 'rgba(248,113,113,0.15)'; gmailStatusPill.style.color = '#f87171';
      gmailStatusDetail.textContent = d.client_configured
        ? 'Click Connect Gmail to sign in.'
        : 'Drop the client-secret JSON from Google Cloud Console into credentials/, or paste your Client ID/Secret below.';
      gmailConnectBtn.classList.remove('hidden');
      gmailDisconnectBtn.classList.add('hidden');
      // No OAuth connection yet — default to the App Password fallback so the
      // page is usable immediately, unless the user already chose otherwise.
      if(apppassEnable && !apppassEnable._userTouched) apppassEnable.checked = true;
    }
    _syncApppassUI();
  }catch(e){
    gmailStatusPill.textContent = 'Unavailable';
    gmailStatusDetail.textContent = 'Could not reach the server to check Gmail connection status.';
  }
};

if(gmailConnectBtn) gmailConnectBtn.addEventListener('click', ()=>{ window.location.href = '/api/gmail-oauth/authorize'; });
if(gmailDisconnectBtn) gmailDisconnectBtn.addEventListener('click', async ()=>{
  try{
    const res = await fetch('/api/gmail-oauth/disconnect', {method:'POST'});
    const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
    toast('Gmail disconnected', 'ok');
    refreshGmailStatus();
  }catch(e){ toast('Could not disconnect: '+e.message, 'err'); }
});
if(gmailOAuthSaveBtn) gmailOAuthSaveBtn.addEventListener('click', async ()=>{
  if(!validateRequired([
    { input: gmailClientIdEl, message: 'Enter the OAuth Client ID.' },
    { input: gmailClientSecretEl, message: 'Enter the OAuth Client Secret.' },
  ])) return;
  try{
    const res = await fetch('/api/gmail-oauth/client-config', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ client_id: gmailClientIdEl.value.trim(), client_secret: gmailClientSecretEl.value.trim() })});
    const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
    gmailClientSecretEl.value = '';
    toast('Gmail OAuth client saved — click Connect Gmail to sign in.', 'ok');
    refreshGmailStatus();
  }catch(e){ toast('Could not save client config: '+e.message, 'err'); }
});
if(apppassEnable) apppassEnable.addEventListener('change', ()=>{ apppassEnable._userTouched = true; });

// Reflect the OAuth callback's redirect (?gmail=connected|error&reason=...) as a toast,
// then strip the query string so a page refresh doesn't re-show it.
(function(){
  const params = new URLSearchParams(window.location.search);
  if(params.has('gmail')){
    const status = params.get('gmail');
    if(status === 'connected') toast('Gmail connected successfully', 'ok');
    else if(status === 'error') toast('Gmail connection failed: '+(params.get('reason')||'unknown error'), 'err');
    params.delete('gmail'); params.delete('reason');
    const qs = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (qs?`?${qs}`:''));
  }
})();
refreshGmailStatus();
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
function usingOAuth(){ return _gmailConnected && !(apppassEnable && apppassEnable.checked); }
function validEmails(){ return candidates.filter(c=>c.email && c.email.includes('@')).length; }
function refreshEmailCount(){ const n=validEmails(); const credsMissing = !usingOAuth() && (!addrEl.value||!passEl.value); if(credsMissing||!linkEl.value) sendInfo.textContent=n>0?`${n} candidate${n>1?'s':''} ready — fill in all fields`:'Parse CVs first, then fill in all fields'; validateEmailForm(); }
function validateEmailForm(){
  const recipients = manualActive()? parseManualEmails().length : validEmails();
  const credsOk = usingOAuth() ? true : (addrEl.value.includes('@') && passEl.value.length>=8);
  const ok = credsOk && linkEl.value.startsWith('http') && recipients>0;
  sendBtn.disabled=!ok;
  if(ok) sendInfo.textContent = usingOAuth() ? `${recipients} email${recipients>1?'s':''} will be sent via connected Gmail` : `${recipients} email${recipients>1?'s':''} will be sent`;
}
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
  const usingO = usingOAuth();
  const gmail_address=addrEl.value.trim(), app_password=passEl.value.trim(), form_link=linkEl.value.trim();
  const requiredFields = [
    { input: linkEl, message: 'Enter the Google Forms assessment link.' },
  ];
  if (!usingO) {
    requiredFields.push({ input: addrEl, message: 'Enter the sender Gmail address.' });
    requiredFields.push({ input: passEl, message: 'Enter the Gmail App Password.' });
  }
  if (manualActive()) {
    requiredFields.push({ input: manualEmailsEl, message: 'Add at least one valid email, or turn off manual mode.',
                          test: () => parseManualEmails().length > 0 });
  }
  if (trackEnable && trackEnable.checked) {
    requiredFields.push({ input: trackBaseEl, message: 'Enter the public URL where this server is reachable.' });
  }
  if (!validateRequired(requiredFields)) return;

  const payload={form_link, send_via: usingO ? 'oauth' : 'app_password'};
  if(!usingO){ payload.gmail_address=gmail_address; payload.app_password=app_password; }
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
