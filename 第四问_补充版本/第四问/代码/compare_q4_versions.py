"""Compare two independently audited frozen-schedule results and write a refresh report."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(str(v).replace('\n', ' ') for v in r) + ' |' for r in rows])


def compare(old_dir, new_dir, workbook_dir):
    old_dir, new_dir, workbook_dir = [p.resolve() for p in (old_dir, new_dir, workbook_dir)]
    before, after = [read(p / 'solution_q4.json') for p in (old_dir, new_dir)]
    qold, qnew = [read(p.parent / '数据/q3_frozen_solution.json') for p in (old_dir, new_dir)]
    q3proof = read(new_dir / 'q3_baseline_validation.json')
    proof = read(new_dir / 'validation_q4.json')
    bookproof = read(workbook_dir / 'workbook_readback_qa.json')
    book = workbook_dir / '第四问_任务分区与资源配置.xlsx'
    assert q3proof['passed'] and q3proof['solution_sha256'] == sha(new_dir.parent / '数据/q3_frozen_solution.json')
    assert proof['passed'] and proof['solution_sha256'] == sha(new_dir / 'solution_q4.json')
    assert bookproof['passed'] and bookproof['source_solution_sha256'] == sha(new_dir / 'solution_q4.json')
    assert bookproof['output_sha256'] == sha(book)
    assert before['source_solution_sha256'] == sha(old_dir.parent / '数据/q3_frozen_solution.json')

    metrics = {key: {'old': qold['metrics'][key], 'new': val,
                     'delta': val - qold['metrics'][key]}
               for key, val in qnew['metrics'].items() if isinstance(val, (int, float))}
    task_changes = []
    for kind in ('transport', 'relay'):
        prior = {t['id']: t for t in qold[kind]}
        current = {t['id']: t for t in qnew[kind]}
        assert prior.keys() == current.keys(), 'Task inventory changed; needs a different task matching report'
        for tid, t in current.items():
            fields = sorted(k for k in prior[tid].keys() | t.keys() if prior[tid].get(k) != t.get(k))
            if fields:
                brief = {k: {'old': prior[tid].get(k), 'new': t.get(k)} for k in fields
                         if k in ('start_s', 'flight_start_s', 'return_s', 'resource_end_s',
                                  'battery_ready_s', 'service_start_s', 'service_end_s', 'station',
                                  'drone', 'battery', 'component', 'order', 'boxes')}
                task_changes.append({'kind': kind, 'task': tid, 'changed_fields': fields, 'values': brief})

    interval_changes = []
    for resource, rows in after['resource_intervals'].items():
        prior = {r['task']: r for r in before['resource_intervals'][resource]}
        for row in rows:
            if row != prior[row['task']]:
                interval_changes.append({'resource': resource, 'task': row['task'],
                                         'old': prior[row['task']], 'new': row})
    oldrows, newrows = [read(p / 'all_partitions.json') for p in (old_dir, new_dir)]
    previous = {r['partition_id']: r for r in oldrows}
    assert previous.keys() == {r['partition_id'] for r in newrows}, 'Partition inventory changed'
    partition_changes = []
    for r in newrows:
        old = previous[r['partition_id']]
        differences = {k: {'old': old.get(k), 'new': r.get(k)} for k in old.keys() | r.keys() if old.get(k) != r.get(k)}
        if differences:
            partition_changes.append({'partition_id': r['partition_id'], 'changes': differences})
    field_counts = Counter(k for r in partition_changes for k in r['changes'])
    recommendations = {}
    for name, rec in after['recommendations'].items():
        old = before['recommendations'][name]
        fields = ('groups', 'total', 'gap', 'workload_cv', 'workload_ms', 'boxes_by_group',
                  'relay_sorties_executed', 'total_energy_kwh', 'pareto_partition_ids',
                  'balance_first', 'same_optimum_count')
        recommendations[name] = {k: old[k] == rec[k] for k in fields if k != 'balance_first'}
        # A balance-first record also contains makespan; compare decisions and resources separately.
        recommendations[name]['balance_first_decision'] = all(old['balance_first'][k] == rec['balance_first'][k]
                                                               for k in ('groups', 'total', 'gap', 'workload_cv'))
    same_decisions = all(all(r.values()) for r in recommendations.values())
    comparisons = {
        'old_q3_sha256': before['source_solution_sha256'], 'new_q3_sha256': after['source_solution_sha256'],
        'old_q4_sha256': sha(old_dir / 'solution_q4.json'), 'new_q4_sha256': sha(new_dir / 'solution_q4.json'),
        'q3_metrics': metrics, 'task_changes': task_changes, 'resource_interval_changes': interval_changes,
        'transport_blocks_unchanged': before['transport_blocks'] == after['transport_blocks'],
        'strict_blocks_unchanged': before['strict_blocks'] == after['strict_blocks'],
        'relay_transport_edges_unchanged': before['relay_transport_edges'] == after['relay_transport_edges'],
        'all_recommendation_decisions_unchanged': same_decisions, 'recommendation_checks': recommendations,
        'all_partition_rows': len(newrows), 'changed_partition_rows': len(partition_changes),
        'partition_changed_field_counts': dict(sorted(field_counts.items())),
        'partition_changes': partition_changes,
        'q3_promoted_to_formal': False,
        'interpretation': 'Separate candidate refresh; original formal Q3 and original Q4 are preserved.'}
    write(new_dir / 'version_comparison.json', comparisons)
    keys = after['resource_keys']

    def area(groups):
        return ' / '.join('、'.join(g) for g in groups)

    def gap(rec):
        return '；'.join(f'{after["resource_labels"][k]} +{rec["gap"][k]}' for k in keys if rec['gap'][k]) or '无'

    intro = ('刷新后的推荐分区、逐类配置、库存缺口和工作量均衡均与原版一致。'
             if same_decisions else '新版出现分区或资源决策变化，以下逐项列出。')
    parts = ['# 第四问：使用更新第三问候选的复算与版本比较',
             '## 1. 刷新结论与来源', intro + '本次实际重新进行了第三问物理验证、第四问完整枚举与独立核验。'
             '新第三问来源为 `results/q3_remote_long_20260925/solution_recommended.json`；'
             '第三问正式入口仍指向 `question3_agent_update`，所以本次结果作为独立候选复算版本保存。',
             '原第三问报告指出，新候选仅把联合完工缩短1毫秒，加权平均交付反而更晚，因此未升级为正式版。'
             '这次不改第三问的正式版本标识，也不覆盖原第四问。',
             table(['第三问指标', '原正式基线', '远端候选', '新减旧'],
                   [[label, f'{metrics[key]["old"]:.12g}', f'{metrics[key]["new"]:.12g}',
                     f'{metrics[key]["delta"]:+.9f}'] for key, label in [
                       ('boxes', '货箱数'), ('transport_sorties', '运输架次'), ('relay_sorties', '中继架次'),
                       ('total_energy_kwh', '总能耗/kWh'), ('joint_finish_s', '联合完工/s'),
                       ('weighted_mean_delivery_s', '加权平均交付/s'), ('late_boxes', '迟到箱数')]]),
             f'原输入SHA256：`{before["source_solution_sha256"]}`。\n\n新输入SHA256：`{after["source_solution_sha256"]}`。',
             '## 2. 模型与枚举边界',
             '冻结第三问的货箱批次、访问顺序、机型、全部起止时刻、中继位置与服务窗口、实际通信对应关系。'
             '同一运输任务涉及的服务区必须同组；主方案要求每个原中继架次完整执行一次，因此使用同一原中继任务的运输也须同组。'
             '同组内保留原资源编号复用关系；跨组必须使用不同实体。',
             '运输机占用准备开始至释放，电池占用准备开始至返航充满；中继机计入返航后300秒周转，能源组件按原两阶段充电模型计入补能。'
             '使用整数毫秒左闭右开区间，同一时刻先释放后复用。每组每类最低配置为区间最大重叠数；区间着色达到该下界。'
             '库存缺口=max(0，各组独立需求之和−一份全局库存)。不把每个组各与全库存比较。',
             '排序依次最小化：飞机缺口、能源资源缺口、飞机配置数、能源配置数、运输工作量CV。'
             '工作量是准备至返航时长之和，单位为无人机·秒。该排序表达数量优先关系，不代表设备价格。',
             table(['任务块', '必须同组的服务区'], [[i, '、'.join(g)] for i, g in enumerate(after['strict_blocks'], 1)]),
             table(['规则', '组数', '任务块数', '不重复分区数', '零库存缺口数'],
                   [[e['policy'], e['K'], e['blocks'], e['partitions'], e['minimum_zero_gap']] for e in after['enumeration']]),
             'strict为主方案；clone仅作为“允许每组复制完整原中继任务”的额外敏感性假设，会增加真实中继架次与能耗。'
             'clone不代表严格保持原中继架次，也不额外证明同位置多机的现实防碰撞或干扰可行性。',
             '## 3. 刷新后的主方案']
    for k in (2, 3):
        rec = after['recommendations'][f'strict_{k}_minimum']
        parts += [f'### {k}组配置', table(['组', '服务区', '货箱', '运输作业量/机·秒', '原中继任务'],
                 [[f'G{i}', '、'.join(g['services']), g['box_count'], f'{g["transport_work_ms"]/1000:.3f}',
                   '、'.join(g['relay']) or '无'] for i, g in enumerate(rec['group_details'], 1)]),
                 table(['组', '运输A', '运输B', '运输C', '电池A', '电池B', '电池C', '中继机', '中继组件'],
                       [[f'G{i}'] + [g['resource_minimum'][r] for r in keys] for i, g in enumerate(rec['group_details'], 1)] +
                       [['合计'] + [rec['total'][r] for r in keys], ['库存'] + [after['inventory'][r] for r in keys],
                        ['缺口'] + [rec['gap'][r] for r in keys]]),
                 f'缺口：{gap(rec)}。运输工作量CV={rec["workload_cv"]:.6f}。补齐库存后保持新第三问时刻执行，'
                 f'联合完工{rec["joint_finish_s"]:.3f} s，总能耗{rec["total_energy_kwh"]:.11f} kWh。']
    witnesses = []
    for k in (2, 3):
        rec = after['recommendations'][f'strict_{k}_minimum']
        for i, g in enumerate(rec['group_details'], 1):
            for resource in keys:
                w = g['peak_witnesses'][resource]
                if rec['gap'][resource] and w:
                    witnesses.append([k, f'G{i}', after['resource_labels'][resource], g['resource_minimum'][resource],
                                      f'{w["time_s"]:.3f}', '、'.join(w['tasks'])])
    parts += ['### 缺口的任务峰值证据', table(['组数', '任务组', '资源', '组内下界', '峰值时刻/s', '同时占用的任务'], witnesses),
              '各组的峰值不必同时发生；禁止跨组调配使各组下界仍须相加。`resource_assignments.csv`已给出对应实体编号，'
              '`ADD-*`仅表示待补资源，不能算作已到位库存。', '## 4. 新旧变化逐项核对',
              f'运输块、中继闭包和实际“运输任务—中继任务”关联集合的比较结果分别为：'
              f'{comparisons["transport_blocks_unchanged"]}、{comparisons["strict_blocks_unchanged"]}、{comparisons["relay_transport_edges_unchanged"]}（True表示未变）。'
              f'逐条比较全部{len(newrows)}条分区评价，发生变化的字段及条数为 `{dict(sorted(field_counts.items()))}`。'
              '这意味着检查涵盖全部候选，不仅比较两套推荐的汇总值。',
              table(['类型', '任务', '变化字段'], [[r['kind'], r['task'], '、'.join(r['changed_fields'])] for r in task_changes]),
              f'共有{len(interval_changes)}条资源占用区间记录与旧版不同；原值与新值均保存于`version_comparison.json`。'
              '局部时间或通信区段变化未必改变任务关联闭包和区间重叠峰值，须以本轮逐项核验为准。',
              '## 5. 扩展规则下的备选',
              table(['组数', '复制中继时的资源优先分区', '缺口', '中继架次', '总能耗/kWh', 'CV'],
                    [[k, area(r['groups']), gap(r), r['relay_sorties_executed'], f'{r["total_energy_kwh"]:.9f}', f'{r["workload_cv"]:.6f}']
                     for k in (2, 3) for r in [after['recommendations'][f'clone_{k}_minimum']]]),
              '主方案与扩展方案的全部Pareto集合、原编号保留口径和均衡优先备选均已重新核对，详见JSON与Excel。',
              '## 6. 验证、结论范围与复现',
              table(['验证', '检查数', '错误数'], [['新第三问独立物理验证', q3proof['check_count'], q3proof['error_count']],
                    ['第四问独立枚举与配置核验', proof['check_count'], proof['error_count']],
                    ['Excel最终文件回读', bookproof['check_count'], bookproof['error_count']]]),
              f'Excel含{bookproof["formulas"]}个公式、{bookproof["task_rows"]}条原任务和{bookproof["resource_interval_rows"]}条资源占用记录。'
              f'求解耗时约{after["elapsed_s"]:.3f}秒，本地CPU完成。未连接远端或使用GPU。',
              '精确最优仅针对本次冻结第三问、明确的中继规则和字典序目标；不能推出第三问或联合Q3/Q4全局最优。'
              '本次没有证据支持第四问资源配置获得改善。要减少现有缺口，需要回到第三问改变路线、中继关联或时序，属于另一个联合优化任务。',
              f'刷新Excel：`{book.relative_to(ROOT)}`。新结果目录：`{new_dir.relative_to(ROOT)}`。'
              '原结果与原Excel保留；修改前脚本、README及日志快照见本版本`数据/刷新前代码.zip`。',
              '复现命令（项目根目录，Python需openpyxl；Node需bundled artifact-tool）：',
              '```powershell\n'
              f'python -X utf8 code/validate_q3_senior.py --solution results/q3_remote_long_20260925/solution_recommended.json --output "{new_dir.relative_to(ROOT)}/q3_baseline_validation.json"\n'
              f'python -X utf8 第四问/代码/solve_q4.py --solution results/q3_remote_long_20260925/solution_recommended.json --validation "{new_dir.relative_to(ROOT)}/q3_baseline_validation.json" --output "{new_dir.relative_to(ROOT)}"\n'
              f'python -X utf8 第四问/代码/validate_q4.py --results "{new_dir.relative_to(ROOT)}"\n'
              f'node 第四问/代码/export_q4.mjs --results "{new_dir.relative_to(ROOT)}" --output "{workbook_dir.relative_to(ROOT)}"\n'
              f'python -X utf8 第四问/代码/verify_q4_workbook.py --results "{new_dir.relative_to(ROOT)}" --output "{workbook_dir.relative_to(ROOT)}"\n'
              f'python -X utf8 第四问/代码/compare_q4_versions.py --old "{old_dir.relative_to(ROOT)}" --new "{new_dir.relative_to(ROOT)}" --workbook-output "{workbook_dir.relative_to(ROOT)}"\n```']
    dest = new_dir.parent / '文字/第四问_刷新与对照报告.md'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('\n\n'.join(parts) + '\n', encoding='utf8')
    print(json.dumps({'report': str(dest), 'decisions_unchanged': same_decisions,
                      'partition_changed_fields': dict(field_counts), 'changed_tasks': len(task_changes),
                      'changed_resource_intervals': len(interval_changes)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, default=ROOT / '第四问/结果')
    parser.add_argument('--new', type=Path, required=True)
    parser.add_argument('--workbook-output', type=Path, required=True)
    args = parser.parse_args()
    compare(args.old, args.new, args.workbook_output)
