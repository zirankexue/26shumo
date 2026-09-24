"""Weighted search, four amplified-weight scenarios, and common-weight reassessment."""
from dataclasses import asdict
from pathlib import Path
import copy
import json
import math
import random
import time
import tomllib
from datetime import datetime, timezone
from .weighted import METRICS, specification, score, integer_score, reselect_scenarios
from .routes import RouteFactory, objective, make_assignment
from .solver import solve_pool, assign_resources
from .alns import DESTROY, REPAIR, destroy, repair_candidates, update_weight, ORDERS
from .candidates import prune_pool
from .validate import validate_schedule
from .data import load_scheduling_inputs
from ..common.data import file_hash


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def list_schedule(ids,pool,data,rng,jitter=0.0):
    """Earliest hard latest-start dispatch with full shared-battery availability."""
    todo=list(ids);result=[]
    machines={g:[0]*len(us) for g,us in data.units.items()}
    batteries={g:[0]*data.battery_count[g] for g in data.units}
    noise={pid:rng.uniform(-jitter,jitter) for pid in todo}
    offsets={pid:make_assignment(pool[pid],0,{},data)['deliveries'] for pid in todo}
    while todo:
        options=[]
        for pid in todo:
            p=pool[pid];start=max(min(machines[p.drone]),min(batteries[p.drone]))
            hard=min((round(data.timing[b].hard*1000)-offsets[pid][b] for b in p.boxes if data.timing[b].hard is not None),default=10**15)
            if start>hard:return None
            expected=min(round(data.timing[b].expected*1000)-offsets[pid][b] for b in p.boxes)
            # Hard constraints never traded away. Random dispatch explores equally feasible orders.
            priority=(0,hard+noise[pid],start,pid) if hard<10**15 else (1,expected+noise[pid],start,pid)
            options.append((priority,pid,start))
        _,pid,start=min(options);p=pool[pid]
        a=make_assignment(p,start,{},data);result.append(a)
        mi=min(range(len(machines[p.drone])),key=lambda i:(machines[p.drone][i],i))
        bi=min(range(len(batteries[p.drone])),key=lambda i:(batteries[p.drone][i],i))
        machines[p.drone][mi]=a['return'];batteries[p.drone][bi]=a['return']+p.charge
        todo.remove(pid)
    return result


def fast_repair(current,pool,by_box,factory,spec,dn,rn,rng):
    removed=destroy(current,pool,factory,dn,rng.randint(3,8),rng)
    changed={a['candidate'] for a in removed}
    uncovered={b for pid in changed for b in pool[pid].boxes}
    kept=[a['candidate'] for a in current if a['candidate'] not in changed]
    selected=[]
    while uncovered:
        options_by_box=[]
        boxes=sorted(uncovered)
        if rn=='greedy':boxes=[rng.choice(boxes)]
        for box in boxes:
            options=[]
            for pid in by_box[box]:
                p=pool[pid]
                if not set(p.boxes)<=uncovered:continue
                # Per-route estimate guides repair; final acceptance uses the scheduled global score.
                v=objective([make_assignment(p,0,{},factory.data)],pool,factory.data)
                proxy=sum(spec['alpha'][k]*v[k]/spec['span'][k] for k in METRICS)/len(p.boxes)
                proxy*=rng.uniform(.8,1.2)
                options.append((proxy,pid))
            options.sort()
            if not options:return None
            regret=options[1][0]-options[0][0] if len(options)>1 else float('inf')
            options_by_box.append((-regret,box,options[0][1]))
        _,_,pid=min(options_by_box)
        selected.append(pid);uncovered.difference_update(pool[pid].boxes)
    new_ids=kept+selected
    # Also explore dispatch order when no alternative grouping exists.
    return list_schedule(new_ids,pool,factory.data,rng,jitter=rng.choice([0,60000,180000]))


def run(project,args):
    config_path=project/args.config
    cfg=tomllib.loads(config_path.read_text(encoding='utf-8'))['weighted']
    if args.iterations is not None:cfg['scenario_iterations']=args.iterations
    if args.budget_seconds is not None:
        total=sum(cfg[k] for k in ['initial_seconds','search_seconds','final_seconds'])+4*cfg['scenario_refine_seconds']
        for k in ['initial_seconds','search_seconds','final_seconds','scenario_refine_seconds']:cfg[k]*=args.budget_seconds/total
    output=project/cfg['output']
    if args.report_only:
        from .weighted_report import export
        export(project,output,json.loads((output/'tables/results.json').read_text(encoding='utf-8')))
        return
    if output.exists():
        output.rename(output.with_name(output.name+'_archive_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
    (output/'logs').mkdir(parents=True)
    old_path=project/cfg['reference'];old=json.loads(old_path.read_text(encoding='utf-8'))
    source_hash={str(old_path):file_hash(old_path)}
    for name,digest in old['metadata']['input_sha256'].items():
        if file_hash(Path(name))!=digest:raise ValueError('原始输入与正式结果不一致')
        source_hash[name]=digest
    physics=old['metadata']['config']['physics']
    paths=old['metadata']['config']['paths']
    data=load_scheduling_inputs((project/paths['data_root']).resolve(),(project/paths['template']).resolve())
    factory=RouteFactory(data,physics)
    candidates=project/cfg['candidate_source'];source_hash[str(candidates)]=file_hash(candidates)
    raw=json.loads(candidates.read_text(encoding='utf-8'));pool={}
    for row in raw:
        p=factory.make(row['drone'],row['boxes'],row['visits'])
        if p is None or p.id!=row['id']:raise ValueError('候选物理口径不一致')
        pool[p.id]=p
    references=old['schedules'];vectors=[objective(s,pool,data) for s in references.values()]
    spec=specification(vectors,cfg['weights'],cfg['score_precision'])
    best=min(references.values(),key=lambda s:score(objective(s,pool,data),spec))
    best=copy.deepcopy(best);primary_start=copy.deepcopy(best)
    traces=[];history=[];cloud=[];accepted_solutions=0
    for k,s in references.items():cloud.append({'source':'reference:'+k,'vector':objective(s,pool,data),'accepted':True})
    for r in old['alns_trace']:
        if r['accepted'] and r['candidate'] is not None:cloud.append({'source':'archived:'+r['scheme']+':'+str(r['iteration']),'vector':r['candidate'],'accepted':True})
    provenance='best_archived_weighted_score'
    saved={k:copy.deepcopy(s) for k,s in references.items()}
    protected={a['candidate'] for s in saved.values() for a in s}
    start=time.perf_counter()
    def checked(s):return validate_schedule(assign_resources(s,pool,data),pool,factory)
    def consider(s,source,full=False):
        nonlocal best,provenance
        vector=objective(s,pool,data)
        if full:checked(s)
        if score(vector,spec)<score(objective(best,pool,data),spec)-1e-12:
            checked(s);best=copy.deepcopy(s);provenance=source
        return vector
    def cp(s,sp,seconds,source,active=None,changed=(),improve=True):
        found,stages=solve_pool(active or pool,data,['weighted'],seconds,0,s,1,
                                improve_only=improve,change_candidates=changed,weighted_spec=sp)
        traces.append({'source':source,'stages':stages})
        if found:
            consider(found,source,True)
        return found
    initial=cp(best,spec,cfg['initial_seconds'],'weighted_initial')
    current=initial or best
    # CP-SAT ALNS: route generation and adaptive destroy/repair, explicit scalar acceptance.
    rng=random.Random(0);wd=dict.fromkeys(DESTROY,1.);wr=dict.fromkeys(REPAIR,1.)
    begin=time.perf_counter();iteration=0
    while time.perf_counter()-begin<cfg['search_seconds']:
        iteration+=1;dn=rng.choices(DESTROY,weights=list(wd.values()))[0];rn=rng.choices(REPAIR,weights=list(wr.values()))[0]
        removed=destroy(current,pool,factory,dn,rng.randint(3,8),rng)
        changed={a['candidate'] for a in removed}
        proposal,unlocked=repair_candidates(pool,factory,removed,rn,list(ORDERS.values())[iteration%4],rng,
                                              min(begin+cfg['search_seconds'],time.perf_counter()+2))
        protect=protected|{a['candidate'] for s in [best,current] for a in s}|proposal
        pool=prune_pool(pool,factory,cfg['candidate_limit'],protect,rng)
        fixed={a['candidate'] for a in current}-changed
        active={pid:p for pid,p in pool.items() if pid in fixed or set(p.boxes)<=unlocked}
        before=score(objective(current,pool,data),spec);before_best=score(objective(best,pool,data),spec)
        left=begin+cfg['search_seconds']-time.perf_counter()
        if left<=0:break
        found=cp(current,spec,min(cfg['repair_seconds'],left),f'weighted_alns:{iteration}',active,changed,False)
        accepted=False;value=None;reward=0
        T=.05*(.001/.05)**min(1,(time.perf_counter()-begin)/max(.001,cfg['search_seconds']))
        if found:
            vector=objective(found,pool,data);value=score(vector,spec)
            accepted=value<=before or rng.random()<math.exp(-min(745,(value-before)/T))
            reward=5 if value<before_best else 3 if value<before else 1 if accepted else 0
            if accepted:current=found;cloud.append({'source':f'weighted_alns:{iteration}','vector':vector,'accepted':True})
        update_weight(wd,dn,reward);update_weight(wr,rn,reward)
        history.append({'phase':'weighted','iteration':iteration,'candidate_score':value,'accepted':accepted,
                        'best_primary_score':score(objective(best,pool,data),spec),'destroy':dn,'repair':rn,
                        'destroy_weights':dict(wd),'repair_weights':dict(wr)})
        print(f'加权ALNS {iteration}: F={score(objective(best,pool,data),spec):.9f}',flush=True)
    cp(best,spec,cfg['final_seconds'],'weighted_final')
    weighted_before_scenarios=copy.deepcopy(best)
    # Four independent starts from the same weighted main solution.
    by_box={b:sorted(pid for pid,p in pool.items() if b in p.boxes) for b in factory.boxes}
    scenario_specs={};scenario_schedules={}
    for j,metric in enumerate(METRICS):
        weights=list(cfg['weights']);weights[j]*=cfg['scenario_multiplier']
        sp=specification(vectors,weights,cfg['score_precision']);scenario_specs[metric]=sp
        rng=random.Random(cfg['seeds'][j%len(cfg['seeds'])]);current=copy.deepcopy(weighted_before_scenarios)
        target=copy.deepcopy(current);wd=dict.fromkeys(DESTROY,1.);wr=dict.fromkeys(REPAIR,1.)
        count=cfg['scenario_iterations']
        for step in range(1,count+1):
            dn=rng.choices(DESTROY,weights=list(wd.values()))[0];rn=rng.choices(REPAIR,weights=list(wr.values()))[0]
            old_score=score(objective(current,pool,data),sp);target_score=score(objective(target,pool,data),sp)
            found=fast_repair(current,pool,by_box,factory,sp,dn,rn,rng)
            accepted=False;value=None;reward=0
            if found:
                # Full independent validation precedes every cloud point and archive update.
                vector=consider(found,f'scenario:{metric}:{step}',True)
                value=score(vector,sp);T=.05*(.001/.05)**(step/count)
                accepted=value<=old_score or rng.random()<math.exp(-min(745,(value-old_score)/T))
                reward=5 if value<target_score else 3 if value<old_score else 1 if accepted else 0
                if value<target_score:target=copy.deepcopy(found)
                if accepted:
                    current=found;accepted_solutions+=1
                    cloud.append({'source':f'scenario:{metric}:{step}','vector':vector,'accepted':True})
            update_weight(wd,dn,reward);update_weight(wr,rn,reward)
            history.append({'phase':metric,'iteration':step,'candidate_score':value,'accepted':accepted,
                            'best_primary_score':score(objective(best,pool,data),spec),'destroy':dn,'repair':rn,
                            'destroy_weights':dict(wd),'repair_weights':dict(wr)})
            if step%500==0:print(f'{metric}权重情景 {step}/{count}: 推荐F={score(objective(best,pool,data),spec):.9f}',flush=True)
        refined=cp(target,sp,cfg['scenario_refine_seconds'],f'scenario:{metric}:refine')
        if refined and score(objective(refined,pool,data),sp)<score(objective(target,pool,data),sp):target=refined
        scenario_schedules[metric]=assign_resources(target,pool,data)
        cloud.append({'source':f'scenario:{metric}:final','vector':objective(target,pool,data),'accepted':True})
    result=checked(best)
    cases={'weighted':result,**{'scenario_'+k:checked(s) for k,s in scenario_schedules.items()}}
    schemes={**cases,**old['schemes']}
    complete={'weighted':assign_resources(best,pool,data),**{'scenario_'+k:s for k,s in scenario_schedules.items()},**references}
    for s in complete.values():checked(s)
    assert all(score(objective(best,pool,data),spec)<=score(objective(s,pool,data),spec)+1e-12 for s in complete.values())
    offset=sum(spec['alpha'][k]*spec['lower'][k]/spec['span'][k] for k in METRICS)
    quant_error=integer_score(objective(best,pool,data),spec)/(sum(spec['integer_weights'].values())*spec['precision'])-offset-score(objective(best,pool,data),spec)
    assert -1e-12<=quant_error<1/spec['precision']+1e-12
    if any(file_hash(Path(p))!=h for p,h in source_hash.items()):raise AssertionError('来源文件改变')
    payload={'main':result,'schemes':schemes,'schedules':complete,'spec':spec,'scenario_specs':scenario_specs,
             'cloud':cloud,'weighted_trace':traces,'weighted_history':history,'weighted_provenance':provenance,
             'weighted_start_vector':objective(primary_start,pool,data),
             'weighted_before_scenarios':objective(weighted_before_scenarios,pool,data),
             'objective_vectors':{k:objective(s,pool,data) for k,s in complete.items()},
             'config':cfg,'search_elapsed_s':time.perf_counter()-start,'quantization_error':quant_error,
             'reference_path':str(old_path),'source_sha256':source_hash,'generated_utc':datetime.now(timezone.utc).isoformat(),
             'code_sha256':{str(p):file_hash(p) for p in sorted((project/'src').rglob('*.py'))},
             'accepted_scenario_solutions':accepted_solutions,'pool_size':len(pool),
             'nodes':old['nodes'],'map':old['map'],'resources':old['resources']}
    reselect_scenarios(payload)
    save(output/'tables/results.json',payload)
    save(output/'logs/validation.json',{k:r['verification'] for k,r in payload['schemes'].items()})
    save(output/'logs/solver_trace.json',traces)
    save(output/'logs/alns_trace.json',history)
    from .weighted_report import export
    export(project,output,payload)
    print('加权方案完成：'+json.dumps(result['summary'],ensure_ascii=False),flush=True)
