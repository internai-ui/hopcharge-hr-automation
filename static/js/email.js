/* ───────── EMAIL CAMPAIGN ───────── */
(function(){
  const linkEl = document.getElementById('form-link');
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
    const linkOk = linkEl && linkEl.value.trim().startsWith('http');
    const ok = auth.connected && linkOk && recipients > 0;

    if (sendBtn) sendBtn.disabled = !ok;
    if (sendInfo) {
      if (!auth.connected) {
        sendInfo.textContent = 'Connect your Google account above to send emails.';
      } else if (!linkOk) {
        sendInfo.textContent = 'Enter a valid Google Forms URL above.';
      } else if (recipients === 0) {
        sendInfo.textContent = manualActive() ? 'Enter at least one valid recipient email below.' : 'Parse CVs first, or use manually added emails.';
      } else {
        sendInfo.textContent = `${recipients} email${recipients > 1 ? 's' : ''} will be sent via connected Google Account (${auth.connected_email || 'OAuth'})`;
      }
    }
  }

  window.refreshEmailCount = validateEmailForm;
  window.addEventListener('google-auth-changed', validateEmailForm);

  if (linkEl) linkEl.addEventListener('input', validateEmailForm);
  if (linkEl) linkEl.addEventListener('blur', () => {
    if (linkEl.value.trim()) clearFieldError(linkEl);
    else showFieldError(linkEl, 'Enter the Google Forms assessment link.');
  });

  if (manualEnable) {
    const syncManualUI = function(){
      if (manualFields) manualFields.classList.toggle('hidden', !manualEnable.checked);
      const knob = manualEnable.closest('.switch')?.querySelector('.slider');
      if (knob) {
        knob.style.setProperty('--knob', manualEnable.checked ? 'translateX(17px)' : 'translateX(0)');
        knob.style.background = manualEnable.checked ? '#60a5fa' : 'rgba(255,255,255,0.15)';
      }
      validateEmailForm();
    };
    manualEnable.addEventListener('change', syncManualUI);
    syncManualUI();
  }

  if (trackEnable) {
    const syncTrackUI = function(){
      if (trackFields) trackFields.classList.toggle('hidden', !trackEnable.checked);
      const knob = trackEnable.closest('.switch')?.querySelector('.slider');
      if (knob) {
        knob.style.setProperty('--knob', trackEnable.checked ? 'translateX(17px)' : 'translateX(0)');
        knob.style.background = trackEnable.checked ? '#60a5fa' : 'rgba(255,255,255,0.15)';
      }
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
    previewBtn.addEventListener('click', () => { previewBox.classList.toggle('hidden'); });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', async () => {
      const auth = window.getGoogleAuthStatus ? window.getGoogleAuthStatus() : { connected: false };
      if (!auth.connected) {
        toast('Please connect your Google account first.', 'err');
        return;
      }

      const form_link = linkEl.value.trim();
      const requiredFields = [
        { input: linkEl, message: 'Enter the Google Forms assessment link.' },
      ];
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
            div.innerHTML = `<div class="er-name">${r.name || '—'}</div><div class="er-email">${r.email}</div><span class="er-badge ${r.status === 'sent' ? 'sent' : 'fail'}">${r.status === 'sent' ? '✓ Sent' : '✗ Failed'}</span>`;
            rows.appendChild(div);
          });
        }
        document.getElementById('email-results')?.classList.remove('hidden');
        const trackMsg = (data.tracked > 0) ? ` · ${data.tracked} tracked ⏱` : '';
        toast(`Campaign complete — ${data.sent} sent, ${data.failed} failed${trackMsg}`, data.failed === 0 ? 'ok' : 'inf');
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
