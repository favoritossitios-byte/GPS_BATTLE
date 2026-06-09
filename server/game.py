"""Estado de jogo em memória.

Tudo single-process; protegemos com um asyncio.Lock para os caminhos críticos
(paint/captura) já que pode haver múltiplos clientes a enviar pings em paralelo.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .grid import Cell, cell_of, neighbors_r5m
from . import persist

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
NAME_MAX = 20

# Limites razoáveis de Lisboa (com folga). Pings fora disto são ignorados —
# evita pintar em sítios absurdos por GPS bug ou se alguém abrir noutro país.
LISBON_BBOX = {"south": 38.60, "north": 39.00, "west": -9.45, "east": -8.93}

# Sanity de GPS
MAX_ACCURACY_M = 50.0   # acc pior que isto = ignorar
MAX_JUMP_M = 100.0      # salto > 100m em < 2s = ignorar
JUMP_WINDOW_S = 2.0
MAX_HISTORY_AGE_S = 3600.0  # histórico offline — pings com mais de 1h são ignorados


@dataclass
class Player:
    id: str
    name: str
    color: str
    last_pos: tuple[float, float] | None = None
    last_pos_t: float = 0.0
    last_seen: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color}


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ValueError(f"Nome inválido (1-{NAME_MAX} chars).")
    return name


def validate_color(color: str) -> str:
    color = (color or "").strip()
    if not COLOR_RE.match(color):
        raise ValueError("Cor inválida — usar formato #RRGGBB.")
    return color.lower()


class GameState:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = os.environ.get("DB_PATH", "turfwar.db")
        self._conn = persist.open_db(db_path)

        # Carregar estado persistido
        loaded_players, loaded_cells = persist.load_state(self._conn)

        # pid -> Player (inclui offline)
        self.players: dict[str, Player] = loaded_players
        # name (lower) -> pid — para reconectar pelo nome
        self._name_index: dict[str, str] = {
            p.name.lower(): p.id for p in loaded_players.values()
        }
        # pid -> online?
        self.online: dict[str, bool] = {pid: False for pid in loaded_players}
        # cell -> player_id (último dono)
        self.cells: dict[Cell, str] = loaded_cells
        # player_id -> contagem de células (cache para ranking)
        self.counts: dict[str, int] = {}
        for pid in loaded_players:
            self.counts[pid] = 0
        for owner_id in loaded_cells.values():
            self.counts[owner_id] = self.counts.get(owner_id, 0) + 1

        self._lock = asyncio.Lock()

    # ---- gestão de jogadores ----

    async def add_player(self, name: str, color: str) -> Player:
        name = validate_name(name)
        color = validate_color(color)
        async with self._lock:
            key = name.lower()
            existing_pid = self._name_index.get(key)
            if existing_pid and existing_pid in self.players:
                # Reconectar — restaurar progresso, actualizar cor se mudou
                p = self.players[existing_pid]
                p.color = color
                p.last_seen = time.time()
                self.online[existing_pid] = True
                persist.save_player(self._conn, p)
                return p
            pid = uuid.uuid4().hex[:12]
            p = Player(id=pid, name=name, color=color)
            self.players[pid] = p
            self._name_index[key] = pid
            self.counts[pid] = 0
            self.online[pid] = True
            persist.save_player(self._conn, p)
            return p

    async def remove_player(self, pid: str) -> None:
        async with self._lock:
            self.online[pid] = False
            p = self.players.get(pid)
            if p:
                p.last_seen = time.time()
                persist.save_player(self._conn, p)

    # ---- pintar ----

    def _is_in_lisbon(self, lat: float, lon: float) -> bool:
        b = LISBON_BBOX
        return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]

    async def paint(
        self, pid: str, lat: float, lon: float, acc: float, ts: float | None = None
    ) -> tuple[list[tuple[int, int, str]], dict[str, int]] | None:
        """Aplica um ping de GPS. Devolve (changes, counts_atualizados) ou None
        se o ping foi descartado.

        `changes` é a lista de células efetivamente alteradas (incluindo capturas):
            [(row, col, new_owner_id), ...]

        `ts` é o timestamp Unix (segundos) do ping — pode vir do histórico offline.
        Pings com mais de MAX_HISTORY_AGE_S são ignorados.
        """
        if acc is None or acc > MAX_ACCURACY_M:
            return None
        if not self._is_in_lisbon(lat, lon):
            return None

        now = time.time()
        ping_ts = ts if (ts is not None and ts > 0) else now
        # Rejeitar pings demasiado antigos
        if now - ping_ts > MAX_HISTORY_AGE_S:
            return None

        async with self._lock:
            p = self.players.get(pid)
            if p is None:
                return None

            # Detetar teleportes — usar timestamps dos próprios pings
            if p.last_pos is not None and (ping_ts - p.last_pos_t) < JUMP_WINDOW_S:
                if _haversine_m(p.last_pos, (lat, lon)) > MAX_JUMP_M:
                    return None

            p.last_pos = (lat, lon)
            p.last_pos_t = ping_ts
            p.last_seen = now

            here = cell_of(lat, lon)
            changes: list[tuple[int, int, str]] = []
            for cell in neighbors_r5m(here):
                old = self.cells.get(cell)
                if old == pid:
                    continue
                if old is not None:
                    self.counts[old] = max(0, self.counts.get(old, 0) - 1)
                self.cells[cell] = pid
                self.counts[pid] = self.counts.get(pid, 0) + 1
                changes.append((cell[0], cell[1], pid))

            if not changes:
                return None
            persist.save_cells(self._conn, changes)
            return changes, dict(self.counts)

    # ---- snapshots ----

    def snapshot(self) -> dict:
        return {
            "players": {pid: p.public() for pid, p in self.players.items()},
            "cells": [(r, c, owner) for (r, c), owner in self.cells.items()],
            "counts": dict(self.counts),
            "online": {pid: self.online.get(pid, False) for pid in self.players},
        }
