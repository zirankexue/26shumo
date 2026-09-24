"""Independently rebuild and export numerical results; no plots or workbooks."""
from pathlib import Path
import sys
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
import run_q2
import csv
from dataclasses import asdict
import json
import math
from uav_rescue.q2.data import load_scheduling_inputs
from uav_rescue.q2.routes import RouteFactory,objective
from uav_rescue.q2.solver import assign_resources
from uav_rescue.q2.validate import validate_schedule
from uav_rescue.q2.weighted import score
from uav_rescue.common.data import file_hash


def dump_csv(path,rows):
    headers=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})


def main():
    output=PROJECT/'outputs/q2_improved';tables=output/'tables';tables.mkdir(exist_ok=True)
    base_path=PROJECT/'outputs/q2_weighted/tables/results.json'
    old=json.loads(base_path.read_text(encoding='utf-8'))
    raw=json.loads(Path(old['reference_path']).read_text(encoding='utf-8'))
    paths=raw['metadata']['config']['paths']
    data=load_scheduling_inputs((PROJECT/paths['data_root']).resolve(),(PROJECT/paths['template']).resolve())
    factory=RouteFactory(data,raw['metadata']['config']['physics'])
    experiment=json.loads((output/'pool_experiment.json').read_text(encoding='utf-8'))
    dynamic=json.loads((output/'dynamic_experiment.json').read_text(encoding='utf-8'))
    assert experiment['spec']==dynamic['spec']==old['spec']
    candidates=[('原加权推荐',old['main'],old['schedules']['weighted'])]
    candidates.extend((name,item['result'],item['schedule']) for name,item in experiment['snapshots'].items())
    candidates.append(('动态邻域补充搜索',dynamic['main'],dynamic['schedule']))
    pool={}
    for _,result,_ in candidates:
        for row in result['sorties']:
            p=factory.make(row['drone'],row['boxes'],row['visits'])
            if p is None or p.id!=row['candidate']:raise AssertionError('保存航段不符合原始物理口径')
            pool[p.id]=p
    evaluated=[]
    for name,result,schedule in candidates:
        rebuilt=validate_schedule(assign_resources(schedule,pool,data),pool,factory)
        if rebuilt!=result:raise AssertionError('完整独立重算不一致:'+name)
        vector=objective(schedule,pool,data)
        evaluated.append((score(vector,old['spec']),name,result,schedule,vector))
    f,name,result,schedule,vector=min(evaluated,key=lambda row:row[0])
    wsum=sum(t.priority for t in data.timing.values())
    mean_delivery=lambda r:math.fsum(b['priority']*b['completion_s'] for b in r['deliveries'])/wsum
    mean_late=lambda r:r['summary']['weighted_tardiness_s']/wsum
    summary=result['summary'];baseline=old['main']['summary']
    stages=[]
    for value,label,r,_,v in evaluated:
        s=r['summary']
        stages.append({'阶段':label,'加权迟到系数秒':s['weighted_tardiness_s'],'加权平均迟到秒':mean_late(r),
                       '加权平均交付秒':mean_delivery(r),'最终返航秒':s['makespan_s'],'最终返航小时':s['makespan_s']/3600,
                       '总能耗kWh':s['energy_kwh'],'架次':s['sorties'],'按期箱数':s['on_time_boxes'],
                       '主评分F':value,'硬截止违反数':r['verification']['hard_deadline_violations']})
    output_hashes={str(base_path):file_hash(base_path),**experiment['source_sha256']}
    if any(file_hash(Path(p))!=h for p,h in output_hashes.items()):raise AssertionError('原始/旧输出被改变')
    final={'main':result,'schedule':schedule,'objective_vector':vector,'score':f,'source':name,'spec':old['spec'],
           'physics':raw['metadata']['config']['physics'],'weighted_mean_tardiness_s':mean_late(result),
           'weighted_mean_delivery_s':mean_delivery(result),'sum_box_priorities':wsum,
           'candidates':[asdict(pool[a['candidate']]) for a in schedule],
           'experiments':{'compact_pool_elapsed_s':experiment['elapsed_s'],'dynamic_elapsed_s':dynamic['seconds'],
                          'dynamic_improved_compact_result':dynamic['score']<experiment['best_score']-1e-12},
           'source_sha256':output_hashes,'input_sha256':raw['metadata']['input_sha256'],'checkpoint_read':False,
           'stages':stages,'code_sha256':{str(p):file_hash(p) for p in [Path(__file__),PROJECT/'scripts/improve_q2_pool.py',
                                       PROJECT/'run_q2_improve.py',PROJECT/'src/uav_rescue/q2/fast_schedule.py',
                                       PROJECT/'src/uav_rescue/q2/dynamic_search.py']},
           'optimality_scope':'independently verified best feasible result found; no original-problem global optimality claim'}
    (tables/'final_result.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
    for kind in ['sorties','deliveries','legs','phases']:dump_csv(tables/(kind+'.csv'),result[kind])
    dump_csv(tables/'阶段结果比较.csv',stages)
    dump_csv(tables/'无人机与电池使用.csv',[
        {'架次':r['sortie'],'无人机':r['unit'],'机型':r['drone'],'电池':r['battery'],
         '准备开始秒':r['start_s'],'离地秒':r['takeoff_s'],'返航释放无人机秒':r['return_s'],
         '返航SOC':r['soc'],'充满释放电池秒':r['charge_end_s']} for r in result['sorties']])
    compare=[{'方案':'原加权推荐','加权平均迟到秒':mean_late(old['main']),'加权平均交付秒':mean_delivery(old['main']),
              '最后返航小时':baseline['makespan_s']/3600,'能耗kWh':baseline['energy_kwh'],'架次':baseline['sorties']},
             {'方案':'截图推荐（仅转录摘要）','加权平均迟到秒':235.508,'加权平均交付秒':3001.488,
              '最后返航小时':1.867,'能耗kWh':70.295,'架次':22},
             {'方案':'本次改进','加权平均迟到秒':mean_late(result),'加权平均交付秒':mean_delivery(result),
              '最后返航小时':summary['makespan_s']/3600,'能耗kWh':summary['energy_kwh'],'架次':summary['sorties']}]
    dump_csv(tables/'截图数值对照.csv',compare)
    stage_rows='\n'.join(f"| {r['阶段']} | {r['加权迟到系数秒']:.3f} | {r['最终返航小时']:.6f} | {r['总能耗kWh']:.6f} | {r['架次']} | {r['主评分F']:.9f} |" for r in stages)
    comparison_rows='\n'.join(f"| {r['方案']} | {r['加权平均迟到秒']:.6f} | {r['加权平均交付秒']:.3f} | {r['最后返航小时']:.6f} | {r['能耗kWh']:.6f} | {r['架次']} |" for r in compare)
    text=f'''# 问题二算法差距与本次改进结果

结论：上一版搜索不充分，本次在相同物理模型、硬约束、主权重3:1.5:1:0.5和固定归一化尺度下得到更优的完整可行方案。未绘图，也未读取检查点；原正式结果保留，改进结果单独保存。

## 1 数值对照

| 方案 | 加权平均迟到/s | 加权平均交付/s | 最后返航/h | 能耗/kWh | 架次 |
|---|---:|---:|---:|---:|---:|
{comparison_rows}

本次80箱均按期，31个硬截止全部满足，最低返航SOC为{summary['min_soc']:.6%}；A/B/C架次为{summary['type_counts'].get('A',0)}/{summary['type_counts'].get('B',0)}/{summary['type_counts'].get('C',0)}，多点架次{summary['multi_stop_sorties']}个，使用8架无人机和14组电池，逐资源占用无冲突。累计作业时间{summary['operation_s']/3600:.6f}h，不等于最终返航时间。

相对原推荐少{baseline['sorties']-summary['sorties']}架次，提前{(baseline['makespan_s']-summary['makespan_s'])/60:.6f}分钟完成，节能{100*(baseline['energy_kwh']-summary['energy_kwh'])/baseline['energy_kwh']:.6f}%。相对截图显示的推荐摘要，少1架次、最终返航早约{(1.867*3600-summary['makespan_s'])/60:.3f}分钟、能耗低约{100*(70.295-summary['energy_kwh'])/70.295:.3f}%。截图数值已四舍五入，比较精度受其限制。

## 2 差距原因

1. **指标及偏好口径不同。** 我方目标W为加权迟到总量。原80箱优先系数之和为956，所以加权平均迟到=W/956；仅除以这个常数、同时一致变换归一化尺度，不会改变优化排序，不能把单位差异本身当作算法差距。截图的T四行与“(加权平均迟到+0.1×加权平均交付)/3600”吻合，但缺少原指标函数，尚不能确认它的定义。若含平均交付项，目标偏好就与我方不同。截图四个对照权重也与我方逐项放大10倍不同，F=5.203不能和我方F直接比大小。
2. **上一版新增路线搜索不足。** 主加权搜索新增候选只有26轮，之后4×3000快速轮次在1500条固定候选中重组；轮次多并不等于探索了足够多的新组批。候选生成还沿用轮换词典序代理，对主加权目标的针对性不足。
3. **原快速排程限制偏强。** 所有含硬截止货箱的架次优先于软箱架次，交箱顺序采用固定规则，主要在最终CP阶段优化；局部CP常只有15秒，多次结束为FEASIBLE。原结果是当时找到的可行解，不能视为充分优化或全局最优。

## 3 实验如何定位改进来源

| 阶段 | W/系数·秒 | 最后返航/h | 能耗/kWh | 架次 | 同尺度F |
|---|---:|---:|---:|---:|---:|
{stage_rows}

先固定原24组仅重排开始时刻和箱序；再系统生成合批、移箱、交换、换机型和访问顺序变体，保留移箱两侧可配套重组的候选，以约480条有针对性的候选交由CP-SAT联合选择及排程。累计构造{len(experiment['candidates'])}条物理可行候选，四轮增强各75秒，机型资源始终保持原题库存。候选池不是越大越好，候选质量和联合排程的搜索时间都影响结果。

紧凑池实验实际{experiment['elapsed_s']:.3f}秒；后续动态邻域和逐站精确箱序补充搜索实际{dynamic['seconds']:.3f}秒，本次未进一步改善紧凑池最好解。每站箱序用有限线性指派优化，其精确性仅限于给定路线和开始时刻；整体仍为启发式搜索。

最终采用`{name}`，主评分F={f:.12f}。负值来自固定历史min-max参考范围被突破，不代表负能耗或负时间，也没有重新调整归一化端点来美化结果。所有阶段CP状态为FEASIBLE；尚有优化空间，但本次没有证明全局最优。

## 4 可比性与结果文件

截图只有指标摘要，尚缺其具体航线、逐箱时刻、能耗和机电资源约束定义，无法独立验证对方完整方案；以上只比较可读数值，不据此宣称算法本身优于对方。原方案已具有更低的平均迟到，不能仅按架次或能耗判定全面落后。

`tables/final_result.json`保存最终完整方案、固定权重及尺度、候选定义和验证明细。`sorties.csv`、`deliveries.csv`、`legs.csv`及`无人机与电池使用.csv`可直接查看路线、逐箱安排和资源使用。`阶段结果比较.csv`与`截图数值对照.csv`保留数值证据。

复现（建模目录）：

```powershell
python scripts/improve_q2_pool.py
python run_q2_improve.py --seconds 150
python scripts/export_q2_improvement.py
```

CP使用2个线程和限时预算，随机种子及参数已保存；不同硬件/调度下重跑可能得到不同的启发式解。最后一条命令只从完整实验结果重新核验和导出数值，不再次优化。此次没有替换旧图，也没有生成新图或Excel。
'''
    (output/'优化结果与差距分析.md').write_text(text,encoding='utf-8')
    verification={'passed':True,'independently_recomputed_complete_schedules':len(evaluated),
                  'related_tests_passed':22,'checkpoint_read':False,'visualizations_generated':False,
                  'old_sources_unchanged':True,'verification':result['verification'],
                  'files_sha256':{str(p.relative_to(output)):file_hash(p) for p in tables.iterdir() if p.is_file()}}
    (output/'final_verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'chosen':name,'score':f,'summary':summary,'mean_delivery_s':mean_delivery(result),
                      'tables':str(tables)},ensure_ascii=False))


if __name__=='__main__':main()
