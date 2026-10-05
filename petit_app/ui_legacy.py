"""Embedded single-page UI (to be removed once the screens move to web/)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


# ===================== Single-page UI =====================

@router.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


_INDEX_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>M5 Petit</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #f5f5f5; color: #333; }
header { background: #4a7c59; color: white; padding: 12px 20px; font-size: 18px; font-weight: bold; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
header #header-char-name { font-weight: normal; }
header .header-user { font-size: 12px; font-weight: normal; display: flex; align-items: center; gap: 8px; }
header .header-user .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
header .header-user button { background: rgba(255,255,255,.2); padding: 4px 10px; font-size: 12px; }
header .header-user button:hover { background: rgba(255,255,255,.35); }
.char-tabs { display: flex; background: #333; }
.char-tab { padding: 8px 18px; cursor: pointer; border: none; border-bottom: 3px solid transparent; background: none; font-size: 13px; font-weight: bold; color: #ccc; }
.char-tab.active { color: white; }
.tabs { display: flex; background: white; border-bottom: 2px solid #4a7c59; }
.tab { padding: 10px 20px; cursor: pointer; border: none; background: none; font-size: 14px; color: #666; }
.tab.active { color: #4a7c59; font-weight: bold; border-bottom: 2px solid #4a7c59; margin-bottom: -2px; }
.panel { display: none; padding: 16px; max-width: 900px; margin: 0 auto; }
.panel.active { display: block; }
.card { background: white; border-radius: 8px; padding: 12px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
button { background: #4a7c59; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; }
button:hover { background: #3a6449; }
button.danger { background: #c0392b; }
input, textarea, select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; margin-top: 4px; }
textarea { height: 80px; resize: vertical; }
label { font-size: 13px; color: #555; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta { font-size: 11px; color: #999; }
img.thumb { max-width: 200px; max-height: 150px; object-fit: cover; border-radius: 4px; cursor: pointer; }
audio { width: 100%; margin-top: 6px; }
.badge { background: #e74c3c; color: white; border-radius: 10px; font-size: 11px; padding: 2px 6px; }
h3 { font-size: 14px; margin-bottom: 8px; color: #444; }
hr { border: none; border-top: 1px solid #eee; margin: 10px 0; }
</style>
</head>
<body>
<header>
  <span>M5 Petit<span id="header-char-name"></span></span>
  <span class="header-user" id="header-user"></span>
</header>
<div class="char-tabs" id="char-tabs" style="display:none"></div>
<div class="tabs">
  <button class="tab active" onclick="showTab('album')">📷 アルバム</button>
  <button class="tab" onclick="showTab('voice')">🎙️ ボイスメモ</button>
  <button class="tab" onclick="showTab('notebook')">📔 ノート</button>
  <button class="tab" onclick="showTab('mail')">✉️ メール</button>
  <button class="tab" onclick="showTab('chat')">💬 会話</button>
  <button class="tab" id="tab-btn-group" style="display:none" onclick="showTab('group')">👨‍👩‍👧‍👦 グループ会話</button>
  <button class="tab" onclick="showTab('records')">📜 記録</button>
  <button class="tab" onclick="showTab('diary')">📖 日記</button>
</div>

<!-- Album -->
<div id="tab-album" class="panel active">
  <div class="card">
    <h3>写真を送る</h3>
    <label>送信者ID <input id="al-from" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="al-title" value="photo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="al-file" accept="image/*" style="width:auto"></label>
    <button onclick="uploadPhoto()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するアルバム</h3>
    <label>ID <input id="al-view-id" style="width:150px"></label>
    <button onclick="loadAlbum()" style="margin-left:8px">表示</button>
  </div>
  <div id="album-list"></div>
</div>

<!-- Voice memo -->
<div id="tab-voice" class="panel">
  <div class="card">
    <h3>ボイスメモを送る</h3>
    <label>送信者ID <input id="vm-from" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="vm-title" value="memo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="vm-file" accept="audio/*" style="width:auto"></label>
    <button onclick="uploadVoice()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するボイスメモ</h3>
    <label>ID <input id="vm-view-id" style="width:150px"></label>
    <button onclick="loadVoiceMemos()" style="margin-left:8px">表示</button>
  </div>
  <div id="voice-list"></div>
</div>

<!-- Notebook -->
<div id="tab-notebook" class="panel">
  <div class="card">
    <h3>書き込む</h3>
    <div class="meta">書き込む人: <span id="nb-author-display">-</span></div>
    <label style="margin-top:6px">内容 <textarea id="nb-content" placeholder="今日の出来事..."></textarea></label>
    <button onclick="addNote()" style="margin-top:8px">追加</button>
  </div>
  <button onclick="loadNotebook()" style="margin-bottom:12px">更新</button>
  <div id="notebook-list"></div>
</div>

<!-- Mail -->
<div id="tab-mail" class="panel">
  <div class="card">
    <h3>メールを送る</h3>
    <div class="row">
      <label style="flex:0 0 auto">From <input id="ml-from" style="width:120px"></label>
      <label style="flex:0 0 auto">To <input id="ml-to" list="char-datalist" style="width:120px"></label>
      <datalist id="char-datalist"></datalist>
    </div>
    <label style="margin-top:6px">件名 <input id="ml-subject" placeholder="件名（省略可）"></label>
    <label style="margin-top:6px">本文 <textarea id="ml-body" placeholder="本文..."></textarea></label>
    <button onclick="sendMail()" style="margin-top:8px">送信</button>
  </div>
  <div class="row" style="margin-bottom:12px;gap:4px">
    <button onclick="loadMail('inbox')">受信トレイ</button>
    <button onclick="loadMail('starred')">スター</button>
    <button onclick="loadMail('archived')">アーカイブ</button>
    <button onclick="loadMail('all')">すべて</button>
  </div>
  <div id="mail-list"></div>
</div>

<!-- Chat -->
<div id="tab-chat" class="panel">
  <div class="card">
    <div id="chat-list" style="max-height:400px;overflow-y:auto"></div>
    <div id="chat-status" class="meta" style="margin-top:4px"></div>
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <textarea id="chat-input" placeholder="メッセージを入力..." style="flex:1"></textarea>
      <button id="chat-send-btn" onclick="sendChat()">送信</button>
    </div>
  </div>
</div>

<!-- Group chat (Phase C: every visible in_group character, replies streamed
     back one at a time as they arrive) -->
<div id="tab-group" class="panel">
  <div class="card">
    <p class="meta" style="margin-bottom:8px">見えているみんなに一度に話しかけます。返事は届いた子から順に表示されます。</p>
    <div id="group-chat-list" style="max-height:400px;overflow-y:auto"></div>
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <textarea id="group-chat-input" placeholder="メッセージを入力..." style="flex:1"></textarea>
      <button id="group-send-btn" onclick="sendGroupChat()">送信</button>
    </div>
  </div>
</div>

<!-- Records -->
<div id="tab-records" class="panel">
  <div class="card">
    <h3>記録一覧</h3>
    <button onclick="loadRecordsList()" style="margin-bottom:8px">更新</button>
    <div id="records-files"></div>
  </div>
  <div id="records-detail"></div>
</div>

<!-- Diary -->
<div id="tab-diary" class="panel">
  <div class="card">
    <div class="row">
      <label>日付 <input type="date" id="diary-date"></label>
      <button onclick="loadDiary()">見る</button>
      <button onclick="summarizeDiary()">日記を書く</button>
    </div>
    <div id="diary-content" class="meta" style="margin-top:8px;white-space:pre-wrap;font-size:14px;color:#333"></div>
  </div>
</div>

<script>
let ME = null;
let CHARACTERS = [];
let currentCharacterId = null;

// ===== Login session (Phase B) =====

async function loadMe() {
  const r = await fetch('/api/me');
  if (r.status === 401) { window.location.href = '/login'; return false; }
  ME = await r.json();
  const el = document.getElementById('header-user');
  el.innerHTML = `<span class="dot" style="background:${ME.color}"></span>${ME.name}<button onclick="logout()">ログアウト / Logout</button>`;
  document.getElementById('al-from').value = ME.id;
  document.getElementById('vm-from').value = ME.id;
  document.getElementById('ml-from').value = ME.id;
  document.getElementById('nb-author-display').textContent = ME.name;
  return true;
}

async function logout() {
  await fetch('/api/auth/logout', {method: 'POST'});
  window.location.href = '/login';
}

// ===== Character selection (Phase A: N characters; Phase B: only the
// characters this logged-in user is allowed to see) =====

async function loadCharacters() {
  if (!(await loadMe())) return;
  CHARACTERS = await fetch('/api/characters').then(r => r.json());
  if (!CHARACTERS.length) {
    document.getElementById('header-char-name').textContent = ' — (キャラクターが見つかりません)';
    return;
  }
  currentCharacterId = CHARACTERS[0].id;
  renderCharTabs();
  onCharacterChanged();
  // Group chat tab only makes sense with 2+ characters this user can see
  // that opted into it (config.json's in_group, default true).
  const groupChars = CHARACTERS.filter(c => c.in_group !== false);
  document.getElementById('tab-btn-group').style.display = groupChars.length >= 2 ? '' : 'none';
}

function renderCharTabs() {
  const el = document.getElementById('char-tabs');
  if (CHARACTERS.length <= 1) { el.style.display = 'none'; updateHeader(); return; }
  el.style.display = 'flex';
  el.innerHTML = CHARACTERS.map(c => `
    <button class="char-tab ${c.id === currentCharacterId ? 'active' : ''}" style="border-bottom-color:${c.color}" onclick="selectCharacter('${c.id}')">${c.name}</button>
  `).join('');
  updateHeader();
}

function selectCharacter(id) {
  currentCharacterId = id;
  renderCharTabs();
  onCharacterChanged();
}

function updateHeader() {
  const c = CHARACTERS.find(c => c.id === currentCharacterId);
  const el = document.getElementById('header-char-name');
  el.textContent = c ? ` — ${c.name}` : '';
  el.style.color = c ? c.color : '';
}

function populateMailToOptions() {
  document.getElementById('char-datalist').innerHTML =
    CHARACTERS.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
}

function onCharacterChanged() {
  document.getElementById('al-view-id').value = currentCharacterId;
  document.getElementById('vm-view-id').value = currentCharacterId;
  document.getElementById('ml-to').value = currentCharacterId;
  populateMailToOptions();
  const active = document.querySelector('.panel.active');
  if (active) refreshActiveTab(active.id.replace('tab-', ''));
}

function refreshActiveTab(name) {
  if (name === 'album') loadAlbum();
  if (name === 'voice') loadVoiceMemos();
  if (name === 'chat') loadChat();
  if (name === 'group') loadGroupChat();
  if (name === 'records') loadRecordsList();
  if (name === 'diary') loadDiary();
}

function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.currentTarget.classList.add('active');
  if (name === 'notebook') loadNotebook();
  if (name === 'mail') loadMail('inbox');
  if (name === 'diary') document.getElementById('diary-date').value = new Date().toISOString().slice(0, 10);
  refreshActiveTab(name);
}

// Album
async function uploadPhoto() {
  const file = document.getElementById('al-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('al-title').value);
  const pid = document.getElementById('al-from').value;
  const r = await fetch(`/api/${currentCharacterId}/album/${pid}/upload`, {method:'POST', body:fd});
  const j = await r.json();
  if (j.ok) { alert('送った！'); loadAlbum(); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadAlbum() {
  const pid = document.getElementById('al-view-id').value;
  const photos = await fetch(`/api/${currentCharacterId}/album/${pid}`).then(r => r.json());
  const el = document.getElementById('album-list');
  if (!photos.length) { el.innerHTML = '<p style="color:#999;padding:8px">写真なし</p>'; return; }
  el.innerHTML = photos.map(p => `
    <div class="card">
      <div class="row">
        <img class="thumb" src="/api/${currentCharacterId}/album/${pid}/${p.filename}" onclick="window.open(this.src)">
        <div style="flex:1">
          <div style="font-weight:bold;font-size:13px">${p.filename}</div>
          <div class="meta">${new Date(p.mtime*1000).toLocaleString('ja-JP')} · ${Math.round(p.size/1024)}KB</div>
          <div class="meta">既読: ${p.read_by.join(', ') || 'なし'} · ${p.locked ? '🔒' : ''}</div>
          <div class="row" style="margin-top:6px;gap:4px">
            <button onclick="lockPhoto('${pid}','${p.filename}')">${p.locked ? '解錠' : '施錠'}</button>
            <button class="danger" onclick="deletePhoto('${pid}','${p.filename}')">削除</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

async function lockPhoto(pid, fn) {
  await fetch(`/api/${currentCharacterId}/album/${pid}/${fn}/lock`, {method:'POST'});
  loadAlbum();
}
async function deletePhoto(pid, fn) {
  if (!confirm('削除する？')) return;
  await fetch(`/api/${currentCharacterId}/album/${pid}/${fn}`, {method:'DELETE'});
  loadAlbum();
}

// Voice memo
async function uploadVoice() {
  const file = document.getElementById('vm-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('vm-title').value);
  const pid = document.getElementById('vm-from').value;
  const r = await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/upload`, {method:'POST', body:fd});
  const j = await r.json();
  if (j.ok) { alert('送った！'); loadVoiceMemos(); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadVoiceMemos() {
  const pid = document.getElementById('vm-view-id').value;
  const memos = await fetch(`/api/${currentCharacterId}/voice_memo/${pid}`).then(r => r.json());
  const el = document.getElementById('voice-list');
  if (!memos.length) { el.innerHTML = '<p style="color:#999;padding:8px">ボイスメモなし</p>'; return; }
  el.innerHTML = memos.map(m => `
    <div class="card">
      <div style="font-weight:bold;font-size:13px">${m.filename}</div>
      <div class="meta">${new Date(m.mtime*1000).toLocaleString('ja-JP')} · ${Math.round(m.size/1024)}KB</div>
      <div class="meta">聴いた: ${m.listened_by.join(', ') || 'なし'} · ${m.locked ? '🔒' : ''}</div>
      <audio controls src="/api/${currentCharacterId}/voice_memo/${pid}/${m.filename}"></audio>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="lockVoice('${pid}','${m.filename}')">${m.locked ? '解錠' : '施錠'}</button>
        <button class="danger" onclick="deleteVoice('${pid}','${m.filename}')">削除</button>
      </div>
    </div>`).join('');
}

async function lockVoice(pid, fn) {
  await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/${fn}/lock`, {method:'POST'});
  loadVoiceMemos();
}
async function deleteVoice(pid, fn) {
  if (!confirm('削除する？')) return;
  await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/${fn}`, {method:'DELETE'});
  loadVoiceMemos();
}

// Notebook (shared, not character-scoped; author always comes from the
// logged-in session server-side, see /api/notebook)
async function addNote() {
  const r = await fetch('/api/notebook', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      content: document.getElementById('nb-content').value,
    })
  });
  document.getElementById('nb-content').value = '';
  loadNotebook();
}

async function loadNotebook() {
  const entries = await fetch('/api/notebook').then(r => r.json());
  const el = document.getElementById('notebook-list');
  if (!entries.length) { el.innerHTML = '<p style="color:#999;padding:8px">まだ何も書いてない</p>'; return; }
  el.innerHTML = entries.map(e => `
    <div class="card">
      <div class="row"><strong>${e.author}</strong><span class="meta" style="margin-left:8px">${e.date}</span></div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${e.content}</div>
    </div>`).join('');
}

// Mail (shared, not character-scoped — recipient list comes from CHARACTERS)
async function sendMail() {
  const r = await fetch('/api/mail/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      from_id: document.getElementById('ml-from').value,
      to_id: document.getElementById('ml-to').value,
      subject: document.getElementById('ml-subject').value,
      body: document.getElementById('ml-body').value,
    })
  });
  const j = await r.json();
  if (j.ok) { alert('送信した！'); document.getElementById('ml-body').value = ''; loadMail('inbox'); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadMail(filter = 'inbox') {
  const data = await fetch(`/api/mailbox?filter=${filter}`).then(r => r.json());
  const el = document.getElementById('mail-list');
  if (!data.items.length) { el.innerHTML = '<p style="color:#999;padding:8px">メールなし</p>'; return; }
  el.innerHTML = data.items.map(m => `
    <div class="card">
      <div class="row">
        <strong>${m.sender} → ${m.recipient}</strong>
        <span class="meta" style="margin-left:8px">${m.date}</span>
        ${m.starred ? '<span class="badge">⭐</span>' : ''}
      </div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${m.content}</div>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="toggleStar('${m.filename}', ${!m.starred})">${m.starred ? 'スター解除' : '⭐スター'}</button>
        <button onclick="toggleArchive('${m.filename}', ${!m.archived})">${m.archived ? '受信トレイへ' : 'アーカイブ'}</button>
      </div>
    </div>`).join('');
}

async function toggleStar(fn, starred) {
  await fetch(`/api/mailbox/${fn}/meta`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({starred})
  });
  loadMail('inbox');
}
async function toggleArchive(fn, archived) {
  await fetch(`/api/mailbox/${fn}/meta`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({archived})
  });
  loadMail('inbox');
}

// Chat
async function loadChat() {
  const log = await fetch(`/api/${currentCharacterId}/chat/history`).then(r => r.json());
  const el = document.getElementById('chat-list');
  el.innerHTML = log.map(e => `
    <div class="card">
      <div class="row"><strong>${e.role}</strong><span class="meta" style="margin-left:8px">${new Date(e.timestamp).toLocaleString('ja-JP')}</span></div>
      <div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${e.text}</div>
    </div>`).join('') || '<p style="color:#999;padding:8px">まだ会話なし</p>';
  el.scrollTop = el.scrollHeight;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('chat-send-btn');
  const statusEl = document.getElementById('chat-status');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  btn.disabled = true;
  const charId = currentCharacterId;
  // While the request is in flight, poll the lock status so we can show
  // "<name>と話し中…" if this character is currently serving someone else
  // (Phase C: one character = one mind, requests to the same character are
  // queued behind an asyncio lock).
  const statusTimer = setInterval(async () => {
    try {
      const s = await fetch(`/api/${charId}/chat/status`).then(r => r.json());
      statusEl.textContent = s.busy ? `${s.partner_name}と話し中…` : '考え中…';
    } catch (e) { /* ignore transient poll errors */ }
  }, 800);
  statusEl.textContent = '考え中…';
  try {
    await fetch(`/api/${charId}/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message})
    });
  } finally {
    clearInterval(statusTimer);
    statusEl.textContent = '';
    btn.disabled = false;
  }
  if (charId === currentCharacterId) loadChat();
}

// Group chat (Phase C)
async function loadGroupChat() {
  const log = await fetch('/api/group/history').then(r => r.json());
  const el = document.getElementById('group-chat-list');
  el.innerHTML = log.map(e => `
    <div class="card">
      <div class="row"><strong style="color:${e.color || '#333'}">${e.name}</strong><span class="meta" style="margin-left:8px">${new Date(e.timestamp).toLocaleString('ja-JP')}</span></div>
      <div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${e.text}</div>
    </div>`).join('') || '<p style="color:#999;padding:8px">まだグループ会話なし</p>';
  el.scrollTop = el.scrollHeight;
}

async function sendGroupChat() {
  const input = document.getElementById('group-chat-input');
  const btn = document.getElementById('group-send-btn');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  btn.disabled = true;
  const el = document.getElementById('group-chat-list');
  el.insertAdjacentHTML('beforeend', `<div class="card"><strong>${ME ? ME.name : ''}</strong><div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${message}</div></div>`);
  const thinking = document.createElement('div');
  thinking.className = 'card meta';
  thinking.textContent = 'みんなに聞いてる…';
  el.appendChild(thinking);
  el.scrollTop = el.scrollHeight;
  try {
    const res = await fetch('/api/group/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message})
    });
    if (!res.ok || !res.body) throw new Error('stream failed');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      let idx;
      while ((idx = buf.indexOf('\\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        const r = JSON.parse(line);
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `<strong style="color:${r.color}">${r.name}</strong><div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${r.reply}</div>`;
        el.insertBefore(card, thinking);
        el.scrollTop = el.scrollHeight;
      }
    }
  } catch (e) {
    thinking.textContent = 'エラーが発生しました';
    return;
  } finally {
    btn.disabled = false;
  }
  thinking.remove();
}

// Records
async function loadRecordsList() {
  const files = await fetch(`/api/${currentCharacterId}/records`).then(r => r.json());
  const el = document.getElementById('records-files');
  el.innerHTML = files.map(f => `<button onclick="loadRecord('${f}')" style="margin:2px 4px 2px 0;font-size:12px">${f}</button>`).join('') || '<p style="color:#999;padding:8px">記録なし</p>';
  document.getElementById('records-detail').innerHTML = '';
}

async function loadRecord(fn) {
  const events = await fetch(`/api/${currentCharacterId}/records/${fn}`).then(r => r.json());
  const el = document.getElementById('records-detail');
  el.innerHTML = events.map(e => {
    if (e.kind === 'text') return `<div class="card">💬 ${e.text}</div>`;
    if (e.kind === 'thinking') return `<div class="card meta">🤔 ${e.text}</div>`;
    if (e.kind === 'tool_call') return `<div class="card meta">🔧 ${e.name}(${JSON.stringify(e.input)})</div>`;
    if (e.kind === 'result') return `<div class="card meta">✅ ${e.turns}ターン · $${e.cost_usd}</div>`;
    return '';
  }).join('');
}

// Diary
async function loadDiary() {
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/${currentCharacterId}/diary/${date}`).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || `(${j.count}件の会話。まだ日記なし)`;
}

async function summarizeDiary() {
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/${currentCharacterId}/diary/${date}/summarize`, {method: 'POST'}).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || '(会話がありませんでした)';
}

// Load characters, then the default tab, on start
loadCharacters();
</script>
</body>
</html>
"""
