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
import shutil
import time
import tomllib
from ..common.data import file_hash
from .data import load_scheduling_inputs
from .routes import RouteFactory
from .search import optimize


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def verify_csv_tables(payload,output):
    checked=0
    for sheet in payload['sheets']:
        with (output/'tables'/f"{sheet['name']}.csv").open(encoding='utf-8-sig',newline='') as f:
            actual=list(csv.reader(f))
        expected=[[str(v) if v is not None else '' for v in row] for row in [sheet['headers'],*sheet['rows']]]
        if actual!=expected:raise AssertionError(f"CSV与JSON不一致: {sheet['name']}")
        checked+=1
    for name,result in payload.get('schemes',{}).items():
        for kind in ['sorties','deliveries','legs','phases']:
            with (output/'tables/by_scheme'/name/f'{kind}.csv').open(encoding='utf-8-sig',newline='') as f:
                actual=list(csv.DictReader(f))
            records=result[kind];headers=list(dict.fromkeys(k for row in records for k in row))
            expected=[{k:json.dumps(row[k],ensure_ascii=False) if isinstance(row.get(k),(list,dict)) else str(row[k]) if row.get(k) is not None else '' for k in headers} for row in records]
            if actual!=expected:raise AssertionError(f'对照明细CSV不一致: {name}/{kind}')
            checked+=1
    return {'csv_files_checked':checked,'json_csv_equal':True}


def retain_best_seen(payload):
    """A contrast run may also improve the primary order; do not discard that result."""
    if 'schemes' in payload:
        return
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
    if output.exists() and not (args.resume or args.report_only or args.verify_only):
        if not output.is_relative_to((project/'outputs').resolve()):
            raise ValueError('自动归档仅允许工程outputs内的目录')
        archived=output.with_name(output.name+'_archive_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        output.rename(archived)
    for folder in ['tables','figures','logs']:(output/folder).mkdir(parents=True,exist_ok=True)
    cache=paths['cache'];cache.mkdir(parents=True,exist_ok=True)
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
            cases = [(name,payload['schemes'][name],schedule) for name,schedule in payload['schedules'].items()] if 'schemes' in payload else [(name,payload[name],payload[sk]) for name,sk in [('main','schedule'),('comparison','comparison_schedule')]]
            for name,result,schedule in cases:
                pool={}
                for row in result['sorties']:
                    p=factory.make(row['drone'],row['boxes'],row['visits'])
                    if p is None:raise AssertionError('保存架次违反物理约束')
                    pool[p.id]=p
                rebuilt=validate_schedule(schedule,pool,factory)
                compare(rebuilt,result,name)
            checks={'schemes_recomputed':[name for name,_,_ in cases],'sources_match':True,
                    'verified_utc':datetime.now(timezone.utc).isoformat(),
                    'verification_code_sha256':{str(p):file_hash(p) for p in sorted((project/'src').rglob('*.py'))}}
            from .report import build_sheets
            payload['sheets']=build_sheets(payload,data)
            checks['csv']=verify_csv_tables(payload,output)
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
            total=cfg['optimization']['initial_seconds']+cfg['optimization']['search_seconds']+3*cfg['optimization']['comparison_seconds']+4*cfg['optimization']['final_seconds']
            for k in keys:cfg['optimization'][k]*=args.budget_seconds/total
        hashes={str(p):file_hash(p) for p in data.base.source_paths}
        cfg['optimization']['input_sha256']=hashes
        if args.resume:
            checkpoint=output/'logs/checkpoint.json'
            if not checkpoint.exists():raise ValueError('没有可继续的检查点')
            cfg['optimization']['resume_checkpoint']=str(checkpoint)
        # Preserve Q1 artifacts and the existing paper as well as raw attachments.
        protected=[p for folder in [*sorted((project/'outputs').glob('q1*')),project.parent/'写作'] if folder.is_dir() for p in folder.rglob('*') if p.is_file() and not p.name.startswith('~$')]
        protected_hashes={str(p):file_hash(p) for p in protected}
        code_paths=[*sorted((project/'src').rglob('*.py')),project/'run_q2.py',config_path]
        code_hashes={str(p):file_hash(p) for p in code_paths}
        save(output/'logs/protected_files.json',protected_hashes)
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
                             'code_sha256':code_hashes,
                             'q1_and_paper_unchanged':True,'protected_file_count':len(protected_hashes),'sources_unchanged':True}
        save(output/'tables/results.json',payload)
    if args.solve_only:
        save(output/'logs/solver_trace.json',payload['trace'])
        save(output/'logs/alns_trace.json',payload.get('alns_trace',[]))
        print('计算完成，结果已保存；使用--report-only导出。',flush=True)
        return
    from .report import build_sheets,make_figures,write_report
    retain_best_seen(payload)
    if 'schemes' in payload:
        from .figures import prepare_map
        prepare_map(payload,data,payload['metadata']['config']['physics'],project)
        export_sources=[project/'src/uav_rescue/q2'/name for name in ['report.py','figures.py','narrative.py','pipeline.py']]+[project/'scripts/plot_q2.py',project/'scripts/export_q2.mjs',project/'scripts/export_q1.mjs']
        payload['metadata']['export_code_sha256']={str(p):file_hash(p) for p in export_sources}
    payload['sheets']=build_sheets(payload,data)
    save(output/'tables/results.json',payload)
    for sheet in payload['sheets']:
        with (output/'tables'/f"{sheet['name']}.csv").open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(sheet['headers']);writer.writerows(sheet['rows'])
    for name in ['main','comparison','initial']:save(output/'tables'/f'{name}_manifest.json',payload[name])
    for name,result in payload.get('schemes',{}).items():
        save(output/'tables'/f'{name}_manifest.json',result)
        destination=output/'tables/by_scheme'/name
        destination.mkdir(parents=True,exist_ok=True)
        for kind in ['sorties','deliveries','legs','phases']:
            records=result[kind]
            headers=list(dict.fromkeys(k for row in records for k in row))
            with (destination/f'{kind}.csv').open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
                for row in records:
                    writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    if 'schemes' in payload:
        bundle={k:payload[k] for k in ['main','schemes','orders','nodes','resources','map','alns_trace','metadata']}
        bundle_path=output/'plotting/data/plot_data.json'
        save(bundle_path,bundle)
        save(bundle_path.with_name('manifest.json'),{'plot_data_sha256':file_hash(bundle_path),'input_sha256':payload['metadata']['input_sha256']})
        code_dir=output/'plotting/code';code_dir.mkdir(parents=True,exist_ok=True)
        shutil.copy2(project/'scripts/plot_q2.py',code_dir/'plot_q2.py')
        shutil.copy2(project/'src/uav_rescue/q2/figures.py',code_dir/'q2_figures.py')
        shutil.copy2(paths['font'],bundle_path.parent/'SimSun.ttf')
        (code_dir/'requirements.txt').write_text('\n'.join(f'{name}=={importlib.metadata.version(name)}' for name in ['numpy','matplotlib','Pillow'])+'\n',encoding='utf-8')
        (output/'plotting/README.md').write_text('# 问题二图册重绘\n\n从工程运行 `python scripts/plot_q2.py`。迁移时保留code与data，执行 `python code/plot_q2.py --from-bundle <data/plot_data.json绝对路径> --output <输出绝对路径>`。绘图不读取原始表格或DEM，不运行优化。字体已随数据保存，可用--font替换；--z-exaggeration 1生成真实比例三维图。\n\n所有图的数值以JSON快照为准，对应CSV在tables中；空值不是零。图中地形网格仅用于显示。\n',encoding='utf-8')
    make_figures(payload,paths['font'],output/'figures')
    write_report(payload,output/'问题二结果分析.md')
    if cfg['export']['excel'] and not args.skip_excel:
        export_excel(project,output,cfg['export']['render_previews'])
        payload['excel_verification']=verify_workbook(output/'问题二结果.xlsx',payload['sheets'])
    else:payload['excel_verification']={'performed':False}
    payload['metadata']['last_export_elapsed_s']=time.perf_counter()-started
    csv_checks=verify_csv_tables(payload,output)
    save(output/'logs/csv_consistency.json',csv_checks)
    save(output/'logs/run_manifest.json',payload['metadata'])
    save(output/'logs/solver_trace.json',payload['trace'])
    save(output/'logs/alns_trace.json',payload.get('alns_trace',[]))
    save(output/'logs/validation.json',{'main':payload['main']['verification'],'comparison':payload['comparison']['verification'],
                                      'excel':payload['excel_verification'],'sources_unchanged':payload['metadata']['sources_unchanged'],
                                      'q1_and_paper_unchanged':payload['metadata']['q1_and_paper_unchanged']})
    if 'schemes' in payload:
        save(output/'logs/all_scheme_validation.json',{name:r['verification'] for name,r in payload['schemes'].items()})
    save(output/'tables/results.json',payload)
    print('问题二完成：'+json.dumps(payload['main']['summary'],ensure_ascii=False),flush=True)
