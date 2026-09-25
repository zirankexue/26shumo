# 第三问结果：正式方案与对照记录

正文最终答案只使用当前目录的 [solution_recommended.json](solution_recommended.json)、[validation_recommended.json](validation_recommended.json) 和[对应 Excel](../输出/第三问_Agent改进联合调度.xlsx)。总能耗为 **66.44527328256846 kWh**，联合完工为 **6612.284 s**。完整数值与来源见[写作统一口径](../../第三问_写作统一口径.md)。

| 位置 | 用途 | 是否为正文最终答案 |
|---|---|---|
| 当前目录 `solution_recommended.json` | 唯一正式方案 | 是 |
| [基线对照](基线对照/README.md) | 上一轮第三问，用于改进前后比较 | 否 |
| [单站对照](ablation_summary.json) | 同运输结构的单站后备消融 | 否 |
| [本地追加试验](追加试验_20260925/本地搜索/README.md) | 本地搜索候选和中间记录 | 否 |
| [远端追加试验](追加试验_20260925/远端搜索/README.md) | 远端搜索候选和中间记录 | 否 |

同名文件保留了运行时的原始文件名，不能根据 `recommended`、`best` 或 `final` 字样跨目录挑选“最新答案”。报告中的验证次数、箱序、通信时刻应与同一版本的方案一起使用。
