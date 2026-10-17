// ZerotheBot mobile web app. Plain JS, no build step. Talks to the same local service as the desktop app.
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const S = { commands: [], bots: [], groups: [], busy: {}, approvals: [], thread: null, threads: [], items: [], stream: {}, view: 'bots', es: null, lastEvent: 0, screenBot: null, screenTimer: null, titleBase: document.title, attach: [], rec: null, providers: [], templates: [] };

  // ------------------------------------------------------------------ api
  async function api(path, opts = {}) {
    const init = { credentials: 'same-origin', headers: {}, method: opts.method || 'GET' };
    if (opts.body !== undefined) { init.method = opts.method || 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    if (r.status === 401) { showLogin(); throw new Error('Not signed in'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || ('HTTP ' + r.status));
    return data;
  }

  async function apiForm(path, form) {
    const r = await fetch(path, { method: 'POST', credentials: 'same-origin', body: form });
    if (r.status === 401) { showLogin(); throw new Error('Not signed in'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || ('HTTP ' + r.status));
    return data;
  }

  // ---------------------------------------------------------------- markdown
  function md(src) {
    const blocks = [];
    let t = esc(src).replace(/```(\w*)\n([\s\S]*?)```/g, (_, l, code) => { blocks.push('<pre><code>' + code + '</code></pre>'); return '\u0000' + (blocks.length - 1) + '\u0000'; });
    const inline = (s) => s.replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<i>$2</i>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    const out = []; let list = null;
    const flush = () => { if (list) { out.push('</' + list + '>'); list = null; } };
    for (const line of t.split('\n')) {
      let m;
      if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== 'ul') { flush(); out.push('<ul>'); list = 'ul'; } out.push('<li>' + inline(m[1]) + '</li>'); }
      else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { flush(); out.push('<ol>'); list = 'ol'; } out.push('<li>' + inline(m[1]) + '</li>'); }
      else if ((m = line.match(/^#{1,4}\s+(.*)/))) { flush(); out.push('<p><b>' + inline(m[1]) + '</b></p>'); }
      else if (line.trim() === '') { flush(); }
      else { flush(); out.push('<p>' + inline(line) + '</p>'); }
    }
    flush();
    return out.join('').replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
  }

  // ------------------------------------------------------------------ views
  function showLogin() { $('#app').hidden = true; $('#login').hidden = false; if (S.es) { S.es.close(); S.es = null; } }
  function setView(v) {
    S.view = v;
    for (const id of ['bots', 'chat', 'approvals', 'settings', 'screen']) $('#view-' + id).hidden = id !== v;
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === v || (v === 'chat' && b.dataset.view === 'bots')));
    $('#tabs').hidden = v === 'screen';
    $('#back').hidden = !(v === 'chat');
    $('#title').textContent = { bots: 'Bots', approvals: 'Approvals', settings: 'Settings', screen: 'Browser' }[v] || $('#title').textContent;
    if (v === 'bots') renderBots(); if (v === 'approvals') renderApprovals(); if (v === 'settings') renderSettings();
  }
  function botById(id) { return S.bots.find((b) => b.id === id); }
  function stateOf(botId) {
    const b = S.busy[botId];
    if (!b) return ['idle', ''];
    if (b.status === 'waiting_approval') return ['wait', 'needs you'];
    return ['work', b.status === 'retrying' ? 'retrying' : 'working'];
  }

  function renderBots() {
    const el = $('#view-bots');
    const rows = S.bots.map((b) => {
      const [cls, label] = stateOf(b.id);
      const p = (S.providers || []).find((x) => x.id === b.profile);
      const brain = p ? `<div class="job">${esc(p.label || p.id)}${b.model ? ' · ' + esc(b.model) : ''}${p.key_set ? '' : ' · ⚠ no key'}</div>` : '';
      return `<div class="card bot-row" data-bot="${b.id}"><div class="emoji">${esc(b.emoji || '🤖')}</div><div class="meta"><div class="name">${esc(b.name)}</div>` +
        `<div class="job">${esc(b.job || 'No job yet')}</div>${brain}</div>${label ? `<span class="pill ${cls}">${label}</span>` : ''}<button class="small edit-bot" data-edit="${b.id}" aria-label="Edit Bot">⚙</button></div>`;
    }).join('');
    const groups = S.groups.map((g) => `<div class="card bot-row" data-group="${g.thread_id}"><div class="emoji">👥</div><div class="meta"><div class="name">${esc(g.name)}</div>` +
      `<div class="job">${esc(g.member_names.join(', '))}</div></div></div>`).join('');
    const tpl = (S.templates || []).map((t) => `<div class="card tpl-row" data-tpl="${esc(t.id)}"><div class="emoji">${esc(t.emoji || '✨')}</div><div class="meta">` +
      `<div class="name">${esc(t.name)}</div><div class="job">${esc(t.blurb || '')}</div></div>` +
      `<button class="small tpl-add" data-add="${esc(t.id)}">Add</button></div>`).join('');
    el.innerHTML = `<button id="new-bot" class="primary wide" type="button">＋ New Bot</button>` +
      `<div id="new-bot-form" class="card" hidden>${botFormHTML()}</div>` +
      (rows || '') + (groups ? '<h3 class="section">Group chats</h3>' + groups : '') +
      (tpl ? `<h3 class="section">Ready-made Bots</h3>${tpl}` : '') +
      (!rows && !tpl ? '<p class="muted">No Bots yet — add one below.</p>' : '');
    el.querySelectorAll('[data-bot]').forEach((n) => n.onclick = (e) => { if (e.target.closest('[data-edit]')) return; openBot(n.dataset.bot); });
    el.querySelectorAll('[data-edit]').forEach((b) => b.onclick = (e) => { e.stopPropagation(); openBotEdit(b.dataset.edit); });
    el.querySelectorAll('[data-group]').forEach((n) => n.onclick = () => openThread(n.dataset.group, S.groups.find((g) => g.thread_id === n.dataset.group).name, null));
    el.querySelectorAll('[data-add]').forEach((b) => b.onclick = async (e) => {
      const t = S.templates.find((x) => x.id === b.dataset.add);
      if (!t) return;
      b.disabled = true; b.textContent = 'Adding…';
      try { await api('/api/bots/from_template', { body: { template: t.id } }); await loadBootstrap(); }
      catch (err) { b.disabled = false; b.textContent = 'Add'; toast('Could not add', err.message); }
    });
    $('#new-bot').onclick = () => { const f = $('#new-bot-form'); f.hidden = !f.hidden; if (!f.hidden) { f.innerHTML = botFormHTML(); wireBotForm('#new-bot-form', null, async (body) => { const b = await api('/api/bots', { body }); await loadBootstrap(); openBot(b.id); }); $('#nb-name').focus(); } };
  }

  function botFormHTML(values = {}) {
    return `<input id="nb-name" placeholder="Bot name" maxlength="60" value="${esc(values.name || '')}">` +
      `<input id="nb-job" placeholder="Job (e.g. triage my inbox)" value="${esc(values.job || '')}" style="margin-top:8px">` +
      `<div class="field"><label>Brain (AI provider)</label>${providerOptions(values.profile)}</div>` +
      `<div class="field"><label>Model</label><div class="row" style="display:flex;gap:8px"><input id="nb-model" list="model-list" placeholder="Model" value="${esc(values.model || '')}" style="flex:1;min-width:0"><button type="button" id="nb-models" class="small">Find models</button></div>` +
      `<datalist id="model-list"></datalist></div>`;
  }

  function providerOptions(sel) {
    const opts = ['<option value="">No AI (local tools only)</option>']
      .concat((S.providers || []).map((p) => `<option value="${esc(p.id)}"${p.id === sel ? ' selected' : ''}>${esc(p.label || p.id)}${p.key_set ? ' ✓' : ' ⚠ no key'}</option>`));
    return `<select id="nb-provider">${opts.join('')}</select>`;
  }

  function wireBotForm(rootSel, botId, onSubmit) {
    const root = document.querySelector(rootSel);
    if (!root) return;
    const nameEl = root.querySelector('#nb-name');
    const jobEl = root.querySelector('#nb-job');
    const provEl = root.querySelector('#nb-provider');
    const modelEl = root.querySelector('#nb-model');
    const go = root.querySelector('#nb-go');
    const modelsBtn = root.querySelector('#nb-models');
    const fill = (pid) => {
      const p = (S.providers || []).find((x) => x.id === pid);
      if (p && !modelEl.value) modelEl.value = p.model || '';
    };
    if (provEl) provEl.onchange = () => fill(provEl.value);
    if (modelsBtn) modelsBtn.onclick = async () => {
      const pid = provEl ? provEl.value : '';
      if (!pid) { toast('Models', 'Pick a provider first.'); return; }
      modelsBtn.disabled = true; modelsBtn.textContent = '…';
      try {
        const d = await api('/api/providers/' + pid + '/test', { body: {} });
        const list = root.querySelector('#model-list');
        if (list) list.innerHTML = (d.models || []).map((m) => `<option value="${esc(m)}">`).join('');
        toast('Models', d.models && d.models.length ? d.models.length + ' found — tap the model field.' : 'No models listed; type one manually.');
      } catch (e) { toast('Could not list models', e.message); }
      modelsBtn.disabled = false; modelsBtn.textContent = 'Find models';
    };
    if (go) go.onclick = async () => {
      const name = nameEl ? nameEl.value.trim() : '';
      if (!name) { if (nameEl) nameEl.placeholder = 'Name is required'; return; }
      const body = { name, job: jobEl ? jobEl.value.trim() : '' };
      if (provEl && provEl.value) body.profile = provEl.value;
      if (modelEl && modelEl.value.trim()) body.model = modelEl.value.trim();
      await onSubmit(body);
    };
  }

  async function openBotEdit(botId) {
    let b;
    try { b = await api('/api/bots/' + botId); }
    catch (e) { toast('Could not open Bot', e.message); return; }
    const el = $('#view-bots');
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<b>Edit ${esc(b.name)}</b>${botFormHTML(b)}` +
      `<textarea id="eb-ins" rows="4" placeholder="Instructions (personality, rules…)" style="margin-top:8px">${esc(b.instructions || '')}</textarea>` +
      `<div class="row" style="display:flex;gap:8px;margin-top:10px"><button id="eb-go" class="primary">Save</button><button id="eb-x">Close</button></div>`;
    el.prepend(card);
    card.querySelector('#eb-x').onclick = renderBots;
    wireBotForm(card, b.id, async (body) => {
      body.instructions = card.querySelector('#eb-ins').value.trim();
      try {
        await api('/api/bots/' + botId, { method: 'PUT', body });
        await loadBootstrap();
      } catch (e) { toast('Could not save Bot', e.message); }
    });
  }

  async function openBot(botId) {
    const ths = await api('/api/bots/' + botId + '/threads');
    const bot = botById(botId);
    openThread(ths[0].id, (bot.emoji || '') + ' ' + bot.name, botId);
  }

  async function openThread(tid, title, botId) {
    const d = await api('/api/threads/' + tid);
    S.thread = { id: tid, title, botId, running: d.running.length > 0 };
    S.items = d.items; S.approvals = [...S.approvals.filter((a) => a.thread_id !== tid), ...d.approvals];
    setView('chat'); $('#title').textContent = title;
    S.attach = []; renderAttach();
    renderMessages(true); renderThreadApprovals(); updateChatStatus();
    loadThreadList(botId, tid);
  }

  async function loadThreadList(botId, current) {
    const bar = $('#threadbar'), sel = $('#thread-sel');
    if (!botId) { bar.hidden = true; return; }
    bar.hidden = false;
    try {
      S.threads = await api('/api/bots/' + botId + '/threads');
      sel.innerHTML = S.threads.map((t) => `<option value="${t.id}"${t.id === current ? ' selected' : ''}>${esc(t.title || 'Thread')}${t.main ? ' ★' : ''}</option>`).join('');
    } catch (e) { bar.hidden = true; }
  }

  function itemHTML(it) {
    if (it.type === 'user') return `<div class="msg user">${md(it.text)}${(it.images || []).map((i) => `<img src="/api/files/shots/${encodeURIComponent(i)}">`).join('')}</div>`;
    if (it.type === 'assistant') return `<div class="msg assistant">${S.thread && !S.thread.botId ? `<div class="who">${esc(it.emoji || '')} ${esc(it.name || '')}</div>` : ''}${md(it.text)}</div>`;
    if (it.type === 'tool') {
      const ic = { running: '●', ok: '✓', error: '✕', denied: '⛔', blocked: '⛔' }[it.status] || '•';
      return `<div class="tool ${it.status}"><span class="ic">${ic}</span><span>${esc(it.label || it.tool)}${(it.images || []).map((i) => `<img src="/api/files/shots/${encodeURIComponent(i)}">`).join('')}</span></div>`;
    }
    if (it.type === 'notice') return it.level === 'cmd' ? `<div class="notice cmd">${md(it.text)}</div>` : `<div class="notice ${esc(it.level)}">${esc(it.text)}</div>`;
    if (it.type === 'system_note') return `<div class="notice">${esc(it.text.slice(0, 200))}</div>`;
    return '';
  }

  function renderMessages(scroll) {
    const el = $('#messages');
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    let html = S.items.map(itemHTML).join('');
    for (const [sid, st] of Object.entries(S.stream)) if (st.thread === (S.thread && S.thread.id) && st.text) html += `<div class="msg assistant">${md(st.text)}</div>`;
    el.innerHTML = html;
    if (scroll || atEnd) el.scrollTop = el.scrollHeight;
  }

  function mergeItems(items) {
    for (const it of items) {
      if (it.type === 'tool') {
        const cur = S.items.find((x) => x.type === 'tool' && x.call_id === it.call_id);
        if (cur) { Object.assign(cur, it.partial ? { status: it.status, summary: it.summary, images: it.images, duration_ms: it.duration_ms } : it); continue; }
      }
      if (it.type === 'assistant' && it.stream_id) delete S.stream[it.stream_id];
      if (!S.items.find((x) => x.id === it.id && x.type === it.type && it.type !== 'tool')) S.items.push(it);
    }
  }

  function updateChatStatus() {
    const el = $('#chat-status');
    const run = S.thread && Object.values(S.busy).find((r) => r.thread_id === S.thread.id);
    $('#stop').hidden = !run;
    el.hidden = !run;
    if (run) el.textContent = ({ waiting_approval: 'Waiting for your approval…', waiting_screen: 'Waiting for its screen…', retrying: 'Retrying after a hiccup…' }[run.status]) || `Working… step ${run.steps || 1}`;
  }

  function approvalHTML(a) {
    const d = a.details || {};
    if (a.category === 'question') {
      const opts = (d.options || []).map((o) => `<button data-ans="${esc(o)}">${esc(o)}</button>`).join('');
      return `<div class="card approval q" data-id="${a.id}"><h4>${esc(botName(a.bot_id))} asks</h4><div>${esc(a.summary)}</div><div class="row">${opts}</div>` +
        `<div class="row"><input placeholder="Type an answer" class="ans"><button class="primary send-ans">Send</button></div></div>`;
    }
    if (a.category === 'login' && Array.isArray(d.fields) && d.fields.length) {
      return `<div class="card approval" data-id="${a.id}"><h4>${esc(botName(a.bot_id))} needs the login for ${esc(d.domain || 'this site')}</h4><div>${esc(a.summary)}</div>` +
        `<p class="muted">Paste from any password manager — it goes into the Windows Credential Manager and straight into the page, never into chat history.</p>` +
        `<div class="row"><input class="cu" placeholder="Username or email" value="${esc(d.username_hint || '')}">` +
        `<input class="cp" type="password" placeholder="Password"></div>` +
        `<div class="row"><label><input type="checkbox" class="cr" checked> Remember on this PC</label></div>` +
        `<div class="row"><button class="primary send-creds">Fill and approve</button>` +
        `<button class="open-screen" data-bot="${a.bot_id}">Open browser</button><button class="deny">Cancel</button></div></div>`;
    }
    if (a.category === 'takeover' || a.category === 'login') {
      return `<div class="card approval" data-id="${a.id}"><h4>${esc(botName(a.bot_id))} needs you at the browser</h4><div>${esc(a.summary)}</div>` +
        `<div class="row"><button class="primary open-screen" data-bot="${a.bot_id}">Open browser</button><button class="approve">I'm done</button><button class="deny">Can't do it</button></div></div>`;
    }
    const taint = a.tainted ? '<p class="err">⚠ Earlier content in this task contained instruction-like text. Be extra careful.</p>' : '';
    const extra = d.body || d.command || d.preview || d.text || '';
    return `<div class="card approval" data-id="${a.id}"><h4>${esc(botName(a.bot_id))}: ${esc(a.title || a.category)}</h4><div>${esc(a.summary)}</div>${taint}` +
      (extra ? `<pre>${esc(String(extra).slice(0, 800))}</pre>` : '') + (d.auto_review ? `<p class="muted">${esc(d.auto_review)}</p>` : '') +
      `<div class="row"><button class="primary approve">Approve</button><button class="remember">Approve &amp; always</button><button class="full">Full access</button><button class="deny">Deny</button></div></div>`;
  }
  function botName(id) { const b = botById(id); return b ? b.name : 'Bot'; }

  function wireApprovals(root) {
    root.querySelectorAll('.approval').forEach((card) => {
      const id = card.dataset.id;
      const decide = (body) => api('/api/approvals/' + id + '/decide', { body }).then(refreshApprovals).catch((e) => toast('Error', e.message));
      const q = (s) => card.querySelector(s);
      if (q('.approve')) q('.approve').onclick = () => decide({ approve: true });
      if (q('.remember')) q('.remember').onclick = () => decide({ approve: true, remember: true });
      if (q('.deny')) q('.deny').onclick = () => decide({ approve: false });
      if (q('.full')) q('.full').onclick = () => { if (confirm('Let this Bot do anything on your behalf between now and its next reply?')) decide({ approve: true, full_access: true }); };
      card.querySelectorAll('[data-ans]').forEach((b) => b.onclick = () => decide({ approve: true, answer: b.dataset.ans }));
      if (q('.send-ans')) q('.send-ans').onclick = () => { const v = q('.ans').value.trim(); if (v) decide({ approve: true, answer: v }); };
      if (q('.send-creds')) q('.send-creds').onclick = () => {
        const pw = q('.cp').value;
        if (!pw) { q('.cp').placeholder = 'Password required'; return; }
        q('.cp').value = '';
        decide({ approve: true, answer: JSON.stringify({ username: (q('.cu').value || '').trim(), password: pw, remember: q('.cr').checked }) });
      };
      if (q('.open-screen')) q('.open-screen').onclick = () => openScreen(q('.open-screen').dataset.bot);
    });
  }
  function renderThreadApprovals() {
    const el = $('#chat-approvals');
    const mine = S.approvals.filter((a) => S.thread && a.thread_id === S.thread.id && a.status === 'pending');
    el.innerHTML = mine.map(approvalHTML).join('');
    wireApprovals(el);
  }
  function renderApprovals() {
    const el = $('#view-approvals');
    const pend = S.approvals.filter((a) => a.status === 'pending');
    el.innerHTML = pend.length ? pend.map(approvalHTML).join('') : '<p class="muted">Nothing needs your attention. Your Bots will come back here when they need an approval.</p>';
    wireApprovals(el);
  }
  async function refreshApprovals() {
    S.approvals = await api('/api/approvals?status=pending');
    updateBadge(); if (S.view === 'approvals') renderApprovals(); if (S.view === 'chat') renderThreadApprovals();
  }
  function updateBadge() {
    const n = S.approvals.filter((a) => a.status === 'pending').length;
    $('#badge').hidden = !n; $('#badge').textContent = n;
    document.title = (n ? `(${n}) ` : '') + S.titleBase;
  }

  async function renderSettings() {
    const el = $('#view-settings');
    const perm = 'Notification' in window ? Notification.permission : 'unsupported';
    el.innerHTML = `<div class="card"><b>AI keys</b><p class="muted">Your Bots think through these providers. Keys stay on your server, never on this phone.</p>` +
      ['openai', 'anthropic', 'groq', 'openrouter'].map((p) => `<div class="row" style="display:flex;gap:8px;margin-top:8px"><input id="key-${p}" type="password" placeholder="${p} API key" autocomplete="off" style="flex:1;min-width:0"><button data-key="${p}" class="small key-save">Save</button></div>`).join('') + `</div>` +
      `<div class="card"><b>Notifications</b><p class="muted">Alerts appear here while this page is open.${window.isSecureContext ? '' : ' System notifications need HTTPS.'}</p>` +
      `<p>System notifications: <b>${perm}</b></p>${perm === 'default' ? '<button id="ask-notif" class="primary">Enable</button>' : ''}</div>` +
      `<div class="card"><b>Install</b><p class="muted">Use your browser menu: “Add to Home screen” for the fullscreen app icon.</p></div>` +
      `<div class="card"><button id="logout">Sign out of this phone</button></div>`;
    if ($('#ask-notif')) $('#ask-notif').onclick = async () => { await Notification.requestPermission(); renderSettings(); };
    el.querySelectorAll('.key-save').forEach((b) => b.onclick = async () => {
      const p = b.dataset.key, v = $('#key-' + p).value.trim();
      if (!v) { toast('AI keys', 'Paste a key first.'); return; }
      try { await api('/api/secrets/provider:' + p, { method: 'PUT', body: { value: v } }); $('#key-' + p).value = ''; toast('Saved', p + ' key is on your server.'); }
      catch (e) { toast('Could not save key', e.message); }
    });
    $('#logout').onclick = async () => { try { await api('/api/logout', { body: {} }); } catch (e) { /* ignore */ } document.cookie = 'zt_session=; Max-Age=0; path=/'; location.reload(); };
  }

  // ----------------------------------------------------------------- toasts
  function toast(title, body, onclick) {
    const el = document.createElement('div'); el.className = 't'; el.innerHTML = `<b>${esc(title)}</b>${esc(body || '')}`;
    if (onclick) el.onclick = onclick;
    $('#toast').appendChild(el); setTimeout(() => el.remove(), 7000);
  }
  function systemNotify(n) {
    if (navigator.vibrate) navigator.vibrate(n.urgent ? [120, 60, 120] : 60);
    if (document.visibilityState === 'visible') return;
    if ('Notification' in window && Notification.permission === 'granted') {
      navigator.serviceWorker?.getRegistration().then((reg) => (reg ? reg.showNotification(n.title, { body: n.body, tag: n.kind, icon: '/icon.svg' }) : new Notification(n.title, { body: n.body })))
        .catch(() => { try { new Notification(n.title, { body: n.body }); } catch (e) { /* ignore */ } });
    }
  }

  // ----------------------------------------------------------------- events
  function connect() {
    if (S.es) S.es.close();
    const es = new EventSource('/api/events?kind=mobile' + (S.lastEvent ? '&since=' + S.lastEvent : ''));
    S.es = es;
    es.onopen = () => $('#conn').classList.remove('off');
    es.onerror = () => $('#conn').classList.add('off');
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  }
  function onEvent(ev) {
    S.lastEvent = Math.max(S.lastEvent, ev.id || 0);
    switch (ev.type) {
      case 'message':
        if (S.thread && ev.thread_id === S.thread.id) { mergeItems(ev.items); renderMessages(); }
        break;
      case 'delta': {
        const st = S.stream[ev.stream_id] || (S.stream[ev.stream_id] = { text: '', thread: ev.thread_id });
        st.text += ev.text;
        if (S.thread && ev.thread_id === S.thread.id) renderMessages();
        break;
      }
      case 'delta_reset': delete S.stream[ev.stream_id]; if (S.thread && ev.thread_id === S.thread.id) renderMessages(); break;
      case 'turn':
        if (['done', 'stopped', 'error', 'limit', 'skipped'].includes(ev.status)) delete S.busy[ev.bot_id];
        else S.busy[ev.bot_id] = { thread_id: ev.thread_id, status: ev.status, steps: ev.steps, turn_id: ev.turn_id };
        if (S.view === 'bots') renderBots(); updateChatStatus();
        break;
      case 'approval': refreshApprovals(); break;
      case 'bots': case 'groups': loadBootstrap(); break;
      case 'notification':
        if (ev.muted) break;   // quiet hours / Do Not Disturb: the Inbox still has it
        toast(ev.title, ev.body, () => { if (ev.thread_id) openThread(ev.thread_id, ev.bot_name || 'Thread', null); });
        systemNotify(ev); break;
      default: break;
    }
  }

  async function loadBootstrap() {
    const d = await api('/api/bootstrap');
    S.bots = d.bots; S.groups = d.groups; S.approvals = d.approvals; S.busy = {};
    for (const [bid, r] of Object.entries(d.status.busy || {})) S.busy[bid] = r;
    S.lastEvent = Math.max(S.lastEvent, d.last_event || 0);
    updateBadge(); if (S.view === 'bots') renderBots();
    $('#login').hidden = true; $('#app').hidden = false;
    api('/api/commands').then((c) => { S.commands = c; }).catch(() => {});
    api('/api/providers').then((p) => { S.providers = p; if (S.view === 'bots') renderBots(); }).catch(() => {});
    api('/api/templates').then((t) => { S.templates = t; if (S.view === 'bots') renderBots(); }).catch(() => {});
    api('/api/settings').then((s) => { S.settings = s; $('#google-btn').hidden = !(s.google_client_id || ''); }).catch(() => {});
  }

  // ------------------------------------------------------------------ chat
  async function send() {
    const ta = $('#input'); const text = ta.value.trim();
    if ((!text && !S.attach.length) || !S.thread) return;
    ta.value = ''; autoGrow(); showCommands();
    const images = S.attach.map((a) => ({ data: a.data }));
    S.attach = []; renderAttach();
    try {
      const d = await api('/api/threads/' + S.thread.id + '/messages', { body: { text, images } });
      if (d.switch_thread && S.thread && S.thread.botId) openThread(d.switch_thread, S.thread.title, S.thread.botId);   // /new
    } catch (e) { toast('Could not send', e.message); ta.value = text; }
  }

  function renderAttach() {
    const bar = $('#attachbar');
    bar.hidden = !S.attach.length;
    bar.innerHTML = S.attach.map((a, i) => `<span class="chip"><img src="${a.url}"><button data-i="${i}" aria-label="Remove">✕</button></span>`).join('');
    bar.querySelectorAll('button').forEach((b) => b.onclick = () => { S.attach.splice(+b.dataset.i, 1); renderAttach(); });
  }

  function downscale(file) {
    // Shrink phone photos so uploads stay small; returns {data (base64), url (preview)}.
    return new Promise((resolve, reject) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        const max = 1280, k = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        const durl = c.toDataURL('image/jpeg', 0.82);
        resolve({ data: durl.split(',')[1], url: durl });
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read that image.')); };
      img.src = url;
    });
  }

  async function toggleMic() {
    const btn = $('#mic');
    if (S.rec) { S.rec.stop(); return; }   // stop() fires onstop below
    if (!window.MediaRecorder) { toast('Voice', 'This browser cannot record audio.'); return; }
    if (!S.thread || !S.thread.botId) { toast('Voice', 'Open a Bot chat first.'); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      const chunks = [];
      mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      mr.onstop = async () => {
        S.rec = null; btn.classList.remove('rec');
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: mr.mimeType || 'audio/webm' });
        if (!blob.size) return;
        btn.disabled = true;
        try {
          const form = new FormData();
          form.append('file', blob, 'voice.' + (blob.type.includes('webm') ? 'webm' : 'bin'));
          const d = await apiForm('/api/voice/transcribe?bot_id=' + encodeURIComponent(S.thread.botId), form);
          if (d.text) { const ta = $('#input'); ta.value = (ta.value ? ta.value + ' ' : '') + d.text; autoGrow(); ta.focus(); }
        } catch (e) { toast('Voice', e.message); }
        btn.disabled = false;
      };
      S.rec = mr; mr.start(); btn.classList.add('rec');
      toast('Recording…', 'Tap 🎙 again to stop.');
    } catch (e) { toast('Voice', 'Microphone blocked. Allow access and try again.'); }
  }

  // ----------------------------------------------------------- slash commands
  function showCommands() {
    const box = $('#cmdlist'); const ta = $('#input');
    const m = /^\/([\w-]*)$/.exec(ta.value);
    if (!m || !S.commands.length) { box.hidden = true; return; }
    const q = m[1].toLowerCase(); const inGroup = !(S.thread && S.thread.botId);
    const rows = S.commands.filter((c) => !(inGroup && c.bot_only)).map((c) => {
      const names = [c.name, ...c.aliases];
      return [names.some((n) => n.startsWith(q)) ? 0 : (q && names.some((n) => n.includes(q)) ? 1 : 2), c];
    }).filter((r) => r[0] < 2).sort((a, b) => a[0] - b[0]).map((r) => r[1]).slice(0, 8);
    if (!rows.length) { box.hidden = true; return; }
    box.innerHTML = rows.map((c, i) => `<button type="button" data-cmd="${esc(c.name)}" class="${i === 0 ? 'sel' : ''}"><b>${esc(c.usage)}</b><span>${esc(c.summary)}</span></button>`).join('');
    box.hidden = false;
    box.querySelectorAll('button').forEach((b) => b.onclick = () => { ta.value = '/' + b.dataset.cmd + ' '; box.hidden = true; ta.focus(); autoGrow(); });
  }
  function autoGrow() { const ta = $('#input'); ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, window.innerHeight * 0.4) + 'px'; }

  // ------------------------------------------------- take over (remote view)
  async function openScreen(botId) {
    S.screenBot = botId; setView('screen');
    try { await api('/api/bots/' + botId + '/takeover', { body: { action: 'start' } }); } catch (e) { toast('Browser', e.message); }
    const img = $('#scr-img');
    const tick = () => { img.src = '/api/bots/' + botId + '/screen.jpg?q=55&t=' + Date.now(); };
    img.onload = () => { S.screenTimer = setTimeout(tick, 700); }; img.onerror = () => { S.screenTimer = setTimeout(tick, 2500); };
    tick();
  }
  async function closeScreen(handBack) {
    clearTimeout(S.screenTimer); const id = S.screenBot; S.screenBot = null;
    if (handBack && id) { try { await api('/api/bots/' + id + '/takeover', { body: { action: 'handback' } }); toast('Handed back', 'The Bot will continue.'); } catch (e) { toast('Error', e.message); } }
    if (S.thread) setView('chat'); else setView('bots');
  }
  const input = (ev) => api('/api/bots/' + S.screenBot + '/screen/input', { body: ev }).catch((e) => toast('Browser', e.message));

  function setAuthMode(signup) {
    S.signupMode = signup;
    $('#login-sub').textContent = signup ? 'Create your account.' : 'Sign in to your app.';
    $('#email').placeholder = 'Email';
    $('#token').placeholder = signup ? 'Choose a password (8+ characters)' : 'Password';
    $('#token2').hidden = !signup;
    $('#login-btn').textContent = signup ? 'Create account' : 'Sign in';
    $('#signup-toggle').innerHTML = signup ? 'Already have an account? <a href="#" id="to-signup">Sign in</a>' : 'New here? <a href="#" id="to-signup">Create an account</a>';
    $('#login-err').textContent = '';
    const t = $('#to-signup'); if (t) t.onclick = (e) => { e.preventDefault(); setAuthMode(!signup); };
  }

  async function signInWithGoogle() {
    const err = $('#login-err');
    const clientId = (S.settings && S.settings.google_client_id) || '';
    if (!clientId) { err.textContent = 'Google sign-in is not set up yet — sign in with email instead.'; return; }
    err.textContent = '';
    try {
      if (!window.google || !window.google.accounts) {
        await new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = 'https://accounts.google.com/gsi/client';
          s.onload = res; s.onerror = () => rej(new Error('Could not load Google sign-in.'));
          document.head.appendChild(s);
        });
      }
      const resp = await new Promise((res, rej) => {
        window.google.accounts.id.initialize({ client_id: clientId, callback: res, auto_select: false });
        window.google.accounts.id.prompt((n) => { if (n && n.isNotDisplayed && n.isNotDisplayed()) rej(new Error('Google sign-in was dismissed.')); });
      });
      const d = await api('/api/google', { body: { id_token: resp.credential } });
      await loadBootstrap(); connect(); setView('bots');
      toast('Signed in', d.email || 'Welcome back.');
    } catch (e) { err.textContent = e.message; }
  }

  function wire() {
    const go = async () => {
      const email = $('#email').value.trim();
      const pw = $('#token').value;
      const err = $('#login-err');
      err.textContent = '';
      if (!email || !pw) { err.textContent = 'Enter your email and password.'; return; }
      try {
        if (S.signupMode) {
          if (pw !== $('#token2').value) { err.textContent = 'Passwords do not match.'; return; }
          await api('/api/signup', { body: { email, password: pw } });
        } else {
          await api('/api/login', { body: { email, password: pw } });
        }
        await loadBootstrap(); connect(); setView('bots');
      } catch (e) { err.textContent = e.message; }
    };
    $('#login-btn').onclick = go;
    for (const id of ['email', 'token']) $('#' + id).addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    $('#to-signup').onclick = (e) => { e.preventDefault(); setAuthMode(!S.signupMode); };
    $('#google-btn').onclick = signInWithGoogle;
    $('#token').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#login-btn').click(); });
    document.querySelectorAll('#tabs button').forEach((b) => b.onclick = () => setView(b.dataset.view));
    $('#back').onclick = () => { S.thread = null; setView('bots'); };
    $('#composer').onsubmit = (e) => { e.preventDefault(); send(); };
    $('#input').addEventListener('input', () => { autoGrow(); showCommands(); });
    $('#input').addEventListener('keydown', (e) => { const b = $('#cmdlist'); if (e.key === 'Tab' && !b.hidden) { e.preventDefault(); b.querySelector('button').click(); } if (e.key === 'Escape') b.hidden = true; });
    $('#stop').onclick = () => S.thread && api('/api/threads/' + S.thread.id + '/stop', { body: {} });
    $('#thread-sel').onchange = (e) => { const t = S.threads.find((x) => x.id === e.target.value); if (t && S.thread) openThread(t.id, S.thread.title, S.thread.botId); };
    $('#thread-new').onclick = async () => {
      if (!S.thread || !S.thread.botId) return;
      try { const t = await api('/api/bots/' + S.thread.botId + '/threads', { body: {} }); openThread(t.id, S.thread.title, S.thread.botId); }
      catch (e) { toast('Could not start chat', e.message); }
    };
    $('#attach').onclick = () => $('#file').click();
    $('#file').onchange = async (e) => {
      for (const f of [...e.target.files].slice(0, 6 - S.attach.length)) {
        try { S.attach.push(await downscale(f)); } catch (err) { toast('Image', err.message); }
      }
      e.target.value = ''; renderAttach();
    };
    $('#mic').onclick = toggleMic;
    $('#scr-done').onclick = () => closeScreen(true);
    $('#scr-back').onclick = () => closeScreen(false);
    $('#scr-reload').onclick = () => input({ type: 'reload' });
    $('#scr-go').onclick = () => { const u = $('#scr-url').value.trim(); if (u) input({ type: 'navigate', url: u }); };
    $('#scr-type').onclick = () => { const t = $('#scr-text').value; if (t) { input({ type: 'text', text: t }); $('#scr-text').value = ''; } };
    $('#scr-enter').onclick = () => input({ type: 'key', key: 'Enter' });
    $('#scr-up').onclick = () => input({ type: 'scroll', dy: -500 });
    $('#scr-down').onclick = () => input({ type: 'scroll', dy: 500 });
    $('#scr-img').addEventListener('click', (e) => {
      const img = e.currentTarget; const r = img.getBoundingClientRect();
      input({ type: 'click', x: Math.round((e.clientX - r.left) * (1280 / r.width)), y: Math.round((e.clientY - r.top) * (800 / r.height)) });
    });
  }

  async function start() {
    wire();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    try {
      const st = await api('/api/auth_status');
      S.settings = st.settings || {};
      if (st.needs_setup) { setAuthMode(true); showLogin(); return; }
      try { await loadBootstrap(); connect(); setView('bots'); return; } catch (e) { /* fall through to sign-in */ }
      showLogin();
    } catch (e) { toast('Could not reach the server', 'Check your connection and reopen the app.'); }
    setInterval(() => { if (S.view === 'chat') updateChatStatus(); }, 1500);
  }
  start();
})();
