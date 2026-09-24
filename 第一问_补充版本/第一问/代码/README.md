# 第一问代码说明

原求解程序保留在根目录 `code`，以维持既有相对路径与第三问导入依赖；本目录提供统一复现入口和论文材料生成代码。

|步骤|程序|作用|
|---|---|---|
|基准组批|[solve_q1_batching.py](../../code/solve_q1_batching.py)|读取原始附件，局部坐标/DEM预处理，数量DP与真实箱号回填|
|完整前沿|[solve_q1_multiobjective.py](../../code/solve_q1_multiobjective.py)|非支配标签DP及全局完整Pareto前沿|
|精确敏感性|[solve_q1_reserve_sensitivity.py](../../code/solve_q1_reserve_sensitivity.py)|所有候选临界值扫描，19段当前最优方案与载荷曲线|
|独立验证|[verify_q1_local_plane.py](../../code/verify_q1_local_plane.py)|几何、逐箱、端点、前沿与载荷复核，参数见原README|
|版本对照|[compare_q1_peer.py](compare_q1_peer.py)|对照师兄当前六种顺序、九档余量和统一前归档中的18批/19段箱号|
|数据整理|[prepare_q1_paper_data.py](prepare_q1_paper_data.py)|完整精度CSV、来源哈希、字段字典和回读核验|
|地理图|[plot_q1_geography.py](plot_q1_geography.py)|F01真实地形地图、F02保守地形剖面|
|定量图|[make_q1_paper_figures.py](make_q1_paper_figures.py)|F03–F16及逐图源CSV、图注清单|
|材料汇编|[build_paper_package.py](build_paper_package.py)|统一图注、图册、原始附件/Excel副本、导出核验与ZIP|

使用 Python 3.12，核心计算依赖见 [requirements_q1.txt](../../code/requirements_q1.txt)，绘图/汇编增量依赖见 [requirements_paper.txt](requirements_paper.txt)。项目中 `.runtime_py` 为隔离依赖位置，脚本已按该目录加载；也可在自建Python环境安装全部依赖。

在工作区根目录运行（将 `python` 换成自己的解释器路径）：

```powershell
python 第一问/代码/复现第一问.py --step materials
```

此命令只根据已有正式结果重新整理数据、绘图和汇编。`--step baseline`、`--step pareto`、`--step reserve` 分别重新求解三个小项，**会由原程序更新对应 results**；`--step solve-all` 按依赖顺序重算三个小项。默认不重算。

基准求解后入口会自动执行独立几何审核（`verify_q1_local_plane.py --geometry-only`），更新与新基准绑定的验证记录，供后续两个求解器使用。全部求解完成后，仍需按原README运行完整独立审核，再导出Excel并更新论文材料。

原Excel导出入口仍是根目录 `code/export_q1_batching.mjs`、`export_q1_multiobjective.mjs`、`export_q1_reserve_sensitivity.mjs`，依赖bundled Node的 `@oai/artifact-tool`。本次汇编直接复制已有验证通过的Excel，不要求收件人为阅读安装Node。

详细求解参数和核验顺序见[原README_Q1](../../code/README_Q1.md)。材料包不携带Python/Node环境；GitHub副本包含实际源码、派生数据、正式计算与核验结果；原始输入需自行准备，详见[分享版总入口](../../README.md)。

`compare_q1_peer.py` 是本次版本统一的核查工具，另需本机 `D:/Desktop/zirankexve` 师兄目录及 `archives/question1_before_peer_alignment_20260924.zip` 统一前备份。上述外部目录和旧备份不属于日常求解、绘图的依赖，也不随论文材料包分发；已有预演与正式对照结论可直接阅读材料包中 `results/question1_peer_alignment` 的验证JSON及差异说明。
