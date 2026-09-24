from __future__ import annotations
from collections import Counter
from itertools import permutations
import random
from .routes import earliest_order, make_assignment, objective
from ..q1.optimize import enumerate_batches, solve, assign_boxes


def variants(factory, ids, rng, old_order=None):
    services = sorted({factory.boxes[b].service for b in ids})
    if len(services) <= 3:
        return list(permutations(services))
    result = [tuple(services), tuple(reversed(services))]
    if old_order and set(old_order) == set(services): result.append(tuple(old_order))
    if old_order and set(old_order) == set(services):
        for _ in range(4):
            left, right = sorted(rng.sample(range(len(services)), 2))
            result.append(tuple(old_order[:left]) + tuple(reversed(old_order[left:right+1])) + tuple(old_order[right+1:]))
            moved = list(old_order)
            moved.insert(right, moved.pop(left))
            result.append(tuple(moved))
    for _ in range(4):
        rest = services.copy(); order=[]; prev="O01"
        while rest:
            ranked=sorted(rest,key=lambda s:factory.legs[prev,s].distance)
            nxt=ranked[0 if rng.random()<0.8 else rng.randrange(min(3,len(ranked)))]
            order.append(nxt);rest.remove(nxt);prev=nxt
        result.append(tuple(order))
    return list(dict.fromkeys(result))


def add_variants(pool, factory, ids, rng, old_order=None):
    for visits in variants(factory, ids, rng, old_order):
        for g in factory.data.base.drones:
            p=factory.make(g,ids,visits)
            if p:pool[p.id]=p


def initial_pool(factory):
    data=factory.data;pool={};rng=random.Random(0)
    for b in factory.boxes:add_variants(pool,factory,[b],rng)
    # Recreate Q1 seeds from raw inputs; no dependency on its generated files.
    for service,node in data.base.services.items():
        route=factory.terrain.route(node,factory.physics['clearance_m'],factory.physics['service_height_m'])
        batches=enumerate_batches(data.base,service,route,factory.physics['reserve'],factory.physics['gravity_m_s2'],factory.physics['energy_tolerance_kwh'])
        for order in [('sorties','energy','time'),('energy','sorties','time'),('sorties','time','energy')]:
            sol=solve(service,data.base.counts(service),batches,order)
            for row in assign_boxes(data.base,[sol]):add_variants(pool,factory,row['box_ids'],rng)
        local=earliest_order(data,[b for b in factory.boxes if factory.boxes[b].service==service])
        for ordering in [local,list(reversed(local))]+[rng.sample(local,len(local)) for _ in range(10)]:
            for start in range(len(local)):
                for length in range(2,min(10,len(local)-start)+1):
                    add_variants(pool,factory,ordering[start:start+length],rng)
        hard=[b for b in local if data.timing[b].hard is not None]
        add_variants(pool,factory,hard,rng)
    # Small multi-stop seeds, including urgent medical/water packages.
    for sa in data.base.services:
        nearest=sorted((s for s in data.base.services if s!=sa),key=lambda s:factory.legs[sa,s].distance)[:5]
        aa=[b for b in factory.boxes if factory.boxes[b].service==sa]
        for sb in nearest:
            bb=[b for b in factory.boxes if factory.boxes[b].service==sb]
            bundles=[([b for b in aa if data.timing[b].hard is not None]+[b for b in bb if data.timing[b].hard is not None]),aa+bb]
            bundles.extend([[a,b] for a in aa for b in bb if data.timing[a].hard is not None and data.timing[b].hard is not None])
            for ids in bundles:add_variants(pool,factory,ids,rng)
    return pool


def greedy_schedule(factory, pool, seed=0):
    """Construct a complete disjoint route selection with earliest resource starts."""
    data=factory.data;rng=random.Random(seed)
    remaining=set(factory.boxes);selected=[]
    machines={g:[0]*len(data.units[g]) for g in data.units}
    batteries={g:[0]*data.battery_count[g] for g in data.units}
    while remaining:
        anchor=min(remaining,key=lambda b:(data.timing[b].hard if data.timing[b].hard is not None else data.timing[b].expected+100000,-data.timing[b].priority,b))
        options=[]
        for p in pool.values():
            if anchor not in p.boxes or not set(p.boxes)<=remaining:continue
            start=max(min(machines[p.drone]),min(batteries[p.drone]))
            a=make_assignment(p,start,{},data)
            if any(data.timing[b].hard is not None and c>data.timing[b].hard*1000 for b,c in a['deliveries'].items()):continue
            lateness=sum(data.timing[b].priority*max(0,c-data.timing[b].expected*1000) for b,c in a['deliveries'].items())
            # Favor useful payload consolidation, but preserve hard-deadline feasibility.
            score=(lateness, (a['return']-start)/len(p.boxes)+start*0.4+p.energy*10000)
            if seed:score=(score[0],score[1]*(0.8+0.4*rng.random()))
            options.append((score,p.id,a))
        if not options:return None
        _,pid,a=min(options,key=lambda x:(x[0],x[1]));p=pool[pid]
        mi=min(range(len(machines[p.drone])),key=lambda i:(machines[p.drone][i],i))
        bi=min(range(len(batteries[p.drone])),key=lambda i:(batteries[p.drone][i],i))
        machines[p.drone][mi]=a['return'];batteries[p.drone][bi]=a['return']+p.charge
        selected.append(a);remaining.difference_update(p.boxes)
    return selected


def prune_pool(pool, factory, limit, protected, rng):
    mandatory={pid for pid,p in pool.items() if len(p.boxes)==1}|set(protected)
    if len(mandatory)>limit:raise ValueError('候选上限小于必须保留的单箱与当前方案数量')
    coverage=Counter(b for p in pool.values() for b in p.boxes)
    def merit(p):
        scarcity=min(coverage[b] for b in p.boxes)
        # Mixed route cost and rarity; a reproducible perturbation diversifies rounds.
        return (p.duration/len(p.boxes)+p.energy*10000)*(0.65+0.7*rng.random())*(1+0.0001*scarcity)
    others=sorted((p for p in pool.values() if p.id not in mandatory),key=lambda p:(merit(p),p.id))
    return {pid:pool[pid] for pid in sorted(mandatory)}|{p.id:p for p in others[:limit-len(mandatory)]}


def neighborhood(pool, factory, schedule, rng):
    selected=[pool[a['candidate']] for a in schedule]
    for p in selected:
        add_variants(pool,factory,p.boxes,rng,p.visits)
        # Splitting, reordering and single-box removal, with residuals retained.
        for b in p.boxes:
            add_variants(pool,factory,[x for x in p.boxes if x!=b],rng)
        for service in p.visits:
            ids=[b for b in p.boxes if factory.boxes[b].service==service]
            add_variants(pool,factory,ids,rng)
    pairs=[(a,b) for i,a in enumerate(selected) for b in selected[i+1:]]
    rng.shuffle(pairs)
    for a,b in pairs:
        add_variants(pool,factory,a.boxes+b.boxes,rng)
        # Both directions: destination gains one box; origin loses it.
        for src,dst in [(a,b),(b,a)]:
            for box in rng.sample(list(src.boxes),min(3,len(src.boxes))):
                add_variants(pool,factory,tuple(dst.boxes)+(box,),rng)
                add_variants(pool,factory,[x for x in src.boxes if x!=box],rng)
        for _ in range(min(3,len(a.boxes)*len(b.boxes))):
            x,y=rng.choice(a.boxes),rng.choice(b.boxes)
            add_variants(pool,factory,tuple(z for z in a.boxes if z!=x)+(y,),rng)
            add_variants(pool,factory,tuple(z for z in b.boxes if z!=y)+(x,),rng)
