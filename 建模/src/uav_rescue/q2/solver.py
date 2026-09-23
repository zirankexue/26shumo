from __future__ import annotations
import time
from ortools.sat.python import cp_model
from .routes import ticks, objective


def solve_pool(pool, data, order, seconds, seed, incumbent=None, workers=1):
    """Exact CP-SAT formulation within a fixed route pool; bounded lexicographic search."""
    started=time.perf_counter()
    pool=dict(sorted(pool.items()))
    model=cp_model.CpModel()
    # Every solution uses <= number of boxes candidates. Serial execution gives a safe horizon.
    horizon=len(data.base.boxes)*max(p.duration+p.charge for p in pool.values())
    if incumbent:
        obj=objective(incumbent,pool,data)
        # If tardiness is zero, no box may exceed its expected time.
        if obj['tardiness']==0 and order[0]=='tardiness':
            horizon=min(horizon,int(max(t.expected for t in data.timing.values())*1000)+max(p.duration+p.charge for p in pool.values()))
        if order[0]=='makespan':horizon=min(horizon,obj['makespan']+max(p.charge for p in pool.values()))
        if order[0]=='makespan' or (order[0]=='tardiness' and obj['tardiness']==0):
            horizon=min(horizon,obj['makespan'])
            pool={pid:p for pid,p in pool.items() if p.duration<=horizon}
    select={};starts={};ends={};ranks={};rank_ends={};battery_ends={}
    delivered={b:model.new_int_var(0,horizon,f'C_{b}') for b in data.timing}
    late={b:model.new_int_var(0,horizon,f'L_{b}') for b in data.timing}
    by_box={b:[] for b in data.timing}
    drone_intervals={g:[] for g in data.units};battery_intervals={g:[] for g in data.units}
    for b,t in data.timing.items():
        if t.hard is not None:model.add(delivered[b]<=ticks(t.hard))
        model.add_max_equality(late[b],[0,delivered[b]-ticks(t.expected)])
    for pid,p in pool.items():
        x=model.new_bool_var(f'x_{pid}');select[pid]=x
        s=model.new_int_var(0,horizon-p.duration,f's_{pid}');starts[pid]=s
        e=model.new_int_var(0,horizon,f'e_{pid}');ends[pid]=e
        model.add(s==0).only_enforce_if(x.Not());model.add(e==0).only_enforce_if(x.Not())
        drone_intervals[p.drone].append(model.new_optional_interval_var(s,p.duration,e,x,f'u_{pid}'))
        be=model.new_int_var(0,horizon+p.charge,f'be_{pid}')
        battery_ends[pid]=be
        battery_intervals[p.drone].append(model.new_optional_interval_var(s,p.duration+p.charge,be,x,f'batt_{pid}'))
        model.add(be==0).only_enforce_if(x.Not())
        box_time=ticks(data.base.drones[p.drone].per_box_handover)
        for service,arrival,base_end,ids in p.stops:
            slots=[]
            for index,b in enumerate(ids):
                by_box[b].append(x)
                if len(ids)==1:
                    rank=0
                else:
                    rank=model.new_int_var(0,len(ids)-1,f'rank_{pid}_{b}')
                    re=model.new_int_var(1,len(ids),f'ranke_{pid}_{b}')
                    rank_ends[pid,b]=re
                    slots.append(model.new_optional_interval_var(rank,1,re,x,f'slot_{pid}_{b}'))
                    model.add(rank==index).only_enforce_if(x.Not())
                    model.add(re==index+1).only_enforce_if(x.Not())
                ranks[pid,b]=rank
                model.add(delivered[b]==s+base_end+(rank+1)*box_time).only_enforce_if(x)
            if slots:model.add_no_overlap(slots)
    for b,choices in by_box.items():
        if not choices:return incumbent,[{'status':'UNCOVERED_BOX','box':b}]
        model.add_exactly_one(choices)
    for g in data.units:
        model.add_cumulative(drone_intervals[g],[1]*len(drone_intervals[g]),len(data.units[g]))
        model.add_cumulative(battery_intervals[g],[1]*len(battery_intervals[g]),data.battery_count[g])
    makespan=model.new_int_var(0,horizon,'makespan');model.add_max_equality(makespan,list(ends.values()))
    # Redundant workload inequalities strengthen propagation without changing feasibility.
    for g in data.units:
        model.add(sum(p.duration*select[pid] for pid,p in pool.items() if p.drone==g)<=len(data.units[g])*makespan)
    for b in data.timing:
        model.add(makespan>=min(p.duration for p in pool.values() if b in p.boxes))
    metrics={'tardiness':sum(t.priority*late[b] for b,t in data.timing.items()),'makespan':makespan,
             'energy':sum(p.energy_int*select[pid] for pid,p in pool.items()),'sorties':sum(select.values())}
    if incumbent:model.add(metrics[order[0]]<=objective(incumbent,pool,data)[order[0]])

    def hints(schedule):
        model.clear_hints()
        known={a['candidate']:a for a in schedule or []}
        for pid,p in pool.items():
            a=known.get(pid);model.add_hint(select[pid],int(a is not None));model.add_hint(starts[pid],a['start'] if a else 0)
            model.add_hint(ends[pid],a['return'] if a else 0)
            model.add_hint(battery_ends[pid],a['return']+p.charge if a else 0)
            for _,_,base,ids in p.stops:
                for index,b in enumerate(ids):
                    rank=ranks[pid,b]
                    if not isinstance(rank,int):
                        value=round((a['deliveries'][b]-a['start']-base)/ticks(data.base.drones[p.drone].per_box_handover))-1 if a else index
                        model.add_hint(rank,value);model.add_hint(rank_ends[pid,b],value+1)
        for a in known.values():
            for b,c in a['deliveries'].items():
                model.add_hint(delivered[b],c);model.add_hint(late[b],max(0,c-ticks(data.timing[b].expected)))
        if known:model.add_hint(makespan,max(a['return'] for a in known.values()))

    trace=[];best=incumbent
    for stage,metric in enumerate(order):
        remaining=seconds-(time.perf_counter()-started)
        if remaining<=0.01:break
        if metric=='tardiness' and best and objective(best,pool,data)['tardiness']==0:
            model.add(metrics[metric]==0)
            trace.append({'stage':metric,'status':'OPTIMAL_BY_ZERO_LOWER_BOUND','pool_size':len(pool),'seconds':0.0,'bound':0,'seed':seed,'value':0,'proven':True})
            continue
        hints(best);model.minimize(metrics[metric])
        if best:model.add(metrics[metric]<=objective(best,pool,data)[metric])
        solver=cp_model.CpSolver()
        solver.parameters.max_time_in_seconds=max(0.01,remaining/(len(order)-stage))
        solver.parameters.num_search_workers=workers
        solver.parameters.random_seed=seed
        solver.parameters.cp_model_probing_level=0
        solver.parameters.max_presolve_iterations=2
        solver.parameters.symmetry_level=0
        status=solver.solve(model)
        if status==cp_model.MODEL_INVALID:
            raise RuntimeError(f'CP-SAT模型无效：{solver.solution_info()}')
        if status==cp_model.INFEASIBLE and best:
            raise AssertionError('模型拒绝已知可行方案，需检查建模或提示值，不得静默返回')
        row={'stage':metric,'status':solver.status_name(status),'pool_size':len(pool),'seconds':solver.wall_time,
             'bound':solver.best_objective_bound,'seed':seed}
        if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            best=[]
            for pid,p in pool.items():
                if solver.value(select[pid]):
                    best.append({'candidate':pid,'start':solver.value(starts[pid]),'return':solver.value(ends[pid]),
                                 'deliveries':{b:solver.value(delivered[b]) for b in p.boxes}})
            value=objective(best,pool,data)[metric]
            row.update(value=value,proven=status==cp_model.OPTIMAL)
        elif best:
            value=objective(best,pool,data)[metric]
            row.update(value=value,proven=False,fallback='保留已验证的前级可行解')
        else:
            row.update(proven=False)
            trace.append(row);break
        trace.append(row)
        model.add(metrics[metric]==value)
    return best,trace


def assign_resources(schedule,pool,data):
    """Interval coloring is exact for identical, initially available resources."""
    result=[dict(a) for a in sorted(schedule,key=lambda a:(a['start'],a['candidate']))]
    for g in data.units:
        units={u:0 for u in sorted(data.units[g])}
        batteries={f'BAT-{g}-{i:02}':0 for i in range(1,data.battery_count[g]+1)}
        for a in result:
            p=pool[a['candidate']]
            if p.drone!=g:continue
            free_u=sorted(u for u,t in units.items() if t<=a['start'])
            free_b=sorted(b for b,t in batteries.items() if t<=a['start'])
            if not free_u or not free_b:raise AssertionError('累计资源解无法着色：资源超限')
            a['unit'],a['battery']=free_u[0],free_b[0]
            units[a['unit']]=a['return'];batteries[a['battery']]=a['return']+p.charge
            a['charge_end']=a['return']+p.charge
    for i,a in enumerate(result,1):a['sortie']=f'Q2-{i:03}'
    return result
