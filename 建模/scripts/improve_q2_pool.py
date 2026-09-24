"""Bounded candidate enrichment experiment; fixed audited physics and weights.

Reads complete published schedules, never checkpoints. No plotting or spreadsheets.
"""
from pathlib import Path
import sys
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
import run_q2
from dataclasses import asdict
from itertools import permutations, combinations
import copy,json,math,random,time
from uav_rescue.q2.routes import RouteFactory,objective
from uav_rescue.q2.solver import solve_pool,assign_resources
from uav_rescue.q2.validate import validate_schedule
from uav_rescue.q2.data import load_scheduling_inputs
from uav_rescue.q2.weighted import score
from uav_rescue.common.data import file_hash

def main():
    begun=time.perf_counter()
    source=PROJECT/'outputs/q2_weighted/tables/results.json'
    src=json.loads(source.read_text(encoding='utf8'))
    old_path=Path(src['reference_path'])
    old=json.loads(old_path.read_text(encoding='utf8'))
    cfg=old['metadata']['config'];paths=cfg['paths']
    data=load_scheduling_inputs((PROJECT/paths['data_root']).resolve(),(PROJECT/paths['template']).resolve())
    fac=RouteFactory(data,cfg['physics']);pool={}
    for result in src['schemes'].values():
        for r in result['sorties']:
            p=fac.make(r['drone'],r['boxes'],r['visits'])
            assert p is not None and p.id==r['candidate']
            pool[p.id]=p
    spec=src['spec'];best=copy.deepcopy(src['schedules']['weighted'])
    out=PROJECT/'outputs/q2_improved';out.mkdir(parents=True,exist_ok=True)
    logfile=out/'pool_experiment.log'
    records=[];snapshots={};origin=copy.deepcopy(best)
    hashes={str(source):file_hash(source),str(old_path):file_hash(old_path),**old['metadata']['input_sha256']}
    log=logfile.open('w',encoding='utf8')
    def say(s): print(s,flush=True);log.write(s+'\n');log.flush()
    def checked(s):return validate_schedule(assign_resources(s,pool,data),pool,fac)
    checked(best)
    def run(active,seconds,seed,label):
        nonlocal best
        before=objective(best,pool,data)
        found,stages=solve_pool(active,data,['weighted'],seconds,seed,best,2,weighted_spec=spec)
        if found:
            result=checked(found);v=objective(found,pool,data)
            if score(v,spec)<score(before,spec)-1e-12:best=copy.deepcopy(found)
            snapshots[label]={'schedule':assign_resources(found,pool,data),'result':result,'vector':v,'score':score(v,spec)}
        records.append({'phase':label,'pool_size':len(active),'stages':stages,'before':before,
                        'after':objective(best,pool,data),'score':score(objective(best,pool,data),spec)})
        say(json.dumps(records[-1],ensure_ascii=False))
        persist()
    def persist():
        result=checked(best)
        payload={'source':str(source),'spec':spec,'physics':cfg['physics'],'source_sha256':hashes,
                 'initial_vector':objective(origin,pool,data),'best_vector':objective(best,pool,data),
                 'initial_score':score(objective(origin,pool,data),spec),'best_score':score(objective(best,pool,data),spec),
                 'schedule':assign_resources(best,pool,data),'result':result,'records':records,'snapshots':snapshots,
                 'candidates':[asdict(p) for p in pool.values()],'elapsed_s':time.perf_counter()-begun,
                 'workers':2,'checkpoint_read':False,'script_sha256':file_hash(Path(__file__))}
        (out/'pool_experiment.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
    run({a['candidate']:pool[a['candidate']] for a in best},25,0,'fixed_group_schedule')

    def alternatives(ids):
        required=sorted({fac.boxes[b].service for b in ids})
        orders=permutations(required) if len(required)<=3 else [required,required[::-1]]
        found=[]
        for visits in orders:
            for g in ['A','B','C']:
                p=fac.make(g,ids,visits)
                if p:pool[p.id]=p;found.append(p)
        return found
    def proxy(p):
        return spec['alpha']['energy']*p.energy_int/spec['span']['energy']+spec['alpha']['sorties']/spec['span']['sorties']+spec['alpha']['makespan']*p.duration/(len(data.units[p.drone])*spec['span']['makespan'])
    reference_ids=set(pool)
    for iteration in range(4):
        rng=random.Random(271+iteration)
        current=[pool[a['candidate']] for a in best]
        bundles=[]
        for p in current:
            for alt in alternatives(p.boxes):bundles.append((proxy(alt)-proxy(p),[alt.id]))
        # Keep both sides of every proposed move/swap as an inseparable enrichment bundle.
        pairs=list(combinations(current,2));rng.shuffle(pairs)
        for p,q in pairs:
            oldcost=proxy(p)+proxy(q)
            for alt in alternatives(p.boxes+q.boxes):bundles.append((proxy(alt)-oldcost,[alt.id]))
            operations=[]
            for donor,receiver in [(p,q),(q,p)]:
                if len(donor.boxes)>1:
                    for b in donor.boxes:
                        operations.append((tuple(z for z in donor.boxes if z!=b),receiver.boxes+(b,)))
            swap_pairs=list((a,b) for a in p.boxes for b in q.boxes)
            rng.shuffle(swap_pairs)
            for a,b in swap_pairs[:max(2,iteration*2)]:
                operations.append((tuple(z for z in p.boxes if z!=a)+(b,),tuple(z for z in q.boxes if z!=b)+(a,)))
            for ids1,ids2 in operations:
                aa=sorted(alternatives(ids1),key=proxy)[:2];bb=sorted(alternatives(ids2),key=proxy)[:2]
                for a in aa:
                    for b in bb:bundles.append((proxy(a)+proxy(b)-oldcost,[a.id,b.id]))
        mandatory=reference_ids|{a['candidate'] for a in best}
        active={pid:pool[pid] for pid in mandatory}
        # Mix strongest cost improvements with diverse feasible routing changes.
        rng.shuffle(bundles)
        bundles.sort(key=lambda x:x[0]+rng.uniform(0,.03 if iteration%2 else .01))
        for _,ids in bundles:
            if len(active)+len(set(ids)-set(active))>480:continue
            active.update({pid:pool[pid] for pid in ids})
            if len(active)>=480:break
        say(f'enrichment {iteration+1}: evaluated={len(pool)}, active={len(active)}, bundles={len(bundles)}')
        run(active,75,iteration,f'enriched_{iteration+1}')
        if time.perf_counter()-begun>450:break
    assert all(file_hash(Path(p))==h for p,h in hashes.items())
    persist();log.close()

if __name__=='__main__':main()
