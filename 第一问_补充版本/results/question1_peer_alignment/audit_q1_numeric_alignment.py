"""Read-only dry run of Q1 gravity and peer integer-comparison conventions.

No production solver or result is modified. Current peer source is imported with
bytecode writes disabled, using locally verified original input attachments.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
PEER = Path('D:/Desktop/zirankexve')
sys.path.insert(0, str(PEER/'建模/src'))
from uav_rescue.common.data import load_inputs
from uav_rescue.common.physics import safe_payload, trip_energy, trip_times
from uav_rescue.common.terrain import Terrain
from uav_rescue.q1.optimize import Batch, solve, assign_boxes

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def signature(rows):
    return sorted((b.get('service_id', b.get('service')), b.get('model',b.get('drone')), tuple(sorted(b['box_ids']))) for b in rows)

def objective(rows):
    return [len(rows),math.fsum(b['energy_kwh'] for b in rows),math.fsum(b['operation_s'] for b in rows)]

class DryRun:
    def __init__(self, inputs, routes, saved_pools, gravity, integer):
        self.inputs,self.routes,self.gravity,self.integer = inputs,routes,gravity,integer
        self.pools,self.critical,self.states,self.cache={},{},{},{}
        for sid,old in saved_pools.items():
            pool=[]
            for p in old:
                drone=inputs.drones[p['model']]
                energy=trip_energy(drone,routes[sid],p['mass_kg'],gravity)
                flight,work=trip_times(drone,routes[sid],p['box_count'])
                pool.append(Batch(sid,p['model'],tuple(p['counts']),p['mass_kg'],p['volume_m3'],flight,work,energy,1-energy/drone.battery))
            self.pools[sid]=pool
            self.critical[sid]=[100*b.soc for b in pool]
            self.states[sid]=sorted(itertools.product(*(range(n+1) for n in inputs.counts(sid))),key=lambda s:(sum(s),s))
        self.single=[]
        for sid,pool in self.pools.items():
            for i,n in enumerate(inputs.counts(sid)):
                if n:
                    count=tuple(int(j==i) for j in range(4))
                    limit=max(100*b.soc for b in pool if b.counts==count)
                    self.single.append(dict(service=sid,category_index=i,limit_percent=limit))
        self.ceiling=min(x['limit_percent'] for x in self.single)

    def local(self,sid,rho,order=('sorties','energy','time')):
        pool=self.pools[sid]
        # Keep physical event locations separate from feasibility arithmetic tolerance.
        feasible=tuple(i for i,b in enumerate(pool) if 100*b.soc+1e-10>=rho)
        key=(sid,feasible,order)
        if key not in self.cache:
            if self.integer:
                result=solve(sid,self.inputs.counts(sid),[pool[i] for i in feasible],order)
                selected=result.batches if result.feasible else None
            else:
                assert order==('sorties','energy','time')
                zero=(0,0,0,0);dp={zero:(0,0.,0.)};prev={}
                for state in self.states[sid][1:]:
                    best=None;choice=None
                    for i in feasible:
                        b=pool[i];remain=tuple(a-c for a,c in zip(state,b.counts))
                        if remain not in dp:continue
                        old=dp[remain];new=(old[0]+1,old[1]+b.energy,old[2]+b.operation)
                        improve=best is None or new[0]<best[0] or (new[0]==best[0] and (new[1]<best[1]-1e-10 or (abs(new[1]-best[1])<=1e-10 and new[2]<best[2]-1e-7)))
                        if improve:best,choice=new,(remain,i)
                    if best is not None:dp[state],prev[state]=best,choice
                state=self.inputs.counts(sid)
                if state not in dp:selected=None
                else:
                    selected=[]
                    while any(state):state,i=prev[state];selected.append(pool[i])
            self.cache[key]=None if selected is None else tuple(sorted((b.drone,b.counts) for b in selected))
        return self.cache[key]

    def signature(self,rho,order=('sorties','energy','time')):
        if rho>self.ceiling+1e-10:return None
        signatures=tuple((sid,self.local(sid,rho,order)) for sid in self.pools)
        return None if any(x[1] is None for x in signatures) else signatures

    def rows(self,sig):
        result=[]
        for sid,choices in sig:
            available={kind:sorted(b.id for b in self.inputs.boxes if b.service==sid and b.category==kind) for kind in ('医疗物资','饮用水','应急食品','生活卫生用品')}
            categories=list(available)
            for model,counts in choices:
                b=next(x for x in self.pools[sid] if x.drone==model and x.counts==counts)
                ids=[]
                for kind,count in zip(categories,counts):ids.extend(available[kind][:count]);available[kind]=available[kind][count:]
                result.append(dict(service=sid,drone=model,counts=list(counts),box_ids=ids,energy_kwh=b.energy,operation_s=b.operation,return_soc=b.soc))
        return result

    def intervals(self):
        edges=sorted(set([0.,self.ceiling]+[r for values in self.critical.values() for r in values if 0<r<self.ceiling]))
        segments=[]
        for lo,hi in zip(edges,edges[1:]):
            sig=self.signature((lo+hi)/2)
            if segments and sig==segments[-1]['signature']:segments[-1]['upper_percent']=hi
            else:segments.append(dict(lower_percent=lo,upper_percent=hi,signature=sig))
        for segment in segments:
            rows=self.rows(segment['signature']);segment['rows']=rows;segment['objective']=objective(rows)
        return edges,segments

def nondominated(points):
    front=[]
    for p in sorted(points):
        if any(all(a<=b for a,b in zip(q,p)) for q in front):continue
        front=[q for q in front if not all(a<=b for a,b in zip(p,q))];front.append(p)
    return front

def integer_frontier(run):
    locals_={}
    for sid,pool in run.pools.items():
        options=[b for b in pool if b.energy<=run.inputs.drones[b.drone].battery*.8+1e-9]
        dp={(0,0,0,0):[(0,0,0)]}
        for state in run.states[sid][1:]:
            generated=[]
            for b in options:
                rem=tuple(x-y for x,y in zip(state,b.counts))
                for previous in dp.get(rem,[]):generated.append(tuple(a+c for a,c in zip(previous,b.cost)))
            dp[state]=nondominated(generated)
        locals_[sid]=dp[run.inputs.counts(sid)]
    global_=[(0,0,0)]
    for points in locals_.values():global_=nondominated([tuple(a+b for a,b in zip(x,y)) for x in global_ for y in points])
    return [[n,e/1e12,t/1e6] for n,e,t in global_]

def main():
    started=time.perf_counter()
    sys.stdout.reconfigure(encoding='utf-8')
    baseline_path=ROOT/'results/question1_batching/solution.json';reserve_path=ROOT/'results/question1_reserve_sensitivity/sensitivity.json'
    ours=load(baseline_path);reserve=load(reserve_path);saved_pools=load(ROOT/'results/question1_reserve_sensitivity/all_capacity_feasible_batches.json')
    peer_audit_path=PEER/'建模/outputs/q1_objective_audit/results.json';peer_audit=load(peer_audit_path)
    inputs=load_inputs(ROOT/'数据',ROOT/'结果提交模板.xlsx');terrain=Terrain(inputs.dem_path,inputs.origin)
    routes={sid:terrain.route(node,50,30) for sid,node in inputs.services.items()}
    for path in inputs.source_paths:assert sha(path) in peer_audit['metadata']['input_sha256'].values()
    variants={name:DryRun(inputs,routes,saved_pools,g,integer) for name,g,integer in [('old_gravity_float',9.81,False),('peer_gravity_float',9.80665,False),('peer_gravity_integer',9.80665,True)]}
    all_results={};intervals={}
    for name,run in variants.items():
        edges,segments=run.intervals();intervals[name]=segments
        baseline=run.rows(run.signature(20))
        oldplans=reserve['interval_plans']
        same_count=len(segments)==len(oldplans)
        comparisons=[dict(index=i+1,old_lower_percent=old['lower_percent'],new_lower_percent=new['lower_percent'],old_upper_percent=old['upper_percent'],new_upper_percent=new['upper_percent'],same_real_box_grouping=signature(old['batches'])==signature(new['rows']),new_objective=new['objective']) for i,(old,new) in enumerate(zip(oldplans,segments))]
        all_results[name]=dict(gravity=run.gravity,integer_comparison=run.integer,interior_candidate_events=len(edges)-2,elementary_intervals=len(edges)-1,selected_plan_intervals=len(segments),global_limit_percent=run.ceiling,baseline_objective=objective(baseline),baseline_minimum_soc_percent=min(b['return_soc'] for b in baseline)*100,baseline_boxes_identical_to_current=signature(baseline)==signature(ours['batches']),same_plan_interval_count=same_count,all_interval_box_assignments_identical=same_count and all(x['same_real_box_grouping'] for x in comparisons),interval_comparison=comparisons,minimum_distinct_event_gap_percentage_points=min(b-a for a,b in zip(edges,edges[1:])),last_interval_width_percentage_points=segments[-1]['upper_percent']-segments[-1]['lower_percent'])
        print(name,json.dumps({k:v for k,v in all_results[name].items() if k!='interval_comparison'},ensure_ascii=False),flush=True)
    aligned=variants['peer_gravity_integer'];orders=[]
    for saved in peer_audit['comparisons']:
        rows=aligned.rows(aligned.signature(20,tuple(saved['order'])))
        orders.append(dict(order=saved['order'],same_real_box_grouping=signature(rows)==signature(saved['rows']),energy_difference_kwh=objective(rows)[1]-saved['summary']['energy_kwh'],time_difference_s=objective(rows)[2]-saved['summary']['operation_s']))
    payload_change=[]
    for sid,route in routes.items():
        for model,drone in inputs.drones.items():
            fine,_=safe_payload(drone,route,.2,9.80665,1e-7);coarse,_=safe_payload(drone,route,.2,9.80665,1e-6)
            payload_change.append(dict(service=sid,model=model,fine_kg=fine,coarse_kg=coarse,difference_kg=None if fine is None else coarse-fine))
    frontier=integer_frontier(aligned)
    report=dict(created_utc=datetime.now(timezone.utc).isoformat(),peer_commit=subprocess.check_output(['git','-C',str(PEER),'rev-parse','HEAD'],text=True).strip(),
                source_sha256={str(p):sha(p) for p in (baseline_path,reserve_path,peer_audit_path)},variants=all_results,
                all_six_peer_objectives=orders,aligned_complete_integer_frontier=frontier,
                payload_bisection_only_comparison=payload_change,
                interpretation=dict(payload_tolerance='Safe-payload bisection is a reported continuous capacity; candidates use direct energy checks, so changing its stop tolerance cannot itself change batching.',
                integer_cost='Each candidate is independently rounded to 1e-12 kWh and 1e-6 s before costs are added. Rounding accumulated raw sums is not equivalent.',
                event_feasibility='All event positions use physical 100*(1-E/B) with 1e-10 percentage-point equality tolerance; the peer baseline feasibility tolerance is 1e-9 kWh. Tolerances should handle arithmetic only, not be described as physical extra battery energy.',
                finite_precision='Integerized complete frontier is exact for quantized candidate objectives. Raw physical totals should still be exported separately.'),elapsed_seconds=time.perf_counter()-started)
    assert all(x['same_real_box_grouping'] for x in orders)
    output=Path(__file__).with_name('numeric_alignment_dry_run.json');output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('FRONTIER',frontier,'elapsed',report['elapsed_seconds'])

if __name__=='__main__':main()
