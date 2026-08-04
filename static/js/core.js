/* Sidebar auth-check (was inline right after the sidebar markup) */
    /* Reveal the Account section only when sign-in is enabled; the Users link
       only for admins. Fails silently — the dashboard never depends on this. */
    fetch('/api/auth/me').then(function(r){ return r.ok ? r.json() : null; }).then(function(d){
      if (!d || d.auth === 'disabled' || !d.email) return;
      document.getElementById('sb-account').style.display = '';
      document.getElementById('sb-account-email').textContent = d.email;
      if (d.role === 'admin') document.getElementById('sb-users-link').style.display = '';
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

/* ───────── CURSOR GLOW (disabled — website has no cursor glow) ───────── */
const glow = document.getElementById('glow-dot');
if (glow) glow.style.display = 'none';
let mx=0,my=0,gx=0,gy=0;
document.addEventListener('mousemove', e => { mx=e.clientX; my=e.clientY; });
(function tick(){ gx+=(mx-gx)*0.12; gy+=(my-gy)*0.12; if(glow){glow.style.left=gx+'px'; glow.style.top=gy+'px';} requestAnimationFrame(tick); })();
document.addEventListener('mouseover', e => { if (glow && e.target.closest('button,a,label,input,.ccard,.sb-item,.wcard')) { glow.style.width=glow.style.height='42px'; } });
document.addEventListener('mouseout',  e => { if (glow && e.target.closest('button,a,label,input,.ccard,.sb-item,.wcard')) { glow.style.width=glow.style.height='22px'; } });

/* ───────── TOASTS ───────── */
const toastBox = document.getElementById('toast-box');
function toast(msg, type='inf', ms=3400) {
  const icon = {ok:'⚡',err:'✗',inf:'◈'}[type];
  const t = document.createElement('div'); t.className=`toast ${type}`;
  t.innerHTML = `<span class="toast-ic">${icon}</span><span>${msg}</span>`;
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

/* ───────── SIDEBAR / ROUTING ───────── */
const body = document.body;
const sidebarHotzone = document.getElementById('sidebar-hotzone');
const sidebarEl = document.getElementById('sidebar');
const scrim = document.getElementById('sidebar-scrim');
/* The sidebar sits off-screen (translateX(-100%)) when closed, but without
   `inert` its buttons still sat in the keyboard tab order — a keyboard-only
   user tabbing through the page would silently land on invisible, off-screen
   nav items with no visual indication. `inert` removes the whole subtree
   from both the tab order and the accessibility tree while closed. */
function openNav(){ body.classList.add('nav-open'); if (sidebarEl) sidebarEl.inert = false; }
function closeNav(){ body.classList.remove('nav-open'); if (sidebarEl) sidebarEl.inert = true; }

/* Hover-to-expand: cursor reaching the left edge opens the sidebar; moving the
   cursor beyond the sidebar's right edge retracts it after a short grace delay
   so a quick move or a slim gap doesn't make it flicker shut. No visible
   trigger button. */
let _navCloseTimer = null;
function _cancelClose(){ if (_navCloseTimer){ clearTimeout(_navCloseTimer); _navCloseTimer = null; } }
function _scheduleClose(){ _cancelClose(); _navCloseTimer = setTimeout(closeNav, 240); }

/* Only enable hover behaviour on devices that actually have a hover pointer
   (a mouse/trackpad) so touch devices aren't affected. */
const _hasHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;

if (sidebarHotzone) {
  sidebarHotzone.addEventListener('mouseenter', () => { if (_hasHover){ _cancelClose(); openNav(); } });
  /* Tap/click the edge still opens it (covers touch + non-hover fallbacks). */
  sidebarHotzone.addEventListener('click', openNav);
}
const sidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
if (sidebarToggleBtn) sidebarToggleBtn.addEventListener('click', openNav);

/* Reliable retract: while the sidebar is open, watch the pointer and close it
   once it moves past the sidebar's right edge (plus a small buffer). This works
   even if the cursor never lands on the sidebar itself (e.g. a fast diagonal
   move away), which a plain mouseleave can miss. */
if (_hasHover) {
  document.addEventListener('mousemove', (e) => {
    if (!body.classList.contains('nav-open')) return;
    const w = sidebarEl ? sidebarEl.getBoundingClientRect().right : 272;
    if (e.clientX > w + 40) _scheduleClose();
    else _cancelClose();
  });
}
if (sidebarEl) {
  sidebarEl.addEventListener('mouseenter', _cancelClose);
}
scrim.addEventListener('click', closeNav);
function goToPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.sb-item').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  window.scrollTo({ top:0, behavior:'auto' });
  closeNav();
  if (name === 'email') { refreshEmailCount(); if (window.refreshGmailStatus) window.refreshGmailStatus(); }
  if (name === 'forms' && window.refreshFormsOAuthStatus) window.refreshFormsOAuthStatus();
  if (name === 'replies' && window.loadRepliesPage) window.loadRepliesPage();
  if (name === 'admin' && window.loadAdminSettings) window.loadAdminSettings();
}
document.querySelectorAll('.sb-item[data-page]').forEach(b => b.addEventListener('click', () => goToPage(b.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => goToPage(b.dataset.goto)));

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
