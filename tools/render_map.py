#!/usr/bin/env python3
"""Render a heatmap/graph view of a single map_id from map_learning.json.

Shows discovered tiles, directional edges, and (now) blocked directions
as short magenta spokes inside each tile.

Usage:
    python tools/render_map.py --map-id 3 \
        --input map_learning.json \
        --output map_3.png

Requires Pillow (pip install pillow).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, ImageDraw

CELL_PX = 36
MARGIN = 40
EDGE_COLORS = {
    "UP": (52, 120, 246),
    "DOWN": (52, 120, 246),
    "LEFT": (62, 180, 137),
    "RIGHT": (62, 180, 137),
    "WARP": (220, 76, 100),
}
BLOCKED_COLOR = (230, 86, 120)
DIRECTION_OFFSETS = {
    "UP": (0, -1),
    "DOWN": (0, 1),
    "LEFT": (-1, 0),
    "RIGHT": (1, 0),
}


@dataclass
class Tile:
    map_id: int
    x: int
    y: int
    edges: Dict[str, Dict[str, int]]


def load_tiles(path: Path, target_map: int) -> Tuple[Dict[Tuple[int, int], Tile], Dict[Tuple[int, int], Dict[str, int]]]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "tiles" in raw:
        data = raw.get("tiles", {})
        blocked_raw = raw.get("blocked", {})
    else:
        data = raw
        blocked_raw = {}

    tiles: Dict[Tuple[int, int], Tile] = {}
    for key, edges in data.items():
        try:
            map_id, x, y = map(int, key.split(":"))
        except ValueError:
            continue
        if map_id != target_map:
            continue
        tiles[(x, y)] = Tile(map_id=map_id, x=x, y=y, edges=edges)

    blocked: Dict[Tuple[int, int], Dict[str, int]] = {}
    for key, directions in blocked_raw.items():
        try:
            map_id, x, y = map(int, key.split(":"))
        except ValueError:
            continue
        if map_id != target_map:
            continue
        if not isinstance(directions, dict):
            continue
        blocked[(x, y)] = {dir_name: int(count) for dir_name, count in directions.items()}

    return tiles, blocked


def color_for_tile(tile: Tile) -> Tuple[int, int, int]:
    degree = len(tile.edges)
    ratio = min(1.0, degree / 4)
    # Gradient from light gray to deep teal
    r = int(240 - 120 * ratio)
    g = int(240 - 160 * ratio)
    b = int(240 - 40 * ratio)
    return (r, g, b)


def render_map(
    tiles: Dict[Tuple[int, int], Tile],
    blocked: Dict[Tuple[int, int], Dict[str, int]],
    target_map: int,
    output: Path,
) -> None:
    if not tiles:
        raise SystemExit(f"No tiles recorded for map_id {target_map}")

    xs = [x for (x, _) in tiles]
    ys = [y for (_, y) in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x + 1) * CELL_PX + MARGIN * 2
    height = (max_y - min_y + 1) * CELL_PX + MARGIN * 2

    img = Image.new("RGB", (width, height), color=(20, 24, 31))
    draw = ImageDraw.Draw(img)

    # Grid + tiles
    for (x, y), tile in tiles.items():
        col = x - min_x
        row = y - min_y
        left = MARGIN + col * CELL_PX
        top = MARGIN + row * CELL_PX
        right = left + CELL_PX
        bottom = top + CELL_PX
        draw.rectangle([left, top, right, bottom], fill=color_for_tile(tile), outline=(45, 52, 63))

        # Coordinate label
        label = f"{x},{y}"
        draw.text((left + 4, top + 4), label, fill=(30, 30, 30))

    # Blocked directions overlay
    for (x, y), directions in blocked.items():
        if (x, y) not in tiles:
            continue
        col = x - min_x
        row = y - min_y
        cx = MARGIN + col * CELL_PX + CELL_PX // 2
        cy = MARGIN + row * CELL_PX + CELL_PX // 2
        for direction in directions:
            offset = DIRECTION_OFFSETS.get(direction)
            if not offset:
                continue
            dx, dy = offset
            length = CELL_PX // 2 - 6
            draw.line(
                [
                    cx,
                    cy,
                    cx + dx * length,
                    cy + dy * length,
                ],
                fill=BLOCKED_COLOR,
                width=4,
            )

    # Edges/arrows
    for (x, y), tile in tiles.items():
        col = x - min_x
        row = y - min_y
        cx = MARGIN + col * CELL_PX + CELL_PX // 2
        cy = MARGIN + row * CELL_PX + CELL_PX // 2
        for direction, dest in tile.edges.items():
            color = EDGE_COLORS.get(direction, (250, 214, 80))
            if direction == "WARP" or dest.get("map_id") != target_map:
                draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=color, width=2)
                continue
            dx = dest.get("x")
            dy = dest.get("y")
            if dx is None or dy is None:
                continue
            dest_col = dx - min_x
            dest_row = dy - min_y
            dcx = MARGIN + dest_col * CELL_PX + CELL_PX // 2
            dcy = MARGIN + dest_row * CELL_PX + CELL_PX // 2
            draw.line([cx, cy, dcx, dcy], fill=color, width=3)

    # Legend
    legend_y = height - MARGIN + 10
    draw.text((MARGIN, legend_y), f"map_id {target_map}", fill=(235, 235, 235))
    draw.text((MARGIN, legend_y + 18), "Magenta spokes = blocked directions", fill=BLOCKED_COLOR)

    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)
    print(f"Wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a tile heatmap for a map_id from map_learning.json")
    parser.add_argument("--map-id", type=int, required=True, help="Target map ID to render")
    parser.add_argument("--input", type=Path, default=Path("map_learning.json"), help="Path to map_learning.json")
    parser.add_argument("--output", type=Path, default=Path("map.png"), help="Output PNG file")
    args = parser.parse_args()

    tiles, blocked = load_tiles(args.input, args.map_id)
    render_map(tiles, blocked, args.map_id, args.output)


if __name__ == "__main__":
    main()
