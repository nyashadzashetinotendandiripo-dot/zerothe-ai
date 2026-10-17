// ZerotheBot mobile web app. Plain JS, no build step. Talks to the same local service as the desktop app.
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const S = { commands: [], bots: [], groups: [], busy: {}, approvals: [], thread: null, threads: [], items: [], stream: {}, view: 'home', es: null, lastEvent: 0, screenBot: null, screenTimer: null, titleBase: document.title, attach: [], rec: null, providers: [], templates: [], loaded: new Set(), mk: [], ib: { pend: [], hist: [], handoffs: [] }, cp: { cards: [], term: [], headless: false }, fl: { entries: [], mode: 'recent', cwd: '', current: null }, sk: { skills: [], recs: [], cur: '' }, rt: { rows: [], runs: [], sel: null }, pl: { plugins: [], catalog: [], mcp: [] }, us: {}, al: [] };

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
    const views = ['home', 'bots', 'chat', 'inbox', 'settings', 'marketplace', 'computer', 'files', 'skills', 'routines', 'plugins', 'usage', 'actionlog', 'screen'];
    for (const id of views) $('#view-' + id).hidden = id !== v;
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === v || (v === 'chat' && b.dataset.view === 'bots')));
    $('#tabs').hidden = v === 'screen';
    $('#back').hidden = !(v === 'chat');
    $('#title').textContent = { home: 'Home', bots: 'Bots', chat: S.thread && S.thread.title || 'Chat', inbox: 'Inbox', settings: 'Settings', marketplace: 'Marketplace', computer: 'Computer', files: 'Files', skills: 'Skills', routines: 'Routines', plugins: 'Plugins', usage: 'Usage', actionlog: 'Action log', screen: 'Browser' }[v] || $('#title').textContent;
    if (v === 'home') renderHome();
    if (v === 'bots') renderBots();
    if (v === 'chat') { renderMessages(true); updateChatStatus(); }
    if (v === 'inbox') renderInbox();
    if (v === 'settings') renderSettings();
    if (v === 'marketplace') renderMarketplace();
    if (v === 'computer') renderComputer();
    if (v === 'files') renderFiles();
    if (v === 'skills') renderSkills();
    if (v === 'routines') renderRoutines();
    if (v === 'plugins') renderPlugins();
    if (v === 'usage') renderUsage();
    if (v === 'actionlog') renderActionLog();
  }
  window.setView = setView;
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
      `<div class="card"><b>More screens</b><div class="row" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">` +
      ['marketplace','computer','files','skills','routines','plugins','usage','actionlog'].map((v) => `<button onclick="setView('${v}')" class="small" style="flex:1 1 48%">${v[0].toUpperCase() + v.slice(1)}</button>`).join('') + `</div></div>` +
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

  // ---------------------------------------------------------------- home
  async function renderHome() {
    const wrap = $('#view-home'); wrap.innerHTML = '<div class="section">Today</div><div class="home-banner"><b>●</b> <span id="home-summary">Loading…</span></div>' +
      '<div class="home-grid">' +
      '<div class="home-tile" id="t-need"><div class="cap">Needs you</div><div class="val" id="v-need">–</div><div class="sub" id="s-need">–</div></div>' +
      '<div class="home-tile" id="t-work"><div class="cap">Working now</div><div class="val" id="v-work">–</div><div class="sub" id="s-work">–</div></div>' +
      '<div class="home-tile" id="t-tok"><div class="cap">Tokens this week</div><div class="val" id="v-tok">–</div><div class="sub" id="s-tok">–</div></div>' +
      '</div><div class="section">Your team</div><div class="team-grid" id="home-team"></div>' +
      '<div class="section">Needs you</div><div id="home-need"></div>' +
      '<div class="section">Recent activity</div><div id="home-feed"></div>';
    const load = async () => {
      try {
        const [us, feed, digest] = await Promise.all([api('/api/usage'), api('/api/actions?limit=8'), api('/api/digest?spec=today')]);
        S.us = us; S.al = (feed || []);
        const today = us.today || {}; let lim = us.limit || 0, tot = us.total || 0;
        $('#v-tok').textContent = (tot || 0).toLocaleString(); $('#s-tok').textContent = lim ? Math.round(tot / lim * 100) + '% of ' + lim.toLocaleString() + ' limit' : 'no weekly limit';
        $('#v-need').textContent = (S.approvals.filter((a) => a.status === 'pending').length || 0).toLocaleString(); $('#s-need').textContent = 'open the inbox';
        const working = (S.bots || []).filter((b) => { const [k] = stateOf(b.id); return k === 'work' || k === 'takeover'; });
        $('#v-work').textContent = working.length.toLocaleString(); $('#s-work').textContent = working.map((b) => b.name).join(', ') || 'nobody is busy';
        $('#home-summary').textContent = digest.headline || 'All quiet — no approvals waiting.';
        const tneed = document.createDocumentFragment();
        (S.approvals.filter((a) => a.status === 'pending')).slice(0, 4).forEach((a) => {
          const c = document.createElement('div'); c.className = 'approval-card';
          c.innerHTML = '<div class="summary">' + esc(a.summary || '') + '</div><div class="meta">' + esc(botName(a.bot_id)) + ' · ' + esc(a.category || '') + '</div>';
          c.onclick = () => { if (a.thread_id) openThread(a.thread_id, esc(a.title || 'Thread'), a.bot_id); };
          tneed.appendChild(c);
        });
        const hn = $('#home-need'); hn.innerHTML = ''; hn.appendChild(tneed);
        if (!hn.children.length) hn.innerHTML = '<p class="muted">Nothing needs your attention. ✓</p>';
        const feedFrag = document.createDocumentFragment();
        const dot = { ok: 'ok', error: 'error', denied: 'warn', blocked: 'warn' };
        (S.al || []).forEach((a) => {
          const r = document.createElement('div'); r.className = 'feed-row';
          r.innerHTML = '<div class="dot ' + (dot[a.status] || '') + '"></div><div class="who">' + esc(botName(a.bot_id)) + ' · ' + esc(a.tool || '') + '</div><div class="when">' + fmtTime(a.ts) + (a.url || a.path ? ' · ' + esc((a.url || a.path).slice(0, 40)) : '') + '</div>';
          feedFrag.appendChild(r);
        });
        const hf = $('#home-feed'); hf.innerHTML = ''; hf.appendChild(feedFrag);
        if (!S.al.length) hf.innerHTML = '<p class="muted">Nothing yet. Bot actions show up here as they happen.</p>';
        const tf = document.createDocumentFragment();
        (S.bots || []).forEach((b) => {
          const [k, label] = stateOf(b.id);
          const c = document.createElement('div'); c.className = 'bot-card';
          c.innerHTML = '<div class="emoji">' + esc(b.emoji || '🤖') + '</div><div><div class="name">' + esc(b.name) + (b.paused ? '  ⏸' : '') + '</div><div class="job">' + esc(b.job || '') + '</div></div><div class="chip ' + (k === 'work' || k === 'takeover' ? 'work' : 'wait') + '">' + label + '</div>';
          c.onclick = () => openBot(b.id);
          tf.appendChild(c);
        });
        const ht = $('#home-team'); ht.innerHTML = ''; ht.appendChild(tf);
        if (!S.bots.length) ht.innerHTML = '<p class="muted">No Bots yet. Create one on the Bots tab.</p>';
      } catch (e) { toast('Home', e.message); }
    };
    load(); setInterval(load, 15000);
  }

  function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts); return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  // ---------------------------------------------------------------- marketplace
  async function renderMarketplace() {
    const wrap = $('#view-marketplace');
    try { if (!S.loaded.has('mk')) { S.mk = await api('/api/templates'); S.loaded.add('mk'); } } catch (e) { toast('Marketplace', e.message); }
    wrap.innerHTML = '<div class="section">Marketplace</div><input id="mk-search" type="search" placeholder="Search name, creator, what it does…" style="width:100%;padding:8px 10px;margin-bottom:8px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--text)"><div class="team-grid" id="mk-grid"></div>';
    const search = $('#mk-search'); const grid = $('#mk-grid');
    const render = () => {
      const q = (search.value || '').toLowerCase();
      grid.innerHTML = '';
      (S.mk || []).filter((t) => !q || (t.name + ' ' + (t.creator || '') + ' ' + (t.blurb || '') + ' ' + (t.job || '')).toLowerCase().includes(q)).forEach((t) => {
        const n = (S.bots || []).filter((b) => b.template === t.id).length;
        const c = document.createElement('div'); c.className = 'mk-card';
        c.innerHTML = '<div class="head"><div class="emoji">' + esc(t.emoji || '✨') + '</div><div class="name">' + esc(t.name) + '</div><div class="cat">' + esc(t.category || 'Other') + '</div></div>' +
          '<div class="blurb">' + esc(t.blurb || t.job || '') + '</div><div class="foot">' + (n ? '<div class="chip installed">Installed ×' + n + '</div>' : '') + '<button class="primary" style="flex:1">Add</button></div>';
        c.querySelector('button').onclick = (ev) => { ev.stopPropagation(); addTemplate(t); };
        c.onclick = () => addTemplate(t);
        grid.appendChild(c);
      });
    };
    search.oninput = render;
    render();
  }

  function addTemplate(t) {
    let name = t.name; const taken = new Set((S.bots || []).map((b) => b.name.toLowerCase()));
    if (taken.has(name.toLowerCase())) { let k = 2; while (taken.has((name + ' ' + k).toLowerCase())) k++; name = name + ' ' + k; }
    api('/api/bots/from_template', { body: { template: t.id, name } }).then(() => { toast('Added', t.name); loadBootstrap(); setView('bots'); }).catch((e) => toast('Add failed', e.message));
  }

  // ---------------------------------------------------------------- inbox
  async function renderInbox() {
    const wrap = $('#view-inbox');
    wrap.innerHTML = '<div class="ib-tabbar"><button data-tab="pend" class="active">Needs you</button><button data-tab="hist">History</button><button data-tab="handoff">Handoffs</button></div>' +
      '<div id="ib-pend"></div><div id="ib-hist"></div><div id="ib-handoff"></div>';
    const tabs = wrap.querySelectorAll('.ib-tabbar button');
    const show = (tab) => {
      tabs.forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
      $('#ib-pend').hidden = tab !== 'pend'; $('#ib-hist').hidden = tab !== 'hist'; $('#ib-handoff').hidden = tab !== 'handoff';
    };
    tabs.forEach((b) => b.onclick = () => show(b.dataset.tab));
    try { if (!S.loaded.has('ib')) { S.ib.pend = await api('/api/approvals?status=all&limit=200'); S.ib.handoffs = await api('/api/handoffs?status=all'); S.loaded.add('ib'); } } catch (e) { toast('Inbox', e.message); }
    show('pend'); renderInboxPane('pend'); renderInboxPane('hist'); renderInboxPane('handoff');
  }
  function renderInboxPane(tab) {
    const el = document.getElementById('ib-' + tab);
    if (tab === 'pend') {
      const pend = (S.ib.pend || []).filter((a) => a.status === 'pending');
      if (!pend.length) { el.innerHTML = '<p class="muted">Nothing needs your attention. ✓</p>'; return; }
      el.innerHTML = pend.map((a) => '<div class="approval-card"><div class="summary">' + esc(a.summary || '') + '</div><div class="meta">' + esc(botName(a.bot_id)) + ' · ' + esc(a.category || '') + '</div>' +
        '<div style="margin-top:8px;display:flex;gap:6px"><button class="primary" onclick="window.ibDecide(\'' + a.id + '\', true)">Approve</button>' +
        '<button onclick="window.ibDecide(\'' + a.id + '\', false)" style="flex:1">Deny</button>' +
        '<button onclick="window.ibScreen(\'' + a.bot_id + '\')" style="flex:1">Browser</button></div></div>').join('');
    } else if (tab === 'hist') {
      const rows = (S.ib.pend || []).slice(0, 60);
      el.innerHTML = rows.length ? rows.map((a) => '<div class="al-row"><div class="dot ' + dotStatus(a.status) + '"></div><div class="who">' + esc(botName(a.bot_id)) + '</div><div class="what">' + esc(a.title || a.category) + ' · ' + esc((a.summary || '').slice(0, 60)) + '</div></div>').join('') : '<p class="muted">No history.</p>';
    } else {
      const rows = (S.ib.handoffs || []).slice(0, 40);
      el.innerHTML = rows.length ? rows.map((h) => '<div class="al-row"><div class="who">' + esc(h.from_name || '') + ' → ' + esc(h.to_name || '') + '</div><div class="what">' + esc(h.title || '') + ' · ' + esc((h.brief || '').slice(0, 50)) + '</div></div>').join('') : '<p class="muted">No handoffs.</p>';
    }
  }
  function dotStatus(s) { return { ok: 'ok', error: 'error', denied: 'denied', blocked: 'blocked', waiting_approval: 'warn' }[s] || ''; }
  window.ibDecide = async (aid, approve) => {
    if (!confirm(approve ? 'Approve this?' : 'Deny this?')) return;
    try { await api('/api/approvals/' + aid + '/decide', { body: { approve } }); toast(approve ? 'Approved' : 'Denied', ''); S.ib.pend = await api('/api/approvals?status=all&limit=200'); renderInboxPane('pend'); updateBadge(); } catch (e) { toast('Decision failed', e.message); }
  };
  window.ibScreen = (bid) => openScreen(bid);

  // ---------------------------------------------------------------- computer
  async function renderComputer() {
    const wrap = $('#view-computer');
    wrap.innerHTML = '<div class="section">Bot screens</div><div class="cp-grid" id="cp-grid"></div>' +
      '<div class="section">Workspace files</div><div id="cp-files"></div>' +
      '<div class="section">Terminal</div><div id="cp-term" style="padding:0 12px 8px"></div>';
    try { const d = await api('/api/computer'); S.cp.cards = (d.browser || {}); S.cp.headless = d.headless; S.cp.term = (d.terminal || []); if (d.browser_error) toast('Browser', d.browser_error); } catch (e) { toast('Computer', e.message); }
    const grid = $('#cp-grid');
    if (!(S.bots || []).length) { grid.innerHTML = '<p class="muted">Create a Bot to give it a screen.</p>'; }
    else {
      grid.innerHTML = '';
      (S.bots || []).forEach((b) => {
        const st = S.cp.cards[b.id]; const open = st && st.open;
        const c = document.createElement('div'); c.className = 'cp-card';
        c.innerHTML = '<div class="head"><div class="emoji">' + esc(b.emoji || '🤖') + '</div><div class="name">' + esc(b.name) + '</div><div class="chip ' + (open ? (st.kind === 'takeover' ? 'take' : 'work') : 'no') + '">' + (open ? (st.kind === 'takeover' ? 'you\'re driving' : 'working') : 'no screen yet') + '</div></div>' +
          '<div class="thumb">' + (open ? '<img src="/api/bots/' + b.id + '/screen.jpg?q=40&t=' + Date.now() + '" style="max-width:100%;max-height:140px;border-radius:6px">' : 'No browser tab open yet') + '</div>' +
          (open ? '<div style="margin-top:6px;display:flex;gap:6px"><button class="primary" style="flex:1" onclick="window.cpTake(\'' + b.id + '\')">Take over</button><button onclick="window.cpScreen(\'' + b.id + '\')" style="flex:1">Follow</button></div>' : '');
        grid.appendChild(c);
      });
    }
    $('#cp-term').innerHTML = (S.cp.term || []).slice(-8).map((e) => '<div style="margin-bottom:6px"><div style="font-size:11px;color:var(--muted)">[${esc(e.who || \'bot\')}] ${esc(e.cwd || \'.\')}> ${esc(e.command || \'\')}</div><div style="font-size:11px;white-space:pre-wrap">${esc((e.output || \'\').slice(0, 400))} <span style="color:#5a6478">(exit ${e.exit || 0})</span></div></div>').join('') || '<p class="muted">No terminal history.</p>';
  }
  window.cpTake = async (bid) => { try { await api('/api/bots/' + bid + '/takeover', { body: { action: 'start' } }); openScreen(bid); } catch (e) { toast('Takeover', e.message); } };
  window.cpScreen = (bid) => openScreen(bid);

  // ---------------------------------------------------------------- files
  async function renderFiles() {
    const wrap = $('#view-files');
    wrap.innerHTML = '<div class="fl-bar"><button onclick="flMode(\'recent\')" id="fl-recent" class="primary">Recent</button><button onclick="flMode(\'browse\')">Folders</button><input id="fl-search" type="search" placeholder="Search file names…" style="flex:1;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text)"></div>' +
      '<div class="fl-picker" id="fl-picker"></div><div class="fl-preview" id="fl-preview" hidden></div>';
    S.fl.mode = 'recent'; S.fl.cwd = ''; S.fl.entries = []; S.fl.current = null;
    $('#fl-search').oninput = () => { S.fl.mode = $('#fl-search').value.trim() ? 'search' : 'recent'; flRender(); };
    window.flMode = (m) => { S.fl.mode = m; if (m !== 'search') $('#fl-search').value = ''; flRender(); };
    flRender();
  }
  async function flRender() {
    const p = $('#fl-picker');
    try {
      let d;
      if (S.fl.mode === 'search') d = await api('/api/ws/search', { params: { q: $('#fl-search').value.trim() } });
      else if (S.fl.mode === 'browse') d = await api('/api/ws/list', { params: { path: S.fl.cwd || '' } });
      else d = await api('/api/ws/recent', { params: { limit: 60 } });
      S.fl.entries = d.entries || []; S.fl.stats = d.stats || {};
      p.innerHTML = (S.fl.entries.length ? S.fl.entries.map((e) => '<div class="fl-entry" onclick="window.flPick(\'' + escJs(e.path) + '\')"><div class="emoji">' + (e.dir ? '📁' : '📄') + '</div><div class="info"><div class="name">' + esc(e.name) + '</div><div class="sub">' + (e.dir ? 'Folder' : humanSize(e.size) + ' · ' + agoTime(e.mtime)) + '</div></div></div>').join('') : '<p class="muted">Nothing here yet.</p>');
      if (S.fl.mode === 'browse') {
        const head = document.createElement('div'); head.style = 'padding:6px 12px;border-bottom:1px solid var(--line);font-size:11px;color:var(--muted);display:flex;justify-content:space-between;align-items:center;';
        head.innerHTML = '<div>workspace / ' + (S.fl.cwd.replace(/\//g, ' / ') || '') + '</div>' + (S.fl.cwd ? '<button onclick="flUp()" style="background:none;border:none;color:var(--accent);cursor:pointer">↑ Up</button>' : '');
        p.prepend(head);
      }
    } catch (e) { p.innerHTML = '<p class="err">Could not load files.</p>'; }
  }
  window.flPick = (path) => {
    const e = (S.fl.entries || []).find((x) => x.path === path);
    if (!e) return;
    if (e.dir) { S.fl.cwd = path; flRender(); return; }
    S.fl.current = e;
    $('#fl-picker').hidden = true; $('#fl-preview').hidden = false;
    $('#fl-preview').innerHTML = '<div style="margin-bottom:8px"><div style="font-weight:600">' + esc(e.name) + '</div><div style="font-size:11px;color:var(--muted);margin-top:2px">' + e.path + ' · ' + humanSize(e.size) + '</div><div style="margin-top:6px;display:flex;gap:6px"><button class="primary" onclick="window.flSave()" style="flex:1">Save a copy</button><button onclick="window.flDel()" style="flex:1">Delete</button><button onclick="window.flBack()" style="flex:1">Back</button></div></div><div id="fl-content"></div>';
    api('/api/ws/preview', { params: { path: e.path } }).then((d) => {
      const c = $('#fl-content');
      if (d.kind === 'text') c.innerHTML = '<pre>' + esc((d.text || '').slice(0, 200000)) + (d.truncated ? '\n… (truncated)' : '') + '</pre>';
      else if (d.kind === 'image' && !d.too_big) c.innerHTML = '<img src="/api/ws/raw?path=' + encodeURIComponent(e.path) + '&t=' + Date.now() + '">';
      else c.innerHTML = '<p class="muted">' + (d.kind === 'image' ? 'Image too large to preview.' : 'No preview for this kind of file.') + '</p>';
    }).catch(() => $('#fl-content').innerHTML = '<p class="err">Could not preview.</p>');
  };
  window.flBack = () => { $('#fl-picker').hidden = false; $('#fl-preview').hidden = true; S.fl.current = null; };
  window.flUp = () => { S.fl.cwd = S.fl.cwd ? S.fl.cwd.split('/').slice(0, -1).join('/') : ''; flRender(); };
  window.flSave = () => { if (!S.fl.current) return; api('/api/ws/raw', { params: { path: S.fl.current.path } }).then(() => { toast('Saved', S.fl.current.name + ' downloaded.'); }).catch(() => toast('Save failed', '')); };
  window.flDel = async () => { if (!S.fl.current || !confirm('Delete ' + S.fl.current.name + '?')) return; try { await api('/api/ws/file', { method: 'DELETE', params: { path: S.fl.current.path } }); toast('Deleted', ''); flBack(); flRender(); } catch (e) { toast('Delete failed', e.message); } };

  function humanSize(n) { return n < 1024 ? n + ' B' : n < 1024 * 1024 ? (n / 1024).toFixed(1) + ' KB' : (n / 1024 / 1024).toFixed(1) + ' MB'; }
  function agoTime(ts) { if (!ts) return ''; const d = max(0, Date.now() / 1000 - ts); return d < 90 ? 'just now' : d < 3600 ? Math.floor(d / 60) + ' min ago' : d < 86400 ? Math.floor(d / 3600) + ' h ago' : new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
  function max(a, b) { return a > b ? a : b; }
  function escJs(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }

  // ---------------------------------------------------------------- skills
  async function renderSkills() {
    const wrap = $('#view-skills');
    wrap.innerHTML = '<div class="sk-list" id="sk-list"></div><div class="sk-editor" id="sk-editor" hidden><textarea id="sk-text" rows="10" style="width:100%;background:#0b0d12;border:1px solid var(--line);border-radius:8px;padding:10px;color:var(--text);font-family:ui-monospace,monospace;font-size:12px"></textarea>' +
      '<div style="margin-top:8px;display:flex;gap:6px"><button class="primary" onclick="window.skSave()" style="flex:1">Save</button><button onclick="window.skToggle()" style="flex:1">Activate / Draft</button><button onclick="window.skTest()" style="flex:1">Test</button><button onclick="window.skEditorHide()" style="flex:1">Close</button></div><div id="sk-msg" style="margin-top:6px;font-size:12px;color:var(--muted)"></div></div>';
    try { if (!S.loaded.has('sk')) { S.sk.skills = await api('/api/skills'); S.sk.recs = await api('/api/recordings'); S.loaded.add('sk'); } } catch (e) { toast('Skills', e.message); }
    skRenderList();
  }
  function skRenderList() {
    const el = $('#sk-list'); el.innerHTML = '';
    (S.sk.skills || []).forEach((s) => {
      const r = document.createElement('div'); r.className = 'sk-row';
      r.innerHTML = '<div class="dot ' + (s.status === 'active' ? '' : 'draft') + '"></div><div class="name">' + esc(s.name) + '</div><div class="meta">' + esc(s.bot || '') + ' · ' + s.status + '</div>';
      r.onclick = () => skOpen(s.name);
      el.appendChild(r);
    });
    if (!S.sk.skills.length) el.innerHTML = '<p class="muted">No skills yet.</p>';
  }
  window.skOpen = async (name) => {
    try { const s = await api('/api/skills/' + name); S.sk.cur = name; $('#sk-text').value = s.raw || ''; $('#sk-editor').hidden = false; $('#sk-list').hidden = true; $('#sk-msg').textContent = ''; $('#sk-editor button:nth-of-type(2)').textContent = s.status === 'active' ? 'Make draft' : 'Activate'; } catch (e) { toast('Skill', e.message); }
  };
  window.skEditorHide = () => { $('#sk-editor').hidden = true; $('#sk-list').hidden = false; };
  window.skSave = async () => {
    if (!S.sk.cur) return;
    try { await api('/api/skills/' + S.sk.cur, { method: 'PUT', body: { raw: $('#sk-text').value } }); toast('Saved', ''); S.sk.skills = await api('/api/skills'); skRenderList(); skEditorHide(); } catch (e) { toast('Save failed', e.message); }
  };
  window.skToggle = async () => {
    if (!S.sk.cur) return;
    try { const s = await api('/api/skills/' + S.sk.cur + '/status', { method: 'POST', body: { status: $('#sk-editor button:nth-of-type(2)').textContent === 'Make draft' ? 'active' : 'draft' } }); $('#sk-text').value = s.raw || ''; $('#sk-editor button:nth-of-type(2)').textContent = s.status === 'active' ? 'Make draft' : 'Activate'; S.sk.skills = await api('/api/skills'); skRenderList(); toast(s.status, ''); } catch (e) { toast('Toggle failed', e.message); }
  };
  window.skTest = async () => {
    if (!S.sk.cur || !(S.bots || []).length) { toast('Test', 'Create a Bot first.'); return; }
    try { const th = await api('/api/skills/' + S.sk.cur + '/test', { method: 'POST', body: { bot_id: S.bots[0].id, dry_run: true } }); toast('Testing', ''); openThread(th.id, 'Skill test', S.bots[0].id); } catch (e) { toast('Test failed', e.message); }
  };

  // ---------------------------------------------------------------- routines
  async function renderRoutines() {
    const wrap = $('#view-routines');
    wrap.innerHTML = '<div class="rt-bar"><button onclick="window.rtNew()" class="primary">New routine</button><button onclick="window.rtEdit()">Edit</button><button onclick="window.rtRun()">Run now</button><button onclick="window.rtToggle()">Enable/disable</button><button onclick="window.rtDel()" style="color:var(--bad)">Delete</button></div>' +
      '<div class="rt-table" id="rt-table"></div><div class="rt-history" id="rt-history"></div>';
    try { if (!S.loaded.has('rt')) { S.rt.rows = await api('/api/routines'); S.loaded.add('rt'); } } catch (e) { toast('Routines', e.message); }
    rtRender();
  }
  function rtRender() {
    const el = $('#rt-table'); el.innerHTML = '';
    (S.rt.rows || []).forEach((r) => {
      const row = document.createElement('div'); row.className = 'rt-row';
      row.innerHTML = '<div class="toggle ' + (r.enabled ? 'on' : '') + '" onclick="window.rtToggleOne(\'' + r.id + '\', ' + !r.enabled + ')"></div><div class="name">' + esc(r.name || 'Routine') + '</div><div class="meta">' + esc(describeCron(r.cron)) + ' · ' + (r.enabled ? (r.next_run_at ? fmtTime(r.next_run_at) : '—') : 'off') + '</div>';
      row.onclick = () => { S.rt.sel = r; rtRenderRuns(r.id); };
      el.appendChild(row);
    });
    if (!S.rt.rows.length) el.innerHTML = '<p class="muted">No routines yet.</p>';
    if (S.rt.sel) rtRenderRuns(S.rt.sel.id);
  }
  function rtRenderRuns(id) {
    const el = $('#rt-history');
    api('/api/routine_runs', { params: { routine_id: id } }).then((rows) => {
      el.innerHTML = '<div style="font-size:11px;color:var(--muted);margin-bottom:6px">Run history</div>' + (rows.length ? rows.map((r) => '<div style="padding:6px 10px;border-bottom:1px solid #161a24;font-size:12px"><div style="display:flex;justify-content:space-between"><div>' + esc(r.routine_name || '') + '</div><div style="color:var(--muted)">' + fmtTime(r.started_at) + ' · ' + r.status + '</div></div><div style="font-size:11px;color:var(--muted);margin-top:2px">' + esc((r.result || r.error || '')).slice(0, 200) + '</div></div>').join('') : '<p class="muted">No runs yet.</p>');
    }).catch(() => el.innerHTML = '');
  }
  window.rtNew = async () => {
    if (!(S.bots || []).length) { toast('Routines', 'Create a Bot first.'); return; }
    const name = prompt('Routine name:'); if (!name) return;
    const bot = S.bots[0].id;
    const cron = prompt('Cron (e.g. 0 8 * * 1-5):', '0 8 * * 1-5'); if (!cron) return;
    const task = prompt('What should the Bot do?'); if (!task) return;
    try { await api('/api/routines', { method: 'POST', body: { bot_id: bot, name, cron, prompt: task } }); toast('Created', ''); S.rt.rows = await api('/api/routines'); rtRender(); } catch (e) { toast('Create failed', e.message); }
  };
  window.rtEdit = () => toast('Edit', 'Tap a routine row first.');
  window.rtRun = async () => { if (!S.rt.sel) { toast('Run', 'Select a routine.'); return; } try { await api('/api/routines/' + S.rt.sel.id + '/run', { method: 'POST' }); toast('Running', ''); } catch (e) { toast('Run failed', e.message); } };
  window.rtToggle = async () => { if (!S.rt.sel) return; try { await api('/api/routines/' + S.rt.sel.id, { method: 'PUT', body: { enabled: !S.rt.sel.enabled } }); S.rt.rows = await api('/api/routines'); rtRender(); } catch (e) { toast('Toggle failed', e.message); } };
  window.rtDel = async () => { if (!S.rt.sel || !confirm('Delete routine?')) return; try { await api('/api/routines/' + S.rt.sel.id, { method: 'DELETE' }); S.rt.sel = null; S.rt.rows = await api('/api/routines'); rtRender(); $('#rt-history').innerHTML = ''; } catch (e) { toast('Delete failed', e.message); } };
  window.rtToggleOne = async (id, on) => { try { await api('/api/routines/' + id, { method: 'PUT', body: { enabled: on } }); S.rt.rows = await api('/api/routines'); rtRender(); } catch (e) {} };
  function describeCron(expr) {
    const m = { '0 8 * * 1-5': 'Weekdays 8:00', '0 7 * * *': 'Daily 7:00', '0 2 * * *': 'Overnight 2:00', '0 * * * *': 'Hourly', '0 9 * * 1': 'Mondays 9:00', '0 9 1 * *': '1st of month 9:00' };
    return m[expr] || expr;
  }

  // ---------------------------------------------------------------- plugins
  async function renderPlugins() {
    const wrap = $('#view-plugins');
    wrap.innerHTML = '<div class="pl-tabbar"><button data-tab="conn" class="active">Connectors</button><button data-tab="mcp">MCP servers</button></div><div id="pl-conn"></div><div id="pl-mcp" hidden></div>';
    wrap.querySelectorAll('.pl-tabbar button').forEach((b) => b.onclick = () => {
      wrap.querySelectorAll('.pl-tabbar button').forEach((b2) => b2.classList.toggle('active', b2 === b));
      $('#pl-conn').hidden = b.dataset.tab !== 'conn'; $('#pl-mcp').hidden = b.dataset.tab === 'conn';
      if (b.dataset.tab === 'conn') plRenderConn(); else plRenderMcp();
    });
    try { if (!S.loaded.has('pl')) { const d = await api('/api/plugins'); S.pl.plugins = d.plugins || []; S.pl.catalog = d.catalog || []; S.loaded.add('pl'); } } catch (e) { toast('Plugins', e.message); }
    await plRenderMcp(); plRenderConn();
  }
  function plRenderConn() {
    const el = $('#pl-conn'); el.innerHTML = '';
    (S.pl.plugins || []).forEach((p) => {
      const ok = p.configured; const c = document.createElement('div'); c.className = 'pl-card';
      c.innerHTML = '<div class="top"><div class="name">' + esc(p.name) + '</div><div class="chip ' + (ok ? 'ok' : 'warn') + '">' + (ok ? 'connected' : ok === false ? 'needs setup' : 'blocked') + '</div></div>' +
        '<div class="desc">' + esc(p.description || '') + '</div><div class="tools">' + (p.tools || []).slice(0, 6).map((t) => esc(t.name) + (t.read_only ? '' : ' *')).join(', ') || '—' + '</div>' +
        '<div style="margin-top:8px;display:flex;gap:6px"><button class="primary" style="flex:1" onclick="window.plConfig(\'' + p.id + '\')">Configure</button><button onclick="window.plTest(\'' + p.id + '\')" style="flex:1">Test</button></div>';
      el.appendChild(c);
    });
    if (!S.pl.plugins.length) el.innerHTML = '<p class="muted">No connectors yet.</p>';
  }
  window.plConfig = async (id) => {
    const p = (S.pl.plugins || []).find((x) => x.id === id);
    if (!p) return;
    const fields = (p.fields || []).map((f) => '<div style="margin-bottom:8px"><div style="font-size:11px;color:var(--muted);margin-bottom:2px">' + esc(f.label) + (f.required ? ' *' : '') + '</div><input type="' + (f.secret ? 'password' : 'text') + '" placeholder="' + esc(f.placeholder || '') + '" class="pl-inp" data-key="' + esc(f.key) + '" style="width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text)"></div>').join('');
    const html = '<div style="padding:12px"><div style="font-weight:600;margin-bottom:8px">Configure ' + esc(p.name) + '</div>' + fields +
      '<div style="margin-top:10px;display:flex;gap:6px"><button class="primary" onclick="window.plConfigSave(\'' + id + '\')" style="flex:1">Save</button><button onclick="document.getElementById(\'pl-modal\').hidden=true" style="flex:1">Cancel</button></div></div>';
    let m = document.getElementById('pl-modal');
    if (!m) { m = document.createElement('div'); m.id = 'pl-modal'; m.style = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;display:flex;align-items:center;justify-content:center;'; document.body.appendChild(m); }
    m.innerHTML = '<div style="background:var(--panel);border:1px solid var(--line);border-radius:12px;width:min(90vw,420px);max-height:80vh;overflow:auto;padding:16px">' + html + '</div>'; m.hidden = false;
  };
  window.plConfigSave = async (id) => {
    const m = document.getElementById('pl-modal'); const body = {};
    m.querySelectorAll('.pl-inp').forEach((i) => { if (i.value) body[i.dataset.key] = i.value; });
    try { await api('/api/plugins/' + id + '/config', { method: 'PUT', body }); m.hidden = true; S.pl.plugins = await api('/api/plugins'); plRenderConn(); toast('Saved', ''); } catch (e) { toast('Configure failed', e.message); }
  };
  window.plTest = async (id) => { try { const d = await api('/api/plugins/' + id + '/test', { method: 'POST' }); toast('Test', d.message || 'ok'); } catch (e) { toast('Test failed', e.message); } };
  async function plRenderMcp() {
    const el = $('#pl-mcp'); el.innerHTML = '';
    try { const rows = await api('/api/mcp'); el.innerHTML = rows.length ? rows.map((r) => '<div class="al-row"><div class="who">' + esc(r.name) + '</div><div class="what">' + esc(r.transport) + ' · ' + esc(r.status || '') + (r.error ? ' · ' + esc(r.error.slice(0, 30)) : '') + '</div></div>').join('') : '<p class="muted">No MCP servers.</p>'; } catch (e) { el.innerHTML = '<p class="err">Could not load MCP.</p>'; }
  }

  // ---------------------------------------------------------------- usage
  async function renderUsage() {
    const wrap = $('#view-usage');
    try { S.us = await api('/api/usage'); } catch (e) { toast('Usage', e.message); }
    const us = S.us || {};
    wrap.innerHTML = '<div class="us-tile"><div class="cap">Total cost</div><div class="val">$' + ((us.total_cost || 0).toFixed(2)) + '</div></div>' +
      '<div class="us-tile"><div class="cap">Tokens (week)</div><div class="val">' + ((us.total_tokens || 0).toLocaleString()) + '</div></div>' +
      '<div class="us-tile"><div class="cap">Requests</div><div class="val">' + ((us.total_requests || 0).toLocaleString()) + '</div></div>' +
      '<div class="us-tile"><div class="cap">Models</div><div class="val">' + ((us.models || []).length || 0) + '</div></div>' +
      '<div class="section">By model</div>' + ((us.by_model || []).map((m) => '<div class="us-tile"><div class="cap">' + esc(m.model || '?') + '</div><div class="val">$' + (m.cost || 0).toFixed(2) + ' · ' + (m.tokens || 0).toLocaleString() + ' tok</div></div>').join('') || '<p class="muted">No usage yet.</p>');
  }

  // ---------------------------------------------------------------- action log
  async function renderActionLog() {
    const wrap = $('#view-actionlog');
    try { S.al = await api('/api/approvals?status=all&limit=100'); } catch (e) { toast('Action log', e.message); }
    const rows = (S.al || []).slice(0, 80);
    wrap.innerHTML = '<div class="section">Recent activity</div>' +
      (rows.length ? rows.map((a) => '<div class="al-row"><div class="dot ' + dotStatus(a.status) + '"></div><div class="who">' + esc(botName(a.bot_id)) + '</div><div class="what">' + esc(a.title || a.category) + ' · ' + esc((a.summary || '').slice(0, 50)) + '</div></div>').join('') : '<p class="muted">Nothing yet.</p>');
  }

  // ---------------------------------------------------------------- settings (more screens)
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
      try { await loadBootstrap(); connect(); setView('home'); return; } catch (e) { /* fall through to sign-in */ }
      showLogin();
    } catch (e) { toast('Could not reach the server', 'Check your connection and reopen the app.'); }
    setInterval(() => { if (S.view === 'chat') updateChatStatus(); }, 1500);
  }
  start();
})();
