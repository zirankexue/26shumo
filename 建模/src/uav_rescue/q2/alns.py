"""Adaptive route neighborhoods; CP-SAT owns final feasibility and scheduling."""
from __future__ import annotations
import math
import time
from .candidates import add_variants
from .routes import objective, make_assignment

DESTROY = ('random', 'related', 'tardiness', 'tail')
REPAIR = ('greedy', 'regret2')
ORDERS = {
    'tardiness': ['tardiness', 'makespan', 'energy', 'sorties'],
    'makespan': ['makespan', 'tardiness', 'energy', 'sorties'],
    'energy': ['energy', 'tardiness', 'makespan', 'sorties'],
    'sorties': ['sorties', 'tardiness', 'makespan', 'energy'],
}
LABELS = {'tardiness': '及时性优先', 'makespan': '完工时间优先', 'energy': '能耗优先', 'sorties': '架次优先'}


def key(schedule, pool, data, order):
    values = objective(schedule, pool, data)
    return tuple(values[m] for m in order)


def scales(schedule, pool, data):
    values = objective(schedule, pool, data)
    floors = {'tardiness': 3600000, 'makespan': 3600000, 'energy': 1000000000, 'sorties': 1}
    return {m: max(values[m], floors[m]) for m in values}


def accept(old, new, order, scale, temperature, rng):
    """Compare quantized lexicographic objectives; never mix objective weights."""
    for metric in order:
        delta = new[metric] - old[metric]
        if delta < 0:
            return True, 1.0
        if delta > 0:
            probability = math.exp(-min(745, delta / scale[metric] / temperature))
            return rng.random() < probability, probability
    return True, 1.0


def update_weight(weights, name, reward, reaction=.2, floor=.1):
    weights[name] = max(floor, (1-reaction)*weights[name] + reaction*reward)


def destroy(schedule, pool, factory, name, count, rng):
    count = min(count, len(schedule))
    rows = sorted(schedule, key=lambda a: a['candidate'])
    if name == 'random':
        return rng.sample(rows, count)
    if name == 'related':
        services = pool[rng.choice(rows)['candidate']].visits
        def distance(a):
            return min(0 if x == y else factory.legs[x, y].distance
                       for x in services for y in pool[a['candidate']].visits)
        return sorted(rows, key=lambda a: (distance(a), a['candidate']))[:count]
    if name == 'tardiness':
        def urgency(a):
            late = sum(factory.data.timing[b].priority * max(0, c-factory.data.timing[b].expected*1000)
                       for b, c in a['deliveries'].items())
            slack = min((factory.data.timing[b].hard or factory.data.timing[b].expected)*1000-c
                        for b, c in a['deliveries'].items())
            return -late, slack, a['candidate']
        return sorted(rows, key=urgency)[:count]
    return sorted(rows, key=lambda a: (-a['return'], a['candidate']))[:count]


def route_proxy(p, factory, start, order):
    a = make_assignment(p, start, {}, factory.data)
    values = objective([a], {p.id: p}, factory.data)
    values['makespan'] = p.duration
    return tuple(values[m] for m in order)


def repair_candidates(pool, factory, removed, method, order, rng, deadline):
    """Build insertion proposals; these proxy costs are not reported objectives."""
    unlocked = sorted({b for a in removed for b in pool[a['candidate']].boxes})
    start = min(a['start'] for a in removed)
    bundles = []
    pending = set(unlocked)
    selected = [pool[a['candidate']] for a in removed]
    for p in selected:
        add_variants(pool, factory, p.boxes, rng, p.visits)
        for b in rng.sample(list(p.boxes), min(3, len(p.boxes))):
            add_variants(pool, factory, [x for x in p.boxes if x != b], rng)
    pairs = [(a, b) for j, a in enumerate(selected) for b in selected[j+1:]]
    rng.shuffle(pairs)
    for a, b in pairs:
        if time.perf_counter() >= deadline:
            break
        add_variants(pool, factory, a.boxes+b.boxes, rng)
        x, y = rng.choice(a.boxes), rng.choice(b.boxes)
        for src, dst, box in [(a, b, x), (b, a, y)]:
            add_variants(pool, factory, tuple(dst.boxes)+(box,), rng)
            add_variants(pool, factory, [z for z in src.boxes if z != box], rng)
        add_variants(pool, factory, tuple(z for z in a.boxes if z != x)+(y,), rng)
        add_variants(pool, factory, tuple(z for z in b.boxes if z != y)+(x,), rng)
    while pending and time.perf_counter() < deadline:
        choices = []
        for box in sorted(pending):
            options = []
            for index, old in [(-1, None), *enumerate(bundles)]:
                variants = {}
                ids = (box,) if old is None else old.boxes+(box,)
                if old and factory.boxes[box].service not in old.visits:
                    service = factory.boxes[box].service
                    orders = [old.visits[:j]+(service,)+old.visits[j:] for j in range(len(old.visits)+1)]
                    for visits in orders:
                        for g in sorted(factory.data.base.drones):
                            p = factory.make(g, ids, visits)
                            if p:
                                variants[p.id] = p
                else:
                    add_variants(variants, factory, ids, rng, old.visits if old else None)
                pool.update(variants)
                base = route_proxy(old, factory, start, order) if old else (0,)*4
                for p in variants.values():
                    cost = tuple(x-y for x, y in zip(route_proxy(p, factory, start, order), base))
                    options.append((cost, p.id, index, p))
            if options:
                options.sort(key=lambda x: (x[0], x[1], x[2]))
                best = options[0]
                second = options[1] if len(options)>1 else None
                regret = tuple(b-a for a, b in zip(best[0], second[0])) if second else (float('inf'),)*4
                choices.append((box, best, regret))
            if time.perf_counter() >= deadline:
                break
        if not choices:
            break
        chosen = min(choices, key=lambda x: (x[1][0], x[0])) if method == 'greedy' else min(
            choices, key=lambda x: (tuple(-v for v in x[2]), x[1][0], x[0]))
        box, (_, _, index, candidate), _ = chosen
        if index == -1:
            bundles.append(candidate)
        else:
            bundles[index] = candidate
        pending.remove(box)
    return {p.id for p in bundles}, set(unlocked)
