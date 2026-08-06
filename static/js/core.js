/* Sidebar auth-check (was inline right after the sidebar markup) */
    /* Reveal the Account section only when sign-in is enabled.
       Fails silently - the dashboard never depends on this. */
    fetch('/api/auth/me').then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
      if (!d || d.auth === 'disabled' || !d.email) return;
      document.getElementById('sb-account').style.display = '';
      document.getElementById('sb-account-email').textContent = d.email;
    }).catch(function(){});

'use strict';

/* ───────── THEME TOGGLE (light / dark) ───────── */
(function(){
  const KEY = 'hopcharge-theme';
  function apply(theme){
    if (theme === 'dark') document.body.setAttribute('data-theme','dark');
    else document.body.removeAttribute('data-theme');
    const ic = document.getElementById('tt-ic');
    const lb = document.getElementById('tt-label');
    if (ic) ic.textContent = theme === 'dark' ? '☀' : '☾';
    if (lb) lb.textContent = theme === 'dark' ? 'Light' : 'Dark';
  }
  let saved = 'light';
  try { saved = localStorage.getItem(KEY) || 'light'; } catch(e){}
  apply(saved);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.addEventListener('click', ()=>{
    const next = document.body.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    apply(next);
    try { localStorage.setItem(KEY, next); } catch(e){}
  });
})();

/* ───────── PARTICLE FIELD ───────── */
(function(){
  const canvas = document.getElementById('particles');
  const ctx = canvas.getContext('2d');
  let W, H, parts = [];
  function resize(){ W = canvas.width = innerWidth; H = canvas.height = innerHeight; }
  function init(){ parts = []; const n = Math.min(80, Math.floor(W/20)); for (let i=0;i<n;i++) parts.push({ x:Math.random()*W, y:Math.random()*H, r:Math.random()*1.8+0.4, vx:(Math.random()-0.5)*0.14, vy:-(Math.random()*0.28+0.04), a:Math.random()*0.5+0.2, ph:Math.random()*Math.PI*2 }); }
  const COLORS = ['31,45,89','232,83,127','47,91,234'];
  // Particles disabled to match the website's clean static background.
  const PARTICLES_ON = false;
  function draw(t){
    ctx.clearRect(0,0,W,H);
    for (const p of parts){
      p.x += p.vx; p.y += p.vy;
      if (p.y < -6){ p.y = H+6; p.x = Math.random()*W; }
      if (p.x < -6) p.x = W+6; if (p.x > W+6) p.x = -6;
      const tw = 0.5 + 0.5*Math.sin(t*0.001 + p.ph);
      const c = COLORS[(p.ph*10|0) % COLORS.length];
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7);
      ctx.fillStyle = `rgba(${c},${(p.a*tw*0.7).toFixed(3)})`; ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  resize(); if (PARTICLES_ON){ init(); requestAnimationFrame(draw); }
  addEventListener('resize', () => { resize(); if (PARTICLES_ON) init(); });
})();

/* ───────── TOASTS ───────── */
const toastBox = document.getElementById('toast-box');
function toast(msg, type='inf', ms=3400) {
  const icons = {
    ok: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10.5l4 4 8-9"/></svg>',
    err: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>',
    inf: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="10" cy="10" r="7.3"/><path d="M10 9.2v4.4M10 6.4h.01"/></svg>',
  };
  const t = document.createElement('div'); t.className=`toast ${type}`;
  t.innerHTML = `<span class="toast-ic">${icons[type]}</span><span>${msg}</span>`;
  toastBox.appendChild(t);
  setTimeout(()=>{ t.style.animation='tout 280ms ease forwards'; setTimeout(()=>t.remove(),280); }, ms);
}

/* FastAPI's own HTTPException(detail="...") errors give a plain string, but
   its AUTO-GENERATED 422 request-validation errors give detail as an ARRAY
   of {loc, msg, type} objects -- `new Error(data.detail)` on that array
   stringifies to the useless "Error: [object Object]". This normalizes
   either shape into one readable string. */
function errDetail(data, fallback) {
  const d = data && data.detail;
  if (typeof d === 'string' && d) return d;
  if (Array.isArray(d) && d.length) {
    return d.map(e => (e && e.msg) ? e.msg : JSON.stringify(e)).join('; ');
  }
  if (typeof fallback === 'number') return `HTTP ${fallback}`;
  return fallback || 'Unknown error';
}

/* ── Required-field validation (shared across every form in the app) ──
   showFieldError/clearFieldError manage one red <div class="field-error">
   per input (inserted right after it). validateRequired runs a list of
   {input, message[, test]} checks -- test is an optional (value)=>boolean
   validator, defaulting to "non-empty" -- shows/clears errors, focuses +
   scrolls to the first invalid field, and returns true only if everything
   passed. Errors clear live as soon as the user provides a value, instead
   of only re-checking on the next submit attempt. */
function _fieldErrorEl(input) {
  let err = document.getElementById(input.id + '-error');
  if (!err) {
    err = document.createElement('div');
    err.className = 'field-error';
    err.id = input.id + '-error';
    input.insertAdjacentElement('afterend', err);
  }
  return err;
}
function showFieldError(input, message) {
  if (!input) return;
  input.classList.add('invalid');
  input.setAttribute('aria-invalid', 'true');
  const err = _fieldErrorEl(input);
  err.textContent = message;
  err.classList.add('show');
  input.setAttribute('aria-describedby', err.id);
}
function clearFieldError(input) {
  if (!input) return;
  input.classList.remove('invalid');
  input.removeAttribute('aria-invalid');
  const err = document.getElementById(input.id + '-error');
  if (err) { err.classList.remove('show'); err.textContent = ''; }
}
function attachLiveClear(input) {
  if (!input || input._liveClearAttached) return;
  input._liveClearAttached = true;
  input.addEventListener('input', () => { if (input.value.trim()) clearFieldError(input); });
}
function validateRequired(fields) {
  let firstInvalid = null;
  fields.forEach(({input, message, test}) => {
    if (!input) return;
    attachLiveClear(input);
    const val = (input.value || '').trim();
    const ok = test ? test(val) : !!val;
    if (!ok) {
      showFieldError(input, message);
      if (!firstInvalid) firstInvalid = input;
    } else {
      clearFieldError(input);
    }
  });
  if (firstInvalid) {
    firstInvalid.focus();
    firstInvalid.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return false;
  }
  return true;
}

/* ───────── SIDEBAR ───────── */
const body = document.body;
const sidebarEl = document.getElementById('sidebar');
const scrim = document.getElementById('sidebar-scrim');
/* The sidebar sits off-screen (translateX(-100%)) when closed, but without
   `inert` its buttons still sat in the keyboard tab order - a keyboard-only
   user tabbing through the page would silently land on invisible, off-screen
   nav items with no visual indication. `inert` removes the whole subtree
   from both the tab order and the accessibility tree while closed. */
function openNav(){ body.classList.add('nav-open'); if (sidebarEl) sidebarEl.inert = false; }
function closeNav(){ body.classList.remove('nav-open'); if (sidebarEl) sidebarEl.inert = true; }
function toggleNav(){ body.classList.contains('nav-open') ? closeNav() : openNav(); }

/* Sidebar only opens via this explicit button - no hover-to-expand. */
const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
if (sidebarToggleBtn) sidebarToggleBtn.addEventListener('click', toggleNav);
scrim.addEventListener('click', closeNav);

/* ───────── ROUTING ───────── */
const PAGE_NAMES = ['main','parser','email','replies','forms','rejected',
                     'accepted','analytics','colleges','employees','admin'];

function pathForPage(name) { return name === 'main' ? '/' : '/' + name; }
function pageForPath(path) {
  const clean = path.replace(/\/+$/, '') || '/';
  if (clean === '/' || clean === '') return 'main';
  const name = clean.slice(1);
  return PAGE_NAMES.includes(name) ? name : null;
}

/* activatePage() only flips DOM state - no history writes, so popstate (the
   browser back/forward buttons) can call it directly without creating new
   history entries. goToPage() is what every click handler calls; it also
   pushes a URL unless told not to. */
function activatePage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const section = document.getElementById('page-' + name);
  if (!section) return false;
  section.classList.add('active');
  document.querySelectorAll('.sb-item').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  window.scrollTo({ top:0, behavior:'auto' });
  closeNav();
  if (name === 'email' && window.refreshEmailCount) refreshEmailCount();
  if (name === 'replies' && window.loadRepliesPage) window.loadRepliesPage();
  if (name === 'admin' && window.loadAdminSettings) window.loadAdminSettings();
  if (name === 'employees' && window.loadEmployees) window.loadEmployees();
  if (window.refreshGoogleAuthWidgets) window.refreshGoogleAuthWidgets();
  return true;
}

function goToPage(name, opts) {
  opts = opts || {};
  if (!PAGE_NAMES.includes(name)) name = 'main';
  if (!activatePage(name)) return;
  const path = pathForPage(name);
  if (opts.replace) history.replaceState({ page: name }, '', path);
  else if (!opts.fromPopstate && location.pathname !== path) history.pushState({ page: name }, '', path);
}
window.goToPage = goToPage;

window.addEventListener('popstate', (e) => {
  activatePage((e.state && e.state.page) || pageForPath(location.pathname) || 'main');
});

document.querySelectorAll('.sb-item[data-page]').forEach(b => b.addEventListener('click', () => goToPage(b.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => goToPage(b.dataset.goto)));

/* Initial load: the server already served the right index.html for this URL
   (see app.py's per-page routes) - sync the DOM to it without pushing a
   redundant history entry. Deferred to DOMContentLoaded (already-fired case
   handled too) rather than running inline, so this always runs against a
   fully-parsed document regardless of where this script tag sits. */
function _initRoute() {
  goToPage(pageForPath(location.pathname) || 'main', { replace: true });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initRoute);
} else {
  _initRoute();
}

/* ───────── SCROLL-DRIVEN CHARGING ───────── */
const sceneWrap=document.getElementById('scene-wrap'), cable=document.getElementById('cable'), plug=document.getElementById('plug'),
      battFill=document.getElementById('batt-fill'), chargePct=document.getElementById('charge-pct'), chargeStat=document.getElementById('charge-status');
const CABLE_LEN=600, BATT_MAX_W=52;
function updateCharge() {
  if (!document.getElementById('page-main').classList.contains('active')) return;
  const rect = sceneWrap.getBoundingClientRect(), vh = window.innerHeight;
  let p = (vh - rect.top) / (vh * 0.75); p = Math.max(0, Math.min(1, p));
  const drawP = Math.min(p / 0.55, 1);
  cable.style.strokeDashoffset = (CABLE_LEN * (1 - drawP)).toFixed(1);
  plug.setAttribute('opacity', drawP > 0.85 ? '1' : (drawP*0.6).toFixed(2));
  const connected = drawP >= 1;
  sceneWrap.classList.toggle('connected', connected);
  let battP = p > 0.55 ? Math.min((p - 0.55)/0.45, 1) : 0;
  battFill.setAttribute('width', (BATT_MAX_W*battP).toFixed(1));
  const pct = Math.round(battP*100); chargePct.textContent = pct + '%';
  if (!connected) { chargeStat.textContent='Connecting…'; chargeStat.className='charge-status'; }
  else if (pct < 100) { chargeStat.textContent='Charging'; chargeStat.className='charge-status charging'; }
  else { chargeStat.textContent='Fully Charged'; chargeStat.className='charge-status full'; }
  battFill.setAttribute('fill', pct >= 100 ? '#2F5BEA' : '#2F5BEA');
}
window.addEventListener('scroll', updateCharge, { passive:true });
window.addEventListener('resize', updateCharge);
updateCharge();
