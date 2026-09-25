"""Controlled Q3 experiments inspired by the four local agent implementations.

Retains senior physics/objective and exact communication acceptance. Experiments
expand route seeds, targeted hover positions and per-segment relay association.
All results are new files; the preceding recommended solution stays unchanged.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'.runtime_q3'))
from q3_senior_adapter import load_senior,SeniorGeometry
from solve_q3_senior import Profiles,solve,stages_for,enrich,dump
from solve_q3_independent import Communication

OUT=ROOT/'results/question3_agent_update'
BASE=ROOT/'results/question3_from_senior'


def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def station_at(geo,x,y,agl):
    ground=geo.terrain(x,y);z=ground+agl
    bh=geo.link((x,y,z),geo.gateway,'backhaul')
    if not bh['available']:return None
    travel=geo.relay_travel((x,y,z))
    if travel['max_service_s']<=0:return None
    needed=[s for s in geo.nodes if s!='O01' and not geo.link(geo.xyz(s),geo.gateway)['available']]
    margins={s:geo.link(geo.xyz(s),(x,y,z),'access')['margin_db'] for s in needed}
    covered=[s for s in needed if margins[s]>=-1e-9]
    if not covered:return None
    lon,lat=geo.lonlat(x,y)
    uid=hashlib.sha256(repr((round(x,6),round(y,6),round(z,6))).encode()).hexdigest()[:10]
    return dict(id='L'+uid,x=x,y=y,z=z,lon=lon,lat=lat,backhaul=bh,
        covered_needed_services=covered,access_margins_db={s:margins[s] for s in covered},**travel)


def refine_stations(previous,data,fac,geo,stations,per_group=10):
    """Demand-conditioned 250m/height refinement plus the entire old coarse pool.

    A candidate must continuously support every route in its target group before
    it can enter the refined shortlist. Ranking is a proxy, CP checks timing.
    """
    begun=time.perf_counter();chosen={s['id']:s for s in stations};records=[]
    old=read(ROOT/'results/question3_independent/geometry_candidates.json')['candidates']
    for relay in previous['relay']:
        routes=[r for r in previous['transport'] if any(row['relay'] and row.get('station',r['station'])==relay['station'] for row in r['communication'])]
        required={v for r in routes for v in r['order'] if not geo.link(geo.xyz(v),geo.gateway)['available']}
        pool={s['id']:copy.deepcopy(s) for s in old if required<=set(s['covered_needed_services'])}
        center=relay['station_data']
        for dx,dy in itertools.product(range(-750,751,250),repeat=2):
            for agl in [50.,100.,150.,200.,250.,275.,300.]:
                try:s=station_at(geo,center['x']+dx,center['y']+dy,agl)
                except (ValueError,AssertionError):continue
                if s and required<=set(s['covered_needed_services']):pool[s['id']]=s
        eligible=[];tested=0
        for s in pool.values():
            s.update(geo.relay_travel((s['x'],s['y'],s['z'])))
            com=Communication(geo,[s]);ps=[]
            for r in routes:
                p=com.profile(r,0)
                if p is None:break
                ps.append((r,p))
            tested+=1
            if len(ps)!=len(routes):continue
            first=min(r['start_s']+p['first'] for r,p in ps if p['first'] is not None)
            last=max(r['start_s']+p['last'] for r,p in ps if p['last'] is not None)
            service=last-first
            if service>s['max_service_s']+1e-7:continue
            energy=s['travel_energy_kwh']+(service+geo.relay['setup_s'])*1.1/3600
            lateness=max(0,s['earliest_service_s']-first)
            # Frozen-schedule proxy only; early-arrival and resource feasibility
            # are subsequently solved jointly by the unchanged CP formulation.
            span=previous['weighted_spec']['span'];alpha=previous['weighted_spec']['alpha']
            proxy=alpha['energy']*energy/(span['energy']/1e9)+alpha['makespan']*(last+s['return_s']+lateness)/(span['makespan']/1000)
            eligible.append((proxy,s,dict(first=first,last=last,energy=energy,proxy=proxy)))
        eligible.sort(key=lambda z:z[0])
        kept=eligible[:per_group]
        for _,s,_ in kept:chosen.setdefault(s['id'],s)
        # Also retain fast-to-arrive alternatives to avoid single-proxy bias.
        fast=sorted(eligible,key=lambda z:z[1]['earliest_service_s'])[:3]
        for _,s,_ in fast:chosen.setdefault(s['id'],s)
        records.append(dict(relay=relay['id'],services=sorted(required),route_ids=[r['id'] for r in routes],
             candidates_tested=tested,continuous_group_compatible=len(eligible),kept=[dict(station=s['id'],**v) for _,s,v in kept]))
        print('STATION_GROUP',records[-1]['relay'],'tested',tested,'continuous',len(eligible),'total_kept',len(chosen),flush=True)
    dump(OUT/'station_refinement_audit.json',dict(seconds=time.perf_counter()-begun,groups=records,
        coarse_step_m=500,local_step_m=250,local_offsets_m=[-750,750],height_levels_m=[50,100,150,200,250,275,300],
        selection_scope='Current relay route-groups; proxy shortlist, not global continuous-site optimum.'))
    unique={}
    for s in chosen.values():
        unique.setdefault(tuple(round(s[k],6) for k in ('x','y','z')),s)
    return list(unique.values())


def inherit(found,prior,label):
    if not found.get('feasible'):return found
    for k in ['physics','senior_source','inputs','limitations']:
        found[k]=copy.deepcopy(prior[k])
    found['limitations']=[v for v in found['limitations'] if 'one fixed relay position' not in v]
    max_stations=found.get('communication_policy',{}).get('max_relay_stations_per_sortie',1)
    found['limitations'].append(f'This experiment allows up to {max_stations} relay positions per sortie; each certified interval uses one active relay; finite position/pair pool.')
    found['experiment']=dict(label=label,baseline=str(BASE/'solution_recommended.json'),baseline_sha256=digest(BASE/'solution_recommended.json'))
    return found


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=float,default=75)
    ap.add_argument('--skip-station-refinement',action='store_true');ap.add_argument('--resume',type=Path)
    ap.add_argument('--rounds',type=int,default=2);ap.add_argument('--pool-limit',type=int,default=350)
    args=ap.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    data,fac,cfg,senior=load_senior();geo=SeniorGeometry()
    previous=read(args.resume or BASE/'solution_recommended.json');spec=previous['weighted_spec']
    stations=read(OUT/'station_pool.json') if args.resume else read(BASE/'station_pool.json')
    allpool={}
    def add_rows(rows,senior_format=False):
        for r in rows:
            p=fac.make(r['drone'] if senior_format else r['model'],r['boxes'],r['visits'] if senior_format else r['order'])
            if p:allpool[p.id]=p
    add_rows(senior['main']['sorties'],True)
    for name in ['main','enhanced','zero_lateness']:
        add_rows(read(BASE/f'solution_{name}.json')['transport'])
    add_rows(previous['transport'])
    if (OUT/'deepseek_v2_structure_seed.json').exists():
        seeds=read(OUT/'deepseek_v2_structure_seed.json')['routes']
        add_rows(seeds)
        rebuilt=[dict(index=i,model=r['model'],boxes=r['boxes'],order=r['order'],
                       senior_physical_feasible=fac.make(r['model'],r['boxes'],r['order']) is not None)
                 for i,r in enumerate(seeds)]
        dump(OUT/'foreign_structure_rebuild.json',dict(total=len(seeds),feasible=sum(v['senior_physical_feasible'] for v in rebuilt),routes=rebuilt))
    history=copy.deepcopy(previous.get('search_history',[])) if args.resume else []
    best=previous
    def run(label,pool,profiles,seed):
        nonlocal best
        found=inherit(solve(pool,data,profiles,spec,args.seconds,seed,hint=best,label=label,zero_lateness=True),previous,label)
        history.append(found['search']);dump(OUT/f'solution_{label}.json',found)
        if found.get('feasible'):
            from validate_q3_senior import validate
            validation=validate(OUT/f'solution_{label}.json');dump(OUT/f'validation_{label}.json',validation)
            assert validation['passed'],validation['errors'][:10]
            if found['metrics']['score']<best['metrics']['score']+1e-12:best=found
        dump(OUT/'solution_search_best.json',best)
        dump(OUT/'search_history.json',history)
        print('BEST',label,json.dumps(best['metrics'],ensure_ascii=False),flush=True)
    if not args.resume:
        # Control: unchanged stations and association, only diverse cargo seeds.
        run('route_seed_union',allpool,Profiles(data,fac,geo,stations),41)
    if not args.skip_station_refinement:
        stations=refine_stations(best,data,fac,geo,stations)
    dump(OUT/'station_pool.json',stations)
    if not args.resume:
        fixed={r['candidate_id']:allpool[r['candidate_id']] for r in best['transport']}
        run('targeted_stations',fixed,Profiles(data,fac,geo,stations),42)
    from q3_multi_profiles import MultiProfiles
    active_station_ids=sorted({r['station'] for r in best['relay']}|{r['station'] for r in previous['relay']})
    profiles=MultiProfiles(data,fac,geo,stations,pair_stations=active_station_ids)
    run('interval_relay_switch',allpool,profiles,43)
    for iteration in range(args.rounds):
        active=enrich(best,allpool,fac,data,profiles,spec,args.pool_limit,iteration+6)
        run(f'joint_neighborhood_{iteration+1}',active,profiles,44+iteration)
    # Tight timing/association refinement over the chosen cargo partition.
    fixed={r['candidate_id']:allpool[r['candidate_id']] for r in best['transport']}
    run('fixed_refinement',fixed,profiles,49)
    dump(OUT/'search_candidate_summary.json',dict(generated=len(allpool),final_groups=len(fixed),
        station_count=len(stations),pair_station_indices=active_station_ids,
        candidates=[dict(id=p.id,model=p.drone,boxes=p.boxes,order=p.visits,energy_kwh=p.energy,duration_ms=p.duration) for p in allpool.values()]))
    from q3_refine_timing import refine_labels
    refined,audit=refine_labels(best,data,fac)
    refined['search_history']=history
    dump(OUT/'solution_recommended.json',refined);dump(OUT/'audit_final_label_symmetry.json',audit)
    from validate_q3_senior import validate
    v=validate(OUT/'solution_recommended.json');assert v['passed'],v['errors'][:10]
    dump(OUT/'validation_recommended.json',v)
    print('FINAL',json.dumps(refined['metrics'],ensure_ascii=False),'hard_slack',v['minimum_hard_deadline_slack_s'],flush=True)


if __name__=='__main__':main()
