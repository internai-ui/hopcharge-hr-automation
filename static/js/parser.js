/* ───────── FILE HANDLING ───────── */
let files = [];
const dropZone=document.getElementById('drop-zone'), fileInput=document.getElementById('file-input'),
      fileChips=document.getElementById('file-chips'), parseBtn=document.getElementById('parse-btn'), clearBtn=document.getElementById('clear-btn');
function addFiles(list) {
  const pdfs = Array.from(list).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (pdfs.length < list.length) toast('Non-PDF files ignored', 'inf');
  pdfs.forEach(f => { if (!files.find(x => x.name===f.name && x.size===f.size)) files.push(f); });
  renderChips();
}
function renderChips() {
  fileChips.innerHTML = '';
  files.forEach((f, i) => {
    const ch = document.createElement('div'); ch.className='chip'; ch.style.animationDelay=`${i*35}ms`;
    const short = f.name.length > 30 ? f.name.slice(0,27)+'…' : f.name;
    ch.innerHTML = `<span class="chip-bolt">⚡</span><span>${short}</span><button type="button" class="chip-x" data-i="${i}" aria-label="Remove ${short}">×</button>`;
    ch.querySelector('.chip-x').addEventListener('click', () => { files.splice(i,1); renderChips(); });
    fileChips.appendChild(ch);
  });
  parseBtn.disabled = files.length === 0;
  dropZone.classList.toggle('has-files', files.length > 0);
}
fileInput.addEventListener('change', () => addFiles(fileInput.files));
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', e => { if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('drag-over'); });
dropZone.addEventListener('drop',      e => { e.preventDefault(); dropZone.classList.remove('drag-over'); addFiles(e.dataTransfer.files); });
clearBtn.addEventListener('click', () => {
  files=[]; candidates=[]; renderChips();
  document.getElementById('results-section').classList.add('hidden');
  document.getElementById('stats-row').classList.add('hidden');
  refreshEmailCount(); toast('Workspace cleared', 'inf');
});

/* ───────── PARSE ───────── */
let candidates = [];
const rail = document.getElementById('progress-rail');
parseBtn.addEventListener('click', async () => {
  if (!files.length) return;
  rail.classList.add('on'); parseBtn.disabled = true;
  parseBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="animation:spin 0.9s linear infinite;flex-shrink:0"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="20 8" stroke-linecap="round"/></svg> Processing…`;
  const fd = new FormData(); files.forEach(f => fd.append('files', f));
  try {
    const res = await fetch('/api/parse', { method:'POST', body:fd });
    if (!res.ok) { const err = await res.json().catch(()=>({detail:'Server error'})); throw new Error(errDetail(err, res.status)); }
    const data = await res.json(); candidates = data.candidates || [];
    if (candidates.length) { toast(`⚡ ${candidates.length} candidate${candidates.length>1?'s':''} extracted`, 'ok'); renderResults(candidates); }
    else { toast('No data extracted - check your PDFs', 'err'); }
    (data.errors || []).forEach(e => toast(`${e.file}: ${e.error}`, 'err'));
    refreshEmailCount();
  } catch (err) { toast(`Error: ${err.message}`, 'err'); }
  finally { rail.classList.remove('on'); parseBtn.disabled = files.length === 0; parseBtn.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><polygon points="13,2 5,13 11,13 9,22 19,11 13,11" fill="currentColor"/></svg> Parse CVs`; }
});

/* ───────── RENDER RESULTS ───────── */
function avgConf(c){ const v=Object.values(c.field_confidence||{}).filter(x=>typeof x==='number'); return v.length?v.reduce((a,b)=>a+b,0)/v.length:0; }
function initials(name){ if(!name)return'?'; const w=name.trim().split(/\s+/); return w.length>=2?(w[0][0]+w[w.length-1][0]).toUpperCase():name.slice(0,2).toUpperCase(); }
function countUp(id,target){ const el=document.getElementById(id); const t0=performance.now(); (function step(now){ const p=Math.min((now-t0)/750,1); el.textContent=Math.round(target*(1-Math.pow(1-p,3))); if(p<1)requestAnimationFrame(step); })(performance.now()); }
function renderResults(data){
  document.getElementById('results-section').classList.remove('hidden');
  document.getElementById('stats-row').classList.remove('hidden');
  const totalSkills=data.reduce((s,c)=>s+(c.skills?.length||0),0), withExp=data.filter(c=>c.work_experience?.length>0).length, avgC=data.reduce((s,c)=>s+avgConf(c),0)/data.length;
  countUp('s-cands',data.length); countUp('s-exp',withExp); countUp('s-skills',totalSkills);
  document.getElementById('s-conf').textContent=`${Math.round(avgC*100)}%`;
  document.getElementById('export-ts').textContent=`Parsed at ${new Date().toLocaleTimeString()}`;
  renderCards(data); renderTable(data);
}
function renderCards(data){
  const grid=document.getElementById('cards-grid'); grid.innerHTML='';
  if(!data.length){ grid.innerHTML=`<div style="text-align:center;padding:60px 0;color:var(--text-dim);font-family:'Poppins',sans-serif;letter-spacing:2px;font-size:13px">NO MATCHING CANDIDATES</div>`; return; }
  data.forEach((c,i)=>{
    const skills=c.skills||[], shown=skills.slice(0,4), extra=skills.length-shown.length, conf=avgConf(c);
    const el=document.createElement('div'); el.className='ccard'; el.style.animationDelay=`${i*55}ms`;
    el.innerHTML=`
      <div class="ccard-head">
        <div class="avatar">${initials(c.full_name)}</div>
        <div class="name-block">
          <div class="cname">${c.full_name||'<em style="opacity:.35;font-style:normal">-</em>'}</div>
          <div class="cloc"><svg width="9" height="9" viewBox="0 0 9 9" fill="none"><circle cx="4.5" cy="3.5" r="1.5" stroke="currentColor" stroke-width="0.9"/><path d="M4.5 8S2 6 2 3.5a2.5 2.5 0 015 0C7 6 4.5 8 4.5 8z" stroke="currentColor" fill="none" stroke-width="0.9"/></svg>${c.location_city||'-'}</div>
        </div>
        ${conf>0?`<div class="conf-badge">${Math.round(conf*100)}%</div>`:''}
      </div>
      <div class="ccontact">
        ${c.phone_number?`<div class="crow"><span class="ic">⊛</span><span class="td-mono">${c.phone_number}</span></div>`:''}
        ${c.email?`<div class="crow"><span class="ic">◉</span><span>${c.email}</span></div>`:''}
        ${c.linkedin_profile?`<div class="crow"><span class="ic">⧉</span><span>${c.linkedin_profile}</span></div>`:''}
      </div>
      ${shown.length?`<div class="ctags">${shown.map(s=>`<span class="stag">${s}</span>`).join('')}${extra>0?`<span class="stag-more">+${extra}</span>`:''}</div>`:''}
      <div class="cfoot">
        <div class="cmeta"><div class="cmeta-item"><strong>${c.work_experience?.length||0}</strong> jobs</div><div class="cmeta-item"><strong>${c.education?.length||0}</strong> edu</div><div class="cmeta-item"><strong>${skills.length}</strong> skills</div></div>
        <button class="btn-view" data-i="${i}">View →</button>
      </div>`;
    el.querySelector('.btn-view').addEventListener('click',()=>openModal(i));
    grid.appendChild(el);
  });
}
function renderTable(data){
  const tb=document.getElementById('tbl-body'); tb.innerHTML='';
  data.forEach((c,i)=>{
    const tr=document.createElement('tr'); tr.style.animationDelay=`${i*28}ms`;
    tr.innerHTML=`
      <td class="td-mono" style="color:var(--text-dim)">${i+1}</td>
      <td class="td-name">${c.full_name||'-'}</td>
      <td class="td-mono">${c.phone_number||'-'}</td>
      <td class="td-mono" style="font-size:10px">${c.email||'-'}</td>
      <td>${c.location_city||'-'}</td>
      <td><span style="font-family:'Poppins',sans-serif;font-size:11px;font-weight:700;color:var(--blue)">${c.skills?.length||0}</span></td>
      <td><span style="font-family:'Poppins',sans-serif;font-size:11px;font-weight:700;color:var(--violet)">${c.work_experience?.length||0}</span></td>
      <td><button class="btn-view" data-i="${i}">View</button></td>`;
    tr.querySelector('.btn-view').addEventListener('click',()=>openModal(i));
    tb.appendChild(tr);
  });
}
document.getElementById('search-input').addEventListener('input', e => {
  const q=e.target.value.toLowerCase().trim();
  const filtered=!q?candidates:candidates.filter(c=>[c.full_name,c.email,c.phone_number,c.location_city,...(c.skills||[]),...(c.work_experience||[]).map(w=>`${w.company} ${w.title}`)].some(v=>v&&String(v).toLowerCase().includes(q)));
  renderCards(filtered); renderTable(filtered);
});
document.getElementById('vp-cards').addEventListener('click',()=>{document.getElementById('vp-cards').classList.add('on');document.getElementById('vp-table').classList.remove('on');document.getElementById('cards-grid').classList.remove('hidden');document.getElementById('tbl-view').classList.add('hidden');});
document.getElementById('vp-table').addEventListener('click',()=>{document.getElementById('vp-table').classList.add('on');document.getElementById('vp-cards').classList.remove('on');document.getElementById('tbl-view').classList.remove('hidden');document.getElementById('cards-grid').classList.add('hidden');});

/* ───────── MODAL ───────── */
function openModal(idx){
  const c=candidates[idx]; if(!c)return;
  document.getElementById('m-name').textContent=c.full_name||'Unknown Candidate';
  document.getElementById('m-src').textContent=`Source: ${c.source_file||'unknown'}`;
  const bodyEl=document.getElementById('m-body'); bodyEl.innerHTML='';
  bodyEl.appendChild(buildSection('Contact Information',[['Name',c.full_name||'-'],['Phone',`<span class="mono">${c.phone_number||'-'}</span>`],['Email',`<span class="mono">${c.email||'-'}</span>`],['City',c.location_city||'-'],['LinkedIn',c.linkedin_profile?`<a href="${c.linkedin_profile}" target="_blank" style="color:var(--blue)">${c.linkedin_profile}</a>`:'-']]));
  if(c.summary_objective_profile){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Profile Summary</div><div style="font-size:13px;color:var(--text-mid);line-height:1.6;background:var(--glass-2);border:1px solid var(--border-dim);border-radius:var(--r-sm);padding:14px 16px">${c.summary_objective_profile}</div>`;bodyEl.appendChild(s);}
  if(c.work_experience?.length){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Work Experience</div>`+c.work_experience.map(w=>`<div class="exp-block">${w.title?`<div class="eb-title">${w.title}</div>`:''}${w.company?`<div class="eb-co">${w.company}</div>`:''}${w.duration?`<div class="eb-dur">⏱ ${w.duration}</div>`:''}${w.description?`<div class="eb-desc">${w.description}</div>`:''}</div>`).join('');bodyEl.appendChild(s);}
  if(c.education?.length){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Education</div>`+c.education.map(e=>`<div class="exp-block" style="border-left-color:var(--blue)">${e.degree?`<div class="eb-title">${e.degree}</div>`:''}${e.institution?`<div class="eb-co" style="color:var(--blue)">${e.institution}</div>`:''}${e.year?`<div class="eb-dur">🎓 ${e.year}${e.score?' · '+e.score:''}</div>`:''}</div>`).join('');bodyEl.appendChild(s);}
  if(c.skills?.length){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Skills</div><div style="display:flex;flex-wrap:wrap;gap:6px">${c.skills.map(sk=>`<span class="stag">${sk}</span>`).join('')}</div>`;bodyEl.appendChild(s);}
  if(c.certifications_courses?.length){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Certifications</div><div style="display:flex;flex-direction:column;gap:6px">${c.certifications_courses.map(cert=>`<div style="font-size:12px;color:var(--text-mid);padding:7px 12px;background:var(--glass-2);border:1px solid var(--border-dim);border-radius:var(--r-sm)">◈ ${cert}</div>`).join('')}</div>`;bodyEl.appendChild(s);}
  if(c.languages?.length)bodyEl.appendChild(buildSection('Languages',[['Known',c.languages.join(', ')]]));
  const pd=c.personal_details||{}; const pdF=[pd.dob&&['Date of Birth',pd.dob],pd.gender&&['Gender',pd.gender],pd.marital_status&&['Marital Status',pd.marital_status],pd.nationality&&['Nationality',pd.nationality],pd.father_name&&["Father's Name",pd.father_name]].filter(Boolean);
  if(pdF.length)bodyEl.appendChild(buildSection('Personal Details',pdF));
  const confN=Object.entries(c.field_confidence||{}).filter(([,v])=>typeof v==='number');
  if(confN.length){const s=document.createElement('div');s.className='msec';s.innerHTML=`<div class="msec-title">Field Confidence</div>`+confN.map(([k,v])=>{const pct=Math.round(v*100);const col=v>=0.8?'var(--green)':v>=0.5?'var(--violet)':'var(--red)';return `<div class="mfield"><div class="mlabel">${k.replace(/_/g,' ')}</div><div class="mval"><div class="conf-bar-wrap"><div class="conf-bar"><div class="conf-bar-fill" style="width:${pct}%;background:${col}"></div></div><span class="conf-pct">${pct}%</span></div></div></div>`;}).join('');bodyEl.appendChild(s);}
  document.getElementById('overlay').classList.add('open'); document.body.style.overflow='hidden';
}
function buildSection(title,fields){const sec=document.createElement('div');sec.className='msec';sec.innerHTML=`<div class="msec-title">${title}</div>`+fields.map(([l,v])=>`<div class="mfield"><div class="mlabel">${l}</div><div class="mval">${v}</div></div>`).join('');return sec;}
function closeModal(){document.getElementById('overlay').classList.remove('open');document.body.style.overflow='';}
document.getElementById('m-close').addEventListener('click',closeModal);
document.getElementById('overlay').addEventListener('click',e=>{if(e.target===document.getElementById('overlay'))closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeModal();closeNav();}});
