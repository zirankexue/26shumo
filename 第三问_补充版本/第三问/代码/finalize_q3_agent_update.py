"""Normalize station identities, then refine and freeze the agent-inspired Q3 plan."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from improve_q3_from_agents import OUT, read, digest, inherit
from solve_q3_senior import dump, solve
from q3_senior_adapter import load_senior, SeniorGeometry
from q3_multi_profiles import MultiProfiles
from q3_refine_timing import refine_labels
from validate_q3_senior import validate


def normalize():
    # Retain the exact first-run search artifacts and their original indices.
    archive=OUT/'before_station_normalization'
    archive.mkdir(exist_ok=True)
    for name in ('station_pool.json','solution_recommended.json','validation_recommended.json',
                 'audit_final_label_symmetry.json','search_history.json','search_candidate_summary.json'):
        target=archive/name
        if not target.exists():target.write_bytes((OUT/name).read_bytes())
    original=read(archive/'station_pool.json')
    seed=read(archive/'solution_recommended.json')
    unique=[];lookup={};mapping={};groups={}
    for i,s in enumerate(original):
        point=tuple(round(s[k],6) for k in ('x','y','z'))
        if point not in lookup:
            lookup[point]=len(unique);unique.append(copy.deepcopy(s))
        j=lookup[point];mapping[i]=j;groups.setdefault(j,[]).append(i)
        assert max(abs(s[k]-unique[j][k]) for k in ('x','y','z'))<1e-9
    active=[mapping[r['station']] for r in seed['relay']]
    assert len(active)==len(set(active)), 'A duplicate position was used by multiple relay missions.'
    before=copy.deepcopy(seed['metrics']);pair_ids=set()
    for r in seed['relay']:
        r['station']=mapping[r['station']]
        r['station_data']=copy.deepcopy(unique[r['station']])
    for r in seed['transport']:
        if r['station']>=0:r['station']=mapping[r['station']]
        for row in r['communication']:
            if row.get('station',-1)>=0:row['station']=mapping[row['station']]
        kind,values=r['communication_option_key'].split(':')
        ids=[int(v) for v in values.split(',')]
        ids=[mapping[v] if v>=0 else v for v in ids]
        r['communication_option_key']=kind+':'+','.join(map(str,ids))
        if kind=='pair':pair_ids.update(ids)
    summary=read(archive/'search_candidate_summary.json')
    pair_ids.update(mapping[i] for i in summary['pair_station_indices'])
    audit=dict(original_station_indices=len(original),unique_positions=len(unique),
               old_to_new=mapping,duplicate_groups=[dict(new_index=j,old_indices=ids,
               old_ids=[original[i]['id'] for i in ids]) for j,ids in groups.items() if len(ids)>1],
               selected_relay_positions_distinct=True,metrics_unchanged=seed['metrics']==before,
               original_solution_sha256=digest(archive/'solution_recommended.json'),
               pair_station_indices=sorted(pair_ids))
    seed['station_normalization']=audit
    dump(OUT/'station_pool.json',unique)
    dump(OUT/'station_normalization_audit.json',audit)
    dump(OUT/'solution_deduplicated_seed.json',seed)
    result=validate(OUT/'solution_deduplicated_seed.json')
    assert result['passed'],result['errors'][:10]
    dump(OUT/'validation_deduplicated_seed.json',result)
    print('NORMALIZED',len(original),'indices to',len(unique),'positions; all metrics invariant',flush=True)
    return seed,unique,sorted(pair_ids)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=65)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    seed,stations,pair_ids=normalize()
    data,fac,_,_=load_senior();geo=SeniorGeometry()
    pool={}
    for r in seed['transport']:
        p=fac.make(r['model'],r['boxes'],r['order'])
        assert p is not None
        pool[p.id]=p
    profiles=MultiProfiles(data,fac,geo,stations,pair_stations=pair_ids)
    found=inherit(solve(pool,data,profiles,seed['weighted_spec'],args.seconds,61,
                  hint=seed,workers=args.workers,label='normalized_fixed_refinement',zero_lateness=True),
                  seed,'normalized_fixed_refinement')
    dump(OUT/'solution_normalized_fixed_refinement.json',found)
    best=seed
    if found.get('feasible'):
        result=validate(OUT/'solution_normalized_fixed_refinement.json')
        assert result['passed'],result['errors'][:10]
        dump(OUT/'validation_normalized_fixed_refinement.json',result)
        if found['metrics']['score']<=seed['metrics']['score']+1e-12:best=found
    final,label_audit=refine_labels(best,data,fac)
    history=read(OUT/'before_station_normalization/search_history.json')+[found['search']]
    final['search_history']=history
    final['station_normalization']=seed['station_normalization']
    final['candidate_scope']=dict(unique_positions=len(stations),max_relay_stations_per_sortie=2,
                                 pair_station_indices=pair_ids,transport_candidates_in_final_solve=len(pool))
    final['recommendation']=dict(method='Minimum frozen score among validated saved experiments; exact-equivalent box-label postprocessing.',
                                 baseline_sha256=digest(OUT.parent/'question3_from_senior/solution_recommended.json'))
    dump(OUT/'solution_recommended.json',final)
    dump(OUT/'audit_final_label_symmetry.json',label_audit)
    dump(OUT/'search_history.json',history)
    result=validate(OUT/'solution_recommended.json')
    assert result['passed'],result['errors'][:10]
    dump(OUT/'validation_recommended.json',result)
    print('FINAL',json.dumps(final['metrics']), 'checks',result['check_count'],flush=True)


if __name__=='__main__':main()
