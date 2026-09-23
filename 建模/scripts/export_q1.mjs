import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// Resolve a public package entry from the configured Codex runtime; do not inspect internals.
const packageRoot = process.env.CODEX_NODE_MODULES;
const requireFrom = packageRoot
  ? createRequire(path.join(packageRoot, '__q1_resolver__.cjs'))
  : createRequire(import.meta.url);
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(requireFrom.resolve('@oai/artifact-tool')).href);
const output = path.resolve(process.argv[2]);
const outputName = process.argv[4] || '问题一结果.xlsx';
const preview = process.argv[3] !== 'false';
const data = JSON.parse(await fs.readFile(path.join(output, 'tables/results.json'), 'utf8'));
const wb = Workbook.create();

function column(n) {
  let s = '';
  for (n++; n; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s;
  return s;
}

for (const source of data.sheets) {
  const sheet = wb.worksheets.add(source.name);
  const count = source.rows.length + 1;
  const lastCol = column(source.headers.length - 1);
  const range = sheet.getRange(`A1:${lastCol}${count}`);
  range.values = [source.headers, ...source.rows];
  range.format.font.name = 'Arial';
  range.format.font.size = 11;
  range.format.font.color = '#1D2935';
  range.format.rowHeight = 29;
  range.format.verticalAlignment = 'center';
  range.format.horizontalAlignment = 'right';
  range.format.wrapText = true;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const header = sheet.getRange(`A1:${lastCol}1`);
  header.format.fill = '#284B63';
  header.format.font.color = '#FFFFFF';
  header.format.font.bold = true;
  header.format.horizontalAlignment = 'center';
  header.format.rowHeight = 42;
  for (let c = 0; c < source.headers.length; c++) {
    const col = column(c);
    sheet.getRange(`${col}1:${col}${count}`).format.columnWidth = source.widths[c];
    if (source.formats[String(c)]) sheet.getRange(`${col}2:${col}${count}`).setNumberFormat(source.formats[String(c)]);
  }
  for (let r = 0; r < source.rows.length; r++) {
    const line = sheet.getRange(`A${r+2}:${lastCol}${r+2}`);
    if (r % 2 === 1) line.format.fill = '#F0F4F7';
    let lines = 1;
    for (let c = 0; c < source.headers.length; c++) {
      const value = source.rows[r][c];
      if (typeof value === 'string') {
        sheet.getRange(`${column(c)}${r+2}`).format.horizontalAlignment = value.length <= 24 ? 'center' : 'left';
        // Chinese glyphs occupy roughly two Latin character cells.
        const length = [...value].reduce((n, x) => n + (x.charCodeAt(0) > 255 ? 2 : 1), 0);
        lines = Math.max(lines, Math.ceil(length / Math.max(8, source.widths[c] - 3)));
      }
    }
    line.format.rowHeight = Math.max(29, 18 * lines + 12);
  }
  for (const [address, format] of Object.entries(source.cell_formats || {})) {
    sheet.getRange(address).setNumberFormat(format);
  }
}
wb.recalculate();
const summary = await wb.inspect({ kind:'sheet', include:'id,name', maxChars:3000 });
console.log(summary.ndjson);
const errors = await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:20},maxChars:2000});
console.log(errors.ndjson);
await fs.mkdir(path.join(output, 'logs/previews'), {recursive:true});
if (preview) {
  for (const source of data.sheets) {
    const end = Math.min(source.rows.length + 1, 12);
    const lastCol = column(source.headers.length - 1);
    const blob = await wb.render({sheetName:source.name,range:`A1:${lastCol}${end}`,scale:1.2,format:'png'});
    await fs.writeFile(path.join(output,'logs/previews',`${source.name}.png`),new Uint8Array(await blob.arrayBuffer()));
    console.log(`Rendered ${source.name}`);
  }
}
const result = await SpreadsheetFile.exportXlsx(wb);
await result.save(path.join(output, outputName));
await fs.writeFile(path.join(output,'logs/artifact_tool_summary.txt'),summary.ndjson+'\n'+errors.ndjson);
console.log(`Exported ${outputName}`);
