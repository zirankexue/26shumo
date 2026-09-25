/** Q4 result workbook. All mathematical search lives in solve_q4.py. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { FileBlob, SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const { values: options }=parseArgs({options:{results:{type:'string'},output:{type:'string'},previews:{type:'string'}}});
const results=path.resolve(options.results??path.join(root,'第四问/结果'));
const output=path.resolve(options.output??path.join(root,'outputs/question4_partition_20260925'));
const previewDir=path.resolve(options.previews??path.join(path.dirname(results),'图片'));
await fs.mkdir(output,{recursive:true});
await fs.mkdir(previewDir,{recursive:true});
const read=async p=>JSON.parse(await fs.readFile(p,'utf8'));
const d=await read(path.join(results,'solution_q4.json'));
const q3=await read(path.join(path.dirname(results),'数据/q3_frozen_solution.json'));
const validation=await read(path.join(results,'validation_q4.json'));
const all=await read(path.join(results,'all_partitions.json'));
const source=await read(path.join(results,'input_metadata.json'));
const assert=(test,why)=>{if(!test) throw new Error(why);};
const close=(a,b,why)=>assert(typeof a==='number'&&Math.abs(a-b)<1e-7,`${why}: ${a} / ${b}`);
const hash=crypto.createHash('sha256').update(await fs.readFile(path.join(results,'solution_q4.json'))).digest('hex');
assert(validation.passed&&validation.solution_sha256===hash,'Require hash-bound passing Q4 validation');
const keys=d.resource_keys;
const shortage=(rec,resources=keys)=>resources.filter(r=>rec.gap[r]).map(r=>`${d.resource_labels[r]} +${rec.gap[r]}`).join('；')||'无';
const color={navy:'#233B57', pale:'#EAF0F6', ink:'#172B40', muted:'#526579', red:'#A32626'};
const font='Microsoft YaHei';
const wb=Workbook.create();
const names=['结果比较','Q4_分区配置','任务明细','资源占用','分区备选','库存与口径'];
const sh=Object.fromEntries(names.map(n=>[n,wb.worksheets.add(n)]));
const col=n=>{let s='';while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26);}return s;};
const serviceLines=services=>Array.from({length:Math.ceil(services.length/6)},(_,i)=>services.slice(i*6,i*6+6).join('、')).join('\n');
const write=(s,row,data)=>{if(data.length)s.getRangeByIndexes(row-1,0,data.length,data[0].length).values=data;};
const init=(s,title,end,rows,widths)=>{
  s.showGridLines=false;
  const r=s.getRange(`A1:${end}${rows}`);
  r.format.font={name:font,size:10,color:color.ink};
  r.format.rowHeight=24;r.format.verticalAlignment='center';
  s.getRange('A2').values=[[title]];s.getRange('A2').format.font={name:font,size:15,bold:true,color:color.navy};
  s.getRange(`A3:${end}3`).format.borders={bottom:{style:'thin',color:'#B8C6D6'}};
  for(let i=0;i<widths.length;i++)s.getRange(`${col(i+1)}1:${col(i+1)}${rows}`).format.columnWidth=widths[i];
};
const table=(s,row,headers,data,tableName)=>{
  write(s,row,[headers,...data]);
  const top=s.getRange(`A${row}:${col(headers.length)}${row}`);
  top.format={fill:color.navy,font:{name:font,size:10,bold:true,color:'#FFFFFF'},wrapText:true,
    horizontalAlignment:'center',verticalAlignment:'center',rowHeight:38};
  if(data.length){
    const t=s.tables.add(`A${row}:${col(headers.length)}${row+data.length}`,true,tableName);
    t.showFilterButton=true;
    // Table styles must not override our deliberately compact header.
    top.format.fill=color.navy;top.format.font={name:font,size:10,bold:true,color:'#FFFFFF'};
    for(let r=0;r<data.length;r++)if(r%2===1)s.getRange(`A${row+1+r}:${col(headers.length)}${row+1+r}`).format.fill='#F3F6F9';
  }
};
const groupOf=(k,tid,relay=false)=>{
  const gs=d.recommendations[`strict_${k}_minimum`].group_details;
  const i=gs.findIndex(g=>g[relay?'relay':'transport'].includes(tid));
  assert(i>=0,`Task not assigned ${tid}`);return `G${i+1}`;
};

// Frozen source tasks. Durations are calculated from original timestamps.
const tasks=[...q3.transport.map(t=>['运输',t.id,t.order.join(' → '),t.model,groupOf(2,t.id),groupOf(3,t.id),t.boxes.length,t.start_s,t.return_s,null,t.energy_kwh,
  Object.entries(d.relay_transport_edges).filter(([,ts])=>ts.includes(t.id)).map(([r])=>r).join('、')||'直连']),
  ...q3.relay.map(t=>['中继',t.id,d.relay_transport_edges[t.id].join('、'),'R',groupOf(2,t.id,true),groupOf(3,t.id,true),0,t.start_s,t.return_s,null,t.energy_kwh,`站${t.station}`])];
const taskEnd=tasks.length+4, relayStart=q3.transport.length+5;
init(sh.任务明细,'第三问固定任务与两套分组','L',taskEnd+3,[10,11,45,9,12,12,9,15,15,17,17,19]);
table(sh.任务明细,4,['类别','任务编号','访问顺序或保障任务','机型','2组归属','3组归属','货箱数','准备开始/s','返航/s','作业量/机·秒','能耗/kWh','实际中继'],tasks,'FrozenTasks');
sh.任务明细.getRange('J5').formulas=[['=ROUND(I5-H5,3)']];sh.任务明细.getRange(`J5:J${taskEnd}`).fillDown();
sh.任务明细.getRange(`H5:K${taskEnd}`).setNumberFormat('0.000');
sh.任务明细.getRange(`K5:K${taskEnd}`).setNumberFormat('0.000000');
sh.任务明细.getRange(`C${relayStart}:C${taskEnd}`).format.wrapText=true;sh.任务明细.getRange(`A${relayStart}:L${taskEnd}`).format.rowHeight=56;
sh.任务明细.freezePanes.freezeRows(4);
sh.任务明细.getRange(`A${taskEnd+2}`).values=[['来源：本版本冻结第三问输入；中继关联来自实际在岗通信区段。']];

// Template interface: original first 11 headers exactly, followed by group workload.
const template=await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(root,'结果提交模板.xlsx')));
const officialHeaders=template.worksheets.getItem('Q4_分区配置').getRange('A1:K1').values[0];
const configs=[];
for(const k of [2,3])for(const [i,g] of d.recommendations[`strict_${k}_minimum`].group_details.entries())
  configs.push([k,`G${i+1}`,serviceLines(g.services),...keys.map(r=>g.resource_minimum[r]),null,null,null,null,g.population,g.mass_kg]);
init(sh.Q4_分区配置,'第四问主方案分区配置','Q',12,[11,12,53,13,13,13,13,13,13,12,14,12,11,19,19,14,14]);
table(sh.Q4_分区配置,1,[...officialHeaders,'运输架次数','货箱数','运输作业量/机·秒','中继作业量/机·秒','保障人口/人','货箱质量/kg'],configs,'Q4Configuration');
// Supplied template starts at A1, so remove the presentation title placed in data.
for(let i=0;i<configs.length;i++){
  const r=i+2,k=configs[i][0],groupColumn=k===2?'E':'F';
  sh.Q4_分区配置.getRange(`L${r}:O${r}`).formulas=[[
    `=COUNTIFS('任务明细'!$A$5:$A$${taskEnd},"运输",'任务明细'!$${groupColumn}$5:$${groupColumn}$${taskEnd},B${r})`,
    `=SUMIFS('任务明细'!$G$5:$G$${taskEnd},'任务明细'!$A$5:$A$${taskEnd},"运输",'任务明细'!$${groupColumn}$5:$${groupColumn}$${taskEnd},B${r})`,
    `=SUMIFS('任务明细'!$J$5:$J$${taskEnd},'任务明细'!$A$5:$A$${taskEnd},"运输",'任务明细'!$${groupColumn}$5:$${groupColumn}$${taskEnd},B${r})`,
    `=SUMIFS('任务明细'!$J$5:$J$${taskEnd},'任务明细'!$A$5:$A$${taskEnd},"中继",'任务明细'!$${groupColumn}$5:$${groupColumn}$${taskEnd},B${r})`]];
}
sh.Q4_分区配置.getRange('C2:C6').format.wrapText=true;
sh.Q4_分区配置.getRange('A2:Q6').format.font={name:font,size:10,color:color.ink};
sh.Q4_分区配置.getRange('A2:Q6').format.rowHeight=56;
sh.Q4_分区配置.getRange('N2:O6').setNumberFormat('0.000');
sh.Q4_分区配置.getRange('D2:M6').setNumberFormat('0');
sh.Q4_分区配置.getRange('P2:Q6').setNumberFormat('0');
sh.Q4_分区配置.getRange('A8').values=[['主口径：原中继架次不拆分、不复制。资源缺口见“结果比较”。']];
sh.Q4_分区配置.getRange('A9').values=[['前11列与题给Q4提交模板一致；原模板文件未改。']];
sh.Q4_分区配置.tabColor=color.navy;

// Inventory and source definitions. Sources are kept beside actual inputs.
init(sh.库存与口径,'库存、基线与核算口径','E',32,[27,10,14,16,82]);
table(sh.库存与口径,4,['资源','单位','现有库存','未分组最低需求','来源'],keys.map(r=>[d.resource_labels[r],r.startsWith('B_')||r==='RE'?'组':'架',d.inventory[r],d.global_group.resource_minimum[r],
  r.startsWith('U_')?'运输无人机数据.xlsx 数据!A9:C16':r.startsWith('B_')?'运输无人机数据.xlsx 数据!A20:C22':r==='R'?'中继无人机数据.xlsx 数据!A7:C8':'中继无人机数据.xlsx 数据!A12:C12']),'ResourceInventory');
const method=[
 ['第三问基线',path.relative(root,d.source_solution)],
 ['方案指纹',d.source_solution_sha256],
 ['中继主口径','每个原中继架次仅执行一次，相关运输任务必须同组。'],
 ['复制扩展','每组复制完整中继时段与位置，会增加物理架次和能耗，仅作敏感性。'],
 ['机体占用','运输机从准备至释放；中继机从准备至返航加300秒周转。'],
 ['能源占用','准备开始至返航后充至100%；同型可在组内复用，组间禁止调配。'],
 ['资源计数','半开区间最大重叠；推荐组内保持原编号复用关系，计数恰好达到下界。'],
 ['缺口定义','各组独立需求之和减去一份全局库存，负值取0。'],
 ['排序优先级','飞机缺口 → 能源缺口 → 飞机配置 → 能源配置 → 运输工作量CV。'],
 ['工作量','运输准备至返航持续时间之和，单位为无人机·秒；CV越小越均衡。'],
 ['精确性范围','给定第三问、给定中继规则及声明排序下精确枚举；不代表Q3全局最优。'],
 ['完整候选',`全部${all.length}条评价记录见本版本结果目录的all_partitions.csv。`]];
for(const [i,[key,text]] of method.entries()){
  sh.库存与口径.getRange(`A${15+i}`).values=[[key]];
  sh.库存与口径.getRange(`E${15+i}`).values=[[text]];
  sh.库存与口径.getRange(`E${15+i}`).format.wrapText=true;
  sh.库存与口径.getRange(`A${15+i}:E${15+i}`).format.rowHeight=38;
}

// Main comparison, formulas link to finished group configurations.
init(sh.结果比较,'第四问：独立分区增加多少资源','J',36,[26,10,12,16,13,13,13,13,13,13]);
table(sh.结果比较,4,['资源','单位','现有库存','未分组需求','2组需求','2组冗余','2组缺口','3组需求','3组冗余','3组缺口'],keys.map((r,i)=>[d.resource_labels[r],r.startsWith('B_')||r==='RE'?'组':'架',null,null,null,null,null,null,null,null]),'Comparison');
for(let i=0;i<keys.length;i++){
  const r=i+5,q=col(i+4);
  sh.结果比较.getRange(`C${r}:J${r}`).formulas=[[
    `='库存与口径'!C${r}`,`='库存与口径'!D${r}`,`=SUM('Q4_分区配置'!${q}2:${q}3)`,`=E${r}-D${r}`,`=MAX(0,E${r}-C${r})`,
    `=SUM('Q4_分区配置'!${q}4:${q}6)`,`=H${r}-D${r}`,`=MAX(0,H${r}-C${r})`]];
}
sh.结果比较.getRange('C5:J12').setNumberFormat('0');
for(const address of ['G5:G12','J5:J12'])sh.结果比较.getRange(address).conditionalFormats.add('cellIs',{operator:'greaterThan',formula:0,format:{fill:'#FCE7E7',font:{color:color.red,bold:true}}});
write(sh.结果比较,15,[['指标','2组','3组']]);
sh.结果比较.getRange('A15:C15').format={fill:color.pale,font:{bold:true,color:color.navy}};
const summaryLabels=['运输无人机/架','共享电池/组','中继无人机/架','中继组件/组','运输工作量CV','联合完成/s','总能耗/kWh'];
for(let i=0;i<summaryLabels.length;i++)sh.结果比较.getRange(`A${16+i}`).values=[[summaryLabels[i]]];
sh.结果比较.getRange('B16:C19').formulas=[['=SUM(E5:E7)','=SUM(H5:H7)'],['=SUM(E8:E10)','=SUM(H8:H10)'],['=E11','=H11'],['=E12','=H12']];
sh.结果比较.getRange('B20').formulas=[["=SQRT(2*SUMSQ('Q4_分区配置'!N2:N3)/SUM('Q4_分区配置'!N2:N3)^2-1)"]];
sh.结果比较.getRange('C20').formulas=[["=SQRT(3*SUMSQ('Q4_分区配置'!N4:N6)/SUM('Q4_分区配置'!N4:N6)^2-1)"]];
sh.结果比较.getRange('B21:C22').formulas=[[`=MAX('任务明细'!I5:I${taskEnd})`,"=B21"],[`=SUM('任务明细'!K5:K${taskEnd})`,"=B22"]];
sh.结果比较.getRange('B20:C22').setNumberFormat('0.000000');
sh.结果比较.getRange('B21:C21').setNumberFormat('0.000');
const notes=[
 `2组新增需求：${shortage(d.recommendations.strict_2_minimum)}。`,
 `3组新增机体：${shortage(d.recommendations.strict_3_minimum,['U_A','U_B','U_C','R'])}。`,
 `3组新增能源：${shortage(d.recommendations.strict_3_minimum,['B_A','B_B','B_C','RE'])}。`,
 '需补齐缺口后才能按第三问原时刻执行；表中未把待补资源当成现有库存。',
 `严格口径保留本版第三问${q3.transport.length}个运输、${q3.relay.length}个中继架次及通信关系。`,
 `基线目录：${path.basename(path.dirname(d.source_solution))}；分组见“Q4_分区配置”。`];
for(let i=0;i<notes.length;i++)sh.结果比较.getRange(`A${25+i}`).values=[[notes[i]]];
sh.结果比较.tabColor=color.navy;

// Actual physical resources for primary, including supply gaps.
const csv=await fs.readFile(path.join(results,'resource_assignments.csv'),'utf8');
const parsed=csv.trim().replace(/^\uFEFF/,'').split(/\r?\n/).map(line=>line.split(','));
const allocations=parsed.slice(1).map(r=>[Number(r[0]),r[1],d.resource_labels[r[2]],r[3],r[7],r[4],r[8]==='existing'?'现有':'待补',Number(r[5]),Number(r[6]),null]);
init(sh.资源占用,'分组资源编号与占用时段','J',allocations.length+6,[8,10,23,22,19,12,10,15,15,17]);
table(sh.资源占用,4,['K','任务组','资源类型','执行资源编号','第三问原编号','任务编号','来源','占用开始/s','释放或充满/s','占用时长/s'],allocations,'ResourceAssignments');
sh.资源占用.getRange('J5').formulas=[['=ROUND(I5-H5,3)']];sh.资源占用.getRange(`J5:J${allocations.length+4}`).fillDown();
sh.资源占用.getRange(`H5:J${allocations.length+4}`).setNumberFormat('0.000');
sh.资源占用.getRange(`G5:G${allocations.length+4}`).conditionalFormats.add('containsText',{text:'待补',format:{fill:'#FCE7E7',font:{color:color.red,bold:true}}});
sh.资源占用.freezePanes.freezeRows(4);

// All strict partitions + distinct expanded objective choices.
const chosen=all.filter(r=>r.policy==='strict'&&r.identity==='minimum').map(r=>({...r,selection:'完整候选'}));
for(const k of [2,3]){
  chosen.push({...d.recommendations[`clone_${k}_minimum`],selection:'资源优先'});
  chosen.push({...d.recommendations[`clone_${k}_minimum`].balance_first,selection:'均衡优先'});
}
const alternatives=chosen.map(r=>[r.policy==='strict'?'原中继不复制':'允许完整中继复制',r.K,r.selection,r.groups.map((g,i)=>`G${i+1}: ${serviceLines(g)}`).join('\n'),...keys.map(k=>r.total[k]),r.workload_cv,r.relay_sorties_executed,r.total_energy_kwh]);
init(sh.分区备选,'分区目标权衡与中继复制敏感性','O',alternatives.length+8,[23,8,13,64,11,11,11,11,11,11,12,14,14,13,18]);
table(sh.分区备选,4,['中继口径','K','目标偏好','任务分区','运输A','运输B','运输C','电池A','电池B','电池C','中继机','中继组件','运输CV','中继架次','总能耗/kWh'],alternatives,'Alternatives');
sh.分区备选.getRange(`D5:D${alternatives.length+4}`).format.wrapText=true;
sh.分区备选.getRange(`A5:O${alternatives.length+4}`).format.rowHeight=94;
sh.分区备选.getRange(`M5:M${alternatives.length+4}`).setNumberFormat('0.000000');
sh.分区备选.getRange(`O5:O${alternatives.length+4}`).setNumberFormat('0.000000');
sh.分区备选.getRange(`A${alternatives.length+6}`).values=[['完整中继复制需要额外假设，增加实际架次及能耗，不作为严格主方案。']];
sh.分区备选.getRange(`A${alternatives.length+7}`).values=[['每类缺口为MAX(0,本列配置数－库存)。全部枚举数据另存CSV。']];

wb.recalculate();
for(let i=0;i<keys.length;i++){
 const row=sh.结果比较.getRange(`C${i+5}:J${i+5}`).values[0];
 const two=d.recommendations.strict_2_minimum,three=d.recommendations.strict_3_minimum,r=keys[i];
 [d.inventory[r],d.global_group.resource_minimum[r],two.total[r],two.redundancy_vs_global_minimum[r],two.gap[r],three.total[r],three.redundancy_vs_global_minimum[r],three.gap[r]].forEach((v,j)=>close(row[j],v,`Summary ${r}/${j}`));
}
close(sh.结果比较.getRange('B20').values[0][0],d.recommendations.strict_2_minimum.workload_cv,'CV2');
close(sh.结果比较.getRange('C20').values[0][0],d.recommendations.strict_3_minimum.workload_cv,'CV3');
// A real formula update check: temporarily add one C transport unit to inventory.
const originalInventory=sh.库存与口径.getRange('C7').values[0][0];
sh.库存与口径.getRange('C7').values=[[originalInventory+1]];
close(sh.结果比较.getRange('G7').values[0][0],Math.max(0,d.recommendations.strict_2_minimum.total.U_C-originalInventory-1),'Live inventory shortage formula');
sh.库存与口径.getRange('C7').values=[[originalInventory]];
wb.recalculate();close(sh.结果比较.getRange('G7').values[0][0],d.recommendations.strict_2_minimum.gap.U_C,'Restore inventory');
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:40},maxChars:3000,summary:'Q4 formula error scan'});
await fs.writeFile(path.join(output,'formula_inspection.ndjson'),errors.ndjson);
console.log(errors.ndjson);
const views=[['结果比较','A1:J31','summary'],['Q4_分区配置','A1:K9','template'],['Q4_分区配置','L1:Q6','workload'],
 ['任务明细',`A1:L${Math.min(taskEnd,16)}`,'tasks'],['任务明细',`A${relayStart-2}:L${taskEnd+2}`,'relay_tasks'],['资源占用','A1:J19','resources'],
 ['分区备选',`A1:D${alternatives.length+5}`,'alternatives_groups'],['分区备选',`E4:O${alternatives.length+4}`,'alternatives_counts'],['库存与口径','A1:E27','inputs']];
for(const [sheet,range,name] of views){
 const blob=await wb.render({sheetName:sheet,range,scale:1.4,format:'png'});
 await fs.writeFile(path.join(previewDir,`q4_${name}.png`),new Uint8Array(await blob.arrayBuffer()));
 console.log('rendered',name);
}
const file=path.join(output,'第四问_任务分区与资源配置.xlsx');
await (await SpreadsheetFile.exportXlsx(wb)).save(file);
await fs.writeFile(path.join(output,'workbook_export_qa.json'),JSON.stringify({source_solution_sha256:hash,
 q3_sha256:d.source_solution_sha256,inventory_change_recalculation:true,output_sha256:crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex'),
 views:views.map(([sheet,range,name])=>({sheet,range,name})),sheets:names},null,2));
console.log(file);
