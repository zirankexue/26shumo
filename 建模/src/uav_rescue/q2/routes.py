from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
import math
from ..common.physics import leg_energy, leg_time
from ..common.charging import charging_time
from ..common.terrain import Terrain
from .data import SchedulingInputs


def ticks(seconds: float) -> int:
    return math.ceil(seconds * 1000 - 1e-9)


@dataclass(frozen=True)
class Candidate:
    id: str
    drone: str
    boxes: tuple[str, ...]
    visits: tuple[str, ...]
    # Per visit: service, arrival offset ms, end of base handover ms, boxes.
    stops: tuple[tuple[str, int, int, tuple[str, ...]], ...]
    duration: int
    charge: int
    energy: float
    mass: float
    volume: float
    flight_s: float
    rounding_s: float

    @property
    def energy_int(self): return round(self.energy * 1e9)


class RouteFactory:
    def __init__(self, data: SchedulingInputs, physics: dict):
        self.data, self.physics = data, physics
        self.boxes = {b.id: b for b in data.base.boxes}
        self.nodes = {"O01": data.base.origin, **data.base.services}
        self.terrain = Terrain(data.base.dem_path, data.base.origin)
        self.legs = {(a, b): self.terrain.leg(na, nb, physics["clearance_m"], physics["service_height_m"])
                     for a, na in self.nodes.items() for b, nb in self.nodes.items() if a != b}
        self.times = {(g, a, b): leg_time(d, leg) for g, d in data.base.drones.items() for (a,b),leg in self.legs.items()}
        self.memo = {}

    def make(self, drone: str, box_ids, visits=None) -> Candidate | None:
        ids = tuple(sorted(box_ids))
        if not ids or len(ids) != len(set(ids)): return None
        required = {self.boxes[b].service for b in ids}
        visits = tuple(visits) if visits is not None else tuple(sorted(required))
        if len(set(visits)) != len(visits) or set(visits) != required: return None
        key = drone, ids, visits
        if key in self.memo: return self.memo[key]
        d = self.data.base.drones[drone]
        mass = math.fsum(self.boxes[b].mass for b in ids)
        vol = math.fsum(self.boxes[b].volume for b in ids)
        if mass > d.payload + 1e-10 or vol > d.volume + 1e-12:
            self.memo[key] = None
            return None
        remaining, energy, flight, elapsed, stops = mass, 0.0, 0.0, ticks(d.preparation + len(ids)*d.loading), []
        prev = "O01"
        for end in (*visits, "O01"):
            energy += leg_energy(d, self.legs[prev, end], max(0.0, remaining), self.physics["gravity_m_s2"])
            seconds = self.times[drone, prev, end]
            flight += seconds
            elapsed += ticks(seconds)
            if end != "O01":
                local = tuple(b for b in ids if self.boxes[b].service == end)
                base_end = elapsed + ticks(d.handover)
                stops.append((end, elapsed, base_end, local))
                # Optimistic deadline screen: even first position misses => impossible.
                if any(self.data.timing[b].hard is not None and base_end + ticks(d.per_box_handover) > ticks(self.data.timing[b].hard) for b in local):
                    self.memo[key] = None
                    return None
                elapsed = base_end + len(local)*ticks(d.per_box_handover)
                remaining -= math.fsum(self.boxes[b].mass for b in local)
            prev = end
        if energy > (1-self.physics["reserve"])*d.battery + self.physics["energy_tolerance_kwh"]:
            self.memo[key] = None
            return None
        charge = ticks(charging_time(1-energy/d.battery, self.data.full_charge_s[drone]))
        exact_duration = d.preparation + len(ids)*d.loading + flight + len(visits)*d.handover + len(ids)*d.per_box_handover
        rid = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        result = Candidate(rid, drone, ids, visits, tuple(stops), elapsed, charge, energy, mass, vol, flight, elapsed/1000-exact_duration)
        self.memo[key] = result
        return result

    def leg_records(self):
        return [asdict(leg) for leg in self.legs.values()]


def earliest_order(data, ids):
    return sorted(ids, key=lambda b: (data.timing[b].hard if data.timing[b].hard is not None else float("inf"), data.timing[b].expected, -data.timing[b].priority, b))


def make_assignment(candidate, start, orders, data):
    d = data.base.drones[candidate.drone]
    deliveries = {}
    for service, _, base_end, ids in candidate.stops:
        for rank, b in enumerate(orders.get(service, earliest_order(data, ids)), 1):
            deliveries[b] = start + base_end + rank*ticks(d.per_box_handover)
    return {"candidate":candidate.id,"start":start,"return":start+candidate.duration,"deliveries":deliveries}


def objective(schedule, pool, data):
    return {"tardiness":sum(data.timing[b].priority*max(0, c-ticks(data.timing[b].expected)) for a in schedule for b,c in a["deliveries"].items()),
            "makespan":max((a["return"] for a in schedule),default=0),
            "energy":sum(pool[a["candidate"]].energy_int for a in schedule),"sorties":len(schedule)}
