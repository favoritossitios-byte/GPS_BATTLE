"""Persistência SQLite para o TurfWar.

Usa sqlite3 da stdlib — zero dependências extra.
Todas as chamadas são síncronas e executadas dentro do asyncio.Lock do GameState,
por isso não há risco de concorrência.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game import Player
    from .grid import Cell

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id        TEXT PRIMARY KEY,
    name      TEXT UNIQUE NOT NULL,
    color     TEXT NOT NULL,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cells (
    row      INTEGER NOT NULL,
    col      INTEGER NOT NULL,
    owner_id TEXT    NOT NULL REFERENCES players(id),
    PRIMARY KEY (row, col)
);
"""


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")   # mais resistente a crashes
    conn.execute("PRAGMA synchronous=NORMAL") # boa velocidade + seguro
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def load_state(conn: sqlite3.Connection):
    """Devolve (players_dict, cells_dict) carregados da DB.

    players_dict: {pid: Player}
    cells_dict:   {(row, col): owner_id}
    """
    from .game import Player  # import local para evitar circular

    players: dict[str, Player] = {}
    rows = conn.execute(
        "SELECT id, name, color, last_seen FROM players"
    ).fetchall()
    for pid, name, color, last_seen in rows:
        p = Player(id=pid, name=name, color=color, last_seen=last_seen)
        players[pid] = p

    cells: dict[tuple[int, int], str] = {}
    for row, col, owner_id in conn.execute(
        "SELECT row, col, owner_id FROM cells"
    ).fetchall():
        cells[(row, col)] = owner_id

    return players, cells


def save_player(conn: sqlite3.Connection, player: Player) -> None:
    conn.execute(
        """
        INSERT INTO players (id, name, color, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET color=excluded.color, last_seen=excluded.last_seen
        """,
        (player.id, player.name, player.color, player.last_seen),
    )
    conn.commit()


def save_cells(
    conn: sqlite3.Connection,
    changes: list[tuple[int, int, str]],
) -> None:
    """Upsert em batch de células alteradas."""
    conn.executemany(
        """
        INSERT INTO cells (row, col, owner_id) VALUES (?, ?, ?)
        ON CONFLICT(row, col) DO UPDATE SET owner_id=excluded.owner_id
        """,
        changes,
    )
    conn.commit()
