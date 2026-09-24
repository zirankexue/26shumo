import itertools
import unittest
from types import SimpleNamespace

from uav_rescue.q2.data import BoxTiming
from uav_rescue.q2.fast_schedule import Scheduler, fast_evaluate, polish
from uav_rescue.q2.routes import Candidate
from uav_rescue.q2.weighted import METRICS, specification


def inputs(timing, units=1, batteries=1, handover=1):
    return SimpleNamespace(
        base=SimpleNamespace(drones={'A': SimpleNamespace(per_box_handover=handover)}),
        timing=timing, units={'A': ['U' + str(i) for i in range(units)]},
        battery_count={'A': batteries})


def timing(expected, weight=1, hard=None):
    return BoxTiming(False, None, expected, weight, hard)


def candidate(pid, boxes, base_end=0, duration=4000, charge=0, drone='A'):
    return Candidate(pid, drone, tuple(boxes), ('S',),
                     (('S', base_end, base_end, tuple(boxes)),),
                     duration, charge, 1.0, 0, 0, 0, 0)


def spec():
    return specification([dict(zip(METRICS, [0, 0, 0, 0])),
                          dict(zip(METRICS, [100000, 10000, 10**10, 10]))],
                         [3, 1.5, 1, .5])


class FastScheduleTests(unittest.TestCase):
    def test_actual_arrival_box_order_equals_exhaustive(self):
        data = inputs({'a': timing(1, 1), 'b': timing(2, 10),
                       'c': timing(6, 2, 4)}, handover=1)
        route = candidate('p', ['a', 'b', 'c'], base_end=1000)
        result = fast_evaluate([route], data, spec())
        self.assertIsNotNone(result)
        schedule, vector, _ = result
        possible = []
        for perm in itertools.permutations(route.boxes):
            completion = {b: 1000 + (i + 1) * 1000 for i, b in enumerate(perm)}
            if any(t.hard is not None and completion[b] > t.hard * 1000
                   for b, t in data.timing.items()):
                continue
            possible.append(sum(t.priority * max(0, completion[b] - t.expected * 1000)
                                for b, t in data.timing.items()))
        self.assertEqual(vector['tardiness'], min(possible))
        self.assertEqual(schedule[0]['deliveries']['b'], 2000)
        self.assertEqual(schedule[0]['deliveries']['a'], 3000)

    def test_first_hard_box_on_time_last_late_is_rejected(self):
        data = inputs({'a': timing(1, hard=1), 'b': timing(1, hard=1)})
        self.assertIsNone(fast_evaluate([candidate('p', ['a', 'b'])], data, spec()))
        data.timing['b'] = timing(1)
        result = fast_evaluate([candidate('p', ['a', 'b'])], data, spec())
        self.assertEqual(result[0][0]['deliveries'], {'a': 1000, 'b': 2000})
        self.assertEqual(result[1]['tardiness'], 1000)

    def test_charge_release_is_inclusive_and_battery_can_be_shared(self):
        data = inputs({b: timing(100) for b in 'abc'}, units=2, batteries=2)
        p = [candidate('p1', ['a'], duration=3000, charge=1000),
             candidate('p2', ['b'], duration=2000, charge=1000),
             candidate('p3', ['c'], duration=1000, charge=0)]
        schedule, _, _ = fast_evaluate(p, data, spec())
        self.assertEqual([a['start'] for a in schedule], [0, 0, 3000])
        # A single battery blocks a second idle aircraft until charging ends.
        data.battery_count['A'] = 1
        schedule, _, _ = fast_evaluate(p[:2], data, spec())
        self.assertEqual([a['start'] for a in schedule], [0, 4000])

    def test_no_cross_type_battery_or_aircraft_borrowing(self):
        data = inputs({'a': timing(100), 'b': timing(100), 'c': timing(100)},
                      units=1, batteries=1)
        data.base.drones['B'] = SimpleNamespace(per_box_handover=1)
        data.units['B'] = ['UB']
        data.battery_count['B'] = 1
        p = [candidate('p1', ['a'], duration=2000, charge=3000),
             candidate('p2', ['b'], duration=1000, drone='B'),
             candidate('p3', ['c'], duration=1000)]
        result = fast_evaluate(p, data, spec())
        self.assertEqual([a['start'] for a in result[0]], [0, 0, 5000])

    def test_priority_is_respected_cache_bounded_and_no_duplicate_boxes(self):
        data = inputs({'a': timing(10), 'b': timing(10)})
        p = [candidate('p1', ['a']), candidate('p2', ['b'])]
        evaluator = Scheduler(data, spec(), cache_size=1, candidate_cache_size=1)
        result = evaluator.evaluate(p, ['p2', 'p1'])
        self.assertEqual([a['candidate'] for a in result[0]], ['p2', 'p1'])
        self.assertLessEqual(len(evaluator._orders), 1)
        self.assertLessEqual(len(evaluator._candidate_data), 1)
        self.assertIsNone(evaluator.evaluate([p[0], candidate('other', ['a'])]))
        with self.assertRaises(ValueError):
            evaluator.evaluate(p, ['p1', 'p1'])

    def test_polish_matches_small_complete_dispatch_enumeration(self):
        data = inputs({'a': timing(20, 1), 'b': timing(2, 10), 'c': timing(6, 5)})
        p = [candidate('p' + b, [b], duration=3000) for b in 'abc']
        evaluator = Scheduler(data, spec())
        all_values = [evaluator.evaluate(p, [r.id for r in permutation])[2]
                      for permutation in itertools.permutations(p)]
        start = evaluator.evaluate(p)
        best = polish(p, start[0], data, spec(), iterations=200, seed=0,
                      scheduler=evaluator)
        self.assertAlmostEqual(best[2], min(all_values))
        self.assertLessEqual(best[2], start[2])
        self.assertEqual({b for a in best[0] for b in a['deliveries']}, set('abc'))


if __name__ == '__main__':
    unittest.main()
