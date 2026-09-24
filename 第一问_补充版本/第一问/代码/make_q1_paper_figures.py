"""Generate F03-F16 from verified local-plane Q1 results, without solving again."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.runtime_py'))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.ticker import MaxNLocator, ScalarFormatter

OUT = ROOT / '第一问' / '图片'
DATA = ROOT / '第一问' / '数据' / '图表源数据'
OUT.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)
SOURCES = {
    'baseline': ROOT / 'results/question1_batching/solution.json',
    'pareto': ROOT / 'results/question1_multiobjective/multiobjective.json',
    'reserve': ROOT / 'results/question1_reserve_sensitivity/sensitivity.json',
}
S, M, R = [json.loads(SOURCES[k].read_text(encoding='utf-8')) for k in SOURCES]
font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
plt.rcParams.update({
    'font.family': 'Microsoft YaHei', 'font.size': 9, 'axes.labelsize': 9,
    'axes.titlesize': 10, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
    'legend.fontsize': 8, 'axes.spines.top': False, 'axes.spines.right': False,
    'axes.linewidth': .7, 'svg.fonttype': 'none', 'pdf.fonttype': 42,
    'axes.unicode_minus': False, 'figure.facecolor': 'white',
    'savefig.facecolor': 'white', 'lines.linewidth': 1.5,
})
W = 180 / 25.4
COLORS = ['#725A9A', '#3A7CA5', '#D3A34A', '#649B88']
MODEL_COLORS = {'A': '#78858D', 'B': '#347D9D', 'C': '#C17B49'}
TYPE_NAMES = ['医疗', '饮水', '食品', '卫生']
MASS = np.array([3, 14, 8, 6])
VOLUME_L = np.array([12, 27, 28, 35])
BATCHES = S['batches']
SERVICES = sorted(S['routes'])
PLANS = R['interval_plans']
manifest = []


def source_csv(name, rows):
    rows = list(rows)
    path = DATA / (name + '.csv')
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ('null' if v is None else v) for k, v in row.items()})
    return path.relative_to(ROOT / '第一问').as_posix()


def save(fig, name, title, caption, rows, recommended=False, source='baseline', risk=''):
    src = source_csv(name, rows)
    fig.canvas.draw()
    for ext in ('png', 'svg', 'pdf'):
        fig.savefig(OUT / f'{name}.{ext}', dpi=600)
    fig.savefig(OUT / f'{name}_preview.png', dpi=135)
    manifest.append(dict(id=name[:3], name=name, title=title, caption=caption,
                         source_csv=src, result_source=SOURCES[source].relative_to(ROOT).as_posix(),
                         recommended_main=recommended, interpretation=risk,
                         width_mm=round(fig.get_figwidth() * 25.4, 3),
                         height_mm=round(fig.get_figheight() * 25.4, 3), dpi=600))
    plt.close(fig)


def grid(ax, axis='x'):
    ax.set_axisbelow(True)
    ax.grid(axis=axis, color='#E7E9EB', linewidth=.6)


def stacked(ax, values, labels, colors, category_labels, xlabel):
    y = np.arange(len(values))
    left = np.zeros(len(values))
    for j, (label, color) in enumerate(zip(labels, colors)):
        ax.barh(y, values[:, j], left=left, label=label, color=color,
                edgecolor='white', linewidth=.4, height=.7)
        left += values[:, j]
    ax.set_yticks(y, category_labels)
    ax.invert_yaxis()
    ax.set_xlabel(xlabel)
    grid(ax)
    return left


def f03():
    counts = np.array([next(a for a in S['service_summary'] if a['service_id'] == x)['demand'] for x in SERVICES])
    fig, axes = plt.subplots(1, 2, figsize=(W, 5.7), layout='constrained')
    stacked(axes[0], counts * MASS, TYPE_NAMES, COLORS, SERVICES, '需求质量 / kg')
    stacked(axes[1], counts * VOLUME_L, TYPE_NAMES, COLORS, SERVICES, '需求体积 / L')
    axes[0].set_title('(a) 质量：总计 758 kg', loc='left')
    axes[1].set_title('(b) 体积：总计 2011 L', loc='left')
    axes[0].legend(ncol=4, bbox_to_anchor=(0, 1.08), loc='lower left', borderaxespad=0)
    rows = [dict(service_id=s, type=t, box_count=int(counts[i,j]), mass_kg=int(counts[i,j]*MASS[j]), volume_litre=int(counts[i,j]*VOLUME_L[j])) for i,s in enumerate(SERVICES) for j,t in enumerate(S['type_order'])]
    save(fig, 'F03_demand', '各服务区的物资需求', '15个服务区共80箱、758 kg、2011 L。两面板分别按质量和体积堆叠四类物资，显示两种容量需求的差异。', rows)


def f04():
    values = np.array([b['counts'] for b in BATCHES]) * MASS
    labels = [f"{i+1:02d}  {b['service_id']} · {b['model']}" for i,b in enumerate(BATCHES)]
    fig, ax = plt.subplots(figsize=(W, 6.5), layout='constrained')
    totals = stacked(ax, values, TYPE_NAMES, COLORS, labels, '装载质量 / kg')
    for i,(v,b) in enumerate(zip(totals,BATCHES)):
        ax.text(v+.7, i, f"{v:g} kg / {b['box_count']}箱", va='center', fontsize=8)
    ax.set_xlim(0, 103)
    ax.legend(ncol=4, loc='lower left', bbox_to_anchor=(0, 1.01), borderaxespad=0)
    ax.set_ylabel('批次序号 / 服务区 / 机型')
    rows = [dict(batch_id=b['batch_id'], service_id=b['service_id'], model=b['model'], type=t, count=b['counts'][j], mass_kg=b['counts'][j]*int(MASS[j])) for b in BATCHES for j,t in enumerate(S['type_order'])]
    save(fig, 'F04_batch_composition', '基准18架次的整箱组批方案', '返航余量为20%时的推荐方案，共18架次，B型和C型各9架次。横向长度为货物质量，行末同时标注质量与箱数。完整真实箱号见逐箱分配表。', rows, True)


def f05():
    rows=[]
    vals=[]
    for b in BATCHES:
        v=[100*b['mass_kg']/b['payload_limit_kg'], 100*b['volume_m3']/b['volume_limit_m3'], 100*b['energy_kwh']/b['energy_budget_kwh']]
        vals.append(v)
        rows.append(dict(batch_id=b['batch_id'], service_id=b['service_id'], model=b['model'], mass_utilization_percent=v[0], volume_utilization_percent=v[1], energy_budget_utilization_percent=v[2]))
    vals=np.array(vals)
    fig, axes=plt.subplots(1,3,figsize=(W,6),sharey=True,layout='constrained')
    labels=[f"{i+1:02d}  {b['service_id']}·{b['model']}" for i,b in enumerate(BATCHES)]
    for j,(ax,title) in enumerate(zip(axes,['质量 / 额定载荷','体积 / 装载容积','能耗 / 安全预算'])):
        ax.scatter(vals[:,j], np.arange(18), s=24, color=COLORS[j+1], zorder=3)
        ax.axvline(100, color='#B3544E', ls='--', lw=1)
        ax.set_xlim(0,108); ax.set_xticks([0,50,100]); ax.set_xlabel('利用率 / %')
        ax.set_title(f'({chr(97+j)}) {title}',loc='left',fontsize=9)
        grid(ax)
    axes[0].set_yticks(range(18), labels); axes[0].invert_yaxis()
    save(fig,'F05_constraint_utilization','三类容量约束的利用率','每批次质量、体积和能量利用率均不超过100%。能量预算为电池可用能量的80%，已经扣除20%的返航安全余量，不能把该列误读为总电量消耗比例。',rows)


def f06():
    fields=['horizontal_out_kwh','horizontal_return_kwh','climb_out_kwh','climb_return_kwh']
    values=np.array([[b[k] for k in fields] for b in BATCHES])
    assert np.allclose(values.sum(axis=1),[b['energy_kwh'] for b in BATCHES],rtol=0,atol=1e-12)
    fig,axes=plt.subplots(1,2,figsize=(W,6),sharey=True,gridspec_kw={'width_ratios':[1.5,1]},layout='constrained')
    labels=[f"{i+1:02d} {b['service_id']}" for i,b in enumerate(BATCHES)]
    names=['去程水平','返程水平','去程爬升','返程爬升']
    stacked(axes[0],values,names,COLORS,labels,'往返能耗 / kWh')
    stacked(axes[1],values[:,2:],names[2:],COLORS[2:],labels,'爬升能耗 / kWh')
    axes[0].invert_yaxis()
    axes[0].set_title('(a) 完整能耗分解',loc='left'); axes[1].set_title('(b) 爬升分量放大',loc='left')
    axes[0].legend(ncol=2, loc='lower left',bbox_to_anchor=(0,1.05),borderaxespad=0)
    axes[1].set_xlim(0,max(values[:,2:].sum(axis=1))*1.1)
    rows=[dict(batch_id=b['batch_id'],**{k:b[k] for k in fields},energy_kwh=b['energy_kwh']) for b in BATCHES]
    save(fig,'F06_energy_components','往返运输能耗的分项组成',f"能耗按去程水平、返程水平、去程爬升和返程爬升四项展开，总计{S['totals']['energy_kwh']:.6f} kWh。右侧放大爬升分量，两面板横轴尺度不同；下降按当前题设展开口径不另加能耗。",rows)


def f07():
    rows=[]
    for b in BATCHES:
        d=S['drones'][b['model']]
        row=dict(batch_id=b['batch_id'],prepare_s=d['prepare_s'],load_s=b['box_count']*d['load_per_box_s'],flight_s=b['flight_time_s'],handover_s=d['handover_base_s']+b['box_count']*d['handover_per_box_s'],work_time_s=b['work_time_s'])
        assert abs(sum(row[k] for k in ['prepare_s','load_s','flight_s','handover_s'])-b['work_time_s'])<1e-8
        rows.append(row)
    values=np.array([[r[k]/60 for k in ['prepare_s','load_s','flight_s','handover_s']] for r in rows])
    fig,ax=plt.subplots(figsize=(W,6),layout='constrained')
    stacked(ax,values,['准备','装载','飞行','交接'],['#B8C1C6',COLORS[0],COLORS[1],COLORS[2]],[f"{i+1:02d} {b['service_id']}·{b['model']}" for i,b in enumerate(BATCHES)],'单架次作业时间 / min')
    ax.legend(ncol=4,loc='lower left',bbox_to_anchor=(0,1.01),borderaxespad=0)
    save(fig,'F07_time_components','累计作业时间的分项组成','各架次准备、装载、飞行与交接时间相加，累计546.275280 min，其中飞行321.475280 min。该累计指标用于组批方案比较，不是有限机队并行调度的完工时间。',rows)


def f08():
    values=np.array([[x['safe_payloads'][m]['safe_payload_kg'] for m in 'ABC'] for x in S['service_summary']])
    fig,ax=plt.subplots(figsize=(W,5.8),layout='constrained')
    im=ax.imshow(values,cmap='Blues',vmin=0,vmax=80,aspect='auto')
    for i,x in enumerate(S['service_summary']):
        for j,m in enumerate('ABC'):
            tag=' *' if x['safe_payloads'][m]['limiting_factor']!='rated_payload' else ''
            ax.text(j,i,f'{values[i,j]:.2f}{tag}',ha='center',va='center',fontsize=9,color='white' if values[i,j]>45 else '#162C40')
    ax.set_xticks(range(3),['A型（额定25 kg）','B型（额定30 kg）','C型（额定80 kg）'])
    ax.set_yticks(range(15),SERVICES)
    ax.tick_params(length=0)
    fig.colorbar(im,ax=ax,label='最大安全载荷 / kg',shrink=.8,pad=.025)
    rows=[dict(service_id=x['service_id'],model=m,**x['safe_payloads'][m]) for x in S['service_summary'] for m in 'ABC']
    save(fig,'F08_safe_payload','20%返航余量下的最大安全载荷','星号表示安全能量约束先于额定载质量生效。每一单元是该航线与机型下连续货重的最大安全值；实际整箱装载还须满足装载体积和不可拆分约束。',rows,True)


def f09():
    front=M['frontier']
    fig,axes=plt.subplots(1,2,figsize=(W,3.65),layout='constrained')
    for i,p in enumerate(front):
        for ax,key in zip(axes,['energy_kwh','work_time_s']):
            v=p[key]/60 if key=='work_time_s' else p[key]
            ax.scatter(p['flight_count'],v,marker=['o','D'][i],color=[MODEL_COLORS['B'],MODEL_COLORS['C']][i],s=75,zorder=3)
            ax.annotate(p['plan_id'],(p['flight_count'],v),xytext=(0,12),textcoords='offset points',ha='center',fontsize=9)
    axes[0].set_ylabel('总运输能耗 / kWh'); axes[1].set_ylabel('累计作业时间 / min')
    axes[0].set_ylim(59.01,59.17); axes[1].set_ylim(538,590)
    axes[0].set_title('(a) 架次与能耗',loc='left');axes[1].set_title('(b) 架次与时间',loc='left')
    for ax in axes:
        ax.set_xlim(17.6,19.4);ax.set_xticks([18,19]);ax.set_xlabel('往返架次数');grid(ax,'both');ax.ticklabel_format(axis='y',useOffset=False)
    rows=[dict(plan_id=p['plan_id'],flight_count=p['flight_count'],energy_kwh=p['energy_kwh'],work_time_s=p['work_time_s'],work_time_min=p['work_time_s']/60) for p in front]
    t=M['tradeoff']
    save(fig,'F09_pareto','完整离散Pareto前沿',f"当前20%余量模型的完整前沿只有PF001与PF002两个目标点。增加{t['extra_flights']}架次可节省{t['energy_saved_kwh']:.6f} kWh（{t['energy_saved_percent']:.6f}%），但累计作业时间增加{t['extra_work_time_s']/60:.6f} min（{t['extra_work_time_percent']:.6f}%）；按架次、能耗、时间字典序推荐PF001。点间不存在连续可选方案。",rows,True,'pareto')


def f10():
    rows=[]
    labels=[]
    values=[]
    for p in M['frontier']:
        for b in p['batches']:
            if b['service_id']=='S008':
                labels.append(f"{p['plan_id']}\n{b['model']}型 · {b['mass_kg']:g} kg")
                values.append(np.array(b['counts'])*MASS)
                rows.append(dict(plan_id=p['plan_id'],model=b['model'],mass_kg=b['mass_kg'],volume_m3=b['volume_m3'],box_ids=json.dumps(b['box_ids'],ensure_ascii=False),counts=json.dumps(b['counts']),energy_kwh=b['energy_kwh'],work_time_s=b['work_time_s']))
    fig,ax=plt.subplots(figsize=(W,3.3),layout='constrained')
    stacked(ax,np.array(values),TYPE_NAMES,COLORS,labels,'装载质量 / kg')
    for i,(v,row) in enumerate(zip(values,rows)):
        left=0
        for j,mass in enumerate(v):
            if mass>0:
                ax.text(left+mass/2,i,str(json.loads(row['counts'])[j]),ha='center',va='center',color='white',fontsize=9)
            left+=mass
    ax.axhline(.5,color='#AEB4B9',lw=.8,ls='--')
    ax.legend(ncol=4,loc='lower left',bbox_to_anchor=(0,1.04),borderaxespad=0)
    save(fig,'F10_s008_split','两个前沿方案的S008组批差异','PF001将S008的5箱45 kg交由1架次C型配送；PF002改为2架次B型，分别为饮水与卫生20 kg、医疗与饮水与食品25 kg。条内数字为该批次该类箱数，其余服务区采用相同代表组批。',rows,source='pareto')


def exact_steps(ax,key,scale=1,xmax=40):
    for i,p in enumerate(PLANS):
        lo,hi=p['lower_percent'],p['upper_percent']; y=p[key]/scale
        ax.hlines(y,lo,hi,color=MODEL_COLORS['B'],lw=1.7)
        ax.plot(hi,y,'o',ms=2.8,color=MODEL_COLORS['B'])
        if i and abs(PLANS[i-1][key]/scale-y)>1e-10:
            ax.plot(lo,y,'o',ms=2.8,mfc='white',mec=MODEL_COLORS['B'],mew=.8)
    limit=PLANS[-1]['upper_percent']
    ax.axvspan(limit,xmax,color='#EEF0F2')
    ax.axvline(20,color='#A17D3E',lw=.9,ls='--')
    ax.axvline(limit,color='#8D5F56',lw=.9,ls=':')
    ax.set_xlim(0,xmax);grid(ax,'y')


def f11():
    fig,axes=plt.subplots(3,1,figsize=(W,6.5),sharex=True,layout='constrained')
    for ax,key,scale,label in zip(axes,['flight_count','energy_kwh','work_time_s'],[1,1,60],['往返架次','能耗 / kWh','累计时间 / min']):
        exact_steps(ax,key,scale)
        ax.set_ylabel(label)
    axes[0].yaxis.set_major_locator(MaxNLocator(integer=True))
    axes[0].text(37.65,21,'全任务\n不可行',ha='center',fontsize=9,color='#5B6269')
    axes[0].annotate(f"18架次方案失效边界\n{PLANS[0]['upper_percent']:.6f}%",(PLANS[0]['upper_percent'],18),xytext=(7,21.7),arrowprops={'arrowstyle':'->','color':'#58646C','lw':.8},fontsize=8)
    axes[-1].set_xlabel('返航安全余量 / %')
    rows=[{k:p[k] for k in ['plan_id','lower_percent','upper_percent','lower_inclusive','upper_inclusive','flight_count','energy_kwh','work_time_s']} for p in PLANS]
    save(fig,'F11_reserve_steps','安全余量变化下的精确最优方案阶梯',f"以真实返航余量为横轴绘制{len(PLANS)}段字典序最优代表方案。实心端点属于左侧方案，后续区间下端开、上端闭；20%为基准虚线，{PLANS[-1]['upper_percent']:.10f}%以上无全任务可行组批。最少架次单调不减，次级能耗和时间未必单调。",rows,True,'reserve')


def f12():
    fig,axes=plt.subplots(1,4,figsize=(W,6.7),sharey=True,gridspec_kw={'width_ratios':[.85,1,1,1.25]},layout='constrained')
    ys=np.arange(19)
    keys=['flight_count','energy_kwh','work_time_s','upper_percent']
    scales=[1,1,60,1]
    titles=['架次','能耗 / kWh','时间 / min','余量上界 / %']
    for j,(ax,key,sc,title) in enumerate(zip(axes,keys,scales,titles)):
        vals=np.array([p[key]/sc for p in PLANS])
        ax.scatter(vals,ys,s=22,color=COLORS[j],zorder=3)
        ax.set_xlabel(title); grid(ax)
        if j==3:
            ax.set_xlim(22,40)
            for y,v in zip(ys,vals): ax.annotate(f'{v:.6f}',(v,y),xytext=(4,0),textcoords='offset points',fontsize=6.7,va='center')
            ax.set_xticks([25,30,35])
    axes[0].set_yticks(ys,[p['plan_id'] for p in PLANS]);axes[0].invert_yaxis()
    axes[0].xaxis.set_major_locator(MaxNLocator(integer=True,nbins=3))
    rows=[dict(plan_id=p['plan_id'],lower_percent=p['lower_percent'],upper_percent=p['upper_percent'],interval_width_percentage_points=p['upper_percent']-p['lower_percent'],flight_count=p['flight_count'],energy_kwh=p['energy_kwh'],work_time_min=p['work_time_s']/60) for p in PLANS]
    save(fig,'F12_interval_comparison','19段最优方案及指标对照','每行对应一个最优代表方案，纵轴为类别编号，不表示等宽余量区间。能耗与时间在固定架次内可能上升，在最少架次数增加时亦可能下降。图中端点仅为显示舍入值，精确边界以CSV和正文附表为准。',rows,source='reserve')


def f13():
    fig,axes=plt.subplots(5,3,figsize=(W,8.5),sharex=True,sharey=True,layout='constrained')
    rows=R['payload_curves']
    for ax,s in zip(axes.flat,SERVICES):
        for m in 'ABC':
            items=[p for p in rows if p['service_id']==s and p['model']==m]
            xs=[p['reserve_percent'] for p in items]
            ys=[np.nan if p['safe_payload_kg'] is None else p['safe_payload_kg'] for p in items]
            ax.plot(xs,ys,color=MODEL_COLORS[m],label=m+'型',ls={'A':':','B':'--','C':'-'}[m],lw=1.3)
        ax.axvline(20,color='#C5B894',lw=.7,ls=':')
        ax.set_title(s,loc='left',fontsize=8)
        ax.set_xlim(0,60);ax.set_ylim(0,85);ax.set_xticks([0,20,40,60]);ax.set_yticks([0,40,80]);grid(ax,'both')
    for ax in axes[-1]:ax.set_xlabel('安全余量 / %',fontsize=8)
    for ax in axes[:,0]:ax.set_ylabel('安全载荷 / kg',fontsize=8)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside upper center',ncol=3,frameon=False)
    save(fig,'F13_payload_curves','15服务区三类机型的安全载荷曲线','每幅小图比较A、B、C型在0%至60%返航余量下的连续质量上限。源数据为1个百分点步长的2745个样点，连线仅辅助读图；空载已不可行的位置停止绘线，不补零。精确组批阈值另由事件扫描求得。',rows,source='reserve')


def f14():
    p18,p19=PLANS[-2:]
    boundary=p18['upper_percent'];limit=p19['upper_percent'];delta=limit-boundary
    fig,axes=plt.subplots(2,1,figsize=(W,4.6),layout='constrained')
    # An offset axis avoids concealing the tiny but nonzero final interval.
    for ax,xlim in zip(axes,[(-.15,.012),(-.35*delta,1.35*delta)]):
        ax.hlines(p18['energy_kwh'],p18['lower_percent']-boundary,0,color=MODEL_COLORS['B'],lw=2)
        ax.hlines(p19['energy_kwh'],0,delta,color=MODEL_COLORS['C'],lw=2)
        ax.plot(0,p18['energy_kwh'],'o',color=MODEL_COLORS['B'],ms=5)
        ax.plot(0,p19['energy_kwh'],'o',mfc='white',mec=MODEL_COLORS['C'],ms=5)
        ax.plot(delta,p19['energy_kwh'],'o',color=MODEL_COLORS['C'],ms=5)
        ax.axvspan(delta,xlim[1],color='#EEF0F2');ax.set_xlim(*xlim);ax.set_ylim(77,85.4)
        ax.set_ylabel('总能耗 / kWh');grid(ax,'y')
    axes[0].set_title('(a) 最后两段的能耗变化',loc='left')
    axes[0].text(-.09,79,'R018：两箱饮水用B型',fontsize=8)
    axes[1].set_title('(b) R019窄区间放大：25架次保持不变',loc='left')
    axes[1].text(delta/2,84.1,f'区间宽度 {delta:.9f} 个百分点',ha='center',fontsize=8)
    axes[1].text(delta/2,81.5,'R019：S008两箱饮水分别改用C型',ha='center',fontsize=8)
    axes[1].xaxis.set_major_locator(MaxNLocator(nbins=5))
    axes[1].ticklabel_format(axis='x',useOffset=False,style='plain')
    axes[1].set_xlabel(f'返航安全余量 − {boundary:.11f}% / 百分点')
    axes[0].set_xlabel('相对于B型饮水单箱阈值的余量增量 / 百分点')
    rows=[dict(plan_id=p['plan_id'],lower_percent=p['lower_percent'],upper_percent=p['upper_percent'],flight_count=p['flight_count'],energy_kwh=p['energy_kwh'],work_time_s=p['work_time_s'],offset_origin_percent=boundary) for p in [p18,p19]]
    save(fig,'F14_last_threshold','最终狭窄可行区间与机型切换',f"B型在{boundary:.13f}%之后不能安全运送S008的14 kg饮水单箱，C型仍可行至{limit:.13f}%。最终区间宽约{delta:.9f}个百分点，两箱分别从B型切换至C型，保持25架次而使总能耗增加{p19['energy_kwh']-p18['energy_kwh']:.6f} kWh。超过右端点后整箱需求无法全部完成。",rows,True,'reserve')


def f15():
    fig,axes=plt.subplots(1,3,figsize=(W,5.8),sharey=True,layout='constrained')
    rows=[]
    for ax,m in zip(axes,'ABC'):
        for i,s in enumerate(SERVICES):
            p=next(x for x in R['payloads'] if x['service_id']==s and x['model']==m)
            lo=p['full_load_maximum_reserve_percent'];hi=p['empty_roundtrip_maximum_reserve_percent']
            ax.hlines(i,lo,hi,color=MODEL_COLORS[m],lw=2)
            ax.plot(lo,i,'o',color=MODEL_COLORS[m],ms=4)
            ax.plot(hi,i,'o',mfc='white',mec=MODEL_COLORS[m],ms=4)
            rows.append(dict(service_id=s,model=m,full_load_maximum_reserve_percent=lo,empty_roundtrip_maximum_reserve_percent=hi))
        ax.set_title(m+'型',loc='left');ax.set_xlim(-5,90);ax.set_xticks([0,20,40,60,80]);ax.set_xlabel('安全余量 / %');grid(ax)
        ax.axvline(0,color='#C6CDD1',lw=.6)
        if m=='C':
            negative=next(p['full_load_maximum_reserve_percent'] for p in R['payloads'] if p['service_id']=='S008' and p['model']=='C')
            ax.text(3,7.35,f'{negative:.3f}%',fontsize=7,color=MODEL_COLORS[m])
        ax.axvline(20,color='#C3B184',ls=':',lw=.8)
    axes[0].set_yticks(range(15),SERVICES);axes[0].invert_yaxis()
    axes[1].plot([],[],'o-',color='#68757D',label='实心：额定满载上限')
    axes[1].plot([],[],'o',mfc='white',mec='#68757D',label='空心：空载往返上限')
    fig.legend(*axes[1].get_legend_handles_labels(),loc='outside upper center',ncol=2)
    save(fig,'F15_payload_thresholds','满载与空载的安全余量边界',f'每条线段连接额定满载和空载往返对应的余量边界。实心点左侧可额定满载，线段内安全载荷受能量限制并下降，空心点右侧空载也不可行。S008/C型的满载阈值为{negative:.6f}%，表示即使0%余量也不能额定满载；负值仅为公式边界，不是可选安全余量。线段不是误差区间。',rows,source='reserve')


def f16():
    fig,ax=plt.subplots(figsize=(W,6),layout='constrained');ax.axis('off');ax.set_xlim(0,1);ax.set_ylim(0,1)
    boxes=[(.1,.86,.8,.10,'题给输入与规则核验\n16节点、3机型、80货箱、原始DEM'),
           (.1,.70,.8,.10,'局部椭球坐标 + 全相交DEM像元\n距离、巡航高度、双向爬升、能耗与时间'),
           (.1,.54,.8,.10,'枚举整箱数量组合\n742个容量可行候选；20%余量下732个候选'),
           (.025,.30,.29,.15,'数量状态DP\n按(N,E,T)字典序\n基准18架次'),
           (.355,.30,.29,.15,'非支配标签DP\n逐区求解、全局合并\n完整Pareto前沿2点'),
           (.685,.30,.29,.15,'精确事件扫描\n110个内部临界值\n111区间 → 19方案段'),
           (.1,.08,.8,.12,'恢复真实箱号 + 独立复核\n逐箱唯一性、容量、能耗、端点、完整前沿与载荷\n论文表格、图组与可回查源数据')]
    for i,(x,y,w,h,label) in enumerate(boxes):
        rect=FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.012',fc='#F1F5F7' if i<3 or i==6 else '#E9F0F4',ec='#7C98A7',lw=1)
        ax.add_patch(rect);ax.text(x+w/2,y+h/2,label,ha='center',va='center',fontsize=9 if i<3 or i==6 else 8.4,linespacing=1.6)
    def arrow(a,b):ax.add_patch(FancyArrowPatch(a,b,arrowstyle='-|>',mutation_scale=12,color='#68808E',lw=1))
    arrow((.5,.855),(.5,.814));arrow((.5,.695),(.5,.654))
    for x in [.17,.5,.83]:arrow((.5,.528),(x,.464));arrow((x,.288),(.5,.212))
    rows=[dict(node=i+1,label=b[-1].replace('\n','; ')) for i,b in enumerate(boxes)]
    save(fig,'F16_solver_flow','第一问建模与精确求解流程','先统一几何与物理口径，再枚举有限整箱候选。数量DP求推荐组批，非支配标签DP求完整目标前沿，候选失效阈值扫描保留所有可能改变可行集的事件，最后恢复真实箱号并独立校验。19段是区间代表方案，不表示所有等价箱号解仅有19个。',rows,source='reserve')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    # The design contract is saved before any rendering.
    assert (ROOT/'第一问/文字/图表设计约定.md').exists()
    assert len(BATCHES)==18 and len(PLANS)==19 and len(M['frontier'])==2
    assert sum(b['box_count'] for b in BATCHES)==80
    assert len({box for b in BATCHES for box in b['box_ids']})==80
    assert abs(sum(b['mass_kg'] for b in BATCHES)-758)<1e-9
    for fn in [f03,f04,f05,f06,f07,f08,f09,f10,f11,f12,f13,f14,f15,f16]:
        fn();print(fn.__name__+' exported',flush=True)
    result=dict(backend='Python matplotlib',versions={'python':sys.version,'matplotlib':matplotlib.__version__,'numpy':np.__version__},
                data_source_sha256={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in SOURCES.items()},figures=manifest)
    (OUT/'figure_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
