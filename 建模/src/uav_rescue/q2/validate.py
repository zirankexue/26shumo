from __future__ import annotations
from collections import Counter, defaultdict
import math


def validate_schedule(schedule,pool,factory):
    """Recompute from original box attributes; never trust cached route energy/times."""
    data=factory.data;boxes=factory.boxes
    seen=[];unit_use=defaultdict(list);battery_use=defaultdict(list)
    records=[];delivery_rows=[];legs=[];phases=[]
    tolerance=1e-7
    total_rounding=0.0;energy_error=0.0
    ceil_ms=lambda s:math.ceil(s*1000-1e-9)
    for a in schedule:
        if a['start']<0 or not isinstance(a['start'],int):raise AssertionError('开始时刻必须为非负整数毫秒')
        p=pool[a['candidate']];d=data.base.drones[p.drone]
        ids=list(p.boxes);seen.extend(ids)
        if a['unit'] not in data.units[p.drone] or not a['battery'].startswith(f'BAT-{p.drone}-'):raise AssertionError('机型/资源不匹配')
        if not 1<=int(a['battery'].split('-')[-1])<=data.battery_count[p.drone]:raise AssertionError('电池编号越界')
        mass=sum(boxes[b].mass for b in ids);volume=sum(boxes[b].volume for b in ids)
        if mass>d.payload+tolerance or volume>d.volume+tolerance:raise AssertionError('起飞装载超限')
        if set(a['deliveries'])!=set(ids):raise AssertionError('逐箱交付列表不符')
        if set(p.visits)!={boxes[b].service for b in ids} or len(set(p.visits))!=len(p.visits):raise AssertionError('访问节点不符')
        remaining=set(ids);start_ms=a['start'];clock_ms=start_ms+ceil_ms(d.preparation+len(ids)*d.loading)
        takeoff_ms=clock_ms;flight_s=0.;energy=0.;prev='O01';rounding=0.
        for segment,end in enumerate((*p.visits,'O01'),1):
            leg=factory.legs[prev,end]
            q=math.fsum(boxes[b].mass for b in remaining)
            v=math.fsum(boxes[b].volume for b in remaining)
            length=d.empty_range-(d.empty_range-d.full_range)*(q/d.payload)**1.5
            e=d.battery*leg.distance/length+(d.empty_mass+q)*factory.physics['gravity_m_s2']*leg.climb/(3.6e6*d.climb_efficiency)
            t_up=leg.climb/d.climb_speed;t_level=leg.distance/d.cruise_speed;t_down=leg.descent/d.descent_speed
            duration=t_up+t_level+t_down;end_ms=clock_ms+ceil_ms(duration)
            rounding+=ceil_ms(duration)/1000-duration
            legs.append({'sortie':a['sortie'],'segment':segment,'from':prev,'to':end,'payload_kg':q,'volume_m3':v,
                         'distance_m':leg.distance,'cruise_altitude_m':leg.cruise_altitude,'climb_m':leg.climb,'descent_m':leg.descent,
                         'start_s':clock_ms/1000,'end_s':end_ms/1000,'flight_s':duration,'energy_kwh':e})
            at=clock_ms/1000
            for label,dt in [('爬升',t_up),('巡航',t_level),('下降',t_down)]:
                phases.append({'sortie':a['sortie'],'from':prev,'to':end,'phase':label,'start_s':at,'end_s':at+dt});at+=dt
            if end_ms/1000-at>1e-10:phases.append({'sortie':a['sortie'],'from':prev,'to':end,'phase':'毫秒保守裕量','start_s':at,'end_s':end_ms/1000})
            flight_s+=duration;energy+=e;clock_ms=end_ms
            if end!='O01':
                local=sorted((b for b in remaining if boxes[b].service==end),key=lambda b:(a['deliveries'][b],b))
                phases.append({'sortie':a['sortie'],'from':end,'to':end,'phase':'基础交接','start_s':clock_ms/1000,'end_s':clock_ms/1000+d.handover})
                clock_ms+=ceil_ms(d.handover)
                for rank,b in enumerate(local,1):
                    begin=clock_ms;clock_ms+=ceil_ms(d.per_box_handover)
                    if a['deliveries'][b]!=clock_ms:raise AssertionError(f'{b}交接时刻/顺序错误')
                    timing=data.timing[b]
                    if timing.hard is not None and clock_ms>ceil_ms(timing.hard):raise AssertionError(f'{b}违反硬时限')
                    delivery_rows.append({'box':b,'sortie':a['sortie'],'service':end,'rank':rank,'start_s':begin/1000,'completion_s':clock_ms/1000,
                                          'expected_s':timing.expected,'hard_s':timing.hard,'first':timing.first,'priority':timing.priority,
                                          'lateness_s':max(0,clock_ms/1000-timing.expected),'mass_kg':boxes[b].mass,'volume_m3':boxes[b].volume})
                    phases.append({'sortie':a['sortie'],'from':end,'to':end,'phase':'逐箱交接','box':b,'start_s':begin/1000,'end_s':clock_ms/1000})
                    remaining.remove(b)
            prev=end
        if remaining or clock_ms!=a['return'] or clock_ms-start_ms!=p.duration:raise AssertionError('返航时间或载荷不符')
        soc=1-energy/d.battery
        if energy>(1-factory.physics['reserve'])*d.battery+factory.physics['energy_tolerance_kwh']:
            raise AssertionError('往返能量预算超限')
        if soc<factory.physics['reserve']-1e-9:raise AssertionError('返航SOC不足')
        # Independent integration of fast and slow SOC increments.
        charge_s=data.full_charge_s[p.drone]*(max(0,0.9-soc)/0.9*0.65+(1-max(0.9,soc))/0.1*0.35)
        if a['charge_end']!=clock_ms+ceil_ms(charge_s):raise AssertionError('电池充电结束时刻错误')
        energy_error=max(energy_error,abs(energy-p.energy))
        if abs(energy-p.energy)>1e-8:raise AssertionError('缓存能耗与原始数据重算不符')
        unit_use[a['unit']].append((start_ms,clock_ms,a['sortie']))
        battery_use[a['battery']].append((start_ms,a['charge_end'],a['sortie']))
        total_rounding+=rounding
        records.append({'sortie':a['sortie'],'candidate':p.id,'unit':a['unit'],'drone':p.drone,'battery':a['battery'],
                        'start_s':start_ms/1000,'takeoff_s':takeoff_ms/1000,'visits':list(p.visits),'return_s':clock_ms/1000,
                        'energy_kwh':energy,'soc':soc,'charge_s':charge_s,'charge_end_s':a['charge_end']/1000,
                        'mass_kg':mass,'volume_m3':volume,'boxes':ids,'flight_s':flight_s,'operation_s':(clock_ms-start_ms)/1000,'rounding_s':rounding})
    if Counter(seen)!=Counter(boxes.keys()):raise AssertionError('货箱遗漏或重复')
    for resources in [unit_use,battery_use]:
        for name,intervals in resources.items():
            ordered=sorted(intervals)
            if any(left[1]>right[0] for left,right in zip(ordered,ordered[1:])):raise AssertionError(f'{name}占用冲突')
    summary={'sorties':len(records),'energy_kwh':math.fsum(r['energy_kwh'] for r in records),'makespan_s':max(r['return_s'] for r in records),
             'weighted_tardiness_s':math.fsum(r['priority']*r['lateness_s'] for r in delivery_rows),
             'late_boxes':sum(r['lateness_s']>1e-9 for r in delivery_rows),'on_time_boxes':sum(r['lateness_s']<=1e-9 for r in delivery_rows),
             'hard_boxes':sum(r['hard_s'] is not None for r in delivery_rows),'min_soc':min(r['soc'] for r in records),
             'multi_stop_sorties':sum(len(r['visits'])>1 for r in records),'operation_s':sum(r['operation_s'] for r in records),
             'type_counts':dict(Counter(r['drone'] for r in records)),'units_used':len(unit_use),'batteries_used':len(battery_use)}
    return {'summary':summary,'sorties':records,'deliveries':sorted(delivery_rows,key=lambda r:r['box']),'legs':legs,'phases':phases,
            'verification':{'passed':True,'unique_boxes':len(seen),'mass_kg':sum(boxes[b].mass for b in seen),'volume_m3':math.fsum(boxes[b].volume for b in seen),
                            'hard_deadline_violations':0,'resource_conflicts':0,'max_energy_error_kwh':energy_error,'total_flight_rounding_s':total_rounding}}
