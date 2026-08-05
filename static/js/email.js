/* ───────── EMAIL CAMPAIGN ───────── */
(function(){
  const formLinkStatus = document.getElementById('form-link-status');
  let _formLink = '';   // sourced from Admin Settings' saved recruitment form link
  const manualEnable = document.getElementById('manual-enable');
  const manualFields = document.getElementById('manual-fields');
  const manualEmailsEl = document.getElementById('manual-emails');
  const manualCountEl = document.getElementById('manual-count');
  const sendBtn = document.getElementById('send-btn');
  const sendInfo = document.getElementById('send-info');
  const previewBtn = document.getElementById('preview-toggle');
  const previewBox = document.getElementById('email-preview');
  const trackEnable = document.getElementById('track-enable');
  const trackFields = document.getElementById('track-fields');
  const trackBaseEl = document.getElementById('track-base');
  const trackEntryEl = document.getElementById('track-entry');

  if (trackBaseEl && !trackBaseEl.value) trackBaseEl.value = window.location.origin;

  function parseManualEmails(){
    if (!manualEmailsEl) return [];
    const raw = manualEmailsEl.value.split(/[\s,;]+/).map(s => s.trim().toLowerCase()).filter(Boolean);
    const seen = new Set(), out = [];
    raw.forEach(e => { if(e.includes('@') && !seen.has(e)){ seen.add(e); out.push(e); } });
    return out;
  }

  function manualActive(){ return manualEnable && manualEnable.checked; }
  function validEmails(){ return (typeof candidates !== 'undefined' ? candidates : []).filter(c => c.email && c.email.includes('@')).length; }

  function validateEmailForm(){
    const auth = window.getGoogleAuthStatus ? window.getGoogleAuthStatus() : { connected: false };
    const recipients = manualActive() ? parseManualEmails().length : validEmails();
    const linkOk = _formLink.startsWith('http');
    const ok = auth.connected && linkOk && recipients > 0;

    if (sendBtn) sendBtn.disabled = !ok;
    if (sendInfo) {
      if (!auth.connected) {
        sendInfo.textContent = 'Connect your Google account above to send emails.';
      } else if (!linkOk) {
        sendInfo.textContent = 'Set the recruitment form link in Admin Settings first.';
      } else if (recipients === 0) {
        sendInfo.textContent = manualActive() ? 'Enter at least one valid recipient email below.' : 'Parse CVs first, or use manually added emails.';
      } else {
        sendInfo.textContent = `${recipients} email${recipients > 1 ? 's' : ''} will be sent via connected Google Account (${auth.connected_email || 'OAuth'})`;
      }
    }
  }

  window.refreshEmailCount = validateEmailForm;
  window.addEventListener('google-auth-changed', validateEmailForm);

  async function loadFormLink(){
    try{
      const res = await fetch('/api/admin/settings');
      const d = await res.json();
      _formLink = ((d.settings && d.settings.recruitment_form && d.settings.recruitment_form.form_link) || '').trim();
    }catch(e){ _formLink = ''; }
    if (formLinkStatus) {
      formLinkStatus.innerHTML = _formLink
        ? `Recruitment form: <a href="${_formLink}" target="_blank" rel="noopener" style="color:var(--violet)">${_formLink}</a>`
        : `No recruitment form set yet. <a href="#" data-goto="admin" style="color:var(--violet)">Set it in Admin Settings</a>.`;
      formLinkStatus.querySelector('[data-goto]')?.addEventListener('click', (e) => {
        e.preventDefault();
        if (window.goToPage) window.goToPage('admin');
      });
    }
    validateEmailForm();
  }
  loadFormLink();
  window.addEventListener('recruitment-form-changed', loadFormLink);

  if (manualEnable) {
    const syncManualUI = function(){
      if (manualFields) manualFields.classList.toggle('hidden', !manualEnable.checked);
      validateEmailForm();
    };
    manualEnable.addEventListener('change', syncManualUI);
    syncManualUI();
  }

  if (trackEnable) {
    const syncTrackUI = function(){
      if (trackFields) trackFields.classList.toggle('hidden', !trackEnable.checked);
    };
    trackEnable.addEventListener('change', async () => {
      syncTrackUI();
      try {
        await fetch('/api/tracking/enabled', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: trackEnable.checked }) });
      } catch(e) { console.error('Failed to save tracking toggle:', e); }
    });
    fetch('/api/tracking/enabled').then(r => r.json()).then(d => {
      trackEnable.checked = !!d.enabled;
      syncTrackUI();
    }).catch(() => { syncTrackUI(); });
  }

  if (manualEmailsEl) {
    manualEmailsEl.addEventListener('input', () => {
      const n = parseManualEmails().length;
      if (manualCountEl) manualCountEl.textContent = n ? `${n} valid email${n > 1 ? 's' : ''} added` : 'No emails added yet';
      validateEmailForm();
    });
  }

  if (previewBtn && previewBox) {
    let previewLoaded = false;
    previewBtn.addEventListener('click', async () => {
      const wasHidden = previewBox.classList.contains('hidden');
      previewBox.classList.toggle('hidden');
      if (!wasHidden || previewLoaded) return;
      // Render the ACTUAL saved recruitment template - same server-side
      // preview endpoint Admin Settings uses - instead of the old hardcoded
      // static copy that never reflected what really gets sent.
      const subjectEl = document.getElementById('email-preview-subject');
      const frameEl = document.getElementById('email-preview-frame');
      try {
        const settingsRes = await fetch('/api/admin/settings');
        const settingsData = await settingsRes.json();
        const rec = (settingsData.settings && settingsData.settings.email && settingsData.settings.email.recruitment) || {};
        const res = await fetch('/api/admin/email/recruitment/preview', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ subject: rec.subject || '', body: rec.body || '' })
        });
        const d = await res.json();
        if (!res.ok) throw new Error(errDetail(d, res.status));
        subjectEl.textContent = 'Subject: ' + (d.subject || '(no subject)');
        frameEl.srcdoc = d.html;
        previewLoaded = true;
      } catch(e) {
        subjectEl.textContent = 'Could not load preview: ' + e.message;
      }
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', async () => {
      const auth = window.getGoogleAuthStatus ? window.getGoogleAuthStatus() : { connected: false };
      if (!auth.connected) {
        toast('Please connect your Google account first.', 'err');
        return;
      }

      if (!_formLink.startsWith('http')) {
        toast('Set the recruitment form link in Admin Settings first.', 'err');
        return;
      }
      const form_link = _formLink;
      const requiredFields = [];
      if (manualActive()) {
        requiredFields.push({
          input: manualEmailsEl,
          message: 'Add at least one valid email, or turn off manual mode.',
          test: () => parseManualEmails().length > 0
        });
      }
      if (trackEnable && trackEnable.checked) {
        requiredFields.push({ input: trackBaseEl, message: 'Enter the public URL where this server is reachable.' });
      }
      if (!validateRequired(requiredFields)) return;

      const payload = { form_link, send_via: 'oauth' };
      if (manualActive()) {
        payload.manual_emails = parseManualEmails().join('\n');
      }
      if (trackEnable && trackEnable.checked) {
        payload.tracking_base_url = (trackBaseEl.value.trim() || window.location.origin);
        const entry = trackEntryEl.value.trim();
        if (entry) payload.email_entry_id = entry;
      }

      sendBtn.disabled = true;
      sendBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 15 15" fill="none" style="animation:spin 0.9s linear infinite;flex-shrink:0"><circle cx="7.5" cy="7.5" r="5.5" stroke="currentColor" stroke-width="2" stroke-dasharray="17 7" stroke-linecap="round"/></svg> Sending…`;
      if (typeof rail !== 'undefined' && rail) rail.classList.add('on');

      try {
        const res = await fetch('/api/send-emails', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) throw new Error(errDetail(data, res.status));

        document.getElementById('er-sent').textContent = `${data.sent} sent`;
        document.getElementById('er-fail').textContent = `${data.failed} failed`;
        document.getElementById('er-skip').textContent = `${data.skipped_no_email} skipped`;

        const rows = document.getElementById('er-rows');
        if (rows) {
          rows.innerHTML = '';
          (data.results || []).forEach((r, i) => {
            const div = document.createElement('div');
            div.className = 'email-result-row';
            div.style.animationDelay = `${i * 30}ms`;
            div.innerHTML = `<div class="er-name">${r.name || '-'}</div><div class="er-email">${r.email}</div><span class="er-badge ${r.status === 'sent' ? 'sent' : 'fail'}">${r.status === 'sent' ? '✓ Sent' : '✗ Failed'}</span>`;
            rows.appendChild(div);
          });
        }
        document.getElementById('email-results')?.classList.remove('hidden');
        const trackMsg = (data.tracked > 0) ? ` · ${data.tracked} tracked ⏱` : '';
        toast(`Campaign complete - ${data.sent} sent, ${data.failed} failed${trackMsg}`, data.failed === 0 ? 'ok' : 'inf');
      } catch(err) {
        toast(`Email error: ${err.message}`, 'err');
      } finally {
        if (typeof rail !== 'undefined' && rail) rail.classList.remove('on');
        validateEmailForm();
        sendBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 15 15" fill="none"><path d="M1 7.5L14 1l-4 13-3-5.5L1 7.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg> Send Campaign`;
      }
    });
  }

  validateEmailForm();
})();
