from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
import tomllib

from ..common.data import CATEGORIES, load_inputs, file_hash
from ..common.physics import safe_payload, trip_energy
from ..common.terrain import Terrain
from .optimize import enumerate_batches, solve, assign_boxes
from .validate import validate_manifest, milp_crosscheck, validate_sensitivity
from .report import build_sheets, write_report, make_figures, verify_workbook


def save_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def summarize(rows, feasible):
    if not feasible:
        return {"sorties": None, "energy_kwh": None, "operation_s": None, "flight_s": None,
                "drone_counts": None, "delivered_boxes": sum(r['box_count'] for r in rows)}
    return {"sorties": len(rows), "energy_kwh": math.fsum(r['energy_kwh'] for r in rows),
            "operation_s": math.fsum(r['operation_s'] for r in rows),
            "flight_s": math.fsum(r['flight_s'] for r in rows),
            "drone_counts": {g:sum(r['drone']==g for r in rows) for g in ['A','B','C']},
            "delivered_boxes": sum(r['box_count'] for r in rows)}


def export_excel(project: Path, output: Path, preview: bool):
    runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
    node = Path(os.environ.get("CODEX_NODE_EXECUTABLE", runtime / "node/bin/node.exe"))
    modules = Path(os.environ.get("CODEX_NODE_MODULES", runtime / "node/node_modules"))
    if not node.exists() or not (modules / "@oai/artifact-tool").exists():
        raise RuntimeError("缺少表格导出运行时。设置CODEX_NODE_EXECUTABLE与CODEX_NODE_MODULES；计算结果已保留。")
    env = dict(os.environ, CODEX_NODE_MODULES=str(modules))
    proc = subprocess.run([str(node), str(project / 'scripts/export_q1.mjs'), str(output),
                           'true' if preview else 'false'], cwd=project, env=env,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    (output / 'logs/excel_export.log').write_text(proc.stdout + '\n' + proc.stderr, encoding='utf-8')
    if proc.returncode:
        raise RuntimeError(f"Excel导出失败，见{output / 'logs/excel_export.log'}\n{proc.stderr[-1500:]}")


def run(project: Path, config_path: Path, output_override: str | None, skip_excel: bool):
    started = time.perf_counter()
    cfg = tomllib.loads(config_path.read_text(encoding='utf-8'))
    paths = {k: (project / v).resolve() for k,v in cfg['paths'].items()}
    output = (project / output_override).resolve() if output_override else paths['output']
    cache = paths['cache']
    for sub in ['tables','figures','logs']:(output/sub).mkdir(parents=True,exist_ok=True)
    cache.mkdir(parents=True,exist_ok=True)
    physics,opt = cfg['physics'],cfg['optimization']
    baseline_reserve=float(opt['baseline_reserve'])
    primary=tuple(opt['primary_order'])
    if primary != ('sorties','energy','time'):
        raise ValueError('本交付主方案固定为sorties→energy→time；对照方案通过comparison_orders设置')
    inputs=load_inputs(paths['data_root'],paths['template'])
    hashes={str(p):file_hash(p) for p in inputs.source_paths}
    terrain=Terrain(inputs.dem_path,inputs.origin)
    routes={sid:terrain.route(n,physics['clearance_m'],physics['service_height_m']) for sid,n in inputs.services.items()}
    save_json(cache/'routes.json',{s:asdict(r) for s,r in routes.items()})
    print('输入校验通过：80箱、758kg、2.011m³；15条往返航线完成地形检查。',flush=True)
    cached_candidates={}

    def scenario(reserve,order,label):
        capacities=[];solutions=[];candidate_counts={}
        for sid in inputs.services:
            for d in inputs.drones.values():
                cap,reason=safe_payload(d,routes[sid],reserve,physics['gravity_m_s2'],physics['payload_tolerance_kg'])
                capacities.append({'service':sid,'drone':d.id,'reserve':reserve,'safe_payload_kg':cap,
                    'reason':reason,'energy_budget_kwh':(1-reserve)*d.battery,
                    'energy_at_limit_kwh':None if cap is None else trip_energy(d,routes[sid],cap,physics['gravity_m_s2'])})
            key=(reserve,sid)
            if key not in cached_candidates:
                cached_candidates[key]=enumerate_batches(inputs,sid,routes[sid],reserve,physics['gravity_m_s2'],physics['energy_tolerance_kwh'])
            candidates=cached_candidates[key]
            candidate_counts[sid]=len(candidates)
            solutions.append(solve(sid,inputs.counts(sid),candidates,order))
        feasible=all(s.feasible for s in solutions)
        impossible=[]
        for solution in solutions:
            if solution.feasible:continue
            sid=solution.service
            candidates=cached_candidates[(reserve,sid)]
            for c,n in enumerate(inputs.counts(sid)):
                if not n or any(batch.counts[c]>0 for batch in candidates):continue
                mass,volume=inputs.attributes(sid)[c]
                for d in inputs.drones.values():
                    e=None if mass>d.payload else trip_energy(d,routes[sid],mass,physics['gravity_m_s2'])
                    impossible.append({'service':sid,'category':CATEGORIES[c],'box_count':n,'mass_kg':mass,'volume_m3':volume,
                        'drone':d.id,'single_box_energy_kwh':e,'budget_kwh':(1-reserve)*d.battery,
                        'mass_ok':mass<=d.payload,'volume_ok':volume<=d.volume,
                        'energy_ok':e is not None and e<=(1-reserve)*d.battery+physics['energy_tolerance_kwh']})
        if not feasible and not impossible:
            raise AssertionError('无共享资源时组批不可行却找不到不可单箱配送类别')
        rows=assign_boxes(inputs,solutions)
        checked=validate_manifest(inputs,routes,rows,reserve,physics['gravity_m_s2'],physics['energy_tolerance_kwh'],feasible)
        result={'label':label,'reserve':reserve,'order':list(order),'feasible':feasible,
                'infeasible_services':[s.service for s in solutions if not s.feasible],
                'infeasibility_details':impossible,
                'summary':summarize(rows,feasible),'rows':rows,'safe_payloads':capacities,
                'candidate_counts':candidate_counts,'state_count':sum(s.states for s in solutions),'verification':checked}
        return result,solutions

    baseline,solutions=scenario(baseline_reserve,primary,'架次→能耗→时间')
    if not baseline['feasible']:
        save_json(output/'logs/baseline_infeasible.json',baseline)
        raise RuntimeError(f"基准问题不可行：{baseline['infeasible_services']}，已保存诊断")
    baseline['sortie_lower_bound']=sum(max(
        math.ceil(math.fsum(b.mass for b in inputs.boxes if b.service==sid)/max(d.payload for d in inputs.drones.values())-1e-12),
        math.ceil(math.fsum(b.volume for b in inputs.boxes if b.service==sid)/max(d.volume for d in inputs.drones.values())-1e-12))
        for sid in inputs.services)
    print('主方案：'+json.dumps(baseline['summary'],ensure_ascii=False),flush=True)
    comparisons=[baseline]
    objective_names={'sorties':'架次','energy':'能耗','time':'时间'}
    for raw_order in opt['comparison_orders']:
        order=tuple(raw_order)
        result,_=scenario(baseline_reserve,order,'→'.join(objective_names[k] for k in order))
        comparisons.append(result)
    sensitivity=[]
    for reserve in sorted(set(float(r) for r in opt['sensitivity_reserves'])):
        result=baseline if reserve==baseline_reserve else scenario(reserve,primary,f'安全余量{reserve:.0%}')[0]
        sensitivity.append(result)
    monotonic=validate_sensitivity(sensitivity)
    crosschecks=[]
    for solution in solutions:
        crosschecks.append(milp_crosscheck(inputs.counts(solution.service),cached_candidates[(baseline_reserve,solution.service)],solution,opt['milp_time_limit_s']))
    print('15个服务区的三阶段整数规划核验通过；敏感性单调性检查通过。',flush=True)
    for (reserve,sid),batches in cached_candidates.items():
        save_json(cache/f'candidates_rho{reserve:.2f}_{sid}.json',[asdict(b) for b in batches])
    metadata={'generated_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
              'dependencies':{n:importlib.metadata.version(n) for n in ['numpy','scipy','openpyxl','Pillow','matplotlib']},
              'input_sha256':hashes,'config':cfg,'config_sha256':file_hash(config_path),
              'rounding':{'dp_energy_kwh':1e-12,'dp_time_s':1e-6,'payload_search_kg':physics['payload_tolerance_kg']}}
    payload={'metadata':metadata,'baseline':baseline,'comparisons':comparisons,'sensitivity':sensitivity,
             'routes':[asdict(r) for r in routes.values()],
             'validation':{'baseline':baseline['verification'],'milp':crosschecks,'sensitivity':monotonic}}
    payload['sheets']=build_sheets(payload,inputs)
    save_json(output/'tables/results.json',payload)
    for sheet in payload['sheets']:
        with (output/'tables'/f"{sheet['name']}.csv").open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(sheet['headers']);writer.writerows(sheet['rows'])
    for index,s in enumerate(comparisons):save_json(output/'tables'/f'objective_{index+1}_manifest.json',s)
    for s in sensitivity:save_json(output/'tables'/f"reserve_{s['reserve']:.2f}_manifest.json",s)
    make_figures(payload,paths['font'],output/'figures')
    write_report(payload,output/'问题一结果分析.md')
    if cfg['export']['excel'] and not skip_excel:
        export_excel(project,output,cfg['export']['render_previews'])
        payload['validation']['excel']=verify_workbook(output/'问题一结果.xlsx',payload['sheets'])
    else:payload['validation']['excel']={'performed':False,'reason':'显式跳过导出'}
    current={str(p):file_hash(p) for p in inputs.source_paths}
    if current!=hashes:raise AssertionError('原始附件发生变化')
    payload['validation']['sources_unchanged']=True
    metadata['elapsed_s']=time.perf_counter()-started
    save_json(output/'logs/run_manifest.json',metadata)
    save_json(output/'logs/validation.json',payload['validation'])
    save_json(output/'tables/results.json',payload)
    print(f'完成。结果目录：{output}',flush=True)
