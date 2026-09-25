# 2026-09-25 追加试验原始记录

本目录补齐此前仅以报告和摘要呈现的本地与远端搜索证据。43 个 JSON/日志文件按原字节复制，共 17,399,024 字节；每个文件的来源、字节数和 SHA-256 见[实验来源清单](实验来源清单.json)。这些试验没有替换[正式推荐方案](../solution_recommended.json)。

| 试验 | 最终候选 | 完整验证 | 搜索过程 |
|---|---|---|---|
| 本地短试及续算 | [方案](本地搜索/solution_recommended.json) | [验证](本地搜索/validation_recommended.json) | [轮次记录](本地搜索/search_history.json)、[候选池](本地搜索/search_candidate_summary.json) |
| 远端延长搜索 | [方案](远端搜索/solution_recommended.json) | [远端验证](远端搜索/validation_recommended.json)、[拉回后的本地复验](远端搜索/validation_local_rerun.json) | [轮次记录](远端搜索/search_history.json)、[日志](远端搜索/run.log)、[候选池](远端搜索/search_candidate_summary.json) |

本地最终候选与验证通过 11899 项、0 错误；远端候选在远端及拉回本地后均通过 11902 项、0 错误。两份候选都是 21 个运输架次、3 个中继架次、66.44527328256846 kWh、联合完工 6612.283 s。正式方案保留 6612.284 s，其评分与上述候选的差异低于报告采用的解释界限。

`solution_recommended.json` 在这里表示各次试验自己的输出文件名，正式交付仍以本目录上一级的同名文件为准。`search_history.json` 包含热启动继承的早期搜索记录，不能把其中全部轮次都算成本次新增计算。本地目录还保留初次短试文件，各方案的实际阶段和搜索参数应读取各自 JSON 的 `search` 字段。

所有 `solution_*.json` 与 `validation_*.json`、站池、标签调整审计、候选池摘要，以及存在的运行日志均已归档。没有将原始附件、Python/Node 环境或完整远端机器快照加入本目录；本目录用于审查保存结果和搜索证据，不构成独立执行环境。
