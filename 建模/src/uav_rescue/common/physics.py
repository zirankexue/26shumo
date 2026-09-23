from __future__ import annotations

from .data import Drone
from .terrain import Leg, Route


def equivalent_range(drone: Drone, payload: float) -> float:
    """题面附录2 P048直接给定；来源审计F07。"""
    if not 0 <= payload <= drone.payload:
        raise ValueError("有效载荷超出机型范围")
    return drone.empty_range - (drone.empty_range - drone.full_range) * (payload / drone.payload) ** 1.5


def leg_time(drone: Drone, leg: Leg) -> float:
    """题面P051；用附件最大竖直速度作为阶段速度属模型假设（F06）。"""
    return leg.climb / drone.climb_speed + leg.distance / drone.cruise_speed + leg.descent / drone.descent_speed


def leg_energy(drone: Drone, leg: Leg, payload: float, gravity: float) -> float:
    """F08—F10：依据Zhang等(2021)文献关系与题给参数推导的简化能耗。

    附录A第20页(A1)：电池能量/航程；假设L(q)对应全可用电量的水平航程。
    附录C第21页(C1)末项：Mg*v_a*sin(theta)，积分并用题给效率折算附加项。
    仅保留势能附加分量；机载电耗效率不另含充电损耗。未做实测标定。
    详见references/05_能耗公式全文核查.md及docs/公式来源与假设审计.md。
    """
    return (drone.battery * leg.distance / equivalent_range(drone, payload)
            + (drone.empty_mass + payload) * gravity * leg.climb / (3.6e6 * drone.climb_efficiency))


def trip_energy(drone: Drone, route: Route, payload: float, gravity: float) -> float:
    return leg_energy(drone, route.outbound, payload, gravity) + leg_energy(drone, route.inbound, 0.0, gravity)


def trip_times(drone: Drone, route: Route, boxes: int) -> tuple[float, float]:
    flight = leg_time(drone, route.outbound) + leg_time(drone, route.inbound)
    operation = flight + drone.preparation + boxes * drone.loading + drone.handover + boxes * drone.per_box_handover
    return flight, operation


def safe_payload(drone: Drone, route: Route, reserve: float, gravity: float,
                 tolerance: float = 1e-6) -> tuple[float | None, str]:
    if not 0 <= reserve < 1:
        raise ValueError("安全余量必须在[0,1)内")
    budget = (1 - reserve) * drone.battery
    if trip_energy(drone, route, 0.0, gravity) > budget:
        return None, "空载往返能量不足"
    if trip_energy(drone, route, drone.payload, gravity) <= budget:
        return drone.payload, "额定载质量限制"
    lo, hi = 0.0, drone.payload
    while hi - lo > tolerance:
        mid = (lo + hi) / 2
        if trip_energy(drone, route, mid, gravity) <= budget:
            lo = mid
        else:
            hi = mid
    return lo, "往返安全能量限制"
