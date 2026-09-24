"""Export existing geometry and fixed-speed flight times; never run optimization."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
for folder in (PROJECT / ".runtime/python", PROJECT / ".runtime/q2", PROJECT / "src"):
    if folder.exists():
        sys.path.insert(0, str(folder))

from uav_rescue.common.data import file_hash, load_inputs
from uav_rescue.common.terrain import Terrain


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT / path).resolve()


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def require_close(actual: float, expected: float, label: str, tolerance: float = 1e-7) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
        raise ValueError(f"{label}: {actual} != {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="outputs/q2/tables/results.json")
    parser.add_argument("--legs", default="cache/q2/legs.json")
    parser.add_argument("--output", default="outputs/common_geometry")
    args = parser.parse_args()
    result_path, leg_path, output = map(resolve_path, (args.results, args.legs, args.output))
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    legs = json.loads(leg_path.read_text(encoding="utf-8"))
    config = payload["metadata"]["config"]
    data = load_inputs(resolve_path(config["paths"]["data_root"]),
                       resolve_path(config["paths"]["template"]))
    terrain = Terrain(data.dem_path, data.origin)
    clearance = float(config["physics"]["clearance_m"])
    service_height = float(config["physics"]["service_height_m"])
    nodes = {data.origin.id: data.origin, **data.services}
    node_ids = sorted(nodes)
    if len(nodes) != 16 or len(legs) != 240:
        raise ValueError("Expected 16 nodes and 240 directed legs")
    saved_nodes = {n["id"]: n for n in payload["nodes"]}
    saved_legs = {(x["start"], x["end"]): x for x in payload["legs"]}
    indexed = {(x["start"], x["end"]): x for x in legs}
    expected_pairs = {(a, b) for a in nodes for b in nodes if a != b}
    if set(indexed) != expected_pairs or set(saved_legs) != expected_pairs:
        raise ValueError("Directed leg coverage is incomplete or duplicated")
    node_rows, leg_rows, time_rows = [], [], []
    coordinates = {}
    for node_id in node_ids:
        node = nodes[node_id]
        for key, value in (("lon", node.lon), ("lat", node.lat), ("elevation", node.elevation)):
            require_close(saved_nodes[node_id][key], value, f"{node_id}/{key}")
        x, y = terrain.xy(node)
        if "map" in payload:
            require_close(payload["map"]["xy"][node_id][0] * 1000, x, node_id + "/map_x")
            require_close(payload["map"]["xy"][node_id][1] * 1000, y, node_id + "/map_y")
        coordinates[node_id] = (x, y)
        node_rows.append(dict(node_id=node_id, name=node.name, longitude_deg=node.lon,
                              latitude_deg=node.lat, ground_elevation_m=node.elevation,
                              operation_altitude_m=node.elevation + (0 if node_id == data.origin.id else service_height),
                              east_m=x, north_m=y))
    maximum_error = 0.0
    for (start, end), leg in sorted(indexed.items()):
        reverse = indexed[end, start]
        distance = math.dist(coordinates[start], coordinates[end])
        maximum_error = max(maximum_error, abs(distance - leg["distance"]))
        require_close(distance, leg["distance"], f"{start}->{end}/distance")
        require_close(leg["distance"], reverse["distance"], f"{start}->{end}/symmetry")
        require_close(leg["cruise_altitude"], reverse["cruise_altitude"], f"{start}->{end}/cruise_symmetry")
        # Reuse the same complete intersected-cell calculation to verify the cached geometry.
        rebuilt = terrain.leg(nodes[start], nodes[end], clearance, service_height)
        for key in ("distance", "cruise_altitude", "climb", "descent"):
            require_close(leg[key], saved_legs[start, end][key], f"{start}->{end}/saved_{key}")
            require_close(leg[key], getattr(rebuilt, key), f"{start}->{end}/rebuilt_{key}")
        leg_rows.append(dict(start=start, end=end, distance_m=leg["distance"],
                             terrain_max_m=leg["cruise_altitude"] - clearance,
                             cruise_altitude_m=leg["cruise_altitude"],
                             climb_m=leg["climb"], descent_m=leg["descent"], clearance_m=clearance))
        for drone_id, drone in sorted(data.drones.items()):
            climb = leg["climb"] / drone.climb_speed
            horizontal = leg["distance"] / drone.cruise_speed
            descent = leg["descent"] / drone.descent_speed
            time_rows.append(dict(start=start, end=end, drone_type=drone_id,
                                  climb_speed_m_s=drone.climb_speed, cruise_speed_m_s=drone.cruise_speed,
                                  descent_speed_m_s=drone.descent_speed, climb_time_s=climb,
                                  horizontal_time_s=horizontal, descent_time_s=descent,
                                  flight_time_s=climb + horizontal + descent))
    source_paths = [result_path, leg_path, Path(__file__).resolve(),
                    PROJECT / "src/uav_rescue/common/terrain.py", *data.source_paths]
    source_hashes = {str(path): file_hash(path) for path in source_paths}
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "nodes_wgs84_local_xy.csv", node_rows)
    write_csv(output / "directed_legs_240.csv", leg_rows)
    write_csv(output / "flight_times_720.csv", time_rows)
    write_csv(output / "horizontal_distance_matrix_m.csv", [
        {"start_end": start, **{end: (0.0 if start == end else indexed[start, end]["distance"])
                               for end in node_ids}} for start in node_ids])
    shutil.copyfile(leg_path, output / "legs_source.json")
    checks = dict(nodes=16, directed_legs=240, unordered_pairs=120, flight_time_rows=720,
                  matrix_shape=[16, 16], matrix_diagonal_zero=True, matrix_symmetric=True,
                  saved_results_equal_cache=True, coordinates_equal_Terrain_and_saved_map=True,
                  full_dem_geometry_matches=True, max_distance_recompute_error_m=maximum_error,
                  source_sha256=source_hashes)
    checks["generated_utc"] = datetime.now(timezone.utc).isoformat()
    checks["output_sha256"] = {path.name: file_hash(path) for path in sorted(output.iterdir())
                              if path.suffix in (".csv", ".json") and path.name != "verification.json"}
    (output / "verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = f"""# 距离、坐标与航段基础数据

本目录由 `python scripts/export_geometry.py` 从正式题二结果与航段缓存导出，不运行优化、不读取检查点。相对路径均相对建模工程。

| 文件 | 内容 |
| --- | --- |
| nodes_wgs84_local_xy.csv | 16节点：原始WGS84经纬度、节点表地面海拔、作业海拔、局部东西/南北坐标（m） |
| horizontal_distance_matrix_m.csv | 16×16水平直线距离矩阵（m），行列同序，主对角为0，矩阵对称 |
| directed_legs_240.csv | 240条有向航段：水平距离、相交DEM像元最高地形、巡航海拔、爬升、下降与净空（m） |
| flight_times_720.csv | 240航段×3机型的爬升、水平、下降及飞行总时间（s）；不含准备、装载和交接 |
| legs_source.json | 原始航段缓存的字节一致副本，保留原字段与未舍入数值 |
| verification.json | 来源SHA-256、输出SHA-256、矩阵和240航段复核记录 |

## 坐标与距离口径

输入经纬度及DEM地理坐标系为WGS84（EPSG:4326）；Terrain读取GeoTIFF时检查4326与PixelIsPoint标签。
以O01（经度{data.origin.lon}°，纬度{data.origin.lat}°）为原点，使用WGS84椭球在原点纬度的卯酉圈曲率半径N₀与子午圈曲率半径M₀进行局部线性换算：

`x = N₀ cos(φ₀) (λ−λ₀)`；`y = M₀ (φ−φ₀)`；`dᵢⱼ = sqrt((xⱼ−xᵢ)² + (yⱼ−yᵢ)²)`。

经纬度差用弧度，`a=6378137 m`，`e²=0.0066943799901413165`。
这是基于WGS84椭球的局部平面近似，**不是椭球测地线精确距离，也不是UTM投影距离**。
距离是水平直线长度，不是贴地表距离、道路距离或把升降合并后的三维斜距。

节点地面海拔来自原节点表，独立于DEM地形；不强行将节点表高度对齐DEM。
O01作业高度为节点地面海拔，服务区为节点地面以上{service_height:g} m。
巡航海拔为完整相交DEM像元最大值加{clearance:g} m，包含边界/角点接触像元。
有向航段的水平距离和巡航高度对称，而爬升与下降因端点作业高度不同而交换。
三阶段时间为 `climb/climb_speed + distance/cruise_speed + descent/descent_speed`，表中保留原始连续秒值；排程向上取整至1毫秒属于后续调度环节。

## 核验与来源

本次核验16节点、240条有向航段、720条机型航段时间。重算距离最大绝对误差为{maximum_error:.3g} m。
坐标与Terrain及正式结果map一致；完整DEM重建的距离、巡航、爬升、下降与正式结果及缓存一致。
源文件SHA-256见 `verification.json`，其中包含 `cache/q2/legs.json` 与 `outputs/q2/tables/results.json`。
原始输入、已有结果及检查点均未修改。
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    for path in source_paths:
        if file_hash(path) != source_hashes[str(path)]:
            raise RuntimeError(f"Source changed during export: {path}")
    print(json.dumps({"output": str(output), "nodes": 16, "directed_legs": 240,
                      "flight_time_rows": 720, "max_distance_error_m": maximum_error,
                      "all_checks_passed": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
