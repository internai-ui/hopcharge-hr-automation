/* ───────── AI-ASSISTED CV PARSING SETTINGS (lives inside Admin Settings) ───────── */
(function () {
  if (!document.getElementById('ai-feature-enable')) return;
  const $ = id => document.getElementById(id);
  const MODELS = { huggingface:['meta-llama/Llama-3.1-8B-Instruct','Qwen/Qwen2.5-Coder-32B-Instruct','deepseek-ai/DeepSeek-R1'],
                   anthropic:['claude-3-5-sonnet-latest','claude-3-5-haiku-latest','claude-3-opus-latest','claude-sonnet-4-20250514'],
                   openai:['gpt-4o','gpt-4o-mini','gpt-4-turbo','gpt-5','gpt-5-mini'],
                   gemini:['gemini-2.0-flash','gemini-2.5-flash','gemini-2.5-pro','gemini-1.5-flash','gemini-1.5-pro'],
                   groq:['llama-3.3-70b-versatile','llama-3.1-8b-instant','gemma2-9b-it','deepseek-r1-distill-llama-70b','qwen-2.5-32b'] };
  let _provider = 'huggingface';
  let _currentMode = 'offline';  // offline or api
  let _loaded = false;
  let _featureEnabled = false;   // master AI-parsing toggle — off by default, persisted server-side

  function fillModels(sel) {
    // Populate the datalist with suggestions for the current provider
    $('ai-model-list').innerHTML = MODELS[_provider].map(m => `<option value="${m}">`).join('');
    // Set the input value: keep saved value if provided, else first suggestion
    if (sel) $('ai-model').value = sel;
    else if (!$('ai-model').value) $('ai-model').value = MODELS[_provider][0];
  }

  // Mode toggle — real radio inputs, native label-click semantics select them.
  document.querySelectorAll('.ai-mode-radio-input').forEach(radio => {
    radio.addEventListener('change', async () => {
      const mode = radio.value;
      document.querySelectorAll('.ai-mode-option').forEach(o => o.classList.toggle('selected', o.dataset.mode === mode));
      _currentMode = mode;
      updateModeUI();

      // If switching TO offline, remove the API key
      if (mode === 'offline') {
        try {
          const payload = { provider: 'anthropic', model: 'claude-sonnet', temperature: 0.2, api_key: '' };
          const r = await fetch('/api/ai/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
          const d = await r.json();
          if (r.ok) {
            toast('Switched to offline parsing','ok');
          }
        } catch(e){ console.error('Switch to offline failed:', e); }
      }
    });
  });

  function updateModeUI() {
    const apiSection = $('api-config-section');
    const switchBtn = $('ai-switch-btn');
    // While the master AI-parsing feature is off, force the offline view
    // regardless of what mode was last selected — the API section, key
    // fields, etc. must never be reachable until the toggle is turned on.
    const effectiveMode = _featureEnabled ? _currentMode : 'offline';
    if (effectiveMode === 'offline') {
      apiSection.classList.add('hidden');
      switchBtn.textContent = 'Switch to API Mode';
      switchBtn.onclick = () => { document.querySelector('input[name="ai-mode"][value="api"]').click(); };
    } else {
      apiSection.classList.remove('hidden');
      switchBtn.textContent = 'Switch to Offline Mode';
      switchBtn.onclick = () => { document.querySelector('input[name="ai-mode"][value="offline"]').click(); };
    }
  }

  // ── Master feature toggle: enable/disable AI-based (external LLM) CV parsing ──
  function updateFeatureUI() {
    const apiOption = $('ai-mode-api-option');
    const switchRow = $('ai-switch-btn-row');
    const howItWorksApi = $('ai-how-it-works-api');
    [apiOption, switchRow, howItWorksApi].forEach(el => el && el.classList.toggle('hidden', !_featureEnabled));
    updateModeUI();
  }

  const featureToggle = $('ai-feature-enable');
  featureToggle.addEventListener('change', async () => {
    featureToggle.disabled = true;
    try {
      const r = await fetch('/api/ai/feature', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: featureToggle.checked }),
      });
      const d = await r.json();
      _featureEnabled = !!d.enabled;
      featureToggle.checked = _featureEnabled;
      updateFeatureUI();
      toast(_featureEnabled ? 'AI-assisted CV parsing enabled' : 'AI-assisted CV parsing disabled — offline parser only', 'ok');
    } catch (e) {
      featureToggle.checked = !featureToggle.checked;
      alert('Failed to update the AI-parsing toggle: ' + e.message);
    } finally {
      featureToggle.disabled = false;
    }
  });

  async function loadFeature() {
    try {
      const d = await fetch('/api/ai/feature').then(r => r.json());
      _featureEnabled = !!d.enabled;
      featureToggle.checked = _featureEnabled;
      updateFeatureUI();
    } catch (e) { }
  }

  // Provider toggle (only in API section)
  document.querySelectorAll('.ai-prov-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ai-prov-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _provider = btn.dataset.provider;
      $('ai-model').value = '';   // reset so the new provider's default suggestion applies
      fillModels();
    });
  });

  $('ai-temp').addEventListener('input', e => $('ai-temp-val').textContent = parseFloat(e.target.value).toFixed(2));

  async function loadConfig() {
    try {
      const d = await fetch('/api/ai/config').then(r=>r.json());
      const c = d.config;
      _currentMode = c.key_set ? 'api' : 'offline';
      document.querySelectorAll('.ai-mode-option').forEach(o => 
        o.classList.toggle('selected', o.dataset.mode === _currentMode));
      updateModeUI();
      if (c.key_set) {
        _provider = c.provider;
        document.querySelectorAll('.ai-prov-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.provider===_provider));
        fillModels(c.model);
        $('ai-temp').value = c.temperature; $('ai-temp-val').textContent = c.temperature.toFixed(2);
        $('api-status').textContent = `✓ Configured · ${_provider}`;
      } else {
        $('offline-status').textContent = 'Active · Parsing now';
      }
      _loaded = true;
    } catch(e) { }
  }

  // Save API Key
  $('ai-save-btn').addEventListener('click', async () => {
    const ok = validateRequired([
      { input: $('ai-model'), message: 'Enter the model id.' },
      { input: $('ai-key'), message: 'Enter your API key.' },
    ]);
    if (!ok) return;
    const key = $('ai-key').value.trim();
    $('ai-save-btn').disabled = true;
    try {
      const r = await fetch('/api/ai/config', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:_provider, model:$('ai-model').value, temperature:parseFloat($('ai-temp').value), api_key:key})
      });
      if(!r.ok) {
        const e = await r.json();
        alert('Save failed: ' + errDetail(e, r.status));
        return;
      }
      $('ai-key').value = '';
      alert('API key saved. CVs will now be parsed using Claude, OpenAI, Gemini, Groq, or Hugging Face. If the key is invalid or out of credits, parsing will fall back to the offline parser.');
      await loadConfig();
    } catch(e) {
      alert('Save failed: ' + e.message);
    } finally {
      $('ai-save-btn').disabled = false;
    }
  });

  // Clear key (switch to offline)
  $('ai-clear-btn').addEventListener('click', async () => {
    if (!confirm('Remove the API key and switch to offline parsing?')) return;
    $('ai-clear-btn').disabled = true;
    try {
      await fetch('/api/ai/config', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:_provider, model:$('ai-model').value, temperature:parseFloat($('ai-temp').value), api_key:''})
      });
      alert('API key removed. CVs will be parsed with the offline regex + spaCy parser.');
      await loadConfig();
    } catch(e) {
      alert('Clear failed: ' + e.message);
    } finally {
      $('ai-clear-btn').disabled = false;
    }
  });

  document.querySelector('.sb-item[data-page="admin"]')
    ?.addEventListener('click', () => { if(!_loaded) loadConfig(); loadFeature(); });

  // Initialize immediately so the page is functional without needing a sidebar click:
  // fill the model dropdown, set the default mode, and load the saved config +
  // the persisted feature toggle (server-side, so it survives restarts/reloads).
  fillModels();
  updateModeUI();
  loadConfig();
  loadFeature();
})();
