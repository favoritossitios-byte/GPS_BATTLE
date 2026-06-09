"""FastAPI app: serve UI estática e WebSocket de jogo.

Correr com:  uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .game import GameState

log = logging.getLogger("turfwar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="TurfWar Lisboa")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
state = GameState()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


class ConnectionManager:
    """Mantém o set de WebSockets ligados e faz broadcast em paralelo."""

    def __init__(self) -> None:
        self._conns: dict[str, WebSocket] = {}  # player_id -> ws

    async def connect(self, pid: str, ws: WebSocket) -> None:
        self._conns[pid] = ws

    def disconnect(self, pid: str) -> None:
        self._conns.pop(pid, None)

    async def broadcast(self, message: dict) -> None:
        if not self._conns:
            return
        data = json.dumps(message)
        # Snapshot da lista para não rebentar se algo for removido a meio.
        targets = list(self._conns.items())
        results = await asyncio.gather(
            *(ws.send_text(data) for _, ws in targets),
            return_exceptions=True,
        )
        for (pid, _), res in zip(targets, results):
            if isinstance(res, Exception):
                self._conns.pop(pid, None)


manager = ConnectionManager()


@app.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    name: str = Query(...),
    color: str = Query(...),
) -> None:
    try:
        player = await state.add_player(name, color)
    except ValueError as exc:
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await ws.close()
        return

    await ws.accept()
    await manager.connect(player.id, ws)
    log.info("player %s (%s) connected", player.name, player.id)

    try:
        # Snapshot inicial
        snap = state.snapshot()
        await ws.send_text(json.dumps({
            "type": "hello",
            "you": player.id,
            **snap,
        }))
        # Notificar os outros
        await manager.broadcast({"type": "join", "player": player.public(), "online": True})

        async for raw in _iter_text(ws):
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "pos":
                pings = [msg]
            elif msg_type == "batch":
                pings = msg.get("pings", [])
                if not isinstance(pings, list):
                    continue
            else:
                continue

            # Processar pings em ordem cronológica
            pings.sort(key=lambda m: m.get("ts", 0))

            all_changes: list = []
            last_counts: dict | None = None
            for ping in pings:
                try:
                    lat = float(ping["lat"]); lon = float(ping["lon"])
                    acc = float(ping.get("acc", 9999))
                    ts = float(ping["ts"]) if "ts" in ping else None
                except (KeyError, TypeError, ValueError):
                    continue
                result = await state.paint(player.id, lat, lon, acc, ts)
                if result is None:
                    continue
                changes, counts = result
                all_changes.extend(changes)
                last_counts = counts

            if all_changes and last_counts is not None:
                await manager.broadcast({
                    "type": "paint",
                    "changes": all_changes,
                    "counts": last_counts,
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error for %s", player.id)
    finally:
        manager.disconnect(player.id)
        await state.remove_player(player.id)
        await manager.broadcast({"type": "leave", "id": player.id})
        log.info("player %s disconnected", player.id)


async def _iter_text(ws: WebSocket):
    """Iterador async sobre mensagens texto. Sai por WebSocketDisconnect."""
    while True:
        yield await ws.receive_text()
