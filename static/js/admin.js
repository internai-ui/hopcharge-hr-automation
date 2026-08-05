/* ───────── ADMIN SETTINGS ───────── */
(function(){
  let _defaults = null;

  async function load(){
    try{
      const res = await fetch('/api/admin/settings');
      const d = await res.json();
      const s = d.settings, def = d.defaults;
      _defaults = def;
      // Thresholds (optional elements)
      const rej = document.getElementById('adm-reject'), mov = document.getElementById('adm-move');
      if (rej && s.thresholds) rej.value = s.thresholds.auto_reject;
      if (mov && s.thresholds) mov.value = s.thresholds.auto_move;
      const rejVal = document.getElementById('adm-reject-val'), movVal = document.getElementById('adm-move-val');
      if (rejVal && s.thresholds) rejVal.textContent = s.thresholds.auto_reject;
      if (movVal && s.thresholds) movVal.textContent = s.thresholds.auto_move;
      if (typeof checkThresholdWarn === 'function') checkThresholdWarn();
      // Emails
      if (s.email && s.email.recruitment) {
        document.getElementById('adm-rec-subject').value = s.email.recruitment.subject || '';
        document.getElementById('adm-rec-body').value    = s.email.recruitment.body || '';
      }
      if (s.email && s.email.onboarding) {
        document.getElementById('adm-ob-subject').value  = s.email.onboarding.subject || '';
        document.getElementById('adm-ob-body').value     = s.email.onboarding.body || '';
      }
      if (s.email && s.email.rejection) {
        document.getElementById('adm-rej-subject').value = s.email.rejection.subject || '';
        document.getElementById('adm-rej-body').value    = s.email.rejection.body || '';
      }
      // Refresh any preview panes that are already open
      ['recruitment','onboarding','rejection'].forEach(k => { if (typeof refreshPreview === 'function') refreshPreview(k); });
    }catch(e){ console.error('Load admin settings failed:', e); toast('Could not load admin settings','err'); }
  }
  window.loadAdminSettings = load;

  // Admin settings loaded cleanly

  // Email save/reset helper
  async function saveEmail(kind, subjId, bodyId){
    const subjEl = document.getElementById(subjId), bodyEl = document.getElementById(bodyId);
    if (!validateRequired([
      { input: subjEl, message: 'Enter a subject line.' },
      { input: bodyEl, message: 'Enter the email body.' },
    ])) return;
    const subject = subjEl.value.trim();
    const body = bodyEl.value.trim();
    try{
      const res = await fetch('/api/admin/email/'+kind,{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({subject, body})});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Email saved','ok');
    }catch(e){ toast('Could not save email: '+e.message,'err'); }
  }
  async function resetEmail(kind, subjId, bodyId){
    try{
      const res = await fetch('/api/admin/email/'+kind+'/reset',{method:'POST'});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      document.getElementById(subjId).value = d.email.subject || '';
      document.getElementById(bodyId).value = d.email.body || '';
      toast('Reset to default','ok');
    }catch(e){ toast('Could not reset: '+e.message,'err'); }
  }

  document.getElementById('adm-rec-save')?.addEventListener('click', ()=> saveEmail('recruitment','adm-rec-subject','adm-rec-body'));
  document.getElementById('adm-rec-reset')?.addEventListener('click', ()=> resetEmail('recruitment','adm-rec-subject','adm-rec-body'));
  document.getElementById('adm-ob-save')?.addEventListener('click', ()=> saveEmail('onboarding','adm-ob-subject','adm-ob-body'));
  document.getElementById('adm-ob-reset')?.addEventListener('click', ()=> resetEmail('onboarding','adm-ob-subject','adm-ob-body'));
  document.getElementById('adm-rej-save')?.addEventListener('click', ()=> saveEmail('rejection','adm-rej-subject','adm-rej-body'));
  document.getElementById('adm-rej-reset')?.addEventListener('click', ()=> resetEmail('rejection','adm-rej-subject','adm-rej-body'));

  // ── Live preview (server-rendered, same templates the real send uses) ──
  const PREVIEW_FIELDS = {
    recruitment: { subject: 'adm-rec-subject', body: 'adm-rec-body' },
    onboarding:  { subject: 'adm-ob-subject',  body: 'adm-ob-body' },
    rejection:   { subject: 'adm-rej-subject', body: 'adm-rej-body' },
  };
  const _previewDebounce = {};

  async function refreshPreview(kind){
    const f = PREVIEW_FIELDS[kind];
    const wrap = document.getElementById('adm-'+kind+'-preview-wrap');
    if (!wrap || wrap.classList.contains('hidden')) return;
    const subjEl = document.getElementById(f.subject), bodyEl = document.getElementById(f.body);
    if (!subjEl || !bodyEl) return;
    try{
      const res = await fetch('/api/admin/email/'+kind+'/preview', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({subject: subjEl.value, body: bodyEl.value})});
      const d = await res.json();
      if (!res.ok) throw new Error(errDetail(d, res.status));
      document.getElementById('adm-'+kind+'-preview-subject').textContent = 'Subject: ' + (d.subject || '(no subject)');
      document.getElementById('adm-'+kind+'-preview-frame').srcdoc = d.html;
    }catch(e){
      document.getElementById('adm-'+kind+'-preview-subject').textContent = 'Preview failed: ' + e.message;
    }
  }
  function debouncedPreview(kind){
    clearTimeout(_previewDebounce[kind]);
    _previewDebounce[kind] = setTimeout(()=> refreshPreview(kind), 400);
  }

  document.querySelectorAll('.adm-preview-toggle').forEach(btn=>{
    const kind = btn.dataset.kind;
    btn.addEventListener('click', ()=>{
      const wrap = document.getElementById('adm-'+kind+'-preview-wrap');
      if (!wrap) return;
      wrap.classList.toggle('hidden');
      if (!wrap.classList.contains('hidden')) refreshPreview(kind);
    });
  });

  Object.keys(PREVIEW_FIELDS).forEach(kind=>{
    const f = PREVIEW_FIELDS[kind];
    [f.subject, f.body].forEach(id=>{
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', ()=> debouncedPreview(kind));
    });
  });

  // ── Click-to-insert placeholder tokens ({name}, {role}, ...) ──
  document.querySelectorAll('.adm-token-btn').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const token = btn.dataset.token;
      const start = target.selectionStart ?? target.value.length;
      const end = target.selectionEnd ?? target.value.length;
      target.value = target.value.slice(0, start) + token + target.value.slice(end);
      const newPos = start + token.length;
      target.focus();
      target.setSelectionRange(newPos, newPos);
      target.dispatchEvent(new Event('input', {bubbles:true}));
    });
  });

})();
