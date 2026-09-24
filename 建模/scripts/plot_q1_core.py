"""Seven question-focused Q1 figures; audited snapshots in, no optimizer invoked."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
_runtime = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (__name__ == '__main__' and (PROJECT/'.runtime/python').is_dir()
        and _runtime.exists() and Path(sys.executable).resolve() != _runtime.resolve()):
    import subprocess
    raise SystemExit(subprocess.call([str(_runtime), '-X', 'utf8', str(Path(__file__).resolve()), *sys.argv[1:]]))
for folder in (PROJECT/'src',PROJECT/'.runtime/python'):
    if folder.is_dir():sys.path.insert(0,str(folder))
if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')

import argparse
from collections import Counter
import csv
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import math
import shutil
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MaxNLocator
import numpy as np

from q1_figure_data import prepare,validate_bundle,require,close,TIME_KEYS

TYPES={'A':'#4477AA','B':'#228833','C':'#CC6677'}
CARGO={'医疗物资':'#D48398','饮用水':'#82B8D2','应急食品':'#E9C16C','生活卫生用品':'#9ABEA4'}
TIME_COLORS=['#7B8FA1','#A7C1D5','#4477AA','#D6B583','#B8794E']
TIME_NAMES=['准备','装载','往返飞行','基础交接','逐箱交接']
ENERGY_COLORS=['#4477AA','#DAA65A']
ORDER_COLORS=['#4477AA','#D28B4B','#458B78']
SERV_COLORS=['#332288','#88CCEE','#44AA99','#117733','#999933','#DDCC77','#CC6677','#882255','#AA4499',
             '#0077BB','#EE7733','#009988','#CC3311','#BBBBBB','#444444']
NAMES=['01_各服务区货箱组批方案','02_批次装载与返航安全','03_各服务区组批统计',
       '04_目标优先级权衡','05_安全余量与最大安全载荷','06_安全余量与组批结果','07_安全余量与服务区可行性']


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


class Figures:
    def __init__(self,bundle,output,dpi):
        self.bundle=bundle;self.t=bundle['tables'];self.output=output;self.dpi=dpi
        self.batches=self.t['02_批次约束与分项'];self.services=[s['service'] for s in self.t['03_服务区统计']]
        self.exports=[]

    def header(self,fig,title,subtitle):
        fig.suptitle(title,fontsize=22,y=.975)
        fig.text(.5,.975-.60/fig.get_figheight(),subtitle,ha='center',fontsize=12,color='#50616B')

    def save(self,fig,index,foot):
        fig.text(.045,.022,foot,fontsize=10,color='#4A5963',va='bottom')
        name=NAMES[index]
        for ext in ['png','pdf']:
            path=self.output/'figures'/f'{name}.{ext}'
            fig.savefig(path,dpi=self.dpi,bbox_inches='tight',facecolor='white')
        plt.close(fig)
        self.exports.append(name)
        print('已生成 '+name,flush=True)

    def batches_plot(self):
        fig,ax=plt.subplots(figsize=(20,12.5))
        fig.subplots_adjust(left=.12,right=.98,top=.84,bottom=.10)
        self.header(fig,'① 各服务区货箱组批方案','20%安全余量 · 15个服务区 · 18个批次 · 80箱完整交付')
        fig.legend(handles=[Patch(facecolor=c,label=k) for k,c in CARGO.items()],ncols=4,
                   loc='upper center',bbox_to_anchor=(.5,.904),frameon=False,fontsize=12)
        cells=self.t['01_逐箱组批']
        for i,b in enumerate(self.batches):
            if int(b['service'][1:])%2:
                ax.axhspan(i-.46,i+.46,color='#F0F4F6',zorder=0)
            for c in [c for c in cells if c['sortie']==b['sortie']]:
                x=c['box_position'];ax.barh(i,1,left=x,height=.74,color=CARGO[c['category']],edgecolor='white',lw=1.6)
                sid,kind,num=c['box_id'].split('-')
                ax.text(x+.5,i,f'{sid}-\n{kind}-{num}',ha='center',va='center',fontsize=10,color='#25313A')
            for x,label,color in [(8.65,f"{b['drone']}型",TYPES[b['drone']]),(9.8,f"{b['mass_kg']:.0f}",'#25313A'),
                                  (11.15,f"{b['volume_m3']:.3f}",'#25313A'),(12.35,str(b['box_count']),'#25313A')]:
                ax.text(x,i,label,ha='center',va='center',fontsize=11,color=color)
        for x,label in [(8.65,'机型'),(9.8,'质量 / kg'),(11.15,'体积 / m³'),(12.35,'箱数')]:
            ax.text(x,-.95,label,ha='center',fontsize=11,color='#25313A',fontweight='bold')
        ax.set(xlim=(0,12.85),ylim=(17.65,-1.35),yticks=range(18),
               yticklabels=[f"{b['service']} · 批{b['sortie'][-2:]}" for b in self.batches],xticks=range(9),xlabel='组内货箱数（每格1个完整货箱）')
        ax.tick_params(axis='y',length=0,labelsize=11)
        ax.spines[['top','right','left']].set_visible(False)
        ax.spines['bottom'].set_bounds(0,8)
        self.save(fig,0,'格宽表示箱数，不表示质量或体积；格内两行连读为原始箱号。行号对应完整架次编号 Q1-服务区-批次。\n货箱唯一性、完整交付及批次装载已从原始逐箱数据独立核验。')

    def constraints_plot(self):
        fig,axs=plt.subplots(1,3,figsize=(19,12),sharey=True)
        fig.subplots_adjust(left=.15,right=.97,top=.80,bottom=.12,wspace=.24)
        self.header(fig,'② 各批次装载与返航安全检查','行序与货箱组批图完全一致 · 18个批次均满足三类限制')
        specs=[('mass_utilization','载质量利用率',100),('volume_utilization','装载体积利用率',100),('return_soc','返航SOC',20)]
        for ax,(key,title,limit) in zip(axs,specs):
            values=np.array([r[key]*100 for r in self.batches])
            if key=='return_soc':ax.axvspan(0,20,color='#FCE6E4')
            ax.barh(range(18),values,color=[TYPES[r['drone']] for r in self.batches],height=.63)
            for i,v in enumerate(values):
                ax.text(v-2 if v>90 else v+1.4,i,f'{v:.1f}%',va='center',fontsize=10,
                        ha='right' if v>90 else 'left',color='white' if v>90 else '#263844')
            ax.axvline(limit,color='#B33A3A',ls='--',lw=1.4)
            ax.set_title(title,fontsize=15,pad=25)
            ax.text(limit,.995,'最低20%' if key=='return_soc' else '上限100%',transform=ax.get_xaxis_transform(),
                    ha='center',va='bottom',fontsize=10,color='#B33A3A')
            ax.set(xlim=(0,115),xticks=range(0,101,20),xlabel='百分比 / %',ylim=(17.7,-.7))
            ax.grid(axis='x',alpha=.16);ax.set_axisbelow(True)
        axs[0].set(yticks=range(18),yticklabels=[f"{b['service']} · 批{b['sortie'][-2:]} · {b['drone']}型" for b in self.batches])
        fig.legend(handles=[Patch(facecolor=c,label=f'{g}型') for g,c in TYPES.items()],ncols=3,frameon=False,
                   loc='upper center',bbox_to_anchor=(.5,.905))
        minimum=min(b['return_soc'] for b in self.batches)*100
        self.save(fig,1,f'载质量利用率=实际质量/机型额定载质量；体积利用率=实际体积/机型装载体积。返航SOC最低为{minimum:.3f}%。\n质量上限与能量安全要求分开检查；图中百分比为展示舍入，实际可行性使用原精度数值判断。')

    def services_plot(self):
        rows=self.t['03_服务区统计'];y=np.arange(15)
        fig,axs=plt.subplots(1,4,figsize=(24,10),gridspec_kw={'width_ratios':[1.3,.8,1.1,.8]})
        fig.subplots_adjust(left=.065,right=.975,top=.83,bottom=.21,wspace=.29)
        self.header(fig,'③ 各服务区组批统计指标','20%安全余量主方案 · 同一服务区顺序 · 各指标独立刻度')
        left=np.zeros(15)
        for key,c,label in zip(TIME_KEYS,TIME_COLORS,TIME_NAMES):
            v=np.array([r[key]/60 for r in rows]);axs[0].barh(y,v,left=left,color=c,label=label,height=.64);left+=v
        for i,v in enumerate(left):axs[0].text(v+1,i,f'{v:.1f}',va='center',fontsize=9)
        axs[0].set(xlim=(0,left.max()*1.17),xlabel='累计作业时间 / min',title='(a) 作业时间分项')
        axs[0].legend(loc='upper left',bbox_to_anchor=(0,-.12),ncols=2,frameon=False,fontsize=10)
        left=np.zeros(15)
        for g in 'ABC':
            v=np.array([r[g] for r in rows]);axs[1].barh(y,v,left=left,color=TYPES[g],label=f'{g}型',height=.64);left+=v
        for i,v in enumerate(left):axs[1].text(v+.05,i,f'{v:.0f}',va='center',fontsize=10)
        axs[1].set(xlim=(0,left.max()+.5),xticks=range(int(left.max())+1),xlabel='往返架次数',title='(b) 服务架次')
        axs[1].legend(loc='upper left',bbox_to_anchor=(0,-.12),ncols=3,frameon=False,fontsize=10)
        left=np.zeros(15)
        for key,c,label in [('horizontal_kwh',ENERGY_COLORS[0],'水平飞行'),('climb_kwh',ENERGY_COLORS[1],'爬升附加')]:
            v=np.array([r[key] for r in rows]);axs[2].barh(y,v,left=left,color=c,label=label,height=.64);left+=v
        for i,v in enumerate(left):axs[2].text(v+.05,i,f'{v:.3f}',va='center',fontsize=9)
        axs[2].set(xlim=(0,left.max()*1.21),xlabel='总运输能耗 / kWh',title='(c) 运输能耗分项')
        axs[2].legend(loc='upper left',bbox_to_anchor=(0,-.12),ncols=2,frameon=False,fontsize=10)
        counts=np.array([[r[g] for g in 'ABC'] for r in rows])
        im=axs[3].imshow(counts,cmap=ListedColormap(['#F0F3F5','#C3D9E8','#6E9FBC']),vmin=0,vmax=2,aspect='auto')
        for i in y:
            for j in range(3):axs[3].text(j,i,str(counts[i,j]),ha='center',va='center',fontsize=12)
        totals=[int(counts[:,i].sum()) for i in range(3)]
        axs[3].set(xticks=range(3),xticklabels=[f'{g}型' for g in 'ABC'],xlabel='机型（格内为架次数）',title='(d) 主方案机型选用')
        axs[3].text(.5,-.155,f'全方案 A/B/C = {totals[0]}/{totals[1]}/{totals[2]} 架次',transform=axs[3].transAxes,ha='center',fontsize=11)
        for i,ax in enumerate(axs):
            ax.set(yticks=y,yticklabels=self.services,ylim=(14.7,-.7))
            ax.tick_params(axis='y',length=0,labelsize=10)
            if i<3:ax.grid(axis='x',alpha=.15);ax.set_axisbelow(True)
            ax.set_title(ax.get_title(),fontsize=14,pad=18)
        self.save(fig,2,'累计作业时间为各架次耗时之和，不是多机并行完工时间；架次不代表实体机数量。\n能耗分项沿用文献依据下的简化水平项与爬升附加项，未新增悬停能耗；机型选择受当前目标顺序与约束共同决定。')

    def objectives_plot(self):
        rows=sorted([r for r in self.t['04_全部目标顺序对照'] if r['main_plot']],key=lambda r:r['main_index'])
        fig,axs=plt.subplots(1,3,figsize=(18,8.5))
        fig.subplots_adjust(left=.065,right=.975,top=.81,bottom=.30,wspace=.30)
        self.header(fig,'④ 不同目标优先级的权衡','分别覆盖架次、能耗和时间优先 · 安全余量与全部物理约束相同')
        labels=['架次优先\n架次→能耗→时间','能耗优先\n能耗→架次→时间','时间优先\n时间→架次→能耗']
        for ax,(key,title,fmt) in zip(axs,[('sorties','往返架次数','d'),('energy_kwh','总运输能耗 / kWh','.6f'),('operation_h','累计作业时间 / h','.6f')]):
            values=[r[key] for r in rows];ax.bar(range(3),values,color=ORDER_COLORS,width=.6)
            for i,v in enumerate(values):ax.text(i,v+max(values)*.025,format(v,fmt),ha='center',fontsize=11)
            ax.set(xticks=range(3),xticklabels=labels,ylim=(0,max(values)*1.19),title=title)
            ax.tick_params(axis='x',labelsize=10,pad=10);ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
            if key=='sorties':ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        energy=rows[1]
        fig.text(.5,.182,f"能耗优先相对主方案：架次 +{energy['delta_sorties']}  |  能耗 {energy['delta_energy_kwh']:.6f} kWh（{energy['delta_energy_pct']:.4f}%）"
                 f"  |  累计作业时间 +{energy['delta_operation_min']:.3f} min",ha='center',fontsize=13,color='#9B5A25',
                 bbox={'boxstyle':'round,pad=.6','facecolor':'#FFF6E8','edgecolor':'none'})
        fig.text(.5,.117,'架次优先与时间优先取得相同最优汇总指标；仍分别展示，不能省略时间优先案例。',ha='center',fontsize=12)
        self.save(fig,3,'主方案采用“架次→能耗→时间”词典序；前一指标最优后才优化后一指标。各纵轴从0起，微小差异用数值注明。\n全部6种排列已补算并经90组服务区MILP核验；本图为三种首要目标对照，不代表完整Pareto前沿。')

    def payload_plot(self):
        fig,axs=plt.subplots(1,3,figsize=(20,10))
        fig.subplots_adjust(left=.055,right=.98,top=.84,bottom=.39,wspace=.23)
        self.header(fig,'⑤ 安全余量与不同机型最大安全载荷','5%-45%，每5个百分点 · 每种机型包含全部15个服务区 · 20%为基准')
        capacities=self.t['05_安全载荷'];rhos=list(range(5,46,5))
        handles=[]
        for j,sid in enumerate(self.services):
            handles.append(Line2D([],[],color=SERV_COLORS[j],ls=['-','--',':'][j//5],lw=1.7,label=sid))
        for ax,g in zip(axs,'ABC'):
            rr=[r for r in capacities if r['drone']==g];rated=rr[0]['rated_payload_kg']
            ax.axhline(rated,color='#47545E',lw=1.1,ls='--');ax.axvline(20,color='#555555',ls=':',lw=1.1)
            for j,sid in enumerate(self.services):
                values=[r['safe_payload_kg'] for r in rr if r['service']==sid]
                ax.plot(rhos,[np.nan if v is None else v for v in values],color=SERV_COLORS[j],
                        ls=['-','--',':'][j//5],lw=1.5,marker='o',ms=3.2,alpha=.92)
            ax.set(xlim=(3,47),ylim=(0,rated*1.15),xticks=rhos,xlabel='返航安全余量 / %',ylabel='最大安全载荷 / kg',title=f'{g}型 · 额定载质量 {rated:g} kg')
            ax.grid(alpha=.16);ax.set_title(ax.get_title(),fontsize=15,pad=12)
            ax.text(.025,.97,'同值曲线原位重合',transform=ax.transAxes,va='top',fontsize=9,color='#596772')
            position=ax.get_position();strip=fig.add_axes([position.x0,.285,position.width,.035])
            strip.set(xlim=(3,47),ylim=(-1,1));strip.axis('off')
            strip.axhspan(-.8,.8,color='#FBEDED')
            missing=[]
            for rho in rhos:
                ids=[r['service'] for r in rr if round(r['reserve']*100)==rho and not r['reachable']]
                if ids:
                    strip.text(rho,0,f'×{len(ids)}',color='#B33636',ha='center',va='center',fontsize=10)
                    missing.append(f"{rho}%：{','.join(ids)}")
            fig.text(position.x0,.325,'不可达条带（×n：该档有n个服务区空载不可达）',fontsize=9,color='#A23C3C')
            details='\n'.join(missing) or '所有档位均空载可达'
            fig.text(position.x0,.265,details,va='top',fontsize=8.5,color='#8D3D3D',linespacing=1.6)
        fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.09),ncols=8,frameon=False,fontsize=10)
        self.save(fig,4,'不可达用空值断开曲线，绝不填成0 kg；相同曲线不作人为偏移。完整405个载荷值及限制原因保存在绘图数据中。\n最大安全载荷仅含质量与能量上限，不等于不可拆货箱的实际可装质量；整批配送可行性见图⑦。')

    def sensitivity_plot(self):
        rows=self.t['06_余量组批汇总'];rhos=np.array([r['reserve']*100 for r in rows])
        fig,axs=plt.subplots(2,2,figsize=(17,12))
        fig.subplots_adjust(left=.07,right=.97,top=.85,bottom=.14,wspace=.22,hspace=.42)
        self.header(fig,'⑥ 安全余量对货箱组批结果的影响','每档重新优化所得的已保存结果 · 全局不可行时不展示部分配送总量')
        for ax,(key,label,digits) in zip(axs.flat,[('sorties','(a) 最少往返架次',0),('energy_kwh','(b) 总运输能耗 / kWh',3),('operation_h','(c) 累计作业时间 / h',3)]):
            values=np.array([np.nan if r[key] is None else r[key] for r in rows])
            ax.plot(rhos,values,'o-',color='#4477AA',lw=2,ms=6)
            for x,v in zip(rhos,values):
                if np.isfinite(v):ax.annotate(f'{v:.{digits}f}',(x,v),xytext=(0,9),textcoords='offset points',ha='center',fontsize=10)
            ax.set_ylim(0,float(np.nanmax(values))*1.24);ax.set_title(label,fontsize=15)
            if key=='sorties':ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax=axs[1,1];valid=[r for r in rows if r['feasible']];x=[r['reserve']*100 for r in valid];bottom=np.zeros(len(valid))
        for g in 'ABC':
            v=np.array([r[g] for r in valid]);ax.bar(x,v,bottom=bottom,width=3.3,color=TYPES[g],label=f'{g}型')
            for xx,value,lo in zip(x,v,bottom):
                if value:ax.text(xx,lo+value/2,str(int(value)),ha='center',va='center',color='white',fontsize=11,fontweight='bold')
            bottom+=v
        ax.set(title='(d) 机型架次构成',ylim=(0,bottom.max()*1.24));ax.legend(ncols=3,frameon=False,loc='upper left');ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        for ax in axs.flat:
            ax.axvspan(37.5,47.5,facecolor='#FBE7E6',hatch='///',edgecolor='#E3C6C3',alpha=.5)
            ax.axvline(20,color='#66727A',ls=':',lw=1.2)
            ax.text(42.5,.78,'整体\n不可行',transform=ax.get_xaxis_transform(),ha='center',color='#A23838',fontsize=12)
            ax.set(xlim=(2.5,47.5),xticks=list(range(5,46,5)),xlabel='返航安全余量 / %')
            ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
        self.save(fig,5,'40%：S004、S008不可行；45%：S002、S003、S004、S008、S012不可行。竖虚线为20%基准。\n仅连接已计算档位，不代表档位之间的精确临界余量；能耗与时间受组批及机型变化共同影响，不预设单调性。')

    def feasibility_plot(self):
        rows=self.t['07_服务区可行性'];rhos=list(range(5,46,5))
        z=np.array([[next(r['min_sorties'] for r in rows if round(r['reserve']*100)==rho and r['service']==sid) for sid in self.services] for rho in rhos],dtype=float)
        maximum=int(np.nanmax(z));palette=['#E5F0F5','#ADCBD9','#709EB7','#3D718F','#194861'][:maximum]
        if maximum>5:palette=[plt.get_cmap('Blues')(x) for x in np.linspace(.16,.9,maximum)]
        cmap=ListedColormap(palette);cmap.set_bad('#E6A5A1')
        fig,ax=plt.subplots(figsize=(17,8.5));fig.subplots_adjust(left=.09,right=.88,top=.83,bottom=.14)
        self.header(fig,'⑦ 安全余量—服务区组批可行性矩阵','每格表示该服务区完整配送的最少架次 · 9档余量 × 15个服务区')
        im=ax.imshow(np.ma.masked_invalid(z),cmap=cmap,norm=BoundaryNorm(np.arange(.5,maximum+1.5),maximum),aspect='auto')
        for i in range(9):
            for j in range(15):
                val=z[i,j];ax.text(j,i,'×' if np.isnan(val) else str(int(val)),ha='center',va='center',fontsize=15,
                                 color='#8B1E22' if np.isnan(val) else ('white' if val>=4 else '#173B50'),fontweight='bold' if np.isnan(val) else 'normal')
        ax.add_patch(Rectangle((-.5,2.5),15,1,fill=False,ec='#252A31',lw=2.2,zorder=10))
        ax.set(xticks=range(15),xticklabels=self.services,yticks=range(9),yticklabels=[f'{r}%'+('（基准）' if r==20 else '') for r in rhos],xlabel='服务区',ylabel='返航安全余量')
        ax.set_xticks(np.arange(-.5,15),minor=True);ax.set_yticks(np.arange(-.5,9),minor=True)
        ax.grid(which='minor',color='white',lw=1.6);ax.tick_params(which='both',length=0)
        cax=fig.add_axes([.91,.34,.014,.36]);fig.colorbar(im,cax=cax,ticks=range(1,maximum+1),label='最少架次')
        fig.text(.90,.23,'× 不可行',fontsize=12,color='#8B1E22')
        self.save(fig,6,'格内可行要求该服务区全部货箱恰好交付一次，不能以空载可达性替代。黑框为20%基准。\n40%、45%虽有部分服务区可行，但不存在覆盖全部15个服务区的完整方案，不汇总为全局配送总架次。')

    def run(self):
        for fn in [self.batches_plot,self.constraints_plot,self.services_plot,self.objectives_plot,
                   self.payload_plot,self.sensitivity_plot,self.feasibility_plot]:fn()


def export_data(bundle,output):
    data=output/'data';data.mkdir(exist_ok=True)
    payload=json.dumps(bundle,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    snapshot=data/'plot_data.json';snapshot.write_text(payload,encoding='utf-8')
    (data/'plot_data.sha256').write_text(file_hash(snapshot)+'  plot_data.json\n',encoding='utf-8')
    for name,rows in bundle['tables'].items():
        path=data/f'{name}.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        with path.open(encoding='utf-8-sig',newline='') as f:
            actual=list(csv.DictReader(f))
        require(len(actual)==len(rows),'CSV行数不符')
        for a,r in zip(actual,rows):
            for k,v in r.items():
                require(a[k]==('' if v is None else str(v)),'CSV与绘图快照不一致')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='outputs/q1_core_figures')
    parser.add_argument('--from-bundle',help='仅使用已保存plot_data.json重绘，无需原始附件或求解器')
    parser.add_argument('--font',help='可选中文字体路径')
    parser.add_argument('--dpi',type=int,default=300)
    args=parser.parse_args();require(args.dpi>=150,'DPI至少150')
    output=(PROJECT/args.output).resolve()
    protected_dirs=[PROJECT.parent/'数据',PROJECT.parent/'写作',*[PROJECT/'outputs'/s for s in
                    ['q1','q2','q1_sensitivity_05_45','q1_objective_audit','visualizations','routes','routes_terrain']]]
    require(not any(output.is_relative_to(p.resolve()) or p.resolve().is_relative_to(output) for p in protected_dirs),'输出目录与受保护文件冲突')
    before={str(p):file_hash(p) for d in protected_dirs for p in d.rglob('*') if p.is_file() and not p.name.startswith('~$')}
    if args.from_bundle:
        path=(PROJECT/args.from_bundle).resolve()
        sha=path.with_suffix('.sha256')
        if sha.exists():require(file_hash(path)==sha.read_text(encoding='utf-8').split()[0],'绘图快照哈希不一致')
        bundle=json.loads(path.read_text(encoding='utf-8'));mode='saved_plot_bundle'
        inputs={str(path):file_hash(path)}
    else:
        bundle=prepare(PROJECT);mode='audited_source_results';inputs=bundle['metadata']['input_sha256']
    validation=validate_bundle(bundle)
    font_candidates=[Path(args.font)] if args.font else [Path(bundle['metadata']['font']),Path('C:/Windows/Fonts/msyh.ttc'),Path('C:/Windows/Fonts/simsun.ttc')]
    font=next((p for p in font_candidates if p.is_file()),None)
    require(font is not None,'未找到中文字体，请通过--font指定')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':[font_manager.FontProperties(fname=str(font)).get_name(),'DejaVu Sans'],
                         'font.size':11,'axes.unicode_minus':False,'pdf.fonttype':42,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.labelcolor':'#334551','text.color':'#263844',
                         'axes.titlepad':14,'figure.facecolor':'white'})
    for name in ['figures','data','code','logs']:(output/name).mkdir(parents=True,exist_ok=True)
    export_data(bundle,output)
    figures=Figures(bundle,output,args.dpi);figures.run()
    code_files=[Path(__file__).resolve(),Path(__file__).with_name('q1_figure_data.py').resolve()]
    for p in code_files:
        dest=output/'code'/p.name
        if dest.resolve()!=p:shutil.copy2(p,dest)
    (output/'code/requirements-redraw.txt').write_text('numpy>=2.0\nmatplotlib>=3.9\n',encoding='utf-8')
    for p,h in before.items():require(file_hash(Path(p))==h,'既有文件改变: '+p)
    report={'passed':True,'mode':mode,'generated_utc':datetime.now(timezone.utc).isoformat(),'checks':validation,
            'inputs_sha256':inputs,'code_sha256':{str(p):file_hash(p) for p in code_files},
            'plot_data_sha256':file_hash(output/'data/plot_data.json'),'font':str(font),'font_sha256':file_hash(font),
            'dpi':args.dpi,'protected_files_unchanged':len(before),'csv_roundtrip_checked':True,'optimizer_invoked':False,
            'dependencies':{p:importlib.metadata.version(p) for p in ['numpy','matplotlib','Pillow']},
            'outputs_sha256':{str(p.relative_to(output)):file_hash(p) for p in (output/'figures').iterdir()}}
    (output/'logs/validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    docs=['# 问题一核心图册与重绘说明','',
          '7组图对应具体组批、约束检查、服务区成本、目标权衡及安全余量影响。每图含300 dpi PNG和矢量PDF；没有重新运行优化。',
          '', '## 复现入口','', '`python scripts/plot_q1_core.py`',
          '', '只使用已保存绘图数据重绘（不读取原始附件、不运行优化）：',
          '`python scripts/plot_q1_core.py --from-bundle outputs/q1_core_figures/data/plot_data.json --output outputs/q1_core_redraw`',
          '', '图册内code目录同时保存两份代码副本及重绘依赖清单。迁移后可用配套Python/独立环境执行code/plot_q1_core.py，以--from-bundle指定JSON绝对路径、--output指定输出绝对路径、--font指定可用中文字体。',
          '例如：`python code/plot_q1_core.py --from-bundle D:/path/data/plot_data.json --output D:/path/redraw --font C:/Windows/Fonts/msyh.ttc`。无字体时明确报错，不静默生成缺字图。',
          '', '## 数据与口径','',
          'data/plot_data.json为全部7图的数值快照，包含原始80箱属性、3种机型参数、来源哈希及绘图明细；7个CSV与JSON逐字段核对一致。空值表示不可达或不可行，不能改成零。CSV只是数值明细，绘图以JSON为统一数据源。',
          '01：80个货箱格，横轴单位为箱数；02：18批次质量/额定质量、体积/额定体积与返航SOC；03：15区作业时间5分项、能耗2分项及A/B/C架次。',
          '04：主图覆盖架次、能耗、时间三种首要目标，CSV保留全部6种排列。时间优先与架次优先汇总指标相同；能耗优先为19架次，少耗约0.1647%能量、多用约27.733分钟。',
          '05：405个“9档余量×15区×3机型”最大安全载荷；不可达只指空载往返不可行。06：9档整体指标，40%、45%整体不可行。07：135个局部服务区可行性格，以完整逐箱交付判定。',
          '水平/爬升能耗是已有文献依据下的简化模型值，未实测标定；引用docs/公式来源与假设审计.md及references/运输能耗公式专项核查.md。统计指标为已有结果直接汇总或无量纲比值，不新增优化或物理假设。',
          '累计作业时间不是多机并行完工时间；架次不是实体机数量；有限目标对照不是完整Pareto前沿。机型矩阵反映当前主方案选择，不代表某种机型无条件最优。',
          '', '## 核验','',
          '源码模式核对原始输入与已保存结果哈希，使用完整DEM复核航段；从原始逐箱属性独立重算全部对照及余量情景，并重新核对405个安全载荷值。18架次80箱、758 kg、2.011 m³保持一致；分项逐批次/逐区/全局守恒。',
          '快照重绘模式检查快照哈希及各表一致性，不宣称重新核对了不可获得的原始附件。所有输出带代码、字体、依赖版本和数据哈希日志。PNG/PDF渲染检查另记logs/export_checks.json。']
    for name in NAMES:docs+=['',f'## {name}','',f'![{name}](figures/{name}.png)']
    (output/'图册说明与重绘方法.md').write_text('\n'.join(docs)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'figures':len(figures.exports),'output':str(output),'validation':validation},ensure_ascii=False))


if __name__=='__main__':main()
