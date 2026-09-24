"""Recompute all six Q1 lexicographic orders, preserving existing results."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
_runtime = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (__name__ == '__main__' and (PROJECT/'.runtime/python').is_dir()
        and _runtime.exists() and Path(sys.executable).resolve() != _runtime.resolve()):
    import subprocess
    raise SystemExit(subprocess.call([str(_runtime), '-X', 'utf8', str(Path(__file__).resolve()), *sys.argv[1:]]))
for folder in (PROJECT/'src', PROJECT/'.runtime/python'):
    if folder.is_dir(): sys.path.insert(0, str(folder))
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')

import argparse
from datetime import datetime, timezone
import importlib.metadata
import itertools
import json
import math

from uav_rescue.common.data import load_inputs, file_hash
from uav_rescue.common.terrain import Terrain
from uav_rescue.q1.optimize import enumerate_batches, solve, assign_boxes
from uav_rescue.q1.pipeline import summarize
from uav_rescue.q1.validate import validate_manifest, milp_crosscheck


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='outputs/q1_sensitivity_05_45/tables/results.json')
    args = parser.parse_args()
    source = (PROJECT/args.source).resolve()
    old = json.loads(source.read_text(encoding='utf-8'))
    cfg = old['metadata']['config']; physics = cfg['physics']
    reserve = old['baseline']['reserve']
    assert reserve == .2
    source_hashes = old['metadata']['input_sha256']
    protected = {str(p): file_hash(p) for directory in [PROJECT/'outputs', PROJECT.parent/'写作']
                 for p in directory.rglob('*') if p.is_file() and not p.name.startswith('~$')
                 and not p.is_relative_to(PROJECT/'outputs/q1_objective_audit')}
    for path, digest in source_hashes.items():
        assert file_hash(Path(path)) == digest, path
    data = load_inputs(PROJECT/cfg['paths']['data_root'], PROJECT/cfg['paths']['template'])
    terrain = Terrain(data.dem_path, data.origin)
    routes = {s: terrain.route(n, physics['clearance_m'], physics['service_height_m']) for s,n in data.services.items()}
    candidates = {s: enumerate_batches(data, s, route, reserve, physics['gravity_m_s2'],
                   physics['energy_tolerance_kwh']) for s,route in routes.items()}
    names = {'sorties': '架次', 'energy': '能耗', 'time': '时间'}
    results = []
    for order in itertools.permutations(['sorties', 'energy', 'time']):
        solutions = [solve(s, data.counts(s), candidates[s], order) for s in data.services]
        assert all(s.feasible for s in solutions)
        rows = assign_boxes(data, solutions)
        validation = validate_manifest(data, routes, rows, reserve, physics['gravity_m_s2'], physics['energy_tolerance_kwh'])
        checks = [milp_crosscheck(data.counts(s.service), candidates[s.service], s,
                  cfg['optimization']['milp_time_limit_s']) for s in solutions]
        summary = summarize(rows, True)
        for original in old['comparisons']:
            if tuple(original['order']) == order:
                assert rows == original['rows'], '旧对照逐箱方案发生变化'
                for key in ['sorties','energy_kwh','operation_s']:
                    assert math.isclose(summary[key], original['summary'][key], rel_tol=0, abs_tol=1e-8)
        item = {'order': list(order), 'label': '→'.join(names[k] for k in order),
                'reserve': reserve, 'feasible': True, 'summary': summary, 'rows': rows,
                'verification': validation, 'milp': checks}
        results.append(item)
        print(item['label']+': '+json.dumps(summary, ensure_ascii=False), flush=True)
    for path, digest in {**protected, **source_hashes}.items():
        assert file_hash(Path(path)) == digest, '原文件改变: '+path
    output = PROJECT/'outputs/q1_objective_audit'; output.mkdir(exist_ok=True)
    scripts = [Path(__file__), PROJECT/'src/uav_rescue/q1/validate.py', PROJECT/'src/uav_rescue/q1/optimize.py']
    report = {'metadata': {'generated_utc': datetime.now(timezone.utc).isoformat(),
              'source': str(source), 'source_sha256': file_hash(source), 'input_sha256': source_hashes,
              'config': cfg, 'code_sha256': {str(p): file_hash(p) for p in scripts},
              'dependencies': {n: importlib.metadata.version(n) for n in ['numpy','scipy','openpyxl','Pillow']},
              'protected_files_unchanged': len(protected),
              'dp_precision': {'energy_kwh': 1e-12, 'operation_s': 1e-6},
              'milp_comparison_tolerance': {'energy_kwh': 1e-7, 'operation_s': 1e-4}},
              'comparisons': results, 'passed': True}
    (output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    lines = ['# 问题一目标优先级补充核验', '',
             '在20%安全余量及既有物理口径下，完整枚举三项目标的6种词典序排列。每种方案均逐箱独立重算，并按该方案自身目标顺序对15个服务区进行三阶段MILP核验。既有结果不覆盖。', '',
             '| 目标优先级 | 架次数 | 能耗/kWh | 累计作业/s | 累计作业/h | A/B/C架次 |',
             '|---|---:|---:|---:|---:|---|']
    for r in results:
        s = r['summary']; counts = '/'.join(str(s['drone_counts'][g]) for g in 'ABC')
        lines.append(f"| {r['label']} | {s['sorties']} | {s['energy_kwh']:.6f} | {s['operation_s']:.3f} | {s['operation_s']/3600:.6f} | {counts} |")
    lines += ['', '主图使用“架次→能耗→时间”“能耗→架次→时间”“时间→架次→能耗”，覆盖每个指标作为首要目标的情形。其余三种排列作为次级优先关系补充核查。',
              '相同汇总指标不自动代表全部逐箱方案相同；完整分配保留于results.json。不同首要目标也可能取得相同最优指标，不能为了显示差异而人为更改结果。',
              '词典序含义是先将第一指标优化至最优，再在其最优解集合内优化第二、第三指标。6种排列的对照仍不等于完整Pareto前沿，也未改变既有的能耗解释与约束。',
              '累计作业时间是各架次耗时相加，不是多机并行任务完成时间。最优性限于本问模型、候选完整枚举及既有数值精度。',
              '', '复现：`python scripts/audit_q1_objectives.py`。']
    (output/'目标优先级补充核验.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print('全部6种顺序、90组服务区MILP核验通过。', flush=True)


if __name__ == '__main__':
    main()
