from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import math

from ..common.data import CATEGORIES, Inputs
from ..common.physics import trip_energy, trip_times
from ..common.terrain import Route

ENERGY_SCALE = 10 ** 12  # pico-kWh; independent integer addition avoids path-order rounding.
TIME_SCALE = 10 ** 6
INDEX = {"sorties": 0, "energy": 1, "time": 2}


@dataclass(frozen=True)
class Batch:
    service: str
    drone: str
    counts: tuple[int, ...]
    mass: float
    volume: float
    flight: float
    operation: float
    energy: float
    soc: float

    @property
    def cost(self) -> tuple[int, int, int]:
        return 1, round(self.energy * ENERGY_SCALE), round(self.operation * TIME_SCALE)


@dataclass
class Solution:
    service: str
    order: tuple[str, ...]
    batches: list[Batch]
    feasible: bool
    states: int

    @property
    def objective(self) -> tuple[int, float, float]:
        return len(self.batches), math.fsum(b.energy for b in self.batches), math.fsum(b.operation for b in self.batches)


def enumerate_batches(inputs: Inputs, service: str, route: Route, reserve: float,
                      gravity: float, energy_tolerance: float) -> list[Batch]:
    demand = inputs.counts(service)
    attrs = inputs.attributes(service)
    result = []
    for counts in product(*(range(n + 1) for n in demand)):
        if not any(counts):
            continue
        mass = math.fsum(n * a[0] for n, a in zip(counts, attrs))
        volume = math.fsum(n * a[1] for n, a in zip(counts, attrs))
        for d in inputs.drones.values():
            if mass > d.payload + 1e-10 or volume > d.volume + 1e-12:
                continue
            energy = trip_energy(d, route, mass, gravity)
            if energy > (1 - reserve) * d.battery + energy_tolerance:
                continue
            flight, operation = trip_times(d, route, sum(counts))
            result.append(Batch(service, d.id, counts, mass, volume, flight, operation,
                                energy, 1 - energy / d.battery))
    return sorted(result, key=lambda b: (b.counts, b.drone))


def solve(service: str, demand: tuple[int, ...], batches: list[Batch],
          order: tuple[str, ...]) -> Solution:
    if len(order) != 3 or set(order) != set(INDEX):
        raise ValueError("目标顺序必须恰好包含sorties,energy,time")
    indices = tuple(INDEX[x] for x in order)

    def key(cost):
        return tuple(cost[i] for i in indices)

    # A composition's lexicographically worse machine is never useful for this order.
    best_batches = {}
    for batch in batches:
        old = best_batches.get(batch.counts)
        if old is None or (key(batch.cost), batch.drone) < (key(old.cost), old.drone):
            best_batches[batch.counts] = batch
    options = sorted(best_batches.values(), key=lambda b: (b.counts, b.drone))
    zero = (0,) * len(demand)
    costs = {zero: (0, 0, 0)}
    prev = {}
    for state in product(*(range(n + 1) for n in demand)):
        if state == zero:
            continue
        for b in options:
            rest = tuple(n - k for n, k in zip(state, b.counts))
            if min(rest) < 0 or rest not in costs:
                continue
            cost = tuple(x + y for x, y in zip(costs[rest], b.cost))
            if state not in costs or key(cost) < key(costs[state]):
                costs[state] = cost
                prev[state] = (rest, b)
    if demand not in costs:
        return Solution(service, order, [], False, math.prod(n + 1 for n in demand))
    selected = []
    state = demand
    while state != zero:
        state, b = prev[state]
        selected.append(b)
    selected.sort(key=lambda b: (b.drone, b.counts))
    return Solution(service, order, selected, True, math.prod(n + 1 for n in demand))


def assign_boxes(inputs: Inputs, solutions: list[Solution], prefix: str = "Q1") -> list[dict]:
    rows = []
    for solution in solutions:
        if not solution.feasible:
            continue
        pools = {c: sorted(b.id for b in inputs.boxes if b.service == solution.service and b.category == c)
                 for c in CATEGORIES}
        offsets = dict.fromkeys(CATEGORIES, 0)
        for i, b in enumerate(solution.batches, 1):
            ids = []
            for category, n in zip(CATEGORIES, b.counts):
                start = offsets[category]
                ids.extend(pools[category][start:start + n])
                offsets[category] += n
            rows.append({"sortie": f"{prefix}-{solution.service}-{i:02}", "service": solution.service,
                         "drone": b.drone, "box_ids": ids, "box_count": len(ids), "counts": list(b.counts),
                         "mass_kg": b.mass, "volume_m3": b.volume, "flight_s": b.flight,
                         "operation_s": b.operation, "energy_kwh": b.energy, "return_soc": b.soc})
    return rows
