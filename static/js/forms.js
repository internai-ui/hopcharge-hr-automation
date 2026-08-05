/* ───────── FORMS RETRIEVAL ───────── */
(function () {
  const credsEl    = document.getElementById('forms-creds');
  const fetchBtn   = document.getElementById('forms-fetch-btn');
  const loadBtn    = document.getElementById('forms-load-btn');
  const dlBtn      = document.getElementById('forms-dl-btn');
  const infoEl     = document.getElementById('forms-info');
  const lastEl     = document.getElementById('forms-last');
  const formIdStatus = document.getElementById('forms-id-status');
  let _formId = '';   // sourced from Admin Settings' saved recruitment form id

  async function loadFormId(){
    try{
      const res = await fetch('/api/admin/settings');
      const d = await res.json();
      _formId = ((d.settings && d.settings.recruitment_form && d.settings.recruitment_form.form_id) || '').trim();
    }catch(e){ _formId = ''; }
    if (formIdStatus) {
      formIdStatus.innerHTML = _formId
        ? `<span style="font-family:'JetBrains Mono',monospace;font-size:11.5px">${_formId}</span>`
        : `No recruitment form set yet. <a href="#" data-goto="admin" style="color:var(--violet)">Set it in Admin Settings</a>.`;
      formIdStatus.querySelector('[data-goto]')?.addEventListener('click', (e) => {
        e.preventDefault();
        if (window.goToPage) window.goToPage('admin');
      });
    }
    validate();
  }
  loadFormId();
  window.addEventListener('recruitment-form-changed', loadFormId);

  /* ── Google account access (same OAuth connection as Send Emails) ── */
  const FORMS_SCOPE = 'https://www.googleapis.com/auth/forms.responses.readonly';
  const svcAcctEnable = document.getElementById('forms-svcacct-enable'), svcAcctFields = document.getElementById('forms-svcacct-fields');

  function usingOAuthForms(){
    const auth = window.getGoogleAuthStatus ? window.getGoogleAuthStatus() : { connected: false };
    const usable = !!auth.connected && (auth.scopes||[]).includes(FORMS_SCOPE);
    return usable && !(svcAcctEnable && svcAcctEnable.checked);
  }

  function _syncSvcAcctUI(){
    const on = svcAcctEnable && svcAcctEnable.checked;
    if (svcAcctFields) svcAcctFields.classList.toggle('hidden', !on);
    validate();
  }
  if (svcAcctEnable) svcAcctEnable.addEventListener('change', ()=>{ svcAcctEnable._userTouched = true; _syncSvcAcctUI(); });

  window.addEventListener('google-auth-changed', (e) => {
    const d = e.detail || {};
    const usable = !!d.connected && (d.scopes||[]).includes(FORMS_SCOPE);
    if (svcAcctEnable && !svcAcctEnable._userTouched) {
      svcAcctEnable.checked = !usable;
      _syncSvcAcctUI();
    }
    validate();
  });

  function validate() {
    const idOk = !!_formId;
    const credsOk = usingOAuthForms() ? true : (credsEl && credsEl.value.trim().startsWith('{'));
    fetchBtn.disabled = !(idOk && credsOk);
  }
  if (credsEl) credsEl.addEventListener('input', validate);
  credsEl.addEventListener('blur', () => {
    if (usingOAuthForms()) { clearFieldError(credsEl); return; }
    const v = credsEl.value.trim();
    if (!v) showFieldError(credsEl, 'Paste the service account JSON key.');
    else if (!v.startsWith('{')) showFieldError(credsEl, 'This doesn\'t look like JSON - it should start with {.');
    else clearFieldError(credsEl);
  });

  // ── Smart field extractor ─────────────────────────────────
  const AVATAR_COLORS = ['#1F2D59','#1F2D59','#3B82F6','#10B981','#F59E0B','#EF4444','#EC4899'];
  function avatarColor(i){ return AVATAR_COLORS[i % AVATAR_COLORS.length]; }
  function initials(name){ return (name||'?').split(' ').map(w=>w[0]||'').join('').slice(0,2).toUpperCase(); }

  function pick(answers, patterns){
    if (!answers) return '-';
    for (const a of answers){
      const q = (a.question||'').toLowerCase();
      if (patterns.some(p => q.includes(p.toLowerCase()))){
        return (a.answer||'').trim() || '-';
      }
    }
    return '-';
  }

  function extractFields(r){
    const ans = r.answers || [];
    return {
      name:       pick(ans, ['name','full name']),
      email:      pick(ans, ['personal email','email id','email']),
      phone:      pick(ans, ['phone','mobile','contact']),
      location:   pick(ans, ['location','city']),
      dob:        pick(ans, ['date of birth','dob','birth date','birth']),
      gender:     pick(ans, ['gender']),
      nationality:pick(ans, ['nationality']),
      marital:    pick(ans, ['marital']),
      languages:  pick(ans, ['language']),
      role:       pick(ans, ['role applying','role']),
      hs_school:  pick(ans, ['high school','10th','ssc']),
      hs_pct:     pick(ans, ['high school','10th','ssc','hsc percent','10th percent']),
      inter_sch:  pick(ans, ['intermediate','12th','inter','10+2']),
      inter_pct:  pick(ans, ['intermediate percent','12th percent','inter percent']),
      grad:       pick(ans, ['graduation','degree','university','college']),
      cgpa:       pick(ans, ['cgpa','gpa','final cgpa']),
      experience: pick(ans, ['experience','previous exp','work exp']),
      skills:     pick(ans, ['professional skills','skills','skill set','key skills']),
    };
  }

  // ── Detail modal ─────────────────────────────────────────
  let _modalResponses = [];
  window.openCandModal = function(idx){
    const r = _modalResponses[idx]; if(!r) return;
    const f = extractFields(r);
    const col = avatarColor(idx);
    document.getElementById('cm-avatar').style.background = `linear-gradient(135deg,${col},${col}99)`;
    document.getElementById('cm-avatar').textContent = initials(f.name);
    document.getElementById('cm-name').textContent = f.name === '-' ? 'Anonymous' : f.name;
    const dateStr = r.submitted_at ? new Date(r.submitted_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '-';
    document.getElementById('cm-meta').textContent = `Submitted ${dateStr}  ·  ${f.role !== '-' ? f.role : 'Role not specified'}`;

    // Render all Q&A pairs
    const body = document.getElementById('cm-body');
    body.innerHTML = '';

    // Fast-fill flag banner (form completed in under 5 minutes)
    const track = f.email !== '-' ? _trackingByEmail[f.email.toLowerCase()] : null;
    if (isFastFill(track)){
      const warn = document.createElement('div');
      warn.style.cssText = 'grid-column:1/-1;margin-bottom:18px;padding:12px 15px;background:rgba(248,113,113,.10);border:1px solid rgba(248,113,113,.4);border-radius:9px';
      warn.innerHTML = `<div style="font-family:'Poppins',sans-serif;font-weight:700;font-size:13px;letter-spacing:.5px;color:#f87171">🚩 FLAGGED - FAST COMPLETION</div>
        <div style="font-size:12px;color:var(--text-mid);margin-top:5px;line-height:1.5">This candidate completed the form in <b style="color:#f87171">${track.time_taken_human}</b> (under 5 minutes). Consider reviewing their answers for low effort or copy-paste responses before advancing them.</div>`;
      body.appendChild(warn);
    }
    const groups = [
      { q:'Personal Email',   a:f.email },
      { q:'Phone Number',     a:f.phone },
      { q:'Location / City',  a:f.location },
      { q:'Date of Birth',    a:f.dob },
      { q:'Gender',           a:f.gender },
      { q:'Nationality',      a:f.nationality },
      { q:'Marital Status',   a:f.marital },
      { q:'Languages Known',  a:f.languages },
      { q:'Role Applying For',a:f.role, full:true },
      { q:'High School (10th) - School & Year', a:f.hs_school, full:true },
      { q:'High School %',    a:f.hs_pct },
      { q:'Intermediate (12th) - School & Year', a:f.inter_sch, full:true },
      { q:'Intermediate %',   a:f.inter_pct },
      { q:'Graduation - University & Year', a:f.grad, full:true },
      { q:'CGPA',             a:f.cgpa },
      { q:'Previous Experience', a:f.experience, full:true },
      { q:'Professional Skills', a:f.skills, full:true },
    ];
    groups.forEach(g => {
      const div = document.createElement('div');
      div.className = 'cand-qa-item' + (g.full ? ' full' : '');
      const isEmpty = !g.a || g.a === '-';
      div.innerHTML = `<div class="cand-qa-q">${g.q}</div><div class="cand-qa-a${isEmpty?' empty':''}">${isEmpty ? 'Not provided' : g.a}</div>`;
      body.appendChild(div);
    });

    // ── Full form responses - every raw question/answer exactly as submitted,
    //    mirroring the view available in the Accepted candidates modal. The
    //    curated groups above only surface known fields; this shows everything.
    const _escFR = s => String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const allAnswers = (r.answers || []);
    if (allAnswers.length){
      const hdr = document.createElement('div');
      hdr.className = 'cand-qa-item full';
      hdr.style.cssText = 'background:none;border:none;padding:14px 0 2px';
      hdr.innerHTML = `<div class="cand-qa-q" style="font-size:11px;letter-spacing:1.5px;color:var(--text-mid)">Full Form Responses <span style="opacity:.55;font-weight:400">- every answer as submitted</span></div>`;
      body.appendChild(hdr);
      allAnswers.forEach(a => {
        const div = document.createElement('div');
        div.className = 'cand-qa-item full';
        const empty = !a.answer;
        div.innerHTML = `<div class="cand-qa-q">${_escFR(a.question)}</div><div class="cand-qa-a${empty?' empty':''}">${empty ? 'no answer' : _escFR(a.answer)}</div>`;
        body.appendChild(div);
      });
    }

    // ── Candidate Notes (append-only timeline) ──
    renderNotes(body, r.response_id);

    document.getElementById('cand-modal').classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  // ── Candidate notes: append-only timeline shown in the modal ──
  function escNote(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function renderNotes(body, responseId){
    const wrap = document.createElement('div');
    wrap.className = 'cand-qa-item full';
    wrap.style.cssText = 'margin-top:8px;border-top:1px solid var(--border-dim);padding-top:18px';
    wrap.innerHTML = `
      <div class="cand-qa-q">Notes <span style="opacity:.55;font-weight:400">- personal remarks, any round</span></div>
      <div id="notes-list-${escNote(responseId)}" style="margin:10px 0 14px">
        <div style="font-size:12px;color:var(--text-dim)">Loading notes…</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <input id="note-stage-${escNote(responseId)}" class="field-input" type="text"
               placeholder="Stage / round (optional) - e.g. Round 1 Telephonic"
               style="font-size:12px">
        <textarea id="note-text-${escNote(responseId)}" rows="2" class="field-input"
               placeholder="Add a remark about this candidate…"
               style="font-size:12.5px;resize:vertical"></textarea>
        <div style="display:flex;justify-content:flex-end">
          <button class="btn-ghost" id="note-add-${escNote(responseId)}">+ Add note</button>
        </div>
      </div>`;
    body.appendChild(wrap);

    const addBtn = document.getElementById('note-add-'+responseId);
    if (addBtn) addBtn.addEventListener('click', ()=> addCandidateNote(responseId));
    loadCandidateNotes(responseId);
  }

  async function loadCandidateNotes(responseId){
    const list = document.getElementById('notes-list-'+responseId);
    if (!list) return;
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}`);
      const d = await res.json();
      const notes = d.notes || [];
      if (!notes.length){
        list.innerHTML = '<div style="font-size:12px;color:var(--text-dim)">No notes yet. Add the first remark below.</div>';
        return;
      }
      list.innerHTML = notes.map(n=>{
        const when = n.created_at ? new Date(n.created_at).toLocaleString('en-IN',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        const stage = n.stage ? `<span style="font-size:9.5px;font-family:'Poppins',sans-serif;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--violet);background:rgba(31,45,89,.12);border:1px solid rgba(31,45,89,.3);border-radius:4px;padding:1px 7px;margin-right:7px">${escNote(n.stage)}</span>` : '';
        return `<div style="background:var(--glass-2);border:1px solid var(--border-dim);border-radius:8px;padding:10px 12px;margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
            <div style="font-size:12.5px;line-height:1.5;color:var(--text-mid);white-space:pre-wrap;flex:1">${stage}${escNote(n.text)}</div>
            <button class="note-del-btn" data-rid="${escNote(responseId)}" data-nid="${escNote(n.id)}" title="Delete note" aria-label="Delete note"
                    style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:13px;flex-shrink:0;padding:0 2px">🗑</button>
          </div>
          <div style="font-size:10px;color:var(--text-dim);margin-top:6px;font-family:'JetBrains Mono',monospace">${escNote(when)}</div>
        </div>`;
      }).join('');
      list.querySelectorAll('.note-del-btn').forEach(btn=>{
        btn.addEventListener('click', ()=> deleteCandidateNote(btn.dataset.rid, btn.dataset.nid));
      });
    }catch(e){
      list.innerHTML = '<div style="font-size:12px;color:#f87171">Could not load notes.</div>';
    }
  }

  async function addCandidateNote(responseId){
    const textEl = document.getElementById('note-text-'+responseId);
    const stageEl = document.getElementById('note-stage-'+responseId);
    if (textEl && !validateRequired([{ input: textEl, message: 'Write a note first.' }])) return;
    const text = (textEl?.value||'').trim();
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}`,{
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text, stage:(stageEl?.value||'').trim()})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      if(textEl) textEl.value=''; if(stageEl) stageEl.value='';
      toast('Note added','ok');
      loadCandidateNotes(responseId);
      if (window.refreshNoteCounts) window.refreshNoteCounts();
    }catch(e){ toast('Could not add note: '+e.message,'err'); }
  }

  async function deleteCandidateNote(responseId, noteId){
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}/${encodeURIComponent(noteId)}`,{method:'DELETE'});
      const d = await res.json().catch(()=>({}));
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Note deleted','ok');
      loadCandidateNotes(responseId);
      if (window.refreshNoteCounts) window.refreshNoteCounts();
    }catch(e){ toast('Could not delete note: '+e.message,'err'); }
  }

  window.closeCandModal = function(){
    document.getElementById('cand-modal').classList.remove('open');
    document.body.style.overflow = '';
  };

  // ── Delete with two-click confirm ────────────────────────
  let _deleteTimer = null;
  window.confirmDelete = async function(btn, responseId, idx) {
    // First click: enter confirming state
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      btn.textContent = '✕ Sure?';
      // Auto-cancel after 3s if user doesn't confirm
      _deleteTimer = setTimeout(() => {
        btn.classList.remove('confirming');
        btn.textContent = '🗑';
      }, 3000);
      return;
    }

    // Second click: actually delete
    clearTimeout(_deleteTimer);
    btn.disabled = true;
    btn.textContent = '…';

    try {
      const res = await fetch(`/api/forms/responses/${responseId}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) throw new Error(errDetail(data, res.status));

      // Animate row out, then rebuild from the canonical dataset
      const tr = btn.closest('tr');
      tr.style.transition = 'opacity 0.3s, transform 0.3s';
      tr.style.opacity = '0';
      tr.style.transform = 'translateX(40px)';
      setTimeout(() => {
        // Remove from the full dataset by response_id (robust to sort/filter)
        if (_fullData && _fullData.responses){
          _fullData.responses = _fullData.responses.filter(x => x.response_id !== responseId);
          _fullData.responses.forEach((x,i)=>{ x._idx = i; });  // re-stamp indices
          _modalResponses = _fullData.responses;
        }
        // Update total count
        const totalEl = document.getElementById('fs-total');
        totalEl.textContent = Math.max(0, parseInt(totalEl.textContent) - 1);
        // Refresh role list (a role may now be empty) and re-render
        populateRoleFilter(_fullData.responses);
        applyFiltersAndRender();
        toast(`Candidate removed`, 'inf');
      }, 300);

    } catch (err) {
      btn.disabled = false;
      btn.classList.remove('confirming');
      btn.textContent = '🗑';
      toast(`Delete failed: ${err.message}`, 'err');
    }
  };

  // ── Accept / Reject decisions ───────────────────────────
  let _rejectTargetId = null;
  let _acceptTargetId = null;
  // When a reject is triggered from inside the Accepted pipeline (any stage),
  // we must also pull the candidate OUT of the accepted store so they don't
  // appear in both tabs. This flag tells the shared confirm handler to do that.
  let _rejectFromPipeline = false;

  window.openAcceptDialog = function(responseId){
    _acceptTargetId = responseId;
    const rec = (_fullData?.responses||[]).find(x=>x.response_id===responseId);
    const nm = rec ? (extractFields(rec).name||'this candidate') : 'this candidate';
    document.getElementById('accd-who').textContent =
      `Move ${nm==='-'?'this candidate':nm} to the Accepted section. They enter at the HR Round stage.`;
    document.getElementById('accd-note').value = '';
    document.getElementById('accept-dialog').classList.add('open');
  };

  window.closeAcceptDialog = function(){
    document.getElementById('accept-dialog').classList.remove('open');
    _acceptTargetId = null;
  };

  document.getElementById('accd-confirm-btn').addEventListener('click', async ()=>{
    if (!_acceptTargetId) return;
    const note = document.getElementById('accd-note').value.trim();
    const btn = document.getElementById('accd-confirm-btn');
    btn.disabled = true; btn.textContent = 'Accepting…';
    try{
      const res = await fetch('/api/accepted', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:_acceptTargetId, note})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      _acceptedIds.add(_acceptTargetId);
      toast(`Accepted into HR Round`, 'ok');
      closeAcceptDialog();
      applyFiltersAndRender();
      if (window.loadAcceptedPage) loadAcceptedPage();
    }catch(e){ toast('Accept failed: '+e.message, 'err'); }
    finally{ btn.disabled=false; btn.textContent='Accept Candidate'; }
  });

  window.openRejectDialog = function(responseId, opts){
    opts = opts || {};
    _rejectTargetId = responseId;
    _rejectFromPipeline = !!opts.fromPipeline;
    // Identify the candidate for the dialog subtitle. Prefer the name passed in
    // by the caller (the Accepted pipeline knows it), else look it up in the
    // form-responses dataset.
    const rec = (_fullData?.responses||[]).find(x=>x.response_id===responseId);
    const nm = opts.name || (rec ? (extractFields(rec).name||'this candidate') : 'this candidate');
    document.getElementById('rejd-who').textContent =
      `Move ${nm==='-'?'this candidate':nm} to the Rejected tab. Their full application is preserved.`;
    document.getElementById('rejd-reason').value = '';
    // Populate rounds (fetch once, then cache); preselect a preferred round when
    // the caller knows which stage the candidate is being rejected from.
    const sel = document.getElementById('rejd-round');
    const preselect = () => {
      if (!opts.preferredRound) return;
      const opt = Array.from(sel.options).find(o => o.value === opts.preferredRound);
      if (opt) sel.value = opts.preferredRound;
    };
    if (!sel.options.length){
      fetch('/api/rejected').then(r=>r.json()).then(d=>{
        sel.innerHTML = (d.rounds||[]).map(rd=>`<option value="${rd}">${rd}</option>`).join('');
        preselect();
      }).catch(()=>{ sel.innerHTML = '<option value="Round 0 - Form Screening">Round 0 - Form Screening</option>'; });
    } else {
      preselect();
    }
    document.getElementById('reject-dialog').classList.add('open');
  };

  window.closeRejectDialog = function(){
    document.getElementById('reject-dialog').classList.remove('open');
    _rejectTargetId = null;
    _rejectFromPipeline = false;
  };

  document.getElementById('rejd-confirm-btn').addEventListener('click', async ()=>{
    if (!_rejectTargetId) return;
    const round_name = document.getElementById('rejd-round').value;
    const reason = document.getElementById('rejd-reason').value.trim();
    const btn = document.getElementById('rejd-confirm-btn');
    btn.disabled = true; btn.textContent = 'Rejecting…';
    try{
      const rejectedId = _rejectTargetId;
      const fromPipeline = _rejectFromPipeline;
      const res = await fetch('/api/rejected', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:rejectedId, round_name, reason})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      _rejectedIds.add(rejectedId);

      // If the candidate was rejected from inside the Accepted pipeline, also
      // remove them from the accepted store so they aren't in both tabs.
      if (fromPipeline){
        try{
          const del = await fetch(`/api/accepted/${encodeURIComponent(rejectedId)}`, {method:'DELETE'});
          if (!del.ok){ const dd = await del.json().catch(()=>({})); throw new Error(errDetail(dd, del.status)); }
        }catch(err){ toast('Rejected, but could not remove from pipeline: '+err.message, 'err'); }
        _acceptedIds.delete(rejectedId);
      }

      toast(`Rejected in ${round_name}`, 'inf');
      closeRejectDialog();
      applyFiltersAndRender();          // grey out the row
      if (fromPipeline && window.loadAcceptedPage) loadAcceptedPage();  // drop from pipeline view
      if (window.loadRejectedPage) loadRejectedPage();  // refresh tab if loaded
    }catch(e){ toast('Reject failed: '+e.message, 'err'); }
    finally{ btn.disabled=false; btn.textContent='Reject Candidate'; }
  });

  // Close dialogs on backdrop click / Escape
  document.getElementById('reject-dialog').addEventListener('click', e=>{
    if (e.target.id === 'reject-dialog') closeRejectDialog();
  });
  document.getElementById('accept-dialog').addEventListener('click', e=>{
    if (e.target.id === 'accept-dialog') closeAcceptDialog();
  });
  document.addEventListener('keydown', e=>{
    if (e.key==='Escape'){ closeRejectDialog(); closeAcceptDialog(); }
  });

  // ── renderResponses ──────────────────────────────────────
  let _fullData = null;          // the complete fetched dataset
  function renderResponses(data) {
    if (!data || !data.responses) return;
    document.getElementById('forms-stats').classList.remove('hidden');
    document.getElementById('forms-results').classList.remove('hidden');
    dlBtn.style.display = 'inline-flex';

    const n = data.responses.length;
    const countUp = (id, v) => {
      const el = document.getElementById(id); const t0 = performance.now();
      (function step(now){ const p=Math.min((now-t0)/700,1); el.textContent=Math.round(v*(1-Math.pow(1-p,3))); if(p<1)requestAnimationFrame(step); })(performance.now());
    };
    countUp('fs-total', n);
    countUp('fs-questions', (data.questions||[]).length);
    lastEl.value = data.last_synced || '';

    const lbl = document.getElementById('forms-title-label');
    lbl.textContent = (data.form_title || 'Responses') + ' - ' + n + ' response' + (n!==1?'s':'');

    // Save full dataset for modal + filtering. Stamp each response with its
    // true index so sorting/filtering never breaks the modal/delete mapping.
    data.responses.forEach((r, i) => { r._idx = i; });
    _fullData = data;
    _modalResponses = data.responses;

    populateRoleFilter(data.responses);

    // Fetch tracking + rejected ids + accepted ids, then apply filters + build rows
    Promise.allSettled([
      fetch('/api/tracking/all').then(r=>r.json()),
      fetch('/api/rejected/ids').then(r=>r.json()),
      fetch('/api/accepted/ids').then(r=>r.json())
    ]).then(([trackRes, rejRes, accRes])=>{
      const byEmail={};
      if (trackRes.status==='fulfilled')
        (trackRes.value.records||[]).forEach(rec=>{ if(rec.email) byEmail[rec.email.toLowerCase()]=rec; });
      _trackingByEmail = byEmail;
      _rejectedIds = new Set(rejRes.status==='fulfilled' ? (rejRes.value.ids||[]) : []);
      _acceptedIds = new Set(accRes.status==='fulfilled' ? (accRes.value.ids||[]) : []);
      // Count flagged (sub-5-min) candidates across the whole dataset
      const flaggedCount = (_fullData?.responses||[]).filter(r =>
        isFastFill(byEmail[(extractFields(r).email||'').toLowerCase()])).length;
      const fEl = document.getElementById('fs-flagged');
      if (fEl) fEl.textContent = flaggedCount;
      applyFiltersAndRender();
    });
  }

  let _rejectedIds = new Set();
  let _acceptedIds = new Set();

  // Global hook so the Rejected/Accepted tabs can refresh decision badges
  window.refreshDecisionState = function(){
    Promise.allSettled([
      fetch('/api/rejected/ids').then(r=>r.json()),
      fetch('/api/accepted/ids').then(r=>r.json())
    ]).then(([rejRes, accRes])=>{
      _rejectedIds = new Set(rejRes.status==='fulfilled' ? (rejRes.value.ids||[]) : []);
      _acceptedIds = new Set(accRes.status==='fulfilled' ? (accRes.value.ids||[]) : []);
      if (_fullData) applyFiltersAndRender();
    }).catch(()=>{});
  };

  // Build the role dropdown from whatever roles are present in the data
  function populateRoleFilter(responses){
    const sel = document.getElementById('filter-role');
    if (!sel) return;
    const prev = sel.value;
    const roles = new Set();
    responses.forEach(r => {
      const role = r.role_name || extractFields(r).role;
      if (role && role !== '-') roles.add(role);
    });
    sel.innerHTML = '<option value="">All roles</option>' +
      [...roles].sort().map(role => `<option value="${role}">${role}</option>`).join('');
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
  }

  // Resolve a single candidate's role string for filtering
  function roleOf(r){
    return r.role_name || extractFields(r).role || '';
  }

  let _sortDir = 'desc';   // 'asc' | 'desc'
  function applyFiltersAndRender(){
    if (!_fullData) return;
    const roleSel   = (document.getElementById('filter-role')||{}).value || '';
    const statusSel = (document.getElementById('filter-status')||{}).value || '';
    const sortField = (document.getElementById('sort-field')||{}).value || 'date';

    let rows = _fullData.responses.slice();

    // Once a candidate is routed to the Accepted or Rejected pipeline they drop
    // off this page - it only lists candidates still awaiting a decision.
    rows = rows.filter(r => !_acceptedIds.has(r.response_id) && !_rejectedIds.has(r.response_id));
    const pendingTotal = rows.length;

    // Filter: role
    if (roleSel) rows = rows.filter(r => roleOf(r) === roleSel);

    // Filter: status
    if (statusSel === 'flagged') rows = rows.filter(r =>
        isFastFill(_trackingByEmail[(extractFields(r).email||'').toLowerCase()]));

    // Sort
    const dir = _sortDir === 'asc' ? 1 : -1;
    rows.sort((a,b) => {
      let av, bv;
      if (sortField === 'time'){
        av = (_trackingByEmail[(extractFields(a).email||'').toLowerCase()]||{}).time_taken_seconds ?? -1;
        bv = (_trackingByEmail[(extractFields(b).email||'').toLowerCase()]||{}).time_taken_seconds ?? -1;
      }
      else if (sortField === 'name'){
        av = (extractFields(a).name||'').toLowerCase(); bv = (extractFields(b).name||'').toLowerCase();
        return av < bv ? -dir : av > bv ? dir : 0;
      }
      else { // date
        av = a.submitted_at ? Date.parse(a.submitted_at) : 0;
        bv = b.submitted_at ? Date.parse(b.submitted_at) : 0;
      }
      return (av - bv) * dir;
    });

    // Update count label
    const cnt = document.getElementById('filter-count');
    if (cnt) cnt.textContent = `Showing ${rows.length} of ${pendingTotal} awaiting decision`;

    buildResponseRows(rows, _trackingByEmail);
    // Re-apply any active text search
    const sq = (document.getElementById('forms-search')||{}).value || '';
    if (sq) filterBySearch(sq);
  }

  let _trackingByEmail = {};
  // Threshold (seconds) under which a completion is flagged as suspiciously fast
  const FAST_FILL_SECONDS = 300;   // 5 minutes

  // Does this candidate's tracking record indicate a sub-5-minute completion?
  function isFastFill(rec){
    return !!(rec && rec.time_taken_seconds != null
              && rec.time_taken_seconds > 0
              && rec.time_taken_seconds < FAST_FILL_SECONDS);
  }

  function timeBadge(rec){
    if(!rec) return '<span style="opacity:.3;font-size:10px">-</span>';
    if(rec.time_taken_human){
      const secs=rec.time_taken_seconds||0;
      // colour: green normal, amber moderate/very-fast, red very-long
      let col='#34d399';
      if(secs<FAST_FILL_SECONDS) col='#fbbf24';   // under 5 min - flagged
      else if(secs>900) col='#f87171';            // very long
      else if(secs>300) col='#fbbf24';            // moderate
      const flag = isFastFill(rec)
        ? ` <span title="Completed in under 5 minutes" style="color:#f87171">🚩</span>` : '';
      return `<span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:${col};font-weight:600" title="Opened → submitted">${rec.time_taken_human}</span>${flag}`;
    }
    if(rec.status==='opened') return '<span style="opacity:.5;font-size:10px;color:#fbbf24">Opened, not submitted</span>';
    if(rec.status==='submitted') return '<span style="opacity:.4;font-size:10px" title="Submitted but no open recorded">submitted</span>';
    return '<span style="opacity:.3;font-size:10px">not opened</span>';
  }

  function buildResponseRows(rows, byEmail){
    const thead = document.getElementById('forms-thead');
    thead.innerHTML = `<th>#</th><th>Candidate</th><th>Role</th><th>Email</th><th>Location</th><th>Submitted</th><th>⏱ Time Taken</th><th>Decision</th><th></th><th></th>`;

    const tbody = document.getElementById('forms-tbody');
    tbody.innerHTML = '';

    if (!rows.length){
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:30px;color:var(--text-dim)">No candidates match the current filters.</td></tr>`;
      return;
    }

    rows.forEach((r, pos) => {
      const idx = r._idx;                 // true index into _modalResponses
      const f   = extractFields(r);
      const col = avatarColor(idx);
      const dateStr = r.submitted_at ? new Date(r.submitted_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '-';
      const role = roleOf(r);
      const displayName = f.name !== '-' ? f.name : 'Anonymous';
      const track = f.email !== '-' ? byEmail[f.email.toLowerCase()] : null;

      const isRejected = _rejectedIds.has(r.response_id);
      const isAccepted = _acceptedIds.has(r.response_id);
      let decisionCell;
      if (isRejected){
        decisionCell = `<span class="dec-badge dec-rejected" title="In Rejected tab">Rejected</span>`;
      } else if (isAccepted){
        decisionCell = `<span class="dec-badge dec-accepted" title="In Accepted tab">Accepted</span>`;
      } else {
        decisionCell = `<span style="display:inline-flex;gap:6px">
             <button class="dec-btn dec-accept" onclick="event.stopPropagation();openAcceptDialog('${r.response_id}')">Accept</button>
             <button class="dec-btn dec-reject" onclick="event.stopPropagation();openRejectDialog('${r.response_id}')">Reject</button>
           </span>`;
      }

      const tr = document.createElement('tr');
      tr.style.animationDelay = `${pos*25}ms`;
      if (isRejected || isAccepted) tr.style.opacity = '0.55';
      const flagged = isFastFill(track);
      if (flagged) tr.classList.add('row-flagged');
      const nameFlag = flagged
        ? ` <span class="fast-flag" title="Filled in under 5 minutes - review for low effort">🚩 ${track.time_taken_human}</span>` : '';
      tr.onclick = () => openCandModal(idx);
      tr.innerHTML = `
        <td style="color:var(--text-dim);font-family:'JetBrains Mono',monospace;font-size:10px">${pos+1}</td>
        <td>
          <div class="cand-chip">
            <div class="cand-avatar" style="background:linear-gradient(135deg,${col},${col}99)">${initials(f.name)}</div>
            <div><div class="cand-name">${displayName}${nameFlag}</div></div>
          </div>
        </td>
        <td>${role && role!=='-' ? `<span class="td-pill" style="background:rgba(31,45,89,.12);color:#c4b5fd">${role}</span>` : '<span style="opacity:.35">-</span>'}</td>
        <td class="td-email">${f.email !== '-' ? f.email : '<span style="opacity:.35;font-style:italic">-</span>'}</td>
        <td><span class="td-pill">${f.location !== '-' ? f.location : '-'}</span></td>
        <td style="font-size:10px;white-space:nowrap;color:var(--text-dim)">${dateStr}</td>
        <td style="white-space:nowrap">${timeBadge(track)}</td>
        <td style="white-space:nowrap">${decisionCell}</td>
        <td><button class="td-view-btn" onclick="event.stopPropagation();openCandModal(${idx})">VIEW</button></td>
        <td><button class="td-del-btn" title="Delete candidate" aria-label="Delete candidate" onclick="event.stopPropagation();confirmDelete(this,'${r.response_id}',${idx})">🗑</button></td>`;
      tbody.appendChild(tr);
    });
  }

  function filterBySearch(q){
    q = (q||'').toLowerCase();
    document.querySelectorAll('#forms-tbody tr').forEach(tr => {
      if (tr.querySelector('td[colspan]')) return;
      tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  // Search
  document.getElementById('forms-search').addEventListener('input', e => {
    filterBySearch(e.target.value);
  });

  // Filter + sort controls
  ['filter-role','filter-status','sort-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', applyFiltersAndRender);
  });
  const sortDirBtn = document.getElementById('sort-dir');
  if (sortDirBtn) sortDirBtn.addEventListener('click', () => {
    _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
    sortDirBtn.textContent = _sortDir === 'asc' ? '↑ Asc' : '↓ Desc';
    applyFiltersAndRender();
  });

  // Sync from Google
  fetchBtn.addEventListener('click', async () => {
    const usingOAuth = usingOAuthForms();
    if (!_formId) {
      toast('Set the recruitment form ID in Admin Settings first.', 'err');
      return;
    }
    const requiredFields = [];
    if (!usingOAuth) {
      requiredFields.push({ input: credsEl, message: 'Paste the service account JSON key (should start with {).',
        test: v => v.startsWith('{') });
    }
    if (!validateRequired(requiredFields)) return;
    const payload = { form_id: _formId, auth_mode: usingOAuth ? 'oauth' : 'service_account' };
    if (!usingOAuth) payload.credentials_json = credsEl.value.trim();
    fetchBtn.disabled = true;
    fetchBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 15 15" fill="none" style="animation:spin 0.9s linear infinite;flex-shrink:0"><circle cx="7.5" cy="7.5" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="18 8" stroke-linecap="round"/></svg> Syncing…`;
    document.getElementById('progress-rail').classList.add('on');
    infoEl.textContent = 'Connecting to Google Forms API…';
    try {
      const res = await fetch('/api/forms/fetch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(errDetail(data, res.status));
      toast(`⚡ ${data.new_added} new response${data.new_added!==1?'s':''} - ${data.total} total`, 'ok');
      infoEl.textContent = `${data.total} responses stored · ${data.new_added} new`;
      renderResponses(data);
    } catch (err) {
      const firstLine = String(err.message).split('\n')[0];
      toast(`Forms error: ${firstLine}`, 'err');
      infoEl.style.whiteSpace = 'pre-wrap';
      infoEl.style.color = 'var(--red, #ff6b6b)';
      infoEl.textContent = err.message;
    } finally {
      document.getElementById('progress-rail').classList.remove('on');
      validate();
      fetchBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 15 15" fill="none"><path d="M7.5 1v5.5M7.5 13v-4M1 7.5h5.5M13 7.5h-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="7.5" cy="7.5" r="6.5" stroke="currentColor" stroke-width="1.4"/></svg> Sync Responses`;
    }
  });

  // Load stored
  loadBtn.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/forms/responses');
      if (!res.ok) { const e = await res.json(); throw new Error(errDetail(e, res.status)); }
      const data = await res.json();
      renderResponses(data);
      lastEl.value = data.last_synced || '';
      infoEl.textContent = `${data.total} stored responses loaded`;
      dlBtn.style.display = 'inline-flex';
      toast(`Loaded ${data.total} stored responses`, 'inf');
    } catch (err) {
      toast(err.message, err.message.includes('No form') ? 'inf' : 'err');
      infoEl.textContent = err.message;
    }
  });

})();
