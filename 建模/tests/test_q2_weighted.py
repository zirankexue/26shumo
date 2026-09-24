import unittest
import copy
from types import SimpleNamespace
from uav_rescue.q2.data import BoxTiming
from uav_rescue.q2.routes import Candidate,make_assignment,objective
from uav_rescue.q2.solver import solve_pool
from uav_rescue.q2.weighted import specification,score,integer_score,METRICS,reselect_scenarios


class WeightedTests(unittest.TestCase):
    def test_actual_solver_switches_with_preference(self):
        data=SimpleNamespace(base=SimpleNamespace(boxes=['a'],drones={'A':SimpleNamespace(per_box_handover=2)}),
            timing={'a':BoxTiming(False,None,30,1,30)},units={'A':['U01']},battery_count={'A':1})
        pool={}
        for pid,duration,energy in [('fast',12000,3),('middle',16000,1.5),('low_energy',20000,1)]:
            pool[pid]=Candidate(pid,'A',('a',),('S',),(('S',3000,5000,('a',)),),duration,5000,energy,0,0,0,0)
        choices=[[make_assignment(p,0,{},data)] for p in pool.values()]
        vectors=[objective(s,pool,data) for s in choices]
        winners=[]
        for weights in [[1,30,1,1],[1,1,30,1],[3,1.5,1,.5]]:
            spec=specification(vectors,weights)
            found,trace=solve_pool(pool,data,['weighted'],3,0,weighted_spec=spec)
            expected=min(integer_score(v,spec) for v in vectors)
            self.assertEqual(integer_score(objective(found,pool,data),spec),expected)
            self.assertTrue(trace[0]['proven'])
            winners.append(found[0]['candidate'])
        self.assertEqual(winners[:2],['fast','low_energy'])

    def test_normalization_units_and_precision(self):
        vectors=[dict(zip(METRICS,[0,1000,10**9,10])),dict(zip(METRICS,[7001,9500,9000000001,21]))]
        spec=specification(vectors,[3,1.5,1,.5])
        v=dict(zip(METRICS,[311,4231,3551234891,14]))
        offset=sum(spec['alpha'][k]*spec['lower'][k]/spec['span'][k] for k in METRICS)
        rounded=integer_score(v,spec)/(sum(spec['integer_weights'].values())*spec['precision'])-offset
        self.assertGreaterEqual(rounded,score(v,spec)-1e-12)
        self.assertLess(rounded-score(v,spec),1/spec['precision'])
        other=[dict(x,energy=x['energy']*100) for x in vectors]
        self.assertAlmostEqual(score(v,spec),score(dict(v,energy=v['energy']*100),specification(other,[3,1.5,1,.5])))
        self.assertEqual(spec['alpha']['tardiness'],.5)

    def test_complete_scenarios_reassessed_without_losing_endpoints(self):
        vectors={
            'weighted':dict(zip(METRICS,[0,1500,2*10**9,2])),
            'scenario_tardiness':dict(zip(METRICS,[2000,2500,3*10**9,3])),
            'scenario_makespan':dict(zip(METRICS,[1000,1000,3*10**9,3])),
            'scenario_energy':dict(zip(METRICS,[1000,2000,10**9,1])),
            'scenario_sorties':dict(zip(METRICS,[3000,3000,4*10**9,4])),
        }
        p={'objective_vectors':vectors,'schemes':{k:{'origin':k} for k in vectors},
           'schedules':{k:[{'origin':k}] for k in vectors},'scenario_specs':{},
           'cloud':[{'vector':dict.fromkeys(METRICS,0)}]}
        for i,m in enumerate(METRICS):
            w=[1]*4;w[i]=10
            p['scenario_specs'][m]=specification(list(vectors.values()),w)
        before=copy.deepcopy(p)
        choices=reselect_scenarios(p)
        self.assertEqual(choices['scenario_tardiness']['source'],'weighted')
        self.assertEqual(choices['scenario_sorties']['source'],'scenario_energy')
        for m in METRICS:
            key='scenario_'+m
            self.assertEqual(p['scenario_search_endpoints'][key]['schedules'],before['schedules'][key])
            sp=p['scenario_specs'][m]
            self.assertAlmostEqual(score(p['objective_vectors'][key],sp),
                                   min(score(v,sp) for v in before['objective_vectors'].values()))
        once=copy.deepcopy(p)
        reselect_scenarios(p)
        self.assertEqual(p,once)
        self.assertEqual(p['schedules']['weighted'],before['schedules']['weighted'])
