"""Independent Q3 transport primitives and reproducible candidate generation.

Reads original workbooks, never any Q2 solution. The horizontal/climb energy
completion is the same explicitly documented assumption as Q1.
"""
from __future__ import annotations
import itertools
import math
import random
from functools import lru_cache
from pathlib import Path
import openpyxl
from solve_q1_batching import read_inputs, local_plane, cells_on_segment
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def charge_time(soc, full_s):
    if soc < .9:
        return full_s * (.65 * (.9-soc)/.9 + .35)
    return full_s * .35 * (1-soc)/.1


class Transport:
    def __init__(self):
        self.files, self.nodes, self.models, self.boxes, _ = read_inputs(ROOT)
        self.xy, self.coordinate_metadata = local_plane(self.nodes)
        self.boxes_by_id = {b['id']: b for b in self.boxes}
        self.ids = list(self.boxes_by_id)
        im = Image.open(self.files['dem'])
        self.dem = np.array(im)
        scale, tie = im.tag_v2[33550], im.tag_v2[33922]
        self.dx, self.dy = scale[:2]
        self.x0 = tie[3]-tie[0]*scale[0]
        self.y0 = tie[4]+tie[1]*scale[1]
        self.fleet = {g: [] for g in self.models}
        self.batteries = {}
        wb = openpyxl.load_workbook(self.files['drones'], read_only=True, data_only=True)
        for row in wb['数据'].iter_rows(min_row=9, max_row=16, values_only=True):
            self.fleet[row[1]].append(row[0])
        for row in wb['数据'].iter_rows(min_row=20, max_row=22, values_only=True):
            self.batteries[row[0]] = dict(count=int(row[1]), full_s=float(row[2]))
        wb.close()
        self.legs = {(i,j): self.make_leg(i,j) for i in self.nodes for j in self.nodes if i!=j}
        self.cache = {}

    def xyz(self, sid):
        n, p = self.nodes[sid], self.xy[sid]
        return [p['x_m'], p['y_m'], n['elevation_m']+(0 if sid=='O01' else 30)]

    def make_leg(self, i, j):
        a,b=self.nodes[i],self.nodes[j]
        ax=(a['lon']-self.x0)/self.dx+.5
        ay=(self.y0-a['lat'])/self.dy+.5
        bx=(b['lon']-self.x0)/self.dx+.5
        by=(self.y0-b['lat'])/self.dy+.5
        cells=set(cells_on_segment(ax,ay,bx,by))
        assert all(0<=r<self.dem.shape[0] and 0<=c<self.dem.shape[1] for r,c in cells)
        heights=[float(self.dem[r,c]) for r,c in cells]
        assert min(heights)>-32000
        h=max(heights)+50
        pa,pb=self.xyz(i),self.xyz(j)
        assert h>=max(pa[2],pb[2]), (i,j,h,pa,pb)
        return dict(i=i,j=j,distance_m=math.dist(pa[:2],pb[:2]), altitude_m=h,
                    climb_m=h-pa[2], descend_m=h-pb[2], crossed_cells=len(cells))

    def route(self, ids, model, order=None):
        key=(tuple(sorted(ids)),model,tuple(order) if order else None)
        if key in self.cache: return self.cache[key]
        bs=[self.boxes_by_id[i] for i in ids]
        g=self.models[model]
        mass=sum(b['mass_kg'] for b in bs)
        vol=sum(b['volume_litre'] for b in bs)
        if not bs or mass>g['payload_kg']+1e-9 or vol>g['volume_litre']:
            self.cache[key]=None
            return None
        sites=sorted({b['service_id'] for b in bs})
        if len(sites)>3: return None
        orders=[tuple(order)] if order else itertools.permutations(sites)
        best=None
        for visit in orders:
            t=g['prepare_s']+len(bs)*g['load_per_box_s']
            stages=[]; delivered={}; energy=0.; q=mass; current='O01'
            for nxt in (*visit,'O01'):
                leg=self.legs[current,nxt]
                rng=g['empty_range_m']-(g['empty_range_m']-g['full_range_m'])*(q/g['payload_kg'])**1.5
                e=g['energy_kwh']*leg['distance_m']/rng+9.81*(g['empty_mass_kg']+q)*leg['climb_m']/(g['climb_efficiency']*3.6e6)
                energy+=e
                a,b=self.xyz(current),self.xyz(nxt)
                topa=[a[0],a[1],leg['altitude_m']];topb=[b[0],b[1],leg['altitude_m']]
                for kind,p0,p1,dur in [('爬升',a,topa,leg['climb_m']/g['climb_mps']),
                                       ('巡航',topa,topb,leg['distance_m']/g['cruise_mps']),
                                       ('下降',topb,b,leg['descend_m']/g['descend_mps'])]:
                    stages.append(dict(kind=kind,leg=[current,nxt],a=p0,b=p1,start=t,end=t+dur))
                    t+=dur
                if nxt!='O01':
                    unload=[v for v in bs if v['service_id']==nxt]
                    dur=g['handover_base_s']+len(unload)*g['handover_per_box_s']
                    stages.append(dict(kind='投送',leg=[nxt,nxt],a=b,b=b,start=t,end=t+dur))
                    t+=dur
                    # Conservative common completion: all boxes at this visit complete at handover end.
                    for v in unload:delivered[v['id']]=t
                    q-=sum(v['mass_kg'] for v in unload)
                current=nxt
            if energy>g['energy_kwh']*(1-g['reserve_percent']/100)+1e-10: continue
            latest=20000.
            for v in bs:
                deadlines=[]
                if v['type']=='MED':deadlines.append(v['due_s'])
                if v['first_batch']=='是':deadlines.append(v['first_deadline_s'])
                if deadlines:latest=min(latest,min(deadlines)-delivered[v['id']])
            if latest<0: continue
            r=dict(boxes=sorted(ids),model=model,order=list(visit),mass_kg=mass,volume_m3=vol/1000,
                   energy_kwh=energy,duration_s=t,flight_start_s=g['prepare_s']+len(bs)*g['load_per_box_s'],
                   stages=stages,delivery=delivered,latest_start_s=latest,
                   charge_s=charge_time(1-energy/g['energy_kwh'],self.batteries[model]['full_s']))
            # Retain best visiting order for this candidate; other orders are inserted explicitly separately.
            score=(sum(self.boxes_by_id[b]['priority']*max(0,tt-self.boxes_by_id[b]['due_s']) for b,tt in delivered.items()),t+energy*25)
            if best is None or score<best[0]:best=(score,r)
        result=best[1] if best else None
        self.cache[key]=result
        return result

    def candidates(self, seeds=60):
        pool={}
        def add(ids,g,order=None):
            r=self.route(ids,g,order)
            if r is not None:pool[(tuple(r['boxes']),g,tuple(r['order']))]=r
            return r
        # Guaranteed per-box options, and compact within-area combinations.
        for bid in self.ids:
            for g in self.models:add([bid],g)
        for site in sorted(self.nodes):
            ids=[b['id'] for b in self.boxes if b['service_id']==site]
            for g in self.models:
                # Type-identical later boxes are handled by randomized construction.
                for k in range(2,min(len(ids),4)+1):
                    for seq in itertools.combinations(ids,k):add(seq,g)
                add(ids,g)
        constructions=[]
        for seed in range(seeds):
            rng=random.Random(seed)
            remain=set(self.ids); selected=[]
            while remain:
                def urgency(bid):
                    b=self.boxes_by_id[bid]
                    due=min(b['due_s'],b['first_deadline_s'] or 1e9)
                    return due+rng.uniform(0,2400),-b['priority']
                anchor=min(sorted(remain),key=urgency)
                gs=['C','B','A'] if seed%3==0 else rng.sample(list(self.models),3)
                options=[]
                for g in gs:
                    r=add([anchor],g)
                    if r is None:continue
                    group=[anchor]
                    for _ in range(9):
                        trials=[]
                        for bid in sorted(remain-set(group)):
                            s0=self.boxes_by_id[anchor]['service_id'];s1=self.boxes_by_id[bid]['service_id']
                            if s0!=s1 and self.legs[s0,s1]['distance_m']>7000:continue
                            rr=self.route(group+[bid],g)
                            if rr is None:continue
                            delta=(rr['duration_s']-r['duration_s'])+ (rr['energy_kwh']-r['energy_kwh'])*rng.uniform(80,300)
                            delta+=rng.uniform(0,500)
                            trials.append((delta,bid,rr))
                        if not trials:break
                        _,bid,r=min(trials,key=lambda v:v[0]);group.append(bid)
                        if rng.random()<.12:break
                    r=add(group,g)
                    score=(r['duration_s']+r['energy_kwh']*(100+seed%7*70))/len(group)**(.75+seed%4*.12)
                    options.append((score*rng.uniform(.85,1.15),r))
                if not options:raise RuntimeError('No feasible route for '+anchor)
                r=min(options,key=lambda z:z[0])[1]
                remain.difference_update(r['boxes']);selected.append(r)
            constructions.append(selected)
        return list(pool.values()),constructions
