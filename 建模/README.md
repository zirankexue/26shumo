# D题建模工程

当前实现问题一：单点往返安全载荷计算、精确货箱组批、目标优先级对比、返航安全余量敏感性分析及结果导出。

现已增加问题二：多点航线、逐箱交接顺序、实体无人机与共享电池联合调度。两问使用独立配置、缓存和输出目录。

公式使用前先查[公式来源与假设审计](docs/公式来源与假设审计.md)：已分开登记原题公式、数学推导、自主假设及算法设置，含原题定位、参数单元格、实际核实的外部资料和代码对应。水平与爬升能耗展开式是有物理依据的简化解释，尚未实测标定；不能将已有数值核验说成物理模型已获验证。后续公式审查规范见上一级`AGENTS.md`。

10条参考文献的下载文件及版本见[文献资料目录](references/README.md)。第[5]篇现已取得原文及所附勘误：水平项依据第20页(A1)结合题给航程具体化，爬升项依据第21页(C1)末项积分并按题给效率折算，详见[全文核查](references/05_能耗公式全文核查.md)。题面规则优先于文献中的其他模型与参数。

本次文献依据落实仅补充两问模型说明与代码注释，计算表达式、参数和求解目标均未改变，无需重新优化。核验与适用边界见[文献公式采用与重算判断](docs/文献公式采用与重算判断.md)。

## 问题二运行

```powershell
python -m pip install -r requirements-q2.txt
python run_q2.py --config configs/q2.toml
```

本机可直接使用已经配置的运行时：

```powershell
& 'C:\Users\Dai\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'D:\xyq\数模\D题\建模\run_q2.py' --config configs/q2.toml
```

问题二优先加载`.runtime/q2`中的OR-Tools及依赖，不改变问题一的独立依赖。默认求解预算60分钟，导出、图表和核验另计。`--budget-seconds 120`可做短时运行；`--output outputs/q2_trial`保留到另一个目录；`--skip-excel`跳过Excel；`--test`执行两问回归与穷举核验；`--report-only`从已有`tables/results.json`重新导出，避免重新求解。

本机默认`python`指向Anaconda，其部分扩展与项目NumPy版本不兼容。检测到本项目已准备依赖且配套Python存在时，问题二入口自动使用配套Python执行相同参数，不修改Anaconda环境。迁移到其他机器时使用独立虚拟环境安装`requirements-q2.txt`即可。

问题二入口不依赖`_阅读缓存`或问题一生成文件。默认读取原始附件重新生成问题一候选种子。Excel导出仍需要下文所述配套表格工具。

若运行中断，可显式使用`--resume`从该输出目录的检查点恢复四种最好可行方案；继续前会重新验证物理、时限与资源可行性。恢复后重新分配所配置的搜索预算和算子权重，并非逐步重放中断前的随机轨迹。默认每次重新构造初解，不隐式继承旧运行。有效预算及是否恢复会记录在结果元数据中。

`python run_q2.py --verify-only`可在不重新优化的情况下，直接从原始输入独立重算已保存的主方案和对照方案，并核对完整结果对象及已有Excel/CSV。

主结果在`outputs/q2/问题二结果.xlsx`和`outputs/q2/问题二结果分析.md`，模型说明在`docs/问题二模型说明.md`。`cache/q2`含全部有向航段和最终候选池；`outputs/q2/tables`含逐箱、逐段、资源明细及完整JSON，`logs`含配置、哈希、求解状态和核验结果。单次搜索的可行检查点在`logs/checkpoint.json`。

逐箱交付采用基础交接后依次交箱；主目标为加权逾期→最后返航→能耗→架次。求解器的最优性状态只针对对应候选池及已固定目标，有限搜索不保证完整问题全局最优。固定种子和单线程支持复现流程；限时截断受机器速度影响，保存的结果可直接复核。

## 运行

### 问题一核心图册（7组）

`python scripts/plot_q1_core.py`生成货箱组批、批次约束、服务区统计、三种首要目标权衡、安全载荷曲线、余量组批变化及服务区可行性矩阵，输出到`outputs/q1_core_figures/`。每图均有300 dpi PNG及PDF，`data/`保存7份逐项CSV与统一`plot_data.json`，`code/`保存本次绘图代码副本，`logs/`保存核验与来源哈希。主目标对照使用已补算的时间优先案例，不运行优化。

重绘命令：`python scripts/plot_q1_core.py --from-bundle outputs/q1_core_figures/data/plot_data.json --output outputs/q1_core_redraw`。该模式只用数值快照，不读取原始附件或调用求解器；`--font`可指定中文字体，`--dpi`可调整导出分辨率。迁移时使用图册内两份代码及重绘依赖清单，详见`图册说明与重绘方法.md`。

### 问题一目标优先级完整核验

`python scripts/audit_q1_objectives.py`在20%安全余量下重算三指标的全部6种词典序排列，逐箱重算并对每种顺序的15个服务区执行三阶段MILP复核；输出`outputs/q1_objective_audit/`中的JSON逐箱方案与Markdown对照表。主图应分别展示架次优先、能耗优先和时间优先，即使部分方案指标相同也保留案例。默认问题一配置已补入时间优先；原有Excel/JSON为旧快照，补充结果独立保存。

### 二维与三维无人机路线图

```powershell
python scripts/plot_routes.py
```

读取两问20%安全余量主方案和原始DEM，生成两问二维/三维总览及问题二8机分面图，共6组300 dpi PNG/PDF；不重新优化。默认采用原始地图HTML内嵌的彩色DEM晕渲及乡界，按其经纬度范围配准，输出在`outputs/routes_terrain/`。图示范围为完整乡界与节点包围盒外扩1 km，二维和三维共用同一底图纹理。原始地图16个节点与节点表逐一核对；只解析附件的数据常量，不执行脚本。

`--background pale`复现旧浅色底图并输出`outputs/routes/`，既有浅色版保留。两个目录均包含完整逐架次、逐航段、三维顶点CSV及核验日志。三维高程默认显示夸张5倍并在图中注明，`--z-exaggeration 1 --output outputs/routes_true_scale`可另存真实比例版本。所有高程核验使用完整DEM，显示网格抽稀与颜色纹理插值不参与安全净空计算。

### 扩展论文图表（①②⑤⑥）

```powershell
python scripts/plot_extended.py
```

无需重新优化；复用问题一5%—45%结果及问题二主方案，独立补算地形剖面、能耗分项、硬时限裕量和服务区可行性矩阵。五张300 dpi PNG和矢量PDF、原始精度CSV、说明与核验日志在`outputs/visualizations/`。运行前核对输入哈希，运行后确认既有输出与论文文件未变化（不读取Excel临时锁文件）。入口路径可使用绝对路径，输出路径按工程位置解析；`--output`可另设独立目录。

返航安全余量5%—45%的扩展图表与结果使用独立配置（每5个百分点，共9档）：

```powershell
python run_q1.py --config configs/q1_sensitivity_05_45.toml
```

输出到`outputs/q1_sensitivity_05_45`，包含重新优化的敏感性曲线、机型载荷曲线、Excel及CSV/JSON；原`outputs/q1`保留。不可行情景不填零架次或部分配送的总能耗。

Python 3.12及以上。所有相对路径基于本工程根目录，与终端所在目录无关。

```powershell
python run_q1.py --config configs/q1.toml
```

在本机Codex环境中可直接使用已准备好的运行时：

```powershell
& 'C:\Users\Dai\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'D:\xyq\数模\D题\建模\run_q1.py' --config configs/q1.toml
```

`run_q1.py`会优先加载工程内`.runtime/python`的科学计算依赖，再加载`src`。这两处均不引用此前的`_阅读缓存`。本机其余读取依赖来自Codex配套Python。其他机器使用独立虚拟环境并安装`requirements.txt`即可执行Python计算。

```powershell
python -m pip install -r requirements.txt
python run_q1.py --test
python run_q1.py --skip-excel
```

Excel由配套Node.js运行时中的`@oai/artifact-tool`导出。默认定位本机Codex运行时，也可设置`CODEX_NODE_EXECUTABLE`与`CODEX_NODE_MODULES`。缺少该工具时，使用`--skip-excel`仍会生成完整CSV、JSON、报告和图表；不会伪造Excel导出成功。

命令行还支持`--output outputs/q1_alternative`，在另一目录保留结果。正式运行包含15个服务区的三阶段MILP交叉核验；`--test`执行独立的13项单元检查。

## 问题二：ALNS与CP-SAT

安装 `requirements-q2.txt` 后运行 `python run_q2.py --config configs/q2.toml`。默认约60分钟求解预算，输出、渲染及独立核验另计。四个首要目标分别为及时性、最后返航、能耗、架次；主方案优先及时性。原始附件、问题一结果与论文工程保留。

`--solve-only`先保存计算结果；`--report-only`从结果重新导出完整Excel、CSV和8组300dpi图；`--verify-only`独立重算四个方案；`--resume`显式核验检查点后继续，新的完整运行会将同名结果目录归档。`--budget-seconds 90 --output outputs/q2_smoke`仅用于试跑。

`outputs/q2/tables/results.json`保留四方案与全部阶段时间线，`tables/by_scheme`保留每方案逐箱和逐航段CSV；`logs`保存ALNS权重、接受概率、CP-SAT状态及输入/代码哈希。`outputs/q2/plotting`保存独立绘图代码、数据快照、字体和依赖，重绘命令为 `python scripts/plot_q2.py --from-bundle outputs/q2/plotting/data/plot_data.json --output outputs/q2_redraw`，不运行优化。

模型说明见 `docs/问题二模型说明.md`，算法参数是自主设置，文献能耗解释未改动；有限候选搜索不宣称全局最优。公式使用持续参照 `docs/公式来源与假设审计.md`。

## 目录职责（问题一）

| 目录 | 内容 |
|---|---|
| `configs` | 输入输出路径、物理参数、目标顺序、敏感性范围 |
| `src/uav_rescue/common` | 强类型输入对象、Excel读取、DEM与坐标、航段时间和能耗 |
| `src/uav_rescue/q1` | 可行组成枚举、精确动态规划、逐箱恢复、独立核验、报告与绘图 |
| `scripts` | Excel导出器 |
| `tests` | 物理边界、地形边界及与穷举对照的检查 |
| `docs` | 模型假设、符号、公式、算法和最优性论证 |
| `cache/q1` | 可再生成的航段与可行候选批次JSON |
| `outputs/q1/tables` | 所有数值明细和完整情景结果 |
| `outputs/q1/figures` | 中文图表的PNG与PDF |
| `outputs/q1/logs` | 核验结果、环境、输入哈希和Excel渲染预览 |

原始附件仍位于工程外的`../数据`，原结果模板和`../写作`不修改。模板中的问题一表头按原样读取，但结果写到新的`outputs/q1/问题一结果.xlsx`。

## 主要接口

- `Inputs`包含节点、机型、逐箱数据及来源；货箱编号和原始属性保持不变。
- `Route`包含同一服务区的去、返两条`Leg`；每条航段明确记录水平距离、巡航海拔、爬升和下降高度。
- `safe_payload(...)`返回安全质量上限及限制原因；不可达返回`None`。
- `enumerate_batches(...)`输出满足质量、体积、能量约束的候选批次。
- `solve(...)`对四类箱数状态做词典序动态规划；输出可行性、批次和状态数。
- `tables/results.json`是结构化结果入口，包含基准、目标对照、余量情景、航段、检查和Excel表格数据。

## 结果阅读

先读`outputs/q1/问题一结果分析.md`，需要完整公式时读`docs/问题一模型说明.md`。

Excel的`Q1_单点组批`保留原9列。其“往返时间”专指飞行时间之和，准备、装载、交接与完整作业时间在`作业时间分解`表。总体累计作业时间不是考虑多机并行后的完工时间。

结果是外部优化程序产生的数值快照，修改输入或配置后必须重新运行。动态规划对主方案取得规定数值精度下的全局最优解；基准结果另经独立整数规划复核。目标对照不构成完整Pareto前沿。

基准为20%安全余量，敏感性为10%至40%共7档。未对实体机、电池库存、通信和交付时限进行排程，因此不得将本问方案直接视为后续问题的可行方案。

## 问题二：显式权重与四情景扩展

用户确认主权重为`W:Cmax:E:N=3:1.5:1:0.5`，先用旧四个完整词典序方案的min–max范围固定归一化，再优化无量纲加权目标。旧词典序方案只作优先关系对照，不冒充加权情景；W始终是加权迟到，硬截止不软化。详细定义、误差界与搜索范围见`docs/问题二加权目标说明.md`。

```powershell
python run_q2_weighted.py --config configs/q2_weighted.toml
python run_q2_weighted.py --config configs/q2_weighted.toml --report-only
```

配置及运行入口分别为`configs/q2_weighted.toml`与`run_q2_weighted.py`；独立输出为`outputs/q2_weighted/`，已有同名加权输出先归档，旧`outputs/q2/`和问题一结果保留，不读取或核对恢复检查点。数据以`tables/results.json`保存完整方案、评分尺度和来源，`logs`保存接受、求解状态和验证信息；重导出不重算优化。

主搜索结束后，四情景分别把一个权重放大10倍、其余保持不变，各运行3000次当前候选池内的快速ALNS重组/列表调度，再进行60秒CP-SAT精修。所有返回且核验通过的完整解都按主权重回评，独立保存主权重最好方案。`--budget-seconds`只缩放初始、主搜索、主收尾及四次CP精修的计时预算；四情景快速迭代和导出用时另计，不是总运行墙钟上限。`--iterations`可覆盖每个情景的快速迭代次数，适合另设配置输出目录进行短试跑。

最终汇总保留`scenario_search_endpoints`中的四个独立搜索终点，再按每个情景自身权重，从主推荐、四个原情景终点及四个旧词典序方案构成的9个完整方案中重选情景结果。此步骤不重新求解、不读取检查点，也不从只有目标向量的点云恢复方案。主推荐不变；及时性放大选中主推荐，架次放大与能耗放大选中同一能耗情景方案，重复结果照实显示。该结论仅是已保存完整方案内最好，不保证全局最优。

云图按全部已保存`accepted=True`记录绘制，没有对重复向量去重；当前1027条记录对应538个唯一四指标向量，记录数包含参考方案及情景终点。唯一向量数不代表唯一航线结构或可恢复方案数量，完整时序的推荐依据与云图统计分开记录。
# 问题二数值改进实验（不绘图）

保持既有物理模型、20%返航余量、主权重3:1.5:1:0.5和冻结归一化尺度，
在原加权完整方案上构造针对性的合批、移箱、交换、换型和访问顺序候选，联合重排。
输出独立写入`outputs/q2_improved/`，旧正式结果保留。

```powershell
python scripts/improve_q2_pool.py
python run_q2_improve.py --seconds 150
python scripts/export_q2_improvement.py
```

最后一条命令仅重算核验已保存的完整实验结果并导出JSON、CSV和文字分析，不优化、
不绘图、不导Excel、不读取检查点。最终数值及与截图口径的比较见
`outputs/q2_improved/优化结果与差距分析.md`，运输及资源明细在该目录的`tables/`中。
固定种子、2线程限时CP的运行记录可复查，但启发式结果不承诺跨机器逐位一致。
