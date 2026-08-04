/* ───────── EMAIL REPLIES PAGE ───────── */
(function(){
  if (!document.getElementById('page-replies')) return;
  let _rows = [];

  function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  window.loadRepliesPage = async function(){
    const filter = document.getElementById('reply-filter').value || 'all';
    try{
      const res = await fetch(`/api/email-replies?status=${encodeURIComponent(filter)}`);
      const d = await res.json();
      _rows = d.replies || [];
      renderReplies(_rows, d.summary || {});
    }catch(e){ /* leave placeholder row */ }
  };

  function renderReplies(rows, summary){
    document.getElementById('reply-total').textContent = summary.total ?? rows.length;
    document.getElementById('reply-replied').textContent = summary.replied ?? rows.filter(r=>r.status==='replied').length;
    const unread = summary.unread ?? rows.filter(r=>r.status==='replied' && !r.read).length;
    document.getElementById('reply-unread').textContent = unread;
    const sbUnread = document.getElementById('sb-replies-unread');
    if (sbUnread) sbUnread.textContent = unread > 0 ? `${unread} unread` : 'Campaign reply inbox';

    const tbody = document.getElementById('reply-tbody');
    if (!rows.length){
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:34px;color:var(--text-dim)">No campaign emails tracked yet. Connect Gmail and send a campaign from the Send Emails tab.</td></tr>';
      return;
    }
    tbody.innerHTML = '';
    rows.forEach((r, i)=>{
      const when = r.received_at ? new Date(r.received_at).toLocaleString('en-IN',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})
                 : (r.sent_at ? new Date(r.sent_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '—');
      
      let statusBadge = '';
      if (r.status === 'replied') {
        if (r.intent) {
          statusBadge = `<span class="dec-badge" style="background:${r.intent.badge_bg};color:${r.intent.badge_color};font-weight:600;display:inline-flex;align-items:center;gap:4px">${r.intent.icon} ${esc(r.intent.label)}</span>`;
        } else {
          statusBadge = `<span class="dec-badge" style="background:${r.read?'rgba(96,165,250,.12)':'rgba(52,211,153,.15)'};color:${r.read?'#60a5fa':'#34d399'}">${r.read?'Replied':'● New reply'}</span>`;
        }
      } else {
        statusBadge = `<span class="dec-badge" style="background:rgba(255,255,255,.08);color:var(--text-dim)">Sent — no reply yet</span>`;
      }

      const snippetText = r.clean_snippet || r.clean_text || r.reply_snippet || '';

      const tr = document.createElement('tr');
      tr.style.animationDelay = `${i*25}ms`;
      tr.style.cursor = 'pointer';
      tr.onclick = ()=> openReplyModal(r.thread_id);
      tr.innerHTML = `
        <td style="color:var(--text-dim);font-family:'JetBrains Mono',monospace;font-size:10px">${i+1}</td>
        <td><div class="cand-name">${esc(r.candidate_name)||'Anonymous'}</div></td>
        <td class="td-email">${esc(r.candidate_email)||'<span style="opacity:.35">—</span>'}</td>
        <td>${statusBadge}</td>
        <td style="font-size:12px;color:var(--text-mid);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(snippetText)}">${esc(snippetText)||'<span style="opacity:.35">—</span>'}</td>
        <td style="font-size:10px;white-space:nowrap;color:var(--text-dim)">${when}</td>
        <td><button class="td-view-btn" onclick="event.stopPropagation();this.closest('tr').click()">VIEW</button></td>`;
      tbody.appendChild(tr);
    });
  }

  function _initials(name){
    const p = String(name||'').trim().split(/\s+/).filter(Boolean);
    return ((p[0]?.[0]||'') + (p[1]?.[0]||'')).toUpperCase() || '?';
  }

  window.openReplyModal = async function(threadId){
    if (!threadId) return;
    document.getElementById('reply-modal').classList.add('open');
    document.body.style.overflow = 'hidden';
    const body = document.getElementById('rm-body');
    body.innerHTML = '<div style="font-size:13px;color:var(--text-dim);padding:20px 0">Loading…</div>';
    try{
      const res = await fetch(`/api/email-replies/${encodeURIComponent(threadId)}`);
      const d = await res.json();
      if (!res.ok) throw new Error(errDetail(d, res.status));
      renderReplyModal(d.reply);
      if (d.reply.status === 'replied' && !d.reply.read) markReplyRead(threadId, true, /*silent*/true);
    }catch(e){
      body.innerHTML = `<div style="font-size:13px;color:#f87171;padding:20px 0">Could not load this reply: ${esc(e.message)}</div>`;
    }
  };

  function renderReplyModal(rec){
    const av = document.getElementById('rm-avatar');
    av.style.background = 'linear-gradient(135deg,#1F2D59,#2F5BEA)';
    av.textContent = _initials(rec.candidate_name);
    document.getElementById('rm-name').textContent = rec.candidate_name || 'Anonymous';
    const sentWhen = rec.sent_at ? new Date(rec.sent_at).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : '—';
    document.getElementById('rm-meta').textContent = `${rec.candidate_email || '—'}  ·  Campaign sent ${sentWhen}`;

    const body = document.getElementById('rm-body');
    body.innerHTML = '';

    if (rec.form_link){
      const fl = document.createElement('div');
      fl.className = 'cand-qa-item full';
      fl.innerHTML = `<div class="cand-qa-q">Form Link Sent</div><div class="cand-qa-a"><a href="${esc(rec.form_link)}" target="_blank" rel="noopener" style="color:#60a5fa;word-break:break-all">${esc(rec.form_link)}</a></div>`;
      body.appendChild(fl);
    }

    if (rec.status !== 'replied' || !rec.reply){
      const empty = document.createElement('div');
      empty.className = 'cand-qa-item full';
      empty.style.marginTop = '10px';
      empty.innerHTML = `<div class="cand-qa-q">Reply</div><div class="cand-qa-a empty">No reply yet — this candidate hasn't responded to the campaign email.</div>`;
      body.appendChild(empty);
      return;
    }

    const reply = rec.reply;
    const wrap = document.createElement('div');
    wrap.className = 'cand-qa-item full';
    wrap.style.cssText = 'margin-top:10px;border-top:1px solid var(--border-dim);padding-top:18px';
    const receivedWhen = reply.received_at ? new Date(reply.received_at).toLocaleString('en-IN',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    
    let intentHeader = '';
    if (reply.intent) {
      intentHeader = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
        <span style="font-size:11px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:var(--text-dim)">DECODED INTENT:</span>
        <span style="background:${reply.intent.badge_bg};color:${reply.intent.badge_color};padding:4px 10px;border-radius:16px;font-size:12px;font-weight:700;display:inline-flex;align-items:center;gap:6px">${reply.intent.icon} ${esc(reply.intent.label)}</span>
      </div>`;
    }

    wrap.innerHTML = `
      ${intentHeader}
      <div class="cand-qa-q">Candidate Response <span style="opacity:.55;font-weight:400">— from ${esc(reply.from||'')}, ${esc(receivedWhen)}</span></div>
    `;

    const cleanText = reply.clean_text || reply.body_text || reply.snippet || '';

    const contentBox = document.createElement('div');
    contentBox.style.cssText = 'margin-top:10px;background:var(--glass-2);border:1px solid var(--border-dim);border-left:3px solid ' + (reply.intent?.badge_color || '#60a5fa') + ';border-radius:8px;padding:14px 16px';
    contentBox.style.whiteSpace = 'pre-wrap';
    contentBox.style.fontSize = '13.5px';
    contentBox.style.lineHeight = '1.65';
    contentBox.style.color = 'var(--text-on)';
    contentBox.textContent = cleanText;
    wrap.appendChild(contentBox);



    body.appendChild(wrap);

    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;margin-top:16px';
    actions.innerHTML = `
      <a class="btn-ghost" id="rm-view-gmail" href="https://mail.google.com/mail/u/0/#all/${encodeURIComponent(rec.thread_id)}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-flex;align-items:center">↗ View in Gmail</a>
      <button class="btn-ghost" id="rm-toggle-read">${rec.read ? 'Mark as unread' : 'Mark as read'}</button>`;
    body.appendChild(actions);
    document.getElementById('rm-toggle-read').addEventListener('click', ()=> markReplyRead(rec.thread_id, !rec.read));
  }

  window.closeReplyModal = function(){
    document.getElementById('reply-modal').classList.remove('open');
    document.body.style.overflow = '';
  };

  window.markReplyRead = async function(threadId, read, silent){
    try{
      const res = await fetch(`/api/email-replies/${encodeURIComponent(threadId)}/mark-read?read=${read}`, {method:'POST'});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      if (!silent){ toast(read?'Marked as read':'Marked as unread', 'ok'); renderReplyModal(d.reply); }
      loadRepliesPage();
    }catch(e){ if(!silent) toast('Could not update: '+e.message, 'err'); }
  };

  document.getElementById('reply-check-now').addEventListener('click', async ()=>{
    const btn = document.getElementById('reply-check-now');
    btn.disabled = true; const orig = btn.textContent; btn.textContent = 'Checking…';
    try{
      const res = await fetch('/api/email-replies/check-now', {method:'POST'});
      const d = await res.json(); if(!res.ok) throw new Error(errDetail(d, res.status));
      if (d.skipped === 'not_connected'){
        toast('Connect Gmail first to check for replies.', 'inf');
      } else {
        toast(`Checked ${d.checked} thread${d.checked===1?'':'s'} — ${d.new_replies} new repl${d.new_replies===1?'y':'ies'}`, 'ok');
      }
      loadRepliesPage();
    }catch(e){ toast('Check failed: '+e.message, 'err'); }
    finally{ btn.disabled = false; btn.textContent = orig; }
  });

  document.getElementById('reply-filter').addEventListener('change', loadRepliesPage);

  const search = document.getElementById('reply-search');
  if (search) search.addEventListener('input', e=>{
    const q = e.target.value.toLowerCase();
    if (!q){ renderReplies(_rows, {}); return; }
    renderReplies(_rows.filter(r =>
      JSON.stringify([r.candidate_name, r.candidate_email, r.clean_text, r.reply_snippet, r.intent?.label]).toLowerCase().includes(q)), {});
  });

  document.querySelector('.sb-item[data-page="replies"]')
    ?.addEventListener('click', loadRepliesPage);
})();
