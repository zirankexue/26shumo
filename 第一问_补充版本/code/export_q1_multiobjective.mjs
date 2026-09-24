/** Export real Q1 Pareto results without changing the original batching workbook. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inputPath = path.resolve(process.argv[2] || path.join(projectDir, 'results/question1_multiobjective/multiobjective.json'));
const outputPath = path.resolve(process.argv[3] || path.join(projectDir, 'outputs/question1_multiobjective/第一问_多目标优化结果.xlsx'));
const outputDir = path.dirname(outputPath);
const FONT = 'Microsoft YaHei';
const COLORS = { dark: '#183650', text: '#243746', pale: '#EEF3F7', total: '#DCE7EF', good: '#1E6B50', error: '#FCE7E7' };
const EPSILON = 1e-7;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
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
  for (const [column, width] of Object.entries(columnWidths)) {
    sheet.getRange(`${column}:${column}`).format.columnWidthPx = width;
  }
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
  for (let row = firstRow; row <= lastRow; row++) {
    if ((row - firstRow) % 2 === 1) sheet.getRange(`A${row}:${lastColumn}${row}`).format.fill = COLORS.pale;
  }
}
function writeTitle(sheet, address, value) {
  const cell = sheet.getRange(address);
  cell.values = [[value]];
  cell.format.font = { name: FONT, size: 14, color: COLORS.dark, bold: true };
  cell.format.rowHeight = 32;
}
function writeNote(sheet, address, value) {
  const cell = sheet.getRange(address);
  cell.values = [[value]];
  cell.format.font = { name: FONT, size: 10, color: '#526574' };
  cell.format.rowHeight = 28;
}

function validateRepresentative(plan, sources) {
  const sourceBoxes = new Map(sources.boxes.map(box => [box.id, box]));
  assert(sourceBoxes.size === sources.boxes.length, 'Duplicate source box identifier');
  const visited = new Set();
  const batchIds = new Set();
  const total = { flight_count: plan.batches.length, box_count: 0, mass_kg: 0, volume_m3: 0, energy_kwh: 0, flight_time_s: 0, work_time_s: 0 };
  for (const batch of plan.batches) {
    assert(!batchIds.has(batch.batch_id), `${plan.plan_id}: duplicate batch identifier ${batch.batch_id}`);
    batchIds.add(batch.batch_id);
    const drone = sources.drones[batch.model];
    assert(drone, `${plan.plan_id}: unknown model ${batch.model}`);
    let mass = 0, volume = 0;
    const counts = sources.type_order.map(() => 0);
    for (const id of batch.box_ids) {
      const box = sourceBoxes.get(id);
      assert(box && box.service_id === batch.service_id, `${plan.plan_id}: unknown or cross-area box ${id}`);
      assert(!visited.has(id), `${plan.plan_id}: repeated box ${id}`);
      visited.add(id);
      mass += box.mass_kg;
      volume += box.volume_m3;
      const typeIndex = sources.type_order.indexOf(box.type);
      assert(typeIndex >= 0, `${plan.plan_id}: unknown box type ${box.type}`);
      counts[typeIndex]++;
    }
    near(batch.mass_kg, mass, 'Batch mass');
    near(batch.volume_m3, volume, 'Batch volume');
    near(batch.box_count, batch.box_ids.length, 'Batch box count');
    assert(JSON.stringify(counts) === JSON.stringify(batch.counts), `${plan.plan_id}: batch type counts differ`);
    near(batch.return_soc_percent, 100 * (1 - batch.energy_kwh / drone.energy_kwh), 'Batch SOC');
    near(batch.energy_kwh, batch.horizontal_out_kwh + batch.horizontal_return_kwh + batch.climb_out_kwh + batch.climb_return_kwh, 'Batch energy components');
    near(batch.work_time_s, batch.flight_time_s + drone.prepare_s + drone.load_per_box_s * batch.box_count + drone.handover_base_s + drone.handover_per_box_s * batch.box_count, 'Batch cumulative work');
    assert(mass <= drone.payload_kg + EPSILON && volume <= drone.volume_m3 + EPSILON && batch.energy_kwh <= drone.energy_kwh * (1 - drone.reserve_percent / 100) + EPSILON, `${plan.plan_id}: unsafe batch ${batch.batch_id}`);
    for (const key of Object.keys(total)) if (key !== 'flight_count') total[key] += batch[key];
  }
  assert(visited.size === sourceBoxes.size, `${plan.plan_id}: unassigned boxes`);
  for (const key of Object.keys(total)) near(total[key], plan.totals[key], `${plan.plan_id} total ${key}`);
  near(Math.min(...plan.batches.map(batch => batch.return_soc_percent)), plan.totals.minimum_soc_percent, `${plan.plan_id} minimum SOC`);
  for (const model of ['A', 'B', 'C']) near(plan.batches.filter(batch => batch.model === model).length, plan.totals.model_counts?.[model] || 0, `${plan.plan_id} model ${model} flight count`);
  return total;
}

function buildWorkbook({ frontier, representatives, baseline, sources, tradeoff, changedServices, gravity }) {
  const workbook = Workbook.create();
  const compare = workbook.worksheets.add('方案比较');
  const pareto = workbook.worksheets.add('完整前沿');
  const detail = workbook.worksheets.add('代表方案批次');
  const detailRows = representatives.flatMap(plan => plan.batches.map(batch => ({ plan, batch })));
  const firstDetailRow = 5;
  const lastDetailRow = firstDetailRow + detailRows.length - 1;
  const parameterHeaderRow = lastDetailRow + 5;
  const parameterStartRow = parameterHeaderRow + 1;
  const parameterEndRow = parameterStartRow + 2;
  const parameterRange = `$A$${parameterStartRow}:$E$${parameterEndRow}`;
  const detailFull = column => `'代表方案批次'!$${column}$${firstDetailRow}:$${column}$${lastDetailRow}`;

  baseStyle(detail, `A1:O${parameterEndRow + 5}`, { A: 130, B: 107, C: 94, D: 70, E: 448, F: 103, G: 107, H: 125, I: 133, J: 143, K: 123, L: 114, M: 114, N: 132, O: 94 });
  writeTitle(detail, 'A1', '代表方案的完整货箱组批');
  writeNote(detail, 'A2', '每个方案独立覆盖全部货箱；不同方案之间的相同箱号是备选分组，不是重复执行。');
  detail.getRange('A4:O4').values = [['方案ID', '架次编号', '服务区', '机型', '货箱编号列表', '质量（kg）', '体积（m³）', '纯飞行（s）', '能耗（kWh）', '累计作业（s）', '返航SOC（%）', '载荷余量（kg）', '体积余量（m³）', '能量余量（kWh）', '约束检验']];
  headerStyle(detail, 'A4:O4');
  detail.getRange(`A${firstDetailRow}:O${lastDetailRow}`).values = detailRows.map(({ plan, batch }) => [plan.plan_id, batch.batch_id, batch.service_id, batch.model, batch.box_ids.join('; '), batch.mass_kg, batch.volume_m3, batch.flight_time_s, batch.energy_kwh, batch.work_time_s, null, null, null, null, null]);
  detail.getRange(`A${parameterHeaderRow}:E${parameterHeaderRow}`).values = [['机型', '载荷上限（kg）', '体积上限（m³）', 'Euse\n（kWh）', '返航SOC下限（%）']];
  detail.getRange(`A${parameterStartRow}:E${parameterEndRow}`).values = ['A', 'B', 'C'].map(model => {
    const drone = sources.drones[model];
    return [model, drone.payload_kg, drone.volume_m3, drone.energy_kwh, drone.reserve_percent];
  });
  headerStyle(detail, `A${parameterHeaderRow}:E${parameterHeaderRow}`);
  writeTitle(detail, `A${parameterHeaderRow - 2}`, '安全约束参数');
  for (let index = 0; index < detailRows.length; index++) {
    const row = firstDetailRow + index;
    const lookup = column => `VLOOKUP(D${row},${parameterRange},${column},FALSE)`;
    detail.getRange(`K${row}:O${row}`).formulas = [[
      `=(1-I${row}/${lookup(4)})*100`,
      `=${lookup(2)}-F${row}`,
      `=${lookup(3)}-G${row}`,
      `=${lookup(4)}*(1-${lookup(5)}/100)-I${row}`,
      `=IF(AND(L${row}>=-0.0000001,M${row}>=-0.0000001,N${row}>=-0.0000001),"满足","不满足")`,
    ]];
    const lines = Math.ceil(detailRows[index].batch.box_ids.join('; ').length / 49);
    detail.getRange(`A${row}:O${row}`).format.rowHeight = Math.max(30, lines * 17 + 10);
  }
  stripeRows(detail, firstDetailRow, lastDetailRow, 'O');
  detail.getRange(`E${firstDetailRow}:E${lastDetailRow}`).format.wrapText = true;
  detail.getRange(`F${firstDetailRow}:N${lastDetailRow}`).format.horizontalAlignment = 'right';
  detail.getRange(`O${firstDetailRow}:O${lastDetailRow}`).format.horizontalAlignment = 'center';
  for (const column of ['F', 'H', 'J', 'K', 'L']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.00');
  for (const column of ['G', 'M']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.000');
  for (const column of ['I', 'N']) detail.getRange(`${column}${firstDetailRow}:${column}${lastDetailRow}`).setNumberFormat('0.000000');
  detail.getRange(`O${firstDetailRow}:O${lastDetailRow}`).conditionalFormats.add('containsText', { text: '不满足', format: { fill: COLORS.error, font: { color: '#9C1C1C', bold: true } } });
  detail.getRange(`C${parameterStartRow}:C${parameterEndRow}`).setNumberFormat('0.000');
  detail.freezePanes.freezeRows(4);
  detail.freezePanes.freezeColumns(4);

  const firstFrontierRow = 5;
  const lastFrontierRow = firstFrontierRow + frontier.length - 1;
  baseStyle(pareto, `A1:G${lastFrontierRow + 4}`, { A: 132, B: 97, C: 165, D: 178, E: 170, F: 325, G: 195 });
  writeTitle(pareto, 'A1', '完整 Pareto 前沿');
  writeNote(pareto, 'A2', '三个目标均为最小化；对任一前沿点，不存在另一可行方案在三个目标均不更差时至少改善一项。');
  pareto.getRange('A4:G4').values = [['方案ID', '架次数', '总能耗（kWh）', '累计作业时间（s）', '累计作业时间（h）', '代表方案标记', '回溯入口']];
  headerStyle(pareto, 'A4:G4');
  for (let index = 0; index < frontier.length; index++) {
    const point = frontier[index], row = firstFrontierRow + index;
    const representativeLabels = representatives.filter(plan => plan.plan_id === point.plan_id).map(plan => plan.label).join('；');
    pareto.getRange(`A${row}:G${row}`).values = [[point.plan_id, point.flight_count, point.energy_kwh, point.work_time_s, null, representativeLabels || '—', 'multiobjective.json / 方案ID']];
    pareto.getRange(`E${row}`).formulas = [[`=D${row}/3600`]];
  }
  stripeRows(pareto, firstFrontierRow, lastFrontierRow, 'G');
  pareto.getRange(`B${firstFrontierRow}:E${lastFrontierRow}`).format.horizontalAlignment = 'right';
  pareto.getRange(`C${firstFrontierRow}:C${lastFrontierRow}`).setNumberFormat('0.000000');
  pareto.getRange(`D${firstFrontierRow}:D${lastFrontierRow}`).setNumberFormat('0.00');
  pareto.getRange(`E${firstFrontierRow}:E${lastFrontierRow}`).setNumberFormat('0.000000');
  pareto.getRange(`F${firstFrontierRow}:G${lastFrontierRow}`).format.wrapText = true;
  pareto.freezePanes.freezeRows(4);
  pareto.freezePanes.freezeColumns(1);

  const firstCompareRow = 6;
  const lastCompareRow = firstCompareRow + representatives.length;
  const priorityKeys = ['N_E_T', 'N_T_E', 'E_N_T', 'E_T_N', 'T_N_E', 'T_E_N'];
  assert(priorityKeys.every(key => sources.endpoints?.[key]), 'Missing one of six objective orders');
  const priorityHeaderRow = lastCompareRow + 5;
  const priorityFirstRow = priorityHeaderRow + 1;
  const priorityLastRow = priorityFirstRow + priorityKeys.length - 1;
  const compareNotesRow = priorityLastRow + 3;
  baseStyle(compare, `A1:N${compareNotesRow + 9}`, { A: 180, B: 132, C: 272, D: 85, E: 141, F: 162, G: 145, H: 152, I: 139, J: 141, K: 104, L: 145, M: 165, N: 120 });
  writeTitle(compare, 'A1', '第一问三目标组批方案比较');
  writeNote(compare, 'A2', '目标：架次数、总能耗、累计作业时间。保留不同权衡方案，按目标优先级或偏好选择。');
  writeNote(compare, 'A3', '与上一轮方案的变化量为“本方案－上一轮”；负数表示减少。完整非支配前沿与代表组批可按方案ID回溯。');
  compare.getRange('A5:N5').values = [['方案名称', '方案ID', '目标优先级／选取规则', '架次数', '总能耗（kWh）', '累计作业时间（s）', '累计作业时间（h）', '纯飞行时间（s）', 'A/B/C型架次数', '最低返航SOC（%）', '架次数变化', '能耗变化（kWh）', '累计作业变化（s）', 'Pareto前沿']];
  headerStyle(compare, 'A5:N5');
  const baselineRow = firstCompareRow;
  compare.getRange(`A${baselineRow}:N${baselineRow}`).values = [['上一轮方案', baseline.plan_id, baseline.priority, baseline.totals.flight_count, baseline.totals.energy_kwh, baseline.totals.work_time_s, null, baseline.totals.flight_time_s, ['A','B','C'].map(model => baseline.totals.model_counts?.[model] || 0).join(' / '), baseline.totals.minimum_soc_percent, 0, 0, 0, baseline.pareto_status]];
  compare.getRange(`G${baselineRow}`).formulas = [[`=F${baselineRow}/3600`]];
  for (let index = 0; index < representatives.length; index++) {
    const plan = representatives[index], row = firstCompareRow + index + 1;
    const selectedRows = detailRows.map((item, i) => item.plan.plan_id === plan.plan_id ? firstDetailRow + i : null).filter(value => value !== null);
    compare.getRange(`A${row}:C${row}`).values = [[plan.label, plan.plan_id, plan.priority]];
    compare.getRange(`D${row}:M${row}`).formulas = [[
      `=COUNTIFS(${detailFull('A')},B${row})`,
      `=SUMIFS(${detailFull('I')},${detailFull('A')},B${row})`,
      `=SUMIFS(${detailFull('J')},${detailFull('A')},B${row})`,
      `=F${row}/3600`,
      `=SUMIFS(${detailFull('H')},${detailFull('A')},B${row})`,
      `=COUNTIFS(${detailFull('A')},B${row},${detailFull('D')},"A")&" / "&COUNTIFS(${detailFull('A')},B${row},${detailFull('D')},"B")&" / "&COUNTIFS(${detailFull('A')},B${row},${detailFull('D')},"C")`,
      `=MIN(${selectedRows.map(detailRow => `'代表方案批次'!K${detailRow}`).join(',')})`,
      `=D${row}-$D$${baselineRow}`,
      `=E${row}-$E$${baselineRow}`,
      `=F${row}-$F$${baselineRow}`,
    ]];
    compare.getRange(`N${row}`).values = [['是']];
  }
  stripeRows(compare, firstCompareRow, lastCompareRow, 'N');
  compare.getRange(`C${firstCompareRow}:C${lastCompareRow}`).format.wrapText = true;
  compare.getRange(`A${firstCompareRow}:N${lastCompareRow}`).format.rowHeight = 48;
  compare.getRange(`D${firstCompareRow}:M${lastCompareRow}`).format.horizontalAlignment = 'right';
  compare.getRange(`N${firstCompareRow}:N${lastCompareRow}`).format.horizontalAlignment = 'center';
  for (const column of ['E', 'G', 'L']) compare.getRange(`${column}${firstCompareRow}:${column}${lastCompareRow}`).setNumberFormat('0.000000');
  for (const column of ['F', 'H', 'J', 'M']) compare.getRange(`${column}${firstCompareRow}:${column}${lastCompareRow}`).setNumberFormat('0.00');
  writeTitle(compare, `A${priorityHeaderRow - 2}`, '六种目标优先顺序');
  compare.getRange(`A${priorityHeaderRow}:G${priorityHeaderRow}`).values = [['优先顺序', '所选方案ID', '目标含义（从左到右）', '架次数', '总能耗（kWh）', '累计作业时间（s）', '累计作业时间（h）']];
  headerStyle(compare, `A${priorityHeaderRow}:G${priorityHeaderRow}`);
  const objectiveNames = { N: '架次', E: '能耗', T: '累计作业时间' };
  priorityKeys.forEach((key, index) => {
    const row = priorityFirstRow + index;
    const planId = sources.endpoints[key];
    assert(frontier.some(point => point.plan_id === planId), `${key}: unknown endpoint`);
    compare.getRange(`A${row}:C${row}`).values = [[key.replaceAll('_', ' → '), planId, key.split('_').map(part => objectiveNames[part]).join(' → ')]];
    const match = `MATCH(B${row},'完整前沿'!$A$${firstFrontierRow}:$A$${lastFrontierRow},0)`;
    compare.getRange(`D${row}:G${row}`).formulas = [[...['B', 'C', 'D'].map(column => `=INDEX('完整前沿'!$${column}$${firstFrontierRow}:$${column}$${lastFrontierRow},${match})`), `=F${row}/3600`]];
  });
  stripeRows(compare, priorityFirstRow, priorityLastRow, 'G');
  compare.getRange(`D${priorityFirstRow}:G${priorityLastRow}`).format.horizontalAlignment = 'right';
  compare.getRange(`E${priorityFirstRow}:E${priorityLastRow}`).setNumberFormat('0.000000');
  compare.getRange(`F${priorityFirstRow}:F${priorityLastRow}`).setNumberFormat('0.00');
  compare.getRange(`G${priorityFirstRow}:G${priorityLastRow}`).setNumberFormat('0.000000');
  const notes = [
    `实际权衡：${tradeoff.to_plan}比${tradeoff.from_plan}多${tradeoff.extra_flights}架次，节能${tradeoff.energy_saved_kwh.toFixed(6)} kWh（${tradeoff.energy_saved_percent.toFixed(4)}%），累计作业增加${tradeoff.extra_work_time_s.toFixed(2)} s（${tradeoff.extra_work_time_percent.toFixed(4)}%）。`,
    `具体差异：${changedServices.map(item => `${item.service_id}由${Object.entries(item.from_summary.model_counts).map(([model, count]) => `${count}架次${model}型`).join('＋')}改为${Object.entries(item.to_summary.model_counts).map(([model, count]) => `${count}架次${model}型`).join('＋')}`).join('；')}。其他服务区复用原方案。`,
    '时间口径：累计作业时间为各架次准备、逐箱装载、飞行及交接时间之和，不等于并行机队最晚完工时刻。',
    '航线采用以O01为原点的WGS84局部椭球平面；各架次只服务一个区并空载返航，本问不进行实体资源调度。',
    `沿用既定补充能耗模型：Ehor=Euse×d/L(q)，Eup=(m0+q)×${gravity}×h/(η×3.6×10^6)，下降不另计附加能耗。`,
    '上述能耗展开式为补充计算假设；Euse是原表电池可用能量字段，返航SOC另约束能耗≤Euse×80%，m0已含电池。',
    '完整前沿和代表方案由求解程序生成；本工作簿用于比较及核对，编辑单元格不会重新运行优化。',
    `六种顺序均在完整前沿上选取；逐候选能耗按1/${sources.algorithm.integer_cost_scales.energy} kWh、时间按1/${sources.algorithm.integer_cost_scales.time} s整数量化后累加比较，表中报告原值。`,
    '来源：results/question1_multiobjective/multiobjective.json；上一轮参照：results/question1_batching/solution.json。',
  ];
  notes.forEach((text, index) => writeNote(compare, `A${compareNotesRow + index}`, text));
  compare.freezePanes.freezeRows(5);
  compare.freezePanes.freezeColumns(3);
  compare.tabColor = COLORS.dark;
  pareto.tabColor = '#54738B';
  workbook.recalculate();

  const compareRows = compare.getRange(`D${firstCompareRow + 1}:N${lastCompareRow}`).values;
  representatives.forEach((plan, index) => {
    near(compareRows[index][0], plan.totals.flight_count, 'Calculated comparison flight count');
    near(compareRows[index][1], plan.totals.energy_kwh, 'Calculated comparison energy');
    near(compareRows[index][2], plan.totals.work_time_s, 'Calculated comparison cumulative work');
    near(compareRows[index][4], plan.totals.flight_time_s, 'Calculated comparison flight time');
    near(compareRows[index][6], plan.totals.minimum_soc_percent, 'Calculated comparison minimum SOC');
  });
  assert(detail.getRange(`O${firstDetailRow}:O${lastDetailRow}`).values.flat().every(value => value === '满足'), 'Workbook safety checks failed');
  priorityKeys.forEach((key, index) => {
    const point = frontier.find(item => item.plan_id === sources.endpoints[key]);
    const values = compare.getRange(`D${priorityFirstRow + index}:F${priorityFirstRow + index}`).values[0];
    ['flight_count', 'energy_kwh', 'work_time_s'].forEach((metric, i) => near(values[i], point[metric], `${key}: ${metric}`));
  });
  return { workbook, compareNotesRow, noteCount: notes.length, lastCompareRow, priorityHeaderRow, priorityLastRow, lastFrontierRow, firstDetailRow, lastDetailRow, parameterHeaderRow, parameterEndRow, detailRows };
}

const data = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const previous = JSON.parse(await fs.readFile(path.join(projectDir, 'results/question1_batching/solution.json'), 'utf8'));
assert(Array.isArray(data.frontier) && data.frontier.length > 0, 'Missing nonempty Pareto frontier');
assert(Array.isArray(data.representatives) && data.representatives.length > 0, 'Missing real representative plans');
const frontierIds = new Set(data.frontier.map(point => point.plan_id));
assert(frontierIds.size === data.frontier.length, 'Duplicate frontier plan identifiers');
const representativeIds = new Set(data.representatives.map(point => point.plan_id));
assert(representativeIds.size === data.representatives.length, 'Duplicate representative plan identifiers');
const objectiveKeys = ['flight_count', 'energy_kwh', 'work_time_s'];
for (const point of data.frontier) {
  for (const key of objectiveKeys) assert(Number.isFinite(point[key]) && point[key] > 0, `${point.plan_id}: invalid objective ${key}`);
  for (const other of data.frontier) {
    if (other === point) continue;
    const dominated = objectiveKeys.every(key => other[key] <= point[key] + EPSILON) && objectiveKeys.some(key => other[key] < point[key] - EPSILON);
    assert(!dominated, `Frontier point ${point.plan_id} is dominated by ${other.plan_id}`);
  }
}
const representatives = data.representatives.map(point => ({ ...point, totals: point }));
for (const plan of representatives) {
  assert(frontierIds.has(plan.plan_id), `${plan.plan_id}: representative missing from frontier`);
  const point = data.frontier.find(item => item.plan_id === plan.plan_id);
  for (const key of objectiveKeys) near(plan[key], point[key], `${plan.plan_id} representative objective ${key}`);
  validateRepresentative(plan, data);
}
for (const key of ['flight_count', 'box_count', 'mass_kg', 'volume_m3', 'energy_kwh', 'flight_time_s', 'work_time_s', 'minimum_soc_percent']) {
  near(data.baseline[key], previous.totals[key], `Previous baseline ${key}`);
}
const matchingBaseline = data.frontier.find(point => objectiveKeys.every(key => Math.abs(point[key] - data.baseline[key]) <= EPSILON));
const baseline = { plan_id: 'BASELINE', priority: '先最少架次，再最低能耗，再最短累计作业时间', totals: data.baseline, pareto_status: matchingBaseline ? `是（${matchingBaseline.plan_id}）` : '否' };
assert(data.tradeoff && Array.isArray(data.changed_services), 'Missing tradeoff or changed service audit');
const from = data.frontier.find(point => point.plan_id === data.tradeoff.from_plan);
const to = data.frontier.find(point => point.plan_id === data.tradeoff.to_plan);
assert(from && to, 'Tradeoff references missing frontier points');
near(to.flight_count - from.flight_count, data.tradeoff.extra_flights, 'Tradeoff extra flights');
near(from.energy_kwh - to.energy_kwh, data.tradeoff.energy_saved_kwh, 'Tradeoff energy saving');
near(to.work_time_s - from.work_time_s, data.tradeoff.extra_work_time_s, 'Tradeoff extra work time');
assert(Number.isFinite(previous.physical_parameters?.gravity_m_s2), 'Missing source gravity');
const built = buildWorkbook({ frontier: data.frontier, representatives, baseline, sources: data, tradeoff: data.tradeoff, changedServices: data.changed_services, gravity: previous.physical_parameters.gravity_m_s2 });
const errorScan = await built.workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: { useRegex: true, maxResults: 20 }, summary: 'Q1 multiobjective workbook formula errors', maxChars: 2500 });
console.log(errorScan.ndjson);
await fs.mkdir(outputDir, { recursive: true });
const file = await SpreadsheetFile.exportXlsx(built.workbook);
await file.save(outputPath);
const previewSpecs = [
  ['方案比较', `A1:N${built.lastCompareRow}`, 'q1_multiobjective_comparison.png'],
  ['方案比较', `A${built.compareNotesRow}:M${built.compareNotesRow + built.noteCount - 1}`, 'q1_multiobjective_notes.png'],
  ['方案比较', `A${built.priorityHeaderRow - 2}:G${built.priorityLastRow}`, 'q1_multiobjective_priorities.png'],
  ['完整前沿', `A1:G${built.lastFrontierRow}`, 'q1_multiobjective_frontier.png'],
];
for (const plan of representatives) {
  const start = built.detailRows.findIndex(item => item.plan.plan_id === plan.plan_id) + built.firstDetailRow;
  const end = start + plan.batches.length - 1;
  previewSpecs.push(['代表方案批次', `A${start === built.firstDetailRow ? 4 : start}:O${end}`, `q1_multiobjective_${plan.plan_id}_batches.png`]);
}
previewSpecs.push(['代表方案批次', `A${built.parameterHeaderRow - 2}:E${built.parameterEndRow}`, 'q1_multiobjective_parameters.png']);
for (const [sheetName, range, name] of previewSpecs) {
  const png = await built.workbook.render({ sheetName, range, scale: 1.3, format: 'png' });
  await fs.writeFile(path.join(outputDir, name), new Uint8Array(await png.arrayBuffer()));
}
console.log(JSON.stringify({ outputPath, frontierCount: data.frontier.length, representativeCount: representatives.length, representativeBatchRows: built.detailRows.length, comparedBaseline: previous.totals, objectives: data.frontier.map(point => ({ plan_id: point.plan_id, flight_count: point.flight_count, energy_kwh: point.energy_kwh, work_time_s: point.work_time_s })), previewFiles: previewSpecs.map(item => item[2]) }, null, 2));
