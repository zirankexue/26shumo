"""Create F01/F02 from the verified local-plane Q1 geometry and original DEM.

Figure contract: F01 establishes the common spatial/terrain basis; F02 explains
the conservative clearance and endpoint ascent terms using three binding routes.
Python-only drawing; 180 mm width, editable vector text, 600 dpi PNG exports.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if (ROOT/'.runtime_py').is_dir():
    sys.path.insert(0,str(ROOT/'.runtime_py'))

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

PAPER=ROOT/'第一问'
FIGURES=PAPER/'图片'
DATA=PAPER/'数据'
RESULTS=PAPER/'结果'
BLUE='#376784'
GREEN='#879C8E'
ORANGE='#BD633D'
GRAY='#4D5755'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def cell_interval(x0,y0,x1,y1,row,col):
    low,high=0.,1.
    for origin,delta,bound in ((x0,x1-x0,col),(y0,y1-y0,row)):
        if delta==0:
            if not bound<=origin<=bound+1:
                return None
        else:
            a,b=(bound-origin)/delta,(bound+1-origin)/delta
            low,high=max(low,min(a,b)),min(high,max(a,b))
            if low>high+1e-13:
                return None
    return max(0.,low),min(1.,high)


def profile(sid,solution,dem):
    meta=solution['dem_metadata'];nodes=solution['nodes'];route=solution['routes'][sid]
    lon0,lat0=meta['centre_origin'];sx,sy=meta['pixel_size_degree']
    grid=lambda n:((n['lon']-lon0)/sx+0.5,(lat0-n['lat'])/sy+0.5)
    x0,y0=grid(nodes['O01']);x1,y1=grid(nodes[sid]);cells=[]
    for row,col in route['crossed_cells']:
        interval=cell_interval(x0,y0,x1,y1,row,col)
        assert interval is not None
        a,b=interval
        cells.append(dict(service_id=sid,row_zero_based=row,column_zero_based=col,t_enter=a,t_exit=b,
                          start_distance_m=a*route['distance_m'],end_distance_m=b*route['distance_m'],
                          elevation_m=float(dem[row,col])))
    assert max(c['elevation_m'] for c in cells)==route['max_terrain_m']
    cuts=sorted(set([0.,1.]+[t for c in cells for t in (c['t_enter'],c['t_exit'])]))
    segments=[]
    for a,b in zip(cuts,cuts[1:]):
        if b-a<1e-14:
            continue
        midpoint=(a+b)/2
        active=[c for c in cells if c['t_enter']<=midpoint<=c['t_exit']]
        assert active
        segments.append(dict(service_id=sid,start_distance_m=a*route['distance_m'],end_distance_m=b*route['distance_m'],
                             terrain_upper_envelope_m=max(c['elevation_m'] for c in active)))
    points=[]
    for t in cuts:
        active=[c for c in cells if c['t_enter']-1e-13<=t<=c['t_exit']+1e-13]
        points.append(dict(service_id=sid,distance_m=t*route['distance_m'],terrain_upper_envelope_m=max(c['elevation_m'] for c in active)))
    return cells,segments,points


def configure_fonts():
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        name=font_manager.FontProperties(fname=str(font)).get_name()
    else:
        name='Microsoft YaHei'
        font=Path(font_manager.findfont(name,fallback_to_default=False))
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':[name,'Arial','DejaVu Sans'],
        'font.size':10,'axes.labelsize':10,'axes.titlesize':11,'xtick.labelsize':10,'ytick.labelsize':10,
        'legend.fontsize':10,'axes.unicode_minus':False,'svg.fonttype':'none','pdf.fonttype':42,
        'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.8,'savefig.facecolor':'white',
        'figure.facecolor':'white','axes.facecolor':'white','legend.frameon':False})
    return name,str(font)


def export_figure(fig,stem,width_mm,height_mm):
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer();width,height=fig.bbox.width,fig.bbox.height
    outside=[]
    from matplotlib.text import Text
    for text in fig.findobj(Text):
        if text.get_visible() and text.get_text():
            bounds=text.get_window_extent(renderer)
            if bounds.x0 < -1 or bounds.y0 < -1 or bounds.x1 > width+1 or bounds.y1 > height+1:
                outside.append(text.get_text())
    assert not outside, f'Figure text outside canvas: {outside}'
    outputs=[]
    for ext in ('png','pdf','svg'):
        path=FIGURES/f'{stem}.{ext}'
        fig.savefig(path,dpi=600,facecolor='white')
        outputs.append(dict(file=f'图片/{path.name}',sha256=sha256(path),size_bytes=path.stat().st_size))
    preview=FIGURES/f'{stem}_preview.png'
    fig.savefig(preview,dpi=160,facecolor='white')
    svg=(FIGURES/f'{stem}.svg').read_text(encoding='utf-8')
    assert '<text' in svg
    with Image.open(FIGURES/f'{stem}.png') as image:
        pixel_size=list(image.size);dpi=image.info.get('dpi')
    assert abs(pixel_size[0]-width_mm/25.4*600)<2
    return dict(outputs=outputs,preview=f'图片/{preview.name}',width_mm=width_mm,height_mm=height_mm,
                png_pixels=pixel_size,png_dpi=list(dpi) if dpi else None,
                qa=dict(text_within_canvas=True,svg_editable_text=True,pdf_fonttype=42,font_size_min_pt=10,
                        visual_review='pending',data_geometry_checks=True))


def draw_map(solution,dem):
    nodes=solution['nodes'];meta=solution['dem_metadata'];plane=meta['local_plane'];coords=plane['node_coordinates']
    lon0,lat0=meta['centre_origin'];sx,sy=meta['pixel_size_degree']
    origin=nodes['O01'];ex=plane['longitude_scale_m_per_radian'];ny=plane['latitude_scale_m_per_radian']
    xvals=[c['x_m'] for c in coords.values()];yvals=[c['y_m'] for c in coords.values()]
    xmin,xmax=min(xvals)-1100,max(xvals)+1100;ymin,ymax=min(yvals)-1000,max(yvals)+1050
    x_to_lon=lambda x:origin['lon']+math.degrees(x/ex)
    y_to_lat=lambda y:origin['lat']+math.degrees(y/ny)
    c0=max(0,math.floor((x_to_lon(xmin)-lon0)/sx+.5));c1=min(dem.shape[1],math.ceil((x_to_lon(xmax)-lon0)/sx+.5))
    r0=max(0,math.floor((lat0-y_to_lat(ymax))/sy+.5));r1=min(dem.shape[0],math.ceil((lat0-y_to_lat(ymin))/sy+.5))
    crop=dem[r0:r1,c0:c1]
    assert np.isfinite(crop).all() and not np.any(crop==meta['nodata'])
    project_x=lambda lon:ex*math.radians(lon-origin['lon'])/1000
    project_y=lambda lat:ny*math.radians(lat-origin['lat'])/1000
    extent=[project_x(lon0+(c0-.5)*sx),project_x(lon0+(c1-.5)*sx),
            project_y(lat0-(r1-.5)*sy),project_y(lat0-(r0-.5)*sy)]
    cmap=LinearSegmentedColormap.from_list('muted_terrain',['#F6F6ED','#DDE2CE','#B9C8B0','#8AAB98','#557D70','#34554E'])
    fig=plt.figure(figsize=(180/25.4,143/25.4))
    ax=fig.add_axes([.10,.15,.73,.75]);cbax=fig.add_axes([.87,.20,.027,.61])
    im=ax.imshow(crop,extent=extent,origin='upper',interpolation='nearest',cmap=cmap,vmin=100,vmax=math.ceil(float(crop.max())/100)*100)
    for sid in solution['routes']:
        xy=coords[sid];ax.plot([0,xy['x_m']/1000],[0,xy['y_m']/1000],color=BLUE,lw=.9,alpha=.86,zorder=2)
    offsets={'S001':(8,-2),'S002':(7,5),'S003':(5,7),'S004':(7,5),'S005':(7,4),
             'S006':(-4,-17),'S007':(-43,4),'S008':(-48,5),'S009':(7,4),'S010':(7,4),
             'S011':(-47,-4),'S012':(6,4),'S013':(7,4),'S014':(-32,-18),'S015':(-45,6)}
    for sid in solution['routes']:
        x,y=coords[sid]['x_m']/1000,coords[sid]['y_m']/1000
        ax.scatter(x,y,s=23,facecolor='white',edgecolor=BLUE,lw=1,zorder=3)
        ax.annotate(sid,(x,y),xytext=offsets[sid],textcoords='offset points',fontsize=10,color='#243839',zorder=5,
                    bbox=dict(facecolor='white',edgecolor='none',alpha=.84,pad=.4))
    ax.scatter(0,0,s=150,marker='*',color=ORANGE,edgecolor='white',lw=.8,zorder=6)
    ax.annotate('O01',(0,0),xytext=(8,-17),textcoords='offset points',color='#643423',fontsize=10,fontweight='bold',
                bbox=dict(facecolor='white',edgecolor='none',alpha=.84,pad=.4))
    ax.set(xlim=(extent[0],extent[1]),ylim=(extent[2],extent[3]),xlabel='东向局部坐标 / km',ylabel='北向局部坐标 / km')
    ax.set_aspect('equal',adjustable='box');ax.tick_params(length=3)
    cbar=fig.colorbar(im,cax=cbax);cbar.set_label('地形海拔 / m',labelpad=8);cbar.outline.set_linewidth(.6)
    ax.annotate('北',xy=(.945,.955),xytext=(.945,.80),xycoords='axes fraction',textcoords='axes fraction',
                ha='center',va='bottom',arrowprops=dict(arrowstyle='-|>',color=GRAY,lw=1),color=GRAY,fontsize=10)
    fig.legend(handles=[Line2D([0],[0],color=BLUE,lw=1,label='单点往返航线'),
                        Line2D([0],[0],marker='o',linestyle='none',mfc='white',mec=BLUE,label='服务区'),
                        Line2D([0],[0],marker='*',linestyle='none',color=ORANGE,markersize=10,label='调度中心')],
               loc='lower center',bbox_to_anchor=(.48,.022),ncol=3,columnspacing=1.4,handlelength=1.5)
    info=export_figure(fig,'F01_routes_terrain',180,143);plt.close(fig)
    info.update(id='F01',archetype='image plate + quant',core_conclusion='15个服务区采用同一O01局部椭球平面与DEM基底，第一问航线均为单点往返辐射航线。',
                source_data=['数据/nodes.csv','数据/routes.csv'],raw_dem_crop=dict(rows=[r0,r1],columns=[c0,c1],shape=list(crop.shape),index_end_exclusive=True),
                processing='原DEM按节点边界外扩裁剪；不做空间降采样或平滑，imshow nearest仅进行背景像素显示；固定单调灰绿伪彩色。航线及节点直接取正式坐标。',
                caption='图1 第一问调度中心、服务区与地形分布。坐标采用以O01为原点的WGS84局部椭球平面，单位为km；蓝线表示O01与15个服务区之间的水平往返航线。地形背景直接取题给30米DEM裁剪，节点位置与地面高程取题给节点表。DEM仅作最近邻显示，不参与插值或平滑；正式地形净空计算仍使用完整相交闭像元原值。')
    return info


def draw_profiles(solution,dem):
    selected=['S004','S008','S012'];allcells=[];allsegments=[];allpoints=[];profiles={}
    for sid in selected:
        cells,segments,points=profile(sid,solution,dem);allcells.extend(cells);allsegments.extend(segments);allpoints.extend(points);profiles[sid]=(cells,segments,points)
    write_csv(DATA/'F02_profile_cell_intervals.csv',allcells)
    write_csv(DATA/'F02_profile_envelope_segments.csv',allsegments)
    write_csv(DATA/'F02_profile_envelope_points.csv',allpoints)
    fig,axes=plt.subplots(3,1,figsize=(180/25.4,207/25.4),sharex=True)
    fig.subplots_adjust(left=.115,right=.965,bottom=.135,top=.96,hspace=.33)
    ymax=math.ceil((max(solution['routes'][sid]['cruise_altitude_m'] for sid in selected)+45)/50)*50
    ymin=math.floor((min(c['elevation_m'] for c in allcells)-35)/50)*50
    distances=[]
    for index,(sid,ax) in enumerate(zip(selected,axes)):
        route=solution['routes'][sid];node=solution['nodes'][sid];distance=route['distance_m']/1000;distances.append(distance)
        cells,segments,points=profiles[sid];xs=[];ys=[]
        for segment in segments:
            xs.extend([segment['start_distance_m']/1000,segment['end_distance_m']/1000]);ys.extend([segment['terrain_upper_envelope_m']]*2)
        ax.fill_between(xs,ys,ymin,color=GREEN,alpha=.38,zorder=1);ax.plot(xs,ys,color=GRAY,lw=.75,zorder=2)
        # Closed-cell point contacts may produce conservative isolated peaks.
        for point in points:
            x=point['distance_m'];adjacent=[seg['terrain_upper_envelope_m'] for seg in segments if seg['start_distance_m']-1e-7<=x<=seg['end_distance_m']+1e-7]
            if adjacent and point['terrain_upper_envelope_m']>max(adjacent)+1e-8:
                ax.vlines(x/1000,max(adjacent),point['terrain_upper_envelope_m'],color=GRAY,lw=.75)
        cruise=route['cruise_altitude_m'];start=solution['nodes']['O01']['elevation_m'];end=node['elevation_m']+30
        ax.plot([0,0,distance,distance],[start,cruise,cruise,end],color=BLUE,lw=1.45,zorder=4)
        ax.scatter([0,distance],[start,end],s=28,color=ORANGE,edgecolor='white',lw=.7,zorder=6)
        ax.text(0,start-22,f'O01：{start:.1f} m',ha='left',va='top',fontsize=10)
        ax.text(distance,end-22,f'{sid}：{end:.1f} m',ha='right',va='top',fontsize=10)
        ax.text(distance*.59,cruise+12,f'巡航海拔 {cruise:.1f} m',ha='center',va='bottom',color=BLUE,fontsize=10)
        peak_cells=[c for c in cells if c['elevation_m']==route['max_terrain_m']]
        peak=peak_cells[0];peakx=(peak['start_distance_m']+peak['end_distance_m'])/2000
        ax.annotate('',xy=(peakx,cruise),xytext=(peakx,route['max_terrain_m']),arrowprops=dict(arrowstyle='<->',color=GRAY,lw=.8,shrinkA=0,shrinkB=0))
        side=-1 if peakx>distance*.6 else 1
        clearance_offset=1.6 if peakx>distance*.85 else .16
        ax.text(peakx+side*clearance_offset,cruise-25,'净空50 m',ha='left' if side>0 else 'right',va='center',fontsize=10,
                bbox=dict(facecolor='white',edgecolor='none',alpha=.90,pad=1))
        for x,low,value,ha,label in [(0.20,start,route['outbound_climb_m'],'left','去程爬升'),(distance-.20,end,route['return_climb_m'],'right','返程爬升')]:
            ax.annotate('',xy=(x,cruise),xytext=(x,low),arrowprops=dict(arrowstyle='<->',color=BLUE,lw=.8))
            label_shift=.13 if ha=='left' else (-.48 if peakx>distance*.85 else -.13)
            ax.text(x+label_shift,(low+cruise)/2,f'{label}\n{value:.1f} m',ha=ha,va='center',fontsize=10,color=BLUE,
                    bbox=dict(facecolor='white',edgecolor='none',alpha=.90,pad=1.5))
        ax.set_title(f'{chr(97+index)}  {sid}（{node["name"]}）',loc='left',fontweight='bold',pad=9)
        ax.set_ylim(ymin,ymax);ax.set_ylabel('海拔 / m');ax.grid(axis='y',color='#DBE0DC',lw=.5,zorder=0)
        ax.tick_params(length=3)
    axes[-1].set_xlim(-.18,max(distances)+.24);axes[-1].set_xticks([0,2,4,6,8]);axes[-1].set_xlabel('自调度中心起的水平距离 / km')
    fig.legend(handles=[Patch(facecolor=GREEN,alpha=.45,label='相交像元地形上包络'),Line2D([0],[0],color=BLUE,lw=1.5,label='飞行高度'),
                        Line2D([0],[0],marker='o',linestyle='none',color=ORANGE,label='节点作业海拔')],
               loc='lower center',bbox_to_anchor=(.53,.025),ncol=3,columnspacing=.9,handlelength=1.35)
    info=export_figure(fig,'F02_terrain_profiles',180,207);plt.close(fig)
    info.update(id='F02',archetype='quantitative grid',core_conclusion='巡航高度由沿途最高原始DEM闭像元而非端点海拔决定，去返爬升分别从各端点作业海拔计算。',
                source_data=['数据/F02_profile_cell_intervals.csv','数据/F02_profile_envelope_segments.csv','数据/F02_profile_envelope_points.csv','数据/nodes.csv','数据/routes.csv'],
                processing='独立解析线段与像元闭矩形相交得到t区间，原像元高程构成分段常值保守上包络；若仅接触角点产生孤立更高峰值亦保留。无双线性平滑、无沿线稀疏采样。',
                profile_checks={sid:dict(cells=len(profiles[sid][0]),segments=len(profiles[sid][1]),max_terrain_m=max(c['elevation_m'] for c in profiles[sid][0]),formal_maximum_matches=True) for sid in selected},
                caption='图2 典型航线地形剖面与高度设置。（a）S004，（b）S008，（c）S012。地形曲线为水平直线经过的原始DEM闭像元高程所构成的保守上包络，不做双线性插值。巡航海拔为最高相交像元海拔加50m；去程爬升为巡航海拔减O01地面海拔，返程爬升为巡航海拔减服务区作业海拔。节点地面海拔取题给节点表，服务区作业海拔在其基础上加30m，与沿途DEM高程分别取源。蓝线表示几何飞行高度，并非时间轨迹；第一问返程空载。')
    info['source_data_hashes']={name:sha256(PAPER/name) for name in info['source_data']}
    return info


def main():
    for folder in (FIGURES,DATA,RESULTS):folder.mkdir(parents=True,exist_ok=True)
    source=ROOT/'results/question1_batching/solution.json';solution=json.loads(source.read_text(encoding='utf-8'))
    assert solution['dem_metadata']['distance_crs']=='WGS84_local_ellipsoidal_plane_O01'
    for item in solution['sources'].values():assert sha256(ROOT/item['path'])==item['sha256']
    with Image.open(ROOT/solution['sources']['dem']['path']) as image:dem=np.array(image)
    font_name,font_path=configure_fonts()
    figures=[draw_map(solution,dem),draw_profiles(solution,dem)]
    manifest=dict(created_utc=datetime.now(timezone.utc).isoformat(),backend='Python matplotlib',matplotlib_version=matplotlib.__version__,
                  font_name=font_name,font_file=font_path,baseline_sha256=sha256(source),source_sha256=solution['sources'],figures=figures,
                  statistics_note='确定性几何与物理模型图；无重复实验、误差条或统计显著性检验。',
                  image_integrity='仅裁剪DEM与统一单调伪彩色显示；未改高程、未局部修图、未平滑地形。')
    (RESULTS/'geography_figures.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({f['id']:f['qa'] for f in figures},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
