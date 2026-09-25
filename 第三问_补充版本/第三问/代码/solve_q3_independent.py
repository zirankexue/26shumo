"""Independent candidate-route / interval CP-SAT joint transport-relay solver.

No Q2 files are read. Original input hashes, search restrictions and exact
continuous geometry certificates are saved alongside every feasible plan.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.runtime_q3'))
from ortools.sat.python import cp_model
from q3_transport import Transport, charge_time
from q3_geometry import Geometry


def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')


class Communication:
    def __init__(self, geo, stations):
        self.geo=geo;self.stations=stations;self.cache={}

    def certify(self, a,b,k):
        key=(tuple(a),tuple(b),k)
        if key in self.cache:return self.cache[key]
        geo=self.geo
        points=[] if k<0 else [(self.stations[k]['x'],self.stations[k]['y'],self.stations[k]['z'])]
        # Fail early on actual outage. This is rejection only, never certification.
        for t in (0,.25,.5,.75,1):
            p=tuple(x+t*(y-x) for x,y in zip(a,b))
            if not geo.link(p,geo.gateway)['available'] and not any(geo.link(p,r,'access')['available'] for r in points):
                self.cache[key]=None;return None
        c=geo.segment_certificate(a,b,points)
        self.cache[key]=c if c['proved'] else None
        return self.cache[key]

    def profile(self,r,k):
        rows=[];first=1e10;last=-1
        for st in r['stages']:
            c=self.certify(st['a'],st['b'],k)
            if c is None:return None
            for seg in c['segments']:
                aa=st['start']+(st['end']-st['start'])*seg['t0']
                bb=st['start']+(st['end']-st['start'])*seg['t1']
                relay=seg['source']!='G01'
                if relay:first=min(first,aa);last=max(last,bb)
                rows.append(dict(kind=st['kind'],leg=st['leg'],start=aa,end=bb,
                    relay=relay,proof=seg['proof'],min_margin_db=seg['min_margin_db'],
                    a=[v+seg['t0']*(w-v) for v,w in zip(st['a'],st['b'])],
                    b=[v+seg['t1']*(w-v) for v,w in zip(st['a'],st['b'])]))
        return dict(first=first if last>=0 else None,last=last if last>=0 else None,rows=rows)


def stations_from_search(geo,path,limit=12):
    j=json.loads(path.read_text(encoding='utf8'))
    # Different coverage patterns are important: a single largest coverage site
    # can have a long approach and poor early-deadline coordination.
    candidates=j['candidates']
    selected=[];patterns=set()
    for s in candidates:
        pattern=tuple(s['covered_needed_services'])
        if pattern in patterns or len(pattern)<3:continue
        patterns.add(pattern);selected.append(s)
        if len(selected)>=limit:break
    covered=set().union(*(set(s['covered_needed_services']) for s in selected))
    for needed in j['needed_services']:
        if needed in covered:continue
        s=min((s for s in candidates if needed in s['covered_needed_services']),key=lambda s:s['earliest_service_s'])
        selected.append(s);covered.update(s['covered_needed_services'])
    for s in selected:
        p=(s['x'],s['y'],s['z'])
        s.update(geo.relay_travel(p));s['backhaul']=geo.link(p,geo.gateway,'backhaul')
        assert s['backhaul']['available']
    return selected


def build_pool(data,comm,seeds,save_path):
    routes,_=data.candidates(seeds)
    accepted=[];t0=time.time()
    for idx,r in enumerate(routes):
        sites=set(r['order'])
        options=[]
        direct=comm.profile(r,-1)
        if direct is not None:options.append(dict(station=-1,profile=direct))
        else:
            for k,s in enumerate(comm.stations):
                if not all(site in s['covered_needed_services'] or comm.geo.link(comm.geo.xyz(site),comm.geo.gateway)['available'] for site in sites):continue
                profile=comm.profile(r,k)
                if profile is not None:options.append(dict(station=k,profile=profile))
        if options:
            r=dict(r,options=options);accepted.append(r)
        if idx%500==0:print('comm candidates',idx,len(routes),'accepted',len(accepted),'elapsed',round(time.time()-t0,1),flush=True)
    dump(save_path,dict(stations=comm.stations,routes=accepted,
        unrestricted_generated=len(routes),geometry_cache_entries=len(comm.cache)))
    return accepted


def solve(data,geo,stations,routes,seconds=45,seed=0,policy='balanced',hint=None):
    model=cp_model.CpModel();H=18000;use=[];starts=[];ends=[]
    aircraft={g:[] for g in data.models};battery={g:[] for g in data.models}
    cover=defaultdict(list);alternatives=[]
    late_by_box={bid:model.new_int_var(0,H,f'late_{bid}') for bid in data.ids}
    tardiness=[int(data.boxes_by_id[bid]['priority'])*v for bid,v in late_by_box.items()]
    relay=[];intervals=[];energy_terms=[]
    cmax=model.new_int_var(0,H,'joint_finish')
    # One optional variable-length sortie per retained station; selected sorties
    # share the two physical relay aircraft, and are colored after optimization.
    # At most 6 sorties => 6 initially-full energy components, no reuse necessary.
    for k,s in enumerate(stations):
        active=model.new_bool_var(f'r_active{k}')
        start=model.new_int_var(0,H,f'r_start{k}');ss=model.new_int_var(0,H,f'r_service{k}');se=model.new_int_var(0,H,f'r_stop{k}')
        dur=model.new_int_var(0,math.floor(s['max_service_s']),f'r_hover{k}')
        ret=model.new_int_var(0,H,f'r_return{k}');total=model.new_int_var(0,H,f'r_total{k}')
        ready=math.ceil(s['earliest_service_s']);rt=math.ceil(s['return_s'])
        model.add(ss==start+ready).only_enforce_if(active)
        model.add(se==ss+dur).only_enforce_if(active)
        model.add(ret==se+rt).only_enforce_if(active)
        model.add(total==ret+math.ceil(geo.relay['turnaround_s'])-start).only_enforce_if(active)
        interval=model.new_optional_interval_var(start,total,ret+math.ceil(geo.relay['turnaround_s']),active,f'relay{k}')
        intervals.append(interval)
        model.add(cmax>=ret).only_enforce_if(active)
        for v in (start,ss,se,dur,ret,total):model.add(v==0).only_enforce_if(active.Not())
        relay.append(dict(active=active,start=start,ss=ss,se=se,dur=dur,ret=ret))
        # Integer milli-Wh coefficient: exact linear hover energy, rounded for objective only.
        energy_terms.append(math.ceil(s['travel_energy_kwh']*1e6+geo.relay['setup_s']*1.1/3600*1e6)*active+306*dur)
    model.add_cumulative(intervals,[1]*len(intervals),2)
    model.add(sum(v['active'] for v in relay)<=6)
    for p,r in enumerate(routes):
        x=model.new_bool_var(f'use{p}');s=model.new_int_var(0,min(H,math.floor(r['latest_start_s'])),f's{p}')
        dur=math.ceil(r['duration_s']);end=model.new_int_var(0,H,f'e{p}')
        model.add(end==s+dur).only_enforce_if(x)
        model.add(s==0).only_enforce_if(x.Not());model.add(end==0).only_enforce_if(x.Not())
        use.append(x);starts.append(s);ends.append(end)
        aircraft[r['model']].append(model.new_optional_interval_var(s,dur,end,x,f'air{p}'))
        bd=dur+math.ceil(r['charge_s'])
        battery[r['model']].append(model.new_optional_interval_var(s,bd,s+bd,x,f'bat{p}'))
        model.add(cmax>=end).only_enforce_if(x)
        for bid,tt in r['delivery'].items():
            cover[bid].append(x);b=data.boxes_by_id[bid]
            late=late_by_box[bid]
            model.add(late>=s+math.ceil(tt)-int(b['due_s'])).only_enforce_if(x)
        opts=[]
        for oi,o in enumerate(r['options']):
            y=model.new_bool_var(f'opt{p}_{oi}');opts.append(y)
            k=o['station']
            if k>=0:
                v=relay[k];model.add_implication(y,v['active'])
                model.add(s+math.floor(o['profile']['first'])>=v['ss']).only_enforce_if(y)
                model.add(s+math.ceil(o['profile']['last'])<=v['se']).only_enforce_if(y)
        model.add(sum(opts)==x);alternatives.append(opts)
    for bid in data.ids:model.add(sum(cover[bid])==1)
    for g in data.models:
        model.add_cumulative(aircraft[g],[1]*len(aircraft[g]),len(data.fleet[g]))
        model.add_cumulative(battery[g],[1]*len(battery[g]),data.batteries[g]['count'])
    transport_energy=sum(round(r['energy_kwh']*1e6)*x for r,x in zip(routes,use))
    total_energy=transport_energy+sum(energy_terms)
    n=sum(use)+sum(v['active'] for v in relay)
    # Explicit multiobjective scalarization: delays dominate practical search
    # values; hard medical/first limits already reside in latest_start_s.
    weights={'fast':(30000,1,100000),'balanced':(10000,1,300000),'energy':(1200,1,150000)}
    wt,we,wn=weights[policy]
    model.minimize(100000000*sum(tardiness)+wt*cmax+we*total_energy+wn*n)
    if hint:
        chosen={p['candidate_index']:p for p in hint['transport']}
        for p in range(len(routes)):
            model.add_hint(use[p],int(p in chosen))
            if p in chosen:
                v=chosen[p];model.add_hint(starts[p],int(v['start_s']))
                for j,y in enumerate(alternatives[p]):model.add_hint(y,int(j==v['option_index']))
    solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=seconds
    solver.parameters.num_search_workers=8;solver.parameters.random_seed=seed
    status=solver.solve(model)
    meta=dict(status=solver.status_name(status),objective=solver.objective_value,best_bound=solver.best_objective_bound,
              wall_s=solver.wall_time,branches=solver.num_branches,policy=policy,seed=seed,
              weights=dict(weighted_delay=100000000,completion_s=wt,energy_milliwh=we,total_sorties=wn),
              route_pool_count=len(routes),station_pool_count=len(stations))
    print('solve',meta,flush=True)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):return dict(search=meta,feasible=False)
    trs=[]
    for p,r in enumerate(routes):
        if not solver.value(use[p]):continue
        oi=next(j for j,y in enumerate(alternatives[p]) if solver.value(y))
        o=r['options'][oi];s=solver.value(starts[p]);v={k:x for k,x in r.items() if k!='options'}
        v.update(candidate_index=p,option_index=oi,start_s=s,return_s=s+r['duration_s'],resource_end_s=solver.value(ends[p]),
                 station=o['station'],communication=o['profile']['rows'])
        trs.append(v)
    trs.sort(key=lambda p:(p['start_s'],p['model'],p['boxes']))
    for j,p in enumerate(trs):p['id']=f'T{j+1:03d}'
    rel=[]
    for k,v in enumerate(relay):
        if not solver.value(v['active']):continue
        ss,se=solver.value(v['ss']),solver.value(v['se'])
        s=stations[k]
        e=s['travel_energy_kwh']+(se-ss+geo.relay['setup_s'])*(geo.relay['hover_kw']+geo.relay['communication_kw'])/3600
        rel.append(dict(station=k,station_data=s,start_s=solver.value(v['start']),service_start_s=ss,service_end_s=se,
                        return_s=se+s['return_s'],resource_end_s=solver.value(v['ret'])+geo.relay['turnaround_s'],
                        energy_kwh=e,soc_percent=100*(1-e/geo.relay['energy_kwh'])))
    rel.sort(key=lambda r:r['start_s'])
    for j,r in enumerate(rel):r.update(id=f'R{j+1:03d}',component=f'RE{j+1:02d}')
    def color(items,ids,endkey,idkey):
        ready={u:0. for u in ids}
        for item in sorted(items,key=lambda v:v['start_s']):
            choices=[u for u in ids if ready[u]<=item['start_s']+1e-7]
            assert choices,('Coloring failed',item,ready)
            u=min(choices,key=lambda v:ready[v]);item[idkey]=u;ready[u]=item[endkey]
    for g in data.models:
        ps=[p for p in trs if p['model']==g]
        color(ps,data.fleet[g],'resource_end_s','drone')
        for p in ps:p['battery_ready_s']=p['resource_end_s']+math.ceil(p['charge_s'])
        color(ps,[f'{g}B{i+1:02d}' for i in range(data.batteries[g]['count'])],'battery_ready_s','battery')
    color(rel,['R01','R02'],'resource_end_s','drone')
    deliveries=[dict(box=bid,sortie=p['id'],service=data.boxes_by_id[bid]['service_id'],time_s=p['start_s']+tt,
                     due_s=data.boxes_by_id[bid]['due_s'],first_deadline_s=data.boxes_by_id[bid]['first_deadline_s'],
                     type=data.boxes_by_id[bid]['type'],priority=data.boxes_by_id[bid]['priority']) for p in trs for bid,tt in p['delivery'].items()]
    delays=[max(0,v['time_s']-v['due_s']) for v in deliveries]
    metrics=dict(boxes=len(deliveries),transport_sorties=len(trs),relay_sorties=len(rel),
                 transport_energy_kwh=sum(p['energy_kwh'] for p in trs),relay_energy_kwh=sum(p['energy_kwh'] for p in rel),
                 joint_finish_s=max([p['return_s'] for p in trs+rel]),last_delivery_s=max(v['time_s'] for v in deliveries),
                 late_boxes=sum(t>1e-7 for t in delays),weighted_delay_s=sum(t*v['priority'] for t,v in zip(delays,deliveries)),
                 multi_stop_sorties=sum(len(p['order'])>1 for p in trs))
    metrics['total_energy_kwh']=metrics['transport_energy_kwh']+metrics['relay_energy_kwh']
    return dict(feasible=True,search=meta,metrics=metrics,transport=trs,relay=rel,deliveries=deliveries)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seeds',type=int,default=45);ap.add_argument('--seconds',type=float,default=60)
    ap.add_argument('--stations',type=int,default=12);ap.add_argument('--reuse-pool',action='store_true');ap.add_argument('--policy',default='balanced',choices=['fast','balanced','energy'])
    ap.add_argument('--name',default='balanced');ap.add_argument('--hint')
    ap.add_argument('--fixed-plan',help='Fix previously selected cargo/model/order; jointly refine times and relay sites.')
    ap.add_argument('--improve-margins','--safer-east',dest='improve_margins',action='store_true',
                    help='Replace weak candidate sites by minimum-travel alternatives with >=1 dB point margins and superset coverage.')
    args=ap.parse_args()
    out=ROOT/'results/question3_independent';out.mkdir(parents=True,exist_ok=True)
    data=Transport();geo=Geometry();poolfile=out/'candidate_pool.json'
    if args.reuse_pool or args.fixed_plan:
        p=json.loads(poolfile.read_text(encoding='utf8'));stations=p['stations'];routes=p['routes']
    else:
        stations=stations_from_search(geo,out/'geometry_candidates.json',args.stations)
        routes=build_pool(data,Communication(geo,stations),args.seeds,poolfile)
    original_pool_count=len(routes)
    if args.fixed_plan:
        previous=json.loads(Path(args.fixed_plan).read_text(encoding='utf8'))
        replacements=[]
        if args.improve_margins:
            raw=json.loads((out/'geometry_candidates.json').read_text(encoding='utf8'))
            for k,s in enumerate(stations):
                if min(s['access_margins_db'].values())>=.1:continue
                alternatives=[v for v in raw['candidates'] if set(v['covered_needed_services'])>=set(s['covered_needed_services'])
                    and min(v['access_margins_db'][site] for site in s['covered_needed_services'])>=1.]
                if alternatives:
                    replacement=min(alternatives,key=lambda v:v['travel_energy_kwh'])
                    replacements.append(dict(old=s['id'],new=replacement['id'],old_min_margin_db=min(s['access_margins_db'].values())))
                    stations[k]=replacement
        comm=Communication(geo,stations);small=[]
        for prior in previous['transport']:
            r=data.route(prior['boxes'],prior['model'],prior['order'])
            options=[]
            direct=comm.profile(r,-1)
            if direct is not None:options=[dict(station=-1,profile=direct)]
            else:
                for k,s in enumerate(stations):
                    if not all(site in s['covered_needed_services'] or geo.link(geo.xyz(site),geo.gateway)['available'] for site in r['order']):continue
                    profile=comm.profile(r,k)
                    if profile is not None:options.append(dict(station=k,profile=profile))
            if not options:raise RuntimeError('Fixed route lost all communication coverage: '+prior['id'])
            small.append(dict(r,options=options))
        routes=small
    hint=json.loads(Path(args.hint).read_text(encoding='utf8')) if args.hint else None
    result=solve(data,geo,stations,routes,args.seconds,policy=args.policy,hint=hint)
    result['search']['initial_route_pool_count']=original_pool_count
    result['search']['fixed_plan']=args.fixed_plan
    result['search']['improve_margins']=args.improve_margins
    result['search']['station_replacements']=replacements if args.fixed_plan else []
    sources=list(data.files.values())+[ROOT/'数据/无人机应急物资运输基础数据'/n for n in ['中继无人机数据.xlsx','通信链路参数.xlsx']]
    result['inputs']={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sources}
    result['limitations']=['Finite candidate route and station pool; not a global optimum certificate.',
        'At most three service areas per transport sortie; one fixed relay station per transport sortie except direct-covered segments.',
        'At most one sortie per candidate relay station and six relay sorties total, each assigned a fresh energy component.',
        'Transport horizontal energy Euse*d/L(q) and climb mg*h/eta are explicit completion of underspecified appendix terms.',
        'Relay cruise altitude max(DEM corridor peak+50m,endpoint heights) avoids negative ascent/descent for high hover endpoints.',
        'Relay setup conservatively charged at hover+communication power; all boxes delivered at visit handover end.',
        'One-second start grid; conservative duration and charge ceilings affect resource reuse, not physical flight speeds.']
    dump(out/f'solution_{args.name}.json',result)
    print(json.dumps(result.get('metrics',result['search']),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
