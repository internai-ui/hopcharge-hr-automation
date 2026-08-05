/* ───────── CENTRAL SHARED GOOGLE AUTH WIDGET ───────── */
(function(){
  'use strict';
  let _authStatus = { connected: false, client_configured: false };

  window.getGoogleAuthStatus = function() {
    return _authStatus;
  };

  window.refreshGoogleAuthWidgets = async function() {
    try {
      const res = await fetch('/api/gmail-oauth/status');
      const d = await res.json();
      _authStatus = d;
      renderAllWidgets(d);
      window.dispatchEvent(new CustomEvent('google-auth-changed', { detail: d }));
    } catch(e) {
      console.warn('Could not fetch Google auth status:', e);
      renderAllWidgets({ connected: false, client_configured: false, error: true });
    }
  };

  function renderAllWidgets(data) {
    const widgets = document.querySelectorAll('.google-auth-widget');
    widgets.forEach(w => renderWidget(w, data));
  }

  function renderWidget(container, d) {
    let card = container.querySelector('.g-widget-card');
    if (!card) {
      container.innerHTML = `
        <div class="g-widget-card">
          <div class="g-widget-header">
            <div class="g-widget-identity">
              <div class="g-widget-logo">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" fill="#FBBC05"/>
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" fill="#EA4335"/>
                </svg>
              </div>
              <div>
                <div class="g-widget-title">Connect with Google</div>
                <div class="g-widget-detail" data-gw="detail">Checking connection status...</div>
              </div>
            </div>
            <div class="g-widget-controls">
              <span class="g-widget-pill" data-gw="pill">Checking...</span>
              <button type="button" class="g-widget-connect-btn hidden" data-gw="connect">
                Connect Google Account
              </button>
              <button type="button" class="btn-ghost g-widget-disconnect-btn hidden" data-gw="disconnect" style="border-color:rgba(248,113,113,0.35);color:#f87171">
                Disconnect
              </button>
            </div>
          </div>

          <div class="g-widget-badges">
            <span class="g-badge" title="Send recruitment & onboarding emails"><span class="g-dot" data-gw="dot-email">●</span> Gmail Send</span>
            <span class="g-badge" title="Track candidate email replies"><span class="g-dot" data-gw="dot-replies">●</span> Reply Tracking</span>
            <span class="g-badge" title="Fetch candidate assessment responses"><span class="g-dot" data-gw="dot-forms">●</span> Google Forms API</span>
          </div>

          <button type="button" class="g-widget-settings-toggle" data-gw="toggle-settings">
            ⚙ Paste OAuth Client Credentials manually
          </button>

          <div class="g-widget-settings-box hidden" data-gw="settings-box">
            <div class="g-settings-hint" data-gw="mode-hint">Drop client secret JSON into <code>credentials/</code> or paste manually below:</div>
            <div class="g-settings-grid">
              <div class="field-group">
                <label class="field-label">OAuth Client ID</label>
                <input class="field-input" data-gw="client-id" type="text" placeholder="xxxxxxxx.apps.googleusercontent.com">
              </div>
              <div class="field-group">
                <label class="field-label">OAuth Client Secret</label>
                <input class="field-input" data-gw="client-secret" type="password" placeholder="GOCSPX-...">
              </div>
            </div>
            <div style="display:flex;justify-content:flex-end;margin-top:10px">
              <button type="button" class="btn-ghost" data-gw="save-config">Save Client Credentials</button>
            </div>
          </div>
        </div>
      `;
      card = container.querySelector('.g-widget-card');

      // Wire up buttons
      const connectBtn = card.querySelector('[data-gw="connect"]');
      const disconnectBtn = card.querySelector('[data-gw="disconnect"]');
      const toggleBtn = card.querySelector('[data-gw="toggle-settings"]');
      const settingsBox = card.querySelector('[data-gw="settings-box"]');
      const saveBtn = card.querySelector('[data-gw="save-config"]');

      if (connectBtn) connectBtn.addEventListener('click', () => {
        // Send the user back to whichever page/dialog they clicked Connect
        // from, instead of always landing on the SPA's default page once
        // Google redirects back to /callback.
        const current = document.querySelector('.page.active')?.id.replace(/^page-/, '') || '';
        const qs = current ? ('?return_to=' + encodeURIComponent(current)) : '';
        window.location.href = '/api/gmail-oauth/authorize' + qs;
      });
      if (disconnectBtn) disconnectBtn.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/gmail-oauth/disconnect', { method: 'POST' });
          const d = await res.json();
          if (!res.ok) throw new Error(errDetail(d, res.status));
          toast('Google account disconnected', 'ok');
          window.refreshGoogleAuthWidgets();
        } catch(e) { toast('Could not disconnect: ' + e.message, 'err'); }
      });
      if (toggleBtn && settingsBox) toggleBtn.addEventListener('click', () => settingsBox.classList.toggle('hidden'));
      if (saveBtn) saveBtn.addEventListener('click', async () => {
        const idEl = card.querySelector('[data-gw="client-id"]');
        const secEl = card.querySelector('[data-gw="client-secret"]');
        if (!idEl || !secEl || !idEl.value.trim() || !secEl.value.trim()) {
          toast('Please enter both Client ID and Client Secret.', 'err');
          return;
        }
        try {
          const res = await fetch('/api/gmail-oauth/client-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: idEl.value.trim(), client_secret: secEl.value.trim() })
          });
          const d = await res.json();
          if (!res.ok) throw new Error(errDetail(d, res.status));
          secEl.value = '';
          toast('Google OAuth client saved - click Connect Google Account to sign in.', 'ok');
          window.refreshGoogleAuthWidgets();
        } catch(e) { toast('Could not save client config: ' + e.message, 'err'); }
      });
    }

    // Populate data
    const detailEl = card.querySelector('[data-gw="detail"]');
    const pillEl = card.querySelector('[data-gw="pill"]');
    const connectBtn = card.querySelector('[data-gw="connect"]');
    const disconnectBtn = card.querySelector('[data-gw="disconnect"]');
    const toggleBtn = card.querySelector('[data-gw="toggle-settings"]');
    const hintEl = card.querySelector('[data-gw="mode-hint"]');
    const clientIdEl = card.querySelector('[data-gw="client-id"]');

    const dotEmail = card.querySelector('[data-gw="dot-email"]');
    const dotReplies = card.querySelector('[data-gw="dot-replies"]');
    const dotForms = card.querySelector('[data-gw="dot-forms"]');

    const fromFile = d.client_source === 'file';
    if (toggleBtn) toggleBtn.classList.toggle('hidden', fromFile);
    if (hintEl) {
      hintEl.innerHTML = fromFile
        ? `Loaded automatically from <code>credentials/${d.client_source_file}</code> (mode: ${d.deployment_mode}).`
        : `Deployment mode: ${d.deployment_mode || 'web'}. Drop client JSON into <code>credentials/</code> or paste below:`;
    }
    if (clientIdEl && d.client_id && !fromFile && !clientIdEl.value) {
      clientIdEl.value = d.client_id;
    }

    if (d.connected) {
      pillEl.textContent = 'Connected';
      pillEl.style.background = 'rgba(52,211,153,0.15)';
      pillEl.style.color = '#34d399';
      detailEl.textContent = d.connected_email ? `Connected as ${d.connected_email}` : 'Connected to Google';
      if (connectBtn) connectBtn.classList.add('hidden');
      if (disconnectBtn) disconnectBtn.classList.remove('hidden');

      const hasForms = (d.scopes || []).includes('https://www.googleapis.com/auth/forms.responses.readonly');
      if (dotEmail) dotEmail.style.color = '#34d399';
      if (dotReplies) dotReplies.style.color = '#34d399';
      if (dotForms) dotForms.style.color = hasForms ? '#34d399' : '#fbbf24';

      if (!hasForms) {
        pillEl.textContent = 'Reconnect needed for Forms';
        pillEl.style.background = 'rgba(251,191,36,0.15)';
        pillEl.style.color = '#fbbf24';
        if (connectBtn) {
          connectBtn.textContent = 'Reconnect for Forms Access';
          connectBtn.classList.remove('hidden');
        }
      }
    } else {
      pillEl.textContent = d.client_configured ? 'Not connected' : 'Not set up';
      pillEl.style.background = 'rgba(248,113,113,0.15)';
      pillEl.style.color = '#f87171';
      detailEl.textContent = d.client_configured
        ? 'Click below to connect your Google account via OAuth.'
        : 'Drop the client-secret JSON into credentials/ or paste OAuth Client ID/Secret below.';
      if (connectBtn) {
        connectBtn.textContent = 'Connect Google Account';
        connectBtn.classList.remove('hidden');
      }
      if (disconnectBtn) disconnectBtn.classList.add('hidden');

      if (dotEmail) dotEmail.style.color = 'var(--text-dim)';
      if (dotReplies) dotReplies.style.color = 'var(--text-dim)';
      if (dotForms) dotForms.style.color = 'var(--text-dim)';
    }
  }

  // Handle redirect feedback params - including returning to whichever page
  // the user clicked Connect from (see the connect button handler above),
  // instead of leaving them on the SPA's default landing page.
  (function(){
    const params = new URLSearchParams(window.location.search);
    if(params.has('gmail')){
      const status = params.get('gmail');
      if(status === 'connected') toast('Google Account connected successfully', 'ok');
      else if(status === 'error') toast('Google connection failed: '+(params.get('reason')||'unknown error'), 'err');
      const returnPage = params.get('page');
      params.delete('gmail'); params.delete('reason'); params.delete('page');
      const qs = params.toString();
      window.history.replaceState({}, '', window.location.pathname + (qs?`?${qs}`:''));
      if (returnPage && window.goToPage) window.goToPage(returnPage);
    }
  })();

  // Initial load
  document.addEventListener('DOMContentLoaded', () => {
    window.refreshGoogleAuthWidgets();
  });
  // Fallback immediate call if DOM is already ready
  if (document.readyState !== 'loading') {
    window.refreshGoogleAuthWidgets();
  }
})();
