"""Prepare and independently reconcile the saved Q1 results for plotting."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import math
from pathlib import Path
import json

MAIN_ORDERS = [('sorties','energy','time'), ('energy','sorties','time'), ('time','sorties','energy')]
TIME_KEYS = ['preparation_s','loading_s','flight_s','handover_s','box_handover_s']


def require(value, message):
    if not value:
        raise ValueError(message)


def close(a,b,message,tol=1e-9):
    require(math.isclose(a,b,rel_tol=0,abs_tol=tol),f'{message}: {a} != {b}')


def prepare(project):
    from uav_rescue.common.data import CATEGORIES, load_inputs, file_hash
    from uav_rescue.common.physics import equivalent_range, safe_payload
    from uav_rescue.common.terrain import Terrain
    from uav_rescue.q1.validate import validate_manifest
    result_path=project/'outputs/q1_sensitivity_05_45/tables/results.json'
    audit_path=project/'outputs/q1_objective_audit/results.json'
    result=json.loads(result_path.read_text(encoding='utf-8'))
    audit=json.loads(audit_path.read_text(encoding='utf-8'))
    require(audit['passed'], '目标对照未通过核验')
    cfg=result['metadata']['config']; p=cfg['physics']; reserve=result['baseline']['reserve']
    require(reserve==.2,'须使用20%基准')
    require(audit['metadata']['source_sha256']==file_hash(result_path),'目标补算与基准快照不一致')
    hashes={**result['metadata']['input_sha256'],str(result_path):file_hash(result_path),str(audit_path):file_hash(audit_path)}
    for path,h in hashes.items(): require(file_hash(Path(path))==h, '输入哈希改变: '+path)
    inputs=load_inputs(project/cfg['paths']['data_root'],project/cfg['paths']['template'])
    terrain=Terrain(inputs.dem_path,inputs.origin)
    routes={s:terrain.route(n,p['clearance_m'],p['service_height_m']) for s,n in inputs.services.items()}
    for saved in result['routes']:
        route=routes[saved['service']]
        for direction in ['outbound','inbound']:
            for key in ['distance','cruise_altitude','climb','descent']:
                close(saved[direction][key],getattr(getattr(route,direction),key),'地形航段',1e-6)
    raw_boxes={b.id:b for b in inputs.boxes}
    baseline=sorted(result['baseline']['rows'],key=lambda r:r['sortie'])
    verification=validate_manifest(inputs,routes,baseline,reserve,p['gravity_m_s2'],p['energy_tolerance_kwh'])
    require(len(baseline)==18,'基准架次变化')
    batches=[]; cells=[]
    for idx,r in enumerate(baseline):
        d=inputs.drones[r['drone']]; route=routes[r['service']]
        boxes=[raw_boxes[x] for x in r['box_ids']]
        mass=math.fsum(b.mass for b in boxes); volume=math.fsum(b.volume for b in boxes)
        n=len(boxes); horizontal=climb=0.
        for leg,q in [(route.outbound,mass),(route.inbound,0.)]:
            horizontal+=d.battery*leg.distance/equivalent_range(d,q)
            climb+=(d.empty_mass+q)*p['gravity_m_s2']*leg.climb/(3.6e6*d.climb_efficiency)
        close(horizontal+climb,r['energy_kwh'],'逐架次分项能耗')
        row={'row_index':idx,**{k:r[k] for k in ['sortie','service','drone','box_count','mass_kg','volume_m3','flight_s','operation_s','energy_kwh','return_soc']},
             'rated_payload_kg':d.payload,'rated_volume_m3':d.volume,'battery_kwh':d.battery,
             'mass_utilization':mass/d.payload,'volume_utilization':volume/d.volume,
             'reserve':reserve,'preparation_s':d.preparation,'loading_s':n*d.loading,
             'handover_s':d.handover,'box_handover_s':n*d.per_box_handover,
             'horizontal_kwh':horizontal,'climb_kwh':climb,'box_ids':';'.join(r['box_ids'])}
        close(math.fsum(row[k] for k in TIME_KEYS),row['operation_s'],'作业时间分项',1e-6)
        batches.append(row)
        for position,b in enumerate(boxes):
            cells.append({'row_index':idx,'sortie':r['sortie'],'service':r['service'],'drone':r['drone'],
                          'box_position':position,'box_id':b.id,'category':b.category,'mass_kg':b.mass,'volume_m3':b.volume})
    services=[]
    for sid in sorted(inputs.services):
        rr=[b for b in batches if b['service']==sid]
        services.append({'service':sid,'sorties':len(rr),'boxes':sum(b['box_count'] for b in rr),
                         **{k:math.fsum(b[k] for b in rr) for k in ['mass_kg','volume_m3','operation_s','energy_kwh',*TIME_KEYS,'horizontal_kwh','climb_kwh']},
                         **{g:sum(b['drone']==g for b in rr) for g in 'ABC'}})
    objectives=[]
    for scenario in audit['comparisons']:
        order=tuple(scenario['order']);s=scenario['summary']
        require(scenario['reserve']==.2 and scenario['feasible'],'目标对比情景不同')
        require(len(scenario['milp'])==15 and all(c['passed'] for c in scenario['milp']),'目标对照MILP未通过')
        validate_manifest(inputs,routes,scenario['rows'],reserve,p['gravity_m_s2'],p['energy_tolerance_kwh'])
        close(math.fsum(r['energy_kwh'] for r in scenario['rows']),s['energy_kwh'],'目标对照能耗')
        close(math.fsum(r['operation_s'] for r in scenario['rows']),s['operation_s'],'目标对照时间',1e-6)
        require(len(scenario['rows'])==s['sorties'],'目标对照架次')
        base=result['baseline']['summary']
        objectives.append({'order':'>'.join(order),'label':scenario['label'],
                           'main_plot':order in MAIN_ORDERS,'main_index':MAIN_ORDERS.index(order) if order in MAIN_ORDERS else None,
                           'sorties':s['sorties'],'energy_kwh':s['energy_kwh'],'operation_s':s['operation_s'],
                           'operation_h':s['operation_s']/3600,**s['drone_counts'],
                           'delta_sorties':s['sorties']-base['sorties'],
                           'delta_energy_kwh':s['energy_kwh']-base['energy_kwh'],
                           'delta_energy_pct':100*(s['energy_kwh']/base['energy_kwh']-1),
                           'delta_operation_min':(s['operation_s']-base['operation_s'])/60})
    capacities=[]; sensitivity=[]; matrix=[]
    scenarios=sorted(result['sensitivity'],key=lambda x:x['reserve'])
    require([round(x['reserve']*100) for x in scenarios]==list(range(5,46,5)),'需5%-45%九档')
    expected_failures={.4:['S004','S008'],.45:['S002','S003','S004','S008','S012']}
    for scenario in scenarios:
        rho=scenario['reserve']; feasible=scenario['feasible']; summary=scenario['summary']
        validate_manifest(inputs,routes,scenario['rows'],rho,p['gravity_m_s2'],p['energy_tolerance_kwh'],feasible)
        require(scenario['infeasible_services']==expected_failures.get(rho,[]),'不可行服务区变化')
        for sid in sorted(inputs.services):
            rr=[r for r in scenario['rows'] if r['service']==sid]
            delivered=Counter(x for r in rr for x in r['box_ids'])
            demanded=Counter(b.id for b in inputs.boxes if b.service==sid)
            complete=delivered==demanded
            require(complete==(sid not in scenario['infeasible_services']),'完整交付与不可行名单冲突')
            if not complete: require(not rr,'不可行服务区不应显示部分配送')
            matrix.append({'reserve':rho,'service':sid,'feasible':complete,'min_sorties':len(rr) if complete else None,
                           'delivered_boxes':sum(delivered.values()),'required_boxes':sum(demanded.values())})
        for cap in scenario['safe_payloads']:
            d=inputs.drones[cap['drone']]
            value,reason=safe_payload(d,routes[cap['service']],rho,p['gravity_m_s2'],p['payload_tolerance_kg'])
            require((value is None)==(cap['safe_payload_kg'] is None),'不可达状态不一致')
            if value is not None:close(value,cap['safe_payload_kg'],'安全载荷',1e-6)
            require(reason==cap['reason'],'安全载荷限制原因')
            capacities.append({**cap,'rated_payload_kg':d.payload,'reachable':value is not None})
        if feasible:
            require(sum(m['min_sorties'] for m in matrix if m['reserve']==rho)==summary['sorties'],'各区架次总和')
            close(math.fsum(r['energy_kwh'] for r in scenario['rows']),summary['energy_kwh'],'余量情景能耗')
            close(math.fsum(r['operation_s'] for r in scenario['rows']),summary['operation_s'],'余量情景时间',1e-6)
        else:
            require(all(summary[k] is None for k in ['sorties','energy_kwh','operation_s','drone_counts']),'全局不可行不得用部分方案总量')
        sensitivity.append({'reserve':rho,'feasible':feasible,'sorties':summary['sorties'],
                            'energy_kwh':summary['energy_kwh'],'operation_s':summary['operation_s'],
                            'operation_h':summary['operation_s']/3600 if feasible else None,
                            **(summary['drone_counts'] if feasible else {g:None for g in 'ABC'}),
                            'infeasible_services':';'.join(scenario['infeasible_services'])})
    bundle={'schema_version':1,'metadata':{'prepared_utc':datetime.now(timezone.utc).isoformat(),'input_sha256':hashes,
            'physics':p,'font':str((project/cfg['paths']['font']).resolve()),'reserve':reserve,
            'source_verification':verification,'safe_payload_recomputed':len(capacities),'optimizer_invoked':False,
            'objective_milp_evidence':str(audit_path),'energy_model':'文献依据下的简化水平项与爬升附加项；见公式来源与假设审计'},
            'categories':list(CATEGORIES),'raw_boxes':[asdict(b) for b in inputs.boxes],
            'drones':[asdict(d) for d in inputs.drones.values()],
            'tables':{'01_逐箱组批':cells,'02_批次约束与分项':batches,'03_服务区统计':services,
                      '04_全部目标顺序对照':objectives,'05_安全载荷':capacities,
                      '06_余量组批汇总':sensitivity,'07_服务区可行性':matrix}}
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle):
    """Cross-table reconciliation also runs in standalone redraw mode."""
    require(bundle['schema_version']==1,'绘图快照版本不支持')
    t=bundle['tables'];cells=t['01_逐箱组批'];batches=t['02_批次约束与分项'];services=t['03_服务区统计']
    require(len(cells)==80 and len({r['box_id'] for r in cells})==80,'须恰好80个独立货箱')
    require(len(batches)==18 and len(services)==15,'18架次/15服务区')
    raw={b['id']:b for b in bundle['raw_boxes']}; require(set(raw)=={r['box_id'] for r in cells},'货箱覆盖')
    for b in batches:
        rr=[c for c in cells if c['sortie']==b['sortie']]
        require(len(rr)==b['box_count'],'批次箱数')
        require([r['box_position'] for r in rr]==list(range(len(rr))),'货箱绘制位置须连续唯一')
        require(all(r['row_index']==b['row_index'] for r in rr),'货箱绘制行序')
        for c in rr:
            orig=raw[c['box_id']]
            require(c['service']==b['service']==orig['service'] and c['category']==orig['category'],'货箱属性')
            close(c['mass_kg'],orig['mass'],'原始箱质量');close(c['volume_m3'],orig['volume'],'原始箱体积')
        for field in ['mass_kg','volume_m3']:close(math.fsum(c[field] for c in rr),b[field],field)
        close(b['mass_kg']/b['rated_payload_kg'],b['mass_utilization'],'质量利用率')
        close(b['volume_m3']/b['rated_volume_m3'],b['volume_utilization'],'体积利用率')
        close(1-b['energy_kwh']/b['battery_kwh'],b['return_soc'],'SOC')
        require(b['mass_utilization']<=1+1e-9 and b['volume_utilization']<=1+1e-9 and b['return_soc']>=.2-1e-9,'批次约束')
        close(math.fsum(b[k] for k in TIME_KEYS),b['operation_s'],'批次时间',1e-6)
        close(b['horizontal_kwh']+b['climb_kwh'],b['energy_kwh'],'批次能耗')
    for s in services:
        rr=[b for b in batches if b['service']==s['service']]
        require(s['sorties']==len(rr),'服务架次')
        for field in ['mass_kg','volume_m3','operation_s','energy_kwh',*TIME_KEYS,'horizontal_kwh','climb_kwh']:
            close(math.fsum(b[field] for b in rr),s[field],field,1e-6 if field.endswith('_s') else 1e-9)
        for g in 'ABC':require(s[g]==sum(b['drone']==g for b in rr),'服务区机型统计')
    close(math.fsum(b['mass_kg'] for b in batches),758,'总质量')
    close(math.fsum(b['volume_m3'] for b in batches),2.011,'总体积')
    objectives=t['04_全部目标顺序对照'];main=[r for r in objectives if r['main_plot']]
    require(len(objectives)==6 and {r['main_index'] for r in main}=={0,1,2},'目标案例覆盖')
    base=next(r for r in main if r['main_index']==0)
    for key in ['energy_kwh','operation_s']:close(math.fsum(b[key] for b in batches),base[key],key,1e-6)
    require(len(t['05_安全载荷'])==405 and len(t['07_服务区可行性'])==135,'载荷/可行性单元格数')
    require(len({(r['reserve'],r['service'],r['drone']) for r in t['05_安全载荷']})==405,'安全载荷索引重复')
    require(len({(r['reserve'],r['service']) for r in t['07_服务区可行性']})==135,'可行性格索引重复')
    for s in t['06_余量组批汇总']:
        rr=[r for r in t['07_服务区可行性'] if r['reserve']==s['reserve']]
        require(len(rr)==15,'每档需15个服务区')
        if s['feasible']:
            require(all(r['feasible'] for r in rr),'局部与整体可行性不一致')
            require(sum(r['min_sorties'] for r in rr)==s['sorties']==sum(s[g] for g in 'ABC'),'余量架次守恒')
        else:
            require(all(s[k] is None for k in ['sorties','energy_kwh','operation_s','operation_h','A','B','C']),'不可行情景总体指标应为空')
            require(s['infeasible_services']==';'.join(r['service'] for r in rr if not r['feasible']),'不可行名单与矩阵')
    for g in 'ABC':
        for sid in [s['service'] for s in services]:
            rr=sorted([r for r in t['05_安全载荷'] if r['drone']==g and r['service']==sid],key=lambda r:r['reserve'])
            for a,b in zip(rr,rr[1:]):
                va,vb=a['safe_payload_kg'],b['safe_payload_kg']
                require(vb is None or (va is not None and vb<=va+1e-6),'安全载荷单调性')
    return {'passed':True,'boxes':80,'sorties':18,'services':15,'capacity_points':405,'feasibility_cells':135,
            'main_objective_cases':3,'all_objective_orders':6,'optimizer_invoked':False}
