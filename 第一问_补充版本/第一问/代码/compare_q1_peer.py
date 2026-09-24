"""Compare finalized Q1 outputs with the peer snapshot and the pre-alignment backup."""
from pathlib import Path
import hashlib
import json
import math
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
PEER=Path('D:/Desktop/zirankexve')
OUT=ROOT/'results/question1_peer_alignment'


def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def signature(batches):
    return sorted((b.get('service_id',b.get('service')),b.get('model',b.get('drone')),tuple(sorted(b['box_ids']))) for b in batches)


def compare(ours,peer):
    assert ours['feasible']==peer['feasible']
    result={'feasible':ours['feasible']}
    if ours['feasible']:
        assert signature(ours['batches'])==signature(peer['rows'])
        differences={a:ours[a]-peer['summary'][b] for a,b in [('flight_count','sorties'),('energy_kwh','energy_kwh'),('work_time_s','operation_s')]}
        assert differences['flight_count']==0
        assert abs(differences['energy_kwh'])<1e-10 and abs(differences['work_time_s'])<1e-6
        result.update(box_assignments_equal=True,differences=differences)
    return result


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    s=load(ROOT/'results/question1_batching/solution.json')
    m=load(ROOT/'results/question1_multiobjective/multiobjective.json')
    r=load(ROOT/'results/question1_reserve_sensitivity/sensitivity.json')
    peer_path=PEER/'建模/outputs/q1_sensitivity_05_45/tables/results.json'
    peer=load(peer_path)
    audit_path=PEER/'建模/outputs/q1_objective_audit/results.json'
    audit=load(audit_path)
    with zipfile.ZipFile(ROOT/'archives/question1_before_peer_alignment_20260924.zip') as z:
        old_s=json.loads(z.read('results/question1_batching/solution.json'))
        old_r=json.loads(z.read('results/question1_reserve_sensitivity/sensitivity.json'))
    original_hashes={Path(k).name:v for k,v in peer['metadata']['input_sha256'].items()}
    for key in ['nodes','drones','boxes','dem']:
        source=s['sources'][key]
        assert source['sha256']==original_hashes[Path(source['path']).name]
    baseline=compare(dict(feasible=True,batches=s['batches'],**s['totals']),peer['baseline'])
    orders=[]
    symbols={'sorties':'N','energy':'E','time':'T'}
    for item in audit['comparisons']:
        key='_'.join(symbols[k] for k in item['order'])
        plan=next(p for p in m['frontier'] if p['plan_id']==m['endpoints'][key])
        orders.append(dict(order=key,plan_id=plan['plan_id'],**compare(dict(feasible=True,**plan),item)))
    assert len(orders)==6
    scenarios=[]
    for item in peer['sensitivity']:
        rho=item['reserve']*100
        current=next(p for p in r['scenarios'] if abs(p['reserve_percent']-rho)<1e-10)
        scenarios.append(dict(reserve_percent=rho,**compare(current,item)))
    assert len(scenarios)==9
    geometry=[]
    for item in peer['routes']:
        current=s['routes'][item['service']]
        diffs={a:current[a]-b for a,b in [('distance_m',item['outbound']['distance']),('max_terrain_m',item['terrain_max']),('outbound_climb_m',item['outbound']['climb']),('return_climb_m',item['inbound']['climb'])]}
        assert max(abs(v) for v in diffs.values())<1e-10
        geometry.append(dict(service_id=item['service'],differences=diffs))
    payload=[]
    for item in peer['baseline']['safe_payloads']:
        current=next(x for x in s['service_summary'] if x['service_id']==item['service'])['safe_payloads'][item['drone']]['safe_payload_kg']
        delta=0 if current is None and item['safe_payload_kg'] is None else current-item['safe_payload_kg']
        assert abs(delta)<=1e-6
        payload.append(dict(service_id=item['service'],model=item['drone'],difference_kg=delta))
    assert signature(s['batches'])==signature(old_s['batches'])
    assert len(r['interval_plans'])==len(old_r['interval_plans'])==19
    intervals=[]
    for a,b in zip(old_r['interval_plans'],r['interval_plans']):
        assert signature(a['batches'])==signature(b['batches'])
        intervals.append(dict(plan_id=b['plan_id'],same_box_assignments=True,old_lower_percent=a['lower_percent'],old_upper_percent=a['upper_percent'],new_lower_percent=b['lower_percent'],new_upper_percent=b['upper_percent'],upper_shift_percentage_points=b['upper_percent']-a['upper_percent'],old_energy_kwh=a['energy_kwh'],new_energy_kwh=b['energy_kwh']))
    result=dict(passed=True,scope='Q1 only; source comparison plus previously executed peer API replay and new independent Q1 verification.',
        peer_commit=subprocess.check_output(['git','-C',str(PEER),'rev-parse','HEAD'],text=True).strip(),
        sources={str(p):sha(p) for p in [peer_path,audit_path,ROOT/'results/question1_batching/solution.json',ROOT/'results/question1_multiobjective/multiobjective.json',ROOT/'results/question1_reserve_sensitivity/sensitivity.json']},
        original_numeric_inputs_match=True,baseline=baseline,all_six_orders=orders,common_nine_scenarios=scenarios,geometry=geometry,payload=payload,
        baseline_box_assignments_unchanged=True,all_19_interval_assignments_unchanged=True,interval_updates=intervals,
        old_baseline=old_s['totals'],new_baseline=s['totals'],energy_change_kwh=s['totals']['energy_kwh']-old_s['totals']['energy_kwh'],
        old_global_limit_percent=old_r['global_feasibility_limit']['reserve_percent'],new_global_limit_percent=r['global_feasibility_limit']['reserve_percent'],
        qualifications=['Interval endpoints move with gravity; identical interval-indexed assignments do not imply identical decisions at every fixed rho in the narrow shifted boundary windows.',
                        'Peer six-order MILP evidence is checked as a saved snapshot; its 90 MILPs were not rerun here. Peer pure DP APIs were separately replayed.'])
    OUT.mkdir(exist_ok=True)
    (OUT/'final_alignment_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['passed','peer_commit','baseline_box_assignments_unchanged','all_19_interval_assignments_unchanged','energy_change_kwh','new_global_limit_percent']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
