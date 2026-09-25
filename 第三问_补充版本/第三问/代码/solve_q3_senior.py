"""Q3 extension of the senior Q2 route-pool / millisecond CP-SAT model.

The senior source and archived Q1/Q2 results are read-only. This module adds
certified communication profiles and relay intervals to the same route choices,
box ordering, cumulative resources and frozen weighted objective.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import itertools
import json
import math
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / '.runtime_q3'))
from ortools.sat.python import cp_model
from q3_senior_adapter import load_senior, SeniorGeometry
from solve_q3_independent import Communication

OUT = ROOT / 'results/question3_from_senior'


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stages_for(p, data, factory, geo):
    """Use physical phase speeds and hold at arrival for the <1ms rounding gap."""
    from uav_rescue.q2.routes import ticks
    d = data.base.drones[p.drone]
    elapsed = ticks(d.preparation + len(p.boxes)*d.loading)
    stages = []
    current = 'O01'
    for nxt in (*p.visits, 'O01'):
        leg = factory.legs[current, nxt]
        a, b = list(geo.xyz(current)), list(geo.xyz(nxt))
        topa, topb = [*a[:2], leg.cruise_altitude], [*b[:2], leg.cruise_altitude]
        t = elapsed/1000
        for kind, aa, bb, seconds in [
            ('爬升', a, topa, leg.climb/d.climb_speed),
            ('巡航', topa, topb, leg.distance/d.cruise_speed),
            ('下降', topb, b, leg.descent/d.descent_speed),
        ]:
            stages.append(dict(kind=kind, leg=[current,nxt], a=aa,b=bb,start=t,end=t+seconds))
            t += seconds
        elapsed += ticks(factory.times[p.drone,current,nxt])
        if elapsed/1000 > t+1e-10:
            stages.append(dict(kind='量化等待',leg=[current,nxt],a=b,b=b,start=t,end=elapsed/1000))
        if nxt != 'O01':
            local = [i for i in p.boxes if factory.boxes[i].service==nxt]
            end = elapsed + ticks(d.handover) + len(local)*ticks(d.per_box_handover)
            stages.append(dict(kind='投送',leg=[nxt,nxt],a=b,b=b,start=elapsed/1000,end=end/1000))
            elapsed = end
        current = nxt
    assert elapsed == p.duration
    return dict(candidate_id=p.id,boxes=list(p.boxes),model=p.drone,order=list(p.visits),
                energy_kwh=p.energy,mass_kg=p.mass,volume_m3=p.volume,
                duration_s=p.duration/1000,charge_s=p.charge/1000,
                flight_start_s=ticks(d.preparation+len(p.boxes)*d.loading)/1000,
                rounding_s=p.rounding_s,stages=stages)


def choose_stations(geo, count=32):
    source = ROOT/'results/question3_independent/geometry_candidates.json'
    old = json.loads(source.read_text(encoding='utf8'))
    # Recompute all travel energies; geometry is reused only after the 240-leg audit.
    candidates = copy.deepcopy(old['candidates'])
    selected = {}
    previous = json.loads((ROOT/'results/question3_independent/solution_recommended.json').read_text(encoding='utf8'))
    for r in previous['relay']:
        s=copy.deepcopy(r['station_data']);selected[s['id']]=s
    patterns = {}
    for s in candidates:
        covered = tuple(s['covered_needed_services'])
        if len(covered)<2: continue
        margin = min(s['access_margins_db'].values())
        # Keep a fast site and a robust site for each coverage pattern.
        key=(covered,margin>=1.0)
        if key not in patterns or s['earliest_service_s']<patterns[key]['earliest_service_s']:
            patterns[key]=s
    ranked=sorted(patterns.values(),key=lambda s:(-len(s['covered_needed_services']),
                    s['earliest_service_s']+(100 if min(s['access_margins_db'].values())<.1 else 0)))
    for s in ranked:
        if len(selected)>=count:break
        selected.setdefault(s['id'],s)
    for needed in old['needed_services']:
        s=min((v for v in candidates if needed in v['covered_needed_services']),key=lambda v:v['earliest_service_s'])
        selected.setdefault(s['id'],s)
    for s in selected.values():
        s.update(geo.relay_travel((s['x'],s['y'],s['z'])))
        s['backhaul']=geo.link((s['x'],s['y'],s['z']),geo.gateway,'backhaul')
        assert s['backhaul']['available']
    return list(selected.values())


class Profiles:
    def __init__(self,data,factory,geo,stations):
        self.data,self.factory,self.geo,self.stations=data,factory,geo,stations
        self.comm=Communication(geo,stations)
        self.cache={}

    def get(self,p):
        if p.id in self.cache:return self.cache[p.id]
        r=stages_for(p,self.data,self.factory,self.geo)
        direct=self.comm.profile(r,-1)
        if direct is not None:opts=[dict(station=-1,profile=direct)]
        else:
            opts=[]
            needed={s for s in p.visits if not self.geo.link(self.geo.xyz(s),self.geo.gateway)['available']}
            for k,s in enumerate(self.stations):
                if not needed<=set(s['covered_needed_services']):continue
                profile=self.comm.profile(r,k)
                if profile is not None:opts.append(dict(station=k,profile=profile))
        self.cache[p.id]=dict(r,options=opts) if opts else None
        return self.cache[p.id]


def solve(pool,data,profiles,spec,seconds,seed=0,hint=None,workers=8,label='joint',zero_lateness=False):
    from uav_rescue.q2.routes import ticks
    from uav_rescue.q2.weighted import add_objective,score,integer_score
    from uav_rescue.q2.solver import assign_resources
    begun=time.perf_counter()
    pool={pid:p for pid,p in sorted(pool.items()) if profiles.get(p) is not None}
    print('MODEL',label,'routes',len(pool),'stations',len(profiles.stations),flush=True)
    H=24_000_000
    model=cp_model.CpModel()
    delivered={b:model.new_int_var(0,H,'C_'+b) for b in data.timing}
    late={b:model.new_int_var(0,H,'L_'+b) for b in data.timing}
    for b,t in data.timing.items():
        if t.hard is not None:model.add(delivered[b]<=ticks(t.hard))
        model.add_max_equality(late[b],[0,delivered[b]-ticks(t.expected)])
        if zero_lateness:model.add(late[b]==0)
    cmax=model.new_int_var(0,H,'joint_makespan')
    relay=[];relay_intervals=[];relay_energy=[]
    rdata=profiles.geo.relay
    power=rdata['hover_kw']+rdata['communication_kw']
    # One sortie per retained position and at most six initially full components.
    for k,s in enumerate(profiles.stations):
        active=model.new_bool_var(f'r_on{k}')
        start=model.new_int_var(0,H,f'r_start{k}')
        ss=model.new_int_var(0,H,f'r_service{k}')
        duration=model.new_int_var(0,math.floor(s['max_service_s']*1000-1e-7),f'r_dur{k}')
        se=model.new_int_var(0,H,f'r_stop{k}')
        ret=model.new_int_var(0,H,f'r_return{k}')
        size=model.new_int_var(0,H,f'r_size{k}')
        ready=ticks(rdata['prepare_s'])+ticks(s['outbound_s'])+ticks(rdata['setup_s'])
        back=ticks(s['return_s']);turn=ticks(rdata['turnaround_s'])
        model.add(ss==start+ready).only_enforce_if(active)
        model.add(se==ss+duration).only_enforce_if(active)
        model.add(ret==se+back).only_enforce_if(active)
        model.add(size==ret+turn-start).only_enforce_if(active)
        relay_intervals.append(model.new_optional_interval_var(start,size,ret+turn,active,f'r_int{k}'))
        model.add(cmax>=ret).only_enforce_if(active)
        for v in [start,ss,duration,se,ret,size]:model.add(v==0).only_enforce_if(active.Not())
        # Two separate ceilings: total error is <2 nano-kWh per relay sortie.
        fixed=s['travel_energy_kwh']+rdata['setup_s']*power/3600
        numerator=int(round(power*1000))*duration # power in W * milliseconds
        hover=model.new_int_var(0,4_000_000_000,f'r_ehover{k}')
        model.add_division_equality(hover,numerator*10+35,36)
        energy=math.ceil(fixed*1e9)*active+hover
        relay_energy.append(energy)
        relay.append(dict(active=active,start=start,ss=ss,dur=duration,se=se,ret=ret))
    model.add_cumulative(relay_intervals,[1]*len(relay),2)
    model.add(sum(v['active'] for v in relay)<=6)
    select={};starts={};ends={};ranks={};option_vars={}
    by_box={b:[] for b in data.timing}
    airs={g:[] for g in data.units};batteries={g:[] for g in data.units}
    for pid,p in pool.items():
        x=model.new_bool_var('x_'+pid);select[pid]=x
        s=model.new_int_var(0,H-p.duration,'s_'+pid);starts[pid]=s
        e=model.new_int_var(0,H,'e_'+pid);ends[pid]=e
        model.add(s==0).only_enforce_if(x.Not());model.add(e==0).only_enforce_if(x.Not())
        airs[p.drone].append(model.new_optional_interval_var(s,p.duration,e,x,'u_'+pid))
        batteries[p.drone].append(model.new_optional_interval_var(s,p.duration+p.charge,s+p.duration+p.charge,x,'b_'+pid))
        model.add(cmax>=e)
        step=ticks(data.base.drones[p.drone].per_box_handover)
        for service,arrival,base,ids in p.stops:
            slots=[]
            for index,b in enumerate(ids):
                by_box[b].append(x)
                rank=0 if len(ids)==1 else model.new_int_var(0,len(ids)-1,f'rank_{pid}_{b}')
                if len(ids)>1:
                    slots.append(model.new_optional_interval_var(rank,1,rank+1,x,f'slot_{pid}_{b}'))
                    model.add(rank==index).only_enforce_if(x.Not())
                ranks[pid,b]=rank
                model.add(delivered[b]==s+base+(rank+1)*step).only_enforce_if(x)
            if slots:model.add_no_overlap(slots)
        choices=[]
        for j,opt in enumerate(profiles.get(p)['options']):
            y=model.new_bool_var(f'opt_{pid}_{j}');choices.append(y)
            requirements=opt.get('requirements',([dict(station=opt['station'],first=opt['profile']['first'],last=opt['profile']['last'])] if opt['station']>=0 else []))
            for requirement in requirements:
                k=int(requirement['station'])
                v=relay[k];model.add_implication(y,v['active'])
                model.add(s+math.floor(requirement['first']*1000+1e-7)>=v['ss']).only_enforce_if(y)
                model.add(s+math.ceil(requirement['last']*1000-1e-7)<=v['se']).only_enforce_if(y)
        model.add(sum(choices)==x);option_vars[pid]=choices
    for b,choices in by_box.items():
        if not choices:return dict(feasible=False,search=dict(status='UNCOVERED_BOX',box=b,label=label))
        model.add_exactly_one(choices)
    for g in data.units:
        model.add_cumulative(airs[g],[1]*len(airs[g]),len(data.units[g]))
        model.add_cumulative(batteries[g],[1]*len(batteries[g]),data.battery_count[g])
        model.add(sum(p.duration*select[pid] for pid,p in pool.items() if p.drone==g)<=len(data.units[g])*cmax)
    energy=model.new_int_var(0,600_000_000_000,'total_energy')
    model.add(energy==sum(p.energy_int*select[pid] for pid,p in pool.items())+sum(relay_energy))
    metrics=dict(tardiness=sum(t.priority*late[b] for b,t in data.timing.items()),makespan=cmax,
                 energy=energy,sorties=sum(select.values())+sum(v['active'] for v in relay))
    objective=add_objective(model,metrics,dict(tardiness=sum(t.priority for t in data.timing.values())*H,
                            makespan=H,energy=600_000_000_000,sorties=86),spec)
    model.minimize(objective)
    if hint and hint.get('feasible') and not (zero_lateness and hint['metrics']['late_boxes']):
        known={r['candidate_id']:r for r in hint['transport']}
        knownr={r['station']:r for r in hint['relay']}
        for pid,p in pool.items():
            a=known.get(pid)
            model.add_hint(select[pid],int(a is not None))
            model.add_hint(starts[pid],round(a['start_s']*1000) if a else 0)
            model.add_hint(ends[pid],round(a['return_s']*1000) if a else 0)
            if a:
                for _,_,base,ids in p.stops:
                    for b in ids:
                        if not isinstance(ranks[pid,b],int):
                            model.add_hint(ranks[pid,b],round((a['delivery'][b]*1000-base)/ticks(data.base.drones[p.drone].per_box_handover))-1)
                wanted=a.get('communication_option_key',f"single:{a['station']}")
                for j,y in enumerate(option_vars[pid]):
                    option=profiles.get(p)['options'][j]
                    model.add_hint(y,int(option.get('key',f"single:{option['station']}")==wanted))
        for b in data.timing:
            d=next(d for d in hint['deliveries'] if d['box']==b)
            model.add_hint(delivered[b],round(d['time_s']*1000))
        for k,v in enumerate(relay):
            a=knownr.get(k);model.add_hint(v['active'],int(a is not None))
            if a:
                for key,field in [('start','start_s'),('ss','service_start_s'),('se','service_end_s'),('ret','return_s')]:
                    model.add_hint(v[key],round(a[field]*1000))
                model.add_hint(v['dur'],round((a['service_end_s']-a['service_start_s'])*1000))
        # A known feasible joint plan is a valid cutoff, including its rounded energies.
        cutoff=integer_score(hint['objective_vector'],spec)
        assert cutoff==round(hint['search']['integer_objective'])
        model.add(objective<=cutoff)
    solver=cp_model.CpSolver()
    solver.parameters.max_time_in_seconds=seconds
    solver.parameters.num_search_workers=workers
    solver.parameters.random_seed=seed
    solver.parameters.cp_model_probing_level=0
    solver.parameters.max_presolve_iterations=2
    solver.parameters.symmetry_level=0
    status=solver.solve(model)
    meta=dict(label=label,status=solver.status_name(status),seconds=solver.wall_time,seed=seed,workers=workers,zero_lateness=zero_lateness,
              route_pool_count=len(pool),station_pool_count=len(relay),integer_objective=round(solver.objective_value),
              integer_bound=solver.best_objective_bound,model_build_s=time.perf_counter()-begun-solver.wall_time)
    print('SOLVE',json.dumps(meta),flush=True)
    if status==cp_model.MODEL_INVALID:raise RuntimeError(solver.solution_info())
    if status not in (cp_model.FEASIBLE,cp_model.OPTIMAL):return dict(feasible=False,search=meta)
    assignments=[]
    for pid,p in pool.items():
        if solver.value(select[pid]):assignments.append(dict(candidate=pid,start=solver.value(starts[pid]),
                       return_=solver.value(ends[pid]),deliveries={b:solver.value(delivered[b]) for b in p.boxes}))
    for a in assignments:a['return']=a.pop('return_')
    assignments=assign_resources(assignments,pool,data)
    transport=[];deliveries=[]
    for i,a in enumerate(assignments,1):
        p=pool[a['candidate']];r=copy.deepcopy(profiles.get(p))
        j=next(j for j,y in enumerate(option_vars[p.id]) if solver.value(y))
        opt=r.pop('options')[j]
        r.update(id=f'T{i:03}',start_s=a['start']/1000,return_s=a['return']/1000,
            resource_end_s=a['return']/1000,battery_ready_s=a['charge_end']/1000,
            drone=a['unit'],battery=a['battery'],station=opt['station'],communication=opt['profile']['rows'],
            communication_option_key=opt.get('key',f"single:{opt['station']}"),
            delivery={b:(c-a['start'])/1000 for b,c in a['deliveries'].items()},
            delivery_order={s:sorted(ids,key=a['deliveries'].get) for s,_,_,ids in p.stops})
        transport.append(r)
        for b,c in a['deliveries'].items():
            t=data.timing[b]
            deliveries.append(dict(box=b,sortie=r['id'],service=factory_service(data,b),time_s=c/1000,
              due_s=t.expected,first_deadline_s=t.first_deadline,type=b.split('-')[1],priority=t.priority))
    relays=[]
    for k,v in enumerate(relay):
        if not solver.value(v['active']):continue
        ss,se=solver.value(v['ss'])/1000,solver.value(v['se'])/1000
        s=profiles.stations[k]
        e=s['travel_energy_kwh']+(se-ss+rdata['setup_s'])*power/3600
        relays.append(dict(station=k,station_data=s,start_s=solver.value(v['start'])/1000,
             service_start_s=ss,service_end_s=se,return_s=solver.value(v['ret'])/1000,
             resource_end_s=solver.value(v['ret'])/1000+rdata['turnaround_s'],energy_kwh=e,
             soc_percent=100*(1-e/rdata['energy_kwh'])))
    relays.sort(key=lambda r:r['start_s'])
    ready={'R01':0,'R02':0}
    for i,r in enumerate(relays,1):
        free=[u for u,t in ready.items() if t<=r['start_s']+1e-8]
        assert free
        u=min(free,key=ready.get);r.update(id=f'R{i:03}',drone=u,component=f'RE{i:02}')
        ready[u]=r['resource_end_s']
    m=dict(boxes=len(deliveries),transport_sorties=len(transport),relay_sorties=len(relays),
           transport_energy_kwh=math.fsum(r['energy_kwh'] for r in transport),
           relay_energy_kwh=math.fsum(r['energy_kwh'] for r in relays),
           joint_finish_s=max(r['return_s'] for r in transport+relays),
           transport_finish_s=max(r['return_s'] for r in transport),last_delivery_s=max(d['time_s'] for d in deliveries),
           late_boxes=sum(d['time_s']>d['due_s']+1e-9 for d in deliveries),
           weighted_delay_s=math.fsum(d['priority']*max(0,d['time_s']-d['due_s']) for d in deliveries),
           weighted_mean_delivery_s=math.fsum(d['priority']*d['time_s'] for d in deliveries)/sum(t.priority for t in data.timing.values()),
           multi_stop_sorties=sum(len(r['order'])>1 for r in transport))
    m['total_energy_kwh']=m['transport_energy_kwh']+m['relay_energy_kwh']
    vector={k:solver.value(v) for k,v in metrics.items()}
    m['score']=score(vector,spec)
    return dict(feasible=True,search=meta,metrics=m,objective_vector=vector,weighted_spec=spec,
                communication_policy=dict(max_relay_stations_per_sortie=getattr(profiles,'max_relay_stations_per_sortie',1),direct_priority=True),
                transport=transport,relay=relays,deliveries=sorted(deliveries,key=lambda d:d['box']))


def factory_service(data,b):
    return next(x.service for x in data.base.boxes if x.id==b)


def enrich(current,pool,fac,data,profiles,spec,limit,iteration):
    """Senior q2_improved paired move/swap/merge enrichment, communication filtered."""
    def proxy(p):
        return (spec['alpha']['energy']*p.energy_int/spec['span']['energy']
            +spec['alpha']['sorties']/spec['span']['sorties']
            +spec['alpha']['makespan']*p.duration/(len(data.units[p.drone])*spec['span']['makespan']))
    def alternatives(ids):
        required=sorted({fac.boxes[b].service for b in ids})
        if len(required)>3:return []
        found=[]
        for visits in itertools.permutations(required):
            for g in data.units:
                p=fac.make(g,ids,visits)
                if p:pool[p.id]=p;found.append(p)
        return found
    selected=[pool[r['candidate_id']] for r in current['transport']]
    bundles=[];rng=random.Random(271+iteration)
    for p in selected:
        for q in alternatives(p.boxes):bundles.append((proxy(q)-proxy(p),[q.id]))
    for p,q in itertools.combinations(selected,2):
        old=proxy(p)+proxy(q)
        for a in alternatives(p.boxes+q.boxes):bundles.append((proxy(a)-old,[a.id]))
        operations=[]
        for donor,receiver in [(p,q),(q,p)]:
            if len(donor.boxes)>1:
                for b in donor.boxes:operations.append((tuple(v for v in donor.boxes if v!=b),receiver.boxes+(b,)))
        swaps=list(itertools.product(p.boxes,q.boxes));rng.shuffle(swaps)
        for a,b in swaps[:2+iteration*2]:
            operations.append((tuple(v for v in p.boxes if v!=a)+(b,),tuple(v for v in q.boxes if v!=b)+(a,)))
        for ids1,ids2 in operations:
            aa=sorted(alternatives(ids1),key=proxy)[:2];bb=sorted(alternatives(ids2),key=proxy)[:2]
            for a in aa:
                for b in bb:bundles.append((proxy(a)+proxy(b)-old,[a.id,b.id]))
    active={p.id:p for p in selected}
    rng.shuffle(bundles)
    bundles.sort(key=lambda x:x[0]+rng.uniform(0,.015))
    for _,ids in bundles:
        if len(active)+len(set(ids)-set(active))>limit:continue
        if all(profiles.get(pool[pid]) is not None for pid in ids):
            active.update({pid:pool[pid] for pid in ids})
        if len(active)>=limit:break
    print('ENRICH',iteration,'physical_generated',len(pool),'active',len(active),'bundles',len(bundles),flush=True)
    return active


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seconds',type=float,default=90)
    ap.add_argument('--rounds',type=int,default=3)
    ap.add_argument('--limit',type=int,default=350)
    ap.add_argument('--stations',type=int,default=32)
    ap.add_argument('--seed',type=int,default=0)
    ap.add_argument('--name',default='main')
    ap.add_argument('--resume',type=Path)
    ap.add_argument('--zero-lateness',action='store_true',help='Comparison scenario: require every expected deadline, retaining the same objective.')
    args=ap.parse_args()
    data,fac,cfg,source=load_senior()
    geo=SeniorGeometry();stations=choose_stations(geo,args.stations)
    profiles=Profiles(data,fac,geo,stations);pool={}
    for r in source['main']['sorties']:
        p=fac.make(r['drone'],r['boxes'],r['visits']);assert p and p.id==r['candidate'];pool[p.id]=p
    spec=source['spec'];history=[]
    if args.resume:
        best=json.loads(args.resume.read_text(encoding='utf8'))
        history=list(best.get('search_history',[best['search']]))
        # Keep indices of saved relay positions stable across resumed rounds.
        stations=json.loads((OUT/'station_pool.json').read_text(encoding='utf8'))
        profiles=Profiles(data,fac,geo,stations)
        for r in best['transport']:
            p=fac.make(r['model'],r['boxes'],r['order']);pool[p.id]=p
        if args.zero_lateness and best['metrics']['late_boxes']:
            fixed={r['candidate_id']:pool[r['candidate_id']] for r in best['transport']}
            best=solve(fixed,data,profiles,spec,args.seconds,args.seed,
                       label='zero_lateness_seed',zero_lateness=True)
            history.append(best['search'])
    else:
        dump(OUT/'station_pool.json',stations)
        best=solve(pool,data,profiles,spec,args.seconds,args.seed,label='fixed_senior_21',zero_lateness=args.zero_lateness)
        dump(OUT/'solution_fixed_senior_21.json',best)
        history.append(best['search'])
        if not best['feasible']:
            # A previously valid cargo partition is a seed only: reconstruct all
            # its candidates with senior physics and millisecond delivery rules.
            prior=json.loads((ROOT/'results/question3_independent/solution_recommended.json').read_text(encoding='utf8'))
            seedpool={}
            for r in prior['transport']:
                p=fac.make(r['model'],r['boxes'],r['order']);assert p
                pool[p.id]=p;seedpool[p.id]=p
            best=solve(seedpool,data,profiles,spec,args.seconds,args.seed,label='recomputed_feasible_seed',zero_lateness=args.zero_lateness)
            history.append(best['search'])
            dump(OUT/'solution_recomputed_seed.json',best)
    if not best['feasible']:raise RuntimeError('No feasible joint seed; inspect recorded CP-SAT status.')
    def persist():
        best.update(physics=cfg['physics'],senior_source=dict(path=cfg['_adapter']['latest_path'],
            sha256=digest(cfg['_adapter']['latest_path']),summary=source['main']['summary']),
            inputs={str(p.relative_to(ROOT)):digest(p) for p in [*data.base.source_paths,
                ROOT/'数据/无人机应急物资运输基础数据/中继无人机数据.xlsx',ROOT/'数据/无人机应急物资运输基础数据/通信链路参数.xlsx']},
            search_history=history,limitations=[
                'Finite route and hover-position pool; no global optimality claim.',
                'Enrichment generates at most three service areas per transport sortie.',
                'Each transport sortie uses at most one fixed relay position as backup to direct communication.',
                'At most one relay sortie per retained position and six sorties, each using a different initially full component.',
                'Senior g=9.80665, exact physical flight phase speeds; each complete leg is rounded upward to 1ms.',
                'Senior box rank optimization and frozen normalization; Q3 includes both fleets in Cmax, energy and sorties.',
                'Relay setup charged at hover+communication power; relay cruise altitude is max(corridor DEM+50, endpoints).',
                'Energy formulas are the documented simplified interpretation, not a calibrated flight-power model.'])
        dump(OUT/f'solution_{args.name}.json',best)
        dump(OUT/'search_history.json',history)
        print('BEST',json.dumps(best['metrics'],ensure_ascii=False),flush=True)
    persist()
    for iteration in range(args.rounds):
        active=enrich(best,pool,fac,data,profiles,spec,args.limit,iteration+args.seed)
        found=solve(active,data,profiles,spec,args.seconds,args.seed+iteration+1,hint=best,label=f'enriched_{iteration+1}',zero_lateness=args.zero_lateness)
        history.append(found['search'])
        dump(OUT/f'solution_{args.name}_round{iteration+1}.json',found)
        if found['feasible'] and found['metrics']['score']<=best['metrics']['score']+1e-10:best=found
        persist()
    # Refine continuous start-time / relay-window choices on the final partition.
    fixed={r['candidate_id']:pool[r['candidate_id']] for r in best['transport']}
    found=solve(fixed,data,profiles,spec,args.seconds,args.seed+20,hint=best,label='fixed_final_refinement',zero_lateness=args.zero_lateness)
    history.append(found['search'])
    if found['feasible'] and found['metrics']['score']<=best['metrics']['score']+1e-10:best=found
    persist()
    dump(OUT/f'candidate_pool_{args.name}.json',dict(stations=stations,candidates=[asdict(p) for p in pool.values()]))


if __name__=='__main__':main()
