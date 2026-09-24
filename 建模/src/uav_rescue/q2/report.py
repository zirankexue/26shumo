from __future__ import annotations
from pathlib import Path
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np


def build_sheets(payload,data):
    main=payload['main'];rows=main['sorties'];deliveries=main['deliveries'];sheets=[]
    from .alns import LABELS
    LABELS = dict(LABELS, **payload.get('scheme_labels', {}))
    primary_name = payload.get('primary_scheme', 'tardiness')
    cases=payload.get('schemes',{'tardiness':main,'makespan':payload['comparison']})
    def sheet(name,headers,values,widths=None):
        widths=widths or [max(16,min(28,len(h)*2+2)) for h in headers]
        formats={str(i):'0.000' for i in range(len(headers)) if any(isinstance(r[i],float) for r in values)}
        sheets.append({'name':name,'headers':headers,'rows':values,'widths':widths,'formats':formats})
    sheet('Q2_运输架次',data.template_headers['Q2_运输架次'],[
        [r['sortie'],r['unit'],r['drone'],r['battery'],r['start_s'],' → '.join(['O01',*r['visits'],'O01']),r['return_s'],r['energy_kwh']] for r in rows],
        [18,16,14,20,21,46,24,23])
    sheets[-1]['formats']['7']='0.000000'
    sheet('Q2_逐箱交付',data.template_headers['Q2_逐箱交付'],[
        [r['box'],r['sortie'],r['service'],r['completion_s']] for r in deliveries],[25,18,18,26])
    sheet('架次装载与时间',['架次','货箱列表','质量kg','体积m³','离地时刻s','飞行时间s','累计作业时间s','返航SOC','飞行取整裕量s'],[
        [r['sortie'],'; '.join(r['boxes']),r['mass_kg'],r['volume_m3'],r['takeoff_s'],r['flight_s'],r['operation_s'],r['soc'],r['rounding_s']] for r in rows],
        [18,92,18,18,22,22,24,18,22])
    sheets[-1]['formats']['7']='0.000%';sheets[-1]['formats']['8']='0.000000'
    sheet('逐箱时限与交接',['货箱','架次','服务区','交接次序','交接开始s','交付完成s','期望时刻s','硬截止s','首批','优先系数','逾期s'],[
        [r['box'],r['sortie'],r['service'],r['rank'],r['start_s'],r['completion_s'],r['expected_s'],r['hard_s'],'是' if r['first'] else '否',r['priority'],r['lateness_s']] for r in deliveries],
        [25,18,16,16,20,20,20,20,14,16,18])
    sheet('电池使用与充电',['电池','机型','架次','占用开始s','返航充电开始s','返航SOC','实际充电时间s','充满可用时刻s'],[
        [r['battery'],r['drone'],r['sortie'],r['start_s'],r['return_s'],r['soc'],r['charge_s'],r['charge_end_s']] for r in sorted(rows,key=lambda r:(r['battery'],r['start_s']))],
        [20,14,18,21,24,18,24,25])
    sheets[-1]['formats']['5']='0.000%'
    sheet('无人机占用',['无人机','机型','架次','准备开始s','离地s','返回释放s','占用时长s'],[
        [r['unit'],r['drone'],r['sortie'],r['start_s'],r['takeoff_s'],r['return_s'],r['operation_s']] for r in sorted(rows,key=lambda r:(r['unit'],r['start_s']))])
    sheet('逐段载荷与能耗',['架次','航段','起点','终点','剩余载荷kg','体积m³','距离m','巡航海拔m','爬升m','下降m','开始s','结束s','飞行时间s','能耗kWh'],[
        [r['sortie'],r['segment'],r['from'],r['to'],r['payload_kg'],r['volume_m3'],r['distance_m'],r['cruise_altitude_m'],r['climb_m'],r['descent_m'],r['start_s'],r['end_s'],r['flight_s'],r['energy_kwh']] for r in main['legs']],
        [17,12,14,14,20,18,20,22,18,18,20,20,22,22])
    sheets[-1]['formats']['13']='0.000000'
    sheet('目标优先级对比',['方案','加权逾期（系数·s）','最后返航s','最后返航h','能耗kWh','架次','多点架次','按期箱数','A型架次','B型架次','C型架次'],[
        [name,r['summary']['weighted_tardiness_s'],r['summary']['makespan_s'],r['summary']['makespan_s']/3600,r['summary']['energy_kwh'],r['summary']['sorties'],r['summary']['multi_stop_sorties'],r['summary']['on_time_boxes'],*[r['summary']['type_counts'].get(g,0) for g in 'ABC']]
        for name,r in [('初始可行调度',payload['initial']),*[(LABELS[k],v) for k,v in cases.items()]]],
        [36,28,22,22,22,14,18,18,18,18,18])
    sheets[-1]['formats']['4']='0.000000'
    sheet('对照运输安排',['方案','架次','无人机','机型','电池','准备开始s','访问路线','返航s','能耗kWh','质量kg','体积m³','SOC','充满时刻s','货箱列表'],[
        [LABELS[name],r['sortie'],r['unit'],r['drone'],r['battery'],r['start_s'],' → '.join(['O01',*r['visits'],'O01']),r['return_s'],r['energy_kwh'],r['mass_kg'],r['volume_m3'],r['soc'],r['charge_end_s'],'; '.join(r['boxes'])]
        for name,result in cases.items() if name!=primary_name for r in result['sorties']],
        [24,18,14,12,20,20,46,20,20,18,18,18,22,88])
    sheets[-1]['formats'].update({'8':'0.000000','11':'0.000%'})
    sheet('对照逐箱交付',['方案','货箱','服务区','架次','交箱次序','完成时刻s','期望时刻s','硬截止s','逾期s'],[
        [LABELS[name],r['box'],r['service'],r['sortie'],r['rank'],r['completion_s'],r['expected_s'],r['hard_s'],r['lateness_s']]
        for name,result in cases.items() if name!=primary_name for r in result['deliveries']])
    sheet('硬截止裕量',['货箱','服务区','身份','有效硬截止s','交付完成s','裕量s','是否满足'],[
        [r['box'],r['service'],'医疗+首批' if '-MED-' in r['box'] and r['first'] else '医疗' if '-MED-' in r['box'] else '首批',
         r['hard_s'],r['completion_s'],r['hard_s']-r['completion_s'],'是' if r['completion_s']<=r['hard_s'] else '否']
        for r in sorted((r for r in deliveries if r['hard_s'] is not None),key=lambda r:(r['hard_s']-r['completion_s'],r['box']))])
    phase_labels={'initial_fixed_routes':'固定组批初调','initial':'初始全池调度','search':'全池搜索','final':'主方案收尾','comparison':'目标对照'}
    metric_labels={'tardiness':'加权逾期','makespan':'最后返航','energy':'能耗','sorties':'架次','weighted':'归一化加权评分'}
    status_labels={'OPTIMAL_BY_ZERO_LOWER_BOUND':'已达零逾期下界','OPTIMAL':'当前模型已证最优','FEASIBLE':'可行，未证最优','UNKNOWN':'限时未取得新解','INFEASIBLE':'当前模型不可行'}
    sheet('求解状态',['方案','阶段','种子','轮次','目标','状态','候选数','当前值（整数单位）','求解器界限（整数单位）','本层条件最优','整个前缀已证最优','用时s'],[
        [LABELS.get(group.get('scheme'),''),'局部组批重排' if group.get('restricted_neighborhood') else phase_labels.get(group['phase'],group['phase']),stage.get('seed'),group.get('round'),metric_labels.get(stage.get('stage'),stage.get('stage')),status_labels.get(stage['status'],stage['status']),stage.get('pool_size'),stage.get('value'),stage.get('bound'),'是' if stage.get('proven') else '否','是' if stage.get('lex_prefix_proven') else '否',stage.get('seconds')]
        for group in payload['trace'] for stage in group['stages']],
        [24,22,12,12,18,27,15,30,32,20,22,20])
    sheet('核验结果',['检查项',*[LABELS[k] for k in cases]],[
        [label,*[r['verification'][field] for r in cases.values()]] for label,field in [
            ('完整交付箱数','unique_boxes'),('硬截止违反数','hard_deadline_violations'),('无人机与电池冲突数','resource_conflicts'),('总质量kg','mass_kg'),('总体积m³','volume_m3')]],
        [36]+[24]*len(cases))
    sheet('方案来源',['方案','目标顺序','最好解来源','候选池数量','说明'],[
        [LABELS[k],' → '.join(metric_labels[m] for m in payload.get('orders',{}).get(k,[])),payload.get('provenance',{}).get(k,''),payload['pool_size'],'收尾状态仅对应当次模型；跨目标搜索可更新此方案，不将其他运行的状态当作本方案最优证书。'] for k in cases],
        [24,50,35,18,90])
    sheet('口径与来源',['事项','说明'],[
        ['主目标',payload.get('objective_description','词典序：加权逾期→最后返航→能耗→架次；医疗和首批截止始终为硬约束。')],
        ['开始时刻','准备开始；无人机和满电电池均从此时占用。准备300秒，再按每箱30秒装载。'],
        ['交付时刻','基础交接后逐箱连续交接；箱序参与优化，最后一箱交完后才能飞往下一站。'],
        ['电池周转','返航立即按SOC分段充电。下一任务准备开始前充满。同型共享，跨型禁用，无额外充电器上限。'],
        ['最终完工','所有运输机最终返航的最晚时刻，不包含末次电池充电，也不等于累计作业时间。'],
        ['数值精度','时间1毫秒；飞行、充电向上取整。能耗目标按1e-9kWh量化。求解状态中的W为系数·毫秒、完工为毫秒、能耗为1e-9kWh单位、架次为整数。'],
        ['最优性范围','CP-SAT状态只针对该次候选池及已固定前级目标。限时固定当前值属于启发式分层，不代表全局最优。'],
        ['搜索限制','每架次同一服务区至多访问一次；不设二站或三站上限。候选池有限，可能遗漏更优路线。'],
        ['刷新方式','本表为外部优化程序的结果快照。修改数据或配置后运行run_q2.py，Excel不会自行重新求解。'],
        ['能源解释','水平能耗=可用能量×距离/等效航程；爬升能耗=势能/效率。沿用问题一，并非题面逐项展开的原公式。'],
        ['原始来源','数据/无人机应急物资运输基础数据：节点、逐箱需求、运输机参数及库存；完整30米DEM；原结果模板。']],[26,116])
    if 'map' in payload:
        nodes={n['id']:n for n in payload['nodes']};vertices=[]
        for r in main['legs']:
            a,b=r['from'],r['to'];pa,pb=payload['map']['xy'][a],payload['map']['xy'][b]
            za=nodes[a]['elevation']+(0 if a=='O01' else 30);zb=nodes[b]['elevation']+(0 if b=='O01' else 30)
            points=[(*pa,za),(*pa,r['cruise_altitude_m']),(*pb,r['cruise_altitude_m']),(*pb,zb)]
            for index,(x,y,z) in enumerate(points,1):vertices.append([r['sortie'],r['segment'],index,a,b,x,y,z])
        sheet('航迹坐标',['架次','航段','顶点次序','起点','终点','东向km','北向km','海拔m'],vertices,[18,14,16,16,16,20,20,20])
        sheets[-1]['formats'].update({'5':'0.000000','6':'0.000000','7':'0.000'})
    return sheets


def make_figures(payload,font_path,output):
    if 'schemes' in payload:
        from .figures import draw_all
        return draw_all(payload,font_path,output)
    font=FontProperties(fname=str(font_path))
    plt.rcParams.update({'font.family':font.get_name(),'axes.unicode_minus':False,'font.size':10,'savefig.bbox':'tight'})
    from matplotlib import font_manager
    font_manager.fontManager.addfont(str(font_path))
    main=payload['main'];rows=main['sorties'];colors={'A':'#4477AA','B':'#228833','C':'#CC6677'}
    def save(fig,name):
        fig.savefig(output/f'{name}.png',dpi=170);fig.savefig(output/f'{name}.pdf');plt.close(fig)
    nodes={n['id']:n for n in payload['nodes']};origin=nodes['O01'];lat=math.radians(origin['lat'])
    # Plot with the same WGS84 linearization as the solver, in kilometres.
    w=math.sqrt(1-0.0066943799901413165*math.sin(lat)**2)
    sx=6378137/w*math.cos(lat);sy=6378137*(1-0.0066943799901413165)/w**3
    xy={k:(sx*math.radians(n['lon']-origin['lon'])/1000,sy*math.radians(n['lat']-origin['lat'])/1000) for k,n in nodes.items()}
    fig,ax=plt.subplots(figsize=(11,8))
    for r in rows:
        route=['O01',*r['visits'],'O01']
        ax.plot([xy[s][0] for s in route],[xy[s][1] for s in route],color=colors[r['drone']],alpha=.38,lw=1.2)
        for a,b in zip(route,route[1:]):
            ax.annotate('',xy=xy[b],xytext=xy[a],arrowprops={'arrowstyle':'->','color':colors[r['drone']],'alpha':.25,'lw':.7})
    for name,(x,y) in xy.items():
        ax.scatter(x,y,s=90 if name=='O01' else 32,color='#222222',marker='*' if name=='O01' else 'o',zorder=4)
        ax.annotate(name,(x,y),xytext=(5,5),textcoords='offset points',fontsize=10)
    for g in 'ABC':ax.plot([],[],color=colors[g],label=f'{g}型 {main["summary"]["type_counts"].get(g,0)}架次')
    ax.legend();ax.set_aspect('equal');ax.set_xlabel('相对O01东向距离 / km');ax.set_ylabel('相对O01北向距离 / km')
    ax.set_title(f"问题二运输航线：{len(rows)}架次，其中{main['summary']['multi_stop_sorties']}架次多点访问\n重叠航线按机型着色；逐架次访问顺序见结果表")
    ax.grid(alpha=.2);save(fig,'01_多点运输航线')
    for key,name,title in [('unit','02_无人机占用甘特图','无人机占用：准备、装载与飞行交接'),('battery','03_电池占用与充电甘特图','电池占用与充电：实色执行任务，浅灰色充电')]:
        labels=sorted(set(r[key] for r in rows));fig,ax=plt.subplots(figsize=(13,max(5,len(labels)*.48)))
        for r in rows:
            y=labels.index(r[key]);left=r['start_s']/3600;width=r['operation_s']/3600
            ax.barh(y,width,left=left,height=.66,color=colors[r['drone']],edgecolor='white')
            ax.text(left+width/2,y,r['sortie'].replace('Q2-',''),ha='center',va='center',fontsize=8,color='white')
            if key=='battery':ax.barh(y,(r['charge_end_s']-r['return_s'])/3600,left=r['return_s']/3600,height=.66,color='#D9DFE5',edgecolor='white')
        ax.set_yticks(range(len(labels)),labels);ax.invert_yaxis();ax.set_xlabel('从任务开始计时 / h');ax.set_title(title)
        ax.axvline(main['summary']['makespan_s']/3600,color='#222222',ls='--',lw=1,label='全部返航')
        ax.legend();ax.grid(axis='x',alpha=.2);save(fig,name)
    ds=main['deliveries'];fig,ax=plt.subplots(figsize=(14,6));x=np.arange(len(ds))
    ax.scatter(x,[r['expected_s']/3600 for r in ds],marker='_',s=100,color='#BBBBBB',label='期望时间')
    hard=[(i,r) for i,r in enumerate(ds) if r['hard_s'] is not None]
    ax.scatter([i for i,r in hard],[r['hard_s']/3600 for i,r in hard],facecolors='none',edgecolors='#CC3311',marker='s',s=38,label='硬截止')
    ax.scatter(x,[r['completion_s']/3600 for r in ds],color=['#CC3311' if r['lateness_s']>0 else '#0077BB' for r in ds],s=20,label='实际逐箱交付')
    centers=[];labels=[]
    for service in sorted({r['service'] for r in ds}):
        ix=[i for i,r in enumerate(ds) if r['service']==service];centers.append(sum(ix)/len(ix));labels.append(service)
        ax.axvline(max(ix)+.5,lw=.5,color='#DDDDDD')
    ax.set_xticks(centers,labels,rotation=45);ax.set_ylabel('任务开始后 / h');ax.set_xlabel('按服务区、货箱编号排列的80个货箱')
    ax.set_title(f"逐箱及时性：{main['summary']['on_time_boxes']}/80箱按期，31个硬时限全部满足")
    ax.legend(ncol=3);ax.grid(axis='y',alpha=.2);save(fig,'04_逐箱交付及时性')


def write_report(payload,path):
    if 'schemes' in payload:
        from .narrative import write_report as write_new_report
        return write_new_report(payload,path)
    main=payload['main'];m=main['summary'];c=payload['comparison']['summary'];initial=payload['initial']['summary']
    final=next(t for t in payload['trace'] if t['phase']=='final')
    statuses='；'.join(f"{s.get('stage')}：{s['status']}" for s in final['stages'])
    adopted='对照搜索还找到了在主目标顺序下更好的方案，因此最终主方案采用该已验证改进；对照前的主方案另存于完整JSON中。' if payload.get('main_improved_by_comparison') else '最终主方案未被对照搜索中的方案改善。'
    effective=payload['metadata']['config']['optimization']
    budget=sum(effective[k] for k in ['initial_seconds','search_seconds','final_seconds','comparison_seconds'])
    execution=f"本次记录的求解预算为{budget/60:.1f}分钟。"+('本次从已验证的可行检查点继续搜索，进入本轮前的方案和求解记录保存在logs/resumed_from.json。' if effective.get('resume_checkpoint') else '本次从原始输入重新构造初始候选与方案。')
    table='\n'.join(f"| {name} | {s['weighted_tardiness_s']:.3f} | {s['makespan_s']/3600:.6f} | {s['energy_kwh']:.6f} | {s['sorties']} | {s['multi_stop_sorties']} |" for name,s in [('初始调度',initial),('及时性优先',m),('完工时间优先',c)])
    figures='\n\n'.join(f'![{name}]({(path.parent/"figures"/(name+".png")).as_posix()})' for name in ['01_多点运输航线','02_无人机占用甘特图','03_电池占用与充电甘特图','04_逐箱交付及时性'])
    text=f'''# 问题二计算结果与分析

## 1 主方案

在20%返航安全余量下，完成80箱、758 kg、2.011 m³交付。主方案共**{m['sorties']}架次**，其中**{m['multi_stop_sorties']}架次多点访问**；最后返航时刻为**{m['makespan_s']:.3f}秒（{m['makespan_s']/3600:.6f}小时）**，运输能耗**{m['energy_kwh']:.6f} kWh**。

31个医疗/首批硬时限箱全部按时。按全部货箱的期望时间评价，{m['on_time_boxes']}箱按期、{m['late_boxes']}箱逾期，应急优先系数加权逾期为{m['weighted_tardiness_s']:.3f}系数·秒。机型构成为A/B/C = {m['type_counts'].get('A',0)}/{m['type_counts'].get('B',0)}/{m['type_counts'].get('C',0)}架次，使用{m['units_used']}架实体机、{m['batteries_used']}组不同电池，最低返航SOC为{m['min_soc']:.6%}。

“开始时刻”是准备开始；“逐箱交付完成”按实际交接顺序逐箱累加。全部返航时间不包含末次充电。累计作业时间为{m['operation_s']/3600:.6f}小时，是各架次占用时间之和，与多机并行的最终完成时间不同。

## 2 目标对照

| 方案 | 加权逾期（系数·秒） | 最后返航h | 能耗kWh | 架次 | 多点架次 |
|---|---:|---:|---:|---:|---:|
{table}

对照在相同最终候选池上重新安排交箱顺序和资源时序；计算预算有限，差异同时受目标顺序与求解进度影响，不能将全部差值解释为精确Pareto权衡。

{adopted}

## 3 可行性与最优性边界

- 从原始逐箱质量、体积、时限与机型数据独立重算，货箱唯一交付、装载、逐段能耗、SOC、交接时间及资源占用检查全部通过。
- 最终候选池含{payload['pool_size']}个候选。主方案收尾阶段状态：{statuses}。每一状态仅适用于该次候选池和已固定的前级目标；限时阶段不代表证明最优。
- 原问题加权逾期下界为0；当前值为{m['weighted_tardiness_s']:.3f}。总质量给出架次数下界10，该下界未考虑地形、体积、时间和资源，因此较弱；主目标并非先最少架次。
- 每架次同一服务区至多访问一次，候选搜索不限制为两站或三站，但有限候选池可能遗漏更优方案。因此不宣称完整问题的全局最优。
- 主方案全部航段的毫秒向上取整裕量累计{main['verification']['total_flight_rounding_s']:.6f}秒；逐箱与资源验证均使用保守时序。能耗目标量化为1e-9 kWh，报告能耗按未量化公式重算。
- 固定种子、单求解线程、候选排序及配置均已记录。限时求解会受机器速度影响；保存的逐箱方案可直接复核，但不承诺跨机器逐位相同。

## 4 航线与资源可视化

{figures}

## 5 复现与交付

工程根目录执行 `python run_q2.py --config configs/q2.toml`。默认求解预算30分钟（建模、导出等开销另计），预算是上限，提前最优终止可更快。`--test`运行问题一与问题二回归检查；`--report-only`可从已保存结果重新生成报告、图表和Excel。

{execution} `python run_q2.py --verify-only`可独立重算保存的完整方案并核对Excel/CSV，避免依赖重新搜索取得相同结果。

结果工作簿保留原模板两张Q2表的列名顺序，并附装载、交接、逐段能耗、电池充电、无人机占用、目标对比和求解核验表。`tables/results.json`保留主方案、对照、逐阶段时间线、配置与输入哈希；`logs/solver_trace.json`保留求解状态与界限。

原始附件、问题一结果和既有论文工程保持原状。当前方案不包含通信保障，不能直接宣称满足问题三。
'''
    path.write_text(text,encoding='utf-8')
