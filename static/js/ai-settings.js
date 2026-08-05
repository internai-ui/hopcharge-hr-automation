/* ───────── AI SETTINGS IN ADMIN PAGE ───────── */
(function () {
  const $ = id => document.getElementById(id);
  const MODELS = {
    huggingface: ['meta-llama/Llama-3.1-8B-Instruct', 'Qwen/Qwen2.5-Coder-32B-Instruct', 'deepseek-ai/DeepSeek-R1'],
    anthropic: ['claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest', 'claude-3-opus-latest'],
    openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
    gemini: ['gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-1.5-flash'],
    groq: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'gemma2-9b-it']
  };
  let _provider = 'huggingface';
  let _featureEnabled = false;

  function fillModels(sel) {
    const list = $('ai-model-list');
    if (!list) return;
    list.innerHTML = (MODELS[_provider] || []).map(m => `<option value="${m}">`).join('');
    const modelInput = $('ai-model');
    if (modelInput) {
      if (sel) modelInput.value = sel;
      else if (!modelInput.value && MODELS[_provider]) modelInput.value = MODELS[_provider][0];
    }
  }

  function updateFeatureUI() {
    const section = $('api-config-section');
    if (section) section.classList.toggle('hidden', !_featureEnabled);
  }

  const featureToggle = $('ai-feature-enable');
  if (featureToggle) {
    featureToggle.addEventListener('change', async () => {
      featureToggle.disabled = true;
      try {
        const r = await fetch('/api/ai/feature', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: featureToggle.checked }),
        });
        const d = await r.json();
        _featureEnabled = !!d.enabled;
        featureToggle.checked = _featureEnabled;
        updateFeatureUI();
        if (window.toast) toast(_featureEnabled ? 'AI CV Parsing enabled' : 'AI CV Parsing disabled — using regex+spaCy', 'ok');
      } catch (e) {
        featureToggle.checked = !featureToggle.checked;
        alert('Failed to update AI toggle: ' + e.message);
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
    loadFeature();
  }

  document.querySelectorAll('.ai-prov-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ai-prov-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _provider = btn.dataset.provider;
      if ($('ai-model')) $('ai-model').value = '';
      fillModels();
    });
  });

  const tempInput = $('ai-temp');
  if (tempInput) {
    tempInput.addEventListener('input', e => {
      const valEl = $('ai-temp-val');
      if (valEl) valEl.textContent = parseFloat(e.target.value).toFixed(2);
    });
  }

  async function loadConfig() {
    try {
      const d = await fetch('/api/ai/config').then(r => r.json());
      const c = d.config;
      if (c) {
        _provider = c.provider || 'huggingface';
        document.querySelectorAll('.ai-prov-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.provider === _provider));
        fillModels(c.model);
        if ($('ai-temp')) $('ai-temp').value = c.temperature;
        if ($('ai-temp-val')) $('ai-temp-val').textContent = (c.temperature || 0.2).toFixed(2);
      }
    } catch (e) { }
  }

  const saveBtn = $('ai-save-btn');
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const model = $('ai-model').value.trim();
      const apiKey = $('ai-key').value.trim();
      const temp = parseFloat($('ai-temp').value);
      const msg = $('ai-config-msg');
      if (!model) { alert('Enter a model name.'); return; }
      saveBtn.disabled = true;
      try {
        const r = await fetch('/api/ai/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: _provider, model, temperature: temp, api_key: apiKey }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Save failed');
        if (msg) msg.textContent = 'Settings saved successfully.';
        if (window.toast) toast('AI Settings saved', 'ok');
      } catch (e) {
        if (msg) msg.textContent = 'Save failed: ' + e.message;
        alert('Save failed: ' + e.message);
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  const testBtn = $('ai-test-btn');
  if (testBtn) {
    testBtn.addEventListener('click', async () => {
      const msg = $('ai-config-msg');
      if (msg) msg.textContent = 'Testing connection…';
      testBtn.disabled = true;
      try {
        const r = await fetch('/api/ai/test', { method: 'POST' });
        const d = await r.json();
        if (d.success) {
          if (msg) msg.textContent = '✓ Connection successful!';
          if (window.toast) toast('Connection test passed', 'ok');
        } else {
          if (msg) msg.textContent = '✕ Test failed: ' + (d.message || d.status);
        }
      } catch (e) {
        if (msg) msg.textContent = '✕ Test failed: ' + e.message;
      } finally {
        testBtn.disabled = false;
      }
    });
  }

  const clearBtn = $('ai-clear-btn');
  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      if (!confirm('Clear saved API key and use offline parsing?')) return;
      try {
        await fetch('/api/ai/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: _provider, model: $('ai-model').value || 'meta-llama/Llama-3.1-8B-Instruct', temperature: 0.2, api_key: '' }),
        });
        if ($('ai-key')) $('ai-key').value = '';
        if (window.toast) toast('API key cleared', 'ok');
      } catch (e) { alert('Failed to clear key: ' + e.message); }
    });
  }

  loadConfig();
})();
