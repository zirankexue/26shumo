"""Read-only adapter to the senior Q2 implementation and its latest result.

Only this project's original inputs and new output directories are used.
No bytecode, configuration, results or caches are written to the senior tree.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import tomllib
from q3_geometry import Geometry

ROOT = Path(__file__).resolve().parents[1]
SENIOR_ROOT = Path(os.environ.get('Q3_SENIOR_ROOT', 'D:/Desktop/zirankexve'))
SENIOR_PROJECT = SENIOR_ROOT/'建模'
LATEST_RELATIVE = Path('outputs/q2_improved/tables/final_result.json')
GRAVITY_M_S2 = 9.80665


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def remap_archived_path(old_path, root=ROOT, senior_root=SENIOR_ROOT):
    """Map archived D:/xyq/... paths without reading that former directory."""
    parts=[p for p in str(old_path).replace('\\','/').split('/') if p]
    if '数据' in parts:return Path(root).joinpath(*parts[parts.index('数据'):])
    if parts and parts[-1]=='结果提交模板.xlsx':return Path(root)/parts[-1]
    if '建模' in parts:return Path(senior_root).joinpath(*parts[parts.index('建模'):])
    raise ValueError('No authorized archive path mapping: '+str(old_path))


def load_senior(senior_root=SENIOR_ROOT, root=ROOT, latest_path=None):
    """Return (SchedulingInputs, RouteFactory, remapped_config, latest_payload).

    Imports only common and q2 data/routes modules from the requested source;
    all five original attachment hashes must match the latest-result manifest.
    """
    senior_root,root=Path(senior_root),Path(root)
    project=senior_root/'建模';source=project/'src'
    if str(source) not in sys.path:sys.path.insert(0,str(source))
    sys.dont_write_bytecode=True
    from uav_rescue.q2.data import load_scheduling_inputs
    from uav_rescue.q2.routes import RouteFactory
    for name in ('uav_rescue.q2.data','uav_rescue.q2.routes'):
        resolved=Path(sys.modules[name].__file__).resolve()
        if not resolved.is_relative_to(source.resolve()):
            raise RuntimeError('A different senior package is already loaded: '+str(resolved))
    final_path=Path(latest_path) if latest_path else project/LATEST_RELATIVE
    payload=json.loads(final_path.read_text(encoding='utf-8'))
    config=tomllib.loads((project/'configs/q2.toml').read_text(encoding='utf-8'))
    if config['physics']!=payload['physics']:
        raise ValueError('Senior current configuration differs from latest Q2 physics')
    input_checks=[]
    for old,expected in payload['input_sha256'].items():
        local=remap_archived_path(old,root,senior_root);actual=sha256(local)
        input_checks.append(dict(archived_path=old,local_path=str(local),expected_sha256=expected,
                                 actual_sha256=actual,matched=actual==expected))
        if actual!=expected:raise ValueError('Original input hash mismatch: '+str(local))
    config=deepcopy(config)
    config['paths'].update(data_root=str(root/'数据'),template=str(root/'结果提交模板.xlsx'),
                           output=str(root/'results/question3_from_senior'),cache=str(root/'results/question3_from_senior/cache'))
    config['_adapter']=dict(senior_root=str(senior_root),senior_source=str(source),latest_path=str(final_path),
                             latest_sha256=sha256(final_path),input_checks=input_checks,
                             source_write_policy='read_only; sys.dont_write_bytecode=True')
    data=load_scheduling_inputs(root/'数据',root/'结果提交模板.xlsx')
    factory=RouteFactory(data,config['physics'])
    return data,factory,config,payload


class SeniorGeometry(Geometry):
    """Same DEM/radio geometry, with the senior gravity constant for relay energy."""
    def __init__(self,root=ROOT,gravity_m_s2=GRAVITY_M_S2):
        super().__init__(root)
        self.gravity_m_s2=float(gravity_m_s2)

    def relay_travel(self,point):
        point=tuple(point);ground=self.terrain(*point[:2]);r=self.relay
        if not ground<=point[2]<=ground+r['max_agl_m']+1e-7:raise ValueError('Relay AGL outside limits')
        leg=self.leg(self.xyz('O01'),point)
        outward=leg['up_m']/r['climb_mps']+leg['distance_m']/r['cruise_mps']+leg['down_m']/r['descend_mps']
        inward=leg['down_m']/r['climb_mps']+leg['distance_m']/r['cruise_mps']+leg['up_m']/r['descend_mps']
        energy=2*r['cruise_kw']*leg['distance_m']/r['cruise_mps']/3600
        energy+=r['mass_kg']*self.gravity_m_s2*(leg['up_m']+leg['down_m'])/(r['climb_efficiency']*3.6e6)
        service=(r['energy_kwh']*(1-r['reserve_percent']/100)-energy)*3600/(r['hover_kw']+r['communication_kw'])-r['setup_s']
        return dict(ground_m=ground,agl_m=point[2]-ground,outbound_s=outward,return_s=inward,
                    earliest_service_s=r['prepare_s']+outward+r['setup_s'],travel_energy_kwh=energy,
                    max_service_s=service,cruise_altitude_m=leg['cruise_altitude_m'],distance_m=leg['distance_m'])


def independent_audit(data,factory,config,payload):
    """Recompute final 21 sorties via local geometry, not factory.make/physics."""
    geo=SeniorGeometry();failures=[];checks=0;max_error=defaultdict(float)
    def check(ok,message):
        nonlocal checks
        checks+=1
        if not ok:failures.append(message)
    def equal(actual,expected,kind,where,tolerance=1e-7):
        difference=abs(actual-expected);max_error[kind]=max(max_error[kind],difference)
        check(difference<=tolerance,f'{where}: {kind} expected {expected}, actual {actual}')
    def tick(value):return math.ceil(value*1000-1e-9)
    def charge(energy,capacity,full):
        fraction=energy/capacity
        return full*(min(fraction,.1)*3.5+max(fraction-.1,0)*(.65/.9))
    geometry=[]
    for (start,end),original in factory.legs.items():
        local=geo.leg(start,end)
        for source,target in [('distance','distance_m'),('cruise_altitude','cruise_altitude_m'),('climb','up_m'),('descent','down_m')]:
            equal(getattr(original,source),local[target],'geometry_'+source,start+'->'+end)
        cells_old=factory.terrain.cells(factory.nodes[start],factory.nodes[end])
        cells_new=geo._cells(geo.xyz(start),geo.xyz(end))
        check(cells_old==cells_new,start+'->'+end+': touched DEM cells differ')
        geometry.append(dict(start=start,end=end,cell_count=len(cells_new),**local))
    boxes={b.id:b for b in data.base.boxes};main=payload['main'];delivery_rows={r['box']:r for r in main['deliveries']}
    assignments={r['sortie']:r for r in payload['schedule']}
    candidates={r['id']:r for r in payload['candidates']}
    legs_by_sortie=defaultdict(list)
    for row in main['legs']:legs_by_sortie[row['sortie']].append(row)
    check(len(main['sorties'])==21,'Latest result does not contain expected 21 sorties')
    check(Counter(b for s in main['sorties'] for b in s['boxes'])==Counter(boxes.keys()),'80 boxes not covered exactly once')
    check(len(main['deliveries'])==len(delivery_rows)==80,'Delivery records duplicate or missing')
    energy_sum=0.;operation_sum=0.;last_return=0.;flight_padding=0.;deliveries=[];sorties=[];hard_slacks=[];soc_min=1.
    air=defaultdict(list);batteries=defaultdict(list)
    gravity=payload['physics']['gravity_m_s2']
    for sortie in main['sorties']:
        name=sortie['sortie'];d=data.base.drones[sortie['drone']];assignment=assignments[name]
        cargo=[boxes[b] for b in sortie['boxes']];mass=math.fsum(b.mass for b in cargo);volume=math.fsum(b.volume for b in cargo)
        check(mass<=d.payload+1e-9 and volume<=d.volume+1e-10,name+': load invalid')
        equal(sortie['mass_kg'],mass,'mass_kg',name);equal(sortie['volume_m3'],volume,'volume_m3',name)
        check(sortie['unit'] in data.units[d.id],name+': unit/model mismatch')
        check(sortie['battery'] in {f'BAT-{d.id}-{i+1:02}' for i in range(data.battery_count[d.id])},name+': battery invalid')
        offset=tick(d.preparation+len(cargo)*d.loading);start=tick(sortie['start_s'])
        equal(sortie['takeoff_s'],(start+offset)/1000,'takeoff_s',name)
        remaining=mass;energy=0.;flight=0.;last='O01';stop_records=[]
        for leg_index,nxt in enumerate([*sortie['visits'],'O01']):
            leg=geo.leg(last,nxt);leg_start=offset
            seconds=leg['up_m']/d.climb_speed+leg['distance_m']/d.cruise_speed+leg['down_m']/d.descent_speed
            equivalent=d.empty_range-(d.empty_range-d.full_range)*(remaining/d.payload)**1.5
            use=d.battery*leg['distance_m']/equivalent+(d.empty_mass+remaining)*gravity*leg['up_m']/(3.6e6*d.climb_efficiency)
            energy+=use;flight+=seconds;offset+=tick(seconds);flight_padding+=tick(seconds)/1000-seconds
            exported=legs_by_sortie[name][leg_index]
            check(exported['from']==last and exported['to']==nxt,name+': exported leg ordering mismatch')
            for field,expected in [('payload_kg',remaining),('distance_m',leg['distance_m']),('cruise_altitude_m',leg['cruise_altitude_m']),
                                   ('start_s',(start+leg_start)/1000),('end_s',(start+offset)/1000),('flight_s',seconds),('energy_kwh',use)]:
                equal(exported[field],expected,'leg_'+field,name)
            if nxt!='O01':
                unloaded=[b for b in cargo if b.service==nxt]
                ranks=[delivery_rows[b.id]['rank'] for b in unloaded]
                check(sorted(ranks)==list(range(1,len(unloaded)+1)),name+': invalid per-box ranks at '+nxt)
                arrival=offset;base_end=offset+tick(d.handover)
                for box in unloaded:
                    row=delivery_rows[box.id];rank=row['rank'];completion=(start+base_end+rank*tick(d.per_box_handover))/1000
                    equal(row['completion_s'],completion,'delivery_s',box.id)
                    equal(row['start_s'],completion-d.per_box_handover,'delivery_start_s',box.id)
                    equal(assignment['deliveries'][box.id]/1000,completion,'schedule_delivery_s',box.id)
                    check(row['sortie']==name and row['service']==nxt,box.id+': delivery references mismatch')
                    timing=data.timing[box.id]
                    if timing.hard is not None:
                        hard_slacks.append(timing.hard-completion);check(completion<=timing.hard+1e-7,box.id+': hard deadline violation')
                    deliveries.append(dict(box=box.id,sortie=name,completion_s=completion,rank=rank,
                                           expected_s=timing.expected,priority=timing.priority,hard_s=timing.hard))
                stop_records.append(dict(service=nxt,arrival_offset_ms=arrival,base_handover_end_ms=base_end))
                offset=base_end+len(unloaded)*tick(d.per_box_handover)
                remaining-=math.fsum(b.mass for b in unloaded)
            last=nxt
        recharge=charge(energy,d.battery,data.full_charge_s[d.id]);soc=1-energy/d.battery
        soc_min=min(soc_min,soc);check(soc>=payload['physics']['reserve']-1e-9,name+': low SOC')
        equal(sortie['energy_kwh'],energy,'sortie_energy_kwh',name);equal(sortie['soc'],soc,'sortie_soc',name)
        equal(sortie['operation_s'],offset/1000,'operation_s',name);equal(sortie['flight_s'],flight,'flight_s',name)
        equal(sortie['return_s'],(start+offset)/1000,'return_s',name)
        equal(sortie['charge_s'],recharge,'charge_s',name);equal(sortie['charge_end_s'],(start+offset+tick(recharge))/1000,'charge_end_s',name)
        equal(assignment['return'],start+offset,'schedule_return_ms',name)
        saved=candidates[sortie['candidate']]
        equal(saved['duration'],offset,'candidate_duration_ms',name);equal(saved['energy'],energy,'candidate_energy_kwh',name)
        exact=d.preparation+len(cargo)*d.loading+flight+len(sortie['visits'])*d.handover+len(cargo)*d.per_box_handover
        equal(sortie['rounding_s'],offset/1000-exact,'rounding_s',name)
        air[sortie['unit']].append((start,start+offset,name))
        batteries[sortie['battery']].append((start,start+offset+tick(recharge),name))
        energy_sum+=energy;operation_sum+=offset/1000;last_return=max(last_return,(start+offset)/1000)
        sorties.append(dict(sortie=name,energy_kwh=energy,return_s=(start+offset)/1000,soc=soc,stops=stop_records))
    for kind,groups in [('unit',air),('battery',batteries)]:
        for key,intervals in groups.items():
            intervals.sort()
            for a,b in zip(intervals,intervals[1:]):check(a[1]<=b[0],f'{kind} {key} resource conflict {a[2]}/{b[2]}')
    tardiness=sum(max(0,r['completion_s']-r['expected_s'])*r['priority'] for r in deliveries)
    summary=dict(sorties=len(sorties),energy_kwh=energy_sum,makespan_s=last_return,operation_s=operation_sum,
                 min_soc=soc_min,weighted_tardiness_s=tardiness,late_boxes=sum(r['completion_s']>r['expected_s']+1e-7 for r in deliveries),
                 on_time_boxes=sum(r['completion_s']<=r['expected_s']+1e-7 for r in deliveries),hard_boxes=len(hard_slacks),
                 multi_stop_sorties=sum(len(s['visits'])>1 for s in main['sorties']),units_used=len(air),batteries_used=len(batteries))
    for key,value in summary.items():equal(main['summary'][key],value,'summary_'+key,'summary')
    source_checks=[]
    for mapping_name in ('source_sha256','code_sha256'):
        for old,expected in payload.get(mapping_name,{}).items():
            local=remap_archived_path(old);actual=sha256(local);matched=actual==expected
            source_checks.append(dict(kind=mapping_name,archived_path=old,actual_path=str(local),expected=expected,actual=actual,matched=matched))
            check(matched,'Source artifact hash mismatch '+str(local))
    audited_sources={str(p):sha256(p) for p in [SENIOR_PROJECT/'src/uav_rescue'/r for r in
                     ['common/data.py','common/terrain.py','common/physics.py','common/charging.py','q2/data.py','q2/routes.py']]}
    return dict(passed=not failures,check_count=checks,errors=failures,input_manifest=config['_adapter'],source_manifest_checks=source_checks,
                independently_recomputed_summary=summary,minimum_hard_deadline_slack_s=min(hard_slacks),
                total_leg_rounding_padding_s=flight_padding,maximum_absolute_errors=dict(max_error),geometry_directed_leg_count=len(geometry),
                common_physics_alignment=dict(gravity_m_s2=gravity,coordinate_model='WGS84 curvature fixed at O01',
                    terrain_rule='PixelIsPoint +0.5; all closed intersecting cells including both boundary sides',
                    transport_clearance_m=50,service_height_m=30,time_quantization='ceil each complete flight leg to 1 ms; physical stages retain real durations',
                    delivery_rule='Base handover followed by actual rank-ordered per-box handover; each box completes separately',
                    energy_rule='Euse*d/L(q) + (empty+remaining payload)*g*climb/(3.6e6*eta)',
                    energy_assumption='Same senior simplified model; not claimed to be a calibrated full rotorcraft model',
                    inherited_differences_from_previous_independent_q3=['g=9.80665 instead of 9.81','Millisecond leg ceilings instead of one-second resource ceilings','Per-box completion instead of common visit-end completion'],
                    relay_rule='SeniorGeometry uses g=9.80665; retains explicit max(peak+50,endpoint altitudes) convention for high relay endpoints'),
                read_only_source_hashes=audited_sources,geometry=geometry,recomputed_sorties=sorties,recomputed_deliveries=deliveries)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'results/question3_from_senior/physics_audit.json')
    args=parser.parse_args();data,factory,config,payload=load_senior()
    report=independent_audit(data,factory,config,payload)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['passed','check_count','errors','independently_recomputed_summary','minimum_hard_deadline_slack_s','maximum_absolute_errors']},ensure_ascii=False))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
