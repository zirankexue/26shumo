# 山区洪涝灾害下无人机运输与通信协同优化

问题一、问题二的建模代码、计算结果及可视化归档。问题三尚未实现。本次上传复用已核验结果，没有重新运行优化。

## 内容

- [建模工程与运行说明](建模/README.md)：Python入口、配置、依赖与接口。
- [问题一模型](建模/docs/问题一模型说明.md)：单点往返安全载荷与精确组批。
- [问题二模型](建模/docs/问题二模型说明.md)：多点运输、逐箱交付与机队/电池联合调度。
- [公式来源与假设审计](建模/docs/公式来源与假设审计.md)：区分原题、文献依据、推导和简化假设。
- [文献公式核查](建模/references/可用公式与适用性.md)：保留研究笔记；下载的文献全文、网页存档和页面截图未包含在本代码仓库。

## 已计算结果

| 内容 | 报告 | Excel或图表 |
|---|---|---|
| 问题一基准与原敏感性 | [结果分析](建模/outputs/q1/问题一结果分析.md) | [Excel](建模/outputs/q1/问题一结果.xlsx) |
| 问题一5%—45%安全余量 | [结果分析](建模/outputs/q1_sensitivity_05_45/问题一结果分析.md) | [Excel](建模/outputs/q1_sensitivity_05_45/问题一结果.xlsx) |
| 问题二联合调度 | [结果分析](建模/outputs/q2/问题二结果分析.md) | [Excel](建模/outputs/q2/问题二结果.xlsx) |
| ①②⑤⑥扩展图表 | [图表说明](建模/outputs/visualizations/图表说明.md) | [PNG与PDF](建模/outputs/visualizations/figures) |

每个结果目录的`tables/`保留CSV与JSON，`logs/`保留运行、求解及核验记录。问题一基准为18架次、59.132260326 kWh；问题二保存的主方案为36架次、95.845699225 kWh。两问约束不同，不能仅凭这些总量比较算法优劣。

问题一5%—45%敏感性每5个百分点一档。40%和45%均无完整可行方案；不可行场景不以部分交付总量替代完整方案。

![服务区配送可行性](建模/outputs/visualizations/figures/06_安全余量服务区可行性矩阵.png)

## 本地复现

Python 3.12及以上；建议创建独立虚拟环境。原始题目附件、提交模板、论文工程、中文字体和本机运行时未随代码上传。先按下列相对位置准备自己持有的原始附件与字体：

```text
仓库根目录/
├─ 建模/                         # 本仓库已提供
├─ 数据/                         # 原始数据目录，保留题目附件层级与文件名
├─ 结果提交模板.xlsx
└─ 写作/fonts/SimSun.ttf          # 配置指定的中文字体，可改为本地已有的中文字体
```

具体输入文件与原运行哈希可在`建模/outputs/*/logs/run_manifest.json`中核对。历史日志包含原机器路径，仅用于溯源；它们不是可移植运行配置。图表入口当前要求来源数据与保存结果哈希匹配；迁移后需将结果元数据中的原机器文件路径映射到本地相同原文件，不能更改或跳过哈希核验。

```bash
cd 建模
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -r requirements-q2.txt
python run_q1.py --config configs/q1.toml --skip-excel
python run_q1.py --config configs/q1_sensitivity_05_45.toml --skip-excel
python run_q2.py --config configs/q2.toml --skip-excel
python scripts/plot_extended.py
```

`requirements*.txt`记录原计算环境的固定依赖版本。Excel导出脚本依赖Codex配套的`@oai/artifact-tool`和Node.js；普通Python环境可用`--skip-excel`生成计算明细，直接查看仓库中已导出的Excel。问题二默认求解预算及数值口径见配置与工程说明；限时搜索不保证原问题全局最优，现有历史方案可独立核验。

原始附件缺失时不能从零重算，但仍可直接阅读本仓库的CSV、JSON、Excel、PNG及PDF。研究笔记里指向未上传原文或历史本机文件的链接需要相应原资料；结果报告内的图表链接已改为仓库相对路径。

## 计算口径与可追溯性

能耗为题给等效航程与文献依据下势能附加项的简化模型，未做实际飞行标定。硬时限、SOC与资源检查通过只能证明当前模型下的数值可行性。

`UPLOAD_MANIFEST.json`记录上传快照的源文件和仓库文件SHA-256。结果数值文件保持原始字节；Markdown仅将本项目内绝对文件/图表链接转换为相对链接。运行环境、可再生成缓存、Excel锁文件及试运行目录不纳入提交。
