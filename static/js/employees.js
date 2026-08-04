(function(){
  let _empFields=[], _emps=[], _empView='tiles', _empReveal=false, _empEditId=null, _empSENS=new Set();
  const SELECTS={ gender:['','Male','Female','Other'],
                  employee_status:['','Active','Inactive','On Notice Period','Exited','Absconding'],
                  id_card_given:['','Yes','No'] };
  const TEXTAREAS=new Set(['permanent_address','correspondence_address']);
  function eA(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function eT(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

  async function loadEmployees(){
    try{
      const r=await fetch('/api/employees'); const d=await r.json();
      _empFields=d.fields||[]; _emps=d.employees||[];
      _empSENS=new Set(_empFields.filter(f=>f.sensitive).map(f=>f.key));
      render();
    }catch(e){ document.getElementById('emp-meta').textContent='Could not load employees.'; }
  }
  function mask(key,val){
    if(!val) return '';
    if(!_empReveal && _empSENS.has(key)){ const s=String(val); return s.length<=4?'••••':'•••• '+s.slice(-4); }
    return val;
  }
  function filtered(){
    const q=(document.getElementById('emp-search').value||'').trim().toLowerCase();
    if(!q) return _emps;
    return _emps.filter(e=>['name','employee_id','email','email_official','designation','department']
      .some(k=>String(e[k]||'').toLowerCase().includes(q)));
  }
  function render(){
    const rows=filtered();
    document.getElementById('emp-meta').textContent =
      `${rows.length} of ${_emps.length} employees${_empReveal?' · sensitive fields visible':''}`;
    if(_empView==='tiles') renderTiles(rows); else renderSheet(rows);
    document.getElementById('emp-tiles').style.display = _empView==='tiles'?'grid':'none';
    document.getElementById('emp-sheet-wrap').style.display = _empView==='sheet'?'block':'none';
  }
  const HUES=['#1F2D59','#3b82f6','#ec4899','#10b981','#f59e0b','#06b6d4'];
  function avColor(s){let h=0;for(const c of String(s||'?'))h+=c.charCodeAt(0);return HUES[h%HUES.length];}
  function initials(n){const p=String(n||'').trim().split(/\s+/);return ((p[0]||'?')[0]+((p[1]||'')[0]||'')).toUpperCase()||'?';}
  function stClass(s){s=(s||'').toLowerCase();if(s.includes('active'))return 'ok';if(s.includes('notice'))return 'warn';if(s.includes('exit')||s.includes('abscond')||s.includes('inactive'))return 'bad';return '';}

  function renderTiles(rows){
    const box=document.getElementById('emp-tiles');
    if(!rows.length){ box.innerHTML='<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text-dim)">No employees yet. Add one, or import from a Google Form.</div>'; return; }
    box.innerHTML=rows.map(e=>{
      const col=avColor(e.name);
      return `<div class="emp-card" data-edit="${eA(e._id)}">
        <div class="emp-card-top">
          <div class="emp-av" style="background:linear-gradient(135deg,${col},${col}bb)">${eT(initials(e.name))}</div>
          <div class="emp-id-badge">${eT(e.employee_id)||'—'}</div>
        </div>
        <div class="emp-name">${eT(e.name)||'Unnamed'}</div>
        <div class="emp-desig">${eT(e.designation)||'—'}</div>
        <div class="emp-dept">${eT(e.department)||'—'}${e.division?' · '+eT(e.division):''}</div>
        <span class="emp-status ${stClass(e.employee_status)}">${eT(e.employee_status)||'—'}</span>
        <div class="emp-rows">
          <div><span>DOJ</span><b>${eT(e.doj)||'—'}</b></div>
          <div><span>Service</span><b>${eT(e.service_duration)||'—'}</b></div>
          <div><span>Contact</span><b>${eT(e.contact_no)||'—'}</b></div>
          <div><span>Email</span><b>${eT(e.email_official||e.email)||'—'}</b></div>
        </div>
        <div class="emp-card-actions">
          <button class="td-mini-btn" data-edit="${eA(e._id)}">Edit</button>
          <button class="td-mini-btn" data-del="${eA(e._id)}" style="border-color:rgba(248,113,113,.4);color:#f87171">✕</button>
        </div>
      </div>`;
    }).join('');
  }
  function renderSheet(rows){
    document.getElementById('emp-thead').innerHTML =
      _empFields.map(f=>`<th>${eT(f.label)}</th>`).join('')+'<th>Actions</th>';
    const tb=document.getElementById('emp-tbody');
    if(!rows.length){ tb.innerHTML=`<tr><td colspan="${_empFields.length+1}" style="text-align:center;padding:34px;color:var(--text-dim)">No employees yet.</td></tr>`; return; }
    tb.innerHTML=rows.map(e=>{
      const cells=_empFields.map(f=>`<td>${eT(mask(f.key,e[f.key]))||'<span style="opacity:.3">—</span>'}</td>`).join('');
      return `<tr>${cells}<td style="white-space:nowrap">
        <button class="td-mini-btn" data-edit="${eA(e._id)}">Edit</button>
        <button class="td-mini-btn" data-del="${eA(e._id)}" style="border-color:rgba(248,113,113,.4);color:#f87171">✕</button></td></tr>`;
    }).join('');
  }

  function buildForm(emp){
    const f=document.getElementById('emp-form'); f.innerHTML='';
    _empFields.forEach(fl=>{
      const wrap=document.createElement('div'); wrap.className='emp-fg'+(TEXTAREAS.has(fl.key)?' wide':'');
      const val=emp?(emp[fl.key]||''):'';
      const fid=`emp-f-${eA(fl.key)}`;
      let input;
      if(fl.derived){ input=`<input id="${fid}" class="field-input" value="${eA(val)}" disabled><span class="emp-hint">auto-calculated</span>`; }
      else if(SELECTS[fl.key]){ input=`<select id="${fid}" class="field-input" data-k="${fl.key}">`+SELECTS[fl.key].map(o=>`<option ${o===val?'selected':''}>${eT(o)}</option>`).join('')+`</select>`; }
      else if(TEXTAREAS.has(fl.key)){ input=`<textarea id="${fid}" class="field-input" data-k="${fl.key}" rows="2">${eT(val)}</textarea>`; }
      else { input=`<input id="${fid}" class="field-input" data-k="${fl.key}" value="${eA(val)}"${fl.key==='name'?' required':''}>`; }
      const reqMark = fl.key==='name' ? '<span class="req-mark">*</span>' : '';
      wrap.innerHTML=`<label class="field-label" for="${fid}">${eT(fl.label)}${reqMark}${fl.sensitive?' 🔒':''}</label>${input}`;
      f.appendChild(wrap);
    });
  }
  function openEdit(emp){
    _empEditId = emp?emp._id:null;
    document.getElementById('emp-form-title').textContent = emp?'Edit Employee':'Add Employee';
    document.getElementById('emp-delete-btn').style.display = emp?'inline-flex':'none';
    buildForm(emp);
    document.getElementById('emp-edit-dialog').classList.add('open');
  }
  function closeEdit(){ document.getElementById('emp-edit-dialog').classList.remove('open'); }
  async function saveEmp(){
    const nameEl = document.querySelector('#emp-form [data-k="name"]');
    if (nameEl && !validateRequired([{ input: nameEl, message: 'Enter the employee\'s name.' }])) return;
    const data={}; document.querySelectorAll('#emp-form [data-k]').forEach(el=>{ data[el.dataset.k]=el.value; });
    try{
      const url = _empEditId?`/api/employees/${encodeURIComponent(_empEditId)}`:'/api/employees';
      const r=await fetch(url,{method:_empEditId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data})});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      toast(_empEditId?'Employee updated':'Employee added','ok'); closeEdit(); loadEmployees();
    }catch(e){ toast('Save failed: '+e.message,'err'); }
  }
  let _empDelId=null;
  function confirmDelEmp(id){
    _empDelId=id;
    const emp=_emps.find(x=>x._id===id);
    const nm=emp&&emp.name?eT(emp.name):'this employee';
    document.getElementById('emp-del-msg').innerHTML=
      `This will permanently remove <b>${nm}</b> from the master record. This action cannot be undone.`;
    document.getElementById('emp-del-dialog').classList.add('open');
  }
  function closeDelConfirm(){ _empDelId=null; document.getElementById('emp-del-dialog').classList.remove('open'); }
  async function delEmp(id){
    try{
      const r=await fetch(`/api/employees/${encodeURIComponent(id)}`,{method:'DELETE'});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      toast('Employee removed','inf'); closeEdit(); loadEmployees();
    }catch(e){ toast('Delete failed: '+e.message,'err'); }
  }

  // Import from Google Form
  async function importPreview(){
    const fid=document.getElementById('emp-form-id').value.trim();
    if(!fid){ toast('Paste a Google Form link or ID','err'); return; }
    const box=document.getElementById('emp-import-status'); box.style.display='block'; box.textContent='Reading form…';
    try{
      const r=await fetch('/api/employees/form/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({form_id:fid})});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      box.innerHTML=`<b>${eT(d.form_title)||'Form'}</b> · ${d.response_count} responses<br>
        Mapped fields: ${d.mapped_fields.map(eT).join(', ')||'none'}<br>
        ${d.unmapped_questions.length?`<span style="color:#fbbf24">Unmatched questions:</span> ${d.unmapped_questions.map(eT).join(', ')}`:''}`;
      document.getElementById('emp-import-go').style.display='inline-flex';
    }catch(e){ box.textContent='Error: '+e.message; document.getElementById('emp-import-go').style.display='none'; }
  }
  async function importGo(){
    const fid=document.getElementById('emp-form-id').value.trim();
    try{
      const r=await fetch('/api/employees/form/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({form_id:fid})});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      toast(`Imported ${d.imported} · skipped ${d.skipped_duplicate} duplicates`,'ok');
      document.getElementById('emp-import-dialog').classList.remove('open');
      document.getElementById('emp-import-go').style.display='none';
      document.getElementById('emp-import-status').style.display='none';
      document.getElementById('emp-form-id').value='';
      loadEmployees();
    }catch(e){ toast('Import failed: '+e.message,'err'); }
  }

  // Import from public Google Sheet (CSV)
  async function csvPreview(){
    const url=document.getElementById('emp-csv-url').value.trim();
    if(!url){ toast('Paste a Google Sheet link','err'); return; }
    const box=document.getElementById('emp-csv-status'); box.style.display='block'; box.textContent='Reading sheet…';
    document.getElementById('emp-csv-go').style.display='none';
    try{
      const r=await fetch('/api/employees/sheet-csv/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet_url:url})});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      box.innerHTML=`<b>${d.total_rows} rows</b> found · <span style="color:#34d399">${d.new} new</span>`
        + (d.duplicates?` · <span style="color:#fbbf24">${d.duplicates} already exist</span>`:'')
        + `<br>Mapped: ${d.mapped_fields.map(eT).join(', ')||'none'}`
        + (d.unmapped_columns.length?`<br><span style="color:#fbbf24">Ignored columns:</span> ${d.unmapped_columns.map(eT).join(', ')}`:'')
        + (d.sample&&d.sample.length?`<br><span style="opacity:.7">e.g. ${d.sample.map(s=>eT(s.name||s.email||'—')).join(', ')}</span>`:'');
      if(d.new>0) document.getElementById('emp-csv-go').style.display='inline-flex';
    }catch(e){ box.textContent='Error: '+e.message; document.getElementById('emp-csv-go').style.display='none'; }
  }
  async function csvGo(){
    const url=document.getElementById('emp-csv-url').value.trim();
    try{
      const r=await fetch('/api/employees/sheet-csv/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet_url:url})});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      toast(`Imported ${d.imported} · skipped ${d.skipped_duplicate} duplicates`,'ok');
      document.getElementById('emp-csv-dialog').classList.remove('open');
      document.getElementById('emp-csv-go').style.display='none';
      document.getElementById('emp-csv-status').style.display='none';
      document.getElementById('emp-csv-url').value='';
      loadEmployees();
    }catch(e){ toast('Import failed: '+e.message,'err'); }
  }

  // Google Sheet backup
  async function sheetLoad(){
    try{
      const r=await fetch('/api/employees/sheet/settings'); const d=await r.json();
      document.getElementById('emp-sheet-id').value=d.spreadsheet_id||'';
      document.getElementById('emp-sheet-ws').value=d.worksheet||'Master';
      const box=document.getElementById('emp-sheet-status'); box.style.display='block';
      box.innerHTML=`Service account: <b>${eT(d.service_account_email)||'not found'}</b> — share the sheet with it as Editor.`+
        (d.last_sync?`<br>Last backup: ${eT(d.last_sync)}`:'')+
        (d.last_error?`<br><span style="color:#f87171">Last error: ${eT(d.last_error)}</span>`:'');
    }catch(e){}
  }
  async function sheetTest(){
    const box=document.getElementById('emp-sheet-status'); box.style.display='block'; box.textContent='Saving & testing…';
    try{
      await fetch('/api/employees/sheet/settings',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({spreadsheet:document.getElementById('emp-sheet-id').value,worksheet:document.getElementById('emp-sheet-ws').value})});
      const r=await fetch('/api/employees/sheet/test',{method:'POST'});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      box.innerHTML=`Connected to <b>${eT(d.title)}</b><br>Tabs: ${d.worksheets.map(eT).join(', ')}<br>Service account: ${eT(d.service_account_email)}`;
      toast('Sheet connected','ok');
    }catch(e){ box.textContent='Error: '+e.message; }
  }
  async function sheetSync(){
    try{
      const r=await fetch('/api/employees/sheet/sync',{method:'POST'});
      const d=await r.json(); if(!r.ok) throw new Error(errDetail(d, r.status));
      toast(`Backed up ${d.rows} rows to ${d.worksheet}`,'ok'); sheetLoad();
    }catch(e){ toast('Sync failed: '+e.message,'err'); }
  }

  // wiring
  document.getElementById('emp-add-btn').addEventListener('click',()=>openEdit(null));
  document.getElementById('emp-cancel-btn').addEventListener('click',closeEdit);
  document.getElementById('emp-save-btn').addEventListener('click',saveEmp);
  document.getElementById('emp-delete-btn').addEventListener('click',()=>{ if(_empEditId) confirmDelEmp(_empEditId); });
  document.getElementById('emp-del-cancel').addEventListener('click',closeDelConfirm);
  document.getElementById('emp-del-confirm').addEventListener('click',()=>{ const id=_empDelId; closeDelConfirm(); if(id) delEmp(id); });
  document.getElementById('emp-import-btn').addEventListener('click',()=>document.getElementById('emp-import-dialog').classList.add('open'));
  document.getElementById('emp-import-cancel').addEventListener('click',()=>document.getElementById('emp-import-dialog').classList.remove('open'));
  document.getElementById('emp-import-preview').addEventListener('click',importPreview);
  document.getElementById('emp-import-go').addEventListener('click',importGo);
  document.getElementById('emp-csv-btn').addEventListener('click',()=>document.getElementById('emp-csv-dialog').classList.add('open'));
  document.getElementById('emp-csv-cancel').addEventListener('click',()=>document.getElementById('emp-csv-dialog').classList.remove('open'));
  document.getElementById('emp-csv-preview').addEventListener('click',csvPreview);
  document.getElementById('emp-csv-go').addEventListener('click',csvGo);
  document.getElementById('emp-sheet-btn').addEventListener('click',()=>{document.getElementById('emp-sheet-dialog').classList.add('open');sheetLoad();});
  document.getElementById('emp-sheet-cancel').addEventListener('click',()=>document.getElementById('emp-sheet-dialog').classList.remove('open'));
  document.getElementById('emp-sheet-test').addEventListener('click',sheetTest);
  document.getElementById('emp-sheet-sync').addEventListener('click',sheetSync);
  document.getElementById('emp-reveal-btn').addEventListener('click',function(){ _empReveal=!_empReveal; this.textContent=_empReveal?'🔓 Hide sensitive':'🔒 Reveal sensitive'; render(); });
  document.getElementById('emp-search').addEventListener('input',render);
  document.querySelectorAll('.emp-vt').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.emp-vt').forEach(x=>x.classList.remove('active'));
    b.classList.add('active'); _empView=b.dataset.view; render();
  }));
  document.querySelectorAll('.emp-overlay').forEach(ov=>ov.addEventListener('click',e=>{ if(e.target===ov) ov.classList.remove('open'); }));
  function findId(t,attr){ const el=t.closest(`[data-${attr}]`); return el?el.getAttribute('data-'+attr):null; }
  document.getElementById('emp-tiles').addEventListener('click',e=>{
    const del=findId(e.target,'del'); if(del){ e.stopPropagation(); confirmDelEmp(del); return; }
    const ed=findId(e.target,'edit'); if(ed){ const emp=_emps.find(x=>x._id===ed); if(emp) openEdit(emp); }
  });
  document.getElementById('emp-tbody').addEventListener('click',e=>{
    const del=findId(e.target,'del'); if(del){ confirmDelEmp(del); return; }
    const ed=findId(e.target,'edit'); if(ed){ const emp=_emps.find(x=>x._id===ed); if(emp) openEdit(emp); }
  });
  const empNav=document.querySelector('.sb-item[data-page="employees"]');
  if(empNav) empNav.addEventListener('click',loadEmployees);
})();
