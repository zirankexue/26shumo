from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import hashlib
import math

import openpyxl

CATEGORIES = ("医疗物资", "饮用水", "应急食品", "生活卫生用品")


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    lon: float
    lat: float
    elevation: float


@dataclass(frozen=True)
class Box:
    id: str
    service: str
    category: str
    mass: float
    volume: float


@dataclass(frozen=True)
class Drone:
    id: str
    empty_mass: float
    payload: float
    volume: float
    cruise_speed: float
    empty_range: float
    full_range: float
    battery: float
    reserve: float
    preparation: float
    loading: float
    handover: float
    per_box_handover: float
    climb_speed: float
    descent_speed: float
    climb_efficiency: float
    descent_efficiency: float


@dataclass
class Inputs:
    origin: Node
    services: dict[str, Node]
    drones: dict[str, Drone]
    boxes: list[Box]
    dem_path: Path
    source_paths: list[Path]
    template_headers: list[str]

    def counts(self, service: str) -> tuple[int, ...]:
        counter = Counter(b.category for b in self.boxes if b.service == service)
        return tuple(counter[c] for c in CATEGORIES)

    def attributes(self, service: str) -> tuple[tuple[float, float], ...]:
        values = {b.category: (b.mass, b.volume) for b in self.boxes if b.service == service}
        return tuple(values.get(c, (0.0, 0.0)) for c in CATEGORIES)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _rows(path: Path, sheet: str = "数据") -> list[tuple]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return list(wb[sheet].values)
    finally:
        wb.close()


def load_inputs(data_root: Path, template: Path) -> Inputs:
    base = data_root / "无人机应急物资运输基础数据"
    node_path = base / "调度中心与服务区.xlsx"
    cargo_path = base / "物资需求与配送时限.xlsx"
    drone_path = base / "运输无人机数据.xlsx"
    rows = _rows(node_path)
    origin = Node(*rows[2][:5])
    services = {r[0]: Node(*r[:5]) for r in rows[6:21]}
    cargo_rows = _rows(cargo_path, "逐箱货箱清单")[1:]
    boxes = [Box(*r[:5]) for r in cargo_rows if r[0] is not None]
    drones = {}
    for row in _rows(drone_path)[2:5]:
        vals = list(row[2:18])
        vals[7] /= 100.0
        drones[row[0]] = Drone(row[0], *vals)
    dem_files = list(data_root.rglob("*DEM.tif"))
    if len(dem_files) != 1:
        raise ValueError(f"需要唯一DEM.tif，实际{len(dem_files)}个")
    headers = list(_rows(template, "Q1_单点组批")[0][:9])
    sources = [node_path, cargo_path, drone_path, dem_files[0], template]
    result = Inputs(origin, services, drones, boxes, dem_files[0], sources, headers)
    validate_inputs(result, _rows(cargo_path)[1:])
    return result


def validate_inputs(data: Inputs, demand: list[tuple]) -> None:
    if data.origin.id != "O01" or set(data.services) != {f"S{i:03}" for i in range(1, 16)}:
        raise ValueError("节点编号或数量与标准场景不一致")
    if len(data.boxes) != 80 or len({b.id for b in data.boxes}) != 80:
        raise ValueError("货箱编号重复或箱数不是80")
    if set(data.drones) != {"A", "B", "C"}:
        raise ValueError("机型必须为A/B/C")
    if not math.isclose(sum(b.mass for b in data.boxes), 758, abs_tol=1e-8):
        raise ValueError("货箱质量汇总不是758kg")
    if not math.isclose(sum(b.volume for b in data.boxes), 2.011, abs_tol=1e-10):
        raise ValueError("货箱体积汇总不是2.011m³")
    groups = defaultdict(list)
    for box in data.boxes:
        if box.service not in data.services or box.category not in CATEGORIES:
            raise ValueError(f"非法货箱引用：{box.id}")
        if not (math.isfinite(box.mass) and box.mass > 0 and math.isfinite(box.volume) and box.volume > 0):
            raise ValueError(f"质量/体积非法：{box.id}")
        groups[(box.service, box.category)].append(box)
    for row in demand:
        key = row[:2]
        group = groups.pop(key, [])
        if len(group) != row[2] or any((b.mass, b.volume) != (row[4], row[5]) for b in group):
            raise ValueError(f"汇总与逐箱数据不一致：{key}")
    if groups:
        raise ValueError("逐箱清单存在汇总表以外的物资")
    for d in data.drones.values():
        if not (0 < d.full_range <= d.empty_range and 0 < d.climb_efficiency <= 1):
            raise ValueError(f"机型{d.id}航程或效率非法")
        if d.descent_efficiency != 0:
            raise ValueError("当前模型只支持题给零下降附加能耗")
