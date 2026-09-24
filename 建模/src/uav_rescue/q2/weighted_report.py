"""Export weighted schedules and their explicit, reproducible preference comparison."""
import csv
import importlib.util
import json
import math
import shutil
from pathlib import Path
from .weighted import METRICS, UNITS, score, reselect_scenarios
from .weighted_run import save

LABELS={'weighted':'加权推荐方案','scenario_tardiness':'及时性权重放大','scenario_makespan':'完工时间权重放大',
        'scenario_energy':'能耗权重放大','scenario_sorties':'架次权重放大',
        'tardiness':'旧词典序：及时性','makespan':'旧词典序：完工时间','energy':'旧词典序：能耗','sorties':'旧词典序：架次'}
SHORT={'tardiness':'及时性W','makespan':'完工时间Cmax','energy':'运输能耗E','sorties':'架次N'}


def sheet(name,headers,rows,widths=None):
    return {'name':name,'headers':headers,'rows':rows,
            'widths':widths or [max(18,min(35,len(h)*2+2)) for h in headers],
            'formats':{str(j):'0.000000' for j in range(len(headers)) if any(isinstance(row[j],float) for row in rows)}}


def export(project,output,payload):
    from .data import load_scheduling_inputs
    from .report import build_sheets
    from .pipeline import export_excel
    from ..q1.report import verify_workbook
    from ..common.data import file_hash
    from .routes import RouteFactory, objective
    from .validate import validate_schedule
    selections=reselect_scenarios(payload)
    old=json.loads(Path(payload['reference_path']).read_text(encoding='utf-8'))
    for p,h in payload['source_sha256'].items():
        if file_hash(Path(p))!=h:raise ValueError('来源改变，禁止新旧输入混用')
    paths=old['metadata']['config']['paths']
    data=load_scheduling_inputs((project/paths['data_root']).resolve(),(project/paths['template']).resolve())
    spec=payload['spec'];new_names=['weighted',*['scenario_'+k for k in METRICS]]
    # Rebuild the new schemes once from raw inputs, without opening any checkpoint.
    factory=RouteFactory(data,old['metadata']['config']['physics']);pool={}
    for name in new_names:
        for row in payload['schemes'][name]['sorties']:
            p=factory.make(row['drone'],row['boxes'],row['visits'])
            if p is None:raise AssertionError('不可行候选')
            pool[p.id]=p
    for name in new_names:
        rebuilt=validate_schedule(payload['schedules'][name],pool,factory)
        if rebuilt!=payload['schemes'][name]:raise AssertionError('独立重算不一致:'+name)
        if objective(payload['schedules'][name],pool,data)!=payload['objective_vectors'][name]:
            raise AssertionError('目标向量与完整方案不一致:'+name)
    compatibility=dict(old,main=payload['main'],primary_scheme='weighted',scheme_labels=LABELS,
                       schemes={k:payload['schemes'][k] for k in new_names},initial=old['main'],
                       provenance={k:payload['weighted_provenance'] if k=='weighted' else 'saved_complete_reassessment:'+selections[k]['source'] for k in new_names},
                       trace=[],pool_size=payload['pool_size'],orders={k:['weighted'] for k in new_names},
                       objective_description='四指标归一化加权，W:Cmax:E:N=3:1.5:1:0.5；31个硬截止均为硬约束。')
    compatibility['trace']=[{'phase':t['source'],'scheme':'weighted','stages':t['stages']} for t in payload['weighted_trace']]
    core=build_sheets(compatibility,data)
    settings=sheet('权重设置',['指标','单位','相对权重','归一化权重α','固定参考下端','固定参考上端','固定尺度'],[
        [SHORT[k],{'tardiness':'系数·秒','makespan':'秒','energy':'kWh','sorties':'架次'}[k],
         spec['weights'][k],spec['alpha'][k],spec['lower'][k]/UNITS[k],spec['upper'][k]/UNITS[k],spec['span'][k]/UNITS[k]] for k in METRICS])
    settings['cell_formulas']={f'D{i+2}':f'=C{i+2}/SUM($C$2:$C$5)' for i in range(4)}
    settings['formats']['2']='0.0'
    settings['formats']['3']='0.00%'
    scores=sheet('主权重统一评分',['方案','W系数·秒','Cmax秒','目标能耗kWh','架次','归一化W','归一化Cmax','归一化E','归一化N','主权重F'],[
        [LABELS[k],*[payload['objective_vectors'][k][m]/UNITS[m] for m in METRICS],
         *[(payload['objective_vectors'][k][m]-spec['lower'][m])/spec['span'][m] for m in METRICS],
         score(payload['objective_vectors'][k],spec)] for k in payload['schemes']],
        [32,24,22,24,14,22,24,22,22,22])
    scores['cell_formulas']={}
    scores['formats']['4']='0'
    for i in range(2,2+len(scores['rows'])):
        for j,(raw,norm) in enumerate(zip('BCDE','FGHI'),2):
            scores['cell_formulas'][f'{norm}{i}']=f'=({raw}{i}-\'权重设置\'!$E${j})/\'权重设置\'!$G${j}'
        scores['cell_formulas'][f'J{i}']='='+ '+'.join(f"{col}{i}*'权重设置'!$D${j}" for j,col in enumerate('FGHI',2))
    scenario=sheet('权重情景定义',['情景','W相对权重','Cmax相对权重','E相对权重','N相对权重','扰动次数','精修秒数','跨情景回评采用来源','独立终点情景F','回评后情景F'],[
        [LABELS['scenario_'+k],*[s['weights'][m] for m in METRICS],payload['config']['scenario_iterations'],payload['config']['scenario_refine_seconds'],
         LABELS[selections['scenario_'+k]['source']],selections['scenario_'+k]['search_endpoint_score'],selections['scenario_'+k]['selected_score']]
        for k,s in payload['scenario_specs'].items()])
    # Keep distinct physical schedules and one shared scoring build.
    core=[s for s in core if s['name'] not in ['目标优先级对比','方案来源']]
    for s in core:
        if s['name']=='口径与来源':
            s['rows'].append(['权重修改','权重设置可对已保存方案重新评分；运输安排不会自动重算。重新优化请运行run_q2_weighted.py。'])
            s['rows'].append(['情景搜索',f"四组仅放大一个权重{payload['config']['scenario_multiplier']:g}倍，其余不变。每组{payload['config']['scenario_iterations']}次快速ALNS与列表调度，末尾CP-SAT精修。"])
            s['rows'].append(['情景间回评','按各情景自身权重，从9个已保存完整方案选择最好；原始独立终点另存，不新增求解，不从仅有目标向量的云图记录恢复方案。'])
            for row in s['rows']:
                if row[0]=='最优性范围':row[1]='分数为固定尺度加权和。CP-SAT状态只针对该次候选池及数值精度，未证明完整问题全局最优。'
                if row[0]=='刷新方式':row[1]='权重设置只重算已保存方案评分；重新优化运行run_q2_weighted.py，仅重导出添加--report-only。'
    payload['sheets']=[scores,settings,scenario,*core]
    for s in payload['sheets']:
        (output/'tables').mkdir(parents=True,exist_ok=True)
        with (output/'tables'/f"{s['name']}.csv").open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(s['headers']);w.writerows(s['rows'])
    for name,result in payload['schemes'].items():
        destination=output/'tables/by_scheme'/name;destination.mkdir(parents=True,exist_ok=True)
        for kind in ['sorties','deliveries','legs','phases']:
            records=result[kind];headers=list(dict.fromkeys(k for row in records for k in row))
            with (destination/(kind+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=headers);w.writeheader()
                for row in records:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    with (output/'tables/可行解云.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['来源','W系数秒','Cmax秒','能耗kWh','架次','主权重F'])
        for row in payload['cloud']:w.writerow([row['source'],*[row['vector'][m]/UNITS[m] for m in METRICS],score(row['vector'],spec)])
    save(output/'tables/scenario_search_endpoints.json',payload['scenario_search_endpoints'])
    save(output/'logs/scenario_reassessment.json',selections)
    payload['export_code_sha256']={str(p):file_hash(p) for p in [project/'src/uav_rescue/q2/weighted_report.py',project/'src/uav_rescue/q2/weighted.py',project/'scripts/export_q1.mjs']}
    save(output/'tables/results.json',payload)
    export_excel(project,output,True)
    validation=verify_workbook(output/'问题二结果.xlsx',payload['sheets'])
    save(output/'logs/export_validation.json',{'xlsx':validation,'new_schemes_recomputed':new_names,
                                              'sources_unchanged':True,'checkpoint_read':False,
                                              'scenario_reassessment':selections})
    module_path=project/'scripts/plot_q2_weighted.py'
    if module_path.exists():
        module=importlib.util.spec_from_file_location('q2_weighted_plot',module_path)
        renderer=importlib.util.module_from_spec(module);module.loader.exec_module(renderer)
        font=(project/paths['font']).resolve()
        renderer.draw(payload,font,output/'figures')
        plotdata={k:payload[k] for k in ['spec','scenario_specs','objective_vectors','cloud','config','scenario_reselected_sources']}
        save(output/'plotting/data/plot_data.json',plotdata)
        save(output/'plotting/data/manifest.json',{'sha256':file_hash(output/'plotting/data/plot_data.json')})
        (output/'plotting/code').mkdir(parents=True,exist_ok=True)
        shutil.copy2(module_path,output/'plotting/code/plot_q2_weighted.py')
        shutil.copy2(font,output/'plotting/data/SimSun.ttf')
        (output/'plotting/README.md').write_text('绘图代码与data快照可单独迁移。命令：python code/plot_q2_weighted.py --data <data/plot_data.json的绝对路径> --font <data/SimSun.ttf的绝对路径> --output <输出目录>。不读取原始附件，不运行优化。依赖：numpy、matplotlib。',encoding='utf-8')
    write_report(payload,output/'四指标加权权衡分析.md')


def write_report(p,path):
    spec=p['spec'];main=p['main']['summary'];vectors=p['objective_vectors']
    rows=[]
    for name in p['schemes']:
        s=p['schemes'][name]['summary'];f=score(vectors[name],spec)
        rows.append(f"| {LABELS[name]} | {s['weighted_tardiness_s']:.3f} | {s['makespan_s']/3600:.6f} | {s['energy_kwh']:.6f} | {s['sorties']} | {s['on_time_boxes']}/80 | {f:.9f} |")
    ranges='\n'.join(f"| {SHORT[k]} | {spec['lower'][k]/UNITS[k]:.6f} | {spec['upper'][k]/UNITS[k]:.6f} | {spec['span'][k]/UNITS[k]:.6f} | {spec['alpha'][k]:.6%} |" for k in METRICS)
    iterations={m:sum(h['phase']==m for h in p['weighted_history']) for m in METRICS}
    late=[r for r in p['main']['deliveries'] if r['lateness_s']>1e-9]
    late_text='；'.join(f"{r['box']}迟到{r['lateness_s']/60:.3f}分钟" for r in late) or '全部货箱均满足期望时刻'
    starting=score(p['weighted_start_vector'],spec);ending=score(vectors['weighted'],spec)
    selection_rows='\n'.join(f"| {LABELS[k]} | {LABELS[v['source']]} | {v['search_endpoint_score']:.9f} | {v['selected_score']:.9f} |" for k,v in p['scenario_reselected_sources'].items())
    text=f'''# 四指标加权权衡与推荐方案

## 1 本次采用的决策方式

用户确认 W:Cmax:E:N=3:1.5:1:0.5，对应归一化权重50%、25%、16.6667%、8.3333%。目标为F=Σα_j(f_j−l_j)/s_j，越小越好。该权重是显式偏好；货箱优先系数w_b仅用于W，不等同于四目标权重。硬截止始终为约束。

归一化范围在新搜索开始前取旧四种词典序方案的最小/最大值并冻结；新解超出范围不裁剪。它们是已见方案比较尺度，不是全局最优/最差界限。

| 指标 | 固定下端 | 固定上端 | 固定跨度 | 主权重 |
|---|---:|---:|---:|---:|
{ranges}

## 2 计算结果与权衡

| 方案 | W/系数·秒 | 最终返航/h | 能耗/kWh | 架次 | 按期箱数 | 主权重统一F |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

推荐主权重评分最低的完整可行方案。当前采用来源为`{p['weighted_provenance']}`，F从旧方案中最好值{starting:.9f}降至{ending:.9f}。这次是实际加权优化，不是仅对旧方案重新排名。

主方案{main['sorties']}架次、{main['multi_stop_sorties']}个多点架次，A/B/C={main['type_counts'].get('A',0)}/{main['type_counts'].get('B',0)}/{main['type_counts'].get('C',0)}；使用{main['units_used']}架无人机、{main['batteries_used']}组电池。全部80箱完整交付，31个硬截止均满足。最低返航SOC={main['min_soc']:.6%}。累计作业时间{main['operation_s']/3600:.6f}小时，区别于最后返航时间。

软时限情况：{late_text}。正权加权允许用一定软迟到换取其他指标改善，不能把50%及时性权重解释为保证W=0。

## 3 权重情景与统一回评

从同一加权主搜索解分别启动四个情景，只将一个指标原相对权重乘{p['config']['scenario_multiplier']:g}，其余不变，再归一化。实际扰动次数为{iterations}。快速ALNS从现有航线池重组批次并列表调度；不在每一轮调用CP-SAT，每个情景末尾用{p['config']['scenario_refine_seconds']:g}秒CP-SAT精修。全程找到的可行方案均按原主权重回评，独立保留最好完整方案；不只比较四个情景终点。

搜索结束后，按各情景自己的权重交叉比较9个已保存完整方案，选出该有限集合内最好的安排。原独立搜索终点连同全部时序保存在`tables/scenario_search_endpoints.json`，回评记录见`logs/scenario_reassessment.json`。此步骤没有新增优化，也不从只有目标向量的云图记录恢复方案。

| 权重情景 | 回评采用来源 | 原独立终点的情景F | 回评后的情景F |
|---|---|---:|---:|
{selection_rows}

若不同权重情景采用同一完整方案，图中如实显示重合曲线与共点，不人为移动数值。旧词典序方案单独标明，不能称为这四个权重情景。情景的限时结果也不能当成严格单指标最优。新计算用时{p['search_elapsed_s']/60:.3f}分钟；取整综合评分对未取整归一化分数的附加误差小于1e-6，本次采用方案差值{p['quantization_error']:.3g}。航段能耗目标还存在原1e-9kWh量化，物理约束仍按未量化值核验。

## 4 图与数据

![四指标加权权衡](figures/09_四指标加权权衡.png)

左图为主方案与四个权重情景的平行坐标，右图为实际保存的已接受可行目标向量，保留重复接受记录，另统计不同四指标向量数。点数不等同于不同路线数；散点不是完整Pareto前沿。未保留完整时序的旧日志点不作为新增推荐方案。

`问题二结果.xlsx`包含推荐运输与逐箱安排、机电资源记录、权重设置及统一评分。修改权重只会对已保存方案重评分，不会在Excel中自动重新调度。完整JSON、逐方案CSV、真实解云CSV、独立绘图代码和数据一并保留。所有新方案已从原始输入独立重算。

复现：`python run_q2_weighted.py --config configs/q2_weighted.toml`；仅重导出：`python run_q2_weighted.py --report-only`。未读取或核对检查点。

当前结论只适用于已审计物理解释、固定归一化、指定权重及候选搜索范围。权重与尺度共同决定交换关系，不宣称原问题全局最优。
'''
    path.write_text(text,encoding='utf-8')
