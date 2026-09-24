"""Enumerate the complete nondominated (sorties, energy, work-time) Q1 frontier.

Keeps the existing single-objective/lexicographic baseline unchanged. Uses the
previously verified route geometry after checking every original input hash.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import solve_q1_batching as physical

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'results' / 'question1_multiobjective'
ENERGY_TOL = 1e-9
TIME_TOL = 1e-6


@dataclass(frozen=True)
class Label:
    n: int
    energy: float
    time: float
    # Each key identifies one feasible batch in the rebuilt per-service pool.
    picks: tuple[tuple[str, int], ...] = ()
    energy_key: int = 0
    time_key: int = 0


def weakly_dominates(a, b):
    return a.n <= b.n and a.energy_key <= b.energy_key and a.time_key <= b.time_key


def nondominated(labels):
    """Retain one representative per vector of summed quantized batch costs."""
    front = []
    for candidate in sorted(labels, key=lambda v: (v.n, v.energy_key, v.time_key, v.picks)):
        if any(weakly_dominates(old, candidate) for old in front):
            continue
        front = [old for old in front if not weakly_dominates(candidate, old)]
        front.append(candidate)
    return front


def enumerate_batches(sid, service_boxes, drones, route, attrs):
    demand = tuple(sum(b['type'] == typ for b in service_boxes) for typ in physical.TYPE_ORDER)
    candidates = []
    for model, drone in drones.items():
        for combo in itertools.product(*(range(n + 1) for n in demand)):
            count = sum(combo)
            if count == 0:
                continue
            mass = sum(attrs[typ][0] * n for typ, n in zip(physical.TYPE_ORDER, combo))
            litres = sum(attrs[typ][1] * n for typ, n in zip(physical.TYPE_ORDER, combo))
            if mass > drone['payload_kg'] or litres > drone['volume_litre']:
                continue
            cost = physical.energy(drone, route, mass)
            budget = drone['energy_kwh'] * (1 - drone['reserve_percent'] / 100)
            if cost['energy_kwh'] > budget + physical.EPS_ENERGY:
                continue
            candidates.append(dict(service_id=sid, model=model, counts=list(combo), box_count=count,
                                   mass_kg=mass, volume_m3=litres / 1000, **cost,
                                   **physical.times(drone, route, count),
                                   return_soc_percent=100*(1-cost['energy_kwh']/drone['energy_kwh']),
                                   payload_limit_kg=drone['payload_kg'], volume_limit_m3=drone['volume_m3'],
                                   energy_budget_kwh=budget, reserve_percent=drone['reserve_percent']))
    candidates.sort(key=lambda p:(p['counts'],p['model']))
    return demand, candidates


def local_frontier(sid, demand, candidates):
    zero = (0, 0, 0, 0)
    states = sorted(itertools.product(*(range(n + 1) for n in demand)), key=lambda s: (sum(s), s))
    dp = {zero: [Label(0, 0.0, 0.0)]}
    stats = dict(state_count=len(states), feasible_candidate_count=len(candidates),
                 generated_labels=0, max_state_frontier_size=1)
    for state in states[1:]:
        generated = []
        for index, batch in enumerate(candidates):
            remaining = tuple(a - b for a, b in zip(state, batch['counts']))
            if min(remaining) < 0:
                continue
            for previous in dp.get(remaining, ()):
                generated.append(Label(previous.n + 1,
                                       previous.energy + batch['energy_kwh'],
                                       previous.time + batch['work_time_s'],
                                       previous.picks + ((sid, index),),
                                       previous.energy_key+physical.batch_cost(batch)[1],
                                       previous.time_key+physical.batch_cost(batch)[2]))
        dp[state] = nondominated(generated)
        stats['generated_labels'] += len(generated)
        stats['max_state_frontier_size'] = max(stats['max_state_frontier_size'], len(dp[state]))
    if not dp[demand]:
        raise ValueError(f'No feasible Q1 solution for {sid}')
    return dp[demand], stats


def materialize(label, pools, boxes):
    selected = [dict(pools[sid][index]) for sid, index in label.picks]
    selected.sort(key=lambda p: (p['service_id'], p['model'], p['counts']))
    available = {(sid, typ): sorted(b['id'] for b in boxes if b['service_id'] == sid and b['type'] == typ)
                 for sid in sorted({b['service_id'] for b in boxes}) for typ in physical.TYPE_ORDER}
    for batch in selected:
        batch['box_ids'] = []
        for typ, count in zip(physical.TYPE_ORDER, batch['counts']):
            key = (batch['service_id'], typ)
            batch['box_ids'].extend(available[key][:count])
            available[key] = available[key][count:]
    assert not any(available.values())
    return selected


def summarize(batches):
    return dict(flight_count=len(batches), energy_kwh=math.fsum(b['energy_kwh'] for b in batches),
                work_time_s=math.fsum(b['work_time_s'] for b in batches),
                flight_time_s=math.fsum(b['flight_time_s'] for b in batches),
                box_count=sum(len(b['box_ids']) for b in batches),
                mass_kg=math.fsum(b['mass_kg'] for b in batches),
                volume_m3=math.fsum(b['volume_m3'] for b in batches),
                model_counts=dict(Counter(b['model'] for b in batches)),
                minimum_soc_percent=min(b['return_soc_percent'] for b in batches))


def verify_plan(plan, boxes):
    by_id = {b['id']: b for b in boxes}
    delivered = [bid for b in plan['batches'] for bid in b['box_ids']]
    assert Counter(delivered) == Counter(by_id.keys())
    for batch in plan['batches']:
        cargo = [by_id[bid] for bid in batch['box_ids']]
        assert all(b['service_id'] == batch['service_id'] for b in cargo)
        assert sum(b['mass_kg'] for b in cargo) == batch['mass_kg']
        assert sum(b['volume_litre'] for b in cargo) == round(batch['volume_m3'] * 1000)
        assert batch['mass_kg'] <= batch['payload_limit_kg']
        assert batch['volume_m3'] <= batch['volume_limit_m3'] + 1e-12
        assert batch['energy_kwh'] <= batch['energy_budget_kwh'] + physical.EPS_ENERGY
        assert batch['return_soc_percent'] + 1e-8 >= batch['reserve_percent']


def main():
    baseline_path = ROOT / 'results' / 'question1_batching' / 'solution.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    for item in baseline['sources'].values():
        current = hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest()
        if current != item['sha256']:
            raise ValueError(f"Original input changed; regenerate baseline geometry: {item['path']}")
    geometry_audit = json.loads((baseline_path.parent / 'geometry_audit.json').read_text(encoding='utf-8'))
    assert geometry_audit['summary']['all_cell_sets_equal']
    assert geometry_audit['sources']['solution_sha256'] == hashlib.sha256(baseline_path.read_bytes()).hexdigest(), 'Geometry audit is stale; rerun verify_q1_local_plane.py --geometry-only.'
    assert baseline['dem_metadata']['distance_crs'] == physical.COORDINATE_MODEL
    assert baseline['solver_sha256'] == hashlib.sha256(Path(physical.__file__).read_bytes()).hexdigest(), 'Physical solver changed; regenerate baseline.'
    _, nodes, drones, boxes, attrs = physical.read_inputs(ROOT)
    pools, locals_, stats = {}, {}, {}
    for sid, route in baseline['routes'].items():
        demand, pools[sid] = enumerate_batches(sid, [b for b in boxes if b['service_id'] == sid], drones, route, attrs)
        locals_[sid], stats[sid] = local_frontier(sid, demand, pools[sid])
    global_front = [Label(0, 0.0, 0.0)]
    merge_sizes = []
    for sid, local in locals_.items():
        combined = [Label(a.n+b.n, a.energy+b.energy, a.time+b.time, a.picks+b.picks,
                          a.energy_key+b.energy_key,a.time_key+b.time_key)
                    for a in global_front for b in local]
        global_front = nondominated(combined)
        merge_sizes.append(dict(service_id=sid, candidates=len(combined), retained=len(global_front)))
    global_front.sort(key=lambda p: (p.n, p.energy, p.time))
    plans = []
    for index, label in enumerate(global_front, 1):
        same_baseline = (label.n == baseline['totals']['flight_count'] and
                         abs(label.energy-baseline['totals']['energy_kwh']) < ENERGY_TOL and
                         abs(label.time-baseline['totals']['work_time_s']) < TIME_TOL)
        # Preserve the concrete original box assignment for an equivalent baseline point.
        batches = [dict(b) for b in baseline['batches']] if same_baseline else materialize(label, pools, boxes)
        plan_id = f'PF{index:03d}'
        for number, batch in enumerate(batches, 1):
            batch['batch_id'] = f'{plan_id}-{number:03d}'
        plan = dict(plan_id=plan_id, **summarize(batches), batches=batches,
                    matches_previous_baseline=same_baseline)
        verify_plan(plan, boxes)
        plans.append(plan)
    rank = lambda p, order: tuple(p[key] for key in order)
    endpoints = {}
    priorities = {
        'N_E_T': ('flight_count', 'energy_kwh', 'work_time_s'),
        'N_T_E': ('flight_count', 'work_time_s', 'energy_kwh'),
        'E_N_T': ('energy_kwh', 'flight_count', 'work_time_s'),
        'T_N_E': ('work_time_s', 'flight_count', 'energy_kwh'),
        'E_T_N': ('energy_kwh', 'work_time_s', 'flight_count'),
        'T_E_N': ('work_time_s', 'energy_kwh', 'flight_count'),
    }
    for name, order in priorities.items():
        endpoints[name] = min(plans, key=lambda p: rank(p, order))['plan_id']
    recommended = next(p for p in plans if p['plan_id'] == endpoints['N_E_T'])
    efficient = next(p for p in plans if p['plan_id'] == endpoints['E_N_T'])
    ideals = {key: min(p[key] for p in plans) for key in ('flight_count', 'energy_kwh', 'work_time_s')}
    for plan in plans:
        plan['normalized_objectives'] = {key: plan[key]/ideals[key] for key in ideals}
    representatives = []
    for plan in plans:
        if plan['plan_id'] == recommended['plan_id']:
            label, priority = '推荐方案：架次与时间优先', '先最少架次，再最低能耗，再最短累计作业时间'
        elif plan['plan_id'] == efficient['plan_id']:
            label, priority = '备选方案：能耗优先', '先最低总能耗，再最少架次，再最短累计作业时间'
        else:
            label, priority = '其他非支配方案', '按任务偏好选择'
        representatives.append(dict(plan, label=label, priority=priority))
    tradeoff = dict(
        from_plan=recommended['plan_id'], to_plan=efficient['plan_id'],
        extra_flights=efficient['flight_count']-recommended['flight_count'],
        energy_saved_kwh=recommended['energy_kwh']-efficient['energy_kwh'],
        energy_saved_percent=100*(recommended['energy_kwh']-efficient['energy_kwh'])/recommended['energy_kwh'],
        extra_work_time_s=efficient['work_time_s']-recommended['work_time_s'],
        extra_work_time_percent=100*(efficient['work_time_s']-recommended['work_time_s'])/recommended['work_time_s'])
    weighted_difference = {key: efficient[key]/ideals[key]-recommended[key]/ideals[key] for key in ideals}
    positive_equal_share = (weighted_difference['flight_count']+weighted_difference['work_time_s'])/2
    energy_advantage = -weighted_difference['energy_kwh']
    threshold = positive_equal_share/(positive_equal_share+energy_advantage) if energy_advantage > 0 else None
    changed_services = []
    for sid in pools:
        batches_a = [b for b in recommended['batches'] if b['service_id'] == sid]
        batches_b = [b for b in efficient['batches'] if b['service_id'] == sid]
        signature = lambda bs: sorted((b['model'], tuple(b['counts'])) for b in bs)
        a, b = summarize(batches_a), summarize(batches_b)
        # Ignore irrelevant equal-cost alternative box compositions elsewhere.
        if a['flight_count'] != b['flight_count'] or abs(a['energy_kwh']-b['energy_kwh']) > ENERGY_TOL or abs(a['work_time_s']-b['work_time_s']) > TIME_TOL:
            changed_services.append(dict(service_id=sid, from_summary=a, to_summary=b,
                                         from_batches=batches_a, to_batches=batches_b))
        elif signature(batches_a) != signature(batches_b):
            # Use the unchanged baseline batching where the local cost vector is identical.
            # Full frontier vectors are unaffected; stabilize representative comparisons.
            replacement = [dict(batch) for batch in batches_a]
            for plan in (efficient,):
                plan['batches'] = [batch for batch in plan['batches'] if batch['service_id'] != sid] + replacement
    # Refresh representatives after stable same-cost local replacements.
    for rep in representatives:
        source = next(plan for plan in plans if plan['plan_id'] == rep['plan_id'])
        source['batches'].sort(key=lambda b: (b['service_id'], b['model'], b['counts']))
        for number, batch in enumerate(source['batches'], 1):
            batch['batch_id'] = f"{source['plan_id']}-{number:03d}"
        source.update(summarize(source['batches']))
        rep.update({key: value for key, value in source.items() if key != 'batches'})
        rep['batches'] = source['batches']
        verify_plan(source, boxes)
    local_output = {}
    for sid, labels in locals_.items():
        local_output[sid] = [dict(flight_count=x.n, energy_kwh=x.energy, work_time_s=x.time,
                                  candidate_indices=[idx for _, idx in x.picks]) for x in labels]
    result = dict(created_utc=datetime.now(timezone.utc).isoformat(),
        scope='Q1 multiobjective single-service batching; not Q2 multi-stop physical-fleet scheduling',
        assumptions=baseline['assumptions'], source_sha256=baseline['sources'],
        baseline_solution_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        type_order=list(physical.TYPE_ORDER), boxes=boxes, drones=drones, nodes=nodes,
        baseline=baseline['totals'], frontier=plans, representatives=representatives,
        local_frontiers=local_output, endpoints=endpoints, ideal_objectives=ideals,
        tradeoff=tradeoff, changed_services=changed_services,
        weighted_sum=dict(normalization='Divide each objective by its coordinate-wise ideal minimum.',
                          difference_energy_plan_minus_recommended=weighted_difference,
                          energy_weight_threshold_when_other_weights_equal=threshold),
        algorithm=dict(name='Exact per-state nondominated-label dynamic programming and global frontier merging',
                       cost_tolerances=dict(energy_kwh=ENERGY_TOL,time_s=TIME_TOL),
                       comparison='Exact componentwise dominance on sums of per-batch integer costs; raw totals retained for reporting and independent high-precision verification.',
                       integer_cost_scales=dict(energy=physical.ENERGY_COST_SCALE,time=physical.TIME_COST_SCALE),
                       state_count=sum(s['state_count'] for s in stats.values()),
                       feasible_candidate_count=sum(len(v) for v in pools.values()),
                       local_statistics=stats,global_merge_sizes=merge_sizes),
        checks=dict(original_input_hashes_unchanged=True,all_frontier_plans_feasible=True,
                    all_frontier_plans_cover_each_box_once=True,baseline_preserved=True))
    OUTPUT.mkdir(parents=True,exist_ok=True)
    (OUTPUT/'multiobjective.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUTPUT/'local_candidates.json').write_text(json.dumps(pools,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(frontier=[{k:p[k] for k in ('plan_id','flight_count','energy_kwh','work_time_s','model_counts')} for p in plans],
                         tradeoff=tradeoff, endpoints=endpoints,weight_threshold=threshold),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
