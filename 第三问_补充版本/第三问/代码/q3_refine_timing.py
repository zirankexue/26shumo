"""Safe label-symmetry refinement of a fully scheduled Q3 plan.

Equal physical boxes with equal soft due dates and priorities can exchange
delivery slots. Earliest hard deadlines get earliest slots. Trajectories,
resources, service windows and the original weighted objective are invariant.
This is a postprocessor; it does not change the former recommended result.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from q3_senior_adapter import load_senior

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hard_slacks(payload,data):
    return sorted((dict(box=d['box'],sortie=d['sortie'],time_s=d['time_s'],deadline_s=data.timing[d['box']].hard,
                        slack_s=data.timing[d['box']].hard-d['time_s'])
                   for d in payload['deliveries'] if data.timing[d['box']].hard is not None),
                  key=lambda x:(x['slack_s'],x['box']))


def refresh_metrics(payload,data):
    from uav_rescue.q2.weighted import score,integer_score
    m=payload['metrics'];deliveries=payload['deliveries']
    m['last_delivery_s']=max(d['time_s'] for d in deliveries)
    m['late_boxes']=sum(d['time_s']>d['due_s']+1e-8 for d in deliveries)
    m['weighted_delay_s']=math.fsum(d['priority']*max(0,d['time_s']-d['due_s']) for d in deliveries)
    m['weighted_mean_delivery_s']=math.fsum(d['priority']*d['time_s'] for d in deliveries)/sum(t.priority for t in data.timing.values())
    vector=payload['objective_vector']
    vector['tardiness']=sum(data.timing[d['box']].priority*max(0,round(d['time_s']*1000)-round(data.timing[d['box']].expected*1000)) for d in deliveries)
    m['score']=score(vector,payload['weighted_spec'])
    payload['search']['integer_objective']=integer_score(vector,payload['weighted_spec'])


def bottlenecks(payload,data):
    grouped=[]
    for r in payload['relay']:
        uses=[]
        for p in payload['transport']:
            intervals=[x for x in p['communication'] if x['relay'] and x.get('station',p['station'])==r['station']]
            if not intervals:continue
            first=min(x['start'] for x in intervals);last=max(x['end'] for x in intervals)
            uses.append(dict(sortie=p['id'],services=p['order'],absolute_first_s=p['start_s']+first,
                             absolute_last_s=p['start_s']+last,return_s=p['return_s']))
        uses.sort(key=lambda x:x['absolute_last_s'])
        grouped.append(dict(relay=r['id'],station_id=r['station_data']['id'],drone=r['drone'],
            service_start_s=r['service_start_s'],service_end_s=r['service_end_s'],return_s=r['return_s'],
            terminal_transport=uses[-1] if uses else None,uses=uses,
            end_slack_s=r['service_end_s']-max((x['absolute_last_s'] for x in uses),default=r['service_end_s'])))
    latest_transport=max(payload['transport'],key=lambda p:p['return_s'])
    return dict(relay_groups=grouped,tight_hard_deadlines=hard_slacks(payload,data)[:8],
                latest_transport=dict(id=latest_transport['id'],order=latest_transport['order'],return_s=latest_transport['return_s'],
                                      drone=latest_transport['drone'],battery=latest_transport['battery']),
                latest_relay=max(grouped,key=lambda r:r['return_s']))


def refine_labels(payload,data,factory):
    """Return (new_payload, audit), with no mutation of the input.

    Exact-equivalence key: service, category, mass, volume, expected deadline,
    priority. Only hard/first-batch labels may differ. EDD labels on sorted
    available delivery slots preserve feasibility: an inversion where a later
    deadline uses an earlier slot can be exchanged without violating either.
    """
    result=deepcopy(payload);boxes={b.id:b for b in data.base.boxes}
    delivery_by_id={d['box']:d for d in payload['deliveries']}
    groups=defaultdict(list)
    for b in boxes.values():
        timing=data.timing[b.id]
        groups[(b.service,b.category,b.mass,b.volume,timing.expected,timing.priority)].append(b.id)
    mapping={b:b for b in boxes};changes=[];before=hard_slacks(payload,data)
    for key,ids in sorted(groups.items(),key=lambda item:repr(item[0])):
        slots=sorted(ids,key=lambda b:(delivery_by_id[b]['time_s'],delivery_by_id[b]['sortie'],b))
        old_position={b:i for i,b in enumerate(slots)}
        # Stable within equal hard deadlines: no arbitrary relabelling.
        labels=sorted(ids,key=lambda b:(data.timing[b].hard if data.timing[b].hard is not None else math.inf,old_position[b]))
        for original,label in zip(slots,labels):
            mapping[original]=label
            if original!=label:
                changes.append(dict(old_label_at_slot=original,new_label_at_slot=label,
                    sortie=delivery_by_id[original]['sortie'],slot_s=delivery_by_id[original]['time_s'],
                    old_hard=data.timing[original].hard,new_hard=data.timing[label].hard,
                    equal_key=dict(service=key[0],category=key[1],mass_kg=key[2],volume_m3=key[3],expected_s=key[4],priority=key[5])))
    assert set(mapping)==set(mapping.values())==set(boxes)
    for p in result['transport']:
        original=next(t for t in payload['transport'] if t['id']==p['id'])
        p['boxes']=sorted(mapping[b] for b in p['boxes'])
        p['delivery']={mapping[b]:offset for b,offset in p['delivery'].items()}
        p['delivery_order']={service:[mapping[b] for b in order] for service,order in p['delivery_order'].items()}
        rebuilt=factory.make(p['model'],p['boxes'],p['order'])
        assert rebuilt is not None
        for actual,expected in [(rebuilt.energy,p['energy_kwh']),(rebuilt.mass,p['mass_kg']),(rebuilt.volume,p['volume_m3']),
                                 (rebuilt.duration/1000,p['duration_s']),(rebuilt.charge/1000,p['charge_s'])]:
            assert abs(actual-expected)<1e-10,(p['id'],actual,expected)
        p['candidate_id']=rebuilt.id
        assert p['communication']==original['communication'] and p['stages']==original['stages']
    for d in result['deliveries']:
        new=mapping[d['box']];b=boxes[new];t=data.timing[new]
        d.update(box=new,service=b.service,due_s=t.expected,first_deadline_s=t.first_deadline,
                 type=new.split('-')[1],priority=t.priority)
    result['deliveries'].sort(key=lambda d:d['box'])
    result['parent_search']=deepcopy(result['search'])
    result['search']=dict(result['search'],label='exact_box_label_symmetry',status='FEASIBLE',
                         method='Earliest hard-deadline labels assigned to earliest strictly equivalent delivery slots',
                         optimality_scope='No global optimality claim; this postprocessing preserves the former objective.')
    result.pop('recommendation',None)
    refresh_metrics(result,data)
    after=hard_slacks(result,data)
    assert after[0]['slack_s']>=-1e-8
    assert result['objective_vector']==payload['objective_vector']
    for key in payload['metrics']:
        assert abs(result['metrics'][key]-payload['metrics'][key])<1e-8,(key,result['metrics'][key],payload['metrics'][key])
    audit=dict(passed=True,changed_delivery_labels=len(changes),changes=changes,
               before_min_hard_slack_s=before[0]['slack_s'],after_min_hard_slack_s=after[0]['slack_s'],
               unchanged_objective_vector=result['objective_vector'],unchanged_score=result['metrics']['score'],
               before_tight_hard_deadlines=before[:8],after_tight_hard_deadlines=after[:8],
               minimum_all_expected_slack_s=min(d['due_s']-d['time_s'] for d in result['deliveries']),
               invariants=['Same service/category/mass/volume/soft deadline/priority only.',
                           'Same exact physical trajectories, loads, time slots, resource allocation and communication certificates.',
                           'Same original weighted objective; no replacement of the original objective by a robustness score.',
                           'Improved hard-label slack is not an improvement of every soft expected-deadline slack.'],
               bottlenecks_before=bottlenecks(payload,data),bottlenecks_after=bottlenecks(result,data))
    result['label_symmetry_refinement']=dict(changed_labels=len(changes),minimum_hard_slack_before_s=before[0]['slack_s'],
                                            minimum_hard_slack_after_s=after[0]['slack_s'],invariants=audit['invariants'])
    return result,audit


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--solution',type=Path,default=ROOT/'results/question3_from_senior/solution_recommended.json')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'results/question3_agent_update')
    parser.add_argument('--name',default='label_symmetry')
    args=parser.parse_args();data,factory,config,source=load_senior()
    payload=json.loads(args.solution.read_text(encoding='utf-8'))
    refined,audit=refine_labels(payload,data,factory)
    provenance=dict(path=str(args.solution),sha256=digest(args.solution),postprocessor_sha256=digest(__file__))
    refined['postprocess_source']=provenance;audit['source']=provenance
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/f'solution_{args.name}.json').write_text(json.dumps(refined,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output_dir/f'audit_{args.name}.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:audit[k] for k in ['passed','changed_delivery_labels','before_min_hard_slack_s','after_min_hard_slack_s',
                                         'minimum_all_expected_slack_s','unchanged_score']},ensure_ascii=False))


if __name__=='__main__':main()
