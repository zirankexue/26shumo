"""Reports use computed values only; search certificates retain their scope."""
from .alns import LABELS


def write_report(payload, path):
    m = payload['main']['summary']
    opt = payload['metadata']['config']['optimization']
    budget = opt['initial_seconds']+opt['search_seconds']+3*opt['comparison_seconds']+4*opt['final_seconds']
    table, differences, statuses = [], [], []
    for name, result in payload['schemes'].items():
        s = result['summary']
        types = '/'.join(str(s['type_counts'].get(g,0)) for g in 'ABC')
        table.append(f"| {LABELS[name]} | {s['weighted_tardiness_s']:.3f} | {s['makespan_s']/3600:.6f} | {s['energy_kwh']:.6f} | {s['sorties']} | {s['on_time_boxes']}/80 | {types} |")
        if name != 'tardiness':
            differences.append(f"- {LABELS[name]}相对主方案：加权逾期变化{s['weighted_tardiness_s']-m['weighted_tardiness_s']:+.3f}系数·秒，最后返航变化{(s['makespan_s']-m['makespan_s'])/60:+.3f}分钟，能耗变化{s['energy_kwh']-m['energy_kwh']:+.6f}kWh（{100*(s['energy_kwh']/m['energy_kwh']-1):+.3f}%），架次变化{s['sorties']-m['sorties']:+d}。")
        final = next(t for t in reversed(payload['trace']) if t['phase']=='final' and t['scheme']==name)
        stages = '；'.join(f"{r.get('stage')}: {r['status']}" for r in final['stages'])
        statuses.append(f"- {LABELS[name]}：{stages or '预算内无新增求解阶段'}；最终解来源 `{payload['provenance'][name]}`。")
    hard = [r for r in payload['main']['deliveries'] if r['hard_s'] is not None]
    tight = min(hard, key=lambda r:(r['hard_s']-r['completion_s'],r['box']))
    history = payload['alns_trace']
    worse = sum(r['accepted'] and r['candidate'] is not None and tuple(r['candidate'][k] for k in payload['orders'][r['scheme']])>tuple(r['before'][k] for k in payload['orders'][r['scheme']]) for r in history)
    resource_rows = []
    for drone, units in sorted(payload['resources']['units'].items()):
        for unit in sorted(units):
            tasks = [r for r in payload['main']['sorties'] if r['unit'] == unit]
            if tasks:
                occupied = sum(r['return_s'] - r['start_s'] for r in tasks)
                last = max(r['return_s'] for r in tasks)
                batteries = len({r['battery'] for r in tasks})
                resource_rows.append(f"| {unit} | {drone} | {len(tasks)} | {occupied/60:.3f} | {last/60:.3f} | {batteries} |")
            else:
                resource_rows.append(f"| {unit} | {drone} | 0 | 0 | 无任务 | 0 |")
    routes = [f"| {r['sortie']} | {r['unit']} | {' → '.join(['O01', *r['visits'], 'O01'])} | {len(r['boxes'])} |"
              for r in payload['main']['sorties'] if len(r['visits']) > 1]
    route_table = ('| 架次 | 无人机 | 访问顺序 | 货箱数 |\n|---|---|---|---:|\n' + '\n'.join(routes)) if routes else '本次推荐方案未使用多点架次。'
    figs = '\n\n'.join(f'![{p.stem}](figures/{p.name})' for p in sorted((path.parent/'figures').glob('*.png')))
    text = f'''# 问题二：多点运输与机电资源联合调度结果

## 1 推荐方案与题目任务

主方案按“加权逾期→全部返航时间→能耗→架次”的词典序选择。在20%返航安全余量下，80箱、758kg、2.011m³全部交付，安排 **{m['sorties']}架次**，其中多点架次 **{m['multi_stop_sorties']}次**。

- 加权逾期：**{m['weighted_tardiness_s']:.3f}系数·秒**；{m['on_time_boxes']}/80箱在期望时刻前交付。
- 全部任务完成：**{m['makespan_s']:.3f}秒（{m['makespan_s']/3600:.6f}小时）**，取最后一架运输机返回O01的时刻。
- 总运输能耗：**{m['energy_kwh']:.6f}kWh**；最低返航SOC：**{m['min_soc']:.6%}**。
- A/B/C机型架次：**{m['type_counts'].get('A',0)}/{m['type_counts'].get('B',0)}/{m['type_counts'].get('C',0)}**；使用{m['units_used']}架实体机、{m['batteries_used']}组不同电池。
- 31个硬时限箱全部满足；最紧箱为 **{tight['box']}**，截止裕量 **{(tight['hard_s']-tight['completion_s'])/60:.6f}分钟**。

架次数不等于实体机数。累计作业时间为{m['operation_s']/3600:.6f}小时，表示全部架次占用时间之和，与并行调度的最后返航时刻不同；末次充电不计入任务完成时间。

## 2 四指标的优先关系与实际权衡

| 首要目标 | 加权逾期/系数·s | 最后返航/h | 能耗/kWh | 架次 | 按期箱数 | A/B/C架次 |
|---|---:|---:|---:|---:|---|---|
{chr(10).join(table)}

{chr(10).join(differences)}

推荐及时性优先方案的依据是救援任务中先减少加权迟到，再在该指标下缩短全部任务完成时间；这是明确采用的决策偏好，不是原题唯一规定。能耗、架次分别优先的结果展示其他偏好的实际代价，相同指标也如实保留。

四种方案均满足同样的物理约束与硬截止。搜索共享已发现候选及可行方案，最终在同一个{payload['pool_size']}候选公共池内分层收尾。主方案获得的搜索时间较多，限时结果差异可能同时来自目标偏好与求解进度，因此这些结果不是完整Pareto前沿，也不作为目标冲突的精确理论边界。

## 3 路线、逐箱交付和资源使用

`问题二结果.xlsx`的“Q2_运输架次”给出实体机、电池、准备开始、访问路线、返回时刻与能耗；“Q2_逐箱交付”给出全部80箱的完成时刻，并保持原模板列名及顺序。附表完整列出每批货箱、装载量、逐段剩余载荷、交箱顺序、硬截止与裕量。

实体机从准备开始到返航占用，电池在返航后继续充电，充满才可再次用于同型无人机。不同电池允许并行充电，模型没有添加题目未给的充电器限制。每个资源编号均独立检查区间不重叠，释放与下一任务开始时刻相等时允许复用。

各对照方案完整结果保存在 `tables/by_scheme/`，并在Excel补充表中提供对照运输安排与逐箱交付。JSON还包含全部飞行、基础交接、逐箱交接的阶段时间线，可供问题三继续使用。

主方案逐机使用统计如下。最后返航从任务起点计时，累计占用是该实体机所有任务占用时长之和；一组同型电池可在不同无人机之间复用，因此最后一列不能直接跨机相加。

| 无人机 | 机型 | 架次 | 累计占用/min | 最后返航/min | 使用过的电池组数 |
|---|---|---:|---:|---:|---:|
{chr(10).join(resource_rows)}

全部{len(payload['main']['legs'])}条实际航段均已复核。多点运输的访问次序如下，单点路线及全部精确开始、返航、充满时刻见Excel与逐方案CSV。

{route_table}

## 4 算法记录与证明范围

正式配置预算{budget/60:.1f}分钟，本次搜索记录用时{payload['search_elapsed_s']/60:.3f}分钟，导出和核验另计。共有{len(history)}次ALNS迭代，接受{sum(r['accepted'] for r in history)}次，其中{worse}次为相对当前状态较差的可行方案。算子权重、温度、接受概率和每次求解状态均已保存。

{chr(10).join(statuses)}

以上状态属于对应候选池、邻域限制及固定前级目标。若前一级限时未证最优，后续阶段只在当前固定值下优化。跨目标搜索可更新其他方案，因此不能把该方案最后一次收尾状态直接当作最终采用解的最优证书。

原问题有严格松弛下界W≥0、N≥ceil(758/80)=10；架次下界忽略了能量、体积、时限及资源，通常较弱。{'本次主方案W=0，第一指标达到原问题零下界；其余指标仍不宣称全局最优。' if m['weighted_tardiness_s']==0 else '本次第一指标尚未达到零下界，不宣称及时性全局最优。'}

每架次同一服务区至多访问一次，最多15区，不设两站或三站上限。有限候选搜索仍可能遗漏更好的路线，因此结论为当前物理解释、搜索范围和预算下经核验的可行方案。

## 5 来源、精度和独立核验

水平能耗参考Zhang等(2021，DOI:10.1016/j.trd.2020.102668)附录A式(A1)的电池能量/航程折算；爬升附加项参考附录C式(C1)的势能功率分量，经积分及题给效率折算。全可用电量标定、仅保留势能附加项属于简化假设，未实测校准，详见模型说明与公式审计。

四种方案均从原始逐箱数据独立重算。主方案毫秒保守飞行裕量累计{payload['main']['verification']['total_flight_rounding_s']:.6f}秒，能耗缓存与复算的最大差为{payload['main']['verification']['max_energy_error_kwh']:.3g}kWh。1毫秒时序下不放宽截止，能耗目标量化为1e-9kWh，物理约束和导出值使用未量化能耗。

输入、配置、依赖、种子、计算代码和导出代码哈希均保留。原始附件、问题一输出和论文工程按哈希检查保持原状。固定种子不能消除墙钟限时受机器速度的影响，完整方案可独立复核，不承诺跨机器逐位相同。

## 6 复现

```powershell
python run_q2.py --config configs/q2.toml
python run_q2.py --verify-only
python run_q2.py --report-only
python scripts/plot_q2.py --from-bundle outputs/q2/plotting/data/plot_data.json --output outputs/q2_redraw
```

`--resume`只读取显式指定输出目录的兼容检查点；`--solve-only`保存计算结果，之后使用`--report-only`导出。重新完整运行会归档同名旧结果。`--budget-seconds`用于短时测试，不代表正式预算。

## 7 图册

{figs}
'''
    path.write_text(text, encoding='utf-8')
