from __future__ import annotations
from dataclasses import replace
from itertools import combinations,permutations,product
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import math
import tomllib
import unittest

from uav_rescue.common.charging import charging_time
from uav_rescue.q2.data import BoxTiming,load_scheduling_inputs
from uav_rescue.q2.routes import Candidate,RouteFactory,make_assignment,objective

HAS_ORTOOLS=importlib.util.find_spec('ortools') is not None
if HAS_ORTOOLS:
    from uav_rescue.q2.solver import solve_pool,assign_resources
from uav_rescue.q2.validate import validate_schedule


class ChargingTests(unittest.TestCase):
    def test_endpoints_and_ninety_percent(self):
        self.assertAlmostEqual(charging_time(0,1800),1800)
        self.assertAlmostEqual(charging_time(.9,1800),630)
        self.assertAlmostEqual(charging_time(1,1800),0)
        self.assertAlmostEqual(charging_time(.95,1800),315)
        self.assertAlmostEqual(charging_time(.9-1e-9,1800),630,places=4)
        self.assertGreater(charging_time(.2,1800),charging_time(.5,1800))
    def test_invalid_soc(self):
        for soc in [-.01,1.01,float('nan')]:
            with self.assertRaises(ValueError):charging_time(soc,1800)


@unittest.skipUnless(HAS_ORTOOLS,'问题二测试需OR-Tools，使用run_q2.py --test')
class SchedulingTests(unittest.TestCase):
    def fixture(self):
        timing={'a':BoxTiming(False,None,7,10,7),'b':BoxTiming(False,None,30,2,None),'c':BoxTiming(False,None,30,1,None)}
        data=SimpleNamespace(base=SimpleNamespace(boxes=list(timing),drones={'A':SimpleNamespace(per_box_handover=2)}),
                             timing=timing,units={'A':['U01']},battery_count={'A':2})
        pool={}
        for n in [1,2]:
            for ids in combinations(timing,n):
                pid=''.join(ids)
                pool[pid]=Candidate(pid,'A',ids,('S',),(('S',3000,5000,ids),),10000+n*2000,14000+n*1000,
                                    1+n*.2,0,0,0,0)
        return data,pool

    def test_cp_matches_exhaustive_route_batch_order_and_batteries(self):
        data,pool=self.fixture();solutions=[]
        for count in [2,3]:
            for selected in combinations(pool,count):
                ids=[b for p in selected for b in pool[p].boxes]
                if sorted(ids)!=sorted(data.timing):continue
                for route_order in permutations(selected):
                    local_orders=[list(permutations(pool[p].boxes)) for p in route_order]
                    for orders in product(*local_orders):
                        for batteries in product(range(2),repeat=count):
                            machine=0;avail=[0,0];schedule=[];valid=True
                            for pid,box_order,bat in zip(route_order,orders,batteries):
                                p=pool[pid];start=max(machine,avail[bat])
                                a=make_assignment(p,start,{'S':box_order},data)
                                if any(data.timing[b].hard is not None and c>data.timing[b].hard*1000 for b,c in a['deliveries'].items()):valid=False;break
                                schedule.append(a);machine=a['return'];avail[bat]=machine+p.charge
                            if valid:solutions.append(schedule)
        order=['tardiness','makespan','energy','sorties']
        key=lambda s:tuple(objective(s,pool,data)[k] for k in order)
        exact=min(solutions,key=key)
        found,trace=solve_pool(pool,data,order,10,0,None)
        self.assertEqual(key(found),key(exact))
        self.assertTrue(all(t['proven'] for t in trace))

    def test_first_box_on_time_last_box_late_and_optimized_order(self):
        data,pool=self.fixture();data.timing={k:v for k,v in data.timing.items() if k in 'ab'};data.base.boxes=list('ab')
        data.timing['b']=BoxTiming(False,None,7,1,None)
        p=pool['ab'];pool={'ab':p}
        result,_=solve_pool(pool,data,['tardiness','makespan','energy','sorties'],5,0)
        self.assertEqual(result[0]['deliveries'],{'a':7000,'b':9000})
        self.assertEqual(objective(result,pool,data)['tardiness'],2000)

    def test_interval_coloring_and_conflict_rejection(self):
        data,pool=self.fixture();p=pool['a'];a=make_assignment(p,0,{},data)
        b=make_assignment(pool['b'],p.duration,{},data)
        assigned=assign_resources([a,b],pool,data)
        self.assertNotEqual(assigned[0]['battery'],assigned[1]['battery'])
        self.assertEqual(assigned[0]['unit'],assigned[1]['unit'])
        with self.assertRaises(AssertionError):assign_resources([a,make_assignment(pool['b'],0,{},data)],pool,data)

    def test_multistop_visit_order_is_a_decision(self):
        data,_=self.fixture();data.timing={k:v for k,v in data.timing.items() if k in 'ab'};data.base.boxes=list('ab')
        # Two mutually exclusive routes: visit a first is required by its deadline.
        pool={}
        for order in permutations('ab'):
            stops=tuple((b,3000+6000*i,5000+6000*i,(b,)) for i,b in enumerate(order))
            pid='route_'+''.join(order)
            pool[pid]=Candidate(pid,'A',('a','b'),order,stops,18000,10000,1.,0,0,0,0)
        expected=[]
        for p in pool.values():
            a=make_assignment(p,0,{},data)
            if a['deliveries']['a']<=7000:expected.append(p.id)
        result,trace=solve_pool(pool,data,['tardiness','makespan','energy','sorties'],5,0)
        self.assertEqual([a['candidate'] for a in result],expected)
        self.assertEqual(result[0]['deliveries']['b'],13000)

    def test_battery_charge_blocks_reuse_on_another_drone(self):
        data,pool=self.fixture();data.timing={k:v for k,v in data.timing.items() if k in 'ab'};data.base.boxes=list('ab')
        data.units={'A':['U01','U02']};data.battery_count={'A':1}
        pool={k:pool[k] for k in 'ab'}
        result,_=solve_pool(pool,data,['tardiness','makespan','energy','sorties'],5,0)
        rows=sorted(result,key=lambda a:a['start'])
        self.assertEqual(rows[1]['start'],pool['a'].duration+pool['a'].charge)
        self.assertEqual(rows[1]['return'],39000)
        with self.assertRaises(AssertionError):
            assign_resources([make_assignment(pool['a'],0,{},data),make_assignment(pool['b'],12000,{},data)],pool,data)


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project=Path(__file__).resolve().parents[1]
        cfg=tomllib.loads((cls.project/'configs/q2.toml').read_text(encoding='utf-8'))
        cls.data=load_scheduling_inputs(cls.project/'../数据',cls.project/'../结果提交模板.xlsx')
        cls.factory=RouteFactory(cls.data,cfg['physics'])

    def test_deadline_intersections(self):
        t=self.data.timing
        self.assertEqual(t['S001-MED-02'].hard,3600)
        self.assertFalse(t['S001-MED-02'].first)
        self.assertEqual(t['S012-MED-01'].hard,3600)
        self.assertEqual(t['S014-MED-01'].hard,3600)
        self.assertEqual(t['S015-MED-01'].hard,7200)
        self.assertEqual(t['S015-WAT-01'].hard,10800)
        self.assertIsNone(t['S001-WAT-02'].hard)

    def test_all_pairs_and_q1_compatibility(self):
        self.assertEqual(len(self.factory.legs),240)
        for sid,node in self.data.base.services.items():
            q1=self.factory.terrain.route(node,50,30)
            self.assertEqual(q1.outbound,self.factory.legs['O01',sid])
            self.assertEqual(q1.inbound,self.factory.legs[sid,'O01'])

    def test_multistop_remaining_payload_and_each_stop_climbs(self):
        from uav_rescue.common.physics import leg_energy
        ids=['S001-MED-01','S011-MED-01']
        p=self.factory.make('B',ids,['S001','S011'])
        self.assertIsNotNone(p)
        d=self.data.base.drones['B'];legs=self.factory.legs
        expected=sum(leg_energy(d,legs[a,b],q,9.80665) for a,b,q in [('O01','S001',6),('S001','S011',3),('S011','O01',0)])
        self.assertAlmostEqual(expected,p.energy)
        self.assertGreater(legs['S001','S011'].climb,0)
        self.assertGreaterEqual(p.rounding_s,0)

    @unittest.skipUnless(HAS_ORTOOLS,'需要OR-Tools资源分配器')
    def test_validator_rejects_tampered_checkpoint(self):
        from uav_rescue.q2.candidates import initial_pool,greedy_schedule
        pool=initial_pool(self.factory)
        schedule=next((s for seed in range(24) if (s:=greedy_schedule(self.factory,pool,seed)) is not None),None)
        self.assertIsNotNone(schedule)
        schedule=assign_resources(schedule,pool,self.data)
        validate_schedule(schedule,pool,self.factory)
        b=next(iter(schedule[0]['deliveries']))
        schedule[0]['deliveries'][b]+=1
        with self.assertRaises(AssertionError):validate_schedule(schedule,pool,self.factory)


if __name__=='__main__':unittest.main()
