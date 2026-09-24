from __future__ import annotations
from dataclasses import asdict
import json
import random
import time
from pathlib import Path
from .candidates import initial_pool, greedy_schedule, prune_pool
from .solver import solve_pool, assign_resources
from .routes import objective
from .validate import validate_schedule
from .alns import DESTROY, REPAIR, ORDERS, LABELS, key, scales, accept, update_weight, destroy, repair_candidates


def optimize(factory, opt, output, cache):
    started = time.perf_counter()
    data = factory.data
    orders = {order[0]: order for order in opt.get('orders', ORDERS.values())}
    trace, history, incumbents, provenance = [], [], {}, {}
    pool = initial_pool(factory)
    print(f'初始候选 {len(pool)}，多点 {sum(len(p.visits)>1 for p in pool.values())}', flush=True)
    solutions = []
    for seed in range(24):
        schedule = greedy_schedule(factory, pool, seed)
        if schedule:
            validate_schedule(assign_resources(schedule, pool, data), pool, factory)
            solutions.append(schedule)
    if opt.get('resume_checkpoint'):
        resumed = json.loads(Path(opt['resume_checkpoint']).read_text(encoding='utf-8'))
        if resumed.get('input_sha256') != opt.get('input_sha256'):
            raise ValueError('检查点输入哈希不一致，不能继续旧方案')
        if resumed.get('physics') != factory.physics:
            raise ValueError('检查点物理参数不一致，不能继续旧方案')
        for raw in resumed['candidates']:
            p = factory.make(raw['drone'], raw['boxes'], raw['visits'])
            if p is None or p.id != raw['id']:
                raise ValueError('检查点候选与当前物理口径不一致')
            pool[p.id] = p
        for schedule in resumed.get('schedules', {'tardiness': resumed['schedule']}).values():
            validate_schedule(schedule, pool, factory)
            solutions.append(schedule)
        (output/'logs/resumed_from.json').write_text(json.dumps(resumed, ensure_ascii=False, indent=2), encoding='utf-8')

    def checked(schedule):
        return validate_schedule(assign_resources(schedule, pool, data), pool, factory)

    def consider(schedule, source):
        for name, order in orders.items():
            if name not in incumbents or key(schedule, pool, data, order) < key(incumbents[name], pool, data, order):
                incumbents[name] = schedule
                provenance[name] = source

    for schedule in solutions:
        consider(schedule, 'initial_construction')

    def protect(*extra):
        return {a['candidate'] for s in [*incumbents.values(), *extra] for a in s}

    def checkpoint(label):
        if not incumbents:
            return
        schedules = {name: assign_resources(s, pool, data) for name, s in incumbents.items()}
        results = {name: validate_schedule(s, pool, factory) for name, s in schedules.items()}
        payload = {'label': label, 'schema_version': 2, 'input_sha256': opt.get('input_sha256'), 'physics':factory.physics,
                   'schedules': schedules, 'schedule': schedules['tardiness'],
                   'candidates': [asdict(pool[pid]) for pid in sorted(protect())],
                   'results': results, 'result': results['tardiness'], 'trace': trace,
                   'alns_trace': history, 'provenance': provenance}
        path = output/'logs/checkpoint.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        tmp.replace(path)
        m = results['tardiness']['summary']
        print(f"{label}: 主方案 W={m['weighted_tardiness_s']:.3f}, Cmax={m['makespan_s']:.3f}s, E={m['energy_kwh']:.6f}, N={m['sorties']}", flush=True)

    pool = prune_pool(pool, factory, opt['candidate_limit'], protect(), random.Random(0))
    remaining = max(.05, opt['initial_seconds']-(time.perf_counter()-started))
    best = incumbents.get('tardiness')
    if best:
        s, stages = solve_pool({a['candidate']: pool[a['candidate']] for a in best}, data,
                              orders['tardiness'], min(20, remaining/3), 0, best, opt['workers'])
        trace.append({'phase': 'initial_fixed_routes', 'scheme': 'tardiness', 'stages': stages})
        if s:
            checked(s); consider(s, 'initial_fixed_routes')
    remaining = max(.05, opt['initial_seconds']-(time.perf_counter()-started))
    s, stages = solve_pool(pool, data, orders['tardiness'], remaining, 0, incumbents.get('tardiness'), opt['workers'])
    trace.append({'phase': 'initial', 'scheme': 'tardiness', 'stages': stages})
    if s:
        checked(s); consider(s, 'initial')
    if not incumbents:
        (output/'logs/no_feasible_solution.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')
        raise RuntimeError('预算内搜索未找到完整可行调度；不能据此断言原问题不可行')
    initial = checked(incumbents['tardiness'])
    checkpoint('初始可行化完成')
    scale = scales(incumbents['tardiness'], pool, data)
    for name, order in orders.items():
        budget = opt['search_seconds'] if name == 'tardiness' else opt['comparison_seconds']
        begin = time.perf_counter(); end = begin+budget
        current = incumbents[name]
        weights_d = dict.fromkeys(DESTROY, 1.0); weights_r = dict.fromkeys(REPAIR, 1.0)
        rngs = {seed: random.Random(seed) for seed in opt['seeds']}
        iteration = 0
        while time.perf_counter() < end-.05:
            seed = opt['seeds'][iteration % len(opt['seeds'])]; rng = rngs[seed]
            iteration += 1
            dn = rng.choices(DESTROY, weights=[weights_d[x] for x in DESTROY])[0]
            rn = rng.choices(REPAIR, weights=[weights_r[x] for x in REPAIR])[0]
            removed = destroy(current, pool, factory, dn, rng.randint(opt['destroy_min'], opt['destroy_max']), rng)
            changed = {a['candidate'] for a in removed}
            proposed, unlocked = repair_candidates(pool, factory, removed, rn, order, rng,
                                                   min(end, time.perf_counter()+4))
            pool = prune_pool(pool, factory, opt['candidate_limit'], protect(current)|proposed, rng)
            fixed = {a['candidate'] for a in current}-changed
            active = {pid: p for pid, p in pool.items() if pid in fixed or set(p.boxes)<=unlocked}
            fraction = min(1., (time.perf_counter()-begin)/max(budget, .001))
            temp = opt['temperature_start']*(opt['temperature_end']/opt['temperature_start'])**fraction
            before = objective(current, pool, data)
            target_before = key(incumbents[name], pool, data, order)
            seconds = min(opt['repair_seconds'], max(.01, end-time.perf_counter()))
            candidate, stages = solve_pool(active, data, order, seconds, seed, current, opt['workers'],
                                           improve_only=False, change_candidates=changed)
            trace.append({'phase': 'alns', 'scheme': name, 'round': iteration, 'seed': seed,
                          'restricted_neighborhood': True, 'stages': stages})
            accepted, probability, reward, after = False, 0., 0, None
            if candidate:
                checked(candidate)
                after = objective(candidate, pool, data)
                accepted, probability = accept(before, after, order, scale, temp, rng)
                new_best = tuple(after[m] for m in order) < target_before
                improved = tuple(after[m] for m in order) < tuple(before[m] for m in order)
                consider(candidate, f'{name}:alns:{iteration}')
                reward = 5 if new_best else 3 if improved else 1 if accepted else 0
                if accepted:
                    current = candidate
            update_weight(weights_d, dn, reward, opt['reaction'], opt['weight_floor'])
            update_weight(weights_r, rn, reward, opt['reaction'], opt['weight_floor'])
            history.append({'scheme': name, 'iteration': iteration, 'seed': seed, 'destroy': dn,
                            'repair': rn, 'removed': len(removed), 'temperature': temp,
                            'accepted': accepted, 'probability': probability, 'reward': reward,
                            'before': before, 'candidate': after, 'best': objective(incumbents[name], pool, data),
                            'elapsed_s': time.perf_counter()-started,
                            'destroy_weights': dict(weights_d), 'repair_weights': dict(weights_r)})
            checkpoint(f'{LABELS[name]} ALNS {iteration} ({dn}/{rn}, 接受={accepted})')
        checkpoint(f'{LABELS[name]}搜索结束')

    pool = prune_pool(pool, factory, opt['candidate_limit'], protect(), random.Random(0))
    frozen_ids = sorted(pool)
    for name, order in orders.items():
        s, stages = solve_pool(pool, data, order, opt['final_seconds'], 0, incumbents[name], opt['workers'])
        trace.append({'phase': 'final', 'scheme': name, 'stages': stages})
        if s:
            checked(s); consider(s, f'{name}:final')
        checkpoint(f'{LABELS[name]}公共池收尾')
    results = {name: checked(s) for name, s in incumbents.items()}
    schedules = {name: assign_resources(s, pool, data) for name, s in incumbents.items()}
    (cache/'candidates.json').write_text(json.dumps([asdict(p) for p in pool.values()], ensure_ascii=False, indent=2), encoding='utf-8')
    return {'schema_version': 2, 'main': results['tardiness'], 'comparison': results['makespan'],
            'initial': initial, 'schemes': results, 'schedules': schedules, 'orders': orders,
            'schedule': schedules['tardiness'], 'comparison_schedule': schedules['makespan'],
            'trace': trace, 'alns_trace': history, 'provenance': provenance,
            'pool_size': len(pool), 'final_pool_ids': frozen_ids,
            'annealing_scales_integer': scale, 'search_elapsed_s': time.perf_counter()-started}
