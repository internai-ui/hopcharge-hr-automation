/* ───────── ANALYTICS PAGE ───────── */
(function(){
  if (!document.getElementById('page-analytics')) return;
  const WINS = [['today','Today'],['7d','7 days'],['30d','30 days'],['90d','90 days'],['180d','6 months'],['1y','1 year'],['all','All time']];
  let _win = '30d';

  function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  const tabsWrap = document.getElementById('an-window-tabs');
  function renderTabs(){
    tabsWrap.innerHTML = WINS.map(([k,lbl])=>
      `<button class="an-win" data-win="${k}" style="background:${k===_win?'#2F5BEA':'var(--glass-2)'};color:${k===_win?'#fff':'var(--text-mid)'};border:1px solid var(--border-dim);border-radius:8px;font-size:12px;font-weight:600;padding:6px 12px;cursor:pointer">${lbl}</button>`
    ).join('');
    tabsWrap.querySelectorAll('.an-win').forEach(b=> b.addEventListener('click', ()=>{ _win=b.dataset.win; renderTabs(); load(); }));
  }

  const CARD_DEFS = [
    ['cvs_parsed','CVs parsed','#2F5BEA'],
    ['emails_sent','Emails sent','#0ea5a3'],
    ['responses_received','Responses','#7c3aed'],
    ['candidates_scored','Scored','#8b5cf6'],
    ['accepted','Accepted','#34d399'],
    ['rejected','Rejected','#f87171'],
    ['interviews_invited','Interviews','#f59e0b'],
    ['accept_rate','Accept rate','#22d3ee'],
  ];
  function renderCards(c){
    document.getElementById('an-cards').innerHTML = CARD_DEFS.map(([k,lbl,col])=>{
      let v = c[k]; if (k==='accept_rate') v = (v||0)+'%';
      return `<div style="background:var(--glass-2);border:1px solid var(--border-dim);border-left:3px solid ${col};border-radius:12px;padding:16px">
        <div style="font-size:26px;font-weight:800;color:var(--text-on);font-family:'Poppins',sans-serif;line-height:1">${v==null?0:v}</div>
        <div style="font-size:10.5px;color:var(--text-dim);margin-top:6px;letter-spacing:.4px;text-transform:uppercase">${lbl}</div>
      </div>`;
    }).join('');
  }

  function bars(containerId, items){
    const el = document.getElementById(containerId);
    if (!items.length){ el.innerHTML = '<div style="font-size:12px;color:var(--text-dim);padding:6px 0">No data in this window.</div>'; return; }
    const max = Math.max(1, ...items.map(i=>i.value));
    el.innerHTML = items.map(i=>{
      const pct = Math.round(100*i.value/max);
      const col = i.color || '#2F5BEA';
      return `<div style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-mid);margin-bottom:3px;gap:10px"><span>${esc(i.label)}</span><span style="font-family:'JetBrains Mono',monospace;color:var(--text-on)">${i.value}</span></div>
        <div style="background:rgba(140,140,160,.16);border-radius:6px;height:9px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${col};border-radius:6px"></div></div>
      </div>`;
    }).join('');
  }

  const SERIES = [['responses','Responses','#7c3aed'],['accepted','Accepted','#34d399'],['rejected','Rejected','#f87171'],['parsed','CVs parsed','#2F5BEA'],['emails','Emails','#0ea5a3']];
  function trend(series){
    document.getElementById('an-legend').innerHTML = SERIES.map(([k,lbl,col])=>
      `<span style="display:inline-flex;align-items:center;gap:5px"><span style="width:10px;height:10px;border-radius:2px;background:${col};display:inline-block"></span>${lbl}</span>`).join('');
    const host = document.getElementById('an-trend');
    if (!series.length){ host.innerHTML='<div style="font-size:12px;color:var(--text-dim)">No data.</div>'; return; }
    const n=series.length, W=Math.max(680, n*46), H=240, pl=36, pb=30, pt=12, pr=12;
    const maxV=Math.max(1,...series.flatMap(s=>SERIES.map(([k])=>s[k]||0)));
    const X=i=> pl + (n<=1? (W-pl-pr)/2 : i*(W-pl-pr)/(n-1));
    const Y=v=> H-pb - (v/maxV)*(H-pt-pb);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMinYMin meet" style="min-width:${W}px">`;
    [0,0.5,1].forEach(f=>{ const v=Math.round(maxV*f), yy=Y(v);
      svg+=`<line x1="${pl}" y1="${yy.toFixed(1)}" x2="${W-pr}" y2="${yy.toFixed(1)}" stroke="rgba(140,140,160,.18)" stroke-width="1"/>`+
           `<text x="${pl-6}" y="${(yy+3).toFixed(1)}" text-anchor="end" font-size="9" fill="rgba(140,140,160,.85)">${v}</text>`; });
    const step = Math.ceil(n/8);
    series.forEach((s,i)=>{ if(i%step===0||i===n-1){ svg+=`<text x="${X(i).toFixed(1)}" y="${H-pb+14}" text-anchor="middle" font-size="9" fill="rgba(140,140,160,.85)">${esc(s.label)}</text>`; }});
    SERIES.forEach(([k,lbl,col])=>{
      const pts = series.map((s,i)=>`${X(i).toFixed(1)},${Y(s[k]||0).toFixed(1)}`).join(' ');
      svg += `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      series.forEach((s,i)=>{ svg+=`<circle cx="${X(i).toFixed(1)}" cy="${Y(s[k]||0).toFixed(1)}" r="2.2" fill="${col}"/>`; });
    });
    host.innerHTML = svg + `</svg>`;
  }

  async function load(){
    try{
      const res = await fetch(`/api/analytics/overview?window=${encodeURIComponent(_win)}`);
      const d = await res.json();
      if(!res.ok) throw new Error(errDetail(d, res.status));
      renderCards(d.cards||{});
      trend(d.series||[]);
      bars('an-roles', (d.role_breakdown||[]).slice(0,12).map(r=>({label:`${r.role}  (✓${r.accepted} ✕${r.rejected})`, value:r.processed, color:'#7c3aed'})));
      bars('an-bands', Object.entries(d.band_distribution||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>({label:k, value:v, color:'#8b5cf6'})));
      const SL={hr:'HR Round',round1:'Round 1',round2:'Round 2',onboarded:'Onboarded'};
      bars('an-stages', Object.entries(d.stage_accepted||{}).map(([k,v])=>({label:SL[k]||k, value:v, color:'#34d399'})));
      bars('an-rounds', Object.entries(d.round_rejected||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>({label:k, value:v, color:'#f87171'})));
      document.getElementById('an-note').textContent = (d.notes&&d.notes.message)||'';
    }catch(e){
      document.getElementById('an-cards').innerHTML = `<div style="grid-column:1/-1;color:#f87171;font-size:13px">Could not load analytics: ${esc(e.message)}</div>`;
    }
  }

  renderTabs();
  document.getElementById('an-refresh')?.addEventListener('click', load);
  document.querySelector('.sb-item[data-page="analytics"]')?.addEventListener('click', load);
})();
