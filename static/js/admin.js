/* ───────── ADMIN SETTINGS ───────── */
(function(){
  let _defaults = null;

  async function load(){
    try{
      const res = await fetch('/api/admin/settings');
      const d = await res.json();
      const s = d.settings, def = d.defaults;
      _defaults = def;
      // Thresholds
      const rej = document.getElementById('adm-reject'), mov = document.getElementById('adm-move');
      rej.value = s.thresholds.auto_reject; mov.value = s.thresholds.auto_move;
      document.getElementById('adm-reject-val').textContent = s.thresholds.auto_reject;
      document.getElementById('adm-move-val').textContent = s.thresholds.auto_move;
      checkThresholdWarn();
      // Emails
      document.getElementById('adm-rec-subject').value = s.email.recruitment.subject || '';
      document.getElementById('adm-rec-body').value    = s.email.recruitment.body || '';
      document.getElementById('adm-ob-subject').value  = s.email.onboarding.subject || '';
      document.getElementById('adm-ob-body').value     = s.email.onboarding.body || '';
    }catch(e){ toast('Could not load admin settings','err'); }
  }
  window.loadAdminSettings = load;

  function checkThresholdWarn(){
    const r = +document.getElementById('adm-reject').value;
    const m = +document.getElementById('adm-move').value;
    document.getElementById('adm-threshold-warn').style.display = (r >= m) ? 'block' : 'none';
  }

  // Live slider labels
  const rej = document.getElementById('adm-reject'), mov = document.getElementById('adm-move');
  if (rej) rej.addEventListener('input', ()=>{ document.getElementById('adm-reject-val').textContent = rej.value; checkThresholdWarn(); });
  if (mov) mov.addEventListener('input', ()=>{ document.getElementById('adm-move-val').textContent = mov.value; checkThresholdWarn(); });

  // Save thresholds
  document.getElementById('adm-threshold-save')?.addEventListener('click', async ()=>{
    try{
      const res = await fetch('/api/admin/thresholds',{method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({auto_reject:+rej.value, auto_move:+mov.value})});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Thresholds saved','ok');
    }catch(e){ toast('Could not save thresholds: '+e.message,'err'); }
  });
  document.getElementById('adm-threshold-reset')?.addEventListener('click', async ()=>{
    try{
      const res = await fetch('/api/admin/thresholds/reset',{method:'POST'});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      rej.value = d.thresholds.auto_reject; mov.value = d.thresholds.auto_move;
      document.getElementById('adm-reject-val').textContent = d.thresholds.auto_reject;
      document.getElementById('adm-move-val').textContent = d.thresholds.auto_move;
      checkThresholdWarn(); toast('Thresholds reset to default','ok');
    }catch(e){ toast('Could not reset: '+e.message,'err'); }
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

  // ── Per-role scoring tuning (strictness + semantic traits) ──
  (function(){
    const sel = document.getElementById('adm-role-select');
    if (!sel) return;
    const strict = document.getElementById('adm-strict');
    const strictVal = document.getElementById('adm-strict-val');
    const creative = document.getElementById('adm-creative');
    const creativeVal = document.getElementById('adm-creative-val');
    const traitsWrap = document.getElementById('adm-traits');
    const msg = document.getElementById('adm-tuning-msg');

    function traitRow(t){
      const div = document.createElement('div');
      div.className = 'adm-trait-row';
      div.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;align-items:flex-start';
      div.innerHTML = `
        <input class="field-input adm-trait-name" placeholder="Trait (e.g. high ownership)" style="flex:0 0 200px">
        <input class="field-input adm-trait-desc" placeholder="What it means — optional, helps the AI judge by gist" style="flex:1">
        <button class="td-mini-btn adm-trait-del" title="Remove" aria-label="Remove" type="button">✕</button>`;
      div.querySelector('.adm-trait-name').value = (t&&t.name)||'';
      div.querySelector('.adm-trait-desc').value = (t&&t.description)||'';
      div.querySelector('.adm-trait-del').addEventListener('click', ()=> div.remove());
      return div;
    }
    function setTraits(arr){
      traitsWrap.innerHTML = '';
      (arr||[]).forEach(t=> traitsWrap.appendChild(traitRow(t)));
      if (!traitsWrap.children.length) traitsWrap.appendChild(traitRow());
    }
    function collectTraits(){
      return Array.from(traitsWrap.querySelectorAll('.adm-trait-row')).map(r=>({
        name: r.querySelector('.adm-trait-name').value.trim(),
        description: r.querySelector('.adm-trait-desc').value.trim(),
      })).filter(t=>t.name);
    }
    if (strict) strict.addEventListener('input', ()=>{ strictVal.textContent = strict.value; });
    if (creative) creative.addEventListener('input', ()=>{ creativeVal.textContent = creative.value; });
    document.getElementById('adm-trait-add')?.addEventListener('click', ()=> traitsWrap.appendChild(traitRow()));

    async function loadTuning(roleKey){
      msg.textContent='';
      try{
        const res = await fetch('/api/admin/role-tuning/'+encodeURIComponent(roleKey));
        const d = await res.json();
        const t = (d && d.tuning) || {strictness:50, creativeness:50, traits:[]};
        strict.value = t.strictness; strictVal.textContent = t.strictness;
        const cv = (t.creativeness==null?50:t.creativeness);
        creative.value = cv; creativeVal.textContent = cv;
        setTraits(t.traits||[]);
      }catch(e){ msg.textContent='Could not load tuning.'; setTraits([]); }
    }
    async function loadRoles(){
      try{
        const res = await fetch('/api/ai/rubrics');
        const d = await res.json();
        const raw = Array.isArray(d) ? d : (d.roles || []);
        const opts = raw.map(r=>({key:r.role_key||r.key||r.id, name:r.role_name||r.name||r.role_key}))
                        .filter(o=>o.key);
        if (!opts.length) throw new Error('no roles');
        sel.innerHTML = opts.map(o=>`<option value="${o.key}">${o.name}</option>`).join('');
        loadTuning(opts[0].key);
      }catch(e){
        sel.innerHTML = '<option value="customer_support_executive">Customer Support Executive</option>'
                      + '<option value="operations_specialist">Operations Specialist</option>';
        loadTuning('customer_support_executive');
      }
    }
    sel.addEventListener('change', ()=> loadTuning(sel.value));
    document.getElementById('adm-tuning-save')?.addEventListener('click', async ()=>{
      msg.textContent='Saving…';
      try{
        const res = await fetch('/api/admin/role-tuning/'+encodeURIComponent(sel.value),{
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({strictness:+strict.value, creativeness:+creative.value, traits:collectTraits()})});
        const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
        msg.textContent='Saved. Re-score candidates (Forms → Score all) to apply.';
        if (window.toast) toast('Scoring tuning saved','ok');
      }catch(e){ msg.textContent='Save failed: '+e.message; }
    });
    loadRoles();
  })();
})();
