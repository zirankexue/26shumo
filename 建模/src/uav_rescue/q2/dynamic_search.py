"""Dynamic feasible route neighborhoods with exact local box ordering.

No physical parameters or normalization scales are changed. Proxy costs only
order proposals; acceptance and saved recommendations use the full schedule F.
"""
from __future__ import annotations

import copy
from itertools import product
import math
import random
import time

from .candidates import variants
from .fast_schedule import Scheduler
from .routes import objective
from .solver import assign_resources
from .validate import validate_schedule
from .weighted import score


def improve(factory, spec, initial, seconds=360, seeds=(0, 1, 2), callback=None):
    """Return complete independently checked best, trace and all used candidates.

    initial is a list of (source, schedule, candidate-dict). Fast evaluators may
    reject a feasible grouping because their dispatch scope is restricted; such
    rejection is not a proof that the grouping or original problem is infeasible.
    """
    data=factory.data
    scheduler=Scheduler(data,spec)
    pool={pid:p for _,_,ps in initial for pid,p in ps.items()}
    best_source,best_schedule,_=min(initial,key=lambda x:score(objective(x[1],pool,data),spec))
    best_schedule=copy.deepcopy(best_schedule)
    best_vector=objective(best_schedule,pool,data);best_f=score(best_vector,spec)
    best_plan=[pool[a['candidate']] for a in sorted(best_schedule,key=lambda a:(a['start'],a['candidate']))]
    trace=[];evaluations=0;feasible=0;iteration=0;accepted=0
    started=time.perf_counter()
    operators=('merge','move','swap','repartition','type','split','order')
    rewards={name:1. for name in operators}
    counts={name:0 for name in operators}
    variant_cache={}

    def route_options(ids, rng, old=None):
        ids=tuple(sorted(ids))
        if not ids:return [None]
        key=(ids,old)
        if key not in variant_cache:
            found=[]
            for visits in variants(factory,ids,rng,old):
                for g in data.base.drones:
                    p=factory.make(g,ids,visits)
                    if p:
                        found.append(p);pool[p.id]=p
            variant_cache[key]=found
        return variant_cache[key]

    def proxy(p):
        if p is None:return 0.
        return (spec['alpha']['energy']*p.energy_int/spec['span']['energy']
                +spec['alpha']['sorties']/spec['span']['sorties']
                +spec['alpha']['makespan']*p.duration/len(data.units[p.drone])/spec['span']['makespan'])

    def priority(plan):
        def latest(p):
            bounds=[round(data.timing[b].hard*1000)-base-round(data.base.drones[p.drone].per_box_handover*1000)
                    for _,_,base,ids in p.stops for b in ids if data.timing[b].hard is not None]
            soft=min(round(data.timing[b].expected*1000)-base
                     for _,_,base,ids in p.stops for b in ids)
            return min(bounds,default=10**12),soft,-p.duration,p.id
        return [p.id for p in sorted(plan,key=latest)]

    def evaluate(plan, rng, reschedule=True):
        nonlocal evaluations,feasible,best_plan,best_schedule,best_vector,best_f,best_source
        choices=[None]
        if reschedule:choices.append(priority(plan))
        result=None
        for order in choices:
            evaluations+=1
            value=scheduler.evaluate(plan,priority=order)
            if value is None:continue
            feasible+=1
            if result is None or value[2]<result[2]:result=value
        if result is not None and result[2]<best_f-1e-12:
            schedule,vector,f=result
            assigned=assign_resources(schedule,pool,data)
            checked=validate_schedule(assigned,pool,factory)
            assert vector==objective(schedule,pool,data)
            best_schedule=copy.deepcopy(assigned);best_vector=dict(vector);best_f=f
            best_plan=[pool[a['candidate']] for a in sorted(schedule,key=lambda a:(a['start'],a['candidate']))]
            best_source=f'dynamic:{iteration}'
            entry={'iteration':iteration,'elapsed_s':time.perf_counter()-started,'score':f,
                   'vector':vector,'summary':checked['summary']}
            trace.append(entry)
            if callback:callback(entry,best_schedule,pool)
        return result

    def replace(plan, indices, options):
        changed=dict(zip(indices,options));return [changed.get(i,p) for i,p in enumerate(plan) if changed.get(i,p) is not None]

    def combinations(option_lists,rng,limit=12):
        if any(not opts for opts in option_lists):return []
        pairs=list(product(*option_lists))
        pairs.sort(key=lambda values:(sum(proxy(p) for p in values),tuple(p.id if p else '' for p in values)))
        if len(pairs)<=limit:return pairs
        return pairs[:max(2,limit//3)]+rng.sample(pairs[max(2,limit//3):],limit-max(2,limit//3))

    for seed in seeds:
        if time.perf_counter()-started>=seconds:break
        rng=random.Random(seed)
        segment_end=started+seconds*(list(seeds).index(seed)+1)/len(seeds)
        plan=list(best_plan)
        initial_eval=evaluate(plan,rng)
        if initial_eval is None:
            # Start from an independently known grouping whose earliest dispatch is feasible.
            options=[]
            for _,schedule,_ in initial:
                trial=[pool[a['candidate']] for a in sorted(schedule,key=lambda a:(a['start'],a['candidate']))]
                ev=evaluate(trial,rng)
                if ev is not None:options.append((ev[2],trial,ev))
            if not options:continue
            _,plan,initial_eval=min(options,key=lambda x:x[0])
        current=initial_eval
        plan=[pool[a['candidate']] for a in current[0]]
        stagnant=0
        while time.perf_counter()<segment_end:
            iteration+=1;stagnant+=1
            op=rng.choices(operators,weights=[rewards[k] for k in operators])[0];counts[op]+=1
            left=rng.randrange(len(plan));a=plan[left]
            if len(plan)>1:
                def distance(j):
                    return min(0 if sa==sb else factory.legs[sa,sb].distance for sa in a.visits for sb in plan[j].visits)
                neighbors=sorted((j for j in range(len(plan)) if j!=left),key=lambda j:(distance(j),j))
                right=rng.choice(neighbors[:min(7,len(neighbors))] if rng.random()<.85 else neighbors)
                b=plan[right]
            else:right=left;b=a
            proposals=[]
            if op=='order':
                trial=list(plan)
                same=[j for j,p in enumerate(plan) if j!=left and p.drone==a.drone]
                if not same:continue
                right=rng.choice(same)
                if rng.random()<.5:trial[left],trial[right]=trial[right],trial[left]
                else:trial.insert(right,trial.pop(left))
                proposals=[trial]
            elif op=='type':
                proposals=[replace(plan,[left],[p]) for p in route_options(a.boxes,rng,a.visits) if p.id!=a.id]
            elif op=='split':
                if len(a.boxes)<2:continue
                ordered=list(a.boxes)
                if rng.random()<.5:rng.shuffle(ordered)
                else:ordered.sort(key=lambda x:(data.timing[x].hard or 10**9,data.timing[x].expected,-data.timing[x].priority,x))
                cut=rng.randrange(1,len(ordered))
                for x,y in combinations([route_options(ordered[:cut],rng),route_options(ordered[cut:],rng)],rng,6):
                    trial=replace(plan,[left],[x]);trial.insert(left+1,y);proposals.append(trial)
            elif left!=right:
                aa=list(a.boxes);bb=list(b.boxes)
                if op=='merge':
                    for p in route_options(aa+bb,rng):proposals.append(replace(plan,[left,right],[p,None]))
                else:
                    if op=='move':
                        n=rng.randint(1,min(3,len(aa)));moved=rng.sample(aa,n)
                        aa=[x for x in aa if x not in moved];bb+=moved
                    elif op=='swap':
                        na=rng.randint(1,min(3,len(aa)));nb=rng.randint(1,min(3,len(bb)))
                        x=rng.sample(aa,na);y=rng.sample(bb,nb)
                        aa=[z for z in aa if z not in x]+y;bb=[z for z in bb if z not in y]+x
                    elif op=='repartition':
                        joined=aa+bb
                        if rng.random()<.5:rng.shuffle(joined)
                        else:joined.sort(key=lambda x:(data.timing[x].hard or 10**9,data.timing[x].expected,-data.timing[x].priority,x))
                        cut=rng.randrange(1,len(joined));aa=joined[:cut];bb=joined[cut:]
                    for x,y in combinations([route_options(aa,rng),route_options(bb,rng)],rng):
                        if x and y and x.id==a.id and y.id==b.id:continue
                        proposals.append(replace(plan,[left,right],[x,y]))
            rng.shuffle(proposals)
            candidate=None
            before_best=best_f
            for trial in proposals:
                if time.perf_counter()>=segment_end:break
                ev=evaluate(trial,rng,reschedule=op!='order')
                if ev is not None and (candidate is None or ev[2]<candidate[2]):candidate=ev
            reward=0
            if candidate is not None:
                progress=min(1,(time.perf_counter()-started)/seconds)
                # Search temperature only; saved recommendation always uses exact F.
                temperature=.008*(.0001/.008)**progress
                delta=candidate[2]-current[2]
                if delta<=0 or rng.random()<math.exp(-min(745,delta/temperature)):
                    accepted+=1;reward=5 if best_f<before_best else 3 if delta<0 else 1
                    current=candidate;plan=[pool[a['candidate']] for a in current[0]]
                    if delta<0:stagnant=0
            rewards[op]=max(.2,.9*rewards[op]+.1*reward)
            if stagnant>=400:
                ev=evaluate(best_plan,rng)
                if ev is not None:current=ev;plan=[pool[a['candidate']] for a in current[0]]
                stagnant=0
            if len(factory.memo)>200000:
                factory.memo={};variant_cache.clear()
    checked=validate_schedule(assign_resources(best_schedule,pool,data),pool,factory)
    return {'main':checked,'schedule':assign_resources(best_schedule,pool,data),
            'vector':best_vector,'score':best_f,'source':best_source,'trace':trace,
            'seconds':time.perf_counter()-started,'iterations':iteration,'evaluations':evaluations,
            'feasible_evaluations':feasible,'accepted_moves':accepted,'operator_counts':counts,
            'operator_final_weights':rewards,'pool':pool}
