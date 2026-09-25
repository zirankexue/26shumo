# 第三问改进版的运行说明

正式推荐及模型解释见[第三问说明](../README.md)和[算法报告](../结果/第三问_Agent算法复核与改进报告.md)。当前目录保存源码与成果，完整运行条件见[完整性检查](../../完整性检查.md)。本文件修复此前与源码索引重复、缺少运行命令的问题。

## 验证已有正式方案

下面命令只适用于已配置数据与环境的原工作区 `E:/sxjm/D题`。当前 GitHub 中文归档目录不是这个布局；不要仅改变当前目录后直接执行。

```powershell
Set-Location 'E:/sxjm/D题'
$env:Q3_SENIOR_ROOT = 'D:/Desktop/zirankexve'
python -X utf8 code/validate_q3_senior.py --solution results/question3_agent_update/solution_recommended.json --output results/question3_agent_update/validation_manual_review.json
```

上述命令重算保存结果的物理、资源、时限、能源与连续通信，并将新验证报告写入单独文件，不重新优化。原始附件、DEM、模板、师兄只读模型及配置必须与保存结果一致。

## 有限候选搜索

在已还原原布局的独立运行副本中，原入口可运行：

```powershell
python -X utf8 code/improve_q3_from_agents.py --seconds 65 --rounds 2 --pool-limit 350
```

这条命令还读取 `results/question3_from_senior` 中的基线最终方案、站池和 `solution_main.json`、`solution_enhanced.json`、`solution_zero_lateness.json`。相关历史文件没有完整复制到当前归档的默认路径，需要从原工作区准备。搜索会写入 `results/question3_agent_update`，运行副本应与正式交付目录分开。

扩大热启动搜索使用 `--resume`、`--skip-station-refinement`、`--rounds` 和 `--pool-limit`。本次本地续算为 3 轮、每阶段 30 秒、活动池 600；远端为 4 轮、每阶段 120 秒、活动池 1200，并分别做通信选项求解及固定结构精修。实际保存参数、继承历史和阶段结果见[追加试验原始记录](../结果/追加试验_20260925/README.md)，不能仅按历史数组长度推算新增计算次数。

## 环境与其他入口

- 求解和验证需要 Python、[Python 核心依赖清单](requirements_q3.txt)以及师兄只读公共模型。源码直接导入 `tomllib`；原本机 Python 3.12 可用，本次远端 Python 3.10 使用过临时兼容层，未把该兼容层发布为正式代码。
- `run_single_station_ablation.py` 原位置为 `results/question3_agent_update`，同目录需 `solution_deduplicated_seed.json` 和 `station_pool.json`。当前归档中的位置仅供源码审阅。
- `finalize_q3_agent_update.py` 使用 `before_station_normalization` 快照与相应候选摘要；不能用已经规范化的文件冒充规范化前输入。
- `export_q3_senior.mjs` 重导出需要 Node 和 `@oai/artifact-tool`。现有[正式工作簿](../输出/第三问_Agent改进联合调度.xlsx)可直接打开，查看它不需要导出环境。

所有限时搜索均受候选集合、零迟到约束与线程调度影响；保存可行结果的复验与逐字重现历史搜索输出是两种不同工作。13 份已归档算法/验证/导出源码未在此次完整性检查中修改。
