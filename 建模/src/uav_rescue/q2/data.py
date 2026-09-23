from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from collections import Counter
from ..common.data import Inputs, _rows, load_inputs


@dataclass(frozen=True)
class BoxTiming:
    first: bool
    first_deadline: float | None
    expected: float
    priority: int
    hard: float | None


@dataclass
class SchedulingInputs:
    base: Inputs
    timing: dict[str, BoxTiming]
    units: dict[str, list[str]]
    battery_count: dict[str, int]
    full_charge_s: dict[str, float]
    template_headers: dict[str, list[str]]


def load_scheduling_inputs(root: Path, template: Path) -> SchedulingInputs:
    base = load_inputs(root, template)
    folder = root / "无人机应急物资运输基础数据"
    timing = {}
    for row in _rows(folder / "物资需求与配送时限.xlsx", "逐箱货箱清单")[1:]:
        if not row[0]: continue
        first = row[5] == "是"
        bounds = ([float(row[6])] if first else []) + ([float(row[7])] if row[2] == "医疗物资" else [])
        timing[row[0]] = BoxTiming(first, float(row[6]) if first else None, float(row[7]), int(row[8]), min(bounds) if bounds else None)
    units = {g: [] for g in base.drones}
    count, charge = {}, {}
    for row in _rows(folder / "运输无人机数据.xlsx"):
        if isinstance(row[0], str) and row[0].startswith("U"):
            if row[2] != "O01": raise ValueError("实体机初始位置应为O01")
            units[row[1]].append(row[0])
        if row[0] in units and isinstance(row[1], (int, float)):
            count[row[0]], charge[row[0]] = int(row[1]), float(row[2])
    if {g: len(v) for g, v in units.items()} != {"A": 4, "B": 2, "C": 2} or count != {"A": 6, "B": 4, "C": 4}:
        raise ValueError("无人机/电池库存不符")
    if len(timing) != 80 or Counter(t.hard for t in timing.values() if t.hard is not None) != {3600: 17, 7200: 7, 10800: 7}:
        raise ValueError("31个硬时限箱分类不符")
    headers = {name: list(_rows(template, name)[0][:n]) for name, n in [("Q2_运输架次", 8), ("Q2_逐箱交付", 4)]}
    return SchedulingInputs(base, timing, units, count, charge, headers)
