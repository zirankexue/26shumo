// Reuse the verified artifact-tool renderer; Q1's default output is unchanged.
process.argv[4] = '问题二结果.xlsx';
await import('./export_q1.mjs');
