/** Export the verified Q1 solution to a separate workbook; never edit the template. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FileBlob, SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const projectDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inputPath = path.resolve(process.argv[2] || path.join(projectDir, 'results/question1_batching/solution.json'));
const outputPath = path.resolve(process.argv[3] || path.join(projectDir, 'outputs/question1_batching/第一问_货箱组批方案.xlsx'));
const outputDir = path.dirname(outputPath);
const solution = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const batches = solution.batches;
const templatePath = path.join(projectDir, '结果提交模板.xlsx');
const expectedHeaders = ['架次编号', '服务区编号', '机型编号', '货箱编号列表', '总质量（kg）', '总体积（m³）', '往返时间（s）', '架次能耗（kWh）', '返航SOC（%）'];
const tolerance = 1e-7;
const near = (a, b, label) => {
  if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a - b) > tolerance * Math.max(1, Math.abs(b))) {
    throw new Error(`${label}: ${a} != ${b}`);
  }
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
assert(Array.isArray(batches) && batches.length > 0, 'Missing nonempty batches.');
assert(Array.isArray(solution.boxes) && solution.boxes.length > 0, 'Missing source boxes.');
assert(Number.isFinite(solution.physical_parameters?.gravity_m_s2), 'Missing source gravity.');
const boxMap = new Map(solution.boxes.map(box => [box.id, box]));
assert(boxMap.size === solution.boxes.length, 'Duplicate source box identifiers.');
const visitedBoxes = new Set();
const batchIds = new Set();
for (const batch of batches) {
  assert(!batchIds.has(batch.batch_id), `Duplicate batch ${batch.batch_id}`);
  batchIds.add(batch.batch_id);
  const drone = solution.drones[batch.model];
  assert(drone && solution.nodes[batch.service_id], `Unknown model or service area in ${batch.batch_id}`);
  let mass = 0, volume = 0;
  const counts = solution.type_order.map(() => 0);
  for (const id of batch.box_ids) {
    const box = boxMap.get(id);
    assert(box && box.service_id === batch.service_id, `Unknown or cross-area box ${id}`);
    assert(!visitedBoxes.has(id), `Repeated box ${id}`);
    visitedBoxes.add(id);
    mass += box.mass_kg;
    volume += box.volume_m3;
    const typeIndex = solution.type_order.indexOf(box.type);
    assert(typeIndex >= 0, `Unknown cargo type ${box.type}`);
    counts[typeIndex]++;
  }
  assert(JSON.stringify(counts) === JSON.stringify(batch.counts), `Cargo counts mismatch in ${batch.batch_id}`);
  near(batch.box_count, batch.box_ids.length, 'Box count');
  near(batch.mass_kg, mass, 'Batch mass');
  near(batch.volume_m3, volume, 'Batch volume');
  near(batch.payload_limit_kg, drone.payload_kg, 'Payload limit');
  near(batch.volume_limit_m3, drone.volume_m3, 'Volume limit');
  near(batch.reserve_percent, drone.reserve_percent, 'Reserve percentage');
  near(batch.energy_budget_kwh, drone.energy_kwh * (1 - drone.reserve_percent / 100), 'Energy budget');
  near(batch.return_soc_percent, 100 * (1 - batch.energy_kwh / drone.energy_kwh), 'Return SOC');
  near(batch.energy_kwh, batch.horizontal_out_kwh + batch.horizontal_return_kwh + batch.climb_out_kwh + batch.climb_return_kwh, 'Energy components');
  near(batch.work_time_s, batch.flight_time_s + drone.prepare_s + drone.load_per_box_s * batch.box_count + drone.handover_base_s + drone.handover_per_box_s * batch.box_count, 'Work time');
  assert(mass <= drone.payload_kg + tolerance && volume <= drone.volume_m3 + tolerance && batch.energy_kwh <= batch.energy_budget_kwh + tolerance, `Infeasible batch ${batch.batch_id}`);
}
assert(visitedBoxes.size === boxMap.size, 'Some source boxes are not assigned.');
for (const key of ['mass_kg', 'volume_m3', 'energy_kwh', 'flight_time_s', 'work_time_s', 'box_count']) {
  near(batches.reduce((sum, batch) => sum + batch[key], 0), solution.totals[key], `Total ${key}`);
}
near(batches.length, solution.totals.flight_count, 'Flight count');

// Read the supplied header through the same library used for export.
const template = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
const headers = template.worksheets.getItem('Q1_单点组批').getRange('A1:I1').values[0];
assert(JSON.stringify(headers) === JSON.stringify(expectedHeaders), 'The Q1 template headers changed.');

const workbook = Workbook.create();
const main = workbook.worksheets.add('Q1_单点组批');
const summary = workbook.worksheets.add('各区汇总');
const detail = workbook.worksheets.add('计算明细');
const lastRow = batches.length + 1;
const parameterHeaderRow = lastRow + 5;
const parameterStartRow = parameterHeaderRow + 1;
const parameterEndRow = parameterStartRow + 2;
const parameterRange = `$A$${parameterStartRow}:$J$${parameterEndRow}`;
const font = 'Microsoft YaHei';
const colors = { dark: '#183650', pale: '#EEF3F7', ink: '#243746', line: '#D3DDE5', red: '#FCE7E7' };

function styleBase(sheet, range) {
  sheet.showGridLines = false;
  sheet.getRange(range).format.font = { name: font, size: 10, color: colors.ink };
  sheet.getRange(range).format.rowHeight = 27;
  sheet.getRange(range).format.verticalAlignment = 'center';
}
function header(sheet, range) {
  const cells = sheet.getRange(range);
  cells.format.fill = colors.dark;
  cells.format.font = { name: font, size: 10, bold: true, color: '#FFFFFF' };
  cells.format.wrapText = true;
  cells.format.rowHeight = 42;
  cells.format.horizontalAlignment = 'center';
  cells.format.borders = { insideVertical: { style: 'thin', color: '#FFFFFF' } };
}
function widths(sheet, columns) {
  for (const [col, pixels] of Object.entries(columns)) sheet.getRange(`${col}:${col}`).format.columnWidthPx = pixels;
}
function stripe(sheet, first, last, endCol) {
  for (let r = first; r <= last; r++) if ((r - first) % 2 === 1) sheet.getRange(`A${r}:${endCol}${r}`).format.fill = colors.pale;
}
function title(sheet, cell, text) {
  sheet.getRange(cell).values = [[text]];
  sheet.getRange(cell).format.font = { name: font, size: 14, bold: true, color: colors.dark };
  sheet.getRange(cell).format.rowHeight = 31;
}
function note(sheet, cell, text) {
  sheet.getRange(cell).values = [[text]];
  sheet.getRange(cell).format.font = { name: font, size: 10, color: '#526574' };
}
const mainRef = (col, row) => `'Q1_单点组批'!${col}${row}`;
const fullMain = col => `'Q1_单点组批'!$${col}$2:$${col}$${lastRow}`;
const lookup = (row, column) => `VLOOKUP(B${row},${parameterRange},${column},FALSE)`;

// The primary result preserves all nine template columns and contains no totals row.
main.getRange(`A1:I${lastRow}`).values = [headers, ...batches.map(batch => [
  batch.batch_id, batch.service_id, batch.model, batch.box_ids.join('; '),
  batch.mass_kg, batch.volume_m3, batch.flight_time_s, batch.energy_kwh, batch.return_soc_percent,
])];
styleBase(main, `A1:I${lastRow}`);
widths(main, { A: 102, B: 97, C: 86, D: 440, E: 110, F: 114, G: 128, H: 137, I: 124 });
header(main, 'A1:I1');
stripe(main, 2, lastRow, 'I');
main.getRange(`D2:D${lastRow}`).format.wrapText = true;
main.getRange(`A2:D${lastRow}`).format.horizontalAlignment = 'left';
main.getRange(`E2:I${lastRow}`).format.horizontalAlignment = 'right';
main.getRange(`E2:E${lastRow}`).setNumberFormat('0.0');
main.getRange(`F2:F${lastRow}`).setNumberFormat('0.000');
main.getRange(`G2:G${lastRow}`).setNumberFormat('0.00');
main.getRange(`H2:H${lastRow}`).setNumberFormat('0.000000');
main.getRange(`I2:I${lastRow}`).setNumberFormat('0.00');
batches.forEach((batch, index) => {
  const lines = Math.ceil(batch.box_ids.join('; ').length / 49);
  main.getRange(`A${index + 2}:I${index + 2}`).format.rowHeight = Math.max(30, lines * 17 + 10);
});
main.freezePanes.freezeRows(1);
main.freezePanes.freezeColumns(3);
main.tabColor = colors.dark;

// This sheet owns preparation/handover calculations; checks are terminal columns.
const detailHeaders = ['架次编号', '机型', '货箱数', '准备及装载（s）', '服务区交接（s）', '累计作业时间（s）', '载荷上限（kg）', '载荷余量（kg）', '体积上限（m³）', '体积余量（m³）', '电池能量（kWh）', '返航SOC下限（%）', '允许能耗（kWh）', '能量余量（kWh）', '约束检验'];
detail.getRange('A1:O1').values = [detailHeaders];
detail.getRange(`A2:O${lastRow}`).values = batches.map(batch => [batch.batch_id, batch.model, batch.box_count, null, null, null, null, null, null, null, null, null, null, null, null]);
detail.getRange(`A${parameterHeaderRow}:J${parameterHeaderRow}`).values = [['机型', '载荷上限（kg）', '体积上限（m³）', '电池能量（kWh）', '返航下限（%）', '固定准备（s）', '每箱装载（s）', '基础交接（s）', '每箱交接（s）', '含电池空载（kg）']];
detail.getRange(`A${parameterStartRow}:J${parameterEndRow}`).values = ['A', 'B', 'C'].map(model => {
  const d = solution.drones[model];
  return [model, d.payload_kg, d.volume_m3, d.energy_kwh, d.reserve_percent, d.prepare_s, d.load_per_box_s, d.handover_base_s, d.handover_per_box_s, d.empty_mass_kg];
});
for (let row = 2; row <= lastRow; row++) {
  detail.getRange(`D${row}:O${row}`).formulas = [[
    `=${lookup(row, 6)}+C${row}*${lookup(row, 7)}`,
    `=${lookup(row, 8)}+C${row}*${lookup(row, 9)}`,
    `=${mainRef('G', row)}+SUM(D${row}:E${row})`,
    `=${lookup(row, 2)}`, `=G${row}-${mainRef('E', row)}`,
    `=${lookup(row, 3)}`, `=I${row}-${mainRef('F', row)}`,
    `=${lookup(row, 4)}`, `=${lookup(row, 5)}`,
    `=K${row}*(1-L${row}/100)`, `=M${row}-${mainRef('H', row)}`,
    `=IF(AND(H${row}>=-0.0000001,J${row}>=-0.0000001,N${row}>=-0.0000001,ABS(${mainRef('I', row)}-(1-${mainRef('H', row)}/K${row})*100)<0.0000001),"满足","不满足")`,
  ]];
}
styleBase(detail, `A1:O${parameterEndRow + 12}`);
widths(detail, { A: 104, B: 66, C: 74, D: 118, E: 118, F: 134, G: 106, H: 110, I: 110, J: 112, K: 114, L: 121, M: 116, N: 116, O: 92 });
header(detail, 'A1:O1');
header(detail, `A${parameterHeaderRow}:J${parameterHeaderRow}`);
stripe(detail, 2, lastRow, 'O');
detail.getRange(`C2:N${lastRow}`).format.horizontalAlignment = 'right';
detail.getRange(`O2:O${lastRow}`).format.horizontalAlignment = 'center';
detail.getRange(`D2:H${lastRow}`).setNumberFormat('0.00');
detail.getRange(`I2:J${lastRow}`).setNumberFormat('0.000');
detail.getRange(`K2:M${lastRow}`).setNumberFormat('0.00');
detail.getRange(`N2:N${lastRow}`).setNumberFormat('0.000000');
detail.getRange(`C${parameterStartRow}:C${parameterEndRow}`).setNumberFormat('0.000');
detail.getRange(`O2:O${lastRow}`).conditionalFormats.add('containsText', { text: '不满足', format: { fill: colors.red, font: { color: '#9C1C1C', bold: true } } });
detail.freezePanes.freezeRows(1);
detail.freezePanes.freezeColumns(3);
title(detail, `A${parameterHeaderRow - 2}`, '机型参数与计算口径');
const notes = [
  '来源：运输无人机数据.xlsx，数据!C2:R5；距离采用以O01为原点的WGS84局部椭球平面。',
  '往返时间为纯飞行时间；累计作业时间还包括固定准备、逐箱装载及服务区交接。',
  '各架次累计作业时间求和不代表并行机队的最晚完工时刻；本问不进行实体无人机或电池排程。',
  `水平能耗采用补充定义 Ehor=Euse×d/L(q)；爬升附加能耗采用 Eup=(m0+q)×${solution.physical_parameters.gravity_m_s2}×h/(η×3.6×10^6)。`,
  'Euse为原表电池可用能量字段；返航SOC另约束消耗不超过Euse×80%。m0为含电池空载质量。',
  '以上两项为题目参数及物理量纲下的补充计算假设；下降不单独增加附加能耗，返程为空载。',
  `求解器能量可行性容差为${solution.numeric_tolerances.energy_kwh} kWh；连续安全载荷二分精度为${solution.numeric_tolerances.payload_bisection_kg} kg。`,
  `逐候选能耗按1/${solution.numeric_tolerances.objective_energy_scale} kWh、时间按1/${solution.numeric_tolerances.objective_time_scale} s整数量化后累加比较；表中报告未量化原值。`,
  '表中为固定优化方案及其计算检查，编辑单元格不会自动重新求解组批。',
];
notes.forEach((text, index) => note(detail, `A${parameterEndRow + 2 + index}`, text));

// Concise service-area output and safe payload results, with traceable formulas.
const serviceIds = solution.service_summary.map(row => row.service_id);
const summaryHeaderRow = 5;
const summaryStartRow = 6;
const summaryLastRow = summaryStartRow + serviceIds.length - 1;
const totalRow = summaryLastRow + 1;
const safeHeaderRow = totalRow + 5;
const safeStartRow = safeHeaderRow + 1;
const safeLastRow = safeStartRow + serviceIds.length - 1;
styleBase(summary, `A1:K${safeLastRow + 5}`);
widths(summary, { A: 100, B: 260, C: 78, D: 84, E: 105, F: 112, G: 134, H: 140, I: 150, J: 140, K: 135 });
title(summary, 'A2', '第一问各服务区组批汇总');
note(summary, 'A3', '优化顺序：架次数最少，其次总能耗最小，再次累计作业时间最小。');
summary.getRange(`A${summaryHeaderRow}:K${summaryHeaderRow}`).values = [['服务区编号', '服务区名称', '箱数', '架次数', '总质量（kg）', '总体积（m³）', '总能耗（kWh）', '纯飞行合计（s）', '累计作业时间（s）', 'A/B/C型架次数', '最低返航SOC（%）']];
for (let index = 0; index < serviceIds.length; index++) {
  const id = serviceIds[index], row = summaryStartRow + index;
  summary.getRange(`A${row}:B${row}`).values = [[id, solution.nodes[id].name]];
  const batchRows = batches.map((batch, i) => batch.service_id === id ? i + 2 : null).filter(Boolean);
  summary.getRange(`C${row}:K${row}`).formulas = [[
    `=SUMIFS('计算明细'!$C$2:$C$${lastRow},${fullMain('B')},A${row})`,
    `=COUNTIFS(${fullMain('B')},A${row})`,
    `=SUMIFS(${fullMain('E')},${fullMain('B')},A${row})`,
    `=SUMIFS(${fullMain('F')},${fullMain('B')},A${row})`,
    `=SUMIFS(${fullMain('H')},${fullMain('B')},A${row})`,
    `=SUMIFS(${fullMain('G')},${fullMain('B')},A${row})`,
    `=SUMIFS('计算明细'!$F$2:$F$${lastRow},${fullMain('B')},A${row})`,
    `=COUNTIFS(${fullMain('B')},A${row},${fullMain('C')},"A")&" / "&COUNTIFS(${fullMain('B')},A${row},${fullMain('C')},"B")&" / "&COUNTIFS(${fullMain('B')},A${row},${fullMain('C')},"C")`,
    `=MIN(${batchRows.map(r => mainRef('I', r)).join(',')})`,
  ]];
}
summary.getRange(`A${totalRow}`).values = [['合计']];
for (const col of ['C', 'D', 'E', 'F', 'G', 'H', 'I']) summary.getRange(`${col}${totalRow}`).formulas = [[`=SUM(${col}${summaryStartRow}:${col}${summaryLastRow})`]];
summary.getRange(`J${totalRow}`).formulas = [[`=COUNTIFS(${fullMain('C')},"A")&" / "&COUNTIFS(${fullMain('C')},"B")&" / "&COUNTIFS(${fullMain('C')},"C")`]];
summary.getRange(`K${totalRow}`).formulas = [[`=MIN(${fullMain('I')})`]];
header(summary, `A${summaryHeaderRow}:K${summaryHeaderRow}`);
stripe(summary, summaryStartRow, summaryLastRow, 'K');
summary.getRange(`A${totalRow}:K${totalRow}`).format.fill = '#DCE7EF';
summary.getRange(`A${totalRow}:K${totalRow}`).format.font.bold = true;
summary.getRange(`C${summaryStartRow}:K${totalRow}`).format.horizontalAlignment = 'right';
summary.getRange(`E${summaryStartRow}:E${totalRow}`).setNumberFormat('0.0');
summary.getRange(`F${summaryStartRow}:F${totalRow}`).setNumberFormat('0.000');
summary.getRange(`G${summaryStartRow}:G${totalRow}`).setNumberFormat('0.000000');
summary.getRange(`H${summaryStartRow}:I${totalRow}`).setNumberFormat('0.00');
summary.getRange(`K${summaryStartRow}:K${totalRow}`).setNumberFormat('0.00');
title(summary, `A${safeHeaderRow - 2}`, '各机型最大安全载荷');
summary.getRange(`A${safeHeaderRow}:E${safeHeaderRow}`).values = [['服务区编号', 'A型（kg）', 'B型（kg）', 'C型（kg）', 'SOC下限（%）']];
summary.getRange(`A${safeStartRow}:E${safeLastRow}`).values = solution.service_summary.map(item => [item.service_id, ...['A', 'B', 'C'].map(model => item.safe_payloads[model].safe_payload_kg), solution.drones.A.reserve_percent]);
header(summary, `A${safeHeaderRow}:E${safeHeaderRow}`);
stripe(summary, safeStartRow, safeLastRow, 'E');
summary.getRange(`B${safeStartRow}:D${safeLastRow}`).setNumberFormat('0.000');
summary.getRange(`B${safeStartRow}:E${safeLastRow}`).format.horizontalAlignment = 'right';
summary.getRange(`A${safeHeaderRow}:E${safeHeaderRow}`).format.rowHeight = 50;
note(summary, `A${safeLastRow + 2}`, '安全载荷是连续质量上限；实际装箱还受不可拆分与体积约束。详细参数及能耗假设见“计算明细”。');
summary.tabColor = '#54738B';

workbook.recalculate();
const calculatedWorkTimes = detail.getRange(`F2:F${lastRow}`).values.flat();
calculatedWorkTimes.forEach((value, index) => near(value, batches[index].work_time_s, `Calculated work time ${index + 1}`));
assert(detail.getRange(`O2:O${lastRow}`).values.flat().every(value => value === '满足'), 'Workbook batch constraint check failed.');
const totals = summary.getRange(`C${totalRow}:K${totalRow}`).values[0];
[solution.totals.box_count, solution.totals.flight_count, solution.totals.mass_kg, solution.totals.volume_m3, solution.totals.energy_kwh, solution.totals.flight_time_s, solution.totals.work_time_s].forEach((expected, index) => near(totals[index], expected, `Workbook total ${index}`));
near(totals[8], solution.totals.minimum_soc_percent, 'Workbook minimum SOC');
const errorScan = await workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!', options: { useRegex: true, maxResults: 20 }, summary: 'Q1 workbook formula errors', maxChars: 2500 });
console.log(errorScan.ndjson);
await fs.mkdir(outputDir, { recursive: true });
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(outputPath);
for (const [sheetName, range, name] of [
  ['Q1_单点组批', `A1:I${lastRow}`, 'q1_batches_preview.png'],
  ['各区汇总', `A2:K${totalRow}`, 'q1_summary_preview.png'],
  ['各区汇总', `A${safeHeaderRow - 2}:E${safeLastRow}`, 'q1_payloads_preview.png'],
  ['计算明细', `A1:O${parameterEndRow}`, 'q1_details_preview.png'],
  ['计算明细', `A${parameterEndRow + 2}:M${parameterEndRow + 1 + notes.length}`, 'q1_notes_preview.png'],
]) {
  const png = await workbook.render({ sheetName, range, scale: 1.3, format: 'png' });
  await fs.writeFile(path.join(outputDir, name), new Uint8Array(await png.arrayBuffer()));
}
console.log(JSON.stringify({ outputPath, batchCount: batches.length, boxCount: visitedBoxes.size, energyKwh: totals[4], flightTimeSeconds: totals[5], cumulativeWorkSeconds: totals[6], minimumSocPercent: totals[8], worksheets: ['Q1_单点组批', '各区汇总', '计算明细'] }, null, 2));
