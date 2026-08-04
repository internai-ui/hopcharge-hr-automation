/* ───── GOOGLE DRIVE → CV SYNC ───── */
(function(){
  const dlg=document.getElementById('drive-dialog');
  if(!dlg) return;
  const folderEl=()=>document.getElementById('drive-folder');
  const statusEl=()=>document.getElementById('drive-status');
  const DEFAULT_HINT='Auth reuses your Google Forms service account. Share the folder with that service-account email (Viewer).';

  async function loadStatus(){
    try{
      const r=await fetch('/api/drive'); const d=await r.json();
      if(d.folder_id && !folderEl().value) folderEl().value=d.folder_id;
      const bits=[];
      if(d.service_account_email) bits.push(`Share the folder with <b style="color:var(--text-mid)">${d.service_account_email}</b> (Viewer).`);
      else if(d.credentials_found===false) bits.push('No service-account key found in the project folder — add the same JSON you use for Forms.');
      if(d.last_synced) bits.push(`Last sync: ${new Date(d.last_synced).toLocaleString()} · ${d.imported_count} imported so far.`);
      if(bits.length) statusEl().innerHTML=bits.join('<br>');
    }catch(e){}
  }

  window.openDrive=function(){ statusEl().innerHTML=DEFAULT_HINT; loadStatus(); dlg.classList.add('open'); };
  window.closeDrive=function(){ dlg.classList.remove('open'); };
  dlg.addEventListener('click',e=>{ if(e.target.id==='drive-dialog') closeDrive(); });
  document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeDrive(); });
  document.getElementById('drive-open-btn')?.addEventListener('click', openDrive);

  document.getElementById('drive-test-btn').addEventListener('click', async ()=>{
    if (!validateRequired([{ input: folderEl(), message: 'Paste a Drive folder link or ID first.' }])) return;
    const folder=folderEl().value.trim();
    statusEl().textContent='Checking access…';
    try{
      await fetch('/api/drive/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})});
      const r=await fetch('/api/drive/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})});
      const d=await r.json();
      if(!r.ok) throw new Error(errDetail(d, r.statusText));
      statusEl().innerHTML=`Connected to <b style="color:var(--text-mid)">${d.folder_name}</b> — ${d.pdf_count} PDF${d.pdf_count!==1?'s':''}, <b style="color:var(--text-mid)">${d.new_count} new</b>.`;
      toast(`Drive: ${d.new_count} new CV${d.new_count!==1?'s':''} ready`,'ok');
    }catch(e){ statusEl().textContent='Access failed: '+e.message; toast('Drive: '+e.message,'err'); }
  });

  document.getElementById('drive-sync-btn').addEventListener('click', async ()=>{
    if (!validateRequired([{ input: folderEl(), message: 'Paste a Drive folder link or ID first.' }])) return;
    const folder=folderEl().value.trim();
    const btn=document.getElementById('drive-sync-btn'); const orig=btn.textContent;
    btn.disabled=true; btn.textContent='Syncing…';
    try{
      await fetch('/api/drive/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})});
      const r=await fetch('/api/drive/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})});
      const d=await r.json();
      if(!r.ok) throw new Error(errDetail(d, r.statusText));
      if(!d.success){ toast(d.error||'Sync failed','err'); }
      else {
        let msg=`Drive sync: ${d.imported} imported`;
        if(d.duplicates) msg+=`, ${d.duplicates} duplicate`;
        if(d.failed) msg+=`, ${d.failed} failed`;
        if(d.imported===0 && !d.failed && !d.duplicates) msg=`Drive: nothing new (${d.scanned} scanned)`;
        toast(msg, d.failed?'err':'ok');
        (d.results||[]).filter(x=>x.status==='failed').forEach(x=>toast(`${x.name}: ${x.error}`,'err'));
        try{
          const cr=await fetch('/api/download/json');
          if(cr.ok){ const arr=await cr.json(); if(Array.isArray(arr)&&arr.length){ candidates=arr; renderResults(candidates); if(typeof refreshEmailCount==='function') refreshEmailCount(); } }
        }catch(_){}
        closeDrive();
      }
    }catch(e){ toast('Drive: '+e.message,'err'); }
    finally{ btn.disabled=false; btn.textContent=orig; }
  });
})();
