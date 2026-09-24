"""Static 2D / 3D route atlas from verified Q1 and Q2 results; no optimization."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
_runtime = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (__name__ == '__main__' and (PROJECT/'.runtime/python').is_dir()
        and _runtime.exists() and Path(sys.executable).resolve() != _runtime.resolve()):
    import subprocess
    raise SystemExit(subprocess.call([str(_runtime), '-X', 'utf8', str(Path(__file__).resolve()), *sys.argv[1:]]))
for folder in (PROJECT/'src', PROJECT/'.runtime/python'):
    if folder.is_dir(): sys.path.insert(0, str(folder))
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')

import argparse
from collections import defaultdict, Counter
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager, colors
from matplotlib.lines import Line2D
import matplotlib.patheffects as pe
from mpl_toolkits.mplot3d import proj3d
import numpy as np

from uav_rescue.common.data import load_inputs, file_hash
from uav_rescue.common.terrain import Terrain
from route_background import SourceRelief

COLORS = {'A':'#4477AA','B':'#228833','C':'#CC6677','mixed':'#737A82'}
STYLES = {'Q2-005':'--','Q2-012':':','Q2-029':'-.'}
NAMES = ['01_问题一二维路线总览','02_问题一三维地形航迹','03_问题二维路线总览',
         '04_问题二三维地形航迹','05_问题二分无人机二维路线','06_问题二分无人机三维航迹']


def check(value, message):
    if not value: raise ValueError(message)


def equal(a,b,message,tol=1e-6):
    check(math.isclose(a,b,rel_tol=0,abs_tol=tol),f'{message}: {a} != {b}')


def csv_write(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def collect(q1,q2,data,terrain,physics):
    nodes={'O01':data.origin,**data.services};segments=[];vertices=[];sorties=[]
    routes={r['service']:r for r in q1['routes']}
    for question,records in [('Q1',q1['baseline']['rows']),('Q2',q2['main']['sorties'])]:
        for sortie in records:
            sid=sortie['sortie'];drone=sortie['drone'];unit=sortie.get('unit','')
            visits=[sortie['service']] if question=='Q1' else sortie['visits']
            chain=['O01',*visits,'O01']
            sorties.append({'question':question,'sortie':sid,'unit':unit,'drone':drone,
                            'visits':' → '.join(chain),'visit_count':len(visits)})
            if question=='Q1':
                raw=[routes[sortie['service']][k] for k in ['outbound','inbound']]
            else:
                raw=[{'start':r['from'],'end':r['to'],'distance':r['distance_m'],
                      'cruise_altitude':r['cruise_altitude_m'],'climb':r['climb_m'],'descent':r['descent_m']}
                     for r in sorted(q2['main']['legs'],key=lambda r:r['segment']) if r['sortie']==sid]
            check(len(raw)==len(chain)-1,'航段数量与访问顺序不符')
            last=None
            for index,leg in enumerate(raw,1):
                check([leg['start'],leg['end']]==chain[index-1:index+1],f'{sid}访问顺序不一致')
                a,b=nodes[leg['start']],nodes[leg['end']]
                actual=terrain.leg(a,b,physics['clearance_m'],physics['service_height_m'])
                for key in ['distance','cruise_altitude','climb','descent']:
                    equal(leg[key],getattr(actual,key),f'{sid}/{index}/{key}')
                za=a.elevation+(0 if a.id=='O01' else physics['service_height_m'])
                zb=b.elevation+(0 if b.id=='O01' else physics['service_height_m'])
                ax,ay=terrain.xy(a);bx,by=terrain.xy(b);h=leg['cruise_altitude']
                points=[(ax/1000,ay/1000,za),(ax/1000,ay/1000,h),
                        (bx/1000,by/1000,h),(bx/1000,by/1000,zb)]
                if last is not None: check(last==points[0],f'{sid}航迹不连续')
                last=points[-1]
                row={'question':question,'sortie':sid,'unit':unit,'drone':drone,'segment':index,
                     'from':a.id,'to':b.id,'horizontal_distance_m':leg['distance'],
                     'terrain_max_m':h-physics['clearance_m'],'cruise_altitude_m':h,
                     'start_working_m':za,'end_working_m':zb,'climb_m':leg['climb'],
                     'descent_m':leg['descent'],'final_return':b.id=='O01'}
                segments.append(row)
                for k,((x,y,z),phase) in enumerate(zip(points,['起点作业','爬升结束','巡航结束','终点作业'])):
                    vertices.append({'question':question,'sortie':sid,'unit':unit,'drone':drone,
                                     'segment':index,'vertex':k,'phase':phase,'east_km':x,'north_km':y,'altitude_m':z})
                equal(math.hypot(points[2][0]-points[1][0],points[2][1]-points[1][1])*1000,
                      leg['distance'],'二维与三维水平距离')
    check(Counter(s['question'] for s in sorties)=={'Q1':18,'Q2':36},'主方案架次数变化')
    check(Counter(s['question'] for s in segments)=={'Q1':36,'Q2':76},'主方案航段数变化')
    check({s['unit'] for s in sorties if s['question']=='Q2'}=={f'U{i:02}' for i in range(1,9)},'实体机集合不符')
    check({s['sortie'] for s in sorties if s['question']=='Q2' and s['visit_count']>1}==set(STYLES),'多点架次集合变化')
    return nodes,segments,vertices,sorties


class Atlas:
    def __init__(self,data,terrain,nodes,segments,sorties,exaggeration,output,relief=None):
        self.data=data;self.terrain=terrain;self.nodes=nodes;self.segments=segments
        self.sorties=sorties;self.exaggeration=exaggeration;self.output=output
        self.relief=relief
        self.working={r['from']:r['start_working_m'] for r in segments}
        self.xy={k:np.array(terrain.xy(n))/1000 for k,n in nodes.items()}
        xy=np.array(list(self.xy.values()))
        if relief is not None:
            xy=np.vstack([xy,*relief.rings])
        self.bounds=(xy[:,0].min()-1,xy[:,0].max()+1,xy[:,1].min()-1,xy[:,1].max()+1)
        xmin,xmax,ymin,ymax=self.bounds
        # PixelIsPoint: TIFF tie point is the centre of pixel (0,0), not its edge.
        lonmin=nodes['O01'].lon+math.degrees(xmin*1000/terrain.scale_x)
        lonmax=nodes['O01'].lon+math.degrees(xmax*1000/terrain.scale_x)
        latmin=nodes['O01'].lat+math.degrees(ymin*1000/terrain.scale_y)
        latmax=nodes['O01'].lat+math.degrees(ymax*1000/terrain.scale_y)
        c0=math.floor((lonmin-terrain.lon0)/terrain.sx);c1=math.ceil((lonmax-terrain.lon0)/terrain.sx)
        r0=math.floor((terrain.lat0-latmax)/terrain.sy);r1=math.ceil((terrain.lat0-latmin)/terrain.sy)
        check(0<=c0<c1<terrain.cols and 0<=r0<r1<terrain.rows,'地图边界超出DEM')
        self.crop=(r0,r1,c0,c1)
        z=terrain.elevations[r0:r1+1,c0:c1+1]
        check(np.all(np.isfinite(z)) and not np.any(z==-32767),'地图范围存在缺失DEM')
        self.zlim=(0,math.ceil(max(float(z.max()),max(s['cruise_altitude_m'] for s in segments))/100)*100+100)
        self.cmap=colors.LinearSegmentedColormap.from_list('pale_terrain',['#F4F3E9','#D9E1CE','#BDCCB7','#A9B7A0','#A9A89E'])
        self.norm=colors.Normalize(float(z.min()),float(z.max()))
        self.grid_cache={}
        self.groups=[]
        self.label_checks=[]

    def queue_label(self,ax,text,point,three,small,node=False):
        if not hasattr(ax,'route_labels'):ax.route_labels=[]
        ax.route_labels.append((text,point,three,small,node))

    def label_layout(self,fig):
        # Screen-space label placement changes text only, never flight geometry.
        fig.canvas.draw();renderer=fig.canvas.get_renderer()
        for ax in fig.axes:
            labels=getattr(ax,'route_labels',[])
            occupied=[]
            for text,point,three,small,node in sorted(labels,key=lambda v:not v[4]):
                if three:
                    x,y,_=proj3d.proj_transform(*point,ax.get_proj());point2=(x,y)
                else:point2=point
                size=(8 if small else 10) if node else (7 if small else 8)
                artist=ax.annotate(text,point2,xytext=(5,5),textcoords='offset points',
                                   fontsize=size,color='#222222' if node else '#555D64',
                                   bbox={'facecolor':'white','edgecolor':'none','alpha':.82 if three else .72,'pad':.7},
                                   zorder=20,annotation_clip=False)
                selected=None
                for radius in [5,13,23,35,49]:
                    for dx,dy in [(radius,radius),(-radius,radius),(radius,-radius),(-radius,-radius),(0,radius),(0,-radius)]:
                        artist.set_position((dx,dy));artist.set_ha('left' if dx>=0 else 'right')
                        artist.set_va('bottom' if dy>=0 else 'top')
                        box=artist.get_window_extent(renderer).expanded(1.10,1.18)
                        if not any(box.overlaps(old) for old in occupied):
                            selected=box;break
                    if selected is not None:break
                check(selected is not None,f'无法放置标签{text}')
                occupied.append(selected)
            self.label_checks.append({'labels':len(labels),'pairwise_label_overlap':False})

    def grid(self,limit):
        if limit in self.grid_cache:return self.grid_cache[limit]
        t=self.terrain;r0,r1,c0,c1=self.crop
        rr=np.unique(np.linspace(r0,r1,min(limit,r1-r0+1)).round().astype(int))
        cc=np.unique(np.linspace(c0,c1,min(limit,c1-c0+1)).round().astype(int))
        x=t.scale_x*np.radians(t.lon0+cc*t.sx-t.origin.lon)/1000
        y=t.scale_y*np.radians(t.lat0-rr*t.sy-t.origin.lat)/1000
        X,Y=np.meshgrid(x,y);Z=t.elevations[np.ix_(rr,cc)]
        self.grid_cache[limit]=(X,Y,Z)
        return X,Y,Z

    def groups_for(self,rows,figure):
        groups=defaultdict(list)
        for r in rows:
            a,b=sorted([r['from'],r['to']])
            groups[a,b,r['cruise_altitude_m']].append(r)
        result=[]
        for (a,b,h),rr in sorted(groups.items()):
            types=sorted({r['drone'] for r in rr});color=COLORS[types[0] if len(types)==1 else 'mixed']
            forward=sum(r['from']==a for r in rr);backward=len(rr)-forward
            result.append((a,b,h,rr,color,forward,backward))
            self.groups.append({'figure':figure,'from':a,'to':b,'cruise_altitude_m':h,
                                'traversals':len(rr),'forward':forward,'backward':backward,
                                'types':';'.join(types),'sorties':';'.join(sorted({r['sortie'] for r in rr}))})
        check(sum(len(g[3]) for g in result)==len(rows),'合并显示丢失航段')
        return result

    def base2d(self,ax,small):
        if self.relief is not None:
            im=ax.imshow(self.relief.rgb,extent=self.relief.extent,origin='upper',interpolation='bilinear',rasterized=True,zorder=0)
            for ring in self.relief.rings:
                ax.plot(ring[:,0],ring[:,1],color='#30353A',lw=.9 if small else 1.2,zorder=2,
                        path_effects=[pe.Stroke(linewidth=2.5 if small else 3.2,foreground='white'),pe.Normal()])
        else:
            X,Y,Z=self.grid(240)
            im=ax.pcolormesh(X,Y,Z,cmap=self.cmap,norm=self.norm,shading='nearest',rasterized=True,zorder=0)
            ax.contour(X,Y,Z,levels=np.arange(100,self.zlim[1],100),colors='#81907A',linewidths=.35,alpha=.3,zorder=1)
        xmin,xmax,ymin,ymax=self.bounds
        ax.set(xlim=(xmin,xmax),ylim=(ymin,ymax),xlabel='东向距离 / km',ylabel='北向距离 / km',aspect='equal')
        ax.grid(alpha=.18,color='white' if self.relief else '#777777');ax.tick_params(labelsize=8 if small else 10)
        ax.annotate('N',xy=(.94,.94),xytext=(.94,.82),xycoords='axes fraction',ha='center',fontsize=10,
                    arrowprops={'arrowstyle':'-|>','color':'#333333'},zorder=10)
        x=xmin+.06*(xmax-xmin);y=ymin+.06*(ymax-ymin)
        ax.plot([x,x+2],[y,y],color='#333333',lw=2,zorder=9)
        ax.plot([x,x,x+2,x+2],[y-.08,y+.08,y+.08,y-.08],color='#333333',lw=.7,zorder=9)
        ax.text(x+1,y+.18,'2 km',ha='center',fontsize=8,zorder=9)
        return im

    def base3d(self,ax,small):
        X,Y,Z=self.grid(85 if small else 200)
        kwargs={'facecolors':self.relief.texture(X,Y)} if self.relief else {'cmap':self.cmap,'norm':self.norm}
        ax.plot_surface(X,Y,Z,**kwargs,rcount=len(Y),ccount=X.shape[1],
                        alpha=.88 if self.relief else .50,linewidth=0,antialiased=False,shade=False,rasterized=True,zorder=0)
        if self.relief:
            for ring in self.relief.rings:
                bx,by,bz=self.relief.boundary3d(ring)
                ax.plot(bx,by,bz,color='#30353A',lw=.6 if small else .85,zorder=1,
                        path_effects=[pe.Stroke(linewidth=1.6 if small else 2.0,foreground='white'),pe.Normal()])
        xmin,xmax,ymin,ymax=self.bounds
        ax.set(xlim=(xmin,xmax),ylim=(ymin,ymax),zlim=self.zlim)
        ax.set_xlabel('东向 / km',fontsize=8 if small else 11,labelpad=2)
        ax.set_ylabel('北向 / km',fontsize=8 if small else 11,labelpad=2)
        ax.set_zlabel('海拔 / m',fontsize=8 if small else 11,labelpad=2)
        ax.view_init(elev=28,azim=-60)
        ax.set_proj_type('ortho')
        ax.set_box_aspect((xmax-xmin,ymax-ymin,(self.zlim[1]-self.zlim[0])/1000*self.exaggeration))
        ax.tick_params(labelsize=7 if small else 9,pad=0)
        ax.set_zticks(np.arange(0,self.zlim[1]+1,200))
        for axis in [ax.xaxis,ax.yaxis,ax.zaxis]:
            axis.pane.set_facecolor((1,1,1,0));axis._axinfo['grid']['color']=(.5,.5,.5,.16)

    def route2d(self,ax,groups,small):
        for a,b,h,rr,color,forward,backward in groups:
            p,q=self.xy[a],self.xy[b];v=q-p
            lw=1.15+.30*math.log2(len(rr)+1)
            ax.plot([p[0],q[0]],[p[1],q[1]],color=color,lw=lw,alpha=.97,zorder=3,
                    path_effects=[pe.Stroke(linewidth=lw+1.6,foreground='white',alpha=.85),pe.Normal()] if self.relief else [])
            for count,start,end in [(forward,.31,.42),(backward,.70,.59)]:
                if count:
                    ax.annotate('',xy=p+end*v,xytext=p+start*v,
                                arrowprops={'arrowstyle':'->','color':color,'lw':1.1,'mutation_scale':9},zorder=4)
            mid=p+.73*v
            if len(rr)>1:
                self.queue_label(ax,f'×{len(rr)}',mid,False,small)

    def route3d(self,ax,groups,small):
        for a,b,h,rr,color,forward,backward in groups:
            p,q=self.xy[a],self.xy[b];za=self.working[a];zb=self.working[b]
            lw=1.25+.22*math.log2(len(rr)+1)
            ax.plot([p[0],p[0],q[0],q[0]],[p[1],p[1],q[1],q[1]],[za,h,h,zb],color=color,lw=lw,zorder=4,
                    path_effects=[pe.Stroke(linewidth=lw+1.3,foreground='white',alpha=.85),pe.Normal()] if self.relief else [])
            v=q-p
            for count,t,sign in [(forward,.35,1),(backward,.68,-1)]:
                if count:
                    mid=p+t*v;delta=v*.07*sign
                    ax.quiver(mid[0],mid[1],h,delta[0],delta[1],0,color=color,arrow_length_ratio=.38,linewidth=.9,zorder=5)
            if len(rr)>1:
                mid=p+.76*v
                self.queue_label(ax,f'×{len(rr)}',(mid[0],mid[1],h+15),True,small)

    def nodes_plot(self,ax,rows,three,small):
        active={r[k] for r in rows for k in ['from','to']}
        for sid,node in self.nodes.items():
            x,y=self.xy[sid];origin=sid=='O01';on=sid in active
            if three:
                z=self.working[sid]
                ax.scatter([x],[y],[node.elevation],s=9,color='#555555',alpha=.7,zorder=6,depthshade=False)
                if on:
                    ax.plot([x,x],[y,y],[node.elevation,z],color='#555555',lw=.7,zorder=6)
                    ax.scatter([x],[y],[z],s=65 if origin else 18,marker='*' if origin else 'o',
                               color='#222222',edgecolor='white',linewidth=.5,depthshade=False,zorder=8)
                    self.queue_label(ax,sid,(x,y,z),True,small,True)
            else:
                ax.scatter([x],[y],s=110 if origin else (30 if on else 13),marker='*' if origin else 'o',
                           color='#222222' if on else '#A7AAA6',edgecolor='white',linewidth=.6,zorder=8)
                if on:
                    self.queue_label(ax,sid,(x,y),False,small,True)

    def highlight(self,ax,rows,three):
        for sid,style in STYLES.items():
            rr=sorted([r for r in rows if r['sortie']==sid],key=lambda r:r['segment'])
            for r in rr:
                p,q=self.xy[r['from']],self.xy[r['to']];h=r['cruise_altitude_m'];c=COLORS[r['drone']]
                if three:
                    ax.plot([p[0],p[0],q[0],q[0]],[p[1],p[1],q[1],q[1]],
                            [r['start_working_m'],h,h,r['end_working_m']],color=c,ls=style,lw=2.4,zorder=6)
                else:
                    ax.plot([p[0],q[0]],[p[1],q[1]],color=c,ls=style,lw=2.2,zorder=5)

    def legend(self,fig,question,top):
        handles=[Line2D([],[],color=c,lw=2,label=f'{g}型') for g,c in COLORS.items() if g!='mixed']
        handles += [Line2D([],[],color=COLORS['mixed'],lw=2,label='多机型共用航段'),
                    Line2D([],[],marker='*',color='#222222',ls='',markersize=10,label='O01调度中心')]
        if self.relief:handles.append(Line2D([],[],color='#30353A',lw=1,label='镇龙乡界'))
        fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,top),ncols=len(handles),frameon=False,fontsize=10)
        if question=='Q2':
            texts=[]
            for sid,ls in STYLES.items():
                s=next(s for s in self.sorties if s['sortie']==sid)
                texts.append(Line2D([],[],color=COLORS[s['drone']],ls=ls,lw=2,
                                    label=f"{sid}：{s['visits']}"))
            fig.legend(handles=texts,loc='lower center',bbox_to_anchor=(.5,.06),ncols=1,frameon=False,fontsize=10)

    def save(self,fig,name):
        self.label_layout(fig)
        fig.savefig(self.output/'figures'/f'{name}.png',dpi=300,bbox_inches='tight',facecolor='white')
        fig.savefig(self.output/'figures'/f'{name}.pdf',dpi=220,bbox_inches='tight',facecolor='white')
        plt.close(fig)
        print('已生成 '+name,flush=True)

    def overall(self,question,three,name):
        rows=[r for r in self.segments if r['question']==question]
        fig=plt.figure(figsize=(13,11) if three else (12,11))
        ax=fig.add_subplot(111,projection='3d',computed_zorder=False) if three else fig.add_subplot(111)
        fig.subplots_adjust(left=.07,right=.89 if not three else .94,bottom=.19 if question=='Q2' else .12,top=.86)
        if three:self.base3d(ax,False)
        else:
            im=self.base2d(ax,False)
            if not self.relief:
                cax=fig.add_axes([.925,.25,.016,.44]);fig.colorbar(im,cax=cax,label='DEM海拔 / m')
        groups=self.groups_for(rows,name)
        if three:self.route3d(ax,groups,False)
        else:self.route2d(ax,groups,False)
        if question=='Q2':self.highlight(ax,rows,three)
        self.nodes_plot(ax,rows,three,False)
        count=18 if question=='Q1' else 36
        title=f"{'问题一' if question=='Q1' else '问题二'} · {'三维地形航迹' if three else '二维运输路线'}"
        fig.suptitle(title,fontsize=20,y=.978)
        note=f'安全余量20% · {count}架次 · {len(rows)}条有向航段'
        if three:note+=f' · 高程显示夸张{self.exaggeration:g}倍'
        fig.text(.5,.935,note,ha='center',fontsize=12,color='#454D53')
        self.legend(fig,question,.914)
        foot='×n表示共用航段累计飞行次数（含去返）；重合路线原位合并，完整架次见CSV。'
        if three:foot+='\n地形为降采样透明显示，航迹保持实际坐标；节点表地面点与作业高度分别表示。'
        else:foot+='\n箭头表示飞行方向；灰色基础航段由多机型共用，彩色虚线突出多点架次。' if question=='Q2' else '\n问题一不分配实体无人机，架次数不代表同时使用的无人机数量。'
        if self.relief:foot+='\n底图：原始附件彩色DEM晕渲与乡界；绿→黄→棕表示高程由低到高，原图为非线性配色。'
        fig.text(.07,.014,foot,fontsize=9,color='#555555')
        self.save(fig,name)

    def facets(self,three,name):
        fig=plt.figure(figsize=(24,13) if three else (22,13))
        fig.subplots_adjust(left=.045,right=.98,top=.88,bottom=.105,wspace=.20 if three else .22,hspace=.25)
        for index in range(8):
            unit=f'U{index+1:02}';rows=[r for r in self.segments if r['unit']==unit]
            ss=[s for s in self.sorties if s['unit']==unit];drone=ss[0]['drone']
            ax=fig.add_subplot(2,4,index+1,projection='3d',computed_zorder=False) if three else fig.add_subplot(2,4,index+1)
            if three:self.base3d(ax,True)
            else:self.base2d(ax,True)
            groups=self.groups_for(rows,name+'_'+unit)
            if three:self.route3d(ax,groups,True)
            else:self.route2d(ax,groups,True)
            self.nodes_plot(ax,rows,three,True)
            labels='、'.join(s['sortie'].replace('Q2-','') for s in ss)
            ax.set_title(f'{unit} · {drone}型 · {len(ss)}架次\n架次编号：{labels}',fontsize=11,color=COLORS[drone],pad=9)
        fig.suptitle('问题二 · 8架实体无人机'+('三维地形航迹' if three else '二维路线'),fontsize=23,y=.984)
        extra=f'高程显示夸张{self.exaggeration:g}倍；统一视角与坐标范围' if three else '统一范围与水平比例；浅灰节点表示该机未访问'
        fig.text(.5,.945,f'36架次按U01-U08分面 · {extra}',ha='center',fontsize=13)
        fig.legend(handles=[Line2D([],[],color=COLORS[g],lw=2,label=f'{g}型') for g in 'ABC'],loc='upper center',bbox_to_anchor=(.5,.932),ncols=3,frameon=False,fontsize=12)
        foot='×n表示该机在共用航段的累计飞行次数（含去返）。完全重合航迹不作空间偏移；各架次访问顺序见逐架次CSV。\n'
        foot+='O01为起降中心；服务区作业高度为节点地面以上30 m。三维图的地形抽稀与高程夸张仅用于显示，不参与能耗或净空核验。'
        if self.relief:foot+='\n底图：原始附件彩色DEM晕渲；黑白线为镇龙乡界。绿→黄→棕为非线性高程配色，航线颜色与底图颜色无关。'
        fig.text(.045,.024,foot,fontsize=11,color='#555555')
        self.save(fig,name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=None)
    parser.add_argument('--background',choices=['source-dem','pale'],default='source-dem',help='原始彩色晕渲或旧浅色DEM')
    parser.add_argument('--z-exaggeration',type=float,default=5.)
    args=parser.parse_args()
    check(math.isfinite(args.z_exaggeration) and args.z_exaggeration>0,'高程夸张须为正有限数')
    output=Path(args.output or ('outputs/routes_terrain' if args.background=='source-dem' else 'outputs/routes'))
    if not output.is_absolute():output=PROJECT/output
    protected=[PROJECT.parent/'数据',PROJECT.parent/'写作',*[PROJECT/'outputs'/x for x in ['q1','q2','q1_sensitivity_05_45','visualizations']]]
    check(not any(output.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(output.resolve()) for p in protected),'输出不得覆盖既有工程或结果')
    before={str(f):file_hash(f) for d in protected for f in d.rglob('*') if f.is_file() and not f.name.startswith('~$')}
    files=[PROJECT/'outputs/q1_sensitivity_05_45/tables/results.json',PROJECT/'outputs/q2/tables/results.json']
    q1,q2=[json.loads(p.read_text(encoding='utf-8')) for p in files]
    check(q1['baseline']['reserve']==.2 and q2['metadata']['config']['physics']['reserve']==.2,'两问须为20%主方案')
    check(q2['main']['verification']['passed'],'问题二保存核验未通过')
    config=q1['metadata']['config'];physics=config['physics']
    for k in ['gravity_m_s2','clearance_m','service_height_m']:
        equal(physics[k],q2['metadata']['config']['physics'][k],'两问物理口径')
    source_hashes={}
    for payload in (q1,q2):
        for p,h in payload['metadata']['input_sha256'].items():
            check(file_hash(Path(p))==h,'原始输入已变更: '+p);source_hashes[p]=h
    data=load_inputs(PROJECT/config['paths']['data_root'],PROJECT/config['paths']['template'])
    terrain=Terrain(data.dem_path,data.origin)
    nodes,segments,vertices,sorties=collect(q1,q2,data,terrain,physics)
    relief=None
    if args.background=='source-dem':
        map_path=PROJECT.parent/'数据/镇龙乡地理空间数据/镇龙乡地理空间详情地图.html'
        source_hashes[str(map_path)]=file_hash(map_path)
        relief=SourceRelief(map_path,terrain,nodes)
    font=PROJECT/config['paths']['font'];font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':[font_manager.FontProperties(fname=str(font)).get_name(),'DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':11,'pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
    for folder in ['figures','tables','logs']:(output/folder).mkdir(parents=True,exist_ok=True)
    atlas=Atlas(data,terrain,nodes,segments,sorties,args.z_exaggeration,output,relief)
    for question,three,name in [('Q1',False,NAMES[0]),('Q1',True,NAMES[1]),('Q2',False,NAMES[2]),('Q2',True,NAMES[3])]:
        atlas.overall(question,three,name)
    atlas.facets(False,NAMES[4]);atlas.facets(True,NAMES[5])
    csv_write(output/'tables/节点与作业高度.csv',[{'node':n.id,'longitude':n.lon,'latitude':n.lat,
              'east_km':float(atlas.xy[n.id][0]),'north_km':float(atlas.xy[n.id][1]),'ground_m':n.elevation,
              'working_m':n.elevation+(0 if n.id=='O01' else physics['service_height_m'])} for n in nodes.values()])
    csv_write(output/'tables/逐架次访问顺序.csv',sorties)
    csv_write(output/'tables/逐航段几何与净空.csv',segments)
    csv_write(output/'tables/逐航段三维顶点.csv',vertices)
    csv_write(output/'tables/合并显示航段计数.csv',atlas.groups)
    if relief:
        csv_write(output/'tables/乡界局部坐标.csv',[{'ring':i,'vertex':j,'east_km':float(x),'north_km':float(y)}
                  for i,ring in enumerate(relief.rings) for j,(x,y) in enumerate(ring)])
        (output/'tables/原始乡界.geojson').write_text(json.dumps(relief.geojson,ensure_ascii=False),encoding='utf-8')
    for p,h in before.items():check(file_hash(Path(p))==h,'受保护文件改变: '+p)
    for p,h in source_hashes.items():check(file_hash(Path(p))==h,'输入文件改变: '+p)
    report={'passed':True,'generated_utc':datetime.now(timezone.utc).isoformat(),
            'input_sha256':source_hashes,'result_sha256':{str(p):file_hash(p) for p in files},
            'code_sha256':{str(p):file_hash(p) for p in [Path(__file__),Path(__file__).with_name('route_background.py')]},
            'background':args.background,'source_relief':relief.metadata if relief else None,
            'counts':{'nodes':len(nodes),'q1_sorties':18,'q1_legs':36,'q2_sorties':36,'q2_legs':76,'units':8,'vertices':len(vertices)},
            'geometry_tolerance_m':1e-6,'all_legs_recomputed_with_full_dem':True,'route_continuity_checked':True,
            'xy_projection_checked':True,'grouping_counts_checked':True,'preserved_files':len(before),
            'bounds_km':atlas.bounds,'z_exaggeration':args.z_exaggeration,'view':{'elevation':28,'azimuth':-60,'projection':'orthographic'},
            'display_grids':{str(k):list(v[2].shape) for k,v in atlas.grid_cache.items()},
            'label_layout_checks':atlas.label_checks,
            'optimizer_invoked':False,'dependencies':{p:importlib.metadata.version(p) for p in ['numpy','matplotlib','Pillow','openpyxl']}}
    (output/'logs/validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 无人机二维与三维路线图','',
           '问题一18架次、36条有向航段；问题二36架次、76条有向航段，使用U01—U08。均采用20%安全余量的已保存主方案，没有重新优化。',
           '', '## 图示口径','',
           '二维坐标沿用WGS84局部平面，以O01为原点，水平单位km；地图范围为'+('乡界及全部节点' if relief else '全部节点')+'包围盒四周扩展1 km。三维顶点的高程单位m。',
           '每段四顶点为起点作业高度、起点上方巡航高度、终点上方巡航高度、终点作业高度。由既有航段与节点定义直接构造，无新增能耗公式。服务区作业高度为节点表海拔加30 m，O01使用节点表海拔。',
           f'三维采用仰角28°、方位角−60°和正交投影；高程显示夸张{args.z_exaggeration:g}倍，物理高程和CSV未缩放。轴盒比例按水平km与高程m换算后设置。',
           ('二维保留附件原始1485×1308彩色纹理；' if relief else '二维地形显示最多240×240栅格；')+'三维总览200×200以内、分面85×85以内；降采样不参与几何与净空计算。地形透明显示，航线置于其前景以便检查路线；图像可见性不是避障证明。完整原始DEM的所有相交闭像元用于巡航高度复核。',
           '节点表地面点与DEM分别显示，不强行改平地形。完全重合的航段原位合并；×n为累计飞行次数，含双向，不是架次数。多机型共用基础航段为灰色，其他颜色沿用A蓝/B绿/C玫红。',
           '问题二总览用不同虚线突出3个多点架次，并列出完整访问顺序；问题一不分配实体无人机，不能将18架次理解为18架实体机。各分面仅标注该机访问节点，完整架次均保留在CSV。',
           '', '## 复现与核验','', '`python scripts/plot_routes.py`',
           '', '真实高程比例版本可另存：`python scripts/plot_routes.py --z-exaggeration 1 --output outputs/routes_true_scale`。',
           '逐航段航程、巡航高度、爬升下降与原结果核对，绝对容差1e-6 m；验证逐架次连续性、二维投影与三维水平坐标一致，以及合并计数守恒。CSV保留全部112条航段、448个顶点及54个架次的原始精度数据。日志记录来源哈希、依赖版本与受保护文件核验。',
           '地图显示与坐标比例属于已有模型结果的几何重建和展示选择，见公式来源审计中的路线可视化补充；不增加地形、性能或能量假设。']
    for name in NAMES:lines+=['',f'## {name}','',f'![{name}](figures/{name}.png)']
    if relief:
        lines+=['','## 彩色地形底图来源','',
                '使用原始附件“镇龙乡地理空间详情地图.html”内嵌terrainUrl PNG及terrainBounds经纬度范围，不使用用户截图作地理底图。只解析JSON常量，不执行HTML中的脚本。原图乡界包含3个闭合环，原图16个调度节点与输入节点表经纬度逐一核对一致。',
                '二维原位显示原始晕渲，三维按地理位置双线性采样原图颜色贴至DEM表面；乡界在三维贴合完整DEM，属于地图装饰。纹理插值、乡界贴地与显示网格均不参与航段净空及能耗计算。',
                '原始颜色为非线性分级且含光照晕渲，故不添加未经核实的线性高程色标；颜色仅作地形辅助阅读，高程以原始DEM、三维轴及航迹CSV为准。航线白色描边提高辨识度，几何位置不偏移。',
                '彩色版复现：`python scripts/plot_routes.py --background source-dem`。旧浅色版：`python scripts/plot_routes.py --background pale`。彩色版独立保存到outputs/routes_terrain，旧版outputs/routes保留。']
    (output/'路线图说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'counts':report['counts'],'preserved_files':len(before)},ensure_ascii=False))


if __name__=='__main__':main()
