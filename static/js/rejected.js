/* ───────── REJECTED CANDIDATES PAGE ───────── */
(function(){
  if (!document.getElementById('page-rejected')) return;
  let _rows = [];
  // Ephemeral, page-local selection for bulk-sending the rejection email —
  // unlike onboarding's persisted ★ SELECTED flag, this doesn't need to
  // survive a reload; it's just "who am I about to email right now".
  const _rejSelectedSet = new Set();

  window.loadRejectedPage = async function(){
    try{
      const res = await fetch('/api/rejected');
      const d = await res.json();
      _rows = d.rejected || [];
      _rejSelectedSet.clear();
      renderRejected(_rows);
    }catch(e){ /* leave placeholder row */ }
  };

  function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function _updateRejEmailCount(){
    const countEl = document.getElementById('rej-email-count');
    if (countEl) countEl.textContent = `(${_rejSelectedSet.size} selected)`;
    const selectAll = document.getElementById('rej-select-all');
    if (selectAll){
      const visible = Array.from(document.querySelectorAll('#rej-tbody .rej-select-row'));
      selectAll.checked = visible.length > 0 && visible.every(cb => cb.checked);
      selectAll.indeterminate = !selectAll.checked && visible.some(cb => cb.checked);
    }
  }

  function renderRejected(rows){
    // Stats
    document.getElementById('rej-total').textContent = rows.length;
    const r0 = rows.filter(r=>(r.rejected_round||'').toLowerCase().includes('round 0')).length;
    document.getElementById('rej-r0').textContent = r0;
    document.getElementById('rej-rlater').textContent = rows.length - r0;

    const tbody = document.getElementById('rej-tbody');
    if (!rows.length){
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:34px;color:var(--text-dim)">No rejected candidates yet. Use the ✕ Reject button on the Form Responses tab.</td></tr>';
      _updateRejEmailCount();
      return;
    }
    tbody.innerHTML = '';
    rows.forEach((r, i)=>{
      const when = r.rejected_at ? new Date(r.rejected_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '—';
      const notifiedCell = r.rejection_email_sent_at
        ? `<span title="${esc(r.rejection_email_sent_at)}" style="font-size:10px;color:#34d399">&#10003; ${new Date(r.rejection_email_sent_at).toLocaleDateString('en-IN',{day:'numeric',month:'short'})}</span>`
        : '<span style="opacity:.35;font-size:10px">—</span>';
      const tr = document.createElement('tr');
      tr.style.animationDelay = `${i*25}ms`;
      tr.style.cursor = 'pointer';
      tr.onclick = ()=> openRejectedModal(r);
      tr.innerHTML = `
        <td onclick="event.stopPropagation()"><input type="checkbox" class="rej-select-row" data-rid="${esc(r.response_id)}" aria-label="Select ${esc(r.name)||'candidate'} for rejection email"></td>
        <td style="color:var(--text-dim);font-family:'JetBrains Mono',monospace;font-size:10px">${i+1}</td>
        <td><div class="cand-name">${esc(r.name)||'Anonymous'}</div></td>
        <td class="td-email">${esc(r.email)||'<span style="opacity:.35">—</span>'}</td>
        <td class="td-phone">${esc(r.phone)||'<span style="opacity:.35">—</span>'}</td>
        <td>${r.role?`<span class="td-pill" style="background:rgba(31,45,89,.12);color:#c4b5fd">${esc(r.role)}</span>`:'<span style="opacity:.35">—</span>'}</td>
        <td><span class="dec-badge dec-rejected">${esc(r.rejected_round)||'—'}</span></td>
        <td style="font-size:10px;white-space:nowrap;color:var(--text-dim)">${when}</td>
        <td>${notifiedCell}</td>
        <td><button class="td-view-btn" onclick="event.stopPropagation();this.closest('tr').click()">VIEW</button></td>
        <td><button class="td-view-btn rej-restore-btn" data-rid="${esc(r.response_id)}" style="border-color:rgba(52,211,153,.45);color:#34d399" title="Restore to active pipeline" aria-label="Restore to active pipeline">↩ RESTORE</button></td>`;
      // Wire the restore button via a listener (robust to any characters in
      // the response_id — building the call as an inline onclick string broke
      // parsing for some IDs, so the click silently did nothing).
      const restoreBtn = tr.querySelector('.rej-restore-btn');
      if (restoreBtn){
        restoreBtn.addEventListener('click', (ev)=>{
          ev.stopPropagation();
          restoreRejected(restoreBtn.dataset.rid);
        });
      }
      const selectCb = tr.querySelector('.rej-select-row');
      if (selectCb){
        selectCb.checked = _rejSelectedSet.has(r.response_id);
        selectCb.addEventListener('change', ()=>{
          if (selectCb.checked) _rejSelectedSet.add(r.response_id);
          else _rejSelectedSet.delete(r.response_id);
          _updateRejEmailCount();
        });
      }
      tbody.appendChild(tr);
    });
    _updateRejEmailCount();
  }

  document.getElementById('rej-select-all')?.addEventListener('change', (e)=>{
    const checked = e.target.checked;
    document.querySelectorAll('#rej-tbody .rej-select-row').forEach(cb=>{
      cb.checked = checked;
      if (checked) _rejSelectedSet.add(cb.dataset.rid);
      else _rejSelectedSet.delete(cb.dataset.rid);
    });
    _updateRejEmailCount();
  });

  /* ── Send Rejection Email dialog ── */
  const _rejEmailDialog = document.getElementById('rejection-email-dialog');
  document.getElementById('rej-email-btn')?.addEventListener('click', ()=>{
    document.getElementById('rej-email-status').style.display = 'none';
    const selected = _rows.filter(r => _rejSelectedSet.has(r.response_id));
    const who = document.getElementById('rej-email-recipients');
    if (who){
      if (selected.length){
        who.innerHTML = `Will send to <b style="color:#f87171">${selected.length} selected</b> candidate${selected.length!==1?'s':''}: `
          + selected.map(r=>esc(r.name||r.email)).join(', ');
        who.style.color = 'var(--text-mid)';
      } else {
        who.innerHTML = `No candidates checked yet. Use the checkboxes in the table to pick who receives the rejection notice.`;
        who.style.color = '#fbbf24';
      }
    }
    _rejEmailDialog.style.display = 'flex';
  });
  document.getElementById('rej-email-cancel')?.addEventListener('click', ()=>{ _rejEmailDialog.style.display = 'none'; });
  _rejEmailDialog?.addEventListener('click', e=>{ if (e.target === _rejEmailDialog) _rejEmailDialog.style.display = 'none'; });

  document.getElementById('rej-email-send')?.addEventListener('click', async ()=>{
    const selectedIds = Array.from(_rejSelectedSet);
    if (!selectedIds.length){
      toast('Check at least one candidate first', 'err');
      return;
    }

    const btn = document.getElementById('rej-email-send');
    const statusBox = document.getElementById('rej-email-status');
    btn.disabled = true; btn.textContent = 'Sending…';
    statusBox.style.display = 'none';

    try{
      const res = await fetch('/api/send-rejection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response_ids: selectedIds })
      });
      const d = await res.json();
      if (!res.ok) throw new Error(errDetail(d, res.status));

      const sent = d.sent || 0, failed = d.failed || 0;
      const rows = (d.results || []).map(r =>
        `<div style="font-size:12px;margin-top:4px;">
           ${r.status === 'sent' ? '&#x2713;' : '&#x2717;'}
           <b>${esc(r.name)}</b> — ${esc(r.email)}
           ${r.error ? `<span style="color:#f87171"> (${esc(r.error)})</span>` : ''}
         </div>`).join('');
      statusBox.innerHTML = `<b style="color:${failed ? '#fbbf24' : '#34d399'}">
        ${sent} sent${failed ? ', ' + failed + ' failed' : ' — all done!'}</b>${rows}`;
      statusBox.style.display = 'block';
      toast(`Rejection email sent to ${sent} candidate${sent !== 1 ? 's' : ''}`, sent && !failed ? 'ok' : 'inf');
      loadRejectedPage();
    }catch(e){
      statusBox.textContent = 'Error: ' + e.message;
      statusBox.style.display = 'block';
      toast('Send failed: ' + e.message, 'err');
    }finally{
      btn.disabled = false; btn.textContent = 'Send to selected';
    }
  });

  function _rejInitials(name){
    const p = String(name||'').trim().split(/\s+/).filter(Boolean);
    return ((p[0]?.[0]||'') + (p[1]?.[0]||'')).toUpperCase() || '?';
  }

  // Open the rejected candidate's full profile in the SAME pop-out modal
  // (#cand-modal) the Accepted pipeline uses — a candidate tile, not an inline
  // dropdown. Reuses the modal shell, the cand-qa styling, and the rejected
  // notes API helpers (rejLoadNotes / rejAddNote with the same element ids).
  window.openRejectedModal = function(r){
    if (!r) return;
    const rid = esc(r.response_id);
    const av = document.getElementById('cm-avatar');
    if (av){ av.style.background = 'linear-gradient(135deg,#7f1d1d,#ef4444)'; av.textContent = _rejInitials(r.name); }
    const nm = document.getElementById('cm-name');
    if (nm) nm.textContent = r.name || 'Anonymous';
    const when = r.rejected_at ? new Date(r.rejected_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '—';
    const meta = document.getElementById('cm-meta');
    if (meta) meta.textContent = `Rejected  ·  ${r.rejected_round || '—'}  ·  ${r.role || 'Role not specified'}  ·  ${when}`;

    const body = document.getElementById('cm-body');
    body.innerHTML = '';

    const idItems = [
      { q:'Full Name',      a:r.name },
      { q:'Email Address',  a:r.email },
      { q:'Phone Number',   a:r.phone },
      { q:'Role',           a:r.role, full:true },
    ];
    idItems.forEach(g=>{
      const div = document.createElement('div');
      div.className = 'cand-qa-item' + (g.full ? ' full' : '');
      const empty = !g.a && g.a !== 0;
      div.innerHTML = `<div class="cand-qa-q">${g.q}</div><div class="cand-qa-a${empty?' empty':''}">${empty?'Not provided':esc(String(g.a))}</div>`;
      body.appendChild(div);
    });

    // Rejection round + reason
    const rb = document.createElement('div');
    rb.className = 'cand-qa-item full';
    rb.innerHTML = `<div class="cand-qa-q">Rejection</div><div class="cand-qa-a">${esc(r.rejected_round||'—')}${r.rejected_reason?` — ${esc(r.rejected_reason)}`:''}</div>`;
    body.appendChild(rb);

    // Full form answers
    (r.answers||[]).forEach(a=>{
      const div = document.createElement('div');
      div.className = 'cand-qa-item full';
      const empty = !a.answer;
      div.innerHTML = `<div class="cand-qa-q">${esc(a.question)}</div><div class="cand-qa-a${empty?' empty':''}">${empty?'no answer':esc(a.answer)}</div>`;
      body.appendChild(div);
    });

    // Notes timeline (same /api/notes path, same ids the rej helpers expect)
    const notesWrap = document.createElement('div');
    notesWrap.className = 'cand-qa-item full';
    notesWrap.style.cssText = 'margin-top:8px;border-top:1px solid var(--border-dim);padding-top:18px';
    notesWrap.innerHTML = `
      <div class="cand-qa-q">Notes <span style="opacity:.55;font-weight:400">— personal remarks, any round</span></div>
      <div id="rejnotes-list-${rid}" style="margin:10px 0 14px"><div style="font-size:12px;color:var(--text-dim)">Loading notes…</div></div>
      <div style="display:flex;flex-direction:column;gap:8px">
        <input id="rejnote-stage-${rid}" type="text" placeholder="Stage / round (optional)" class="field-input" style="font-size:12px">
        <textarea id="rejnote-text-${rid}" rows="2" placeholder="Add a remark about this candidate…" class="field-input" style="font-size:12.5px;resize:vertical"></textarea>
        <div style="display:flex;justify-content:flex-end"><button class="btn-ghost rejnote-add" data-rid="${rid}">+ Add note</button></div>
      </div>`;
    body.appendChild(notesWrap);
    const addBtn = notesWrap.querySelector('.rejnote-add');
    if (addBtn) addBtn.addEventListener('click', ()=> rejAddNote(r.response_id));
    rejLoadNotes(r.response_id);

    document.getElementById('cand-modal').classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  // ── Notes helpers for the Rejected view (same /api/notes API) ──
  function rejLoadNotes(responseId){
    const list = document.getElementById('rejnotes-list-'+responseId);
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
            <button class="rejnote-del" data-rid="${esc(responseId)}" data-nid="${esc(n.id)}" title="Delete note" aria-label="Delete note" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:13px;flex-shrink:0">🗑</button>
          </div>
          <div style="font-size:10px;color:var(--text-dim);margin-top:5px;font-family:'JetBrains Mono',monospace">${esc(when)}</div>
        </div>`;
      }).join('');
      list.querySelectorAll('.rejnote-del').forEach(b=> b.addEventListener('click', ()=> rejDeleteNote(b.dataset.rid, b.dataset.nid)));
    }).catch(()=>{ list.innerHTML = '<div style="font-size:12px;color:#f87171">Could not load notes.</div>'; });
  }
  async function rejAddNote(responseId){
    const t = document.getElementById('rejnote-text-'+responseId);
    const s = document.getElementById('rejnote-stage-'+responseId);
    if (t && !validateRequired([{ input: t, message: 'Write a note first.' }])) return;
    const text = (t?.value||'').trim();
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text, stage:(s?.value||'').trim()})});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      if(t) t.value=''; if(s) s.value=''; toast('Note added','ok'); rejLoadNotes(responseId);
    }catch(e){ toast('Could not add note: '+e.message,'err'); }
  }
  async function rejDeleteNote(responseId, noteId){
    try{
      const res = await fetch(`/api/notes/${encodeURIComponent(responseId)}/${encodeURIComponent(noteId)}`,{method:'DELETE'});
      const d = await res.json().catch(()=>({})); if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Note deleted','ok'); rejLoadNotes(responseId);
    }catch(e){ toast('Could not delete note: '+e.message,'err'); }
  }

  window.restoreRejected = async function(responseId){
    try{
      const res = await fetch(`/api/rejected/${encodeURIComponent(responseId)}`, {method:'DELETE'});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      toast('Candidate restored to active pipeline', 'ok');
      loadRejectedPage();
      // Un-grey the row in the responses table if it's loaded
      if (window.refreshDecisionState) window.refreshDecisionState();
    }catch(e){ toast('Restore failed: '+e.message, 'err'); }
  };

  // Search within rejected list
  const search = document.getElementById('rej-search');
  if (search) search.addEventListener('input', e=>{
    const q = e.target.value.toLowerCase();
    if (!q){ renderRejected(_rows); return; }
    renderRejected(_rows.filter(r =>
      JSON.stringify([r.name,r.email,r.phone,r.role,r.rejected_round,r.rejected_reason]).toLowerCase().includes(q)));
  });

  // Load when the tab is opened (and refresh on revisit)
  document.querySelector('.sb-item[data-page="rejected"]')
    ?.addEventListener('click', loadRejectedPage);
})();
