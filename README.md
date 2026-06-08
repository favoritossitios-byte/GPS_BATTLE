# TurfWar Lisboa 🎨

App tipo "Splatoon real-life": cada jogador escolhe nome + cor, anda por
Lisboa, e o caminho dele fica pintado num mapa. Se passas por cima da tinta
de outro jogador, ela passa a ser tua. Ranking ao vivo no canto.

MVP para correr no browser e jogar com um grupo pequeno de amigos.

## Setup

Precisas de Python 3.10+.

```bash
cd turfWar
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r server/requirements.txt
```

## Correr

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

Abre `http://localhost:8000` no browser. Põe nome + cor → Entrar.
O browser vai pedir permissão de localização — aceita.

## Jogar com amigos

Para testes na mesma rede WiFi, partilha o teu IP local
(`http://192.168.x.y:8000`). **Importante:** o `navigator.geolocation` só
funciona em `https://` ou `localhost` — para amigos fora da rede precisas
de TLS. A forma mais simples:

```bash
# noutro terminal, depois do uvicorn estar a correr
ngrok http 8000
```

E partilha o URL `https://...ngrok.app`.

## Como funciona

- **Grelha** geográfica fixa de ~2m × 2m. `server/grid.py` arredonda lat/lon.
- **A cada ping de GPS**, pintas a célula onde estás + as 8 vizinhas (~6m²).
  Compromisso para disfarçar GPS jitter; podes afinar em `grid.py:neighbors_3x3`.
- **Captura**: ao pintar uma célula que era de outro player, ela passa a tua.
  O ranking é simplesmente a contagem de células por player.
- **Sanity GPS**: pings com precisão > 50m ou que indiquem teleportes
  (>100m em <2s) são descartados. Configurável em `server/game.py`.
- **Estado em memória**: quando matas o `uvicorn`, o jogo é reiniciado.
  É de propósito (MVP); persistência fica para depois.
- **Limites de Lisboa**: pings fora do bounding box de Lisboa são ignorados,
  para não pintares no estrangeiro por engano.

## Ficheiros

```
server/
  grid.py        # célula <-> lat/lon
  game.py        # GameState, validação, captura, anti-bug GPS
  main.py        # FastAPI + WebSocket + serve estáticos
static/
  index.html     # ecrã de login + container do mapa
  style.css      # layout
  app.js         # Leaflet, WS, geolocation, render
```

## Próximos passos (se isto fizer sentido evoluir)

- Persistência (SQLite ou Redis).
- PWA / instalar no telemóvel ("ecrã principal").
- Matchmaking / jogos com início e fim ("primeiro a 1000 blocos").
- Power-ups (apagar tinta de outros, sprint).
- Áreas para lá de Lisboa.
