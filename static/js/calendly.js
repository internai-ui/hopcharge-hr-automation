/* ───────── ROUND 1 → CALENDLY BULK INVITE (free shared link OR API single-use) ───────── */
(function(){
  const dlg = document.getElementById('r1-invite-dialog');
  if (!dlg) return;
  let _preview = { count:0, mode:'free', calendly_url:'', api_ready:false, event_type_uri:'', event_type_name:'' };
  let _mode = 'free';

  async function fetchPreview(){
    try { const r = await fetch('/api/round1-invite'); _preview = await r.json(); }
    catch(e){ _preview = { count:0, mode:'free', calendly_url:'', api_ready:false }; }
    return _preview;
  }
  function creds(){
    const a = document.getElementById('gmail-addr'), p = document.getElementById('gmail-pass');
    return { sender: a ? a.value.trim() : '', pass: p ? p.value.trim() : '' };
  }
  function setMode(m){
    _mode = m;
    document.getElementById('r1-mode-free').classList.toggle('r1-mode-on', m==='free');
    document.getElementById('r1-mode-api').classList.toggle('r1-mode-on', m==='api');
    document.getElementById('r1-free-sec').style.display = m==='free' ? '' : 'none';
    document.getElementById('r1-api-sec').style.display  = m==='api'  ? '' : 'none';
  }
  document.getElementById('r1-mode-free').addEventListener('click', ()=>setMode('free'));
  document.getElementById('r1-mode-api').addEventListener('click', ()=>{ setMode('api'); maybeAutoLoadEvents(); });

  async function loadEvents(){
    const status = document.getElementById('r1-api-status');
    const tok = document.getElementById('r1-token').value.trim();
    try {
      if (tok) await fetch('/api/round1-invite/settings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'api',api_token:tok})});
      status.textContent = 'Loading event types…';
      const r = await fetch('/api/round1-invite/event-types');
      const d = await r.json();
      if (!r.ok) throw new Error(errDetail(d, r.statusText));
      const sel = document.getElementById('r1-event');
      sel.innerHTML = (d.event_types||[]).map(e=>`<option value="${e.uri}">${e.name}${e.duration?` (${e.duration}m)`:''}</option>`).join('') || '<option value="">No event types found</option>';
      if (_preview.event_type_uri) sel.value = _preview.event_type_uri;
      status.textContent = d.user ? `Connected as ${d.user}. Each candidate gets a unique single-use link.` : 'Each candidate gets a unique single-use link.';
    } catch(e){ status.textContent = 'Could not load events: ' + e.message; toast('Calendly: ' + e.message, 'err'); }
  }
  document.getElementById('r1-load-events').addEventListener('click', loadEvents);
  function maybeAutoLoadEvents(){ if (_preview.api_ready || _preview.event_type_uri) loadEvents(); }

  window.openR1Invite = function(){
    const c = creds();
    setMode(_preview.mode==='api' ? 'api' : 'free');
    // Populate each role's link from saved settings + show how many go to each.
    const roles = _preview.roles || [];
    const cse = roles.find(r=>r.role_key==='customer_support_executive');
    const ops = roles.find(r=>r.role_key==='operations_specialist');
    const cseEl = document.getElementById('r1-link-cse');
    const opsEl = document.getElementById('r1-link-ops');
    if (cseEl) cseEl.value = cseEl.value || (cse && cse.link) || '';
    if (opsEl) opsEl.value = opsEl.value || (ops && ops.link) || '';
    const cseN = cse ? cse.count : 0, opsN = ops ? ops.count : 0;
    const cseHint = document.getElementById('r1-cse-hint');
    const opsHint = document.getElementById('r1-ops-hint');
    if (cseHint) cseHint.textContent = `Goes to ${cseN} Round 1 candidate${cseN!==1?'s':''} who applied for Customer Support Executive.`;
    if (opsHint) opsHint.textContent = `Goes to ${opsN} Round 1 candidate${opsN!==1?'s':''} who applied for Operations Specialist.`;
    document.getElementById('r1-sender').value = document.getElementById('r1-sender').value || c.sender;
    document.getElementById('r1-pass').value   = document.getElementById('r1-pass').value   || c.pass;
    document.getElementById('r1-sub').textContent = _preview.count
      ? `Email ${_preview.count} Round 1 candidate${_preview.count!==1?'s':''} a Calendly booking link for their role.`
      : 'No Round 1 candidates with an email were found yet.';
    if (_mode==='api') maybeAutoLoadEvents();
    dlg.classList.add('open');
  };
  window.closeR1Invite = function(){ dlg.classList.remove('open'); };
  dlg.addEventListener('click', e=>{ if (e.target.id==='r1-invite-dialog') closeR1Invite(); });
  document.addEventListener('keydown', e=>{ if (e.key==='Escape') closeR1Invite(); });

  async function doSend(){
    const senderEl = document.getElementById('r1-sender'), passEl = document.getElementById('r1-pass');
    const sender = senderEl.value.trim();
    const pass   = passEl.value.trim();
    const cseEl = document.getElementById('r1-link-cse'), opsEl = document.getElementById('r1-link-ops');
    const baseFields = [
      { input: senderEl, message: 'Enter the sender Gmail address.' },
      { input: passEl, message: 'Enter the Gmail App Password.' },
    ];
    if (_mode==='free') {
      const bad = u => u && !u.toLowerCase().includes('calendly.com');
      baseFields.push(
        { input: cseEl, message: 'Must be a valid Calendly URL.', test: v => !bad(v) },
        { input: opsEl, message: 'Must be a valid Calendly URL.', test: v => !bad(v) },
      );
    }
    if (!validateRequired(baseFields)) return;
    if (_mode==='free' && !cseEl.value.trim() && !opsEl.value.trim()) {
      showFieldError(cseEl, 'Add at least one role\u2019s Calendly link.');
      showFieldError(opsEl, 'Add at least one role\u2019s Calendly link.');
      cseEl.focus();
      return;
    }
    const btn = document.getElementById('r1-confirm-btn'); const orig = btn.textContent;
    const payload = { gmail_address: sender, app_password: pass };
    try {
      if (_mode==='free'){
        const cseLink = cseEl.value.trim();
        const opsLink = opsEl.value.trim();
        const role_links = {
          customer_support_executive: cseLink,
          operations_specialist: opsLink,
        };
        await fetch('/api/round1-invite/settings', {method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({mode:'free',
            link_customer_support_executive:cseLink,
            link_operations_specialist:opsLink})});
        payload.role_links = role_links;
      } else {
        const evEl = document.getElementById('r1-event');
        if (!validateRequired([{ input: evEl, message: 'Pick a Calendly event type (Load events first).' }])) return;
        const ev = evEl.value;
        const tok = document.getElementById('r1-token').value.trim();
        const evName = document.getElementById('r1-event').selectedOptions[0]?.textContent || '';
        const body = Object.assign({ mode:'api', event_type_uri:ev, event_type_name:evName }, tok ? { api_token:tok } : {});
        await fetch('/api/round1-invite/settings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      }
      btn.disabled = true; btn.textContent = 'Sending…';
      const res = await fetch('/api/round1-invite/send', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const d = await res.json();
      if (!res.ok) throw new Error(errDetail(d, res.statusText));
      const failed = (d.results||[]).filter(x=>x.status==='failed').length;
      const skipped = d.skipped || (d.results||[]).filter(x=>x.status==='skipped').length;
      if (d.sent>0 && !failed && !skipped)  toast(`Sent the Round 1 Calendly invite to ${d.sent} candidate${d.sent!==1?'s':''}`, 'ok');
      else if (d.sent>0 && skipped)         toast(`Sent ${d.sent}; ${skipped} skipped (no link set for their role)`, 'ok');
      else if (d.sent>0)                    toast(`Sent ${d.sent}, ${failed} failed — check addresses`, 'ok');
      else if (skipped)                     toast(`Nothing sent — ${skipped} candidate(s) have no link set for their role`, 'err');
      else                                  toast(d.error || 'Nothing was sent', 'err');
      closeR1Invite();
    } catch(e){ toast('Send failed: ' + e.message, 'err'); }
    finally { btn.disabled = false; btn.textContent = orig; }
  }
  document.getElementById('r1-confirm-btn').addEventListener('click', doSend);

  const trigger = document.getElementById('r1-invite-btn');
  if (trigger) trigger.addEventListener('click', async ()=>{
    await fetchPreview();
    if (!_preview.count){ toast('No Round 1 candidates to invite yet', 'err'); return; }
    openR1Invite();   // always open the dialog so the free / API choice is explicit
  });

  document.querySelector('.sb-item[data-page="accepted"]')?.addEventListener('click', fetchPreview);
  fetchPreview();
})();
