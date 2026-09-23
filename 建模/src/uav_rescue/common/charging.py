"""题面附录2 P063—065直接给定的分段充电模型；来源审计F13。"""
import math


def charging_time(soc: float, full_time: float) -> float:
    if not math.isfinite(soc) or not 0 <= soc <= 1 or full_time <= 0:
        raise ValueError("SOC必须在[0,1]且完全充电时间为正")
    if soc < 0.9:
        return full_time * (0.65 * (0.9 - soc) / 0.9 + 0.35)
    return full_time * 0.35 * (1 - soc) / 0.1
