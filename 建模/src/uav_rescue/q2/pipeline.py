from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import csv
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import time
import tomllib
from ..common.data import file_hash
from .data import load_scheduling_inputs
from .routes import RouteFactory
from .search import optimize


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def retain_best_seen(payload):
    """A contrast run may also improve the primary order; do not discard that result."""
    def key(result,order):
        s=result['summary']
        values={'tardiness':round(s['weighted_tardiness_s']*1000),'makespan':round(s['makespan_s']*1000),
                'energy':sum(round(r['energy_kwh']*1e9) for r in result['sorties']),'sorties':s['sorties']}
        return tuple(values[k] for k in order)
    order=payload['metadata']['config']['optimization']['primary_order']
    if key(payload['comparison'],order)<key(payload['main'],order):
        payload['main_before_comparison']=payload['main']
        payload['schedule_before_comparison']=payload['schedule']
        payload['main']=payload['comparison']
        payload['schedule']=payload['comparison_schedule']
        payload['main_improved_by_comparison']=True


def export_excel(project,output,preview):
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    node=Path(os.environ.get('CODEX_NODE_EXECUTABLE',runtime/'node/bin/node.exe'))
    modules=Path(os.environ.get('CODEX_NODE_MODULES',runtime/'node/node_modules'))
    if not node.exists() or not (modules/'@oai/artifact-tool').exists():raise RuntimeError('缺少表格工具运行时；可用--skip-excel仅输出CSV/JSON')
    env=dict(os.environ,CODEX_NODE_MODULES=str(modules))
    result=subprocess.run([str(node),str(project/'scripts/export_q2.mjs'),str(output),'true' if preview else 'false'],
                          env=env,cwd=project,capture_output=True,text=True,encoding='utf-8',errors='replace')
    (output/'logs/excel_export.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('Excel导出失败，见logs/excel_export.log')
    inspection=output/'问题二结果.xlsx.inspect.ndjson'
    if inspection.exists():inspection.replace(output/'logs/artifact_tool_inspect.ndjson')


def run(project,config_path,args):
    started=time.perf_counter();cfg=tomllib.loads(config_path.read_text(encoding='utf-8'))
    paths={k:(project/v).resolve() for k,v in cfg['paths'].items()}
    output=(project/args.output).resolve() if args.output else paths['output']
    for folder in ['tables','figures','logs']:(output/folder).mkdir(parents=True,exist_ok=True)
    cache=paths['cache'];cache.mkdir(parents=True,exist_ok=True)
    from .report import build_sheets,make_figures,write_report
    from ..q1.report import verify_workbook
    data=load_scheduling_inputs(paths['data_root'],paths['template'])
    if args.report_only or args.verify_only:
        payload=json.loads((output/'tables/results.json').read_text(encoding='utf-8'))
        for name,digest in payload['metadata']['input_sha256'].items():
            if file_hash(Path(name))!=digest:raise RuntimeError('输入已改变，禁止将旧结果与新数据混用')
        if args.verify_only:
            from .validate import validate_schedule
            factory=RouteFactory(data,payload['metadata']['config']['physics'])
            def compare(a,b,location='result'):
                if isinstance(a,dict):
                    if set(a)!=set(b):raise AssertionError(f'{location}字段不一致')
                    for key in a:compare(a[key],b[key],location+'.'+key)
                elif isinstance(a,list):
                    if len(a)!=len(b):raise AssertionError(f'{location}长度不一致')
                    for i,(x,y) in enumerate(zip(a,b)):compare(x,y,f'{location}[{i}]')
                elif isinstance(a,(int,float)) and not isinstance(a,bool):
                    if not isinstance(b,(int,float)) or not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-8):raise AssertionError(f'{location}数值不一致')
                elif a!=b:raise AssertionError(f'{location}内容不一致')
            for name,schedule_key in [('main','schedule'),('comparison','comparison_schedule')]:
                pool={}
                for row in payload[name]['sorties']:
                    p=factory.make(row['drone'],row['boxes'],row['visits'])
                    if p is None:raise AssertionError('保存架次违反物理约束')
                    pool[p.id]=p
                rebuilt=validate_schedule(payload[schedule_key],pool,factory)
                compare(rebuilt,payload[name],name)
            checks={'main_recomputed':True,'comparison_recomputed':True,'sources_match':True}
            if (output/'问题二结果.xlsx').exists():checks['excel']=verify_workbook(output/'问题二结果.xlsx',build_sheets(payload,data))
            save(output/'logs/verification_replay.json',checks)
            print('独立重算核验通过：'+json.dumps(checks,ensure_ascii=False),flush=True)
            return
    else:
        if cfg['optimization']['time_unit_s']!=0.001 or cfg['optimization']['energy_unit_kwh']!=1e-9:
            raise ValueError('当前实现的调度精度固定为1毫秒，能耗目标精度1e-9kWh')
        if args.budget_seconds is not None:
            keys=['initial_seconds','search_seconds','final_seconds','comparison_seconds']
            if args.budget_seconds<=0:raise ValueError('预算必须为正')
            total=sum(cfg['optimization'][k] for k in keys)
            for k in keys:cfg['optimization'][k]*=args.budget_seconds/total
        hashes={str(p):file_hash(p) for p in data.base.source_paths}
        if args.resume:
            checkpoint=output/'logs/checkpoint.json'
            if not checkpoint.exists():raise ValueError('没有可继续的检查点')
            cfg['optimization']['resume_checkpoint']=str(checkpoint)
        # Preserve Q1 artifacts and the existing paper as well as raw attachments.
        protected=[p for folder in [project/'outputs/q1',project.parent/'写作'] if folder.exists() for p in folder.rglob('*') if p.is_file() and not p.name.startswith('~$')]
        protected_hashes={str(p):file_hash(p) for p in protected}
        factory=RouteFactory(data,cfg['physics'])
        save(cache/'legs.json',factory.leg_records())
        print('问题二输入通过：80箱、31个硬时限箱、8架实体机、14组电池、240条有向航段。',flush=True)
        payload=optimize(factory,cfg['optimization'],output,cache)
        payload['nodes']=[{'id':n.id,'lon':n.lon,'lat':n.lat,'elevation':n.elevation} for n in factory.nodes.values()]
        payload['legs']=factory.leg_records()
        payload['lower_bounds']={'weighted_tardiness_s':0,'sorties':math.ceil(sum(b.mass for b in data.base.boxes)/max(d.payload for d in data.base.drones.values())),
                                 'note':'零逾期与总质量/最大载质量是原问题有效下界；候选池CP-SAT界限仅适用于该次候选池及已固定前级目标。'}
        if any(file_hash(Path(p))!=h for p,h in hashes.items()):raise AssertionError('输入发生变化')
        if any(file_hash(Path(p))!=h for p,h in protected_hashes.items()):raise AssertionError('问题一或论文工程发生变化')
        payload['metadata']={'generated_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
                             'dependencies':{n:importlib.metadata.version(n) for n in ['ortools','numpy','scipy','openpyxl','matplotlib','Pillow']},
                             'config':cfg,'config_sha256':file_hash(config_path),'input_sha256':hashes,
                             'q1_and_paper_unchanged':True,'protected_file_count':len(protected_hashes),'sources_unchanged':True}
        save(output/'tables/results.json',payload)
    retain_best_seen(payload)
    payload['sheets']=build_sheets(payload,data)
    save(output/'tables/results.json',payload)
    for sheet in payload['sheets']:
        with (output/'tables'/f"{sheet['name']}.csv").open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(sheet['headers']);writer.writerows(sheet['rows'])
    for name in ['main','comparison','initial']:save(output/'tables'/f'{name}_manifest.json',payload[name])
    make_figures(payload,paths['font'],output/'figures')
    write_report(payload,output/'问题二结果分析.md')
    if cfg['export']['excel'] and not args.skip_excel:
        export_excel(project,output,cfg['export']['render_previews'])
        payload['excel_verification']=verify_workbook(output/'问题二结果.xlsx',payload['sheets'])
    else:payload['excel_verification']={'performed':False}
    payload['metadata']['last_export_elapsed_s']=time.perf_counter()-started
    save(output/'logs/run_manifest.json',payload['metadata'])
    save(output/'logs/solver_trace.json',payload['trace'])
    save(output/'logs/validation.json',{'main':payload['main']['verification'],'comparison':payload['comparison']['verification'],
                                      'excel':payload['excel_verification'],'sources_unchanged':payload['metadata']['sources_unchanged'],
                                      'q1_and_paper_unchanged':payload['metadata']['q1_and_paper_unchanged']})
    save(output/'tables/results.json',payload)
    print('问题二完成：'+json.dumps(payload['main']['summary'],ensure_ascii=False),flush=True)
