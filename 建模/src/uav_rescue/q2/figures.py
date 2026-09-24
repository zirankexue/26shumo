"""Eight static scientific figures, redrawable from a self-contained JSON bundle."""
from pathlib import Path
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

LABELS = {'tardiness':'及时性优先','makespan':'完工时间优先','energy':'能耗优先','sorties':'架次优先'}
COLORS = {'A':'#4477AA','B':'#228833','C':'#CC6677','mixed':'#626A73'}
NAMES = ['01_二维运输路线','02_三维实际航迹','03_无人机占用甘特图','04_电池使用与充电',
         '05_逐箱配送及时性','06_硬截止裕量排序','07_四目标优先级权衡','08_ALNS搜索进展']


def prepare_map(payload, data, physics, project):
    from ..common.terrain import Terrain
    from ..common.data import file_hash
    import importlib.util
    terrain = Terrain(data.base.dem_path, data.base.origin)
    nodes = {'O01':data.base.origin, **data.base.services}
    xy = {k:[v/1000 for v in terrain.xy(n)] for k,n in nodes.items()}
    xs,ys = np.array(list(xy.values())).T
    bounds = [float(xs.min()-1),float(xs.max()+1),float(ys.min()-1),float(ys.max()+1)]
    lon = terrain.origin.lon+np.degrees(np.array(bounds[:2])*1000/terrain.scale_x)
    lat = terrain.origin.lat+np.degrees(np.array(bounds[2:])*1000/terrain.scale_y)
    cols = np.unique(np.linspace(max(0,math.floor((lon[0]-terrain.lon0)/terrain.sx)),
                                 min(terrain.cols-1,math.ceil((lon[1]-terrain.lon0)/terrain.sx)),220).astype(int))
    rows = np.unique(np.linspace(max(0,math.floor((terrain.lat0-lat[1])/terrain.sy)),
                                 min(terrain.rows-1,math.ceil((terrain.lat0-lat[0])/terrain.sy)),220).astype(int))
    x = terrain.scale_x*np.radians(terrain.lon0+cols*terrain.sx-terrain.origin.lon)/1000
    y = terrain.scale_y*np.radians(terrain.lat0-rows*terrain.sy-terrain.origin.lat)/1000
    z = terrain.elevations[np.ix_(rows,cols)].astype(float)
    if np.any(~np.isfinite(z)) or np.any(z == -32767):
        raise ValueError('显示区域DEM含缺失值')
    source = project.parent/'数据/镇龙乡地理空间数据/镇龙乡地理空间详情地图.html'
    spec = importlib.util.spec_from_file_location('q2_relief',project/'scripts/route_background.py')
    mod = importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    relief = mod.SourceRelief(source,terrain,nodes)
    X,Y = np.meshgrid(x,y)
    xmin,xmax,ymin,ymax = relief.extent
    rgb = relief.texture(np.clip(X,xmin,xmax),np.clip(Y,ymin,ymax))
    payload['map'] = {'xy':xy,'bounds':bounds,'x':x.tolist(),'y':y.tolist(),'z':z.tolist(),
                      'rgb':np.round(rgb,4).tolist(),'rings':[r.tolist() for r in relief.rings],
                      'source':str(source),'source_sha256':file_hash(source),
                      'dem_sha256':file_hash(data.base.dem_path),'display_grid_only':True,
                      'z_exaggeration':5,'source_colors_clamped_at_texture_edge':True}
    payload['resources'] = {'units':data.units,'batteries':data.battery_count}


def draw_all(payload, font_path, output, z_exaggeration=5):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if not Path(font_path).is_file():
        raise FileNotFoundError('需要有效中文字体')
    font_manager.fontManager.addfont(str(font_path))
    font=font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({'font.family':font,'font.size':11,'axes.unicode_minus':False,
                         'pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
    main=payload['main'];rows=main['sorties'];m=main['summary'];mp=payload['map']
    xy=mp['xy'];bounds=mp['bounds'];nodes={n['id']:n for n in payload['nodes']}
    X,Y=np.meshgrid(mp['x'],mp['y']);Z=np.array(mp['z']);rgb=np.array(mp['rgb'])
    display_rgb=.67*rgb+.33
    extent=(mp['x'][0],mp['x'][-1],mp['y'][-1],mp['y'][0])
    def save(fig,index,note=None):
        if note:fig.text(.5,.015,note,ha='center',va='bottom',fontsize=9,color='#444444')
        fig.savefig(output/(NAMES[index]+'.png'),dpi=300,bbox_inches='tight',facecolor='white')
        fig.savefig(output/(NAMES[index]+'.pdf'),bbox_inches='tight',facecolor='white')
        plt.close(fig)
    def legend(ax):
        ax.legend(handles=[Line2D([0],[0],color=COLORS[g],lw=2,label=f'{g}型 {m["type_counts"].get(g,0)}架次') for g in 'ABC']+
                  [Line2D([0],[0],color=COLORS['mixed'],lw=2,label='多机型共用航段')],loc='upper left',fontsize=9)
    groups={}
    for leg in main['legs']:
        g=next(r['drone'] for r in rows if r['sortie']==leg['sortie'])
        groups.setdefault((leg['from'],leg['to']),[]).append((g,leg))
    fig,ax=plt.subplots(figsize=(12,10));fig.subplots_adjust(bottom=.12)
    ax.imshow(display_rgb,extent=extent,origin='upper',zorder=0)
    for ring in mp['rings']:
        ring=np.array(ring);ax.plot(ring[:,0],ring[:,1],color='#7C6877',lw=.7,alpha=.6)
    for (a,b),items in groups.items():
        types={g for g,_ in items};color=COLORS[next(iter(types))] if len(types)==1 else COLORS['mixed']
        pa,pb=np.array(xy[a]),np.array(xy[b]);ax.plot(*np.stack([pa,pb]).T,color=color,lw=1.3,alpha=.85,zorder=3)
        ax.annotate('',xy=pa+.43*(pb-pa),xytext=pa+.32*(pb-pa),arrowprops={'arrowstyle':'-|>','color':color,'lw':1.3},zorder=4)
        if len(items)>1:
            mid=pa+.30*(pb-pa);ax.text(*mid,str(len(items)),fontsize=8,color=color,bbox={'fc':'white','ec':'none','alpha':.8,'pad':.6},zorder=5)
    for n,p in xy.items():
        ax.scatter(*p,s=130 if n=='O01' else 35,marker='*' if n=='O01' else 'o',c='#20262B',zorder=6)
        ax.annotate(n,p,xytext=(5,5),textcoords='offset points',fontsize=10,
                    bbox={'fc':'white','ec':'none','alpha':.8,'pad':1},zorder=7)
    ax.set(xlim=bounds[:2],ylim=bounds[2:],xlabel='相对O01东向距离 / km',ylabel='相对O01北向距离 / km')
    ax.set_aspect('equal');legend(ax)
    ax.annotate('N',xy=(.96,.94),xytext=(.96,.85),xycoords='axes fraction',ha='center',arrowprops={'arrowstyle':'-|>','color':'#222222'})
    bx,by=bounds[0]+1,bounds[2]+.7
    ax.plot([bx,bx+2],[by,by],color='black',lw=2);ax.text(bx+1,by+.15,'2 km',ha='center',fontsize=9)
    ax.set_title(f'问题二主方案：{m["sorties"]}架次，{m["multi_stop_sorties"]}次多点运输')
    multi=[f'{r["sortie"]}: '+ ' → '.join(['O01',*r['visits'],'O01']) for r in rows if len(r['visits'])>1]
    note='数字为有向航段执行次数；合并显示保持真实几何。'
    if len(multi)<=4:note+='\n'+'\n'.join(multi)
    else:note+='多点访问次序见运输安排表。'
    save(fig,0,note)

    fig=plt.figure(figsize=(13,10));ax=fig.add_subplot(111,projection='3d',computed_zorder=False)
    ax.plot_surface(X,Y,Z,facecolors=display_rgb,rcount=min(220,len(mp['y'])),ccount=min(220,len(mp['x'])),
                    linewidth=0,antialiased=False,shade=False,alpha=.6,rasterized=True,zorder=0)
    for (a,b),items in groups.items():
        leg=items[0][1];types={g for g,_ in items};color=COLORS[next(iter(types))] if len(types)==1 else COLORS['mixed']
        p,q=xy[a],xy[b];H=leg['cruise_altitude_m']
        za=nodes[a]['elevation']+(0 if a=='O01' else 30);zb=nodes[b]['elevation']+(0 if b=='O01' else 30)
        ax.plot([p[0],p[0],q[0],q[0]],[p[1],p[1],q[1],q[1]],[za,H,H,zb],color=color,lw=1.4,alpha=.9,zorder=5)
        dx,dy=q[0]-p[0],q[1]-p[1]
        ax.quiver(p[0]+.32*dx,p[1]+.32*dy,H,.12*dx,.12*dy,0,color=color,arrow_length_ratio=.35,linewidth=1,zorder=6)
    for n,p in xy.items():
        z=nodes[n]['elevation']+(0 if n=='O01' else 30)
        ax.scatter(*p,z,s=80 if n=='O01' else 18,marker='*' if n=='O01' else 'o',color='black',zorder=6)
        ax.text(p[0],p[1],z+25,n,fontsize=9,zorder=7)
    zlo=float(Z.min());zhi=max(float(Z.max()),max(r['cruise_altitude_m'] for r in main['legs']))+80
    ax.set(xlim=bounds[:2],ylim=bounds[2:],zlim=(zlo,zhi),xlabel='东向 / km',ylabel='北向 / km',zlabel='海拔 / m')
    ax.set_box_aspect((bounds[1]-bounds[0],bounds[3]-bounds[2],(zhi-zlo)/1000*z_exaggeration))
    ax.view_init(elev=28,azim=-60);ax.set_proj_type('ortho');legend(ax)
    ax.set_title('主方案实际空间航迹：每站交付后重新爬升')
    save(fig,1,f'高程显示夸张{z_exaggeration:g}倍；圆点为节点作业高度。地形网格仅用于显示，净空按完整DEM核验。')

    for index,field,title in [(2,'unit','无人机占用'),(3,'battery','电池使用与返航充电')]:
        inventory=payload['resources']
        labels=sorted(u for us in inventory['units'].values() for u in us) if field=='unit' else [f'BAT-{g}-{j:02}' for g in 'ABC' for j in range(1,inventory['batteries'][g]+1)]
        fig,ax=plt.subplots(figsize=(14,6 if field=='unit' else 8));fig.subplots_adjust(bottom=.24 if field=='unit' else .21,left=.13)
        for r in rows:
            y=labels.index(r[field]);left=r['start_s']/3600;duration=r['operation_s']/3600
            ax.barh(y,duration,left=left,height=.67,color=COLORS[r['drone']],edgecolor='white')
            ax.text(left+duration/2,y,r['sortie'].split('-')[-1],ha='center',va='center',fontsize=8,color='white')
            if field=='battery':ax.barh(y,r['charge_s']/3600,left=r['return_s']/3600,height=.67,color='#D9DEE2',edgecolor='white')
        ax.set_yticks(range(len(labels)),labels);ax.invert_yaxis();ax.set_xlabel('任务开始后 / h');ax.grid(axis='x',alpha=.2)
        ax.axvline(m['makespan_s']/3600,color='#222222',ls='--',lw=1)
        handles=[Patch(color=COLORS[g],label=f'{g}型任务') for g in 'ABC']
        if field=='battery':handles.append(Patch(color='#D9DEE2',label='返航后充电'))
        fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.055),ncol=4,fontsize=10)
        ax.set_title(title+'：条内数字为架次编号')
        save(fig,index,'任务占用包含准备、装载、飞行和交接；虚线为最后返航，不包含末次充电。')

    ds=main['deliveries'];fig,axes=plt.subplots(2,1,figsize=(15,10),sharey=True);fig.subplots_adjust(bottom=.15,hspace=.40)
    split=next(i for i,r in enumerate(ds) if r['service']=='S008')
    for ax,part in zip(axes,[ds[:split],ds[split:]]):
        x=np.arange(len(part))
        ax.vlines(x,[r['completion_s']/3600 for r in part],[r['expected_s']/3600 for r in part],color='#B5BDC4',lw=.8)
        ax.scatter(x,[r['expected_s']/3600 for r in part],marker='_',s=130,c='#555E66',label='期望时刻')
        hard=[(i,r) for i,r in enumerate(part) if r['hard_s'] is not None]
        ax.scatter([i for i,r in hard],[r['hard_s']/3600 for i,r in hard],marker='s',s=50,facecolors='none',edgecolors='#C23B4B',label='硬截止')
        ax.scatter(x,[r['completion_s']/3600 for r in part],s=24,c=['#C23B4B' if r['lateness_s']>0 else '#287D93' for r in part],label='交付完成',zorder=4)
        ax.set_xticks(x,[r['box'] for r in part],rotation=90,fontsize=8)
        ax.set_ylabel('任务开始后 / h');ax.grid(axis='y',alpha=.2);ax.legend(ncol=3,loc='upper right',fontsize=9)
    fig.suptitle(f'逐箱交付：{m["on_time_boxes"]}/80箱按期，31个硬截止全部满足',y=.98)
    save(fig,4,'期望时刻用于软迟到评价；硬截止取医疗与首批规则中适用的最严格值。')

    hard=sorted([r for r in ds if r['hard_s'] is not None],key=lambda r:(r['hard_s']-r['completion_s'],r['box']))
    identities=['医疗+首批' if '-MED-' in r['box'] and r['first'] else '仅医疗' if '-MED-' in r['box'] else '仅首批' for r in hard]
    cs={'医疗+首批':'#A95078','仅医疗':'#358DA0','仅首批':'#CA963C'}
    slack=[(r['hard_s']-r['completion_s'])/60 for r in hard]
    fig,ax=plt.subplots(figsize=(12,12));fig.subplots_adjust(left=.24,bottom=.10)
    ax.barh(range(len(hard)),slack,color=[cs[t] for t in identities],height=.7)
    ax.set_yticks(range(len(hard)),[r['box'] for r in hard],fontsize=10);ax.invert_yaxis()
    for i,v in enumerate(slack):ax.text(v+.3,i,f'{v:.2f}',va='center',fontsize=9)
    ax.axvline(0,color='#C23B4B',lw=1.4);ax.set_xlim(min(-2,min(slack)-2),max(slack)*1.13+1)
    ax.set_xlabel('有效硬截止 - 交付完成 / min');ax.grid(axis='x',alpha=.2)
    ax.legend(handles=[Patch(color=c,label=k) for k,c in cs.items()],loc='upper right')
    ax.set_title(f'31个硬时限箱的截止裕量：最小 {min(slack):.3f} min')
    save(fig,5,'按未四舍五入的时刻核验；箱号前缀同时标识服务区。')

    cases=payload['schemes'];names=list(cases);fig,axes=plt.subplots(2,2,figsize=(13,9));fig.subplots_adjust(bottom=.13,hspace=.38,wspace=.27)
    metrics=[('weighted_tardiness_s','加权逾期 / 系数·min',60),('makespan_s','全部返航时间 / h',3600),('energy_kwh','运输能耗 / kWh',1),('sorties','架次',1)]
    for ax,(metric,label,unit) in zip(axes.flat,metrics):
        values=[cases[k]['summary'][metric]/unit for k in names]
        bars=ax.bar(range(len(names)),values,color=['#28687D','#63A596','#CE9550','#AE6581'],width=.6)
        for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,v,f'{v:.0f}' if metric=='sorties' else f'{v:.3f}',ha='center',va='bottom',fontsize=10)
        ax.set_xticks(range(len(names)),[LABELS[k].replace('优先','\n优先') for k in names]);ax.set_ylabel(label)
        ax.set_ylim(0,max(values)*1.2 if max(values)>0 else 1);ax.grid(axis='y',alpha=.2)
    fig.suptitle('四种首要目标的可行方案对照',y=.98)
    save(fig,6,'相同约束，公共候选池收尾；限时搜索结果不是完整Pareto前沿。各子图使用独立单位。')

    fig,axes=plt.subplots(2,2,figsize=(13,9));fig.subplots_adjust(bottom=.12,hspace=.33,wspace=.26)
    unit_by={'tardiness':60000,'makespan':3600000,'energy':1e9,'sorties':1}
    ylabels={'tardiness':'加权逾期 / 系数·min','makespan':'最后返航 / h','energy':'能耗 / kWh','sorties':'架次'}
    for ax,name in zip(axes.flat,names):
        history=[r for r in payload['alns_trace'] if r['scheme']==name]
        x=[r['iteration'] for r in history];u=unit_by[name]
        ax.step(x,[r['best'][name]/u for r in history],where='post',color='#28687D',lw=2,label='已见最好值')
        accepted=[r for r in history if r['accepted'] and r['candidate'] is not None]
        ax.scatter([r['iteration'] for r in accepted],[r['candidate'][name]/u for r in accepted],s=15,color='#CA963C',alpha=.7,label='接受的当前值')
        ax.set(title=LABELS[name],xlabel='该目标ALNS迭代',ylabel=ylabels[name]);ax.grid(alpha=.2);ax.legend(fontsize=9)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        if name=='sorties':ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        if name=='tardiness':
            ymax=max([r['best'][name]/u for r in history]+[r['candidate'][name]/u for r in accepted]+[0])
            ax.set_ylim(0,max(1,ymax*1.1))
    save(fig,7,'仅展示ALNS阶段首要指标，收尾结果另见对照表；较差当前解不会覆盖最好可行解。')
    return [str(output/(n+'.png')) for n in NAMES]
