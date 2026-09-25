"""Q4 exact partitions of a frozen Q3 schedule. Python 3.10+, standard library.

Primary: keep every relay sortie indivisible (no added relay flights).
Sensitivity: duplicate a complete frozen relay sortie in each group needing it.
Neither mode changes a transport route, box, timestamp or communication segment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction
import hashlib
import heapq
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
KEYS = ('U_A', 'U_B', 'U_C', 'B_A', 'B_B', 'B_C', 'R', 'RE')
AIR = ('U_A', 'U_B', 'U_C', 'R')
POWER = ('B_A', 'B_B', 'B_C', 'RE')
LABELS = dict(zip(KEYS, ('A型运输无人机', 'B型运输无人机', 'C型运输无人机',
                       'A型共享电池', 'B型共享电池', 'C型共享电池', '中继无人机', '中继能源组件')))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def read_xlsx(path):
    """Read the actual source cells, preserving row/column addresses; no writers."""
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    rid = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    with zipfile.ZipFile(path) as z:
        ss = []
        if 'xl/sharedStrings.xml' in z.namelist():
            ss = [''.join(n.text or '' for n in si.iter(ns + 't'))
                  for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rel = {v.attrib['Id']: v.attrib['Target'] for v in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        result = {}
        for sh in ET.fromstring(z.read('xl/workbook.xml')).find(ns + 'sheets'):
            target = rel[sh.attrib[rid]]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = {}
            for row in ET.fromstring(z.read(target)).findall('.//' + ns + 'sheetData/' + ns + 'row'):
                vals = {}
                for c in row.findall(ns + 'c'):
                    v = c.find(ns + 'v')
                    value = v.text if v is not None else None
                    typ = c.attrib.get('t')
                    if typ == 's' and value is not None:
                        value = ss[int(value)]
                    elif typ == 'inlineStr':
                        value = ''.join(n.text or '' for n in c.iter(ns + 't'))
                    elif typ not in ('s', 'str', 'e') and value is not None:
                        value = float(value)
                    vals[''.join(c for c in c.attrib['r'] if c.isalpha())] = value
                rows[int(row.attrib['r'])] = vals
            result[sh.attrib['name']] = rows
    return result


def ms(value):
    exact = Decimal(str(value)) * 1000
    n = int(exact.to_integral_value())
    if abs(exact - n) > Decimal('0.00001'):
        raise ValueError(f'Non-millisecond resource timestamp: {value}')
    return n


def recharge_ms(energy, capacity, full):
    used = Decimal(str(energy)) / Decimal(str(capacity))
    charge = Decimal(str(full)) * (min(used, Decimal('.1')) * Decimal('3.5')
               + max(used - Decimal('.1'), Decimal(0)) * Decimal('0.65') / Decimal('.9'))
    return int((charge * 1000).to_integral_value(rounding=ROUND_CEILING))


def read_inputs():
    folder = ROOT / '数据/无人机应急物资运输基础数据'
    tr = read_xlsx(folder / '运输无人机数据.xlsx')['数据']
    rr = read_xlsx(folder / '中继无人机数据.xlsx')['数据']
    fleet = {r['A']: r['B'] for r in tr.values() if str(r.get('A', '')).startswith('U0')}
    inv = {f'U_{t}': list(fleet.values()).count(t) for t in 'ABC'}
    energy = {}
    for row in (20, 21, 22):
        r = tr[row]
        inv['B_' + r['A']] = int(r['B'])
        energy[r['A']] = {'full_s': r['C']}
    for row in (3, 4, 5):
        energy[tr[row]['A']]['capacity_kwh'] = tr[row]['I']
    inv['R'] = sum(str(r.get('A', '')).startswith('R0') for r in rr.values())
    inv['RE'] = int(rr[12]['B'])
    energy['R'] = {'full_s': rr[12]['C'], 'capacity_kwh': rr[3]['H']}
    nodes = read_xlsx(folder / '调度中心与服务区.xlsx')['数据']
    services = {r['A']: {'population': int(r['F']), 'lon': r['C'], 'lat': r['D']}
                for r in nodes.values() if str(r.get('A', '')).startswith('S0')}
    raw = read_xlsx(folder / '物资需求与配送时限.xlsx')['逐箱货箱清单']
    boxes = {r['A']: {'service': r['B'], 'mass_kg': r['D'], 'volume_m3': r['E']}
             for i, r in raw.items() if i > 1 and r.get('A')}
    return {'inventory': inv, 'energy': energy, 'services': services, 'boxes': boxes,
            'transport_fleet': fleet, 'relay_turnaround_s': rr[3]['L']}


class UnionFind:
    def __init__(self, nodes):
        self.parent = {x: x for x in nodes}

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def merge(self, values):
        values = list(values)
        for x in values[1:]:
            self.parent[self.find(x)] = self.find(values[0])

    def blocks(self):
        out = defaultdict(list)
        for x in sorted(self.parent):
            out[self.find(x)].append(x)
        return sorted(out.values())


def relay_edges(schedule):
    """Bind actual certified communication segments to actual timed relay sorties.

    Q3 legacy single-station rows inherit sortie.station; -2 is a sentinel,
    never a physical station. Never use potential covered_needed_services.
    """
    edges = {r['id']: set() for r in schedule['relay']}
    associations = []
    for t in schedule['transport']:
        for j, c in enumerate(t['communication']):
            if not c['relay']:
                continue
            station = c.get('station', t['station'])
            if station < 0:
                raise ValueError(f"Missing relay station: {t['id']} segment {j}")
            start, end = t['start_s'] + c['start'], t['start_s'] + c['end']
            matching = [r for r in schedule['relay'] if r['station'] == station
                        and r['service_start_s'] <= start + 1e-6
                        and r['service_end_s'] >= end - 1e-6]
            if len(matching) != 1:
                raise ValueError(f"Non-unique active relay for {t['id']} segment {j}")
            r = matching[0]
            edges[r['id']].add(t['id'])
            associations.append({'transport': t['id'], 'segment': j, 'relay': r['id'],
                                 'station': station, 'start_s': start, 'end_s': end})
    if any(not v for v in edges.values()):
        raise ValueError('Unused relay sortie requires explicit task ownership')
    return {k: sorted(v) for k, v in edges.items()}, associations


def partitions(n, k):
    """Restricted growth strings enumerate all unlabelled nonempty partitions."""
    def visit(labels, highest):
        if len(labels) == n:
            if highest == k - 1:
                yield tuple(tuple(i for i, x in enumerate(labels) if x == g) for g in range(k))
            return
        if highest + n - len(labels) < k - 1:
            return
        for x in range(min(highest + 1, k - 1) + 1):
            yield from visit(labels + [x], max(highest, x))
    yield from visit([0], 0)


def interval_coloring(intervals):
    """Greedy interval coloring attains the half-open maximum overlap exactly."""
    busy, free, assigned = [], [], []
    count = 0
    for x in sorted(intervals, key=lambda r: (r['start_ms'], r['end_ms'], r['task'])):
        if x['end_ms'] <= x['start_ms']:
            raise ValueError('Empty resource occupation')
        while busy and busy[0][0] <= x['start_ms']:
            _, slot = heapq.heappop(busy)
            heapq.heappush(free, slot)
        if free:
            slot = heapq.heappop(free)
        else:
            count += 1
            slot = count
        heapq.heappush(busy, (x['end_ms'], slot))
        assigned.append(dict(x, slot=slot))
    event = defaultdict(lambda: [[], []])
    for x in intervals:
        event[x['start_ms']][1].append(x['task'])
        event[x['end_ms']][0].append(x['task'])
    active, peak, witness = set(), 0, None
    for tick, (ends, starts) in sorted(event.items()):
        active.difference_update(ends)
        active.update(starts)
        if len(active) > peak:
            peak, witness = len(active), {'time_s': tick / 1000, 'tasks': sorted(active)}
    if peak != count:
        raise AssertionError('Coloring did not attain clique lower bound')
    return count, assigned, witness


def resource_intervals(schedule, source):
    out = defaultdict(list)
    for t in schedule['transport']:
        for kind, end, original in [('U_', t['resource_end_s'], t['drone']),
                                    ('B_', t['battery_ready_s'], t['battery'])]:
            out[kind + t['model']].append({'task': t['id'], 'start_ms': ms(t['start_s']),
                                          'end_ms': ms(end), 'original_id': original})
        pars = source['energy'][t['model']]
        ready = ms(t['return_s']) + recharge_ms(t['energy_kwh'], pars['capacity_kwh'], pars['full_s'])
        if ms(t['battery_ready_s']) < ready:
            raise ValueError('Transport battery recharge ends too early')
    for r in schedule['relay']:
        pars = source['energy']['R']
        ready = ms(r['return_s']) + recharge_ms(r['energy_kwh'], pars['capacity_kwh'], pars['full_s'])
        for key in ('component_ready_s', 'battery_ready_s', 'charge_end_s'):
            if key in r:
                ready = max(ready, ms(r[key]))
        if ms(r['resource_end_s']) < ms(r['return_s']) + ms(source['relay_turnaround_s']):
            raise ValueError('Relay turnaround missing')
        for key, end, original in [('R', ms(r['resource_end_s']), r['drone']),
                                   ('RE', ready, r['component'])]:
            out[key].append({'task': r['id'], 'start_ms': ms(r['start_s']), 'end_ms': end,
                             'original_id': original})
    return {key: out[key] for key in KEYS}


def group_data(services, schedule, source, edges, intervals):
    services = sorted(services)
    tr = [t for t in schedule['transport'] if t['order'][0] in services]
    assert all(set(t['order']).issubset(services) for t in tr)
    trids = {t['id'] for t in tr}
    re = [r for r in schedule['relay'] if trids.intersection(edges[r['id']])]
    tasks = trids | {r['id'] for r in re}
    counts, preserved, assignments, witnesses = {}, {}, {}, {}
    for key in KEYS:
        ints = [x for x in intervals[key] if x['task'] in tasks]
        counts[key], assignments[key], witnesses[key] = interval_coloring(ints)
        preserved[key] = len({x['original_id'] for x in ints})
    boxids = [b for t in tr for b in t['boxes']]
    return {'services': services, 'transport': sorted(trids), 'relay': sorted(r['id'] for r in re),
            'resource_minimum': counts, 'original_id_count': preserved,
            'assignments': assignments, 'peak_witnesses': witnesses,
            'box_count': len(boxids), 'population': sum(source['services'][s]['population'] for s in services),
            'mass_kg': sum(source['boxes'][b]['mass_kg'] for b in boxids),
            'transport_work_ms': sum(ms(t['return_s']) - ms(t['start_s']) for t in tr),
            'relay_work_ms': sum(ms(r['return_s']) - ms(r['start_s']) for r in re),
            'transport_energy_kwh': sum(t['energy_kwh'] for t in tr),
            'relay_energy_kwh': sum(r['energy_kwh'] for r in re),
            'joint_finish_s': max([t['return_s'] for t in tr] + [r['return_s'] for r in re])}


def summarize(groups, inventory, global_minimum, policy, identity='minimum'):
    field = 'resource_minimum' if identity == 'minimum' else 'original_id_count'
    total = {r: sum(g[field][r] for g in groups) for r in KEYS}
    gap = {r: max(total[r] - inventory[r], 0) for r in KEYS}
    redundant = {r: total[r] - global_minimum[r] for r in KEYS}
    workloads = [g['transport_work_ms'] for g in groups]
    variance_ratio = Fraction(len(groups) * sum(x*x for x in workloads), sum(workloads)**2) - 1
    score = (sum(gap[r] for r in AIR), sum(gap[r] for r in POWER),
             sum(total[r] for r in AIR), sum(total[r] for r in POWER), variance_ratio)
    relay_copies = Counter(r for g in groups for r in g['relay'])
    if policy == 'strict' and max(relay_copies.values(), default=0) > 1:
        raise AssertionError('Strict partition duplicated a relay sortie')
    return {'K': len(groups), 'policy': policy, 'identity': identity,
            'groups': [g['services'] for g in groups], 'total': total, 'gap': gap,
            'redundancy_vs_global_minimum': redundant,
            'unused_inventory': {r: max(inventory[r] - total[r], 0) for r in KEYS},
            'workload_cv': float(variance_ratio)**0.5,
            'workload_cv_squared_fraction': str(variance_ratio),
            'workload_ms': workloads, 'boxes_by_group': [g['box_count'] for g in groups],
            'relay_sorties_executed': sum(relay_copies.values()),
            'relay_copy_counts': dict(relay_copies),
            'total_energy_kwh': sum(g['transport_energy_kwh'] + g['relay_energy_kwh'] for g in groups),
            'joint_finish_s': max(g['joint_finish_s'] for g in groups),
            'ranking_first_four': list(score[:4])}, score


def csv_write(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(solution, validation, output):
    started = time.perf_counter()
    snapshot = output.parent / '数据'
    target = snapshot / 'q3_frozen_solution.json'
    if target.exists() and sha(target) != sha(solution):
        raise ValueError('Existing frozen solution differs; select a fresh output directory before writing any results')
    schedule = json.loads(solution.read_text(encoding='utf8'))
    proof = json.loads(validation.read_text(encoding='utf8'))
    if not proof['passed'] or proof['error_count'] != 0 or proof['solution_sha256'] != sha(solution):
        raise ValueError('Q3 validation does not certify the exact input bytes')
    source = read_inputs()
    hashes = {}
    for relative, expected in schedule['inputs'].items():
        p = ROOT / relative.replace('\\', '/')
        hashes[relative] = sha(p)
        if hashes[relative] != expected:
            raise ValueError(f'Q3 source changed: {relative}')
    allboxes = [b for t in schedule['transport'] for b in t['boxes']]
    if Counter(allboxes) != Counter({b: 1 for b in source['boxes']}):
        raise ValueError('Box inventory mismatch')
    nodes = sorted(source['services'])
    uf = UnionFind(nodes)
    for t in schedule['transport']:
        uf.merge(t['order'])
    transport_blocks = uf.blocks()
    edges, associations = relay_edges(schedule)
    for r in schedule['relay']:
        uf.merge(a for t in schedule['transport'] if t['id'] in edges[r['id']] for a in t['order'])
    strict_blocks = uf.blocks()
    intervals = resource_intervals(schedule, source)
    cache = {}

    def group(areas):
        key = tuple(sorted(areas))
        if key not in cache:
            cache[key] = group_data(key, schedule, source, edges, intervals)
        return cache[key]

    global_group = group(nodes)
    allrows, recommendations, enumeration = [], {}, []
    for policy, blocks in [('strict', strict_blocks), ('clone', transport_blocks)]:
        for k in (2, 3):
            candidates = list(partitions(len(blocks), k))
            enum = {'policy': policy, 'K': k, 'blocks': len(blocks), 'partitions': len(candidates)}
            for identity in ('minimum', 'preserve_ids'):
                evaluated = []
                for i, p in enumerate(candidates, 1):
                    gs = [group([s for j in inds for s in blocks[j]]) for inds in p]
                    row, score = summarize(gs, source['inventory'], global_group['resource_minimum'], policy, identity)
                    row['partition_id'] = f'{policy}-{k}-{i:04}-{identity}'
                    allrows.append(row)
                    evaluated.append((score, row, gs))
                evaluated.sort(key=lambda x: (x[0], x[1]['groups']))
                if not evaluated:
                    raise ValueError(f'No feasible {k}-group partition under {policy}')
                best, row, gs = evaluated[0]
                rec = dict(row, group_details=gs)
                # All non-dominated resource vector / workload candidates retained.
                frontier = []
                for _, candidate, _ in evaluated:
                    vec = tuple(candidate['total'][r] for r in KEYS) + (Fraction(candidate['workload_cv_squared_fraction']),)
                    if any(all(a <= b for a, b in zip(v, vec)) and any(a < b for a, b in zip(v, vec)) for v, _ in frontier):
                        continue
                    frontier = [(v, c) for v, c in frontier if not (all(a <= b for a, b in zip(vec, v)) and any(a < b for a, b in zip(vec, v)))]
                    frontier.append((vec, candidate))
                rec['pareto_partition_ids'] = [c['partition_id'] for _, c in frontier]
                rec['same_optimum_count'] = sum(score == best for score, _, _ in evaluated)
                rec['balance_first'] = min(evaluated, key=lambda x: (x[0][-1], x[0][:-1]))[1]
                recommendations[f'{policy}_{k}_{identity}'] = rec
                enum[f'{identity}_zero_gap'] = sum(not any(r['gap'].values()) for _, r, _ in evaluated)
                enum[f'{identity}_pareto_count'] = len(frontier)
            enumeration.append(enum)

    result = {'schema': 'q4-frozen-schedule-v1', 'source_solution': str(solution.resolve()),
              'source_solution_sha256': sha(solution), 'source_validation_sha256': sha(validation),
              'source_input_hashes': hashes, 'source_metrics': schedule['metrics'],
              'resource_keys': list(KEYS), 'resource_labels': LABELS, 'inventory': source['inventory'],
              'energy_parameters': source['energy'], 'transport_blocks': transport_blocks,
              'strict_blocks': strict_blocks, 'relay_transport_edges': edges,
              'communication_associations': associations, 'resource_intervals': intervals,
              'global_group': global_group, 'enumeration': enumeration, 'recommendations': recommendations,
              'ranking': ['min aircraft shortage', 'min battery/component shortage',
                          'min aircraft total', 'min battery/component total', 'min transport workload CV'],
              'primary_policy': 'strict', 'primary_identity': 'minimum',
              'scope': 'Exact optimum conditional on frozen Q3, stated relay policy and lexicographic ranking; not joint Q3/Q4 optimum.',
              'elapsed_s': time.perf_counter() - started}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'solution_q4.json', result)
    write_json(output / 'all_partitions.json', allrows)
    write_json(output / 'input_metadata.json', source)
    snapshot.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(solution, target)
    write_json(snapshot / 'input_manifest.json', {'q3': sha(solution), 'q3_validation': sha(validation), 'inputs': hashes})
    flat = []
    for row in allrows:
        d = {k: row[k] for k in ('partition_id', 'policy', 'identity', 'K', 'workload_cv', 'relay_sorties_executed', 'total_energy_kwh')}
        d['partition'] = ' / '.join(','.join(g) for g in row['groups'])
        d.update({f'total_{r}': row['total'][r] for r in KEYS})
        d.update({f'gap_{r}': row['gap'][r] for r in KEYS})
        flat.append(d)
    csv_write(output / 'all_partitions.csv', flat)
    template_rows, allocation = [], []
    for k in (2, 3):
        rec = recommendations[f'strict_{k}_minimum']
        used_physical = {key: set() for key in KEYS}
        pools = {f'U_{t}': sorted(u for u, model in source['transport_fleet'].items() if model == t) for t in 'ABC'}
        pools.update({f'B_{t}': [f'BAT-{t}-{i:02}' for i in range(1, source['inventory'][f'B_{t}'] + 1)] for t in 'ABC'})
        pools['R'] = [f'R{i:02}' for i in range(1, source['inventory']['R'] + 1)]
        pools['RE'] = [f'RE{i:02}' for i in range(1, source['inventory']['RE'] + 1)]
        for i, g in enumerate(rec['group_details'], 1):
            if g['resource_minimum'] != g['original_id_count']:
                raise ValueError('Primary requires recoloring; review identity-preservation policy before export')
            template_rows.append({'K（2或3）': k, '任务组编号': f'G{i}', '服务区列表': ','.join(g['services']),
                **{name: g['resource_minimum'][key] for name, key in zip(
                    ['A型运输无人机数', 'B型运输无人机数', 'C型运输无人机数', 'A型电池组数', 'B型电池组数', 'C型电池组数', '中继无人机数', '中继能源组件数'], KEYS)}})
            for resource, assignments in g['assignments'].items():
                physical_map = {}
                for original in sorted({x['original_id'] for x in assignments}):
                    free = [p for p in pools[resource] if p not in used_physical[resource]]
                    physical = original if original in free else (free[0] if free else f'ADD-{resource}-{len(used_physical[resource])-len(pools[resource])+1:02}')
                    used_physical[resource].add(physical)
                    physical_map[original] = physical
                for x in assignments:
                    physical = physical_map[x['original_id']]
                    allocation.append({'K': k, 'group': f'G{i}', 'resource_type': resource, 'local_resource': physical,
                                       'task': x['task'], 'start_s': x['start_ms']/1000, 'end_s': x['end_ms']/1000,
                                       'q3_original_id': x['original_id'], 'supply': 'additional_required' if physical.startswith('ADD-') else 'existing'})
    csv_write(output / 'Q4_分区配置.csv', template_rows)
    csv_write(output / 'resource_assignments.csv', allocation)
    print(json.dumps({'elapsed_s': result['elapsed_s'], 'enumeration': enumeration,
                      'primary': {k: {n: recommendations[k][n] for n in ('groups','total','gap','workload_cv')}
                                  for k in ('strict_2_minimum','strict_3_minimum')}}, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solution', type=Path, default=ROOT / 'results/question3_agent_update/solution_recommended.json')
    parser.add_argument('--validation', type=Path, default=ROOT / '第四问/结果/q3_baseline_validation.json')
    parser.add_argument('--output', type=Path, default=ROOT / '第四问/结果')
    args = parser.parse_args()
    run(args.solution, args.validation, args.output)
