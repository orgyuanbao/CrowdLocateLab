# CrowdLocateLab

**面向异步众包定位的科研智能体开发与实证研究。** 项目用真实大模型选择研究工具、提出和修订假设，再由本地数值程序执行实验；保留模型请求、工具结果、独立批评、方法冻结及独立测试证据。

研究来源为用户提供的《HarmonyOS难题发布》第七期第 1 页“基于非线性优化的众包离线高精度位置聚合难题”。本项目研究其中可计算的**合成子问题**：存在历史观测、异常距离和运动模型失配时，如何估计目标当前位置，并给出可校准的误差半径。它没有解决完整工业开放问题，也不声称解决公认数学猜想或首次发明所用的稳健估计、交叉验证和共形预测方法。

## 先查看交付结果

第一次使用可以先读 [快速入口](START_HERE.md)。

无需模型账号即可阅读 PDF、检查结果文件或打开只读仪表盘：

| 入口 | 内容 |
| --- | --- |
| [研究报告](report/CrowdLocateLab_Report.pdf) | 来源、建模、数学命题、智能体系统、实际轨迹、独立实验与局限 |
| [正式研究记录](runs/live_reference/) | 实际大模型调用、工具结果、状态、批评和冻结方法 |
| [正式评测摘要](results/benchmark/summary.json) | 基线、所选方法、消融、成对比较与覆盖率 |
| [评测协议](results/benchmark/protocol.json) | 独立种子、样本量、方法配置、源码校验和环境 |
| [课程提交说明](docs/SUBMISSION.md) | 要求对照、答辩演示及个人 GitHub 上传步骤 |

本次正式运行已冻结，最终方法为 `range_cv_reported`。下表来自已保存的真实记录：

| 已完成项目 | 实际结果 |
| --- | --- |
| 大模型研究 | 13 个研究动作，14 次成功的真实模型调用，其中 1 次为独立提示的审阅调用 |
| 研究迭代 | 6 次实验或消融调用，注册 5 个新方法（含消融） |
| 冻结后定位预测 | 独立评测 5040 次 + 敏感性网格 1152 次 = **6192 次**，不是模型调用次数 |
| 软件与数学实现测试 | **66 项通过**；原始输出及完整性审计位于 `verification/` |
| 主测试总体均值 | 静态 WLS 的 6.532 m → 所选方法的 4.189 m，降低 35.87% |
| 必须保留的负面结果 | 正常静态均值相对 WLS 退化 37.63%；90% 名义区域的同分布检验实际覆盖为 85.625% |

总体改善没有解决所有场景问题，也不代表完整工业题验收。失败场景、比较区间和覆盖限制详见报告及 [结果审查](docs/results_review.md)。

在项目根目录运行：

```text
python dashboard.py --run runs/live_reference --results results/benchmark --port 8765
```

浏览器打开 <http://127.0.0.1:8765>。仪表盘仅用 Python 标准库，读取保存的文件，不发起模型调用，也不执行新实验。页面区分真实运行与回放，并显示负面结果。缺失文件显示等待，不生成示例指标。

## 环境与安装

数值核心只依赖 `numpy` 和 `scipy`，建议使用 Python 3.11 或更新版本及独立虚拟环境。以下命令均在项目根目录执行。

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

后文的 `python` 可替换为 `.\.venv\Scripts\python.exe`，不必修改 PowerShell 执行策略或激活环境。

macOS / Linux：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试检查数值 Jacobian、观测交叉验证、数学反例、校准边界、预算、恢复与证据校验。测试中的假模型夹具只用于软件验证，不能当作真实智能体研究记录。

## 三种运行方式

| 方式 | 是否调用大模型 | 是否重新数值计算 | 用途 |
| --- | --- | --- | --- |
| 阅读文件 / `dashboard.py` | 否 | 否 | 直接审阅随项目保存的证据 |
| `--provider replay` | 否 | 是 | 重新执行保存的决策与工具轨迹 |
| `--provider codex` | 是 | 是 | 发起一次新的模型驱动研究 |

### 1. 真实大模型研究

需要安装 Codex CLI、完成自己的账号登录，并能正常运行 `codex exec`。安装与首次登录参见 [OpenAI 官方 CLI 文档](https://learn.chatgpt.com/docs/codex/cli)；登录状态可通过 `codex login status` 检查，参见[官方命令说明](https://learn.chatgpt.com/docs/developer-commands)。项目复用 CLI 的正常认证，不要求将任何凭据放进项目。

```text
codex --version
codex login status
python run_agent.py --output runs/my_study --provider codex --timeout 300 --reasoning-effort medium
```

若 CLI 不在 PATH 中，在当前 PowerShell 设置其实际可执行文件路径：

```powershell
$env:CODEX_EXECUTABLE = 'C:\path\to\codex.exe'
```

程序沿用 CLI 配置中的默认模型，不指定模型名称。实际名称只取 CLI 返回字段，未返回时保留未知。推理强度通过本次调用临时设为 `medium`。新研究需要联网及可用模型额度；本地 CPU 负责数值计算。网络等待和数值工作量分别留有记录。

默认上限为 26 个研究动作、10 次实验或消融调用、38 次模型请求，后者包含批评与重试。验证集只允许 4 次调用。输出目录必须是新目录，以免覆盖证据。模型或认证持续失败会明确终止并保存记录，不会静默改成规则搜索。

冻结成功后，再独立执行正式评测：

```text
python benchmark_v2_1.py --run runs/my_study --output results/my_benchmark
python dashboard.py --run runs/my_study --results results/my_benchmark --port 8765
```

正式评估统一使用 `benchmark_v2_1.py`。它在研究尚未冻结、holdout 尚未生成时修复了“选中基线简称却生成非法组件消融”的外层接口错误；原 `benchmark.py` 保留为初始化版本及共享校验/统计库，不能用作正式入口。原快照、精确 diff、哈希与时间记录见 [评估器预先修订说明](research/evaluator_amendment.md)。不同时跑两个版本后择优。

评测入口在生成测试数据前核对冻结状态、源码、方法注册表和验证证据。修改研究源码后应开启新版本研究，不应改写旧记录以绕过校验。新的评测输出目录也必须不存在。停止、恢复、预算设置和失败定位详见 [智能体使用说明](docs/agent_usage.md)。

### 2. 显式回放

```text
python run_agent.py --output runs/replay_audit --provider replay --replay-source runs/live_reference
```

回放读取已保存的模型响应并重新执行工具，不产生新模型推理。它会明确记录 `replay=true`，并在响应耗尽时失败；不能将回放称为第二次独立科研发现。回放仍需数值依赖和 CPU 时间。不同平台或依赖版本可能产生小幅数值差异。

### 3. 只重算冻结方法的评测

使用随项目提供、与当前源码一致的冻结记录，无需模型账号：

```text
python benchmark_v2_1.py --run runs/live_reference --output results/reproduction
```

默认独立评测包括主测试 360 例、压力测试 200 例、独立校准 240 例和覆盖检验 320 例。主测试包含 4 个场景、3 个种子，每场景每种子 30 例；压力测试包含 5 个场景、每场景 40 例。校准与覆盖检验分别从核心场景混合分布独立抽样。计算时间还取决于被冻结方法与实际触发的消融数量。

### 4. 冻结后的敏感性网格

```text
python sensitivity.py --run runs/live_reference --output results/sensitivity_reproduction
```

此独立脚本先验证 `runs/live_reference` 的冻结证据，再执行 [预登记方案](research/sensitivity_plan.json)：观测数 8、12、16、24，名义污染比例 0、0.1、0.25、0.4，静态与运动两个场景，每格 12 例。合计 384 个独立案例、三个固定方法配对的 1152 次预测。已交付的正式结果位于 `results/sensitivity/`；上面的重现命令写入新的 `results/sensitivity_reproduction/`，再次重现时还需换一个不存在的输出目录。可用 `--run` 指向另一份有效冻结记录。

分析保存实际污染比例、每格均值 / CEP90、工作量、模型选择频率和 CV 门控诊断，用于说明适用范围，不用于重新选方法。默认三折 CV 在 8 份观测时存在仅有 5 份训练观测的折，可能直接排除运动模型；应结合冻结配置和诊断解释。每格只有 12 例，不把网格差异当作多重比较下的确证。脚本不调用模型，不重新校准，也不更改冻结方法。

## 应用于一组新观测

冻结方法也可以直接处理新的观测文件，无需模型账号或目标真值：

```text
python locate.py --input examples/observations.json --run runs/live_reference --calibration results/benchmark/conformal.json --alpha 0.1 --output results/new_location.json
```

输入只包含上报位置 `anchors`、相对时间 `times`、距离 `ranges` 和观测尺度 `sigma`。字段、单位及输出解释见 [应用说明](docs/application.md)。示例用于展示程序接口，不是工业真实数据。将合成数据得到的校准量用于不同来源的新观测时，只能作为参考误差半径；原来的交换性条件未必成立，不能继续宣称名义覆盖率。

## 数学方法与研究边界

每份观测包含上报位置、时间、由信号转换的距离及报告尺度。目标状态为参考时刻位置；候选模型包括静态位置、常速度位置以及通过观测层交叉验证选择的模型。预实现的可组合算子包括距离或对数距离残差、均值为一的对数正态噪声修正、稳健损失、有限次残差裁剪、随机子集初始化和运动正则化。精确定义见 [method_math.md](localization/method_math.md)。

模型负责选择组合、写出可检验假设、根据诊断修订方案、安排控制消融并冻结方法；它不在运行中任意编写求解器。控制器管理结构化响应校验、工具白名单、持久记忆、预算、恢复及证据哈希。批评由另一提示下的模型调用产生；这是角色与上下文分离，不表示不同模型或独立实验室复现。

理论部分包括三个带条件和证明的命题：相对距离损失在对数正态噪声下的偏移、共线观测几何中的镜像不可辨识性、满秩与局部条件下的误差界。数值见证不能替代证明。几何尺度再通过独立数据作分割共形校准；交换性支持边际覆盖结论，不能直接推出每个场景、拒绝定位后的子集或分布偏移下的覆盖保证。

原题给出的目标包括上报用户数大于 10 时 CEP90 不超过 30 米、标准场景不劣化、运动场景精度提升 20%。原题没有明确 20% 的计算定义和工业对照。项目报告当前位置欧氏误差的均值、经验 CEP90、分场景结果和成对区间，不能将这些指标直接等同于原题验收，也没有建立标准静态场景处处不劣化保证。开发阶段多次探索属于探索性研究，不将未作多重校正的比较当作普遍优越性的确证。

所有定位数据由公开生成器合成，没有使用外部真实众包轨迹、真实射频测量或工业系统日志。随机子集、裁剪和局部非线性求解不保证全局最优；运动加速度、坏几何、偏置和高污染可能导致失败。实际成功与失败以正式结果和报告为准。

## 代码与证据目录

```text
CrowdLocateLab/
├── run_agent.py                 # 真实研究 / 回放入口
├── agent_runtime/              # CLI provider、控制器、schema、持久状态
├── tools/                      # 研究工具、证据管理、数学检查
├── localization/               # 合成数据、求解器、基线与精确数学定义
├── benchmark_v2_1.py           # 正式独立测试入口（预先修订）
├── benchmark.py                # 初始化版本及共享校验/统计库，保留不改
├── sensitivity.py              # 预登记的观测数 × 污染比例敏感性网格
├── dashboard.py                # 只读本地仪表盘
├── locate.py                   # 将冻结方法应用于新观测
├── examples/observations.json  # 不含目标真值的接口示例
├── tests/                      # 软件与数学实现测试
├── runs/live_reference/        # 正式真实模型研究记录
├── results/benchmark/          # 独立评测输入、逐例预测和汇总
├── reporting/build_report.py   # 从实际记录生成 LaTeX 实证内容
├── latex/main.tex              # 完整论文排版入口
├── report/CrowdLocateLab_Report.pdf
└── docs/                       # 来源、文献、使用与提交说明
```

审计研究过程时，从 `events.jsonl` 中的一次 `model_response` 对照 `tool_call`、`tool_result`，再查其指向的逐例实验文件。冻结规格保存在 `runs/live_reference/tools/frozen_method.json`。模型请求原文、结构化响应和 CLI 原始事件位于 `model_calls/`。哈希链可检测意外修改，不等于第三方认证，也不能证明科学结论正确。详细证据路径与统计协议见 [复现说明](docs/reproduction.md)。

## 从保存结果重新构建 PDF

已交付 PDF 可直接阅读。重建报告还需要 `requirements-report.txt` 及包含 XeLaTeX、BibTeX、ctex、TikZ/PGFPlots、algorithm2e 等包的 TeX 环境。

```text
python -m pip install -r requirements-report.txt
python reporting/build_report.py --run runs/live_reference --results results/benchmark
python reporting/sensitivity_figures.py --run runs/live_reference --summary results/sensitivity/summary.json
cd latex
xelatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
xelatex -interaction=nonstopmode -halt-on-error main.tex
xelatex -interaction=nonstopmode -halt-on-error main.tex
```

`build_report.py` 只读取记录并生成主研究实证章节、绘图数据和 `latex/summary_metrics.json`；`sensitivity_figures.py` 根据配套敏感性结果更新该章节。两者都核对运行与结果的冻结记录，避免混用不同研究。编译所得文件为 `latex/main.pdf`。在回到项目根目录后，将其复制至 `report/CrowdLocateLab_Report.pdf`。正文或身份信息修改后重新编译即可，不需要重新调用模型。

当前 LaTeX 模板使用 Windows 字体配置。macOS / Linux 编译时需在 `latex/main.tex` 中将 `fontset=windows` 和 Times New Roman / Arial / Consolas 设置改为本机可用字体，例如 ctex 的 `fandol` 配置及 TeX Gyre 字体；这仅改变排版，不改变研究数据。Python 数值入口不依赖这些字体。

## 来源、许可与提交

架构借鉴 [Creative-Intelligence](https://github.com/DeepMathLLM/Creative-Intelligence) 将研究、验证和状态管理分开的思路；本项目为原创实现，没有复制该仓库源码，也不是 Moonshine 的 fork。方法依据和原始论文集中列于 [研究背景](docs/research_context.md) 与 [BibTeX 文献](docs/references.bib)。

[MIT LICENSE](LICENSE) 仅适用于本仓库原创程序代码。引用的论文、课程资料、商标与第三方软件保留各自权利；原始 PDF/PPT 未打包，也未通过本项目重新许可。项目未填入学生姓名、学号或学校，请按 [提交说明](docs/SUBMISSION.md) 补充。GitHub 仓库由提交者自行创建和上传。


### 可选：固定参考数值环境

参考运行使用 Python 3.12.14、NumPy 2.3.5、SciPy 1.18.1；模型调用的参考 CLI 版本为 0.155.0-alpha.16.4。若需更接近参考数值环境，可在独立 Python 3.12.14 虚拟环境中安装：

```text
python -m pip install -r requirements-lock.txt
```

`requirements.txt` 保留兼容版本范围，`requirements-lock.txt` 固定核心数值依赖；Python 和 CLI 需单独安装。该文件不包含账号、认证或模型配置，也不锁定远端模型。因此固定环境有助于复现数值计算，不保证真实大模型再次作出完全相同的研究决策。实际评测环境仍以结果 `protocol.json` 为准。
