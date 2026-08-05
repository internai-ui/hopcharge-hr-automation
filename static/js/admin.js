/* ───────── ADMIN SETTINGS ───────── */
(function(){
  let _defaults = null;

  async function load(){
    try{
      const res = await fetch('/api/admin/settings');
      const d = await res.json();
      const s = d.settings, def = d.defaults;
      _defaults = def;
      // Emails
      document.getElementById('adm-rec-subject').value = s.email.recruitment.subject || '';
      document.getElementById('adm-rec-body').value    = s.email.recruitment.body || '';
      document.getElementById('adm-ob-subject').value  = s.email.onboarding.subject || '';
      document.getElementById('adm-ob-body').value     = s.email.onboarding.body || '';
      document.getElementById('adm-rej-subject').value = s.email.rejection.subject || '';
      document.getElementById('adm-rej-body').value    = s.email.rejection.body || '';
      // Refresh any preview panes that are already open (e.g. re-navigating
      // back to this page) so they don't show stale content.
      ['recruitment','onboarding','rejection'].forEach(refreshPreview);
      // Recruitment form
      const rf = s.recruitment_form || {};
      document.getElementById('adm-form-id').value   = rf.form_id || '';
      document.getElementById('adm-form-link').value = rf.form_link || '';
    }catch(e){ toast('Could not load admin settings','err'); }
  }
  window.loadAdminSettings = load;

  // Save recruitment form
  document.getElementById('adm-form-save')?.addEventListener('click', async ()=>{
    const idEl = document.getElementById('adm-form-id'), linkEl = document.getElementById('adm-form-link');
    const msg = document.getElementById('adm-form-msg');
    if (linkEl.value.trim() && !linkEl.value.trim().startsWith('http')) {
      msg.textContent = 'The form link must start with http(s)://'; msg.style.color = 'var(--red)';
      return;
    }
    try{
      const res = await fetch('/api/admin/recruitment-form', {method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({form_id: idEl.value.trim(), form_link: linkEl.value.trim()})});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      msg.textContent = 'Saved.'; msg.style.color = 'var(--text-dim)';
      toast('Recruitment form saved','ok');
      window.dispatchEvent(new CustomEvent('recruitment-form-changed', {detail: d.recruitment_form}));
    }catch(e){ msg.textContent = 'Save failed: '+e.message; msg.style.color = 'var(--red)'; }
  });

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
