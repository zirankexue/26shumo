# 第三问独立求解：算法文献核验与适用边界

检索与核验日期：2026-09-24。本文服务于第三问独立建模，不依赖问题二的求解结果，也不声称复现下列论文。共纳入8篇真实文献；7篇的算法由公开摘要或本地作者稿核验，第6篇只完成元数据与题名中的方法核验。

## 1. 检索与证据口径

采用 `nature-academic-search` 的 `multi-source-search` 流程。当前环境未挂载 academic-search MCP，因此使用公开HTTP API：先查Crossref与arXiv，随后借助OpenAlex发现和交叉核验，并访问DTU、Springer、RePEc、OSTI及arXiv官方网页。关键词覆盖多架次、异构机队、载荷能耗、共享电池/换电、ALNS、Benders、通信轨迹与中继。按规范化DOI去重；同题同作者的arXiv稿与正式期刊稿合并。

实际访问限制：Crossref部分并行关键词请求返回HTTP 429，后续直接DOI查询成功；arXiv检索API返回HTTP 406，但官方摘要页可以访问；ScienceDirect的Bruni论文页面返回HTTP 403。因此下面明确区分“原文/摘要可核验”与“只有题名可核验”，不把方法猜测写成论文事实。精确查询词、来源URL、元数据快照、短证据摘录及失败记录见同目录 `literature_evidence.json`。

## 2. 核验的8篇文献

| 编号 | 文献与正式发表年份 | 已核验方法 | 对第三问的用途 | 核验深度 |
|---|---|---|---|---|
| L01 | Dorling等，*Vehicle Routing Problems for Drone Delivery*，2017。[DOI](https://doi.org/10.1109/TSMC.2016.2582745) | 多架次MILP、模拟退火SA、载荷/电池质量相关能耗 | 架次复用、载荷逐站递减、路线选择与实体无人机排程 | 本轮复核本地作者稿第1、5、9、10页；Crossref与OpenAlex |
| L02 | Sacramento、Pisinger、Ropke，*An adaptive large neighborhood search metaheuristic for the vehicle routing problem with drones*，2019。[DOI](https://doi.org/10.1016/j.trc.2019.02.018) | 数学模型、ALNS | 联合修改组批、访问序、起飞时间和中继服务窗的搜索框架 | Crossref及DTU官方研究数据库摘要 |
| L03 | Torabbeigi、Lim、Kim，*Drone Delivery Scheduling Optimization Considering Payload-induced Battery Consumption Rates*，2020。[DOI](https://doi.org/10.1007/s10846-019-01034-w) | 战略层最小集合覆盖、运营层MILP、变量预处理、原始/对偶界 | 候选覆盖与调度分层，按载荷检查返航电量 | Crossref及Springer公开摘要 |
| L04 | Cheng、Adulyasak、Rousseau，*Drone routing with energy function: Formulation and exact algorithm*，2020。[DOI](https://doi.org/10.1016/j.trb.2020.06.011) | 二索引模型、凸非线性能耗、逻辑割/次梯度割、branch-and-cut | 精确能耗可行性筛选、不可行候选删除、精确子问题思路 | Crossref及RePEc公开摘要 |
| L05 | Cokyasar、Dong、Jin、Verbas，*Designing a drone delivery network with automated battery swapping machines*，2021。[DOI](https://doi.org/10.1016/j.cor.2020.105177) | 含换电等待时间的MINLP、导数支持的割平面法 | 说明能源补充及等待过程应显式纳入决策 | Crossref及美国能源部OSTI摘要 |
| L06 | Bruni、Khodaparasti、Moshref-Javadi，*A logic-based Benders decomposition method for the multi-trip traveling repairman problem with drones*，2022。[DOI](https://doi.org/10.1016/j.cor.2022.105845) | 仅核验题名明确的logic-based Benders decomposition | 作为主问题—资源/通信可行性子问题的进一步方法入口 | **仅Crossref、OpenAlex及Elsevier元数据；未核验其分解细节** |
| L07 | Zeng、Zhang、Lim，*Throughput Maximization for UAV-Enabled Mobile Relaying Systems*，2016。[DOI](https://doi.org/10.1109/TCOMM.2016.2611512) | 功率与中继轨迹交替优化，轨迹子问题逐次凸优化 | 运输排程与中继位置/服务窗交替优化的结构依据 | Crossref及OpenAlex收录摘要 |
| L08 | Wu、Zeng、Zhang，*Joint Trajectory and Communication Design for Multi-UAV Enabled Wireless Networks*，2018。[DOI](https://doi.org/10.1109/TWC.2017.2789293)；[arXiv](https://arxiv.org/abs/1705.02723) | BCD与successive convex optimization；调度关联、轨迹、功率交替优化 | 解释几何位置与通信分配的耦合；支持先候选点离散、再排程优化 | Crossref、OpenAlex及arXiv官方摘要 |

日期处理：L01作者稿为2016年、期刊为2017年；L03于2019年在线、期刊卷期为2020年3月；L05的OSTI记录为2020-12-17，而Crossref正式卷期为2021年5月、129卷、105177；L08首个arXiv稿为2017年、期刊为2018年。上表优先采用正式卷期年份，未将预印本和期刊版重复计数。

## 3. 可以借鉴的结构与不能照搬的约束

### 3.1 多架次与载荷能耗：L01、L03、L04

这三篇能够支撑“无人机可以复用、载荷随交付变化、航程或电量可行性必须逐段计算”的建模取向。第三问适合把每个运输候选架次保存为货箱集合、访问顺序、机型、实际分段载荷、能耗、交付相对时刻和通信需求区间，再进行资源排程。

但L01假定任务前已经准备足够的满电电池，其电池质量还可优化；它没有提供本题有限共享电池池的充电回流模型。L03摘要未证明其包含本题这种有限编号电池资源；L04采用自己的非线性凸能耗公式。**本题固定电池、含电池空机质量、20%返航下限、DEM航高及等效航程公式，均应直接按附件计算，不能被这些论文的公式替换。**

### 3.2 联合搜索：L02

ALNS适合处理组批、访问顺序和排程之间的组合耦合。可设计本题专用的“移箱/换箱、拆批/合批、交换访问顺序、调整起飞时间、移动同一中继覆盖的一组运输任务、中继窗口合并/拆分”等操作，再用确定性排程与通信验证修复。每次接受解之前，都应重新检查硬时限、能源及连续通信。

L02本身研究卡车与无人机协同配送，包含发射和回收同步。第三问没有卡车，因此只能借鉴搜索框架，不能直接沿用其全部约束。上述专用算子是本题的建议设计，并非已经从该文复现或已经实现的内容。

### 3.3 能源库存与分解：L05、L06

L05强调换电设施与等待过程的作用；L06证明多趟无人机相关问题确实存在logic-based Benders研究路线。对本题，可把“选哪些运输架次及何时起飞”与“中继能否保障、实体机/能源能否无冲突周转”分成主问题和子问题，用具体冲突反馈修复。

不过L05为长期换电网络设施设计，并非本题仅在O01进行共享电池轮换；L06没有取得摘要/全文，本文不声称其主子问题、有效割或复杂度与本题相同。**本轮没有核验到一个可原样覆盖‘有限共享电池池+两阶段回充+异构运输+中继连续通信’的现成统一模型。**两类能源必须直接按题设独立编号、记录任务占用、返航SOC、充电完成和再次使用时刻。

### 3.4 通信与中继位置：L07、L08

这两篇的直接启发是将连续几何变量、通信关联及调度变量交替优化。第三问可以先构造满足DEM范围和离地高度限制的悬停候选点与高度，精确判断回传可用性及所覆盖的运输轨迹区间，再与服务开始/结束时刻、能源周转一起安排。

它们的优化对象分别涉及移动中继或空中基站、吞吐量、轨迹和可调发射功率。第三问运输航段按题设固定，设备参数由附件给定，DEM遮挡还会引起传播损耗跳变。因此：

1. 不允许借通信优化之名任意改变运输水平直线航段、放宽巡航海拔规则。
2. 不把论文中的平均吞吐量目标当作本题任一时刻链路可用的替代条件。
3. 不因为引入SCA就声称本题变成凸问题；题设二值地形遮挡应保留，或在明确的局部近似下再独立验证。
4. 不迁移论文的收敛与最优性结论，也不假定本题中继可以在去程和未完成建链时提供悬停通信服务。

## 4. 适合第三问独立实现的建议组合

建议采用“统一物理/通信预处理—独立候选运输架次—资源与中继调度—局部联合改进—独立验证”的流程。先用确定性构造和候选枚举获得完全独立于Q2的可行基线，再结合整数规划、CP排程或ALNS做进一步改进。若使用Benders或SCA，应另行给出本题的分解有效性或近似适用条件，不以文献名称替代论证。

论文中可以说明：本题的多架次与载荷建模受L01、L03、L04启发，组合搜索受L02启发，能源设施约束处理参考L05，联合通信的交替优化结构参考L07、L08；实际约束以赛题附录为准。L06在取得全文前，只作为备选方法文献，不宜写成已经复现的核心求解算法。

以上是经检索的建模依据与算法建议。实际求解采用了哪些方法、候选规模、随机种子、运行时间、可行性验证及目标值，应以同目录最终程序和求解报告为准。仅有文献检索与算法建议不能证明第三问已经求解，也不能证明所得解全局最优。

