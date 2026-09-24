# 问题二图册重绘

从工程运行 `python scripts/plot_q2.py`。迁移时保留code与data，执行 `python code/plot_q2.py --from-bundle <data/plot_data.json绝对路径> --output <输出绝对路径>`。绘图不读取原始表格或DEM，不运行优化。字体已随数据保存，可用--font替换；--z-exaggeration 1生成真实比例三维图。

所有图的数值以JSON快照为准，对应CSV在tables中；空值不是零。图中地形网格仅用于显示。
