"""Export lossless paper source tables from the verified first-question results.

This script does not optimize or modify formal solver results. Nested structures
are retained as JSON cells; floats use round-trip-safe Python representations.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / '第一问'
DATA = PAPER / '数据'
TEXT = PAPER / '文字'
RESULTS = PAPER / '结果'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def encode(value):
    if isinstance(value, (dict, list, bool)) or value is None:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return repr(value) if isinstance(value, float) else str(value)


def field_info(field, examples):
    descriptions = {
        'id':'原始节点、机型或货箱唯一编号', 'name':'原表名称', 'lon':'WGS84经度', 'lat':'WGS84纬度',
        'elevation_m':'原节点表地面海拔；与DEM像元高程分别取源', 'x_m':'O01原点局部椭球平面东向坐标',
        'y_m':'O01原点局部椭球平面北向坐标', 'operation_altitude_m':'O01取地面海拔，服务区取地面海拔+30m',
        'service_id':'目标服务区编号', 'model':'机型编号', 'batch_id':'方案内架次编号',
        'plan_id':'方案编号', 'box_ids':'该架次包含的真实货箱编号JSON数组', 'box_count':'货箱数',
        'counts':'按MED,WAT,FOD,HYG排列的各类货箱数JSON数组',
        'demand':'按MED,WAT,FOD,HYG排列的服务区需求数量JSON数组',
        'distance_m':'O01到目标服务区的单程水平直线距离；不含垂直段',
        'cruise_altitude_m':'所有相交DEM闭像元最高海拔+50m', 'max_terrain_m':'相交DEM闭像元原值的最大海拔',
        'outbound_climb_m':'巡航海拔减O01地面海拔', 'return_climb_m':'巡航海拔减服务区作业海拔',
        'crossed_cells':'完整相交DEM像元的零基[row,column]数组；PixelIsPoint',
        'peak_cells':'达到最高地形海拔的零基[row,column]数组', 'crossed_cell_count':'相交DEM闭像元数',
        'energy_kwh':'单架次或方案往返总运输能耗', 'flight_time_s':'含水平往返及垂直升降的飞行时间',
        'work_time_s':'准备+装载+飞行+交接的累计作业时间；不是多机并行完工时间',
        'flight_count':'往返架次数', 'safe_payload_kg':'仅质量与能量约束下的连续最大安全载荷；null为空载往返亦不可行',
        'limiting_factor':'约束分类：额定载荷/能量余量/空载不可行', 'return_soc_percent':'返航剩余能量占可用电池能量的百分数',
        'minimum_soc_percent':'方案中最低返航SOC百分数', 'reserve_percent':'安全余量百分数，例如20表示20%',
        'critical_reserve_percent':'该候选批次保持可行的最大安全余量百分数',
        'threshold_percent':'相邻选中方案切换的安全余量百分数',
        'lower_percent':'方案适用区间下界；是否包含见lower_inclusive', 'upper_percent':'方案适用区间上界',
        'lower_inclusive':'首段为true，之后下端点属于前一段', 'upper_inclusive':'上端点满足能量等号，包含在当前区间',
        'maximum_reserve_percent':'该服务区该类单箱在所有可用机型中的最大可行余量',
        'best_models':'达到最高单箱可行余量的机型JSON数组', 'model_limits':'逐机型单箱可行上限JSON数组',
        'batches':'完整逐架次方案JSON数组，保留真实箱号及能耗时间等所有字段',
        'changes':'方案切换前后发生变化的服务区与批次JSON数组',
        'safe_payloads':'A/B/C机型连续安全载荷JSON字典', 'samples':'报告中指定余量取值的安全载荷JSON数组',
        'model_counts':'各机型架次数JSON字典', 'normalized_objectives':'相对各坐标理想最小值的目标归一化字典',
        'first_batch':'原需求表保留字段；第一问未施加物理机队与首批时限调度',
        'first_deadline_s':'原需求表保留字段；第一问未作为约束求解',
        'due_s':'原需求表保留字段；第一问未作为约束求解',
        'priority':'原需求表保留字段；第一问未作为调度目标求解',
    }
    units = {'lon':'degree', 'lat':'degree', 'x_m':'m', 'y_m':'m'}
    if field not in units:
        units[field] = next((unit for suffix,unit in [('_percent','%'),('_degree','degree'),('_kwh','kWh'),('_mps','m/s'),('_kg','kg'),('_m3','m^3'),('_litre','L'),('_m','m'),('_s','s')] if field.endswith(suffix)), '1')
    sample = next((v for v in examples if v is not None), None)
    dtype = 'json' if isinstance(sample,(dict,list)) else 'boolean' if isinstance(sample,bool) else 'integer' if isinstance(sample,int) else 'number' if isinstance(sample,float) else 'text'
    return dict(field=field, unit=units[field], type=dtype, description=descriptions.get(field, '沿用正式JSON字段；详见源对象及字段名中的单位后缀'))


def export_table(name, rows, source, note):
    assert rows, name
    keys = list(dict.fromkeys(key for row in rows for key in row))
    path = DATA / f'{name}.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(keys)
        for row in rows:
            writer.writerow([encode(row.get(key)) for key in keys])
    # Validate numeric round-trip and retained structured cells after serialization.
    with path.open(encoding='utf-8-sig', newline='') as stream:
        recovered = list(csv.DictReader(stream))
    assert len(recovered) == len(rows)
    for original, saved in zip(rows, recovered):
        for key in keys:
            value = original.get(key)
            if isinstance(value,(dict,list,bool)) or value is None:
                assert json.loads(saved[key]) == value
            elif isinstance(value,float):
                assert float(saved[key]) == value
            elif isinstance(value,int):
                assert int(saved[key]) == value
            else:
                assert saved[key] == str(value)
    return dict(file=f'数据/{path.name}', row_count=len(rows), sha256=sha256(path), source=source,
                processing=note, columns=[field_info(key,[row.get(key) for row in rows]) for key in keys],
                lossless_roundtrip_verified=True)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    TEXT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    baseline_path = ROOT/'results/question1_batching/solution.json'
    multi_path = ROOT/'results/question1_multiobjective/multiobjective.json'
    sensitivity_path = ROOT/'results/question1_reserve_sensitivity/sensitivity.json'
    candidate_path = ROOT/'results/question1_reserve_sensitivity/all_capacity_feasible_batches.json'
    baseline,multi,sensitivity,pools = map(load,(baseline_path,multi_path,sensitivity_path,candidate_path))
    assert baseline['dem_metadata']['distance_crs'] == 'WGS84_local_ellipsoidal_plane_O01'
    assert multi['baseline_solution_sha256'] == sensitivity['baseline_solution_sha256'] == sha256(baseline_path)
    originals = []
    for key, source in baseline['sources'].items():
        path = ROOT/source['path']
        assert sha256(path) == source['sha256'], f'原始文件变化：{path}'
        originals.append(dict(key=key, path=source['path'], sha256=source['sha256'], verified=True))
    coordinates = baseline['dem_metadata']['local_plane']['node_coordinates']
    nodes = [dict(node, **coordinates[sid], operation_altitude_m=node['elevation_m']+(0 if sid=='O01' else 30))
             for sid,node in baseline['nodes'].items()]
    specs = [
        ('nodes',nodes,'solution.json:nodes, dem_metadata.local_plane.node_coordinates','保留16节点原经纬度与地面海拔；添加正式局部平面坐标和作业海拔'),
        ('drones',list(baseline['drones'].values()),'solution.json:drones','3机型原始物理参数；升降、准备、装载与交接参数全保留'),
        ('boxes',baseline['boxes'],'solution.json:boxes','80个不可拆分真实货箱；原始时限与优先级字段保留，但第一问未使用这些调度约束'),
        ('routes',list(baseline['routes'].values()),'solution.json:routes','15条O01单服务区航线，保留全部相交像元及峰值像元JSON数组'),
        ('batches',baseline['batches'],'solution.json:batches','18架次推荐方案，逐架次保留所有原始数值及箱号数组'),
        ('service_summary',baseline['service_summary'],'solution.json:service_summary','15区组批汇总与各机型安全载荷'),
        ('pareto',multi['frontier'],'multiobjective.json:frontier','2个完整非支配目标点，完整批次嵌入JSON列'),
        ('reserve_intervals',sensitivity['interval_plans'],'sensitivity.json:interval_plans','19段字典序最优方案；区间首段闭下界、后续开下界，均闭上界'),
        ('reserve_transitions',sensitivity['transitions'],'sensitivity.json:transitions','18次方案切换，保留服务区变化明细JSON列'),
        ('payload_limits',sensitivity['payloads'],'sensitivity.json:payloads','15服务区×3机型=45行，额定满载和空载余量极限及指定样本'),
        ('payload_curves',sensitivity['payload_curves'],'sensitivity.json:payload_curves','45组×0..60整数百分数=2745点，不可行载荷写null'),
        ('all_candidates',[p for pool in pools.values() for p in pool],'all_capacity_feasible_batches.json','742个满足质量与体积限制的非空候选；每个附能量余量失效阈值'),
        ('single_box_limits',sensitivity['single_box_limits'],'sensitivity.json:single_box_limits','53个服务区物资类别的单箱可行上限，用于判定全任务可行余量上限'),
        ('reserve_scenarios',sensitivity['scenarios'],'sensitivity.json:scenarios',f"{len(sensitivity['scenarios'])}个正式报告情景；不可行的全任务目标值与载荷按正式JSON保留null"),
        ('batch_box_assignments',[dict(batch_id=b['batch_id'],service_id=b['service_id'],model=b['model'],box_id=bid) for b in baseline['batches'] for bid in b['box_ids']],
         'solution.json:batches[].box_ids','将推荐方案箱号数组展开为80行，一箱一行'),
    ]
    tables = [export_table(*spec) for spec in specs]
    assert {x['file'].split('/')[-1]:x['row_count'] for x in tables}['payload_curves.csv'] == 2745
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), scope='第一问三个小问；局部椭球平面当前正式版本',
                    encoding='UTF-8 with BOM; RFC4180 CSV; float repr round-trip; nested structures are JSON; missing/null is literal null',
                    baseline_sha256=sha256(baseline_path), original_sources=originals,
                    formal_results=[dict(path=str(path.relative_to(ROOT)),sha256=sha256(path)) for path in (baseline_path,multi_path,sensitivity_path,candidate_path)],
                    coordinate_model=baseline['dem_metadata']['local_plane'], dem_metadata=baseline['dem_metadata'],
                    gravity_m_s2=baseline['physical_parameters']['gravity_m_s2'],
                    numeric_tolerances=baseline['numeric_tolerances'], tables=tables,
                    numerical_precision_note='小数保持计算结果，不代表坐标、DEM、耗电模型具有同等实测精度。',
                    scope_note='未将多机资源、充电排程、首批与配送时限纳入第一问；全程往返单点，返程空载。')
    (RESULTS/'data_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    dictionary_rows = [dict(table=t['file'],**column) for t in tables for column in t['columns']]
    with (DATA/'data_dictionary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['table','field','unit','type','description']);writer.writeheader();writer.writerows(dictionary_rows)
    lines = ['# 第一问数据来源与处理说明','',
             '本数据包对应当前正式的局部椭球平面版本，不改变原始附件或正式求解结果。数据保留完整数值；显示位数不代表实测精度。', '',
             '## 原始来源', '', '|来源|文件|SHA256|', '|---|---|---|']
    for item in originals:
        lines.append(f"|{item['key']}|{item['path']}|`{item['sha256']}`|")
    lines += ['', '节点地面高程取题给节点表；沿航线最高地形取原始30米DEM像元。两种高程来源分别使用，不以DEM替换节点表。', '',
              '## 坐标与航线', '',
              'WGS84局部椭球平面以O01为原点，固定原点纬度的卯酉圈曲率半径N0与子午圈曲率半径M0。x=N0 cos(phi0)(lambda-lambda0)，y=M0(phi-phi0)，角度先转弧度；x向东、y向北，单位米。', '',
              '航线是局部平面内O01到服务区的水平直线；因为变换系数固定，在经纬度栅格中同样为直线。DEM为WGS84 PixelIsPoint，原始tiepoint为像元中心，完整相交像元采用闭边界规则，接触边缘或角点的像元均纳入。巡航海拔=相交原像元最高海拔+50m。O01作业海拔为原表地面，服务区为地面+30m。', '',
              f"采用g={baseline['physical_parameters']['gravity_m_s2']}m/s²；这是统一后的计算设定，不是题给强制值。能耗、时间、体积等单位见字段字典。货箱不拆分，单架次仅配送一个服务区，卸货后空载返航。第一问累计作业时间是各架次准备、装载、飞行与交接之和，不是多机并行完工时间。", '',
              '## 导出表清单', '', '|CSV|行数|来源对象与处理|', '|---|---:|---|']
    for table in tables:
        lines.append(f"|{Path(table['file']).name}|{table['row_count']}|{table['source']}；{table['processing']}|")
    lines += ['', '## 读取约定', '',
              '- CSV使用UTF-8 BOM，英文列名保持正式JSON字段命名；每列单位、类型与说明见数据目录中的data_dictionary.csv。',
              '- 所有浮点数采用可往返恢复的字符串形式，未主动四舍五入。读取脚本已逐单元格核对回读值。',
              '- 数组和字典列是标准JSON字符串，例如box_ids、crossed_cells、batches、changes；需要进一步处理时使用json.loads。',
              '- 空值使用字面量null，表示数学模型下不可行或无值；不得将空载不可行误当成安全载荷0kg。',
              '- reserve_percent等百分数字段的20表示20%，不是0.2；阈值与边界遵循正式结果的浮点比较容差。',
              '- 题给首批、时限与优先级字段为可追溯性保留，第一问没有用这些字段实施物理机队调度。', '',
              '## 图形数据', '',
              'F01采用真实DEM裁剪作为背景与正式15条航线；背景渲染不改变地形计算。F02将每个相交闭像元沿线段的参数区间与原像元高程导出，构造分段常值保守上包络，不进行双线性插值或地形平滑。图形数据与处理详情由geography_figures.json单独记录。', '',
              '数据及正式结果SHA256、坐标常数、数值容差、字段说明和回读验证状态统一记录在第一问/结果/data_manifest.json。']
    (TEXT/'数据来源与处理说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({Path(t['file']).name:t['row_count'] for t in tables},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
