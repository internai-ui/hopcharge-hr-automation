/* ───────── COLLEGE OUTREACH ───────── */
(function () {
  // Guard: only initialize if the colleges page exists
  if (!document.getElementById('page-colleges')) return;

  const STATUSES = ["Not Contacted","Email Sent","Awaiting Response","Interested",
    "Need More Information","Call Scheduled","Partnership Discussion","Active Partner","Not Interested"];
  const TYPES = ["IIT","NIT","IIIT","Government","Private","Other"];
  const api = (p,o) => fetch('/api/colleges'+p, o).then(async r => { const j = await r.json(); if(!r.ok) throw new Error(errDetail(j, r.status)); return j; });
  const slug = s => 'st-' + s.toLowerCase().replace(/\s+/g,'-');
  const $ = id => document.getElementById(id);

  let _colleges = [];
  let _loaded = false;

  // Populate filter dropdowns once (safe: elements are guaranteed to exist by guard above)
  try {
    STATUSES.forEach(s => $('col-filter-status').insertAdjacentHTML('beforeend', `<option value="${s}">${s}</option>`));
    TYPES.forEach(t => $('col-filter-type').insertAdjacentHTML('beforeend', `<option value="${t}">${t}</option>`));
  } catch(e) {
    console.error('College module init error:', e);
    return;
  }

  const countUp = (id,v) => { const el=$(id),t0=performance.now(); (function s(n){const p=Math.min((n-t0)/600,1);el.textContent=Math.round(v*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(s);})(performance.now()); };

  async function loadDashboard() {
    try {
      const d = await api('/stats/dashboard');
      countUp('cs-total', d.cards.total_colleges);
      countUp('cs-contacted', d.cards.colleges_contacted);
      countUp('cs-awaiting', d.cards.awaiting_response);
      countUp('cs-interested', d.cards.interested);
      countUp('cs-active', d.cards.active_partners);

      // Funnel
      const maxF = Math.max(1, ...d.funnel.map(f => f.count));
      $('col-funnel').innerHTML = d.funnel.map(f => `
        <div class="col-funnel-row">
          <div class="col-funnel-label">${f.stage}</div>
          <div class="col-funnel-track"><div class="col-funnel-fill" style="width:${f.count/maxF*100}%"></div></div>
          <div class="col-funnel-count">${f.count}</div>
        </div>`).join('');

      // Priority bars
      const maxP = Math.max(1, ...d.by_priority.map(p => p.count));
      $('col-priority-bars').innerHTML = d.by_priority.map(p => `
        <div class="col-prio-row">
          <div class="col-prio-label ${p.level}">${p.level}</div>
          <div class="col-prio-track"><div class="col-prio-fill ${p.level}" style="width:${p.count/maxP*100}%"></div></div>
          <div class="col-prio-count">${p.count}</div>
        </div>`).join('');

      // Type chips
      $('col-type-chips').innerHTML = d.by_type.filter(t=>t.count>0).map(t =>
        `<span class="col-type-chip">${t.type}<b>${t.count}</b></span>`).join('') || '<span class="col-type-chip">No data</span>';
    } catch(e) { /* dashboard is best-effort */ }
  }

  function renderTable() {
    const tbody = $('col-tbody');
    const q = $('col-search').value.toLowerCase();
    const fs = $('col-filter-status').value;
    const ft = $('col-filter-type').value;
    const filtersActive = !!(q || fs || ft);

    let rows = _colleges.filter(c => {
      if (fs && c.outreach_status !== fs) return false;
      if (ft && c.college_type !== ft) return false;
      if (q && !(`${c.college_name} ${c.placement_officer_name} ${c.city}`.toLowerCase().includes(q))) return false;
      return true;
    });

    $('col-thead').innerHTML = `<th>College</th><th>Type</th><th>Officer</th><th>Email</th><th>City</th><th>Status</th><th>Priority</th><th></th>`;

    // Distinguish "no colleges at all" from "filters hid them all" so a leftover
    // search never looks like data loss.
    const emptyEl = $('col-empty');
    if (rows.length === 0 && _colleges.length > 0 && filtersActive) {
      emptyEl.classList.add('hidden');
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:30px;color:var(--text-dim)">
        No colleges match your filters — but you still have <b style="color:var(--text-mid)">${_colleges.length}</b> saved.
        <button class="td-view-btn" style="margin-left:10px" onclick="clearCollegeFilters()">Clear filters</button>
      </td></tr>`;
      $('col-info').textContent = `${rows.length} of ${_colleges.length} shown`;
      return;
    }
    emptyEl.classList.toggle('hidden', rows.length>0);

    tbody.innerHTML = rows.map((c,i) => `
      <tr style="animation-delay:${i*25}ms">
        <td><div class="cand-name">${c.college_name}</div>${c.state?`<div class="cand-role">${c.state}</div>`:''}</td>
        <td><span class="col-type-tag">${c.college_type}</span></td>
        <td style="font-size:12px">${c.placement_officer_name||'<span style="opacity:.35">—</span>'}</td>
        <td class="td-email">${c.email||'<span style="opacity:.35">—</span>'}</td>
        <td style="font-size:12px">${c.city||'<span style="opacity:.35">—</span>'}</td>
        <td><span class="col-status-pill ${slug(c.outreach_status)}" onclick="event.stopPropagation();openStatusMenu(event,'${c.id}')">${c.outreach_status} ▾</span></td>
        <td><span class="col-prio-badge ${c.priority_level}">${c.priority_score??'—'}</span> <span style="font-size:10px;color:var(--text-dim)">${c.priority_level||''}</span></td>
        <td style="white-space:nowrap">
          <button class="td-view-btn" onclick="editCollege('${c.id}')">EDIT</button>
          <button class="td-del-btn" title="Delete" aria-label="Delete" onclick="delCollege(this,'${c.id}')">🗑</button>
        </td>
      </tr>`).join('');
    if (filtersActive && _colleges.length)
      $('col-info').textContent = `${rows.length} of ${_colleges.length} shown`;
  }

  window.clearCollegeFilters = function(){
    $('col-search').value = '';
    $('col-filter-status').value = '';
    $('col-filter-type').value = '';
    renderTable();
    if (_colleges.length) $('col-info').textContent = `${_colleges.length} college${_colleges.length!==1?'s':''}`;
  };

  async function loadColleges() {
    try {
      $('col-info').textContent = 'Loading…';
      const sort = $('col-sort').value;
      const d = await api(`?sort_by=${sort}&order=${sort==='college_name'?'asc':'desc'}&limit=1000`);
      _colleges = d.colleges || [];
      _loaded = true;
      renderTable();
      $('col-info').textContent = `${d.total} college${d.total!==1?'s':''}`;
      // Dashboard stats are secondary — never let them break the table render.
      try { await loadDashboard(); } catch(e){ console.warn('dashboard stats failed:', e); }
    } catch(e) {
      $('col-info').textContent = 'Error loading colleges: '+e.message;
      console.error('loadColleges failed:', e);
    }
  }

  // ── Status menu ──
  let _statusTargetId = null;
  window.openStatusMenu = function(ev, id) {
    _statusTargetId = id;
    const menu = $('col-status-menu');
    menu.innerHTML = STATUSES.map(s => `<div class="col-status-opt" onclick="setColStatus('${s}')">${s}</div>`).join('');
    menu.classList.add('open');

    const r = ev.target.getBoundingClientRect();
    // Measure the menu now that it's rendered
    const menuH = menu.offsetHeight;
    const menuW = menu.offsetWidth || 200;
    const gap = 6;
    const spaceBelow = window.innerHeight - r.bottom;
    const spaceAbove = r.top;

    // Horizontal: keep within viewport
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menuW - 8)) + 'px';

    // Vertical: open downward if it fits, otherwise upward; clamp to viewport
    if (spaceBelow >= menuH + gap || spaceBelow >= spaceAbove) {
      menu.style.top = Math.min(r.bottom + gap, window.innerHeight - menuH - 8) + 'px';
    } else {
      menu.style.top = Math.max(8, r.top - menuH - gap) + 'px';
    }
  };
  window.setColStatus = async function(status) {
    $('col-status-menu').classList.remove('open');
    try {
      await api(`/${_statusTargetId}/status`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outreach_status:status})});
      toast(`Status → ${status}`, 'ok');
      loadColleges();
    } catch(e) { toast('Failed: '+e.message,'err'); }
  };
  document.addEventListener('click', e => {
    if (!e.target.closest('#col-status-menu') && !e.target.closest('.col-status-pill'))
      $('col-status-menu').classList.remove('open');
  });
  // Close the menu if the user scrolls (fixed menu would otherwise detach from its pill)
  window.addEventListener('scroll', () => $('col-status-menu').classList.remove('open'), true);

  // ── Add / Edit modal ──
  let _editId = null;
  const F = ['name','type','officer','designation','email','phone','city','state','website','ppurl','intake','interns','quality','engagement'];
  function clearForm(){ F.forEach(f=>{ const el=$('cf-'+f); if(el) el.value = (f==='type'?'Private':''); }); $('col-modal-info').textContent=''; }
  window.openColModal = function(){ _editId=null; $('col-modal-title').textContent='Add College'; clearForm(); $('col-modal').classList.add('open'); document.body.style.overflow='hidden'; };
  window.closeColModal = function(){ $('col-modal').classList.remove('open'); document.body.style.overflow=''; };
  window.editCollege = function(id){
    const c = _colleges.find(x=>x.id===id); if(!c) return;
    _editId = id; $('col-modal-title').textContent='Edit College';
    $('cf-name').value=c.college_name; $('cf-type').value=c.college_type;
    $('cf-officer').value=c.placement_officer_name; $('cf-designation').value=c.designation;
    $('cf-email').value=c.email; $('cf-phone').value=c.phone; $('cf-city').value=c.city;
    $('cf-state').value=c.state; $('cf-website').value=c.website; $('cf-ppurl').value=c.placement_page_url;
    $('cf-intake').value=c.engineering_intake??''; $('cf-interns').value=c.internship_opportunities??'';
    $('cf-quality').value=c.placement_quality_score??''; $('cf-engagement').value=c.historical_engagement??0;
    $('col-modal-info').textContent=''; $('col-modal').classList.add('open'); document.body.style.overflow='hidden';
  };

  function formPayload(){
    const num = v => v===''?null:parseInt(v,10);
    return {
      college_name:$('cf-name').value.trim(), college_type:$('cf-type').value,
      placement_officer_name:$('cf-officer').value.trim(), designation:$('cf-designation').value.trim(),
      email:$('cf-email').value.trim(), phone:$('cf-phone').value.trim(),
      city:$('cf-city').value.trim(), state:$('cf-state').value.trim(),
      website:$('cf-website').value.trim(), placement_page_url:$('cf-ppurl').value.trim(),
      engineering_intake:num($('cf-intake').value), internship_opportunities:num($('cf-interns').value),
      placement_quality_score:num($('cf-quality').value), historical_engagement:num($('cf-engagement').value)||0,
    };
  }

  $('col-save-btn').addEventListener('click', async () => {
    if (!validateRequired([{ input: $('cf-name'), message: 'Enter the college name.' }])) return;
    const payload = formPayload();
    $('col-save-btn').disabled = true;
    try {
      if (_editId) await api(`/${_editId}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      else await api('', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      toast(_editId?'College updated':'College added','ok');
      closeColModal(); loadColleges();
    } catch(e) { $('col-modal-info').textContent = e.message; }
    finally { $('col-save-btn').disabled = false; }
  });

  window.delCollege = async function(btn, id){
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming'); btn.textContent='✕';
      setTimeout(()=>{ btn.classList.remove('confirming'); btn.textContent='🗑'; }, 3000);
      return;
    }
    try { await api(`/${id}`, {method:'DELETE'}); toast('College removed','inf'); loadColleges(); }
    catch(e){ toast('Delete failed: '+e.message,'err'); }
  };

  // ── Import ──
  $('col-import-file').addEventListener('change', async e => {
    const file = e.target.files[0]; if(!file) return;
    const fd = new FormData(); fd.append('file', file);
    $('col-info').textContent = 'Importing…';
    document.getElementById('progress-rail')?.classList.add('on');
    try {
      const res = await fetch('/api/colleges/import?on_duplicate=update', {method:'POST', body:fd});
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, 'Import failed'));
      toast(`Imported: ${d.created} new, ${d.updated} updated, ${d.skipped} skipped`, 'ok');
      loadColleges();
    } catch(err){ toast('Import error: '+err.message,'err'); $('col-info').textContent=err.message; }
    finally { document.getElementById('progress-rail')?.classList.remove('on'); e.target.value=''; }
  });

  // ── Re-score ──
  $('col-rescore-btn').addEventListener('click', async () => {
    try { const d = await api('/rescore',{method:'POST'}); toast(`Re-scored ${d.rescored} colleges`,'ok'); loadColleges(); }
    catch(e){ toast('Re-score failed: '+e.message,'err'); }
  });

  // Wiring
  $('col-add-btn').addEventListener('click', openColModal);

  // ── Discovery modal ──────────────────────────────────────
  function escD(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  let _discWebsite = '';

  window.closeDiscoverModal = function(){ $('discover-modal').classList.remove('open'); };

  $('col-discover-btn').addEventListener('click', ()=>{
    $('disc-name').value=''; $('disc-site').value='';
    $('disc-status').textContent=''; $('disc-results').innerHTML='';
    $('discover-modal').classList.add('open');
    setTimeout(()=>$('disc-name').focus(), 50);
  });

  $('disc-run-btn').addEventListener('click', runDiscovery);
  $('disc-name').addEventListener('keydown', e=>{ if(e.key==='Enter') runDiscovery(); });
  $('disc-site').addEventListener('keydown', e=>{ if(e.key==='Enter') runDiscovery(); });

  async function runDiscovery(){
    const site = $('disc-site').value.trim();
    if (!validateRequired([{ input: $('disc-name'), message: 'Enter a college name.' }])) return;
    const name = $('disc-name').value.trim();
    const btn = $('disc-run-btn'); btn.disabled=true; const o=btn.textContent; btn.textContent='Searching…';
    $('disc-status').textContent='Checking placement pages… usually takes 5–20 seconds. Results appear when ready.';
    $('disc-results').innerHTML='';
    try{
      const r = await fetch('/api/colleges/discover', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({college_name:name, website:site})});
      const d = await r.json();
      if(!r.ok) throw new Error(errDetail(d, r.status));
      renderDiscovery(d.result, d.linkedin_search, d.google_tpo_search, d.website_guesses, name);
    }catch(e){ $('disc-status').textContent='Error: '+e.message; }
    finally{ btn.disabled=false; btn.textContent=o; }
  }

  function renderDiscovery(res, linkedin, googleSearch, websiteGuesses, name){
    _discWebsite = res.website || '';
    const notes = (res.method_notes||[]).join(' · ');
    let head = '';
    if (res.website) head += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:4px">Website: <a href="${escD(res.website)}" target="_blank" style="color:#5fd3c8">${escD(res.website)}</a></div>`;
    if (res.brochure_url) head += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:4px">Brochure: <a href="${escD(res.brochure_url)}" target="_blank" style="color:#5fd3c8">PDF found ↗</a></div>`;
    if (res.error) head += `<div style="font-size:12.5px;color:#f0a;margin-bottom:8px">${escD(res.error)}</div>`;
    $('disc-status').innerHTML = notes ? `<span style="opacity:.7;font-size:11.5px">${escD(notes)}</span>` : '';

    const cands = res.candidates||[];
    let html = head;
    if (!cands.length){
      html += `<div style="padding:14px;background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.3);border-radius:8px;font-size:12.5px;color:var(--text-mid);line-height:1.6;margin-bottom:10px">
        <b style="color:#fbbf24">No contacts found automatically.</b> This usually means the site protects emails, uses JavaScript rendering, or the placement page has a non-standard URL.<br>
        Try adding the correct website URL above and retrying, or use the manual search links below.
      </div>`;
      // Show website guesses if no site was provided
      if (websiteGuesses && websiteGuesses.length && !res.website){
        html += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px">Possible websites to try:</div>`;
        websiteGuesses.forEach(g=>{ html+=`<button class="btn-ghost" style="font-size:11px;margin:3px" onclick="document.getElementById('disc-site').value='${escD(g)}';runDiscovery()">${escD(g)}</button>`; });
        html += '<br style="margin:8px 0">';
      }
    } else {
      html += `<div style="font-size:11px;letter-spacing:.5px;color:var(--text-dim);margin:12px 0 8px">${cands.length} CANDIDATE${cands.length!==1?'S':''} — REVIEW & SAVE</div>`;
      cands.forEach((c,i)=>{
        const conf = c.confidence||0;
        const cc = conf>=90?'#34d399':conf>=75?'#fbbf24':'#f0a05a';
        html += `<div style="border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:12px 14px;margin-bottom:10px;background:rgba(255,255,255,.02)">
          <div style="display:flex;justify-content:space-between;align-items:start;gap:12px">
            <div style="flex:1">
              <div style="font-size:14px;font-weight:600;color:var(--text-bright,#e8e6f0)">${escD(c.name)||'<span style="opacity:.5">Name not found</span>'}</div>
              <div style="font-size:12px;color:#c4b5fd;margin:2px 0">${escD(c.designation)||'<span style="opacity:.4">Designation unknown</span>'}</div>
              <div style="font-size:12px;color:var(--text-mid);font-family:'JetBrains Mono',monospace">${escD(c.email)||'<span style="opacity:.5">no email</span>'}${c.phone?'  ·  '+escD(c.phone):''}</div>
              <div style="font-size:10.5px;color:var(--text-dim);margin-top:5px">${escD(c.source_type)} · <span style="color:${cc}">confidence ${conf}/100</span>${c.source_url?' · <a href="'+escD(c.source_url)+'" target="_blank" style="color:#5fd3c8">source ↗</a>':''}</div>
            </div>
            <button class="btn-parse" style="white-space:nowrap;padding:7px 14px" onclick='saveDiscovered(${i})'>Save</button>
          </div>
        </div>`;
      });
    }

    // Always show manual search helpers at the bottom
    html += `<div style="margin-top:14px;padding:12px 14px;border:1px solid rgba(255,255,255,.08);border-radius:8px;font-size:12px;color:var(--text-dim);line-height:1.8">
      <b style="color:var(--text-mid)">Manual search links</b><br>
      <a href="${escD(linkedin)}" target="_blank" style="color:#5fd3c8">LinkedIn TPO search ↗</a>
      &nbsp;·&nbsp;
      <a href="${escD(googleSearch)}" target="_blank" style="color:#5fd3c8">Google TPO+email search ↗</a>
    </div>`;

    $('disc-results').innerHTML = html;
    window._discCands = cands;
    window._discName = name;
  }

  window.saveDiscovered = async function(i){
    const c = (window._discCands||[])[i]; if(!c) return;
    try{
      const r = await fetch('/api/colleges/discover/accept', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          college_name: window._discName, contact_name:c.name, designation:c.designation,
          email:c.email, phone:c.phone, website:_discWebsite, placement_page_url:c.source_url,
          source_type:c.source_type, source_url:c.source_url, confidence_score:c.confidence})});
      const d = await r.json();
      if(!r.ok) throw new Error(errDetail(d, 'Save failed'));
      toast(`Saved ${c.name||'contact'} → ${window._discName} (${d.action})`, 'ok');
      if (_loaded) loadColleges();
    }catch(e){ toast('Save failed: '+e.message, 'err'); }
  };

  ['col-search','col-filter-status','col-filter-type','col-sort'].forEach(id =>
    $(id).addEventListener('input', () => _loaded && (id==='col-sort' ? loadColleges() : renderTable())));

  // Lazy-load when the College Outreach tab is first opened
  document.querySelector('.sb-item[data-page="colleges"]')
    ?.addEventListener('click', () => { if (!_loaded) loadColleges(); });
})();
