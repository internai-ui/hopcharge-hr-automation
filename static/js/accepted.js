/* ───────── ACCEPTED CANDIDATES PAGE (3-stage pipeline) ───────── */
(function(){
  if (!document.getElementById('page-accepted')) return;
  // ── Local cache of the whole pipeline ──
  // We fetch ALL accepted candidates once and keep them in memory, then switch
  // tabs / search purely locally. This makes tab switching instant (no network)
  // and removes the race where a slow per-stage fetch could render the wrong
  // stage's rows under the wrong heading.
  let _all = [];                  // every accepted candidate, all stages
  let _rows = [];                 // candidates in the currently-selected stage (derived from _all)
  let _counts = { hr:0, round1:0, round2:0, onboarded:0 };
  let _stage = 'hr';              // hr | round1 | round2 | onboarded
  let _labels = { hr:'HR Round', round1:'Round 1', round2:'Round 2', onboarded:'Onboarded' };
  let _loadToken = 0;             // monotonic guard: only the newest fetch may apply
  let _refreshTimer = null;
  let _detailOpen = false;   // true while a candidate detail row is expanded
  const NEXT = { hr:'round1', round1:'round2', round2:'onboarded', onboarded:null };
  const PREV = { hr:null, round1:'hr', round2:'round1', onboarded:'round2' };

  function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  let _selectedSet = new Set();

  // Recompute the derived view (_rows, counts, title, action wraps) from the
  // in-memory cache for the active stage, then render. Pure local - instant.
  function applyLocal(){
    _rows = _all.filter(r => (r.stage || 'hr') === _stage);
    ['hr','round1','round2','onboarded'].forEach(s=>{
      const el = document.getElementById('acc-count-'+s);
      if (el) el.textContent = _counts[s] || 0;
    });
    document.getElementById('acc-stage-title').textContent = _labels[_stage] || _stage;
    const _r1w = document.getElementById('r1-invite-wrap');
    if (_r1w) _r1w.style.display = (_stage === 'round1') ? '' : 'none';
    const _r2w = document.getElementById('r2-onboard-wrap');
    if (_r2w) _r2w.style.display = (_stage === 'round2') ? '' : 'none';
    // Role filter (applies on top of the stage slice). Normalised, both-ways
    // substring so a stored "Operations Specialist" matches the "Operations
    // Specialist(s)" option regardless of plural/casing/spacing differences.
    let base = _rows;
    const roleSel = (document.getElementById('acc-role-filter')?.value || '');
    if (roleSel){
      const norm = s => String(s||'').toLowerCase().replace(/\s+/g,' ').trim();
      const want = norm(roleSel);
      base = base.filter(r => { const have = norm(r.role); return !!have && (have.includes(want) || want.includes(have)); });
    }
    // Re-apply any active search filter on top of the stage + role slice.
    const q = (document.getElementById('acc-search')?.value || '').toLowerCase();
    if (q){
      renderAccepted(base.filter(r =>
        JSON.stringify([r.name,r.email,r.phone,r.role,r.accepted_note]).toLowerCase().includes(q)));
    } else {
      renderAccepted(base);
    }
  }

  // Fetch the WHOLE pipeline once (no stage param → backend returns all stages).
  // `background` = true means don't disrupt the view if it fails; just keep
  // showing what we have. A monotonic token discards out-of-order responses.
  window.loadAcceptedPage = async function(opts){
    const background = opts && opts.background;
    const myToken = ++_loadToken;
    try{
      const [accRes, selRes] = await Promise.allSettled([
        fetch('/api/accepted').then(r=>r.json()),      // NO stage param = all rows
        fetch('/api/selection').then(r=>r.ok ? r.json() : {ids:[]}),
      ]);
      // If a newer load started while we were waiting, drop this stale result.
      if (myToken !== _loadToken) return;
      if (accRes.status === 'fulfilled'){
        const d = accRes.value;
        _all = d.accepted || [];
        _labels = d.stage_labels || _labels;
        _counts = d.counts || _counts;
      }
      if (selRes.status === 'fulfilled'){
        _selectedSet = new Set(selRes.value.ids || []);
      }
      applyLocal();
    }catch(e){
      if (!background){ /* leave whatever is on screen */ }
    }
  };

  // Light background refresh so the list stays current without the user doing
  // anything. Cheap (one small request); only the active tab re-renders.
  function startAutoRefresh(){
    if (_refreshTimer) return;
    _refreshTimer = setInterval(()=>{
      // Don't refresh while the candidate detail modal is open - the user may be
      // typing a note. Resume once it's closed.
      const modal = document.getElementById('cand-modal');
      if (modal && modal.classList.contains('open')) return;
      if (document.getElementById('page-accepted').classList.contains('active'))
        loadAcceptedPage({background:true});
    }, 30000); // every 30s
  }

  function renderAccepted(rows){
    // Any full re-render removes expanded detail rows, so the flag is no longer open.
    _detailOpen = false;
    const tbody = document.getElementById('acc-tbody');
    if (!rows.length){
      let msg;
      if (_stage==='hr')
        msg = 'No candidates in this stage yet. Use the Accept button on the Form Responses tab.';
      else if (_stage==='onboarded')
        msg = 'No onboarded candidates yet. In Round 2, mark someone ★ SELECTED and then move them here with ➜ ONBOARDED.';
      else
        msg = `No candidates in ${_labels[_stage]} yet. Promote them from ${_labels[PREV[_stage]]}.`;
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:34px;color:var(--text-dim)">${msg}</td></tr>`;
      return;
    }
    tbody.innerHTML = '';
    rows.forEach((r, i)=>{
      const since = r.stage_changed_at || r.accepted_at;
      const when = since ? new Date(since).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '-';
      const isSelected = _selectedSet.has(r.response_id);
      let promoteBtn;
      if (_stage === 'hr' || _stage === 'round1'){
        // Straight promote to the next interview round.
        const nx = NEXT[_stage];
        promoteBtn = `<button class="td-view-btn" style="border-color:rgba(52,211,153,.5);color:#34d399" title="Advance to ${_labels[nx]}" aria-label="Advance to ${_labels[nx]}" onclick="event.stopPropagation();promoteAccepted('${esc(r.response_id)}')">➜ ${esc(_labels[nx].toUpperCase())}</button>`;
      } else if (_stage === 'round2'){
        // Round 2 is the hiring decision. First SELECT (mark hired), then the
        // button becomes "➜ ONBOARDED" to move them into the final stage.
        if (!isSelected){
          promoteBtn = `<button class="td-view-btn" style="border-color:rgba(250,204,21,.6);color:#facc15" title="Mark as selected (hired)" aria-label="Mark as selected (hired)" onclick="event.stopPropagation();markSelected('${esc(r.response_id)}')">★ SELECT</button>`;
        } else {
          promoteBtn = `<span class="dec-badge dec-accepted" title="Selected (hired)" style="border-color:rgba(250,204,21,.55);color:#facc15">★ SELECTED</span> `
            + `<button class="td-view-btn" style="border-color:rgba(96,165,250,.6);color:#60a5fa" title="Move to Onboarded - do this after sending the onboarding form" aria-label="Move to Onboarded - do this after sending the onboarding form" onclick="event.stopPropagation();promoteAccepted('${esc(r.response_id)}')">➜ ONBOARDED</button> `
            + `<button class="td-mini-btn" title="Undo selection" aria-label="Undo selection" onclick="event.stopPropagation();unmarkSelected('${esc(r.response_id)}')">↩</button>`;
        }
      } else { // onboarded - terminal stage
        promoteBtn = `<span class="dec-badge dec-accepted" title="Hired & onboarding form sent" style="border-color:rgba(96,165,250,.55);color:#60a5fa">✓ ONBOARDED</span>`;
      }
      const backBtn = PREV[_stage]
        ? `<button class="td-mini-btn" title="Move back to ${_labels[PREV[_stage]]}" aria-label="Move back to ${_labels[PREV[_stage]]}" onclick="event.stopPropagation();demoteAccepted('${esc(r.response_id)}')">↩</button>`
        : `<button class="td-mini-btn" title="Remove from pipeline" aria-label="Remove from pipeline" onclick="event.stopPropagation();removeAccepted('${esc(r.response_id)}')">✕</button>`;
      // Reject is available at EVERY stage (HR Round, Round 1, Round 2,
      // Onboarded), not just the HR Round. It moves the candidate to the
      // Rejected tab and removes them from this pipeline.
      const rejectBtn = `<button class="td-mini-btn labeled" title="Reject - move to the Rejected tab" aria-label="Reject - move to the Rejected tab" style="border-color:rgba(248,113,113,.5);color:#f87171;margin-left:6px" onclick="event.stopPropagation();rejectAccepted('${esc(r.response_id)}')">✕ Reject</button>`;
      const tr = document.createElement('tr');
      tr.style.animationDelay = `${i*25}ms`;
      tr.style.cursor = 'pointer';
      tr.onclick = ()=> toggleAnswers(r, tr);   // pass the row itself, not an index
      tr.innerHTML = `
        <td style="color:var(--text-dim);font-family:'JetBrains Mono',monospace;font-size:10px">${i+1}</td>
        <td><div class="cand-name">${esc(r.name)||'Anonymous'}</div></td>
        <td class="td-email">${esc(r.email)||'<span style="opacity:.35">-</span>'}</td>
        <td class="td-phone">${esc(r.phone)||'<span style="opacity:.35">-</span>'}</td>
        <td>${r.role?`<span class="td-pill" style="background:rgba(31,45,89,.12);color:#c4b5fd">${esc(r.role)}</span>`:'<span style="opacity:.35">-</span>'}</td>
        <td style="font-size:10px;white-space:nowrap;color:var(--text-dim)">${when}</td>
        <td style="white-space:nowrap">${promoteBtn}</td>
        <td style="white-space:nowrap">${backBtn}${rejectBtn}</td>`;
      tbody.appendChild(tr);
    });
  }

  // Open the candidate's full detail in the SAME pop-out modal the CV-parser
  // stage uses (#cand-modal), instead of an inline dropdown. Reuses the modal
  // shell + styling, and the same notes API path (accLoadNotes/accAddNote).
  function toggleAnswers(r, tr){ openAcceptedModal(r); }

  function _accInitials(name){
    const p = String(name||'').trim().split(/\s+/).filter(Boolean);
    return ((p[0]?.[0]||'') + (p[1]?.[0]||'')).toUpperCase() || '?';
  }

  window.openAcceptedModal = function(r){
    if (!r) return;
    const rid = esc(r.response_id);
    // Header
    const av = document.getElementById('cm-avatar');
    if (av){ av.style.background = 'linear-gradient(135deg,#1F2D59,#2F5BEA)'; av.textContent = _accInitials(r.name); }
    const nm = document.getElementById('cm-name');
    if (nm) nm.textContent = r.name || 'Anonymous';
    const meta = document.getElementById('cm-meta');
    const since = r.stage_changed_at || r.accepted_at;
    const sinceStr = since ? new Date(since).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '-';
    if (meta) meta.textContent = `${_labels[_stage]||_stage}  ·  ${r.role || 'Role not specified'}  ·  since ${sinceStr}`;

    const body = document.getElementById('cm-body');
    body.innerHTML = '';

    // Identity block
    const idItems = [
      { q:'Full Name',     a:r.name },
      { q:'Email Address',  a:r.email },
      { q:'Phone Number',  a:r.phone },
      { q:'Location / City', a:r.location || r.city },
      { q:'Role',          a:r.role, full:true },
    ];
    idItems.forEach(g=>{
      const div = document.createElement('div');
      div.className = 'cand-qa-item' + (g.full ? ' full' : '');
      const empty = !g.a && g.a !== 0;
      div.innerHTML = `<div class="cand-qa-q">${g.q}</div><div class="cand-qa-a${empty?' empty':''}">${empty?'Not provided':esc(String(g.a))}</div>`;
      body.appendChild(div);
    });

    // Pipeline trail + accept note
    const hist = (r.history||[]).map(h=>`${_labels[h.stage]||h.stage}`).join('  →  ');
    if (hist){
      const t = document.createElement('div');
      t.className = 'cand-qa-item full';
      t.innerHTML = `<div class="cand-qa-q">Pipeline</div><div class="cand-qa-a">${esc(hist)}</div>`;
      body.appendChild(t);
    }
    if (r.accepted_note){
      const n = document.createElement('div');
      n.className = 'cand-qa-item full';
      n.innerHTML = `<div class="cand-qa-q">Decision note</div><div class="cand-qa-a">${esc(r.accepted_note)}</div>`;
      body.appendChild(n);
    }

    // Answers
    const ans = (r.answers||[]);
    if (ans.length){
      ans.forEach(a=>{
        const div = document.createElement('div');
        div.className = 'cand-qa-item full';
        const empty = !a.answer;
        div.innerHTML = `<div class="cand-qa-q">${esc(a.question)}</div><div class="cand-qa-a${empty?' empty':''}">${empty?'no answer':esc(a.answer)}</div>`;
        body.appendChild(div);
      });
    }

    // Notes timeline (same /api/notes path, reused handlers)
    const notesWrap = document.createElement('div');
    notesWrap.className = 'cand-qa-item full';
    notesWrap.style.cssText = 'margin-top:8px;border-top:1px solid var(--border-dim);padding-top:18px';
    notesWrap.innerHTML = `
      <div class="cand-qa-q">Notes <span style="opacity:.55;font-weight:400">- personal remarks, any round</span></div>
      <div id="accnotes-list-${rid}" style="margin:10px 0 14px"><div style="font-size:12px;color:var(--text-dim)">Loading notes…</div></div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <input id="accnote-stage-${rid}" type="text" placeholder="Stage / round (optional)" class="field-input" style="font-size:12px">
        <textarea id="accnote-text-${rid}" rows="2" placeholder="Add a remark about this candidate…" class="field-input" style="font-size:12.5px;resize:vertical"></textarea>
        <div style="display:flex;justify-content:flex-end"><button class="btn-ghost accnote-add" data-rid="${rid}">+ Add note</button></div>
      </div>`;
    body.appendChild(notesWrap);
    const addBtn = notesWrap.querySelector('.accnote-add');
    if (addBtn) addBtn.addEventListener('click', ()=> accAddNote(r.response_id));
    accLoadNotes(r.response_id);

    document.getElementById('cand-modal').classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  // ── Notes helpers for the Accepted/HR Round view (same /api/notes API) ──
  function accLoadNotes(responseId){
    const list = document.getElementById('accnotes-list-'+responseId);
    if (!list) return;
    fetch(`/api/notes/${encodeURIComponent(responseId)}`).then(r=>r.json()).then(d=>{
      const notes = d.notes || [];
      if (!notes.length){ list.innerHTML = '<div style="font-size:12px;color:var(--text-dim)">No notes yet.</div>'; return; }
      list.innerHTML = notes.map(n=>{
        const when = n.created_at ? new Date(n.created_at).toLocaleString('en-IN',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        const stage = n.stage ? `<span style="font-size:9.5px;font-family:'Poppins',sans-serif;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:var(--violet);background:rgba(31,45,89,.12);border:1px solid rgba(31,45,89,.3);border-radius:4px;padding:1px 7px;margin-right:7px">${esc(n.stage)}</span>` : '';
        return `<div style="background:var(--glass-2);border:1px solid var(--border-dim);border-radius:8px;padding:9px 11px;margin-bottom:7px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
            <div style="font-size:12.5px;line-height:1.5;color:var(--text-mid);white-space:pre-wrap;flex:1">${stage}${esc(n.text)}</div>
            <button class="accnote-del" data-rid="${esc(responseId)}" data-nid="${esc(n.id)}" title="Delete note" aria-label="Delete note" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:13px;flex-shrink:0">🗑</button>
          </div>
          <div style="font-size:10px;color:var(--text-dim);margin-top:5px;font-family:'JetBrains Mono',monospace">${esc(when)}</div>
        </div>`;
      }).join('');
      list.querySelectorAll('.accnote-del').forEach(b=> b.addEventListener('click', ()=> accDeleteNote(b.dataset.rid, b.dataset.nid)));
    }).catch(()=>{ list.innerHTML = '<div style="font-size:12px;color:#f87171">Could not load notes.</div>'; });
  }
  async function accAddNote(responseId){
    const t = document.getElementById('accnote-text-'+responseId);
    const s = document.getElementById('accnote-stage-'+responseId);
    if (t && !validateRequired([{ input: t, message: 'Write a note first.' }])) return;
    const text = (t?.value||'').trim();
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text, stage:(s?.value||'').trim()})});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      if(t) t.value=''; if(s) s.value=''; toast('Note added','ok'); accLoadNotes(responseId);
    }catch(e){ toast('Could not add note: '+e.message,'err'); }
  }
  async function accDeleteNote(responseId, noteId){
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}/${encodeURIComponent(noteId)}`,{method:'DELETE'});
      const d = await res.json().catch(()=>({})); if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Note deleted','ok'); accLoadNotes(responseId);
    }catch(e){ toast('Could not delete note: '+e.message,'err'); }
  }

  // Reject a candidate from any stage of the Accepted pipeline. Reuses the
  // shared reject dialog (with the round dropdown); the {fromPipeline:true}
  // flag makes the confirm handler also remove them from the accepted store
  // and refresh this page, so they end up only in the Rejected tab.
  window.rejectAccepted = function(responseId){
    const row = _all.find(r => r.response_id === responseId);
    const name = row ? (row.name || 'this candidate') : 'this candidate';
    if (typeof window.openRejectDialog === 'function'){
      window.openRejectDialog(responseId, {fromPipeline:true, name});
    } else {
      toast('Reject dialog unavailable', 'err');
    }
  };

  window.promoteAccepted = async function(responseId){
    // Optimistic: move the row to the next stage in the local cache and
    // re-render instantly, then confirm with the server in the background.
    const row = _all.find(r => r.response_id === responseId);
    const from = row ? (row.stage || 'hr') : null;
    const to = from ? NEXT[from] : null;
    if (row && to){
      row.stage = to;
      row.stage_changed_at = new Date().toISOString();
      if (_counts[from]!=null) _counts[from]--;
      if (_counts[to]!=null) _counts[to]++;
      applyLocal();
    }
    try{
      const res = await fetch('/api/accepted/promote', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:responseId})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast(`Advanced to ${d.stage_label}`, 'ok');
      // No forced reload here: the optimistic update already shows the correct
      // state, and the 30s background refresh reconciles with the server. This
      // avoids a flicker where a slightly-stale read could momentarily revert
      // the row before settling.
    }catch(e){
      toast('Promote failed: '+e.message, 'err');
      loadAcceptedPage();                    // only reload to revert on actual failure
    }
  };

  window.demoteAccepted = async function(responseId){
    const row = _all.find(r => r.response_id === responseId);
    const from = row ? (row.stage || 'hr') : null;
    const to = from ? PREV[from] : null;
    if (row && to){
      row.stage = to;
      row.stage_changed_at = new Date().toISOString();
      if (_counts[from]!=null) _counts[from]--;
      if (_counts[to]!=null) _counts[to]++;
      applyLocal();
    }
    try{
      const res = await fetch('/api/accepted/demote', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:responseId})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast(`Moved back to ${d.stage_label}`, 'inf');
    }catch(e){
      toast('Move failed: '+e.message, 'err');
      loadAcceptedPage();
    }
  };

  window.removeAccepted = async function(responseId){
    const row = _all.find(r => r.response_id === responseId);
    const from = row ? (row.stage || 'hr') : null;
    if (row){
      _all = _all.filter(r => r.response_id !== responseId);
      if (from && _counts[from]!=null) _counts[from]--;
      applyLocal();
    }
    try{
      const res = await fetch(`/api/accepted/${encodeURIComponent(responseId)}`, {method:'DELETE'});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Candidate removed from pipeline', 'inf');
      if (window.refreshDecisionState) window.refreshDecisionState();
    }catch(e){
      toast('Remove failed: '+e.message, 'err');
      loadAcceptedPage();
    }
  };

  /* ── Onboarding email ── */
  const _obDialog = document.getElementById('onboard-dialog');
  document.getElementById('r2-onboard-btn').addEventListener('click', () => {
    document.getElementById('ob-status').style.display = 'none';
    // Show exactly who will be emailed: the SELECTED Round 2 candidates only.
    const selected = _all.filter(r => (r.stage||'hr')==='round2' && _selectedSet.has(r.response_id));
    const who = document.getElementById('ob-recipients');
    if (who){
      if (selected.length){
        who.innerHTML = `Will send to <b style="color:#6ee7b7">${selected.length} selected</b> candidate${selected.length!==1?'s':''}: `
          + selected.map(r=>esc(r.name||r.email)).join(', ');
        who.style.color = 'var(--text-mid)';
      } else {
        who.innerHTML = `No Round 2 candidates are marked <b style="color:#facc15">★ SELECTED</b> yet. `
          + `Use the SELECT button on a candidate first - onboarding only goes to selected people.`;
        who.style.color = '#facc15';
      }
    }
    _obDialog.style.display = 'flex';
  });
  document.getElementById('ob-cancel').addEventListener('click', () => { _obDialog.style.display = 'none'; });
  _obDialog.addEventListener('click', e => { if (e.target === _obDialog) _obDialog.style.display = 'none'; });

  document.getElementById('ob-send').addEventListener('click', async () => {
    const formEl2 = document.getElementById('ob-form');
    if (!validateRequired([
      { input: formEl2, message: 'Enter the onboarding form link.' },
    ])) return;
    const form = formEl2.value.trim();

    // Onboarding goes ONLY to candidates explicitly marked SELECTED in Round 2.
    const selectedIds = _all
      .filter(r => (r.stage||'hr')==='round2' && _selectedSet.has(r.response_id))
      .map(r => r.response_id);
    if (!selectedIds.length){
      toast('Select at least one candidate first - onboarding only goes to ★ SELECTED people', 'err');
      return;
    }

    const btn = document.getElementById('ob-send');
    const statusBox = document.getElementById('ob-status');
    btn.disabled = true; btn.textContent = 'Sending…';
    statusBox.style.display = 'none';

    try {
      const res = await fetch('/api/send-onboarding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ form_link: form, response_ids: selectedIds })
      });
      const d = await res.json();
      if (!res.ok) throw new Error(errDetail(d, res.status));

      const sent   = d.sent   || 0;
      const failed = d.failed || 0;
      const rows   = (d.results || []).map(r =>
        `<div style="font-size:12px;margin-top:4px;">
           ${r.status === 'sent' ? '&#x2713;' : '&#x2717;'}
           <b>${r.name}</b> - ${r.email}
           ${r.error ? `<span style="color:#f87171"> (${r.error})</span>` : ''}
         </div>`).join('');

      statusBox.innerHTML = `<b style="color:${failed ? '#fbbf24' : '#34d399'}">
        ${sent} sent${failed ? ', ' + failed + ' failed' : ' - all done!'}</b>${rows}`;
      statusBox.style.display = 'block';
      toast(`Onboarding email sent to ${sent} selected candidate${sent !== 1 ? 's' : ''}`, 'ok');
    } catch(e) {
      statusBox.textContent = 'Error: ' + e.message;
      statusBox.style.display = 'block';
      toast('Send failed: ' + e.message, 'err');
    } finally {
      btn.disabled = false; btn.textContent = 'Send to selected';
    }
  });

  window.markSelected = async function(responseId){
    // Optimistic: flip the UI immediately, then confirm with the server.
    _selectedSet.add(responseId);
    applyLocal();
    try{
      const res = await fetch('/api/selection/mark', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:responseId})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Marked as selected \uD83C\uDF89', 'ok');
    }catch(e){
      _selectedSet.delete(responseId);   // roll back the optimistic change
      applyLocal();
      toast('Select failed: '+e.message, 'err');
    }
  };

  window.unmarkSelected = async function(responseId){
    _selectedSet.delete(responseId);
    applyLocal();
    try{
      const res = await fetch('/api/selection/unmark', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({response_id:responseId})});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Selection removed', 'inf');
    }catch(e){
      _selectedSet.add(responseId);      // roll back
      applyLocal();
      toast('Undo failed: '+e.message, 'err');
    }
  };

  // Stage tab switching - purely local, no network. Instant.
  document.querySelectorAll('#acc-stage-tabs .stage-tab').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      document.querySelectorAll('#acc-stage-tabs .stage-tab').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');
      _stage = btn.dataset.stage;
      const s = document.getElementById('acc-search'); if (s) s.value='';
      applyLocal();                 // render from cache immediately
    });
  });

  const search = document.getElementById('acc-search');
  if (search) search.addEventListener('input', ()=> applyLocal());
  const roleFilter = document.getElementById('acc-role-filter');
  if (roleFilter) roleFilter.addEventListener('change', ()=> applyLocal());

  // First time the Accepted page is opened: full fetch + start auto-refresh.
  document.querySelector('.sb-item[data-page="accepted"]')
    ?.addEventListener('click', ()=>{ loadAcceptedPage(); startAutoRefresh(); });
})();
