from __future__ import annotations
from dataclasses import asdict
import json
import random
import time
from pathlib import Path
from .candidates import initial_pool,greedy_schedule,prune_pool,neighborhood
from .solver import solve_pool,assign_resources
from .routes import objective
from .validate import validate_schedule


def optimize(factory,opt,output,cache):
    started=time.perf_counter();data=factory.data;order=opt['primary_order'];trace=[]
    pool=initial_pool(factory)
    print(f'初始物理可行候选：{len(pool)}，其中多点{sum(len(p.visits)>1 for p in pool.values())}。',flush=True)
    solutions=[s for seed in range(24) if (s:=greedy_schedule(factory,pool,seed)) is not None]
    key=lambda s:tuple(objective(s,pool,data)[k] for k in order)
    best=min(solutions,key=key) if solutions else None
    if opt.get('resume_checkpoint'):
        resumed=json.loads(Path(opt['resume_checkpoint']).read_text(encoding='utf-8'))
        for raw in resumed['candidates']:
            p=factory.make(raw['drone'],raw['boxes'],raw['visits'])
            if p is None or p.id!=raw['id']:raise ValueError('检查点与当前物理输入不一致')
            pool[p.id]=p
        validate_schedule(resumed['schedule'],pool,factory)
        if best is None or key(resumed['schedule'])<key(best):best=resumed['schedule']
        (output/'logs/resumed_from.json').write_text(json.dumps(resumed,ensure_ascii=False,indent=2),encoding='utf-8')
    protected={a['candidate'] for s in solutions[:4] for a in s}
    if best:protected.update(a['candidate'] for a in best)
    pool=prune_pool(pool,factory,opt['candidate_limit'],protected,random.Random(0))
    def checkpoint(label):
        if not best:return
        final=assign_resources(best,pool,data)
        checked=validate_schedule(final,pool,factory)
        payload={'label':label,'schedule':final,'candidates':[asdict(pool[a['candidate']]) for a in final],
                 'result':checked,'trace':trace}
        (output/'logs/checkpoint.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f"{label}：W={checked['summary']['weighted_tardiness_s']:.3f}，最晚返航={checked['summary']['makespan_s']:.3f}s，能耗={checked['summary']['energy_kwh']:.6f}kWh，{len(best)}架次。",flush=True)
    checkpoint('构造初解')
    repair_seconds=0
    if best:
        # Cheap schedule repair of the constructed partition before the large master model.
        repair_start=time.perf_counter()
        repaired,stages=solve_pool({a['candidate']:pool[a['candidate']] for a in best},data,order,min(20,opt['initial_seconds']/3),0,best,opt['workers'])
        repair_seconds=time.perf_counter()-repair_start
        if repaired and key(repaired)<key(best):best=repaired
        trace.append({'phase':'initial_fixed_routes','stages':stages})
    best,stages=solve_pool(pool,data,order,max(.1,opt['initial_seconds']-repair_seconds),0,best,opt['workers'])
    trace.append({'phase':'initial','stages':stages});checkpoint('初始调度')
    if best is None:
        (output/'logs/no_feasible_solution.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
        raise RuntimeError('搜索尚未找到完整可行调度；不等于原问题不可行，见诊断日志')
    initial=validate_schedule(assign_resources(best,pool,data),pool,factory)
    search_start=time.perf_counter()
    total_rounds=len(opt['seeds'])*opt['rounds_per_seed'];round_no=0
    for seed in opt['seeds']:
        rng=random.Random(seed)
        for step in range(opt['rounds_per_seed']):
            remaining=opt['search_seconds']-(time.perf_counter()-search_start)
            if remaining<=0:break
            round_no+=1
            neighborhood(pool,factory,best,rng)
            pool=prune_pool(pool,factory,opt['candidate_limit'],[a['candidate'] for a in best],rng)
            budget=max(0.1,(opt['search_seconds']-(time.perf_counter()-search_start))/(total_rounds-round_no+1))
            active_pool=pool
            # Most rounds reconfigure 6--10 routes; others use the entire master pool.
            # Outside boxes have only their incumbent candidate, but all start times stay free.
            if round_no%5:
                tail=sorted(best,key=lambda a:a['return'],reverse=True)
                changed=tail[:3]+rng.sample(tail[3:],min(3+step,len(tail[3:])))
                changed_ids={a['candidate'] for a in changed}
                unlocked={b for a in changed for b in pool[a['candidate']].boxes}
                fixed={a['candidate'] for a in best if a['candidate'] not in changed_ids}
                active_pool={pid:p for pid,p in pool.items() if pid in fixed or set(p.boxes)<=unlocked}
            next_best,stages=solve_pool(active_pool,data,order,budget,seed,best,opt['workers'])
            if next_best is not None and key(next_best)<=key(best):best=next_best
            trace.append({'phase':'search','seed':seed,'round':step+1,'restricted_neighborhood':len(active_pool)<len(pool),'stages':stages})
            checkpoint(f'搜索{round_no}/{total_rounds}')
    best,stages=solve_pool(pool,data,order,opt['final_seconds'],0,best,opt['workers'])
    trace.append({'phase':'final','stages':stages});checkpoint('主方案完成')
    main=validate_schedule(assign_resources(best,pool,data),pool,factory)
    comparison,stages=solve_pool(pool,data,opt['comparison_order'],opt['comparison_seconds'],0,best,opt['workers'])
    trace.append({'phase':'comparison','stages':stages})
    contrast=validate_schedule(assign_resources(comparison,pool,data),pool,factory)
    (cache/'candidates.json').write_text(json.dumps([asdict(p) for p in pool.values()],ensure_ascii=False,indent=2),encoding='utf-8')
    return {'main':main,'comparison':contrast,'initial':initial,'trace':trace,'pool_size':len(pool),'search_elapsed_s':time.perf_counter()-started,
            'schedule':assign_resources(best,pool,data),'comparison_schedule':assign_resources(comparison,pool,data)}
