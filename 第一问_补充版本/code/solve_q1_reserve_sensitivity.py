"""Q1 reserve sensitivity: continuous payloads, exact batch-change intervals.

All aircraft share a scenario reserve percentage; all other physical rules stay
fixed. The selected policy remains lexicographic (sorties, energy, work time).
"""
from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import solve_q1_batching as physical
import solve_q1_multiobjective as multi

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'results' / 'question1_reserve_sensitivity'
RHO_TOL = 1e-10  # percentage points, not a fraction
SCENARIO_PERCENTAGES = (0, 5, 10, 15, 20, 22.5, 23, 25, 27.5, 30, 32.5, 35, 36, 40, 45, 50, 60)
PAYLOAD_PERCENTAGES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60)


def load_data():
    baseline_path = ROOT / 'results' / 'question1_batching' / 'solution.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    for source in baseline['sources'].values():
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256'], 'Original input changed; regenerate baseline.'
    audit = json.loads((baseline_path.parent / 'geometry_audit.json').read_text(encoding='utf-8'))
    assert audit['summary']['all_cell_sets_equal']
    assert audit['sources']['solution_sha256'] == hashlib.sha256(baseline_path.read_bytes()).hexdigest(), 'Geometry audit is stale; rerun verify_q1_local_plane.py --geometry-only.'
    assert baseline['dem_metadata']['distance_crs'] == physical.COORDINATE_MODEL
    assert baseline['solver_sha256'] == hashlib.sha256(Path(physical.__file__).read_bytes()).hexdigest(), 'Physical solver changed; regenerate baseline.'
    _, nodes, drones, boxes, attrs = physical.read_inputs(ROOT)
    return baseline_path, baseline, nodes, drones, boxes, attrs


class Solver:
    def __init__(self, baseline, drones, boxes, attrs):
        self.routes, self.drones, self.boxes = baseline['routes'], drones, boxes
        self.pools, self.demands, self.states, self.cache = {}, {}, {}, {}
        self.single_box_limits = []
        for sid, route in self.routes.items():
            demand = tuple(sum(b['service_id'] == sid and b['type'] == typ for b in boxes) for typ in physical.TYPE_ORDER)
            self.demands[sid] = demand
            self.states[sid] = sorted(itertools.product(*(range(n+1) for n in demand)), key=lambda s:(sum(s),s))
            pool = []
            for model, drone in drones.items():
                for counts in itertools.product(*(range(n+1) for n in demand)):
                    count = sum(counts)
                    if not count:
                        continue
                    mass = sum(attrs[t][0]*c for t,c in zip(physical.TYPE_ORDER,counts))
                    litres = sum(attrs[t][1]*c for t,c in zip(physical.TYPE_ORDER,counts))
                    if mass > drone['payload_kg'] or litres > drone['volume_litre']:
                        continue
                    cost = physical.energy(drone, route, mass)
                    critical = 100*(1-cost['energy_kwh']/drone['energy_kwh'])
                    pool.append(dict(service_id=sid, model=model, counts=list(counts), box_count=count,
                                     mass_kg=mass, volume_m3=litres/1000, **cost,
                                     **physical.times(drone,route,count), critical_reserve_percent=critical,
                                     return_soc_percent=critical, payload_limit_kg=drone['payload_kg'],
                                     volume_limit_m3=drone['volume_m3']))
            pool.sort(key=lambda p:(p['counts'],p['model']))
            self.pools[sid] = pool
            for k,typ in enumerate(physical.TYPE_ORDER):
                if not demand[k]:
                    continue
                one = tuple(int(j == k) for j in range(4))
                options = [b for b in pool if tuple(b['counts']) == one]
                if not options:
                    raise ValueError(f'{sid}/{typ}: single box exceeds every model capacity.')
                best = max(b['critical_reserve_percent'] for b in options)
                self.single_box_limits.append(dict(service_id=sid, type=typ, mass_kg=attrs[typ][0],
                    volume_m3=attrs[typ][1]/1000, maximum_reserve_percent=best,
                    best_models=[b['model'] for b in options if abs(b['critical_reserve_percent']-best)<RHO_TOL],
                    model_limits=[dict(model=b['model'],critical_reserve_percent=b['critical_reserve_percent'],
                                       energy_kwh=b['energy_kwh']) for b in options]))
        self.ceiling = min(x['maximum_reserve_percent'] for x in self.single_box_limits)

    def blocked(self, reserve):
        return [x for x in self.single_box_limits if reserve > x['maximum_reserve_percent']+RHO_TOL]

    def local(self, sid, reserve):
        pool = self.pools[sid]
        feasible = tuple(k for k,p in enumerate(pool) if p['critical_reserve_percent']+RHO_TOL >= reserve)
        cache_key = (sid, feasible)
        if cache_key in self.cache:
            return self.cache[cache_key]
        zero = (0,0,0,0)
        dp, parent = {zero:(0,0,0)}, {}
        for state in self.states[sid][1:]:
            best, pred = None, None
            for k in feasible:
                p = pool[k]
                remaining = tuple(s-c for s,c in zip(state,p['counts']))
                if min(remaining) < 0 or remaining not in dp:
                    continue
                old = dp[remaining]
                value = tuple(a+b for a,b in zip(old,physical.batch_cost(p)))
                if physical.better(value,best):
                    best,pred = value,(remaining,k)
            if best is not None:
                dp[state],parent[state] = best,pred
        final = self.demands[sid]
        if final not in dp:
            result = None
        else:
            selected, state = [], final
            while any(state):
                previous,k = parent[state]
                selected.append(k)
                state = previous
            selected.sort(key=lambda k:(pool[k]['model'],pool[k]['counts']))
            result = dict(objective=[len(selected),math.fsum(pool[k]['energy_kwh'] for k in selected),
                                     math.fsum(pool[k]['work_time_s'] for k in selected)],
                          integer_objective=list(dp[final]),candidate_indices=selected)
        self.cache[cache_key] = result
        return result

    def signature(self, reserve):
        if self.blocked(reserve):
            return None
        entries = []
        for sid in self.routes:
            local = self.local(sid,reserve)
            assert local is not None
            entries.append((sid,tuple(local['candidate_indices'])))
        return tuple(entries)

    def build_plan(self, signature, reserve, plan_id):
        batches = []
        for sid,indices in signature:
            available = {typ:sorted(b['id'] for b in self.boxes if b['service_id']==sid and b['type']==typ)
                         for typ in physical.TYPE_ORDER}
            for k in indices:
                p = dict(self.pools[sid][k])
                p['reserve_percent'] = reserve
                p['energy_budget_kwh'] = self.drones[p['model']]['energy_kwh']*(1-reserve/100)
                p['box_ids'] = []
                for typ,count in zip(physical.TYPE_ORDER,p['counts']):
                    p['box_ids'].extend(available[typ][:count])
                    available[typ] = available[typ][count:]
                batches.append(p)
            assert not any(available.values())
        for j,b in enumerate(batches,1):
            b['batch_id'] = f'{plan_id}-{j:03d}'
        result = dict(plan_id=plan_id,reserve_percent=reserve,feasible=True,
                      **multi.summarize(batches),batches=batches)
        multi.verify_plan(result,self.boxes)
        return result


def payload(drone,route,reserve):
    modified = dict(drone,reserve_percent=reserve)
    capacity = physical.safe_payload(modified,route)
    if capacity['safe_payload_kg'] is not None:
        budget=drone['energy_kwh']*(1-reserve/100)
        assert physical.energy(drone,route,capacity['safe_payload_kg'])['energy_kwh'] <= budget+1e-10
    return capacity


def payload_data(drones,routes):
    table, curves = [], []
    for sid,route in routes.items():
        for model,drone in drones.items():
            full=100*(1-physical.energy(drone,route,drone['payload_kg'])['energy_kwh']/drone['energy_kwh'])
            empty=100*(1-physical.energy(drone,route,0)['energy_kwh']/drone['energy_kwh'])
            samples=[dict(reserve_percent=r,**payload(drone,route,r)) for r in PAYLOAD_PERCENTAGES]
            table.append(dict(service_id=sid,model=model,rated_payload_kg=drone['payload_kg'],
                              full_load_maximum_reserve_percent=full,
                              empty_roundtrip_maximum_reserve_percent=empty,samples=samples))
            for rho in range(61):
                curves.append(dict(service_id=sid,model=model,reserve_percent=rho,
                                   **payload(drone,route,rho)))
    return table,curves


def compare_signatures(a,b,solver):
    changes=[]
    for (sid,ia),(sid2,ib) in zip(a,b):
        assert sid==sid2
        if ia==ib:
            continue
        simplify=lambda ids:[dict(model=solver.pools[sid][k]['model'],counts=solver.pools[sid][k]['counts'],
                                  mass_kg=solver.pools[sid][k]['mass_kg'],volume_m3=solver.pools[sid][k]['volume_m3'],
                                  energy_kwh=solver.pools[sid][k]['energy_kwh'],
                                  critical_reserve_percent=solver.pools[sid][k]['critical_reserve_percent']) for k in ids]
        old,new=simplify(ia),simplify(ib)
        changes.append(dict(service_id=sid,old_batches=old,new_batches=new,
                            flight_count_change=len(ib)-len(ia),
                            energy_change_kwh=math.fsum(x['energy_kwh'] for x in new)-math.fsum(x['energy_kwh'] for x in old)))
    return changes


def main():
    baseline_path,baseline,nodes,drones,boxes,attrs=load_data()
    solver=Solver(baseline,drones,boxes,attrs)
    candidates=[p for pool in solver.pools.values() for p in pool]
    # Between consecutive critical values the feasible batch set is identical.
    edges=sorted(set([0.0,solver.ceiling]+[p['critical_reserve_percent'] for p in candidates
                                         if 0 < p['critical_reserve_percent'] < solver.ceiling]))
    segments=[]
    for left,right in zip(edges,edges[1:]):
        sig=solver.signature((left+right)/2)
        assert sig is not None
        if segments and sig==segments[-1]['signature']:
            segments[-1]['upper_percent']=right
        else:
            segments.append(dict(lower_percent=left,upper_percent=right,signature=sig))
    assert solver.signature(0)==segments[0]['signature']
    interval_plans=[]
    for index,segment in enumerate(segments,1):
        label=f'R{index:03d}'
        # Upper end is included; lower end belongs to the preceding segment.
        plan=solver.build_plan(segment['signature'],segment['upper_percent'],label)
        interval_plans.append(dict(plan,lower_percent=segment['lower_percent'],upper_percent=segment['upper_percent'],
                                   lower_inclusive=(index==1),upper_inclusive=True))
    transitions=[]
    for index in range(1,len(segments)):
        old,new=interval_plans[index-1],interval_plans[index]
        transitions.append(dict(threshold_percent=new['lower_percent'],
            equality_plan_id=old['plan_id'],above_plan_id=new['plan_id'],
            old_flight_count=old['flight_count'],new_flight_count=new['flight_count'],
            old_energy_kwh=old['energy_kwh'],new_energy_kwh=new['energy_kwh'],
            old_work_time_s=old['work_time_s'],new_work_time_s=new['work_time_s'],
            changes=compare_signatures(segments[index-1]['signature'],segments[index]['signature'],solver)))
    scenarios=[]
    values=sorted(set(SCENARIO_PERCENTAGES+(solver.ceiling,)))
    for index,rho in enumerate(values,1):
        plan_id=f'SC{index:03d}'
        blockers=solver.blocked(rho)
        if blockers:
            scenarios.append(dict(plan_id=plan_id,reserve_percent=rho,feasible=False,
                                  flight_count=None,energy_kwh=None,work_time_s=None,model_counts={},
                                  minimum_soc_percent=None,blocked_demands=blockers,batches=[]))
        else:
            sig=solver.signature(rho)
            plan=solver.build_plan(sig,rho,plan_id)
            interval=next(p['plan_id'] for p in interval_plans if rho<=p['upper_percent']+RHO_TOL)
            scenarios.append(dict(plan,interval_plan_id=interval,blocked_demands=[]))
    baseline_case=next(p for p in scenarios if p['reserve_percent']==20)
    assert baseline_case['flight_count']==baseline['totals']['flight_count']
    assert abs(baseline_case['energy_kwh']-baseline['totals']['energy_kwh'])<1e-9
    assert abs(baseline_case['work_time_s']-baseline['totals']['work_time_s'])<1e-6
    capacities,curves=payload_data(drones,solver.routes)
    ceiling_witnesses=[x for x in solver.single_box_limits if abs(x['maximum_reserve_percent']-solver.ceiling)<RHO_TOL]
    assert all(a['flight_count']<=b['flight_count'] for a,b in zip(interval_plans,interval_plans[1:]))
    # Explicit boundary checks: the equality point is feasible, just above is not.
    upper_sig=solver.signature(solver.ceiling)
    assert upper_sig is not None
    assert solver.signature(solver.ceiling+1e-6) is None
    result=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        scope='Q1 reserve sensitivity; all models share rho; fixed lexicographic (N,E,T) policy.',
        assumptions=baseline['assumptions'],sources=baseline['sources'],
        baseline_solution_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        nodes=nodes,drones=drones,boxes=boxes,type_order=list(physical.TYPE_ORDER),baseline=baseline['totals'],
        baseline_reserve_percent=20,
        conventional_scenario_percentages=list(range(5,46,5)),
        interval_convention='[0, first upper]; subsequently (lower, upper]. Equality is feasible. Above ceiling infeasible.',
        global_feasibility_limit=dict(reserve_percent=solver.ceiling,witnesses=ceiling_witnesses,
                                     equality_feasible=True,just_above_feasible=False),
        baseline_plan_valid_up_to_percent=min(p['return_soc_percent'] for p in baseline['batches']),
        payload_sample_percentages=list(PAYLOAD_PERCENTAGES),payloads=capacities,payload_curves=curves,
        scenarios=scenarios,interval_plans=interval_plans,transitions=transitions,
        single_box_limits=solver.single_box_limits,
        algorithm=dict(physical_candidate_count=len(candidates),
                       twenty_percent_candidate_count=sum(p['critical_reserve_percent']+RHO_TOL>=20 for p in candidates),
                       distinct_critical_values_within_feasible_range=len(edges)-2,
                       elementary_intervals_evaluated=len(edges)-1,
                       selected_plan_intervals=len(interval_plans),
                       local_dp_cache_entries=len(solver.cache),reserve_tolerance_percentage_points=RHO_TOL),
        checks=dict(original_inputs_unchanged=True,baseline_reproduced=True,
                    all_output_plans_cover_each_box_once=True,all_output_plans_satisfy_constraints=True,
                    minimum_flight_count_nondecreasing=True))
    OUTPUT.mkdir(parents=True,exist_ok=True)
    (OUTPUT/'sensitivity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUTPUT/'all_capacity_feasible_batches.json').write_text(json.dumps(solver.pools,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['algorithm'],ensure_ascii=False))
    print('GLOBAL_CEILING',solver.ceiling)
    for s in scenarios:
        print('SCENARIO',s['reserve_percent'],s['feasible'],s['flight_count'],s['energy_kwh'],s['work_time_s'],s['model_counts'])
    for p in interval_plans:
        print('INTERVAL',p['plan_id'],p['lower_percent'],p['upper_percent'],p['flight_count'],p['energy_kwh'],p['work_time_s'],p['model_counts'])


if __name__=='__main__':
    main()
