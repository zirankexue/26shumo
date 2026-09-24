from __future__ import annotations

from collections import Counter
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from ..common.data import Inputs
from ..common.terrain import Route
from .optimize import Batch, Solution, INDEX


def validate_manifest(inputs: Inputs, routes: dict[str, Route], rows: list[dict],
                      reserve: float, gravity: float, energy_tolerance: float,
                      require_complete: bool = True) -> dict:
    """从逐箱原始属性重算；不调用候选批次的能耗和时间函数。"""
    by_id = {b.id: b for b in inputs.boxes}
    used = []
    residuals = {"mass_kg": 0.0, "volume_m3": 0.0, "energy_kwh": 0.0,
                 "flight_s": 0.0, "operation_s": 0.0, "return_soc": 0.0}
    for row in rows:
        ids = row["box_ids"]
        if not ids or any(i not in by_id for i in ids):
            raise AssertionError("空批次或未知货箱")
        boxes = [by_id[i] for i in ids]
        if any(b.service != row["service"] for b in boxes):
            raise AssertionError("跨服务区装箱")
        d, route = inputs.drones[row["drone"]], routes[row["service"]]
        mass = math.fsum(b.mass for b in boxes)
        volume = math.fsum(b.volume for b in boxes)
        energy, flight = 0.0, 0.0
        for leg, q in [(route.outbound, mass), (route.inbound, 0.0)]:
            effective_range = d.empty_range - (d.empty_range - d.full_range) * (q / d.payload) ** 1.5
            energy += d.battery * leg.distance / effective_range
            energy += (d.empty_mass + q) * gravity * leg.climb / (3600000 * d.climb_efficiency)
            flight += leg.distance / d.cruise_speed + leg.climb / d.climb_speed + leg.descent / d.descent_speed
        operation = flight + d.preparation + d.loading * len(ids) + d.handover + d.per_box_handover * len(ids)
        actual = {"mass_kg": mass, "volume_m3": volume, "energy_kwh": energy,
                  "flight_s": flight, "operation_s": operation, "return_soc": 1 - energy / d.battery}
        if mass > d.payload + 1e-9 or volume > d.volume + 1e-12:
            raise AssertionError(f"{row['sortie']}装载超限")
        if energy > (1 - reserve) * d.battery + energy_tolerance:
            raise AssertionError(f"{row['sortie']}能量超限")
        for k, v in actual.items():
            error = abs(v - row[k])
            residuals[k] = max(residuals[k], error)
            if error > (1e-6 if k.endswith('_s') else 1e-9):
                raise AssertionError(f"{row['sortie']} {k}重算不一致")
        used.extend(ids)
    if len(used) != len(set(used)):
        raise AssertionError("有货箱被重复交付")
    if require_complete and set(used) != set(by_id):
        raise AssertionError("未完成全部货箱交付")
    return {"passed": True, "delivered_boxes": len(used), "complete": set(used) == set(by_id),
            "sorties": len(rows), "max_recalculation_errors": residuals,
            "min_return_soc": min((r["return_soc"] for r in rows), default=None)}


def milp_crosscheck(demand: tuple[int, ...], batches: list[Batch], solution: Solution,
                    time_limit: float) -> dict:
    """用未做机型支配删减的全部批次，分三阶段整数规划独立核对。"""
    if not batches or not solution.feasible:
        raise ValueError("MILP基准核验要求可行候选和方案")
    a = np.array([b.counts for b in batches], dtype=float).T
    objectives = [np.ones(len(batches)), np.array([b.energy for b in batches]) * 1000,
                  np.array([b.operation for b in batches])]
    constraints = [LinearConstraint(a, demand, demand)]
    stage_values, statuses = [], []
    chosen = None
    for stage, name in enumerate(solution.order):
        objective = objectives[INDEX[name]]
        result = milp(objective, integrality=np.ones(len(batches)),
                      bounds=Bounds(np.zeros(len(batches)), np.full(len(batches), sum(demand))),
                      constraints=constraints,
                      options={"time_limit": time_limit, "mip_rel_gap": 0.0})
        if not result.success or result.status != 0:
            raise RuntimeError(f"{solution.service} MILP阶段{stage + 1}未证明最优：{result.message}")
        chosen = np.rint(result.x).astype(int)
        if not np.array_equal(a @ chosen, demand):
            raise AssertionError("MILP舍入后的解不满足箱数等式")
        value = float(objective @ chosen)
        stage_values.append(value)
        statuses.append({"stage": stage + 1, "objective": name, "status": result.status, "mip_gap": float(result.mip_gap)})
        # Energy measured in Wh; tolerance = 1e-8 kWh, well below printed precision.
        eps = 0.0 if name == 'sorties' else 1e-5
        constraints.append(LinearConstraint(objective, value - eps, value + eps))
    objective = (int(objectives[0] @ chosen), float(objectives[1] @ chosen) / 1000,
                 float(objectives[2] @ chosen))
    dp = solution.objective
    if objective[0] != dp[0] or abs(objective[1] - dp[1]) > 1e-7 or abs(objective[2] - dp[2]) > 1e-4:
        raise AssertionError(f"{solution.service} DP与MILP不一致：DP={dp}; MILP={objective}")
    return {"service": solution.service, "passed": True, "candidate_count": len(batches),
            "dp_objective": list(dp), "milp_objective": list(objective), "stages": statuses}


def validate_sensitivity(scenarios: list[dict]) -> dict:
    previous = None
    for scenario in sorted(scenarios, key=lambda s: s['reserve']):
        if previous is not None:
            if not previous['feasible'] and scenario['feasible']:
                raise AssertionError("安全余量增加后从不可行变为可行")
            if previous['feasible'] and scenario['feasible'] and scenario['summary']['sorties'] < previous['summary']['sorties']:
                raise AssertionError("安全余量增加后最少架次下降")
            old = {(r['service'], r['drone']): r['safe_payload_kg'] for r in previous['safe_payloads']}
            for r in scenario['safe_payloads']:
                a, b = old[(r['service'], r['drone'])], r['safe_payload_kg']
                if (a is None and b is not None) or (a is not None and b is not None and b > a + 1e-6):
                    raise AssertionError("安全余量增加后安全载荷上升")
        previous = scenario
    return {"passed": True, "scenario_count": len(scenarios), "checked": "可行域、安全载荷与最少架次单调性"}
