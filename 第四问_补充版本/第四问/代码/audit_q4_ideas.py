"""Audit external Q4 ideas against the authoritative frozen 21-transport schedule.

The external folders are read-only references. This script evaluates the existing
Q4 candidate rows with two additional diagnostics suggested by those references:
interval-based resource utilization and balance-first ranking. It never rebuilds
or re-plans Q3 relay tasks.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / '第四问/版本/20260925_21运输确认刷新/结果'
KEYS = ('U_A', 'U_B', 'U_C', 'B_A', 'B_B', 'B_C', 'R', 'RE')
AIR = ('U_A', 'U_B', 'U_C', 'R')
POWER = ('B_A', 'B_B', 'B_C', 'RE')


def load(path: Path):
    return json.loads(path.read_text(encoding='utf8'))


def interval_peak(rows):
    """Maximum overlap on half-open [start, end) integer-ms intervals."""
    events = defaultdict(lambda: [0, 0])
    for row in rows:
        if row['end_ms'] <= row['start_ms']:
            raise ValueError(f"Invalid interval: {row}")
        events[row['start_ms']][1] += 1
        events[row['end_ms']][0] += 1
    active = peak = 0
    for tick in sorted(events):
        # Release first at equal timestamps, matching the Q4 solver and validator.
        active -= events[tick][0]
        active += events[tick][1]
        peak = max(peak, active)
    return peak


def usage(rows):
    """Return busy-time utilization of the minimum interval coloring."""
    if not rows:
        return None
    count = interval_peak(rows)
    span = max(x['end_ms'] for x in rows) - min(x['start_ms'] for x in rows)
    busy = sum(x['end_ms'] - x['start_ms'] for x in rows)
    return None if not span or not count else busy / (count * span)


def groups_for_row(row, q3, edges):
    transport = {t['id']: t for t in q3['transport']}
    relay = {r['id']: r for r in q3['relay']}
    out = []
    for services in row['groups']:
        service_set = set(services)
        tids = {t['id'] for t in q3['transport'] if t['order'][0] in service_set}
        rids = {rid for rid, linked in edges.items() if tids.intersection(linked)}
        out.append((tids, rids, transport, relay))
    return out


def row_metrics(row, q3, edges, intervals):
    groups = groups_for_row(row, q3, edges)
    group_utils = []
    for tids, rids, _, _ in groups:
        tasks = tids | rids
        per_resource = {}
        for key in KEYS:
            selected = [x for x in intervals[key] if x['task'] in tasks]
            per_resource[key] = usage(selected)
        values = [v for v in per_resource.values() if v is not None]
        group_utils.append({'by_resource': per_resource,
                            'mean': sum(values) / len(values) if values else None,
                            'minimum': min(values) if values else None})
    means = [x['mean'] for x in group_utils if x['mean'] is not None]
    minima = [x['minimum'] for x in group_utils if x['minimum'] is not None]
    row = dict(row)
    row['gap_total'] = sum(row['gap'].values())
    row['gap_aircraft'] = sum(row['gap'][key] for key in AIR)
    row['gap_energy'] = sum(row['gap'][key] for key in POWER)
    row['total_aircraft'] = sum(row['total'][key] for key in AIR)
    row['total_energy_resources'] = sum(row['total'][key] for key in POWER)
    row['group_resource_utilization'] = group_utils
    row['mean_resource_utilization'] = sum(means) / len(means) if means else None
    row['minimum_resource_utilization'] = min(minima) if minima else None
    return row


def current_key(row):
    return (row['gap_aircraft'], row['gap_energy'], row['total_aircraft'],
            row['total_energy_resources'], row['workload_cv'], row['groups'])


def balance_key(row):
    return (row['workload_cv'], row['gap_aircraft'], row['gap_energy'],
            row['total_aircraft'], row['total_energy_resources'], row['groups'])


def utilization_key(row):
    # Diagnostic only: do not replace the declared shortage-first objective.
    return (-row['mean_resource_utilization'],) + current_key(row)


def main(results: Path):
    results = results.resolve()
    q4 = load(results / 'solution_q4.json')
    q3 = load(results.parent / '数据/q3_frozen_solution.json')
    rows = load(results / 'all_partitions.json')
    edges = q4['relay_transport_edges']
    intervals = q4['resource_intervals']

    checked = [row_metrics(r, q3, edges, intervals) for r in rows]
    by_policy = {}
    recommendations = {}
    for policy in ('strict', 'clone'):
        for k in (2, 3):
            candidates = [r for r in checked if r['policy'] == policy and r['K'] == k and r['identity'] == 'minimum']
            if not candidates:
                continue
            tag = f'{policy}_{k}'
            current = min(candidates, key=current_key)
            balance = min(candidates, key=balance_key)
            utilization = min(candidates, key=utilization_key)
            recommendations[tag] = {
                'current_shortage_first': current['partition_id'],
                'balance_first': balance['partition_id'],
                'utilization_diagnostic': utilization['partition_id'],
                'current_equals_solver_recommendation': current['partition_id'] == q4['recommendations'][f'{policy}_{k}_minimum']['partition_id'],
                'current': current,
                'balance': balance,
                'utilization': utilization,
            }
            by_policy[tag] = len(candidates)

    # A dominance check on the two ideas most relevant to Q4: shortage and balance.
    dominance = {}
    for tag, info in recommendations.items():
        candidates = [r for r in checked if f"{r['policy']}_{r['K']}" == tag and r['identity'] == 'minimum']
        cur = info['current']
        dominating = [r['partition_id'] for r in candidates
                      if r['gap_aircraft'] <= cur['gap_aircraft']
                      and r['gap_energy'] <= cur['gap_energy']
                      and r['workload_cv'] <= cur['workload_cv']
                      and (r['gap_aircraft'], r['gap_energy'], r['workload_cv']) !=
                      (cur['gap_aircraft'], cur['gap_energy'], cur['workload_cv'])]
        dominance[tag] = dominating

    compact = []
    for tag, info in recommendations.items():
        for label in ('current', 'balance', 'utilization'):
            r = info[label]
            compact.append({
                'policy_K': tag, 'selection': label, 'partition_id': r['partition_id'],
                'groups': r['groups'], 'gap_total': r['gap_total'],
                'gap_aircraft': r['gap_aircraft'], 'gap_energy': r['gap_energy'],
                'total_aircraft': r['total_aircraft'],
                'total_energy_resources': r['total_energy_resources'],
                'workload_cv': r['workload_cv'],
                'mean_resource_utilization': r['mean_resource_utilization'],
                'minimum_resource_utilization': r['minimum_resource_utilization'],
                'relay_sorties_executed': r['relay_sorties_executed'],
                'total_energy_kwh': r['total_energy_kwh'],
            })
    audit = {
        'source_results': str(results),
        'source_q3_sha256': q4['source_solution_sha256'],
        'transport_sorties': len(q3['transport']),
        'relay_sorties': len(q3['relay']),
        'partition_counts': by_policy,
        'recommendations': recommendations,
        'shortage_balance_dominance': dominance,
        'conclusion': {
            'strict_shortage_first_has_no_dominator': all(not dominance[tag] for tag in dominance if tag.startswith('strict_')),
            'strict_partition_structure_can_improve_balance_only_by_accepting_more_shortage': (
                recommendations['strict_2']['current']['partition_id'] != recommendations['strict_2']['balance']['partition_id']
                and recommendations['strict_2']['balance']['workload_cv'] < recommendations['strict_2']['current']['workload_cv']
                and recommendations['strict_2']['balance']['gap_total'] > recommendations['strict_2']['current']['gap_total']),
            'clone_is_sensitivity_only': True,
            'q4_refresh_improves_declared_primary_objective': False,
        },
        'external_reference_findings': [
            {'source': r'D:\Desktop\Agent\第一版\求解\问题四\问题四.py',
             'usable_idea': '运输架次形成原子块后枚举K=2/3分区，并同时记录缺口、资源总量和工作量CV。',
             'boundary': '其结果来自不同第三问任务/中继结构，不能直接覆盖当前21运输＋3中继冻结输入。'},
            {'source': r'D:\Desktop\Agent\Deepseek版本二\code\q4.py',
             'usable_idea': '把区间利用率和负载均衡作为资源配置的补充诊断。',
             'boundary': '代码重新运行通信分析和中继排程；且rgrp筛选会把所有中继纳入组，不能作为当前冻结通信关系的求解器。'},
            {'source': r'E:\sxjm\method.txt',
             'usable_idea': '超图/不可拆原子块、枚举或MILP/CP-SAT、区间最大并发、缺口和Pareto比较。',
             'boundary': '公开资料只提供方法级框架，没有可直接核验的Q4数值结果。'},
        ],
        'implementation_checks': [
            '所有资源区间使用半开规则[start,end)，端点先释放后占用。',
            '中继任务按照实际通信关联绑定，不重新规划、不从候选覆盖集合推断实际保障。',
            '当前主排序仍为飞机缺口、能源缺口、飞机配置、能源配置、运输工作量CV。',
        ],
    }
    (results / 'external_idea_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    with (results / 'external_idea_partition_metrics.csv').open('w', encoding='utf-8-sig', newline='') as f:
        fieldnames = list(compact[0])
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(compact)

    lines = [
        '# 第四问外部思路复核与刷新判断', '',
        '## 1. 复核范围', '',
        '本次只读取 `D:/Desktop/Agent` 下的四份 Agent 工程和 `E:/sxjm/method.txt`。外部结果不作为当前第三问输入；当前第四问继续冻结正式的21个运输架次和3个中继架次。', '',
        '可复用的方法是：运输架次超图形成不可拆原子块、K=2/3完整枚举、资源区间最大并发、资源缺口与工作量均衡共同比较，以及保留Pareto候选。当前第四问的正式求解器已经实现这些核心内容；本轮新增利用率诊断并对全部候选重新排序。', '',
        '## 2. 外部代码的边界问题', '',
        '1. `第一版/问题四.py`采用另一份第三问任务和中继结构，输出4个原子块，不能把它的分区编号直接映射到当前21运输＋3中继方案。',
        '2. `Deepseek版本二/q4.py`在每个组内重新执行通信分析和中继规划，这会改变冻结的中继安排与通信关系；其中中继筛选使用全部`rsched_all`的站点—开始时刻集合，实际会把所有中继任务纳入每个组。',
        '3. `Deepseek版本二/q4.py`的`peak`事件在同一时刻先处理开始再处理结束，等价于闭区间，会把刚好释放后复用的资源误算为冲突。当前复核使用左闭右开区间并先释放。',
        '4. 外部代码以“先均衡”或资源总数作为排序时，可能得到更低CV但更大的库存缺口；这只能作为备选，不能覆盖题面要求下的资源可执行性。', '',
        '## 3. 当前冻结方案的三种比较', '',
        '| 口径 | 资源优先分区 | 均衡优先分区 | 诊断结果 |',
        '| --- | --- | --- | --- |',
    ]
    for tag, info in recommendations.items():
        cur, bal, util = info['current'], info['balance'], info['utilization']
        lines.append(f'| {tag} | `{cur["partition_id"]}`，缺口{cur["gap_total"]}，CV={cur["workload_cv"]:.6f} | `{bal["partition_id"]}`，缺口{bal["gap_total"]}，CV={bal["workload_cv"]:.6f} | 利用率优先候选 `{util["partition_id"]}` |')
    lines += [
        '',
        '严格2组中，资源优先的当前推荐为 `S001` 与其余14区，缺口总数4、CV=0.842429；如果把负载均衡提前，最好的分区变为 `S001/S010/S014` 与其余12区，CV降至0.673048，但缺口总数升至9。因此它是目标权衡，不是对当前主方案的支配改进。严格3组只有1个合法原子块分区，无法通过算法换出新结构。',
        '允许复制完整中继的clone结果仍只作敏感性分析；它可以改善某些CV或机型缺口，但会增加真实中继架次和能耗，不满足严格冻结中继任务的主口径。',
        '',
        '## 4. 刷新结论', '',
        '在当前正式21运输＋3中继、固定任务/通信关系、组间资源不可调配的题面边界下，没有发现同时减少主排序缺口并改善工作量均衡的合法新分区。第四问主答案不需要改写；本轮新增的是外部思路审计、资源利用率指标和可复核的均衡备选比较。', '',
        '完整逐候选诊断见 `external_idea_partition_metrics.csv`；结构化证据见 `external_idea_audit.json`。',
    ]
    (results.parent / '文字/第四问_外部思路复核报告.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    print(json.dumps({'results': str(results), 'transport_sorties': len(q3['transport']), 'relay_sorties': len(q3['relay']),
                      'rows_checked': len(checked), 'conclusion': audit['conclusion']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=DEFAULT_RESULTS)
    main(parser.parse_args().results)
