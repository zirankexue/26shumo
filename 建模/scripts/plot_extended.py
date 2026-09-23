"""Plot audited saved results; never invoke an optimizer. Paths are project-relative."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
_python = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (__name__ == '__main__' and (PROJECT / '.runtime/python').is_dir()
        and _python.exists() and Path(sys.executable).resolve() != _python.resolve()):
    import subprocess
    raise SystemExit(subprocess.call([str(_python), '-X', 'utf8', str(Path(__file__).resolve()), *sys.argv[1:]]))
for folder in (PROJECT / 'src', PROJECT / '.runtime/python'):
    if folder.is_dir():
        sys.path.insert(0, str(folder))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import platform

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
import numpy as np

from uav_rescue.common.data import file_hash
from uav_rescue.common.physics import equivalent_range, leg_energy
from uav_rescue.common.terrain import Terrain, Leg
from uav_rescue.q2.data import load_scheduling_inputs

TYPE_COLORS = {'A': '#4477AA', 'B': '#228833', 'C': '#CC6677'}
ENERGY_COLORS = ['#4477AA', '#DD9944', '#88BBDD', '#EECC88']
TOL = 1e-9


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b, label, tol=TOL):
    require(math.isclose(a, b, rel_tol=0, abs_tol=tol), f'{label}: {a} != {b}')


def write_csv(path, rows):
    require(bool(rows), f'空明细: {path}')
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cell_intervals(terrain, start, end):
    """Clip the line against EVERY supercover closed cell, including point contacts.

    t is a dimensionless affine distance along the horizontal leg. Boundary-only
    contacts have t_enter == t_exit; they are plotted as dots, never discarded.
    """
    x0, y0 = terrain.grid(start)
    x1, y1 = terrain.grid(end)
    records = []
    cells = terrain.cells(start, end)
    require(cells == terrain.cells(end, start), '正反向像元不一致')
    terrain.maximum(cells)  # Reject nodata before plotting.
    for row, col in sorted(cells):
        lo, hi = 0., 1.
        for origin, delta, lower in ((x0, x1-x0, col), (y0, y1-y0, row)):
            if abs(delta) < 1e-15:
                require(lower-1e-9 <= origin <= lower+1+1e-9, '平行像元不相交')
            else:
                a, b = sorted(((lower-origin)/delta, (lower+1-origin)/delta))
                lo, hi = max(lo, a), min(hi, b)
        require(lo <= hi + 1e-9, 'supercover像元裁剪失败')
        if lo > hi:
            lo = hi = min(1., max(0., (lo+hi)/2))
        records.append({'row': row, 'col': col, 't_enter': lo, 't_exit': hi,
                        'elevation_m': float(terrain.elevations[row, col]),
                        'point_contact': hi-lo <= 1e-12})
    require(bool(records), '剖面没有像元')
    close(max(r['elevation_m'] for r in records), terrain.maximum(cells), '剖面最高高程')
    # Check full [0,1] coverage, allowing coincident intervals on grid boundaries.
    end_t = 0.
    for r in sorted(records, key=lambda r: (r['t_enter'], r['t_exit'])):
        require(r['t_enter'] <= end_t+1e-9, '剖面存在水平覆盖缺口')
        end_t = max(end_t, r['t_exit'])
    close(end_t, 1., '剖面终点')
    return records


def energy_parts(drone, leg, mass, gravity):
    horizontal = drone.battery * leg.distance / equivalent_range(drone, mass)
    climb = (drone.empty_mass+mass)*gravity*leg.climb/(3.6e6*drone.climb_efficiency)
    close(horizontal+climb, leg_energy(drone, leg, mass, gravity), '分项与物理函数')
    return horizontal, climb


def save(fig, output, name):
    fig.savefig(output/'figures'/f'{name}.png', dpi=300, bbox_inches='tight', facecolor='white')
    fig.savefig(output/'figures'/f'{name}.pdf', bbox_inches='tight', facecolor='white')
    plt.close(fig)


def plot_profiles(q1, q2, inputs, terrain, physics, output):
    nodes = {inputs.origin.id: inputs.origin, **inputs.services}
    far = sorted(q1['routes'], key=lambda r: (-r['outbound']['distance'], r['service']))[0]
    high = sorted((r for r in q1['routes'] if r['service'] != far['service']),
                  key=lambda r: (-r['outbound']['climb'], r['service']))[0]
    multi = sorted(q2['main']['sorties'], key=lambda r: (-len(r['visits']), r['sortie']))[0]
    selected = [
        ('问题一 · 最长水平航段', [Leg(**far['outbound'])]),
        ('问题一 · 最大去程爬升', [Leg(**high['outbound'])]),
        (f"问题二 · {multi['sortie']} · {len(multi['visits'])}站完整往返", [
            Leg(r['from'], r['to'], r['distance_m'], r['cruise_altitude_m'], r['climb_m'], r['descent_m'])
            for r in sorted(q2['main']['legs'], key=lambda r: r['segment']) if r['sortie'] == multi['sortie']])]
    fig, axes = plt.subplots(3, 1, figsize=(15, 12))
    fig.subplots_adjust(left=.075, right=.98, top=.90, bottom=.105, hspace=.59)
    all_cells, segments = [], []
    for panel, (ax, (title, legs)) in enumerate(zip(axes, selected), 1):
        offset = 0.
        boundaries = []
        for index, leg in enumerate(legs, 1):
            a, b = nodes[leg.start], nodes[leg.end]
            computed = terrain.leg(a, b, physics['clearance_m'], physics['service_height_m'])
            for key in ('distance', 'cruise_altitude', 'climb', 'descent'):
                close(getattr(leg, key), getattr(computed, key), f'剖面{a.id}->{b.id}/{key}', 1e-6)
            intervals = cell_intervals(terrain, a, b)
            for rec in intervals:
                left = offset+rec['t_enter']*leg.distance
                right = offset+rec['t_exit']*leg.distance
                all_cells.append({'panel': panel, 'segment': index, 'from': a.id, 'to': b.id,
                                  **rec, 'distance_enter_m': left, 'distance_exit_m': right})
                if rec['point_contact']:
                    ax.plot(left/1000, rec['elevation_m'], '.', color='#665C48', ms=3, zorder=4)
                else:
                    ax.fill_between([left/1000, right/1000], [rec['elevation_m']]*2,
                                    color='#C7CEBA', linewidth=0)
                    ax.plot([left/1000, right/1000], [rec['elevation_m']]*2,
                            color='#768365', lw=.75)
            x0, x1 = offset/1000, (offset+leg.distance)/1000
            za, zb = leg.cruise_altitude-leg.climb, leg.cruise_altitude-leg.descent
            # Dashed descent and solid ascent remain distinct at a shared service node.
            ax.plot([x0, x0, x1], [za, leg.cruise_altitude, leg.cruise_altitude], color='#246590', lw=1.8)
            ax.plot([x1, x1], [leg.cruise_altitude, zb], color='#B36D32', lw=1.6, ls='--', zorder=5)
            for xx, low, high_y, up in [(x0, za, leg.cruise_altitude, True), (x1, zb, leg.cruise_altitude, False)]:
                span = high_y-low
                if span > 0:
                    arrow_y = (low+.25*span, low+.65*span) if up else (low+.65*span, low+.25*span)
                    ax.annotate('', xy=(xx, arrow_y[1]), xytext=(xx, arrow_y[0]),
                                arrowprops={'arrowstyle': '->', 'color': '#246590' if up else '#B36D32', 'lw': 1.2}, zorder=6)
            ax.annotate('', xy=(x0+.63*(x1-x0), leg.cruise_altitude),
                        xytext=(x0+.42*(x1-x0), leg.cruise_altitude),
                        arrowprops={'arrowstyle': '->', 'color': '#246590'})
            peak = max(intervals, key=lambda r: r['elevation_m'])
            xp = (offset+leg.distance*(peak['t_enter']+peak['t_exit'])/2)/1000
            zmax = peak['elevation_m']
            ax.hlines(zmax, x0, x1, colors='#9F7841', ls=':', lw=.9)
            ax.plot(xp, zmax, '^', color='#9F7841', ms=5)
            ax.annotate('', xy=(xp, leg.cruise_altitude), xytext=(xp, zmax),
                        arrowprops={'arrowstyle': '<->', 'color': '#9F7841', 'lw': .9})
            ax.annotate('50 m', (xp, (zmax+leg.cruise_altitude)/2), xytext=(5, 0),
                        textcoords='offset points', fontsize=8, color='#805D2A', va='center')
            ax.text((x0+x1)/2, leg.cruise_altitude+20, f"巡航 {leg.cruise_altitude:.1f} m"+
                    (' · 返航' if leg.end == 'O01' else '')+f'\n最高地形 {zmax:.1f} m',
                    ha='center', fontsize=9, color='#246590')
            if not boundaries:
                boundaries.append((x0, a, za))
            boundaries.append((x1, b, zb))
            segments.append({'panel': panel, 'segment': index, 'from': a.id, 'to': b.id,
                             'distance_m': leg.distance, 'terrain_max_m': zmax,
                             'cruise_altitude_m': leg.cruise_altitude, 'climb_m': leg.climb,
                             'descent_m': leg.descent, 'cell_count': len(intervals)})
            offset += leg.distance
        for xx, node, working in boundaries:
            ax.plot(xx, node.elevation, 'o', color='#333333', ms=4, zorder=6)
            ax.plot(xx, working, 'D', color='#AE5674', ms=4, zorder=7)
        ax.set_xticks([b[0] for b in boundaries], [f'{n.id}\n{x:.2f} km' for x,n,_ in boundaries])
        ax.set_ylim(0, max(g.cruise_altitude for g in legs)+95)
        ax.set_xlim(-offset/1000*.035, offset/1000*1.035)
        node_note = '；'.join(f'{n.id}地面/作业 {n.elevation:g}/{z:g} m' for _,n,z in boundaries)
        ax.set_title(title+'\n'+node_note, loc='left', fontsize=11, pad=9)
        ax.set_ylabel('海拔 / m')
        ax.set_xlabel('累计水平航程（升降阶段无水平位移）' if panel == 3 else '沿航段水平距离', labelpad=4)
        ax.grid(axis='y', alpha=.16)
    handles = [Patch(facecolor='#C7CEBA', label='相交DEM像元剖面'),
               Line2D([],[],color='#9F7841',ls=':',marker='^',label='航段最高地形'),
               Line2D([],[],color='#246590',label='爬升 / 巡航'),
               Line2D([],[],color='#B36D32',ls='--',label='下降'),
               Line2D([],[],color='#333333',marker='o',ls='',label='节点表地面海拔'),
               Line2D([],[],color='#AE5674',marker='D',ls='',label='节点作业高度')]
    fig.suptitle('① 地形与飞行高度剖面', fontsize=19, y=.985)
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5,.958), ncols=6, frameon=False, fontsize=9)
    fig.text(.075,.025,'地形按全部相交闭像元绘制；仅边界或角点接触的像元以点保留。节点表与DEM高程分别表示。\n'
             '巡航海拔 = 本航段最高地形 + 50 m；服务区作业高度为节点地面 + 30 m。示意为模型航迹，非实测。', fontsize=9, color='#555555')
    save(fig, output, '01_地形与飞行高度剖面')
    write_csv(output/'tables/01_剖面像元明细.csv', all_cells)
    write_csv(output/'tables/01_剖面航段核验.csv', segments)
    return {'selected_q1': [far['service'], high['service']], 'selected_q2': multi['sortie'],
            'segments': len(segments), 'cells': len(all_cells), 'geometry_passed': True}


def plot_energy(q1, q2, inputs, physics, output):
    boxes = {b.id: b for b in inputs.boxes}
    routes = {r['service']: r for r in q1['routes']}
    details, totals, summaries = [], [], {}
    for question, scenario in [('Q1', q1['baseline']), ('Q2', q2['main'])]:
        records = scenario['rows'] if question == 'Q1' else scenario['sorties']
        for rec in records:
            d = inputs.drones[rec['drone']]
            if question == 'Q1':
                mass = math.fsum(boxes[b].mass for b in rec['box_ids'])
                route = routes[rec['service']]
                legs = [(Leg(**route['outbound']), mass, False), (Leg(**route['inbound']), 0., True)]
                close(mass, rec['mass_kg'], 'Q1原箱质量')
            else:
                remaining = set(rec['boxes'])
                legs = []
                raw_legs = sorted([r for r in scenario['legs'] if r['sortie'] == rec['sortie']], key=lambda r:r['segment'])
                for j, r in enumerate(raw_legs):
                    mass = math.fsum(boxes[b].mass for b in remaining)
                    close(mass, r['payload_kg'], 'Q2逐段原箱质量')
                    leg = Leg(r['from'],r['to'],r['distance_m'],r['cruise_altitude_m'],r['climb_m'],r['descent_m'])
                    close(leg_energy(d,leg,mass,physics['gravity_m_s2']), r['energy_kwh'], 'Q2保存航段能耗')
                    final = j == len(raw_legs)-1
                    require((r['to']=='O01') == final, '最终返航定义不一致')
                    if final:
                        require(not remaining, '最终返航仍有未交付箱')
                    legs.append((leg, mass, final))
                    remaining = {b for b in remaining if boxes[b].service != r['to']}
                require(not remaining, '架次货箱未交齐')
            parts = [0., 0., 0., 0.]
            for index, (leg, mass, final) in enumerate(legs, 1):
                h, u = energy_parts(d, leg, mass, physics['gravity_m_s2'])
                parts[2 if final else 0] += h
                parts[3 if final else 1] += u
                details.append({'question':question,'sortie':rec['sortie'],'drone':d.id,'segment':index,
                                'from':leg.start,'to':leg.end,'payload_kg':mass,'distance_m':leg.distance,
                                'climb_m':leg.climb,'final_return':final,'horizontal_kwh':h,'climb_kwh':u,'total_kwh':h+u})
            close(math.fsum(parts), rec['energy_kwh'], '架次能耗分项总和')
            totals.append({'question':question,'sortie':rec['sortie'],'drone':d.id,
                           'delivery_horizontal_kwh':parts[0],'delivery_climb_kwh':parts[1],
                           'return_horizontal_kwh':parts[2],'return_climb_kwh':parts[3],
                           'total_kwh':math.fsum(parts)})
        rows = [r for r in totals if r['question']==question]
        keys = ['delivery_horizontal_kwh','delivery_climb_kwh','return_horizontal_kwh','return_climb_kwh']
        values = np.array([[r[k] for k in keys] for r in rows])
        overall = values.sum(axis=0)
        close(float(overall.sum()), scenario['summary']['energy_kwh'], '全方案总能耗')
        labels = ['去程水平','去程爬升','返程水平','返程爬升'] if question=='Q1' else ['配送航段水平','配送航段爬升','最终返航水平','最终返航爬升']
        fig, (ax, share) = plt.subplots(2,1,figsize=(15,7.4),gridspec_kw={'height_ratios':[5,1]})
        fig.subplots_adjust(left=.065,right=.985,top=.85,bottom=.19,hspace=.53)
        x=np.arange(len(rows));bottom=np.zeros(len(rows))
        for j, (label,color) in enumerate(zip(labels,ENERGY_COLORS)):
            ax.bar(x, values[:,j],bottom=bottom,color=color,label=label,width=.73)
            bottom+=values[:,j]
        for xx,r in zip(x,rows):
            ax.text(xx,r['total_kwh']+.05,f"{r['total_kwh']:.2f}",ha='center',fontsize=7 if question=='Q2' else 9)
        ticklabels=[f"{r['sortie'].replace('Q1-','').replace('Q2-','')}\n{r['drone']}型" for r in rows]
        ax.set_xticks(x,ticklabels,rotation=0 if question=='Q2' else 45,ha='center' if question=='Q2' else 'right',fontsize=8)
        for tick,r in zip(ax.get_xticklabels(), rows):
            tick.set_color(TYPE_COLORS[r['drone']])
        ax.set_ylim(0,float(bottom.max())*1.18);ax.set_xlim(-.8,len(rows)-.2)
        ax.set_ylabel('架次能耗 / kWh');ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
        left=0.
        for amount,color in zip(overall,ENERGY_COLORS):
            percent=100*amount/overall.sum()
            share.barh(0,percent,left=left,color=color,height=.48)
            if percent>=5:
                share.text(left+percent/2,0,f'{percent:.1f}%',ha='center',va='center',fontsize=10)
            left+=percent
        share.set_xlim(0,100);share.set_yticks([0],['整体构成']);share.set_xticks([0,25,50,75,100],['0%','25%','50%','75%','100%'])
        share.spines[['left','bottom']].set_visible(False)
        legendlabels=[f'{s}：{e:.3f} kWh（{100*e/overall.sum():.1f}%）' for s,e in zip(labels,overall)]
        fig.legend([Patch(color=c) for c in ENERGY_COLORS],legendlabels,ncols=2,loc='upper center',bbox_to_anchor=(.5,.94),frameon=False,fontsize=10)
        fig.suptitle(f"② {'问题一' if question=='Q1' else '问题二'}主方案能耗构成 · {len(rows)}架次 · 合计 {overall.sum():.3f} kWh",fontsize=18,y=.995)
        fig.text(.065,.055,'水平与爬升为模型计算分项；沿用公式审计F09、F10（Zhang附录A(A1)、C(C1)的条件推导）。\n'
                 '仅保留爬升势能附加项，未做实测标定。柱顶为总能耗；架次标签颜色：A蓝、B绿、C玫红。',fontsize=9,color='#555555')
        name='02a_问题一能耗构成' if question=='Q1' else '02b_问题二能耗构成'
        save(fig,output,name)
        summaries[question]={'sorties':len(rows),'energy_kwh':float(overall.sum()),
                             'components_kwh':dict(zip(labels,overall.tolist())),
                             'climb_percent':float(100*(overall[1]+overall[3])/overall.sum())}
    write_csv(output/'tables/02_逐航段能耗分解.csv',details)
    write_csv(output/'tables/02_逐架次能耗分解.csv',totals)
    return summaries


def plot_deadlines(q2, data, output):
    boxes={b.id:b for b in data.base.boxes}
    rows=[]
    require(Counter(r['box'] for r in q2['main']['deliveries']) == Counter(boxes.keys()), '80箱交付不完整或重复')
    for rec in q2['main']['deliveries']:
        timing=data.timing[rec['box']]
        require(timing.hard==rec['hard_s'], '保存硬时限与原始数据不一致')
        if timing.hard is None:
            continue
        box=boxes[rec['box']]
        medical=box.category=='医疗物资'
        category='医疗+首批' if medical and timing.first else ('仅医疗' if medical else '仅首批')
        slack=timing.hard-rec['completion_s']
        rows.append({'box':rec['box'],'service':box.service,'sortie':rec['sortie'],'category':category,
                     'hard_deadline_s':timing.hard,'completion_s':rec['completion_s'],'slack_s':slack,
                     'slack_min':slack/60,'violation':slack<0})
    rows.sort(key=lambda r:(r['slack_s'],r['box']))
    require(len(rows)==31 and len({r['box'] for r in rows})==31,'硬时限箱必须为31个')
    colors={'仅医疗':'#4477AA','仅首批':'#228833','医疗+首批':'#AA6677'}
    fig,ax=plt.subplots(figsize=(12,12.4))
    fig.subplots_adjust(left=.225,right=.93,top=.88,bottom=.085)
    x=[r['slack_min'] for r in rows]
    ax.barh(range(len(rows)),x,color=['#CC3311' if r['violation'] else colors[r['category']] for r in rows],height=.7)
    ax.set_yticks(range(len(rows)),[r['box'] for r in rows],fontsize=10)
    ax.invert_yaxis();ax.axvline(0,color='#333333',lw=1)
    span=max(max(x)-min(x),1)
    for i,r in enumerate(rows):
        ax.text(r['slack_min']+.013*span,i,f"{r['slack_min']:.2f}",va='center',fontsize=9)
    ax.set_xlim(min(0,min(x))-.025*span,max(x)+.13*span)
    ax.set_xlabel('有效硬截止 - 交付完成 / min（越小越紧迫）');ax.set_ylabel('货箱编号（前缀为服务区）')
    ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
    counts=Counter(r['category'] for r in rows)
    fig.legend([Patch(color=c) for c in colors.values()],[f'{c}（{counts[c]}箱）' for c in colors],
               loc='upper center',bbox_to_anchor=(.56,.94),ncols=3,frameon=False)
    fig.suptitle('⑤ 问题二硬时限裕量排序',fontsize=19,y=.982)
    fig.text(.225,.90,f"最紧：{rows[0]['box']}，裕量 {rows[0]['slack_s']:.3f} s = {rows[0]['slack_min']:.2f} min",fontsize=11,color='#A14D3E')
    fig.text(.225,.027,'双重身份采用两项截止中更早者；按未四舍五入的秒值核验，图中分钟保留两位小数。\n'
             '显示主方案全部31个硬时限箱；颜色表示时限身份，负裕量如出现则用红色显示。',fontsize=9,color='#555555')
    save(fig,output,'05_硬时限裕量排序')
    write_csv(output/'tables/05_硬时限裕量.csv',rows)
    return {'hard_boxes':len(rows),'identity_counts':dict(counts),'minimum':rows[0],
            'violations':sum(r['violation'] for r in rows)}


def plot_matrix(q1, inputs, output):
    services=sorted(inputs.services)
    ss=sorted(q1['sensitivity'],key=lambda s:s['reserve'])
    require([round(s['reserve']*100) for s in ss]==list(range(5,46,5)),'需要5%-45%九档')
    values=np.full((len(ss),len(services)),np.nan)
    rows=[]
    for i,s in enumerate(ss):
        failed=set(s['infeasible_services'])
        for j,sid in enumerate(services):
            batches=[r for r in s['rows'] if r['service']==sid]
            delivered=Counter(b for r in batches for b in r['box_ids'])
            expected=Counter(b.id for b in inputs.boxes if b.service==sid)
            feasible=sid not in failed
            require((delivered==expected)==feasible, '局部完整交付与不可行标记不一致')
            if feasible:
                values[i,j]=len(batches)
            rows.append({'reserve':s['reserve'],'service':sid,'feasible':feasible,
                         'minimum_sorties':len(batches) if feasible else None,
                         'expected_boxes':sum(expected.values()),'whole_scenario_feasible':s['feasible']})
        require(s['feasible']==(not failed),'全局与局部可行性不一致')
        if s['feasible']:
            close(float(values[i].sum()),s['summary']['sorties'],'矩阵架次合计')
        else:
            require(s['summary']['sorties'] is None,'不可行情景不应有完整总架次')
    expected_failures={40:{'S004','S008'},45:{'S002','S003','S004','S008','S012'}}
    for rho,expected in expected_failures.items():
        require(set(next(s for s in ss if round(s['reserve']*100)==rho)['infeasible_services'])==expected,'高余量诊断不匹配')
    for j in range(len(services)):
        v=values[:,j];finite=v[np.isfinite(v)]
        require(np.all(np.diff(finite)>=0),'最少架次随余量下降')
        if np.any(np.isnan(v)):
            require(np.all(np.isnan(v[np.where(np.isnan(v))[0][0]:])),'不可行后恢复可行')
    maximum=int(np.nanmax(values))
    palette=plt.get_cmap('Blues')(np.linspace(.16,.78,maximum))
    cmap=ListedColormap(palette);cmap.set_bad('#F2C6C0')
    norm=BoundaryNorm(np.arange(.5,maximum+1.5),maximum)
    fig,ax=plt.subplots(figsize=(14,7.1))
    fig.subplots_adjust(left=.075,right=.925,top=.85,bottom=.18)
    im=ax.imshow(np.ma.masked_invalid(values),cmap=cmap,norm=norm,aspect='auto')
    for i in range(len(ss)):
        for j in range(len(services)):
            v=values[i,j]
            ax.text(j,i,'×' if np.isnan(v) else str(int(v)),ha='center',va='center',fontsize=13,
                    color='#A53125' if np.isnan(v) else ('white' if v>maximum*.72 else '#18364B'))
    ax.set_xticks(range(len(services)),services,rotation=35,ha='right')
    ax.set_yticks(range(len(ss)),[f"{s['reserve']:.0%}"+('（基准）' if s['reserve']==.2 else '') for s in ss])
    ax.set_xlabel('服务区');ax.set_ylabel('返航安全余量')
    ax.set_xticks(np.arange(-.5,len(services),1),minor=True)
    ax.set_yticks(np.arange(-.5,len(ss),1),minor=True)
    ax.grid(which='minor',color='white',lw=1.6);ax.tick_params(which='minor',bottom=False,left=False)
    bi=next(i for i,s in enumerate(ss) if s['reserve']==.2)
    ax.add_patch(Rectangle((-.5,bi-.5),len(services),1,fill=False,lw=2.2,edgecolor='#333333',clip_on=False))
    cb=fig.colorbar(im,ax=ax,fraction=.025,pad=.025,ticks=range(1,maximum+1));cb.set_label('该服务区最少架次')
    fig.suptitle('⑥ 安全余量与服务区配送可行性',fontsize=19,y=.975)
    fig.text(.075,.895,'问题一独立组批 · 5%-45%，每5个百分点 · 15个服务区 · 数字为完整交付该区所需最少架次',fontsize=11)
    fig.text(.075,.045,'×：该服务区无法完整配送；任一区不可行，则该余量下全场景不可行。\n'
             '40%：S004、S008不可行；45%另增加S002、S003、S012。此图不含问题二的时限、机队或电池调度约束。',fontsize=10,color='#555555')
    save(fig,output,'06_安全余量服务区可行性矩阵')
    write_csv(output/'tables/06_服务区可行性矩阵.csv',rows)
    return {'cells':len(rows),'range_percent':list(range(5,46,5)),
            'infeasible_cells':int(np.isnan(values).sum()),'checks_passed':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='outputs/visualizations')
    args=parser.parse_args()
    output=Path(args.output)
    if not output.is_absolute():
        output=PROJECT/output
    protected=[PROJECT.parent/'数据',PROJECT.parent/'写作',PROJECT/'outputs/q1',PROJECT/'outputs/q2',PROJECT/'outputs/q1_sensitivity_05_45']
    require(not any(output.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(output.resolve()) for p in protected),
            '输出目录不得覆盖原始附件、论文或既有结果目录')
    q1_path=PROJECT/'outputs/q1_sensitivity_05_45/tables/results.json'
    q2_path=PROJECT/'outputs/q2/tables/results.json'
    q1=json.loads(q1_path.read_text(encoding='utf-8'))
    q2=json.loads(q2_path.read_text(encoding='utf-8'))
    require(q2['main']['verification']['passed'], '问题二主方案未通过原核验')
    config=q1['metadata']['config'];physics=config['physics']
    for k in ['gravity_m_s2','clearance_m','service_height_m']:
        close(physics[k],q2['metadata']['config']['physics'][k],'两问物理口径')
    paths={str(q1_path):file_hash(q1_path),str(q2_path):file_hash(q2_path)}
    for result in (q1,q2):
        for name,h in result['metadata']['input_sha256'].items():
            require(file_hash(Path(name))==h,f'原始输入已变更，禁止复用旧结果: {name}')
            paths[name]=h
    # Also protect all saved solution artifacts and the existing writing project.
    preserved={str(f):file_hash(f) for folder in [PROJECT/'outputs/q1',PROJECT/'outputs/q2',
               PROJECT/'outputs/q1_sensitivity_05_45',PROJECT.parent/'写作'] for f in folder.rglob('*')
               if f.is_file() and not f.name.startswith('~$')}
    data=load_scheduling_inputs(PROJECT/config['paths']['data_root'],PROJECT/config['paths']['template'])
    terrain=Terrain(data.base.dem_path,data.base.origin)
    font=PROJECT/config['paths']['font']
    require(font.exists(),'缺少配置中文字体')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':[font_manager.FontProperties(fname=str(font)).get_name(),'DejaVu Sans'],
                         'font.size':11,'axes.unicode_minus':False,'pdf.fonttype':42,
                         'axes.spines.top':False,'axes.spines.right':False})
    for folder in ['figures','tables','logs']:
        (output/folder).mkdir(parents=True,exist_ok=True)
    checks={}
    checks['profile']=plot_profiles(q1,q2,data.base,terrain,physics,output)
    checks['energy']=plot_energy(q1,q2,data.base,physics,output)
    checks['deadline']=plot_deadlines(q2,data,output)
    checks['matrix']=plot_matrix(q1,data.base,output)
    require(checks['deadline']['violations']==0,'已绘出硬时限违反，请检查方案')
    for name,h in {**paths,**preserved}.items():
        require(file_hash(Path(name))==h,f'受保护文件被更改: {name}')
    checks['preserved_files']=len(preserved)
    checks['passed']=True
    checks['input_sha256']=paths
    checks['code_sha256']={str(Path(__file__)):file_hash(Path(__file__))}
    checks['generated_utc']=datetime.now(timezone.utc).isoformat()
    checks['python']=platform.python_version()
    checks['dependencies']={p:importlib.metadata.version(p) for p in ['numpy','matplotlib','Pillow','openpyxl']}
    checks['physics']=physics
    checks['optimizer_invoked']=False
    (output/'logs/validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    names=['01_地形与飞行高度剖面','02a_问题一能耗构成','02b_问题二能耗构成','05_硬时限裕量排序','06_安全余量服务区可行性矩阵']
    minimum=checks['deadline']['minimum']
    text=['# 扩展图表说明','', '本批复用已保存主方案，仅重建地形剖面和派生统计量，没有重新优化。',
          '',f"代表剖面：O01→{checks['profile']['selected_q1'][0]}、O01→{checks['profile']['selected_q1'][1]}、{checks['profile']['selected_q2']}完整往返。",
          f"最紧硬时限箱为{minimum['box']}，裕量{minimum['slack_s']:.3f}秒（{minimum['slack_min']:.4f}分钟）；31箱均满足硬时限。",'']
    for q,s in checks['energy'].items():
        text.append(f"{q}共{s['sorties']}架次，运输能耗{s['energy_kwh']:.9f} kWh，爬升附加项占{s['climb_percent']:.4f}%。")
    text+=['','40%时S004、S008不可行；45%时S002、S003、S004、S008、S012不可行。矩阵其他单元格只表示该服务区的独立组批可行性，不代表全场景可行。',
           '', '## 公式与口径','',
           '水平与爬升分项直接使用既有公式审计F09、F10：Zhang等附录A(A1)的能量/航程关系具体化，附录C(C1)爬升功率分量积分后按题给效率折算。全可用电量航程标定与仅保留爬升势能附加项均为已有假设，不是完整实测飞行模型。',
           '硬时限裕量 Δ_b=H_b-C_b，由审计F25的有效截止与完成时刻直接相减，单位秒；展示时除以60换算分钟。负值表示违反硬截止。能耗占比为分项能量除以总能量，是统计定义，不添加新物理假设。',
           '沿途DEM按线段与每个闭像元的相交区间绘制。共享边界可能存在重叠高程，保留各像元；角点零长度接触用点显示。节点海拔取节点表，不用DEM替换。',
           '可行性矩阵中的数字为已有词典序精确组批方案的服务区架次数；先核对该区全部原始货箱恰好交付一次，再显示数字。不可行格不填零。',
           '', '## 复现与核验','', '`python scripts/plot_extended.py`；任意工作目录可使用入口的绝对路径。',
           '图表为300 dpi PNG与内嵌字体矢量PDF；CSV保留未四舍五入计算值。logs/validation.json记录来源哈希、依赖版本、数值校验和既有文件保护校验。',
           '逐航段分项相加、架次总量与完整方案能耗逐级核验；剖面最高高程和飞行几何对照保存值；31个硬时限身份从原始数据复核；135个矩阵单元格检查交付完整性、不可行诊断与架次数单调性。']
    for name in names:
        text+=['',f'## {name}','',f'![{name}](figures/{name}.png)']
    (output/'图表说明.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in checks.items() if k in ['profile','energy','deadline','matrix','passed','preserved_files']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
