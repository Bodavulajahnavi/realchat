(() => {
  // ---------- Screens ----------
  const authScreen = document.getElementById('auth-screen');
  const lobbyScreen = document.getElementById('lobby-screen');
  const chatScreen = document.getElementById('chat-screen');

  function showScreen(el) {
    [authScreen, lobbyScreen, chatScreen].forEach((s) => s.classList.add('hidden'));
    el.classList.remove('hidden');
  }

  // ---------- Auth elements ----------
  const authForm = document.getElementById('auth-form');
  const authUsername = document.getElementById('auth-username');
  const authPassword = document.getElementById('auth-password');
  const authSubmit = document.getElementById('auth-submit');
  const authError = document.getElementById('auth-error');
  const authSubtitle = document.getElementById('auth-subtitle');
  const tabs = document.querySelectorAll('.tab');
  let authMode = 'login'; // or 'signup'

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      tabs.forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      authMode = tab.dataset.tab;
      authSubmit.textContent = authMode === 'login' ? 'LOG IN' : 'SIGN UP';
      authSubtitle.textContent = authMode === 'login' ? 'sign in to key up' : 'create a call sign';
      authError.textContent = '';
    });
  });

  // ---------- Lobby elements ----------
  const lobbyYouName = document.getElementById('lobby-you-name');
  const logoutBtn = document.getElementById('logout-btn');
  const createRoomForm = document.getElementById('create-room-form');
  const newRoomName = document.getElementById('new-room-name');
  const roomError = document.getElementById('room-error');
  const roomList = document.getElementById('room-list');

  // ---------- Chat elements ----------
  const backToLobby = document.getElementById('back-to-lobby');
  const youName = document.getElementById('you-name');
  const rosterList = document.getElementById('roster-list');
  const log = document.getElementById('log');
  const typingIndicator = document.getElementById('typing-indicator');
  const messageForm = document.getElementById('message-form');
  const messageInput = document.getElementById('message-input');
  const clockEl = document.getElementById('clock');
  const roomTitle = document.getElementById('room-title');

  // ---------- State ----------
  let token = localStorage.getItem('transmission_token') || '';
  let myName = localStorage.getItem('transmission_username') || '';
  let currentRoom = null;
  let ws = null;
  let typingTimeout = null;
  const typingUsers = new Set();

  function tickClock() {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString([], { hour12: false });
  }
  setInterval(tickClock, 1000);
  tickClock();

  function fmtTime(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  // ---------- API helper ----------
  async function api(path, options = {}) {
    const headers = Object.assign(
      { 'Content-Type': 'application/json' },
      token ? { Authorization: `Bearer ${token}` } : {},
      options.headers || {}
    );
    const res = await fetch(path, { ...options, headers });
    if (res.status === 401) {
      logout();
      throw new Error('Session expired. Please log in again.');
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || 'Something went wrong.');
    }
    return data;
  }

  // ---------- Auth ----------
  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    authError.textContent = '';
    const username = authUsername.value.trim();
    const password = authPassword.value;
    if (!username || !password) return;

    try {
      const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/signup';
      const data = await api(endpoint, {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      token = data.access_token;
      myName = data.username;
      localStorage.setItem('transmission_token', token);
      localStorage.setItem('transmission_username', myName);
      authPassword.value = '';
      enterLobby();
    } catch (err) {
      authError.textContent = err.message;
    }
  });

  function logout() {
    if (ws) { ws.close(); ws = null; }
    token = '';
    myName = '';
    localStorage.removeItem('transmission_token');
    localStorage.removeItem('transmission_username');
    authUsername.value = '';
    authPassword.value = '';
    showScreen(authScreen);
  }
  logoutBtn.addEventListener('click', logout);

  // ---------- Lobby ----------
  async function enterLobby() {
    lobbyYouName.textContent = myName;
    showScreen(lobbyScreen);
    await refreshRooms();
  }

  async function refreshRooms() {
    roomError.textContent = '';
    try {
      const rooms = await api('/api/rooms');
      renderRooms(rooms);
    } catch (err) {
      roomError.textContent = err.message;
    }
  }

  function renderRooms(rooms) {
    roomList.innerHTML = '';
    if (rooms.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-note';
      empty.textContent = 'No frequencies yet — create the first one above.';
      roomList.appendChild(empty);
      return;
    }
    rooms.forEach((room) => {
      const li = document.createElement('li');
      li.className = 'room-item';
      li.innerHTML = `
        <div>
          <div class="room-name">#${room.name}</div>
          <div class="room-meta">opened by ${room.created_by}</div>
        </div>
        <span class="room-meta">TUNE IN →</span>
      `;
      li.addEventListener('click', () => enterRoom(room));
      roomList.appendChild(li);
    });
  }

  createRoomForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    roomError.textContent = '';
    const name = newRoomName.value.trim();
    if (!name) return;
    try {
      const room = await api('/api/rooms', {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
      newRoomName.value = '';
      await refreshRooms();
    } catch (err) {
      roomError.textContent = err.message;
    }
  });

  backToLobby.addEventListener('click', () => {
    if (ws) { ws.close(); ws = null; }
    currentRoom = null;
    enterLobby();
  });

  // ---------- Chat ----------
  async function enterRoom(room) {
    currentRoom = room;
    youName.textContent = myName;
    roomTitle.textContent = `#${room.name}`;
    log.innerHTML = '';
    rosterList.innerHTML = '';
    typingUsers.clear();
    renderTyping();
    showScreen(chatScreen);

    try {
      const history = await api(`/api/rooms/${room.id}/messages`);
      history.forEach((m) => addMessage(m.user, m.text, m.ts, m.user === myName));
    } catch (err) {
      addSystemEntry(`Could not load history: ${err.message}`);
    }

    connectWs(room.id);
    messageInput.focus();
  }

  function connectWs(roomId) {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${protocol}://${window.location.host}/ws/${roomId}?token=${encodeURIComponent(token)}`;
    ws = new WebSocket(url);

    ws.onclose = (event) => {
      if (currentRoom && currentRoom.id === roomId) {
        if (event.code === 4001) {
          addSystemEntry('SESSION EXPIRED — please log back in');
          logout();
        } else if (event.code === 4009) {
          addSystemEntry('Already connected to this frequency in another tab.');
        } else {
          addSystemEntry('CONNECTION LOST — signal dropped');
        }
      }
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case 'roster':
          renderRoster(data.users);
          break;
        case 'system':
          renderRoster(data.users);
          addSystemEntry(
            data.event === 'join' ? `${data.user} came on air` : `${data.user} went off air`
          );
          typingUsers.delete(data.user);
          renderTyping();
          break;
        case 'message':
          addMessage(data.user, data.text, data.ts, data.user === myName);
          typingUsers.delete(data.user);
          renderTyping();
          break;
        case 'typing':
          if (data.state) typingUsers.add(data.user);
          else typingUsers.delete(data.user);
          renderTyping();
          break;
      }
    };
  }

  function scrollToBottom() {
    log.scrollTop = log.scrollHeight;
  }

  function addMessage(user, text, ts, mine) {
    const entry = document.createElement('div');
    entry.className = 'entry' + (mine ? ' mine' : '');
    const meta = document.createElement('div');
    meta.className = 'entry-meta';
    meta.textContent = `${user} · ${fmtTime(ts)}`;
    const body = document.createElement('div');
    body.className = 'entry-body';
    body.textContent = text;
    entry.appendChild(meta);
    entry.appendChild(body);
    log.appendChild(entry);
    scrollToBottom();
  }

  function addSystemEntry(text) {
    const entry = document.createElement('div');
    entry.className = 'system-entry';
    entry.textContent = text;
    log.appendChild(entry);
    scrollToBottom();
  }

  function renderRoster(users) {
    rosterList.innerHTML = '';
    users.forEach((u) => {
      const li = document.createElement('li');
      li.textContent = u === myName ? `${u} (you)` : u;
      rosterList.appendChild(li);
    });
  }

  function renderTyping() {
    const others = [...typingUsers].filter((u) => u !== myName);
    if (others.length === 0) typingIndicator.textContent = '';
    else if (others.length === 1) typingIndicator.textContent = `${others[0]} is transmitting…`;
    else typingIndicator.textContent = `${others.join(', ')} are transmitting…`;
  }

  messageForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'message', text }));
    messageInput.value = '';
    sendTyping(false);
  });

  function sendTyping(state) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'typing', state }));
  }

  messageInput.addEventListener('input', () => {
    sendTyping(true);
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => sendTyping(false), 1200);
  });

  // ---------- Boot ----------
  (async function boot() {
    if (token && myName) {
      try {
        await api('/api/auth/me');
        await enterLobby();
        return;
      } catch {
        // token invalid/expired — fall through to auth screen
      }
    }
    showScreen(authScreen);
  })();
})();
