/** Export Q3 inheriting the senior Q2 physics, box scheduling and fixed scoring. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { FileBlob, SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const inputPath = path.resolve(process.argv[2] || path.join(project, 'results/question3_from_senior/solution_recommended.json'));
const outputPath = path.resolve(process.argv[3] || path.join(project, 'outputs/question3_from_senior/第三问_师兄基线联合调度.xlsx'));
const outputDir = path.dirname(outputPath);
const inputBytes = await fs.readFile(inputPath);
const sol = JSON.parse(inputBytes.toString('utf8'));
const assert = (ok, why) => { if (!ok) throw new Error(why); };
const close = (a, b, why) => assert(Number.isFinite(a) && Math.abs(a-b) <= 1e-7*Math.max(1,Math.abs(b)), `${why}: ${a} vs ${b}`);
const spec=sol.weighted_spec;
assert(spec && sol.physics?.gravity_m_s2===9.80665,'Require senior physics and frozen Q2 weighted specification.');
const metricKeys=['tardiness','makespan','energy','sorties'];
const seniorSummary=sol.senior_source?.summary;
assert(seniorSummary?.sorties===21,'Require verified senior Q2 comparison metadata.');
const seniorVector={tardiness:0,makespan:6361702,energy:63845040082,sorties:21};
const score=v=>metricKeys.reduce((a,k)=>a+spec.alpha[k]*(v[k]-spec.lower[k])/spec.span[k],0);
const fixedGrouping=Boolean(sol.search.fixed_plan)||/fixed|polish/.test(sol.search.label||'');
const maxRelayStations=Number(sol.communication_policy?.max_relay_stations_per_sortie??1);
assert(Number.isInteger(maxRelayStations)&&maxRelayStations>=1,'Invalid maximum relay-station count.');
const relayPolicy=maxRelayStations===1
  ?'每个运输架次最多使用1个固定后备中继站。'
  :`每个运输架次最多使用${maxRelayStations}个后备中继站，每个连续认证区段仅绑定一个在役中继。`;
const scope=sol.search.status==='OPTIMAL'&&fixedGrouping
  ?'仅固定最终运输分组、访问顺序与机型的有限站点排程子问题OPTIMAL；不是第三问全局最优。'
  :`有限运输候选与悬停站点${maxRelayStations>1?'组合':''}搜索中的可行解（${sol.search.status}）；未证明第三问全局最优。`;
assert(sol.feasible && sol.transport.length && sol.deliveries.length === 80, 'Require a feasible solution with 80 box deliveries.');
assert(new Set(sol.deliveries.map(x=>x.box)).size === 80, 'Duplicate box identifiers.');
assert(new Set(sol.transport.flatMap(x=>x.boxes)).size === 80, 'Transport box coverage differs from 80.');
const template = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(project,'结果提交模板.xlsx')));
const raw = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(project,'数据/无人机应急物资运输基础数据/运输无人机数据.xlsx')));
const rawRelay = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(project,'数据/无人机应急物资运输基础数据/中继无人机数据.xlsx')));
const params = raw.worksheets.getItem('数据').getRange('A3:R5').values;
const energyByModel = Object.fromEntries(params.map(r=>[r[0],Number(r[8])]));
const relayParams = rawRelay.worksheets.getItem('数据').getRange('A3:S3').values[0];
const relayFull = Number(rawRelay.worksheets.getItem('数据').getRange('C12').values[0][0]);
const relaySetupSeconds = Number(relayParams[10]);
const relayPowerWatts = Math.round((Number(relayParams[16])+Number(relayParams[17]))*1000);
const chargeTime = (soc,full) => soc<.9 ? full*(.65*(.9-soc)/.9+.35) : full*.35*(1-soc)/.1;
const rows = sol.transport, relays = sol.relay, deliveries = [...sol.deliveries].sort((a,b)=>a.box.localeCompare(b.box));
const nr=rows.length+1, rr=relays.length+1, dr=deliveries.length+1;
const routeMap = new Map(rows.map(x=>[x.id,x]));
const comm=[];
const routeRelayBindings={};
for (const route of rows) {
  const usedStations=new Set();
  for (const piece of route.communication) {
    const start=route.start_s+piece.start, end=route.start_s+piece.end;
    let relayId='';
    const station=piece.station??route.station;
    if (piece.relay) {
      assert(Number.isInteger(station)&&station>=0,`No valid relay station on ${route.id} ${start}-${end}`);
      const matches=relays.filter(r=>r.station===station && r.service_start_s<=start+1e-6 && r.service_end_s>=end-1e-6);
      assert(matches.length===1,`No unique active relay for ${route.id} ${start}-${end}`);
      relayId=matches[0].id;
      usedStations.add(station);
    }
    const key=[route.id,piece.kind,piece.leg.join('→'),piece.relay,relayId].join('|');
    const last=comm.at(-1);
    if(last && last.key===key && Math.abs(last.end-start)<1e-6) {
      last.end=end; last.margin=Math.min(last.margin,piece.min_margin_db); last.count++;
      last.proofs.add(piece.proof);
    } else comm.push({key,route:route.id,kind:piece.kind,leg:piece.leg.join('→'),start,end,
      mode:piece.relay?'直连优先，中继后备':'直连',relayId,station:piece.relay?station:null,margin:piece.min_margin_db,count:1,proofs:new Set([piece.proof])});
  }
  assert(usedStations.size<=maxRelayStations,`Relay station count exceeds policy for ${route.id}`);
  routeRelayBindings[route.id]=[...usedStations].sort((a,b)=>a-b);
}
const workbook=Workbook.create();
const names=['方案汇总','Q3_运输架次','逐箱交付','Q3_中继架次','Q3_通信保障','运输资源','算法说明'];
const sheets=Object.fromEntries(names.map(n=>[n,workbook.worksheets.add(n)]));
const ink='#25384A',navy='#213B56',pale='#F0F4F8',font='Microsoft YaHei';
const col=n=>{let s='';for(let x=n+1;x;x=Math.floor((x-1)/26))s=String.fromCharCode(65+(x-1)%26)+s;return s;};
function base(s,range){s.showGridLines=false;const f=s.getRange(range).format;f.font={name:font,size:10,color:ink};f.rowHeight=28;f.verticalAlignment='center';}
function width(s,ws){ws.forEach((w,i)=>s.getRange(`${col(i)}:${col(i)}`).format.columnWidthPx=w);}
function header(s,range){const f=s.getRange(range).format;f.fill=navy;f.font={name:font,size:10,bold:true,color:'#FFFFFF'};f.rowHeight=48;f.wrapText=true;f.horizontalAlignment='center';f.borders={insideVertical:{style:'thin',color:'#FFFFFF'}};}
function table(s,headers,data,ws){const last=data.length+1,c=col(headers.length-1);s.getRange(`A1:${c}${last}`).values=[headers,...data];base(s,`A1:${c}${last}`);width(s,ws);header(s,`A1:${c}1`);for(let r=3;r<=last;r+=2)s.getRange(`A${r}:${c}${r}`).format.fill=pale;s.freezePanes.freezeRows(1);s.freezePanes.freezeColumns(1);return last;}
function numeric(s,range,format='0.000'){s.getRange(range).setNumberFormat(format);s.getRange(range).format.horizontalAlignment='right';}
function errorFormat(s,range){s.getRange(range).conditionalFormats.add('containsText',{text:'超期',format:{fill:'#FCE4E4',font:{color:'#9B2222',bold:true}}});}
const transport=sheets['Q3_运输架次'];
const transportHeaders=[...template.worksheets.getItem('Q2_运输架次').getRange('A1:H1').values[0],
 '货箱编号列表','载质量（kg）','装载体积（m³）','实际起飞时刻（s）','架次作业时长（s）','电池可再用时刻（s）','电池能量（kWh）','返航SOC（%）','实体释放时刻（s）','评分用整数能耗'];
table(transport,transportHeaders,rows.map(r=>[r.id,r.drone,r.model,r.battery,r.start_s,r.order.join('→'),r.return_s,r.energy_kwh,r.boxes.join('; '),r.mass_kg,r.volume_m3,r.start_s+r.flight_start_s,r.duration_s,r.battery_ready_s,energyByModel[r.model],null,r.resource_end_s,null]),
 [86,100,80,95,124,198,142,133,400,120,132,150,144,162,136,115,169,172]);
transport.getRange(`I2:I${nr}`).format.wrapText=true;
rows.forEach((r,i)=>transport.getRange(`A${i+2}:R${i+2}`).format.rowHeight=Math.max(30,Math.ceil(r.boxes.join('; ').length/42)*17+8));
numeric(transport,`E2:E${nr}`);numeric(transport,`G2:H${nr}`);numeric(transport,`J2:Q${nr}`);numeric(transport,`K2:K${nr}`,'0.000');numeric(transport,`H2:H${nr}`,'0.000000');
for(let r=2;r<=nr;r++){transport.getRange(`P${r}`).formulas=[[`=(1-H${r}/O${r})*100`]];transport.getRange(`R${r}`).formulas=[[`=ROUND(H${r}*1000000000,0)`]];}
numeric(transport,`R2:R${nr}`,'0');

const delivery=sheets['逐箱交付'];
table(delivery,[...template.worksheets.getItem('Q2_逐箱交付').getRange('A1:D1').values[0],
 '物资类型','优先系数','期望送达（s）','首批硬截止（s）','医疗硬截止（s）','期望拖期（s）','硬时限拖期（s）','加权拖期（s）','硬时限状态','站内交付顺序'],
 deliveries.map(d=>[d.box,d.sortie,d.service,d.time_s,d.type,d.priority,d.due_s,d.first_deadline_s,d.type==='MED'?d.due_s:null,null,null,null,null,(routeMap.get(d.sortie).delivery_order[d.service]||[]).indexOf(d.box)+1]),
 [158,86,105,150,82,86,134,147,147,135,147,136,114,128]);
numeric(delivery,`N2:N${dr}`,'0');
numeric(delivery,`D2:D${dr}`);numeric(delivery,`F2:L${dr}`);numeric(delivery,`F2:F${dr}`,'0');
for(let r=2;r<=dr;r++)delivery.getRange(`J${r}:M${r}`).formulas=[[
 `=MAX(0,D${r}-G${r})`,
 `=MAX(0,IF(ISNUMBER(H${r}),D${r}-H${r},0),IF(ISNUMBER(I${r}),D${r}-I${r},0))`,
 `=F${r}*J${r}`,
 `=IF(AND(H${r}="",I${r}=""),"无硬时限",IF(K${r}>0.000001,"超期","满足"))`]];
errorFormat(delivery,`M2:M${dr}`);

const relaySheet=sheets['Q3_中继架次'];
table(relaySheet,[...template.worksheets.getItem('Q3_中继架次').getRange('A1:K1').values[0],
 '站点编号','悬停离地（m）','地面高程（m）','返航SOC（%）','周转后释放（s）','能源充满时刻（s）','回传裕量（dB）','评分用整数能耗','往返与建链能耗（kWh）','悬停与通信功率（W）'],
 relays.map(r=>[r.id,r.drone,r.component,r.start_s,r.station_data.lon,r.station_data.lat,r.station_data.z,r.service_start_s,r.service_end_s,r.return_s,null,r.station_data.id,r.station_data.agl_m,r.station_data.ground_m,r.soc_percent,r.resource_end_s,r.return_s+chargeTime(r.soc_percent/100,relayFull),r.station_data.backhaul.margin_db,null,r.station_data.travel_energy_kwh+relaySetupSeconds*relayPowerWatts/3600000,relayPowerWatts]),
 [108,115,110,126,139,139,139,151,145,150,141,100,142,139,134,151,165,139,172,180,172]);
for(let r=2;r<=rr;r++){
 relaySheet.getRange(`K${r}`).formulas=[[`=T${r}+ROUND((I${r}-H${r})*1000,0)*U${r}/3600000000`]];
 relaySheet.getRange(`S${r}`).formulas=[[`=ROUNDUP(T${r}*1000000000,0)+INT((ROUND(U${r},0)*ROUND((I${r}-H${r})*1000,0)*10+35)/36)`]];
}
if(rr>1)numeric(relaySheet,`S2:S${rr}`,'0');
if(rr>1){numeric(relaySheet,`T2:T${rr}`,'0.000000000');numeric(relaySheet,`U2:U${rr}`,'0');}
if(rr>1){numeric(relaySheet,`D2:K${rr}`);numeric(relaySheet,`E2:F${rr}`,'0.000000');numeric(relaySheet,`K2:K${rr}`,'0.000000');numeric(relaySheet,`M2:R${rr}`);numeric(relaySheet,`R2:R${rr}`,'0.0000');}

const communication=sheets['Q3_通信保障'];
const proofNames={distance_even_if_blocked:'最坏遮挡距离界',los_triangle:'三角面净空',terrain_triangle:'三角面净空',terrain_swept_triangle:'三角面净空'};
table(communication,[...template.worksheets.getItem('Q3_通信保障').getRange('A1:F1').values[0],
 '任务航段','连续认证方式','最低证书裕量（dB）','合并原始区间数'],
 comm.map(c=>[c.route,c.kind,c.start,c.end,c.mode,c.relayId,c.leg,[...c.proofs].map(p=>proofNames[p]||p).join('；'),c.margin,c.count]),
 [128,96,145,145,230,122,165,250,160,150]);
const cr=comm.length+1;numeric(communication,`C2:D${cr}`,'0.000000');numeric(communication,`I2:I${cr}`,'0.000000');numeric(communication,`J2:J${cr}`,'0');

const resource=sheets['运输资源'];
const resourceRows=[];
for(const r of rows){
 const soc=100*(1-r.energy_kwh/energyByModel[r.model]);
 resourceRows.push(['运输无人机',r.drone,r.id,r.model,r.start_s,r.return_s,r.resource_end_s,null,soc,'完整航段以1ms保守取整']);
 resourceRows.push(['共享电池',r.battery,r.id,r.model,r.start_s,r.return_s,r.battery_ready_s,r.battery_ready_s,soc,'充电至100%后复用；回充时间向上取整到1ms']);
}
for(const r of relays){
 const charged=r.return_s+chargeTime(r.soc_percent/100,relayFull);
 resourceRows.push(['中继无人机',r.drone,r.id,'R',r.start_s,r.return_s,r.resource_end_s,null,r.soc_percent,'返航后含300秒周转及整数余量']);
 resourceRows.push(['中继能源组件',r.component,r.id,'R',r.start_s,r.return_s,charged,charged,r.soc_percent,'每架次使用独立初始满电组件']);
}
resourceRows.sort((a,b)=>a[0].localeCompare(b[0])||a[1].localeCompare(b[1])||a[4]-b[4]);
table(resource,['资源类别','资源编号','任务架次','机型','占用开始（s）','物理返回（s）','资源可再用（s）','充满时刻（s）','返航SOC（%）','资源口径'],resourceRows,
 [140,102,96,78,138,138,151,144,131,320]);
numeric(resource,`E2:I${resourceRows.length+1}`);

const summary=sheets['方案汇总'];base(summary,'A1:D40');width(summary,[260,178,178,650]);summary.tabColor=navy;
summary.getRange('A2').values=[['第三问运输与中继联合调度']];summary.getRange('A2').format.font={name:font,size:14,bold:true,color:navy};
summary.getRange('A4:D4').values=[['指标','第三问推荐方案','师兄第二问参照','口径']];header(summary,'A4:D4');
const metrics=[
 ['交付货箱数',`=COUNTA('逐箱交付'!A2:A${dr})`,'每个货箱单独记录，不拆箱'],
 ['运输架次数',`=COUNTA('Q3_运输架次'!A2:A${nr})`,'包含多服务区架次'],
 ['中继架次数',rr>1?`=COUNTA('Q3_中继架次'!A2:A${rr})`:'=0','实体资源安排见运输资源表'],
 ['运输总能耗（kWh）',`=SUM('Q3_运输架次'!H2:H${nr})`,'逐段真实载荷能耗之和'],
 ['中继总能耗（kWh）',rr>1?`=SUM('Q3_中继架次'!K2:K${rr})`:'=0','含往返、服务及建链的保守能耗'],
 ['联合总能耗（kWh）','=SUM(B8:B9)','运输与中继之和'],
 ['联合完成时刻（s）',`=MAX('Q3_运输架次'!G2:G${nr}${rr>1?`,\'Q3_中继架次\'!J2:J${rr}`:''})`,'两类无人机最后物理返航的最晚时刻'],
 ['联合完成时间（h）','=B11/3600','从任务零时刻开始'],
 ['最后交付完成（s）',`=MAX('逐箱交付'!D2:D${dr})`,'按站内优化箱序逐箱完成交付计'],
 ['期望送达拖期箱数',`=COUNTIFS('逐箱交付'!J2:J${dr},">0.000001")`,'非医疗非首批的期望时间是软指标'],
 ['医疗或首批硬违约箱数',`=COUNTIFS('逐箱交付'!K2:K${dr},">0.000001")`,'同时适用两个时限时检查两者'],
 ['加权拖期（s）',`=SUM('逐箱交付'!L2:L${dr})`,'优先系数乘期望拖期'],
 ['运输最低返航SOC（%）',`=MIN('Q3_运输架次'!P2:P${nr})`,'题设下限20%'],
 ['中继最低返航SOC（%）',rr>1?`=MIN('Q3_中继架次'!O2:O${rr})`:'=0','题设下限20%'],
 ['通信证书区间数',`=SUM('Q3_通信保障'!J2:J${cr})`,'展示表合并相邻同阶段、航段与保障策略区间'],
 ['两类无人机总架次数','=SUM(B6:B7)','Q3同时计算运输与中继；Q2只有运输'],
 ['固定权重评分 F','=SUM(D28:D31)','权重3:1.5:1:0.5；沿用师兄Q2归一化尺度，分数越小越优'],
];
const q2Compare={5:80,6:21,8:seniorSummary.energy_kwh,10:seniorSummary.energy_kwh,11:seniorSummary.makespan_s,12:seniorSummary.makespan_s/3600,14:0,15:0,16:0,17:100*seniorSummary.min_soc,20:21,21:score(seniorVector)};
metrics.forEach(([label,f,note],i)=>{const r=i+5;summary.getRange(`A${r}:D${r}`).values=[[label,null,q2Compare[r]??null,`　${note}`]];summary.getRange(`B${r}`).formulas=[[f]];if(i%2)summary.getRange(`A${r}:D${r}`).format.fill=pale;});
numeric(summary,'B5:C21');[5,6,7,14,15,19,20].forEach(r=>numeric(summary,`B${r}:C${r}`,'0'));numeric(summary,'B8:C10','0.000000');numeric(summary,'B21:C21','0.000000000');
summary.getRange('A23:D23').values=[['求解状态',sol.search.status,'最优性范围',scope]];
summary.getRange('D23').format.wrapText=true;summary.getRange('A23:D23').format.rowHeight=42;
summary.getRange('A26').values=[['评分复算（固定Q2尺度）']];summary.getRange('A26').format.font={name:font,size:12,bold:true,color:navy};
summary.getRange('A27:D27').values=[['指标','当前原生整数值','归一化值','加权贡献']];header(summary,'A27:D27');
summary.getRange('A35:D35').values=[['固定评分参数','归一权重 alpha','基准下界','尺度跨度']];header(summary,'A35:D35');
const nativeFormulas=['=ROUND(B16*1000,0)','=ROUND(B11*1000,0)',`=SUM('Q3_运输架次'!R2:R${nr})${rr>1?`+SUM('Q3_中继架次'!S2:S${rr})`:''}`,'=B20'];
const metricLabels=['加权迟到（系数×ms）','联合完工（ms）','两类总能耗（10^-9 kWh）','两类总架次'];
metricKeys.forEach((k,i)=>{const r=28+i,p=36+i;summary.getRange(`A${r}:D${r}`).values=[[metricLabels[i],null,null,null]];summary.getRange(`B${r}:D${r}`).formulas=[[nativeFormulas[i],`=(B${r}-C${p})/D${p}`,`=B${p}*C${r}`]];summary.getRange(`A${p}:D${p}`).values=[[k,spec.alpha[k],spec.lower[k],spec.span[k]]];});
numeric(summary,'B28:B31','0');numeric(summary,'C28:D31','0.000000000');numeric(summary,'B36:B39','0.000000000');numeric(summary,'C36:D39','0');
summary.getRange('A40:D40').values=[['尺度来源','4个已归档Q2字典序解',null,'沿用师兄冻结尺度；不是Q3全局上下界。原生单位时间为毫秒，能耗为kWh×10^9。']];
summary.getRange('A40:D40').format.rowHeight=40;summary.getRange('D40').format.wrapText=true;

const explain=sheets['算法说明'];
const inputSources=Object.entries(sol.inputs||{}).map(([name,hash])=>['原始输入',name,hash]);
const notes=[
 ['继承基础','沿用师兄最新版Q1/Q2运输物理模型、逐箱CP-SAT箱序与固定评分。','从师兄Q2改进结果及候选池构造Q3，再联合安排运输起始时刻、中继位置与服务时段。'],
 ['通信执行策略','优先使用G01直连；直连不可用时，使用该连续区段指定的在役中继。',`${relayPolicy}接入与回传同时可用。`],
 ['中继证书含义','“直连优先，中继后备”表示整段具有可行后备中继证书。','不表示整段每一时刻实际都处于中继状态，未用离散采样冒称精确切换时刻。'],
 ['连续保障','几何线段采用最坏遮挡距离界或DEM三角面净空认证。','相邻同阶段/航段/指定中继的证书合并，最低裕量取原证书最小值。'],
 ['时间与逐箱交付','开始时刻含工位准备和装载，实际起飞另列；每个完整运输航段向上取整到1ms。','每站基础交接后按优化箱序逐箱完成交付；1ms量化等待也检查通信。联合完工取两类无人机最终返航。'],
 ['运输能源口径','水平能耗 Euse*d/L(q)，爬升附加项 (m0+q)*g*h/(3.6×10^6×eta)。','继承师兄审计解释，g=9.80665m/s²，载荷指数3/2。单位为kWh，不是实测标定的完整旋翼功率模型。'],
 ['电池周转','运输机实体在返航时释放；共享电池充至100%后复用，回充向上取整到1ms。','中继组件每架次用独立初始满电组；资源表同时列出按SOC推算的充满时刻。'],
 ['中继周转','同一中继返航后需300秒周转，随后才能开始下架次准备。','资源占用表包含周转；最后任务完工指标不包含末次周转或充电尾段。'],
 ['联合目标','加权迟到、两类无人机共同返航Cmax、运输与中继总能耗E、两类总架次N。','固定权重3:1.5:1:0.5和师兄Q2归一化尺度。F可以为负，越小越优；汇总表保留参数与逐项复算。'],
 ['整数能耗评分','运输能耗按师兄模型四舍五入到10^-9 kWh；中继固定与服务能耗分别向上取整。','中继每架次的双取整裕量小于2×10^-9 kWh；汇总评分沿用求解器原生整数值。'],
 ['搜索范围',`当前排程子问题运输候选 ${sol.search.route_pool_count}，候选站 ${sol.search.station_pool_count}。`,`扩池候选至多3个服务区。${relayPolicy}每站至多1中继架次。`],
 ['搜索限制','使用毫秒时间网格、有限候选池与悬停点，最多6个中继架次。',scope],
 ['策略',`${sol.search.label||sol.search.policy||'joint'}; seed=${sol.search.seed}; status=${sol.search.status}`,`求解耗时 ${Number(sol.search.seconds??sol.search.wall_s).toFixed(2)}秒。${scope}`],
 ['表格更新','修改表格数值会重算汇总和时限公式。','不会重新优化路线或验证通信；更换方案须重新运行求解与验证。'],
 ['方案来源',path.basename(inputPath),crypto.createHash('sha256').update(inputBytes).digest('hex')],
 ['师兄Q2来源','q2_improved/tables/final_result.json',sol.senior_source.sha256],
 ...inputSources,
 ['原始参数','运输电池能量与中继组件充电参数从本项目原始工作簿读取。','原始结果模板的运输/交付/中继/通信前置列名保持一致。'],
];
table(explain,['项目','方法或数据来源','适用条件或SHA-256'],notes,[160,680,720]);
explain.getRange(`B2:C${notes.length+1}`).format.wrapText=true;
for(let r=2;r<=notes.length+1;r++)explain.getRange(`A${r}:C${r}`).format.rowHeight=58;
explain.tabColor='#8796A5';

workbook.recalculate();
close(Number(summary.getRange('B5').values[0][0]),80,'Workbook box count');
close(Number(summary.getRange('B6').values[0][0]),sol.metrics.transport_sorties,'Transport count');
close(Number(summary.getRange('B7').values[0][0]),sol.metrics.relay_sorties,'Relay count');
close(Number(summary.getRange('B10').values[0][0]),sol.metrics.total_energy_kwh,'Total energy');
close(Number(summary.getRange('B11').values[0][0]),sol.metrics.joint_finish_s,'Joint completion');
close(Number(summary.getRange('B16').values[0][0]),sol.metrics.weighted_delay_s,'Weighted delay');
close(Number(summary.getRange('B21').values[0][0]),sol.metrics.score,'Frozen Q2 weighted score');
close(Number(summary.getRange('C21').values[0][0]),-0.05174036312804231,'Senior Q2 weighted score');
metricKeys.forEach((k,i)=>assert(Number(summary.getRange(`B${28+i}`).values[0][0])===sol.objective_vector[k],`Native metric ${k}: ${summary.getRange(`B${28+i}`).values[0][0]} vs ${sol.objective_vector[k]}`));
assert(Number(summary.getRange('B15').values[0][0])===0,'Hard deadline violations in workbook');
// Exercise a deadline formula through a real temporary input change, then restore.
const medIndex=deliveries.findIndex(x=>x.type==='MED'),testRow=medIndex+2;
const original=delivery.getRange(`D${testRow}`).values[0][0];
delivery.getRange(`D${testRow}`).values=[[deliveries[medIndex].due_s+1]];
assert(Number(delivery.getRange(`K${testRow}`).values[0][0])>=1,'Deadline formula does not recalculate.');
delivery.getRange(`D${testRow}`).values=[[original]];
// Exercise energy, SOC and F dependency chains, then restore before export.
const oldEnergy=transport.getRange('H2').values[0][0],oldScore=Number(summary.getRange('B21').values[0][0]);
const oldSoc=Number(transport.getRange('P2').values[0][0]);
transport.getRange('H2').values=[[oldEnergy+0.01]];
close(Number(summary.getRange('B21').values[0][0])-oldScore,spec.alpha.energy*10000000/spec.span.energy,'Energy-to-score perturbation');
assert(Number(transport.getRange('P2').values[0][0])<oldSoc,'SOC formula did not react to energy edit.');
transport.getRange('H2').values=[[oldEnergy]];
workbook.recalculate();
const inspections=[];
for(const name of names){const inspected=await workbook.inspect({kind:'table',range:`'${name}'!A1:${name==='方案汇总'?'D23':'F5'}`,include:'values,formulas',tableMaxRows:name==='方案汇总'?23:5,tableMaxCols:6,maxChars:4000});inspections.push({name,ndjson:inspected.ndjson});}
const errors=await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},summary:'Q3 formula error scan',maxChars:2500});
console.log(errors.ndjson);
await fs.mkdir(outputDir,{recursive:true});
const xlsx=await SpreadsheetFile.exportXlsx(workbook);await xlsx.save(outputPath);
const previews=[['方案汇总','A2:D23','summary'],['方案汇总','A26:D40','score'],['Q3_运输架次','A1:H10','transport'],['逐箱交付','A1:N10','deliveries'],['Q3_中继架次',`A1:K${Math.min(rr,9)}`,'relay'],['Q3_通信保障','A1:J12','communication'],['运输资源','A1:J12','resources'],['算法说明','A1:C15','method']];
const multiStationRoute=Object.keys(routeRelayBindings).find(id=>routeRelayBindings[id].length>1);
if(multiStationRoute){const positions=comm.map((c,i)=>c.route===multiStationRoute?i+2:null).filter(x=>x!==null);previews.push(['Q3_通信保障',`A${positions[0]}:J${positions.at(-1)}`,'communication_switch']);}
for(const [sheetName,range,name] of previews){const png=await workbook.render({sheetName,range,scale:1,format:'png'});await fs.writeFile(path.join(outputDir,`q3_${name}_preview.png`),new Uint8Array(await png.arrayBuffer()));}
await fs.writeFile(path.join(outputDir,'workbook_export_qa.json'),JSON.stringify({input:inputPath,input_sha256:crypto.createHash('sha256').update(inputBytes).digest('hex'),output:outputPath,counts:{boxes:deliveries.length,transport:rows.length,relay:relays.length,communication_merged:comm.length,communication_original:rows.reduce((n,r)=>n+r.communication.length,0),resource_rows:resourceRows.length,multi_station_transport:rows.filter(r=>routeRelayBindings[r.id].length>1).length},communication_policy:{max_relay_stations_per_sortie:maxRelayStations,direct_priority:true},route_relay_bindings:routeRelayBindings,relay_binding_check:'Each original interval is bound to its own station and one active relay; policy count enforced.',formula_error_scan:errors.ndjson,deadline_perturbation_test:'passed and restored',energy_soc_score_perturbation_test:'passed and restored',score:sol.metrics.score,senior_q2_score:score(seniorVector),inspections},null,2),'utf8');
console.log(JSON.stringify({outputPath,boxes:80,transport:rows.length,relay:relays.length,energy_kwh:sol.metrics.total_energy_kwh,joint_finish_s:sol.metrics.joint_finish_s,score:sol.metrics.score,communication_rows:comm.length},null,2));
