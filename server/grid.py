"""Grelha geográfica fixa de ~2m para o turf war.

Usamos uma grelha simples baseada em arredondamento de lat/lon. Em Lisboa
(~38.72°N) 1° de latitude ≈ 111_320 m e 1° de longitude ≈ 86_900 m, então
uma célula de 2m corresponde a passos de ~1.8e-5° lat e ~2.3e-5° lon.

Não é uma projeção rigorosa — chega-nos para o âmbito (Lisboa, jogo casual).
"""
from __future__ import annotations

CELL_SIZE_M = 2.0
LAT_STEP = CELL_SIZE_M / 111_320.0
LON_STEP = CELL_SIZE_M / 86_900.0

PAINT_RADIUS_M = 20.0  # raio de pintura por ping

Cell = tuple[int, int]  # (row, col) = (lat_idx, lon_idx)


def cell_of(lat: float, lon: float) -> Cell:
    """Devolve o ID da célula que contém (lat, lon)."""
    return (int(lat // LAT_STEP), int(lon // LON_STEP))


def bounds_of(cell: Cell) -> dict:
    """Bounding box geográfico da célula, para o cliente desenhar o retângulo."""
    r, c = cell
    south = r * LAT_STEP
    north = south + LAT_STEP
    west = c * LON_STEP
    east = west + LON_STEP
    return {"south": south, "west": west, "north": north, "east": east}


def neighbors_r5m(cell: Cell) -> list[Cell]:
    """Células dentro do raio PAINT_RADIUS_M a partir do centro (círculo)."""
    r, c = cell
    max_steps = int(PAINT_RADIUS_M / CELL_SIZE_M) + 1
    result = []
    for dr in range(-max_steps, max_steps + 1):
        for dc in range(-max_steps, max_steps + 1):
            if (dr * CELL_SIZE_M) ** 2 + (dc * CELL_SIZE_M) ** 2 <= PAINT_RADIUS_M ** 2:
                result.append((r + dr, c + dc))
    return result

