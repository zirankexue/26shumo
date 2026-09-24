/** Export verified reserve-ratio sensitivity results and native Excel charts. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inputPath = path.resolve(process.argv[2] || path.join(projectDir, 'results/question1_reserve_sensitivity/sensitivity.json'));
const outputPath = path.resolve(process.argv[3] || path.join(projectDir, 'outputs/question1_reserve_sensitivity/第一问_安全余量敏感性.xlsx'));
const outputDir = path.dirname(outputPath);
const FONT = 'Microsoft YaHei';
const COLORS = { dark: '#183650', text: '#243746', pale: '#EEF3F7', total: '#DCE7EF', error: '#FCE7E7', A: '#4378A8', B: '#DA8540', C: '#3E8C78' };
const EPSILON = 1e-7;
const assert = (condition, message) => { if (!condition) throw new Error(message); };
function near(actual, expected, label) {
  assert(Number.isFinite(actual) && Number.isFinite(expected), `${label}: non-finite number`);
  assert(Math.abs(actual - expected) <= EPSILON * Math.max(1, Math.abs(expected)), `${label}: ${actual} != ${expected}`);
}
function baseStyle(sheet, range, columnWidths) {
  sheet.showGridLines = false;
  const cells = sheet.getRange(range);
  cells.format.font = { name: FONT, size: 10, color: COLORS.text };
  cells.format.rowHeight = 27;
  cells.format.verticalAlignment = 'center';
  for (const [column, width] of Object.entries(columnWidths)) sheet.getRange(`${column}:${column}`).format.columnWidthPx = width;
}
function headerStyle(sheet, range) {
  const cells = sheet.getRange(range);
  cells.format.fill = COLORS.dark;
  cells.format.font = { name: FONT, size: 10, color: '#FFFFFF', bold: true };
  cells.format.rowHeight = 44;
  cells.format.wrapText = true;
  cells.format.horizontalAlignment = 'center';
  cells.format.borders = { insideVertical: { style: 'thin', color: '#FFFFFF' } };
}
function stripeRows(sheet, firstRow, lastRow, lastColumn) {
  for (let row = firstRow; row <= lastRow; row++) if ((row - firstRow) % 2 === 1) sheet.getRange(`A${row}:${lastColumn}${row}`).format.fill = COLORS.pale;
}
function title(sheet, address, value) {
  const cell = sheet.getRange(address);
  cell.values = [[value]];
  cell.format.font = { name: FONT, size: 14, color: COLORS.dark, bold: true };
  cell.format.rowHeight = 32;
}
function note(sheet, address, value) {
  const cell = sheet.getRange(address);
  cell.values = [[value]];
  cell.format.font = { name: FONT, size: 10, color: '#526574' };
  cell.format.rowHeight = 28;
}
function nativeChart(sheet, type, ranges, chartTitle, start, end, numberFormat, colors) {
  const chart = sheet.charts.add(type === 'bar' ? 'ColumnClustered' : type, ranges.length === 1 ? sheet.getRange(ranges[0]) : ranges.map(range => sheet.getRange(range)));
  chart.setPosition(start, end);
  chart.title = chartTitle;
  chart.titleTextStyle.fontSize = 14;
  chart.titleTextStyle.typeface = FONT;
  chart.hasLegend = colors.length > 1;
  chart.legend = { position: 'top', textStyle: { typeface: FONT, fontSize: 11 } };
  chart.xAxis = { axisType: 'textAxis', numberFormatCode: '0.######"%"', numberFormatSourceLinked: false, textStyle: { typeface: FONT, fontSize: 11 } };
  chart.yAxis = { numberFormatCode: numberFormat, numberFormatSourceLinked: false, textStyle: { typeface: FONT, fontSize: 11 } };
  chart.xAxis.title.text = '返航安全余量 rho（%）';
  chart.series.items.forEach((series, index) => {
    const color = colors[index % colors.length];
    series.fill = color;
    series.line = { fill: color, style: 'solid', width: 2 };
  });
  return chart;
}

function validateScenario(scenario, sources) {
  if (!scenario.feasible) {
    assert(!scenario.batches || scenario.batches.length === 0, `${scenario.scenario_id}: infeasible scenario contains assigned batches`);
    assert(scenario.reason, `${scenario.scenario_id}: infeasibility reason missing`);
    return;
  }
  const boxMap = new Map(sources.boxes.map(box => [box.id, box]));
  assert(boxMap.size === sources.boxes.length, 'Duplicate source box identifiers');
  const visited = new Set(), batchIds = new Set();
  const total = { flight_count: scenario.batches.length, box_count: 0, mass_kg: 0, volume_m3: 0, energy_kwh: 0, flight_time_s: 0, work_time_s: 0 };
  for (const batch of scenario.batches) {
    assert(!batchIds.has(batch.batch_id), `${scenario.scenario_id}: duplicate batch ${batch.batch_id}`);
    batchIds.add(batch.batch_id);
    const drone = sources.drones[batch.model];
    assert(drone, `${scenario.scenario_id}: unknown drone model`);
    let mass = 0, volume = 0;
    const counts = sources.type_order.map(() => 0);
    for (const id of batch.box_ids) {
      const box = boxMap.get(id);
      assert(box && box.service_id === batch.service_id, `${scenario.scenario_id}: unknown or cross-area box ${id}`);
      assert(!visited.has(id), `${scenario.scenario_id}: repeated box ${id}`);
      visited.add(id);
      mass += box.mass_kg;
      volume += box.volume_m3;
      const typeIndex = sources.type_order.indexOf(box.type);
      assert(typeIndex >= 0, `${scenario.scenario_id}: unknown box type ${box.type}`);
      counts[typeIndex]++;
    }
    assert(JSON.stringify(counts) === JSON.stringify(batch.counts), `${scenario.scenario_id}: mismatched type counts`);
    near(batch.mass_kg, mass, 'Batch mass');
    near(batch.volume_m3, volume, 'Batch volume');
    near(batch.box_count, batch.box_ids.length, 'Batch count');
    near(batch.reserve_percent, scenario.rho_percent, 'Scenario reserve');
    near(batch.energy_kwh, batch.horizontal_out_kwh + batch.horizontal_return_kwh + batch.climb_out_kwh + batch.climb_return_kwh, 'Batch energy components');
    near(batch.return_soc_percent, 100 * (1 - batch.energy_kwh / drone.energy_kwh), 'Batch SOC');
    near(batch.work_time_s, batch.flight_time_s + drone.prepare_s + batch.box_count * drone.load_per_box_s + drone.handover_base_s + batch.box_count * drone.handover_per_box_s, 'Batch cumulative work');
    assert(mass <= drone.payload_kg + EPSILON && volume <= drone.volume_m3 + EPSILON && batch.energy_kwh <= drone.energy_kwh * (1 - scenario.rho_percent / 100) + EPSILON, `${scenario.scenario_id}: unsafe batch ${batch.batch_id}`);
    for (const key of Object.keys(total)) if (key !== 'flight_count') total[key] += batch[key];
  }
  assert(visited.size === boxMap.size, `${scenario.scenario_id}: missing boxes`);
  for (const key of Object.keys(total)) near(total[key], scenario.totals[key], `${scenario.scenario_id} total ${key}`);
  near(Math.min(...scenario.batches.map(batch => batch.return_soc_percent)), scenario.totals.minimum_soc_percent, `${scenario.scenario_id} minimum SOC`);
  return total;
}

const data = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const previous = JSON.parse(await fs.readFile(path.join(projectDir, 'results/question1_batching/solution.json'), 'utf8'));
assert(Number.isFinite(previous.physical_parameters?.gravity_m_s2), 'Missing source gravity');
const reasonFor = scenario => (scenario.blocked_demands || []).map(item => `${item.service_id}-${item.type}`).join('；');
const scenarios = data.scenarios.map(item => ({ ...item, scenario_id: item.plan_id, rho_percent: item.reserve_percent, totals: item, batches: item.batches || [], reason: reasonFor(item) }));
assert(scenarios.length > 0 && data.interval_plans.length > 0, 'Missing sensitivity scenarios or intervals');
assert(new Set(scenarios.map(item => item.plan_id)).size === scenarios.length, 'Duplicate scenario IDs');
for (const scenario of scenarios) validateScenario(scenario, data);
for (const interval of data.interval_plans) validateScenario({ ...interval, scenario_id: interval.plan_id, rho_percent: interval.reserve_percent, totals: interval }, data);
for (const key of ['flight_count', 'energy_kwh', 'work_time_s', 'box_count', 'mass_kg', 'volume_m3']) near(data.baseline[key], previous.totals[key], `Previous baseline ${key}`);
assert(data.payloads.length === 45, 'Expected all 15 service areas and 3 drone types');
for (const row of data.payloads) {
  near(row.rated_payload_kg, data.drones[row.model].payload_kg, 'Rated payload');
  let previousPayload = Infinity;
  for (const sample of [...row.samples].sort((a, b) => a.reserve_percent - b.reserve_percent)) {
    if (sample.safe_payload_kg === null) continue;
    assert(sample.safe_payload_kg >= -EPSILON && sample.safe_payload_kg <= row.rated_payload_kg + EPSILON, 'Invalid continuous safe payload');
    assert(sample.safe_payload_kg <= previousPayload + EPSILON, 'Safe payload must be nonincreasing');
    previousPayload = sample.safe_payload_kg;
  }
}
for (let index = 0; index < data.interval_plans.length; index++) {
  const interval = data.interval_plans[index];
  assert(interval.upper_percent > interval.lower_percent && interval.upper_inclusive, 'Invalid reserve interval');
  if (index > 0) near(interval.lower_percent, data.interval_plans[index - 1].upper_percent, 'Contiguous intervals');
}
near(data.interval_plans.at(-1).upper_percent, data.global_feasibility_limit.reserve_percent, 'Global reserve upper limit');

const workbook = Workbook.create();
const compare = workbook.worksheets.add('情景比较');
const payload = workbook.worksheets.add('最大安全载荷');
const critical = workbook.worksheets.add('组批变化临界点');
const detail = workbook.worksheets.add('情景批次');
const notes = workbook.worksheets.add('口径说明');
const feasibleScenarios = scenarios.filter(item => item.feasible);
const batchRows = feasibleScenarios.flatMap(scenario => scenario.batches.map(batch => ({ scenario, batch })));
const firstDetailRow = 5, lastDetailRow = firstDetailRow + batchRows.length - 1;
const parameterRange = "'口径说明'!$A$6:$E$8";
const detailFull = column => `'情景批次'!$${column}$${firstDetailRow}:$${column}$${lastDetailRow}`;
const modelCounts = value => ['A','B','C'].map(model => value[model] || 0).join(' / ');

// A compact source/assumption panel also owns the parameters used by safety formulas.
baseStyle(notes, 'A1:N38', { A: 145, B: 155, C: 155, D: 170, E: 175, F: 140, G: 140, H: 140, I: 140, J: 140, K: 140, L: 140, M: 140, N: 140 });
title(notes, 'A1', '第一问安全余量敏感性：口径与参数');
notes.getRange('A5:E5').values = [['机型', '载荷上限（kg）', '体积上限（m³）', '电池可用能量（kWh）', '含电池空载质量（kg）']];
notes.getRange('A6:E8').values = ['A','B','C'].map(model => { const drone = data.drones[model]; return [model, drone.payload_kg, drone.volume_m3, drone.energy_kwh, drone.empty_mass_kg]; });
headerStyle(notes, 'A5:E5');
notes.getRange('C6:C8').setNumberFormat('0.000');
const noteLines = [
  '统一安全余量rho指各机型返航时最低SOC；本分析只改变rho，机型参数、地形航线、货箱需求与能耗系数保持一致。',
  '每个rho下按“先最少架次数，再最低总能耗，再最短累计作业时间”重新枚举并求解，不沿用20%下的可行候选集。',
  '能量约束为 Etrip ≤ Euse×(1-rho)。Euse取原表电池可用能量，水平能耗系数不会再乘一次(1-rho)。',
  `沿用补充能耗假设：Ehor=Euse×d/L(q)，Eup=(m0+q)×${previous.physical_parameters.gravity_m_s2}×h/(η×3.6×10^6)。下降不另计附加能耗，m0已含电池。`,
  '航线采用以O01为原点的WGS84局部椭球平面；货箱不可拆分、去程带货、返程空载，每架次只服务一个区。',
  '累计作业时间为所有架次准备、装载、飞行及交接时长之和，不等于并行机队的最晚完工时刻。',
  '连续最大安全载荷qmax只考虑额定载荷与能量；实际组批还受体积和货箱不可拆分约束。空白qmax表示空载往返也不可行，不能替换为0。',
  '情景图采用分类柱状图比较可行方案，横轴为所列情景，不将非均匀rho当成连续等距刻度；精确区间见“组批变化临界点”。',
  'qmax曲线采用0%到60%、每1个百分点的均匀网格；曲线在空载也不可行时终止。表内同时保留45个服务区—机型组合。',
  `精确组批第一段为[0,${data.baseline_plan_valid_up_to_percent.toFixed(9)}%]，其余均为左开右闭；等于临界值时旧方案仍可行。`,
  `全任务统一安全余量上限为${data.global_feasibility_limit.reserve_percent.toFixed(12)}%，等号可行；超过该值，S008饮用水箱无法由任何机型单独运送。`,
  'MED/WAT/FOD/HYG分别为医疗、饮用水、食品、卫生物资。临界变化表使用“机型[四类箱数]”，顺序为MED/WAT/FOD/HYG。',
  '表格保留真实箱号；每个情景分别执行一次完整任务，不同情景的相同箱号为备选方案。编辑工作簿不会自动重新求解。',
  `含5%—45%每5个百分点的9档常规情景；qmax二分精度为${previous.numeric_tolerances.payload_bisection_kg} kg，逐候选整数费用累加比较。`,
  '来源：results/question1_reserve_sensitivity/sensitivity.json；20%基准来自results/question1_batching/solution.json。',
];
noteLines.forEach((text, index) => note(notes, `A${11 + index}`, text));
title(notes, 'A27', '全任务可行性上限的限制货箱');
notes.getRange('A29:F29').values = [['服务区', '货物类别', '真实货箱编号', '单箱质量（kg）', '最佳机型', '最高余量（%）']];
headerStyle(notes, 'A29:F29');
data.global_feasibility_limit.witnesses.forEach((witness, index) => {
  const row = 30 + index;
  const ids = data.boxes.filter(box => box.service_id === witness.service_id && box.type === witness.type).map(box => box.id);
  notes.getRange(`A${row}:F${row}`).values = [[witness.service_id, witness.type, ids.join('; '), witness.mass_kg, witness.best_models.join('/'), witness.maximum_reserve_percent]];
  notes.getRange(`C${row}`).format.wrapText = true;
  notes.getRange(`A${row}:F${row}`).format.rowHeight = 48;
  notes.getRange(`F${row}`).setNumberFormat('0.000000000');
});

// Detailed batches retain all real identifiers and use the scenario-specific reserve.
baseStyle(detail, `A1:P${lastDetailRow}`, { A: 90, B: 110, C: 112, D: 88, E: 68, F: 448, G: 105, H: 106, I: 120, J: 128, K: 134, L: 120, M: 118, N: 115, O: 128, P: 94 });
title(detail, 'A1', '典型可行情景的完整组批');
note(detail, 'A2', '只列可行情景；不同情景独立覆盖全部80箱。准备、装载与交接已计入累计作业时间。');
detail.getRange('A4:P4').values = [['情景ID', '安全余量（%）', '架次编号', '服务区', '机型', '货箱编号列表', '质量（kg）', '体积（m³）', '纯飞行（s）', '能耗（kWh）', '累计作业（s）', '返航SOC（%）', '载荷余量（kg）', '体积余量（m³）', '能量余量（kWh）', '约束检验']];
detail.getRange(`A${firstDetailRow}:P${lastDetailRow}`).values = batchRows.map(({ scenario, batch }) => [scenario.plan_id, scenario.reserve_percent, batch.batch_id, batch.service_id, batch.model, batch.box_ids.join('; '), batch.mass_kg, batch.volume_m3, batch.flight_time_s, batch.energy_kwh, batch.work_time_s, null, null, null, null, null]);
headerStyle(detail, 'A4:P4');
stripeRows(detail, firstDetailRow, lastDetailRow, 'P');
batchRows.forEach(({ batch }, index) => {
  const row = firstDetailRow + index;
  const lookup = column => `VLOOKUP(E${row},${parameterRange},${column},FALSE)`;
  detail.getRange(`L${row}:P${row}`).formulas = [[`=(1-J${row}/${lookup(4)})*100`, `=${lookup(2)}-G${row}`, `=${lookup(3)}-H${row}`, `=${lookup(4)}*(1-B${row}/100)-J${row}`, `=IF(AND(M${row}>=-0.0000001,N${row}>=-0.0000001,O${row}>=-0.0000001),"满足","不满足")`]];
  detail.getRange(`A${row}:P${row}`).format.rowHeight = Math.max(30, Math.ceil(batch.box_ids.join('; ').length / 49) * 17 + 10);
});
detail.getRange(`F${firstDetailRow}:F${lastDetailRow}`).format.wrapText = true;
detail.getRange(`B${firstDetailRow}:B${lastDetailRow}`).setNumberFormat('0.000000000');
detail.getRange(`G${firstDetailRow}:O${lastDetailRow}`).format.horizontalAlignment = 'right';
for (const column of ['G','I','K','L','M']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.00');
for (const column of ['H','N']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.000');
for (const column of ['J','O']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.000000');
detail.getRange(`P${firstDetailRow}:P${lastDetailRow}`).format.horizontalAlignment = 'center';
detail.freezePanes.freezeRows(4);
detail.freezePanes.freezeColumns(5);

// Comparison sources are formula-backed summaries of the real scenario batch rows.
const firstCompareRow = 6, lastCompareRow = firstCompareRow + scenarios.length - 1;
baseStyle(compare, 'A1:S46', { A: 91, B: 137, C: 82, D: 81, E: 138, F: 149, G: 138, H: 136, I: 133, J: 106, K: 138, L: 149, M: 128, N: 388, O: 30, P: 135, Q: 110, R: 140, S: 145 });
title(compare, 'A1', '第一问：安全余量敏感性');
note(compare, 'A2', `20%为原基准；统一安全余量可行上限为${data.global_feasibility_limit.reserve_percent.toFixed(9)}%，等号可行。`);
note(compare, 'A3', '柱状图只比较所列可行情景；不可行方案的目标保持空白。横轴为分类情景，精确变化区间见临界点表。');
compare.getRange('A5:N5').values = [['情景ID', '安全余量（%）', '可行性', '最少架次', '总能耗（kWh）', '累计作业（s）', '累计作业（h）', 'A/B/C型架次数', '最低SOC（%）', '较20%增减架次', '较20%能耗差（kWh）', '较20%作业差（s）', '区间方案ID', '不可单独运输的服务区—货物类别']];
headerStyle(compare, 'A5:N5');
scenarios.forEach((scenario, index) => {
  const row = firstCompareRow + index;
  compare.getRange(`A${row}:N${row}`).values = [[scenario.plan_id, scenario.reserve_percent, scenario.feasible ? '可行' : '不可行', null, null, null, null, scenario.feasible ? modelCounts(scenario.model_counts) : null, null, null, null, null, scenario.interval_plan_id || null, scenario.reason || null]];
  if (scenario.feasible) {
    const selected = batchRows.map((entry, i) => entry.scenario.plan_id === scenario.plan_id ? firstDetailRow + i : null).filter(value => value !== null);
    compare.getRange(`D${row}:G${row}`).formulas = [[`=COUNTIFS(${detailFull('A')},A${row})`, `=SUMIFS(${detailFull('J')},${detailFull('A')},A${row})`, `=SUMIFS(${detailFull('K')},${detailFull('A')},A${row})`, `=F${row}/3600`]];
    compare.getRange(`I${row}`).formulas = [[`=MIN(${selected.map(detailRow => `'情景批次'!L${detailRow}`).join(',')})`]];
  }
  compare.getRange(`A${row}:N${row}`).format.rowHeight = scenario.feasible ? 31 : Math.max(44, Math.ceil(scenario.reason.length / 39) * 16 + 8);
  if (!scenario.feasible) compare.getRange(`C${row}`).format.fill = COLORS.error;
});
const baselineRow = firstCompareRow + scenarios.findIndex(item => Math.abs(item.reserve_percent - data.baseline_reserve_percent) < EPSILON);
assert(baselineRow >= firstCompareRow, 'Missing baseline scenario');
scenarios.forEach((scenario,index) => { if (scenario.feasible) { const row = firstCompareRow + index; compare.getRange(`J${row}:L${row}`).formulas = [[`=D${row}-$D$${baselineRow}`, `=E${row}-$E$${baselineRow}`, `=F${row}-$F$${baselineRow}`]]; } });
stripeRows(compare, firstCompareRow, lastCompareRow, 'N');
compare.getRange(`N${firstCompareRow}:N${lastCompareRow}`).format.wrapText = true;
compare.getRange(`B${firstCompareRow}:B${lastCompareRow}`).setNumberFormat('0.000000000');
compare.getRange(`D${firstCompareRow}:L${lastCompareRow}`).format.horizontalAlignment = 'right';
compare.getRange(`C${firstCompareRow}:C${lastCompareRow}`).format.horizontalAlignment = 'center';
compare.getRange(`M${firstCompareRow}:M${lastCompareRow}`).format.horizontalAlignment = 'center';
for (const column of ['E','G','K']) compare.getRange(`${column}${firstCompareRow}:${column}${lastCompareRow}`).setNumberFormat('0.000000');
for (const column of ['F','I','L']) compare.getRange(`${column}${firstCompareRow}:${column}${lastCompareRow}`).setNumberFormat('0.00');
compare.getRange(`A${baselineRow}:N${baselineRow}`).format.fill = COLORS.total;
compare.getRange('P5:S5').values = [['情景rho', '架次数', '能耗（kWh）', '累计作业（h）']];
headerStyle(compare, 'P5:S5');
feasibleScenarios.forEach((scenario,index) => {
  const row = 6 + index, source = firstCompareRow + scenarios.findIndex(item => item.plan_id === scenario.plan_id);
  compare.getRange(`P${row}:S${row}`).formulas = [[`=IF(B${source}=INT(B${source}),TEXT(B${source},"0"),TEXT(B${source},"0.######"))&"%"`, `=D${source}`, `=E${source}`, `=G${source}`]];
});
const helperEnd = 5 + feasibleScenarios.length;
const compareCharts = [
  nativeChart(compare, 'bar', ['P5:P'+helperEnd, 'Q5:Q'+helperEnd], '典型可行情景：架次数', 'A25','E40','0',[COLORS.A]),
  nativeChart(compare, 'bar', ['P5:P'+helperEnd, 'R5:R'+helperEnd], '典型可行情景：能耗（kWh）', 'F25','J40','0.0',[COLORS.B]),
  nativeChart(compare, 'bar', ['P5:P'+helperEnd, 'S5:S'+helperEnd], '典型可行情景：累计作业（h）', 'K25','N40','0.0',[COLORS.C]),
];
compareCharts.forEach(chart => { chart.xAxis.title.text = '典型情景（rho见表）'; chart.xAxis = { tickLabelInterval: 2 }; });
note(compare, 'A42', '柱状图按情景分类比较；真实最优组批在临界值处离散改变，精确范围与端点规则见临界区间表。');
compare.freezePanes.freezeRows(5);
compare.tabColor = COLORS.dark;

// Forty-five safe-payload combinations plus the complete 1 percentage-point curve grid.
const samplePercentages = data.payload_sample_percentages;
assert(samplePercentages.length > 0 && new Set(samplePercentages).size === samplePercentages.length, 'Missing or duplicate safe-payload sample percentages');
const columnName = index => { let name = ''; for (; index > 0; index = Math.floor((index - 1) / 26)) name = String.fromCharCode(65 + (index - 1) % 26) + name; return name; };
const payloadLastColumn = columnName(5 + samplePercentages.length);
const sampleWidths = Object.fromEntries(samplePercentages.map((_, index) => [columnName(index + 6), 100]));
baseStyle(payload, `A1:${columnName(Math.max(19, 5 + samplePercentages.length))}1010`, { A: 92, B: 69, C: 104, D: 150, E: 150, R: 100, S: 100, ...sampleWidths });
title(payload, 'A1', '45个服务区—机型组合的最大安全载荷');
note(payload, 'A2', 'qmax单位为kg；空白表示空载往返不可行。连续质量上限还需结合体积和不可拆箱约束使用。');
payload.getRange(`A4:${payloadLastColumn}4`).values = [['服务区','机型','额定载荷（kg）','满载可行余量上限（%）','空载可行余量上限（%）', ...samplePercentages.map(value => `rho=${value}%`)]];
payload.getRange(`A5:${payloadLastColumn}49`).values = data.payloads.map(item => [item.service_id, item.model, item.rated_payload_kg, item.full_load_maximum_reserve_percent, item.empty_roundtrip_maximum_reserve_percent, ...samplePercentages.map(percent => { const sample = item.samples.find(value => value.reserve_percent === percent); assert(sample, 'Missing safe-payload sample'); return sample.safe_payload_kg; })]);
headerStyle(payload, `A4:${payloadLastColumn}4`);
stripeRows(payload,5,49,payloadLastColumn);
payload.getRange('D5:E49').setNumberFormat('0.000000000');
payload.getRange(`F5:${payloadLastColumn}49`).setNumberFormat('0.000');
payload.getRange(`C5:${payloadLastColumn}49`).format.horizontalAlignment = 'right';
const curveStart = 76;
payload.getRange(`A${curveStart-1}:F${curveStart-1}`).values = [['服务区','余量（%）','A型qmax（kg）','B型qmax（kg）','C型qmax（kg）','限制说明']];
headerStyle(payload,`A${curveStart-1}:F${curveStart-1}`);
const curveGroups = new Map();
for (const point of data.payload_curves) {
  const key = `${point.service_id}|${point.reserve_percent}`;
  if (!curveGroups.has(key)) curveGroups.set(key,{service_id:point.service_id,reserve_percent:point.reserve_percent,models:{}});
  curveGroups.get(key).models[point.model] = point;
}
const curveRows = [...curveGroups.values()].sort((a,b) => a.service_id.localeCompare(b.service_id) || a.reserve_percent-b.reserve_percent);
const curveEnd = curveStart + curveRows.length - 1;
payload.getRange(`A${curveStart}:F${curveEnd}`).values = curveRows.map(row => [row.service_id,row.reserve_percent,...['A','B','C'].map(model => { assert(row.models[model], 'Incomplete curve model grid'); return row.models[model].safe_payload_kg; }), ['A','B','C'].filter(model=>row.models[model].safe_payload_kg===null).map(model=>`${model}型空载也不可行`).join('；')]);
payload.getRange(`C${curveStart}:E${curveEnd}`).setNumberFormat('0.000000');
payload.getRange(`F${curveStart}:F${curveEnd}`).format.wrapText = true;
const payloadCharts = [];
for (const [service,start,end] of [['S001','A53','E71'],['S004','F53','J71'],['S008','K53','O71']]) {
  const startRow = curveStart + curveRows.findIndex(row=>row.service_id===service);
  const endRow = startRow + curveRows.filter(row=>row.service_id===service).length-1;
  const helperColumn = {S001:'H',S004:'L',S008:'P'}[service];
  const columnNumber = helperColumn.charCodeAt(0)-65;
  payload.getRangeByIndexes(curveStart-2,columnNumber,1,4).values = [['余量','A型','B型','C型']];
  for(let row=startRow;row<=endRow;row++) {
    const targetRow=curveStart+(row-startRow);
    payload.getRangeByIndexes(targetRow-1,columnNumber,1,4).formulas = [[`=TEXT(B${row},"0")&"%"`,...['C','D','E'].map(column=>`=IF(${column}${row}="","",${column}${row})`)]];
  }
  const lastColumn=String.fromCharCode(helperColumn.charCodeAt(0)+3);
  const chart=nativeChart(payload,'line',[`${helperColumn}${curveStart-1}:${lastColumn}${curveStart+60}`],`${service}：安全载荷随余量变化（kg）`,start,end,'0',[COLORS.A,COLORS.B,COLORS.C]);
  chart.xAxis={tickLabelInterval:10};
  payloadCharts.push(chart);
}
note(payload,'A73','下表保留0%—60%每1个百分点的完整915行载荷曲线数值；右侧为三个图表的公式引用区。');
payload.freezePanes.freezeRows(4);

// Exact intervals preserve enough decimals to distinguish the final narrow interval.
const intervalFirst=5, intervalLast=intervalFirst+data.interval_plans.length-1;
const transitionHeader=intervalLast+6, transitionFirst=transitionHeader+1, transitionLast=transitionFirst+data.transitions.length-1;
baseStyle(critical,`A1:M${transitionLast+3}`,{A:140,B:155,C:155,D:150,E:106,F:145,G:160,H:130,I:137,J:150,K:95,L:135,M:155});
title(critical,'A1','最优组批的精确安全余量区间');
note(critical,'A2','临界值等号属于左侧旧方案；第一段左端含0，其余左开右闭。临界余量保留9位小数。');
critical.getRange('A4:J4').values=[['方案ID','余量下界（%）','余量上界（%）','端点规则','架次数','总能耗（kWh）','累计作业（s）','累计作业（h）','A/B/C型架次数','最低SOC（%）']];
critical.getRange(`A${intervalFirst}:J${intervalLast}`).values=data.interval_plans.map(plan=>[plan.plan_id,plan.lower_percent,plan.upper_percent,plan.lower_inclusive?'[下界,上界]':'(下界,上界]',plan.flight_count,plan.energy_kwh,plan.work_time_s,null,modelCounts(plan.model_counts),plan.minimum_soc_percent]);
for(let row=intervalFirst;row<=intervalLast;row++)critical.getRange(`H${row}`).formulas=[[`=G${row}/3600`]];
headerStyle(critical,'A4:J4');
stripeRows(critical,intervalFirst,intervalLast,'J');
critical.getRange(`B${intervalFirst}:C${intervalLast}`).setNumberFormat('0.000000000');
critical.getRange(`F${intervalFirst}:F${intervalLast}`).setNumberFormat('0.000000');
critical.getRange(`G${intervalFirst}:G${intervalLast}`).setNumberFormat('0.00');
critical.getRange(`H${intervalFirst}:H${intervalLast}`).setNumberFormat('0.000000');
critical.getRange(`J${intervalFirst}:J${intervalLast}`).setNumberFormat('0.000000000');
note(critical,`A${intervalLast+2}`,`rho > ${data.global_feasibility_limit.reserve_percent.toFixed(12)}%时全任务不可行；因此不填写架次数、能耗或时间。`);
title(critical,`A${transitionHeader-2}`,'跨越临界值后具体改变的组批');
critical.mergeCells(`E${transitionHeader}:G${transitionHeader}`);
critical.mergeCells(`H${transitionHeader}:J${transitionHeader}`);
critical.getRange(`A${transitionHeader}:D${transitionHeader}`).values=[['临界余量（%）','等号处方案','刚超过后方案','改变服务区']];
critical.getRange(`E${transitionHeader}`).values=[['原组批：机型[MED/WAT/FOD/HYG]']];
critical.getRange(`H${transitionHeader}`).values=[['新组批：机型[MED/WAT/FOD/HYG]']];
critical.getRange(`K${transitionHeader}:M${transitionHeader}`).values=[['架次数差','能耗差（kWh）','作业时间差（s）']];
headerStyle(critical,`A${transitionHeader}:M${transitionHeader}`);
const batchLabel=batch=>`${batch.model}[${batch.counts.join('/')}]`;
data.transitions.forEach((transition,index)=>{
  const row=transitionFirst+index;
  critical.mergeCells(`E${row}:G${row}`);
  critical.mergeCells(`H${row}:J${row}`);
  critical.getRange(`A${row}:D${row}`).values=[[transition.threshold_percent,transition.equality_plan_id,transition.above_plan_id,transition.changes.map(item=>item.service_id).join('；')]];
  critical.getRange(`E${row}`).values=[[transition.changes.map(item=>`${item.service_id}: ${item.old_batches.map(batchLabel).join(' + ')}`).join('\n')]];
  critical.getRange(`H${row}`).values=[[transition.changes.map(item=>`${item.service_id}: ${item.new_batches.map(batchLabel).join(' + ')}`).join('\n')]];
  const oldRow=intervalFirst+data.interval_plans.findIndex(plan=>plan.plan_id===transition.equality_plan_id);
  const newRow=intervalFirst+data.interval_plans.findIndex(plan=>plan.plan_id===transition.above_plan_id);
  critical.getRange(`K${row}:M${row}`).formulas=[[`=E${newRow}-E${oldRow}`,`=F${newRow}-F${oldRow}`,`=G${newRow}-G${oldRow}`]];
  critical.getRange(`A${row}:M${row}`).format.rowHeight=Math.max(50,transition.changes.length*28);
});
stripeRows(critical,transitionFirst,transitionLast,'M');
critical.getRange(`E${transitionFirst}:J${transitionLast}`).format.wrapText=true;
critical.getRange(`A${transitionFirst}:A${transitionLast}`).setNumberFormat('0.000000000');
critical.getRange(`L${transitionFirst}:L${transitionLast}`).setNumberFormat('0.000000');
critical.getRange(`M${transitionFirst}:M${transitionLast}`).setNumberFormat('0.00');
critical.freezePanes.freezeRows(4);

workbook.recalculate();
assert(detail.getRange(`P${firstDetailRow}:P${lastDetailRow}`).values.flat().every(value=>value==='满足'),'Workbook scenario safety check failed');
scenarios.forEach((scenario,index)=>{
  const row=firstCompareRow+index;
  if(scenario.feasible){
    for(const [column,key] of [['D','flight_count'],['E','energy_kwh'],['F','work_time_s'],['I','minimum_soc_percent']])near(compare.getRange(`${column}${row}`).values[0][0],scenario[key],`Workbook ${scenario.plan_id} ${key}`);
  }else assert(compare.getRange(`D${row}:G${row}`).values.flat().every(value=>value===null),'Infeasible objectives must remain blank');
});
const allCharts=[...compareCharts,...payloadCharts];
assert(allCharts.length===6 && allCharts.every(chart=>chart.series.items.every(series=>series.formula && series.categoryFormula)),'Chart source references missing');
const errorScan=await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},summary:'Sensitivity workbook formula errors',maxChars:2500});
console.log(errorScan.ndjson);
await fs.mkdir(outputDir,{recursive:true});
const file=await SpreadsheetFile.exportXlsx(workbook);
await file.save(outputPath);
const previews=[
  ['情景比较',`A1:N${lastCompareRow}`,'reserve_scenarios.png'],
  ['情景比较','A25:N42','reserve_scenario_charts.png'],
  ['最大安全载荷',`A1:${payloadLastColumn}19`,'reserve_payload_table.png'],
  ['最大安全载荷','A53:O73','reserve_payload_charts.png'],
  ['组批变化临界点',`A1:J${intervalLast+2}`,'reserve_intervals.png'],
  ['组批变化临界点',`A${transitionHeader-2}:M${transitionLast}`,'reserve_transitions.png'],
  ['口径说明','A1:L31','reserve_notes.png'],
];
for(const target of [scenarios.find(item=>item.reserve_percent===20),feasibleScenarios.at(-1)]){
  const start=firstDetailRow+batchRows.findIndex(item=>item.scenario.plan_id===target.plan_id);
  previews.push(['情景批次',`A${start}:P${start+target.batches.length-1}`,`reserve_${target.plan_id}_batches.png`]);
}
for(const [sheetName,range,name] of previews){
  const png=await workbook.render({sheetName,range,scale:1.2,format:'png'});
  await fs.writeFile(path.join(outputDir,name),new Uint8Array(await png.arrayBuffer()));
}
console.log(JSON.stringify({outputPath,scenarioCount:scenarios.length,feasibleScenarioCount:feasibleScenarios.length,batchCount:batchRows.length,safePayloadCombinations:data.payloads.length,curvePoints:data.payload_curves.length,intervalCount:data.interval_plans.length,transitionCount:data.transitions.length,nativeChartCount:allCharts.length,chartBindings:allCharts.map(chart=>({title:chart.title.text,series:chart.series.items.map(series=>({formula:series.formula,categoryFormula:series.categoryFormula}))})),previewFiles:previews.map(item=>item[2])},null,2));
