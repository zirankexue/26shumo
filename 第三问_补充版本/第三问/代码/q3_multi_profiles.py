"""Continuously certified one- or two-station communication route options.

Extends the senior-compatible Profiles class without changing physical routes.
Each certificate interval names exactly one station or the direct gateway.
Separate station service envelopes must be constrained by the joint solver.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
import argparse
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import time
from solve_q3_senior import Profiles, stages_for
from q3_senior_adapter import load_senior, SeniorGeometry

ROOT=Path(__file__).resolve().parents[1]


class MultiProfiles(Profiles):
    max_relay_stations_per_sortie=2

    def __init__(self,data,factory,geo,stations,pair_stations=(),reverse_priority=True):
        super().__init__(data,factory,geo,stations)
        self.pair_stations=tuple(dict.fromkeys(int(k) for k in pair_stations))
        if any(k<0 or k>=len(stations) for k in self.pair_stations):
            raise ValueError('pair_stations must contain valid global station indices')
        self.pairs=list(itertools.combinations(self.pair_stations,2))
        if reverse_priority:self.pairs += [(b,a) for a,b in self.pairs]
        self.multi_cache={}
        self.pair_geometry_cache={}
        self.counters=dict(pair_profile_attempts=0,pair_profile_proved=0,pair_profile_rejected=0,
                           direct_pair_duplicates_skipped=0,pair_segment_cache_hits=0,pair_segment_evaluations=0)

    def _segment(self,a,b,pair):
        key=(tuple(a),tuple(b),tuple(pair))
        if key in self.pair_geometry_cache:
            self.counters['pair_segment_cache_hits']+=1
            return self.pair_geometry_cache[key]
        points=[(self.stations[k]['x'],self.stations[k]['y'],self.stations[k]['z']) for k in pair]
        self.counters['pair_segment_evaluations']+=1
        if not all(self.geo.link(point,self.geo.gateway,'backhaul')['available'] for point in points):
            self.pair_geometry_cache[key]=None
            return None
        # Geometry.segment_certificate proves a complete continuum. A failed
        # proof is rejected; point tests never act as a coverage certificate.
        cert=self.geo.segment_certificate(a,b,points)
        self.pair_geometry_cache[key]=cert if cert['proved'] else None
        return self.pair_geometry_cache[key]

    def _pair_profile(self,route,pair):
        self.counters['pair_profile_attempts']+=1
        rows=[];requirements={}
        for stage in route['stages']:
            cert=self._segment(stage['a'],stage['b'],pair)
            if cert is None:
                self.counters['pair_profile_rejected']+=1
                return None
            for segment in cert['segments']:
                start=stage['start']+(stage['end']-stage['start'])*segment['t0']
                end=stage['start']+(stage['end']-stage['start'])*segment['t1']
                source=segment['source']
                if source=='G01':station=-1
                elif source in ('R1','R2'):station=pair[int(source[1:])-1]
                else:raise ValueError('Unexpected source in proved segment: '+str(source))
                if station>=0:
                    req=requirements.setdefault(station,dict(station=station,first=math.inf,last=-math.inf))
                    req['first']=min(req['first'],start);req['last']=max(req['last'],end)
                rows.append(dict(kind=stage['kind'],leg=list(stage['leg']),start=start,end=end,
                    relay=station>=0,station=station,proof=segment['proof'],min_margin_db=segment['min_margin_db'],
                    a=[a+segment['t0']*(b-a) for a,b in zip(stage['a'],stage['b'])],
                    b=[a+segment['t1']*(b-a) for a,b in zip(stage['a'],stage['b'])]))
        # Direct-only profiles already have the unchanged single:-1 option.
        if not requirements:
            self.counters['direct_pair_duplicates_skipped']+=1
            return None
        requirements=[requirements[k] for k in pair if k in requirements]
        assert 1<=len(requirements)<=2
        profile=dict(first=min(r['first'] for r in requirements),last=max(r['last'] for r in requirements),rows=rows)
        self.counters['pair_profile_proved']+=1
        return dict(key='pair:'+','.join(map(str,pair)),station=-2,requirements=requirements,profile=profile)

    def get(self,p):
        if p.id in self.multi_cache:return self.multi_cache[p.id]
        single=super().get(p)
        if single is None:route=stages_for(p,self.data,self.factory,self.geo);options=[]
        else:
            route=deepcopy(single);options=route.pop('options')
            for option in options:option.setdefault('key','single:'+str(option['station']))
        # Single-station feasibility does not preclude additional pair options:
        # distributing intervals can shorten each station's service envelope.
        for pair in self.pairs:
            option=self._pair_profile(route,pair)
            if option is not None:options.append(option)
        result=dict(route,options=options) if options else None
        self.multi_cache[p.id]=result
        return result


def inspect_profiles(solution,stations,data,factory,geo,pair_stations):
    profiles=MultiProfiles(data,factory,geo,stations,pair_stations=pair_stations)
    records=[];start=time.perf_counter()
    for transport in solution['transport']:
        p=factory.make(transport['model'],transport['boxes'],transport['order'])
        if p is None:raise ValueError('Source transport route is not physically feasible')
        result=profiles.get(p);options=result['options'] if result else []
        pair_options=[o for o in options if o['station']==-2]
        actual_two=[o for o in pair_options if len(o['requirements'])==2]
        # All profile intervals must cover every airborne/work stage with no
        # gap. Their proof records are constructed by the geometry certificate.
        for option in options:
            prior=result['flight_start_s']
            for row in option['profile']['rows']:
                if abs(row['start']-prior)>1e-7:raise AssertionError('Communication profile gap')
                prior=row['end']
                if 'station' in row:
                    assert row['relay']==(row['station']>=0)
            assert abs(prior-result['duration_s'])<1e-7
        records.append(dict(sortie=transport['id'],candidate=p.id,visits=list(p.visits),
            single_options=sum(o['station']!=-2 for o in options),pair_options=len(pair_options),
            actual_two_station_options=len(actual_two),
            pair_requirements=[dict(key=o['key'],requirements=o['requirements']) for o in pair_options]))
    return dict(passed=True,route_count=len(records),pair_stations=list(pair_stations),
                station_ids=[stations[k]['id'] for k in pair_stations],
                ordered_pairs=[list(p) for p in profiles.pairs],
                total_single_options=sum(r['single_options'] for r in records),
                total_pair_options=sum(r['pair_options'] for r in records),
                total_actual_two_station_options=sum(r['actual_two_station_options'] for r in records),
                routes_with_two_station_options=sum(r['actual_two_station_options']>0 for r in records),
                segment_cache_entries=len(profiles.pair_geometry_cache),counters=profiles.counters,
                wall_seconds=time.perf_counter()-start,routes=records,
                guarantee='Every accepted interval has a whole-segment Geometry certificate and one assigned source; scheduler must honor every per-station requirement.',
                optimality_scope='Finite pair combinations and certificate-induced assignments; not all possible handover schedules.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--solution',type=Path,default=ROOT/'results/question3_agent_update/solution_label_symmetry.json')
    parser.add_argument('--stations',type=Path,default=ROOT/'results/question3_from_senior/station_pool.json')
    parser.add_argument('--pair-stations',type=int,nargs='*')
    parser.add_argument('--output',type=Path,default=ROOT/'results/question3_agent_update/multi_profile_audit.json')
    args=parser.parse_args();data,factory,config,payload=load_senior()
    solution=json.loads(args.solution.read_text(encoding='utf-8'))
    stations=json.loads(args.stations.read_text(encoding='utf-8'))
    selected=args.pair_stations if args.pair_stations is not None else [r['station'] for r in solution['relay']]
    report=inspect_profiles(solution,stations,data,factory,SeniorGeometry(),selected)
    report['source_solution']=str(args.solution);report['source_station_pool']=str(args.stations)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['passed','route_count','pair_stations','station_ids','total_single_options',
                     'total_pair_options','total_actual_two_station_options','routes_with_two_station_options','wall_seconds']},ensure_ascii=False))


if __name__=='__main__':main()
