from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PIL import Image

from .data import Node


def supercover(x0: float, y0: float, x1: float, y1: float,
               rows: int, cols: int) -> set[tuple[int, int]]:
    """相交闭像元集合。坐标以像元边界为整数，包括角点和沿边界两侧。"""
    eps = 1e-10
    if not all(math.isfinite(x) for x in (x0, y0, x1, y1)):
        raise ValueError("栅格坐标非有限值")
    if not all(0 <= x <= cols and 0 <= y <= rows for x, y in [(x0, y0), (x1, y1)]):
        raise ValueError("航段端点超出DEM覆盖范围")
    times = {0.0, 1.0}
    for a, b in [(x0, x1), (y0, y1)]:
        if a != b:
            for boundary in range(math.ceil(min(a, b)), math.floor(max(a, b)) + 1):
                t = (boundary - a) / (b - a)
                if 0 < t < 1:
                    times.add(t)
    ordered = sorted(times)
    samples = ordered + [(a + b) / 2 for a, b in zip(ordered, ordered[1:])]

    def neighbors(x):
        nearest = round(x)
        return (nearest - 1, nearest) if abs(x - nearest) <= eps else (math.floor(x),)

    cells = set()
    for t in samples:
        for col in neighbors(x0 + t * (x1 - x0)):
            for row in neighbors(y0 + t * (y1 - y0)):
                if 0 <= row < rows and 0 <= col < cols:
                    cells.add((row, col))
    return cells


@dataclass(frozen=True)
class Leg:
    start: str
    end: str
    distance: float
    cruise_altitude: float
    climb: float
    descent: float


@dataclass(frozen=True)
class Route:
    service: str
    terrain_max: float
    cell_count: int
    outbound: Leg
    inbound: Leg


class Terrain:
    def __init__(self, path: Path, origin: Node):
        with Image.open(path) as image:
            self.elevations = np.array(image)
            sx, sy, _ = image.tag_v2[33550]
            _, _, _, self.lon0, self.lat0, _ = image.tag_v2[33922]
            keys = image.tag_v2[34735]
        entries = {keys[i]: tuple(keys[i + 1:i + 4]) for i in range(4, len(keys), 4)}
        if entries.get(2048) != (0, 1, 4326) or entries.get(1025) != (0, 1, 2):
            raise ValueError("要求WGS84、PixelIsPoint格式DEM")
        self.sx, self.sy = sx, sy
        self.rows, self.cols = self.elevations.shape
        self.origin = origin
        phi = math.radians(origin.lat)
        a, e2 = 6378137.0, 6.6943799901413165e-3
        w = math.sqrt(1 - e2 * math.sin(phi) ** 2)
        self.scale_x = a / w * math.cos(phi)
        self.scale_y = a * (1 - e2) / w ** 3

    def xy(self, node: Node) -> tuple[float, float]:
        return (self.scale_x * math.radians(node.lon - self.origin.lon),
                self.scale_y * math.radians(node.lat - self.origin.lat))

    def grid(self, node: Node) -> tuple[float, float]:
        return ((node.lon - self.lon0) / self.sx + 0.5,
                (self.lat0 - node.lat) / self.sy + 0.5)

    def cells(self, a: Node, b: Node) -> set[tuple[int, int]]:
        return supercover(*self.grid(a), *self.grid(b), self.rows, self.cols)

    def maximum(self, cells: set[tuple[int, int]]) -> float:
        values = np.array([self.elevations[r, c] for r, c in cells])
        if not values.size or np.any(~np.isfinite(values)) or np.any(values == -32767):
            raise ValueError("航段经过缺失高程，禁止默认为零")
        return float(values.max())

    def leg(self, start: Node, end: Node, clearance: float = 50.0, service_height: float = 30.0) -> Leg:
        """任意不同任务节点之间的有向航段，保持问题一的坐标与DEM口径。"""
        if start.id == end.id:
            raise ValueError("航段起终点必须不同")
        cruise = self.maximum(self.cells(start, end)) + clearance
        ax, ay = self.xy(start)
        bx, by = self.xy(end)
        za = start.elevation + (0 if start.id == self.origin.id else service_height)
        zb = end.elevation + (0 if end.id == self.origin.id else service_height)
        if cruise < max(za, zb):
            raise ValueError(f"{start.id}->{end.id}节点作业海拔超过规定巡航海拔")
        return Leg(start.id, end.id, math.hypot(ax - bx, ay - by), cruise, cruise - za, cruise - zb)

    def route(self, service: Node, clearance: float, service_height: float) -> Route:
        cells = self.cells(self.origin, service)
        if cells != self.cells(service, self.origin):
            raise AssertionError("正反向相交像元不一致")
        zmax = self.maximum(cells)
        cruise = zmax + clearance
        ox, oy = self.xy(self.origin)
        x, y = self.xy(service)
        distance = math.hypot(x - ox, y - oy)
        h0, hi = self.origin.elevation, service.elevation + service_height
        if cruise < max(h0, hi):
            raise ValueError(f"{service.id}表列作业海拔高于规定巡航海拔，需明确模型口径")
        outbound = Leg(self.origin.id, service.id, distance, cruise, cruise - h0, cruise - hi)
        inbound = Leg(service.id, self.origin.id, distance, cruise, cruise - hi, cruise - h0)
        return Route(service.id, zmax, len(cells), outbound, inbound)
