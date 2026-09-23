import itertools
import math
import unittest
from dataclasses import replace
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, TiffImagePlugin

from uav_rescue.common.data import Drone, Node
from uav_rescue.common.terrain import Leg, Route, Terrain, supercover
from uav_rescue.common.physics import equivalent_range, leg_energy, leg_time, safe_payload, trip_energy
from uav_rescue.q1.optimize import Batch, solve


def drone():
    return Drone('T', 10, 10, .1, 10, 20000, 10000, 2, .2, 30, 2, 10, 2, 3, 2, .75, 0)


def route():
    return Route('S', 100, 3, Leg('O','S',5000,150,150,120), Leg('S','O',5000,150,120,150))


class TerrainTests(unittest.TestCase):
    def test_geotiff_pixel_center_georeferencing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.tif'
            tags=TiffImagePlugin.ImageFileDirectory_v2()
            tags[33550]=(.1,.1,0.0)
            tags[33922]=(0.0,0.0,0.0,100.0,20.0,0.0)
            tags[34735]=(1,1,0,2,1025,0,1,2,2048,0,1,4326)
            Image.fromarray(np.zeros((3,3),dtype=np.float32)).save(path,tiffinfo=tags)
            center=Node('O','origin',100,20,0)
            terrain=Terrain(path,center)
            self.assertEqual(terrain.grid(center),(.5,.5))
            self.assertEqual(terrain.cells(center,center),{(0,0)})
            next_center=Node('S','next',100.1,19.9,0)
            self.assertEqual(terrain.cells(next_center,next_center),{(1,1)})

    def test_point_at_center(self):
        self.assertEqual(supercover(.5,.5,.5,.5,4,4), {(0,0)})

    def test_corner_includes_four_cells(self):
        self.assertEqual(supercover(.5,.5,1.5,1.5,4,4), {(0,0),(0,1),(1,0),(1,1)})

    def test_line_along_boundary(self):
        self.assertEqual(supercover(1,.5,1,2.5,4,4), {(r,c) for r in range(3) for c in [0,1]})

    def test_reversal(self):
        rng = np.random.default_rng(42)
        for _ in range(100):
            a,b,c,d = rng.uniform(.01, 19.99, 4)
            self.assertEqual(supercover(a,b,c,d,20,20),supercover(c,d,a,b,20,20))

    def test_peak_on_corner_not_missed(self):
        t = Terrain.__new__(Terrain)
        t.elevations = np.array([[0,999],[0,0]], dtype=float)
        self.assertEqual(t.maximum(supercover(.5,.5,1.5,1.5,2,2)),999)

    def test_invalid_and_missing(self):
        with self.assertRaises(ValueError):supercover(-.1,0,1,1,2,2)
        t = Terrain.__new__(Terrain)
        for invalid in [float('nan'),float('inf'),-32767]:
            t.elevations=np.array([[invalid]])
            with self.assertRaises(ValueError):t.maximum({(0,0)})


class PhysicsTests(unittest.TestCase):
    def test_empty_full_ranges(self):
        d=drone()
        self.assertEqual(equivalent_range(d,0),20000)
        self.assertEqual(equivalent_range(d,10),10000)

    def test_descent_time_not_energy(self):
        d=drone(); a=route().outbound; b=replace(a,descent=a.descent+100)
        self.assertEqual(leg_energy(d,a,3,9.80665),leg_energy(d,b,3,9.80665))
        self.assertAlmostEqual(leg_time(d,b)-leg_time(d,a),50)

    def test_reserve_monotonic_and_boundary(self):
        d=drone();r=route();prev=math.inf
        for reserve in [.1,.2,.3,.4]:
            cap,status=safe_payload(d,r,reserve,9.80665)
            self.assertIsNotNone(cap)
            self.assertLessEqual(cap,prev)
            self.assertLessEqual(trip_energy(d,r,cap,9.80665),(1-reserve)*d.battery)
            if cap < d.payload-1e-6:
                self.assertGreater(trip_energy(d,r,cap+1e-6,9.80665),(1-reserve)*d.battery)
            prev=cap

    def test_unreachable_and_zero_feasible_distinct(self):
        d=drone();r=route();e=trip_energy(d,r,0,9.80665)
        cap,_=safe_payload(d,r,1-e/d.battery,9.80665)
        self.assertIsNotNone(cap)
        self.assertLess(cap,1e-6)
        self.assertIsNone(safe_payload(d,r,.99,9.80665)[0])


class OptimizationTests(unittest.TestCase):
    def test_against_exhaustive_batch_multiplicities(self):
        batches=[Batch('S','A',(1,0),1,.01,1,7,.12,.9),
                 Batch('S','B',(0,1),1,.01,1,6,.13,.9),
                 Batch('S','A',(1,1),2,.02,1,10,.30,.8),
                 Batch('S','B',(2,0),2,.02,1,14,.20,.8)]
        demand=(2,1)
        for order in itertools.permutations(['sorties','energy','time']):
            index={'sorties':0,'energy':1,'time':2}
            candidates=[]
            for counts in itertools.product(range(4),repeat=len(batches)):
                if tuple(sum(n*b.counts[j] for n,b in zip(counts,batches)) for j in range(2))!=demand:continue
                total=tuple(sum(n*b.cost[j] for n,b in zip(counts,batches)) for j in range(3))
                candidates.append(tuple(total[index[k]] for k in order))
            result=solve('S',demand,batches,order)
            actual=tuple(sum(b.cost[j] for b in result.batches) for j in range(3))
            self.assertEqual(tuple(actual[index[k]] for k in order),min(candidates))

    def test_infeasible_not_partial_success(self):
        self.assertFalse(solve('S',(1,1),[],('sorties','energy','time')).feasible)


if __name__=='__main__':unittest.main()
