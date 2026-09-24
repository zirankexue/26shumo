// Read-only audit: temporary edits remain in memory; this script never exports XLSX.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const options = Object.fromEntries(process.argv.slice(2).reduce((pairs, item, index, all) => {
  if (item.startsWith('--')) pairs.push([item.slice(2), all[index + 1]]);
  return pairs;
}, []));
const resolve = value => path.isAbsolute(value) ? value : path.resolve(project, value);
const input = resolve(options.xlsx || 'outputs/q2_weighted/问题二结果.xlsx');
const definition = path.join(path.dirname(input), 'tables/results.json');
const output = resolve(options.log || 'outputs/q2_weighted/logs/weight_formula_check.json');
const runtimeModules = process.env.CODEX_NODE_MODULES;
const requireFrom = runtimeModules
  ? createRequire(path.join(runtimeModules, '__weighted_audit_resolver__.cjs'))
  : createRequire(import.meta.url);
const { SpreadsheetFile, FileBlob } = await import(pathToFileURL(requireFrom.resolve('@oai/artifact-tool')).href);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const beforeHash = hash(await fs.readFile(input));
const tolerance = 1e-11;
const log = {
  generated_utc: new Date().toISOString(),
  stage: options.stage || 'final',
  workbook: input,
  engine: '@oai/artifact-tool',
  native_excel_engine_tested: false,
  disk_workbook_modified: false,
  input_sha256: beforeHash,
  tolerance,
  cases: [],
};

function close(actual, expected, label) {
  if (typeof actual !== 'number' || !Number.isFinite(actual) || !Number.isFinite(expected)
      || Math.abs(actual - expected) > tolerance * Math.max(1, Math.abs(expected))) {
    throw new Error(`${label}: actual ${actual}; expected ${expected}`);
  }
}

try {
  const source = JSON.parse(await fs.readFile(definition, 'utf8'));
  if (source.sheets.length !== 17) throw new Error(`Expected 17 declared sheets, got ${source.sheets.length}`);
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
  log.verified_sheet_names = source.sheets.map(sheet => {
    const loaded = workbook.worksheets.getItem(sheet.name);
    if (!loaded) throw new Error(`Missing worksheet: ${sheet.name}`);
    return sheet.name;
  });
  const settings = workbook.worksheets.getItem('权重设置');
  const score = workbook.worksheets.getItem('主权重统一评分');
  const scoreDefinition = source.sheets.find(sheet => sheet.name === '主权重统一评分');
  const count = scoreDefinition.rows.length;
  if (count !== 9) throw new Error(`Expected 9 comparison rows, got ${count}`);
  const last = count + 1;
  const defaults = [3, 1.5, 1, 0.5];
  const originalWeights = settings.getRange('C2:C5').values.map(row => row[0]);
  originalWeights.forEach((value, i) => close(value, defaults[i], `Original weight ${i}`));
  const originalFormula = score.getRange(`F2:J${last}`).formulas;
  for (const row of originalFormula) {
    if (!row.every(value => typeof value === 'string' && value.startsWith('='))) {
      throw new Error('All normalized metrics and scores must contain actual formulas');
    }
  }
  const originalRaw = score.getRange(`B2:E${last}`).values;
  const fixed = settings.getRange('E2:G5').values;
  const expectedNormalized = originalRaw.map(row => row.map((value, i) => (value - fixed[i][0]) / fixed[i][2]));

  function auditCase(name, weights) {
    settings.getRange('C2:C5').values = weights.map(value => [value]);
    workbook.recalculate();
    const total = weights.reduce((a, b) => a + b, 0);
    const alphas = weights.map(value => value / total);
    const actualAlphas = settings.getRange('D2:D5').values.map(row => row[0]);
    actualAlphas.forEach((value, i) => close(value, alphas[i], `${name}/alpha${i}`));
    const values = score.getRange(`F2:J${last}`).values;
    const rows = values.map((row, r) => {
      for (let i = 0; i < 4; i++) close(row[i], expectedNormalized[r][i], `${name}/row${r+2}/normalization${i}`);
      const expected = expectedNormalized[r].reduce((sum, value, i) => sum + value * alphas[i], 0);
      close(row[4], expected, `${name}/J${r+2}`);
      return { row: r + 2, scenario: scoreDefinition.rows[r][0], actual: row[4], expected,
               absolute_error: Math.abs(row[4] - expected) };
    });
    log.cases.push({ name, relative_weights: weights, normalized_weights: actualAlphas, rows, passed: true });
    return rows.map(row => row.actual);
  }

  const baseline = auditCase('default_before_temporary_change', defaults);
  baseline.forEach((value, r) => close(value, scoreDefinition.rows[r][9], `Default score/JSON row ${r+2}`));
  const equal = auditCase('equal_weights_mean_of_normalized_metrics', [1, 1, 1, 1]);
  equal.forEach((value, r) => close(value, expectedNormalized[r].reduce((a, b) => a + b, 0) / 4, `Equal-weight mean row ${r+2}`));
  if (!equal.some((value, r) => Math.abs(value - baseline[r]) > tolerance)) {
    throw new Error('Changing weights did not alter any score; recalculation was not demonstrated');
  }
  const restored = auditCase('default_restored', defaults);
  restored.forEach((value, r) => close(value, baseline[r], `Restore row ${r+2}`));
  if (JSON.stringify(score.getRange(`F2:J${last}`).formulas) !== JSON.stringify(originalFormula)) {
    throw new Error('Temporary input changes altered formulas');
  }
  if (JSON.stringify(score.getRange(`B2:E${last}`).values) !== JSON.stringify(originalRaw)) {
    throw new Error('Temporary input changes altered raw metrics');
  }
  const errors = [];
  const errorPattern = /^#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A|NUM!|NULL!|SPILL!|CALC!)/;
  for (const sheet of source.sheets) {
    const values = workbook.worksheets.getItem(sheet.name)
      .getRangeByIndexes(0, 0, sheet.rows.length + 1, sheet.headers.length).values;
    values.forEach((row, r) => row.forEach((value, c) => {
      if (typeof value === 'string' && errorPattern.test(value)) errors.push({sheet: sheet.name, row: r + 1, column: c + 1, value});
    }));
  }
  if (errors.length) throw new Error(`Formula errors: ${JSON.stringify(errors)}`);
  log.checked_score_range = `主权重统一评分!F2:J${last}`;
  log.changed_input_range = '权重设置!C2:C5';
  log.default_scores_equal_results_json = true;
  log.formulas_and_raw_metrics_unchanged = true;
  log.recalculation_demonstrated = true;
  log.formula_error_count = 0;
  log.all_checks_passed = true;
} catch (error) {
  log.all_checks_passed = false;
  log.error = String(error.stack || error);
  process.exitCode = 1;
} finally {
  log.final_input_sha256 = hash(await fs.readFile(input));
  log.disk_workbook_modified = log.final_input_sha256 !== beforeHash;
  if (log.disk_workbook_modified) {
    log.all_checks_passed = false;
    log.error = `${log.error || ''}\nSource XLSX hash changed during audit`;
    process.exitCode = 1;
  }
  await fs.mkdir(path.dirname(output), {recursive: true});
  await fs.writeFile(output, JSON.stringify(log, null, 2) + '\n', 'utf8');
  console.log(JSON.stringify({output, stage: log.stage, all_checks_passed: log.all_checks_passed,
                              disk_workbook_modified: log.disk_workbook_modified,
                              tested_cases: log.cases.length, error: log.error}, null, 2));
}
