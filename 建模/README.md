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

问题二优先加载`.runtime/q2`中的OR-Tools及依赖，不改变问题一的独立依赖。默认求解预算30分钟，生成模型、候选、图表和Excel另有少量开销。`--budget-seconds 120`可做短时运行；`--output outputs/q2_trial`保留到另一个目录；`--skip-excel`跳过Excel；`--test`执行两问回归与穷举核验；`--report-only`从已有`tables/results.json`重新导出，避免重新求解。

本机默认`python`指向Anaconda，其部分扩展与项目NumPy版本不兼容。检测到本项目已准备依赖且配套Python存在时，问题二入口自动使用配套Python执行相同参数，不修改Anaconda环境。迁移到其他机器时使用独立虚拟环境安装`requirements-q2.txt`即可。

问题二入口不依赖`_阅读缓存`或问题一生成文件。默认读取原始附件重新生成问题一候选种子。Excel导出仍需要下文所述配套表格工具。

若运行中断，可显式使用`--resume`从该输出目录的检查点继续；继续前会重新验证物理、时限与资源可行性。默认每次重新构造初解，不隐式继承旧运行。有效预算及是否继续运行会记录在结果元数据中。

`python run_q2.py --verify-only`可在不重新优化的情况下，直接从原始输入独立重算已保存的主方案和对照方案，并核对完整结果对象及已有Excel/CSV。

主结果在`outputs/q2/问题二结果.xlsx`和`outputs/q2/问题二结果分析.md`，模型说明在`docs/问题二模型说明.md`。`cache/q2`含全部有向航段和最终候选池；`outputs/q2/tables`含逐箱、逐段、资源明细及完整JSON，`logs`含配置、哈希、求解状态和核验结果。单次搜索的可行检查点在`logs/checkpoint.json`。

逐箱交付采用基础交接后依次交箱；主目标为加权逾期→最后返航→能耗→架次。求解器的最优性状态只针对对应候选池及已固定目标，有限搜索不保证完整问题全局最优。固定种子和单线程支持复现流程；限时截断受机器速度影响，保存的结果可直接复核。

## 运行

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

## 目录职责

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
