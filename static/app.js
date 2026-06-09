/* TurfWar — cliente: login, mapa, WS, geolocation, render. */

(() => {
  // ---------- Constantes ----------
  const LISBON_CENTER = [38.7223, -9.1393];
  const LISBON_ZOOM = 16;
  const POSITRON_URL =
    "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  const POSITRON_ATTR =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

  // Bounding box da zona de jogo (tem de coincidir com server/game.py LISBON_BBOX)
  const GAME_BBOX = [[38.60, -9.45], [39.00, -8.93]];

  // Mantém em sincronia com server/grid.py
  const LAT_STEP = 2.0 / 111320.0;
  const LON_STEP = 2.0 / 86900.0;

  // Histórico offline: máx. 1 hora de pings guardados localmente
  const OFFLINE_MAX_AGE_MS = 3600 * 1000;
  const OFFLINE_MAX_PINGS = 3600; // ~1/s durante 1h

  // ---------- Cor aleatória ----------
  function randomColor() {
    // Gera matiz aleatório, saturação e luminosidade fixas para cores vivas
    const h = Math.floor(Math.random() * 360);
    const s = 75 + Math.floor(Math.random() * 20); // 75-95%
    const l = 45 + Math.floor(Math.random() * 15); // 45-60%
    // Converte HSL -> HEX
    const hsl2rgb = (h, s, l) => {
      s /= 100; l /= 100;
      const k = n => (n + h / 30) % 12;
      const a = s * Math.min(l, 1 - l);
      const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
      return [f(0), f(8), f(4)].map(v => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
    };
    return "#" + hsl2rgb(h, s, l);
  }

  // ---------- Login ----------
  const loginEl = document.getElementById("login");
  const nameInput = document.getElementById("nameInput");
  const colorInput = document.getElementById("colorInput");
  const loginForm = document.getElementById("loginForm");
  const loginErr = document.getElementById("loginError");

  // Restaurar última escolha; cor aleatória se for a primeira vez
  const saved = JSON.parse(localStorage.getItem("turfwar.profile") || "{}");
  if (saved.name) nameInput.value = saved.name;
  colorInput.value = saved.color || randomColor();

  loginForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = nameInput.value.trim();
    const color = colorInput.value;
    if (!name) {
      loginErr.textContent = "Põe um nome.";
      loginErr.hidden = false;
      return;
    }
    localStorage.setItem("turfwar.profile", JSON.stringify({ name, color }));
    loginEl.style.display = "none";
    start(name, color);
  });

  // ---------- Mapa ----------
  let map, cellsLayer, meMarker;
  // cellKey "r,c" -> { rect: L.Rectangle, owner: pid }
  const cellViews = new Map();
  // pid -> { name, color, id }
  const players = new Map();
  // pid -> count
  const counts = new Map();
  // pid -> online bool
  const online = new Map();
  let myId = null;
  let myColor = "#ff3366";

  function initMap() {
    map = L.map("map", { zoomControl: true }).setView(LISBON_CENTER, LISBON_ZOOM);
    L.tileLayer(POSITRON_URL, {
      attribution: POSITRON_ATTR,
      maxZoom: 20,
      subdomains: "abcd",
    }).addTo(map);
    cellsLayer = L.layerGroup().addTo(map);

    // Zona de jogo
    L.rectangle(GAME_BBOX, {
      color: "#2563eb",
      weight: 2,
      fill: false,
      dashArray: "6 4",
      interactive: false,
    }).addTo(map);
  }

  function cellBounds(r, c) {
    const south = r * LAT_STEP;
    const north = south + LAT_STEP;
    const west = c * LON_STEP;
    const east = west + LON_STEP;
    return [[south, west], [north, east]];
  }

  function paintCell(r, c, ownerId) {
    const key = `${r},${c}`;
    const owner = players.get(ownerId);
    const color = owner ? owner.color : "#888";
    const existing = cellViews.get(key);
    if (existing) {
      if (existing.owner === ownerId) return;
      existing.owner = ownerId;
      existing.rect.setStyle({ fillColor: color, color });
      return;
    }
    const rect = L.rectangle(cellBounds(r, c), {
      stroke: false,
      fillColor: color,
      fillOpacity: 0.55,
      interactive: false,
    });
    rect.addTo(cellsLayer);
    cellViews.set(key, { rect, owner: ownerId });
  }

  // ---------- Ranking ----------
  const rankingList = document.getElementById("rankingList");
  const myScoreEl = document.getElementById("myScore");
  const myNameEl = document.getElementById("myName");
  const connStatus = document.getElementById("connStatus");

  function renderRanking() {
    // Mostra TODOS os jogadores (online e offline), ordenados por células
    const rows = [...players.values()]
      .map((p) => ({ ...p, count: counts.get(p.id) || 0, isOnline: online.get(p.id) || false }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

    rankingList.innerHTML = "";
    for (const r of rows) {
      const li = document.createElement("li");
      if (r.id === myId) li.classList.add("you");
      if (!r.isOnline) li.classList.add("offline");
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = r.color;
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = r.name + (r.id === myId ? " (tu)" : "");
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = r.count;
      li.append(sw, name, count);
      rankingList.appendChild(li);
    }

    // Painel direito — só a pontuação própria
    if (myId) {
      const me = players.get(myId);
      if (me) myNameEl.textContent = me.name;
      myScoreEl.textContent = counts.get(myId) || 0;
    }
  }

  // ---------- WebSocket ----------
  let ws, wsReady = false;
  // Fila de posições offline: pings guardados quando WS não está pronto
  let offlineQueue = [];

  function flushOfflineQueue() {
    if (!wsReady || offlineQueue.length === 0) return;
    const now = Date.now();
    // Descartar pings mais antigos que 1 hora
    offlineQueue = offlineQueue.filter(p => now - p.ts * 1000 <= OFFLINE_MAX_AGE_MS);
    if (offlineQueue.length === 0) return;
    ws.send(JSON.stringify({ type: "batch", pings: offlineQueue }));
    offlineQueue = [];
  }

  function connect(name, color) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws?name=${encodeURIComponent(
      name
    )}&color=${encodeURIComponent(color)}`;
    ws = new WebSocket(url);
    connStatus.textContent = "a ligar…";

    ws.addEventListener("open", () => {
      wsReady = true;
      connStatus.textContent = "ligado";
      flushOfflineQueue();
    });
    ws.addEventListener("close", () => {
      wsReady = false;
      connStatus.textContent = "ligação perdida";
      // Tentar reconectar após 3s
      setTimeout(() => connect(
        JSON.parse(localStorage.getItem("turfwar.profile") || "{}").name || "",
        JSON.parse(localStorage.getItem("turfwar.profile") || "{}").color || myColor
      ), 3000);
    });
    ws.addEventListener("error", () => {
      connStatus.textContent = "erro de ligação";
    });
    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleMessage(msg);
    });
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "error":
        alert("Erro do servidor: " + msg.message);
        break;
      case "hello": {
        myId = msg.you;
        players.clear();
        for (const [pid, p] of Object.entries(msg.players)) players.set(pid, p);
        counts.clear();
        for (const [pid, n] of Object.entries(msg.counts)) counts.set(pid, n);
        online.clear();
        for (const [pid, isOn] of Object.entries(msg.online || {})) online.set(pid, isOn);
        // Células iniciais — players offline já estão em `players`, logo a cor é correcta
        for (const [r, c, ownerId] of msg.cells) paintCell(r, c, ownerId);
        renderRanking();
        break;
      }
      case "join":
        players.set(msg.player.id, msg.player);
        if (!counts.has(msg.player.id)) counts.set(msg.player.id, 0);
        online.set(msg.player.id, msg.online ?? true);
        renderRanking();
        break;
      case "leave":
        online.set(msg.id, false);
        // Mantemos em `players` e `counts` — células continuam coloridas e visíveis no ranking
        renderRanking();
        break;
      case "paint":
        for (const [r, c, ownerId] of msg.changes) paintCell(r, c, ownerId);
        counts.clear();
        for (const [pid, n] of Object.entries(msg.counts)) counts.set(pid, n);
        renderRanking();
        break;
    }
  }

  // ---------- Geolocation ----------
  let lastSentAt = 0;
  const SEND_MIN_INTERVAL = 1000; // 1 ping/s

  function startGeolocation() {
    if (!("geolocation" in navigator)) {
      connStatus.textContent = "GPS não suportado neste browser.";
      return;
    }
    navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        // Marcador "tu"
        if (!meMarker) {
          meMarker = L.circleMarker([latitude, longitude], {
            radius: 8,
            color: "#fff",
            weight: 2,
            fillColor: myColor,
            fillOpacity: 1,
          }).addTo(map);
          map.setView([latitude, longitude], 18);
        } else {
          meMarker.setLatLng([latitude, longitude]);
        }

        const now = Date.now();
        if (now - lastSentAt < SEND_MIN_INTERVAL) return;
        lastSentAt = now;

        const ping = {
          type: "pos",
          lat: latitude,
          lon: longitude,
          acc: accuracy,
          ts: Math.floor(now / 1000),
        };

        if (wsReady) {
          ws.send(JSON.stringify(ping));
        } else {
          // Guardar na fila offline (máx. 1h de histórico)
          offlineQueue.push(ping);
          const cutoff = now - OFFLINE_MAX_AGE_MS;
          if (offlineQueue.length > OFFLINE_MAX_PINGS) {
            offlineQueue = offlineQueue.filter(p => p.ts * 1000 >= cutoff);
          }
        }
      },
      (err) => {
        connStatus.textContent = "GPS: " + err.message;
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 15000 }
    );
  }

  // ---------- Boot ----------
  function start(name, color) {
    myColor = color;
    document.getElementById("ranking").hidden = false;
    document.getElementById("rankingRight").hidden = false;
    initMap();
    connect(name, color);
    startGeolocation();
  }
})();

