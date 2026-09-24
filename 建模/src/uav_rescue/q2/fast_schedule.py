"""Fast, exact stop ordering within a heuristic fixed-route list schedule.

Routes are dispatched in the supplied order.  For each type, the next route takes
the earliest available aircraft and full battery; these resources are released
at return and return + charge respectively.  At each stop, a linear assignment
minimizes weighted tardiness for its actual arrival time, with forbidden hard
deadline positions.  Identical per-box handover durations make this assignment
exact for a fixed route start; it does not make the route schedule globally exact.

An evaluation costs O(R log(max(U, B)) + sum_stop n_stop**3).  No DEM, energy, or
whole-solution independent validation is repeated here: callers must supply
physically feasible Candidate objects and independently validate final results.
Both caches are bounded and cache values never escape as mutable dictionaries.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import heapq
import math
import random

import numpy as np
from scipy.optimize import linear_sum_assignment

from .routes import objective, ticks
from .weighted import score


class Scheduler:
    def __init__(self, data, spec, cache_size=20000, candidate_cache_size=4000):
        self.data, self.spec = data, spec
        self.cache_size = max(0, int(cache_size))
        self.candidate_cache_size = max(0, int(candidate_cache_size))
        self._orders = OrderedDict()
        self._candidate_data = OrderedDict()
        self.evaluations = 0

    @staticmethod
    def _remember(cache, key, value, limit):
        if not limit:
            return
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _compile(self, p):
        if p.id in self._candidate_data:
            self._candidate_data.move_to_end(p.id)
            return self._candidate_data[p.id]
        per_box = ticks(self.data.base.drones[p.drone].per_box_handover)
        stops = []
        for _, _, base_end, ids in p.stops:
            ids = tuple(ids)
            times = [self.data.timing[b] for b in ids]
            # Hard bounds are rounded inward, never relaxed to the next ms.
            hard = np.array([math.floor(t.hard * 1000 + 1e-9)
                             if t.hard is not None else np.inf for t in times])
            expected = np.array([ticks(t.expected) for t in times], dtype=np.int64)
            weights = np.array([t.priority for t in times], dtype=np.int64)
            offsets = base_end + np.arange(1, len(ids) + 1, dtype=np.int64) * per_box
            stops.append((ids, offsets, hard, expected, weights))
        result = tuple(stops)
        self._remember(self._candidate_data, p.id, result, self.candidate_cache_size)
        return result

    def _deliveries(self, p, start):
        key = p.id, start
        if key in self._orders:
            self._orders.move_to_end(key)
            return self._orders[key]
        deliveries = []
        for ids, offsets, hard, expected, weights in self._compile(p):
            completions = start + offsets
            costs = weights[:, None] * np.maximum(0, completions[None, :] - expected[:, None])
            costs = costs.astype(float)
            costs[completions[None, :] > hard[:, None]] = np.inf
            try:
                rows, columns = linear_sum_assignment(costs)
            except ValueError:
                self._remember(self._orders, key, None, self.cache_size)
                return None
            if not np.isfinite(costs[rows, columns]).all():
                self._remember(self._orders, key, None, self.cache_size)
                return None
            deliveries.extend((ids[r], int(completions[c])) for r, c in zip(rows, columns))
        result = tuple(deliveries)
        self._remember(self._orders, key, result, self.cache_size)
        return result

    def evaluate(self, candidates, priority=None):
        """Return (schedule, native-unit objective vector, weighted score) or None.

        ``priority=None`` preserves the input Candidate order.  Otherwise priority
        must contain each selected Candidate.id exactly once.  Subsets of boxes
        are allowed for small tests and partial repairs; duplicates are forbidden.
        """
        self.evaluations += 1
        candidates = list(candidates)
        pool = {p.id: p for p in candidates}
        if len(pool) != len(candidates):
            return None
        box_ids = [b for p in candidates for b in p.boxes]
        if len(box_ids) != len(set(box_ids)):
            return None
        if priority is None:
            priority = [p.id for p in candidates]
        else:
            priority = list(priority)
            if len(priority) != len(pool) or set(priority) != set(pool):
                raise ValueError('priority must contain each selected candidate ID exactly once')
        units = {g: [(0, i) for i in range(len(us))] for g, us in self.data.units.items()}
        batteries = {g: [(0, i) for i in range(self.data.battery_count[g])]
                     for g in self.data.units}
        schedule = []
        for pid in priority:
            p = pool[pid]
            if not units.get(p.drone) or not batteries.get(p.drone):
                return None
            ready_u, uid = heapq.heappop(units[p.drone])
            ready_b, bid = heapq.heappop(batteries[p.drone])
            start = max(ready_u, ready_b)
            deliveries = self._deliveries(p, start)
            if deliveries is None:
                return None
            returned = start + p.duration
            schedule.append({'candidate': pid, 'start': start, 'return': returned,
                             'deliveries': dict(deliveries)})
            heapq.heappush(units[p.drone], (returned, uid))
            heapq.heappush(batteries[p.drone], (returned + p.charge, bid))
        vector = objective(schedule, pool, self.data)
        return schedule, vector, score(vector, self.spec)

    def clear_cache(self):
        self._orders.clear()
        self._candidate_data.clear()


def fast_evaluate(candidates, data, spec, priority=None):
    return Scheduler(data, spec).evaluate(candidates, priority)


def polish(candidates, starting_schedule, data, spec, iterations=2000, seed=0,
           scheduler=None):
    """Improve dispatch order, preserving the best complete feasible schedule.

    Swap, relocate, and short shuffle affect only same-type dispatch orders;
    different types do not compete for aircraft or batteries.  Equal-score moves
    permit traversal of plateaus.  The supplied starting schedule is retained as
    a fallback and is assumed already independently validated by the caller.
    Returns the same tuple as ``fast_evaluate``; never reads checkpoints.
    """
    candidates = list(candidates)
    pool = {p.id: p for p in candidates}
    scheduler = scheduler or Scheduler(data, spec)
    rng = random.Random(seed)
    best = None
    if starting_schedule is not None:
        if len(starting_schedule) != len(pool) or {a['candidate'] for a in starting_schedule} != set(pool):
            raise ValueError('Starting schedule does not match the candidate set')
        vector = objective(starting_schedule, pool, data)
        best = copy.deepcopy(starting_schedule), vector, score(vector, spec)
        priority = [a['candidate'] for a in sorted(starting_schedule,
                                                  key=lambda a: (a['start'], a['candidate']))]
    else:
        priority = [p.id for p in candidates]
    current = scheduler.evaluate(candidates, priority)
    if current is None:
        return best
    if best is None or current[2] < best[2] - 1e-12:
        best = copy.deepcopy(current)
    slots = {g: [i for i, pid in enumerate(priority) if pool[pid].drone == g]
             for g in data.units}
    mutable = [s for s in slots.values() if len(s) > 1]
    if not mutable:
        return best
    for _ in range(max(0, iterations)):
        positions = rng.choice(mutable)
        values = [priority[i] for i in positions]
        i, j = rng.sample(range(len(values)), 2)
        move = rng.randrange(3)
        if move == 0:
            values[i], values[j] = values[j], values[i]
        elif move == 1:
            values.insert(j, values.pop(i))
        else:
            lo, hi = sorted((i, j))
            chunk = values[lo:hi + 1]
            rng.shuffle(chunk)
            values[lo:hi + 1] = chunk
        proposal = priority.copy()
        for index, pid in zip(positions, values):
            proposal[index] = pid
        evaluated = scheduler.evaluate(candidates, proposal)
        if evaluated is None:
            continue
        if evaluated[2] <= current[2] + 1e-12:
            priority, current = proposal, evaluated
        if evaluated[2] < best[2] - 1e-12:
            best = copy.deepcopy(evaluated)
    return best
