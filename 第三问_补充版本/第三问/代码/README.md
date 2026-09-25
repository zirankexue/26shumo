# 第三问代码说明

本目录保存本次实际使用的第三问源代码快照。核心入口和职责如下：

| 文件 | 作用 |
|---|---|
| q3_geometry.py | DEM、链路和连续通信几何 |
| solve_q3_senior.py | 沿用师兄运输物理、逐箱排序、机体/电池资源和固定评分，联合安排运输与中继 |
| q3_senior_adapter.py | 只读加载师兄第二问来源，并核对原始输入哈希 |
| validate_q3_senior.py | 独立复算运输、中继、时限、资源、SOC和连续通信 |
| q3_multi_profiles.py | 生成单站及有限双站连续通信候选，逐区段记录实际 station |
| improve_q3_from_agents.py | 合并结构种子、定向选站和有限邻域搜索 |
| finalize_q3_agent_update.py | 站点坐标去重、固定结构精修和最终冻结 |
| q3_refine_timing.py | 等价真实箱号的硬时限标签后处理 |
| export_q3_senior.mjs | 导出并检查第三问 Excel |
| run_single_station_ablation.py | 同结构单站对照实验入口 |
| q3_transport.py / solve_q3_independent.py / solve_q1_batching.py | 运输、通信和公共几何依赖 |
| requirements_q3.txt | 已验证的 Python 核心版本 |

源码中的 ROOT 和输入路径按原工作区布局编写。本 GitHub 补充目录不携带题目原始附件、DEM、运行时和师兄源码，因此这里的源码主要用于审阅与溯源；需要完整重跑时，应在原 E:\sxjm\D题 工作区准备附件，并将 Q3_SENIOR_ROOT 指向只读的师兄工程。冻结结果可以直接阅读和用结果目录中的验证 JSON 审计。

第三问使用的公共口径、有限候选边界、站点去重及运行命令见 [第三问说明](../README.md) 和 [最终报告](../结果/第三问_Agent算法复核与改进报告.md)。
