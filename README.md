# CrowdLocateLab · 双轮数学科研智能体

**从第一轮失败中提出修订，再用独立数据检验。** 项目研究异步、污染距离观测下的当前位置估计：真实大模型选择研究工具、提出与修订假设，本地数值程序执行实验；方法冻结后另行审计和评测。代码、模型记录、合成输入、逐例预测与中文论文共同组成可审阅的课程项目。

来源为《HarmonyOS难题发布》第七期第 1 页“基于非线性优化的众包离线高精度位置聚合难题”。这里处理的是原工业开放问题的**合成数学子问题**，没有声称解决完整工业题、著名数学猜想或首次发明稳健估计、交叉验证、共形预测。原题“运动场景精度提升 20%”未给出完整指标定义；本项目的平均欧氏误差、CEP90 和分场景结果不能直接代替工业验收。

## 从这里开始

2026-10-12 工程审查发现并处理了原测试未覆盖的运行恢复、回放完整性、异常 JSON、输入来源绑定和数值稳定性问题。新运行使用 `run_research.py`；原始入口、冻结核心、两轮结果和 52 页论文保留作历史证据。应用入口增加独立数值筛查，失败时明确拒绝使用原估计。详见 [审查记录](docs/QUALITY_REVIEW.md) 和 [LaTeX 补充报告](report/CrowdLocateLab_Quality_Review.pdf)。测试通过不等于零缺陷，也不构成工业精度证明。

进一步复核补齐了实验产物校验、诊断读取快照和部分工具失败后的停止保护，并修正通过数统计与论文尺度公式说明。当前检查点格式为第 2 版，旧检查点不自动升级。首轮记录保留；权限受限时的结果见 [复核记录](verification/hardening/final_checks.json)，恢复网络权限后的补验见 [最新检查记录](verification/hardening/network_enabled_checks.json)。

2026-10-12 恢复网络权限后重新运行完整检查：**208 项中 207 项通过，0 失败、0 错误，1 项因本机符号链接权限跳过**。之前受限的两项 HTTP 集成测试已通过；符号链接用例仍需以对应提交的 Linux CI 结果为准。两轮历史审计及 123,279 项统计一致性检查通过。6 页补充报告保留上一阶段的真实检查快照，后续状态见最新检查记录和审查文档。

初次使用请读 [START_HERE.md](START_HERE.md)。无需模型账号即可查看已保存证据：

```text
python dashboard_revision.py --port 8766
```

打开 <http://127.0.0.1:8766>，切换 V2 / Revision，查看真实时间线、批评、失败案例几何图、Global / Mondrian 校准和观测数边界。界面仅用 Python 标准库，不运行模型或数值实验。缺失或运行中的结果显示等待，不能当作零误差。详见 [研究浏览器指南](docs/DASHBOARD.md)。

| 内容 | 第一轮 V2 | 第二轮 Revision |
| --- | --- | --- |
| 模型记录 | [runs/live_reference](runs/live_reference/) | [runs/revision_reference](runs/revision_reference/) |
| 独立评测 | [results/benchmark](results/benchmark/) | [results/revision_benchmark](results/revision_benchmark/) |
| 追加分析 | [敏感性网格](results/sensitivity/) | 评测目录内的 edge、分组校准与非劣诊断 |
| 历史研究入口 | `run_agent.py` | `run_revision.py` |
| 正式评测入口 | `benchmark_v2_1.py` | `benchmark_revision.py` |
| 新观测应用 | `locate.py` | `locate_revision.py` |

[中文报告](report/CrowdLocateLab_Report.pdf)与 [LaTeX 源码](latex/main.tex)用于提交。两轮研究及第二轮独立评测均已完成，以下数字来自保存记录：

| 已完成内容 | 实际记录 |
| --- | --- |
| 第一轮 / 第二轮研究动作 | 13 / 12 个；合计 25 个 |
| 第一轮 / 第二轮真实模型调用 | 14 / 13 次；合计 27 次，包含两次独立提示审阅 |
| 两轮实验与消融调用 | 各 6 次；第二轮登记 4 个新配置 |
| 冻结后定位计算 | 第一轮 6,192 次 + 第二轮 15,860 次 = **22,052 次** |
| 原版本软件检查 | **129 项测试通过**；本次加固检查另见审查记录 |
| 独立统计核对 | 123,279 项一致性检查通过，重新核对误差、155 组配对比较和覆盖 |
| 新主测试 | 所选方法 3.920 m，WLS 6.643 m，第一轮方法 4.064 m |
| n=8 运动边界 | 第一轮 10.788 m → 第二轮 6.217 m；恒速基线仍更低（5.107 m） |
| 必须保留的失败 | 新主测试静态误差较 WLS 高 29.86%；90% 分组区域实际覆盖 88.17% |

新主测试相对第一轮方法的改善区间跨零，不能声称总体明确优于上一轮；n=8 静态场景则由 1.888 m 退化至 2.746 m。95% 分组区域实际覆盖为 95.00%，也不能据此隐去 90% 水平的不足。详见 [第二轮独立结果审查](docs/results_revision_review.md)。测试数、定位计算次数与模型调用次数含义不同；相同配置的别名不计作额外独立计算。

运行身份、冻结与完成状态分别保存在 `runs/revision_reference/state.json`、`results/revision_benchmark/status.json` 和 `summary.json`。已有结果重算不增加独立研究次数。

第一轮发现了正常静态退化、稀疏观测下 CV 排除运动模型、实际覆盖不足等问题。第二轮把这些旧结果作为**已暴露先验**，使用新开发与评测种子；它是延续研究，不是对第一轮研究过程的完全独立重复。第一轮冻结源码和记录保持原样。[V2 README 归档](docs/README_V2.md)保留历史正文并修正了归档位置下的链接。

## 环境检查与安装

研究和数值入口要求 Python 3.11+。数值核心仅依赖 NumPy、SciPy；报告另需 TeX 和 PDF 工具。

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe doctor.py
```

后文的 `python` 可替换为 `.\.venv\Scripts\python.exe`，无需更改执行策略。macOS / Linux：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python doctor.py
```

`doctor.py` 只检查本地 Python、数值库和 `codex --version`，不登录、不读凭据、不调用模型，也不验证账号额度。`--json` 输出机器可读结果；`--require-codex` 将缺少 CLI 视为错误。可改装 [requirements-lock.txt](requirements-lock.txt) 以接近参考数值环境，但版本固定不能保证模型重新作出相同决策。

```text
python -m unittest discover -s tests -v
```

测试中的假模型和小型夹具用于软件检查，不是正式科研运行或留出评测。

## 区分阅读、离线复现和真实模型运行

| 工作 | 新模型调用 | 数值计算 | 解释 |
| --- | --- | --- | --- |
| 阅读 PDF、JSON 或研究浏览器 | 否 | 否 | 审阅已有证据 |
| replay、冻结方法评测、新观测定位 | 否 | 是 | 重放或数值应用，不产生新模型推理 |
| `run_research.py --provider codex` | 是 | 是 | 使用已公开协议的工程运行，不是新盲测 |

### 审计后重算第二轮评测

前提是第二轮已冻结。先检查**完整历史证据链**：

```text
python audit.py --run runs/revision_reference --require-frozen
```

只有退出码 0、`passed_frozen` 时继续。退出码 3 表示尚未冻结，退出码 1 表示完整性失败。运行中审计通过只说明已保存部分一致，不授权生成留出数据。

再检查冻结规格、源码和预登记方案；此步不生成数据：

```text
python benchmark_revision.py --run runs/revision_reference --validate-only
```

通过后才执行独立评测，输出必须是不存在的新目录：

```text
python benchmark_revision.py --run runs/revision_reference --output results/reproduction_revision
```

评测入口会再次校验冻结来源，但不替代前面的完整链审计。[revision_plan.json](research/revision_plan.json) 计划主测试 720 例、压力 400 例、n=6…12 边界 420 例、校准 800 例、覆盖评价 1200 例；这些是设计参数。有效配置相同的方法别名复用预测，实际计算数与逻辑行数分别记录，不能当成独立重复试验。

重现使用相同种子，不增加独立样本，也不能在看过结果后重新称为首次盲测。详见 [第二轮复现说明](docs/reproduction_revision.md)。

### 发起真实模型研究

需安装 Codex CLI、用自己的账号登录并具备模型额度。[官方 CLI 文档](https://learn.chatgpt.com/docs/codex/cli)说明安装与登录；项目复用正常 CLI 认证，不要求把凭据写入源码。

```text
codex --version
codex login status
python doctor.py --require-codex
python run_research.py --output runs/my_revision --provider codex --timeout 300 --reasoning-effort medium
```

程序沿用 CLI 默认模型，本次临时设置 medium 推理强度；名称只采用 CLI 实际报告值。若 CLI 不在 PATH，可在当前 PowerShell 设置：

```powershell
$env:CODEX_EXECUTABLE = 'C:\path\to\codex.exe'
```

第二轮默认预算为 20 个动作、8 次实验 / 消融、28 次模型请求，后者包含审阅与重试；验证最多 4 次调用。冻结要求诊断后的修订、有效消融和验证对照，获胜不是冻结前提。持续模型失败会记录并终止，没有静默规则后备。

新入口使用已公开的研究协议与 V2 先验，不覆盖正式目录；沿用原种子，不能将重算称为新的盲测或独立科学发现。无法冻结时可诚实停止为未冻结状态；恢复前检查状态与事件链尾、运行身份和中断请求，异常时保留证据并拒绝继续。冻结后才对 `runs/my_revision` 依次执行审计、`--validate-only` 和新目录评测。原 `run_revision.py`、`run_agent.py` 仅用于历史源码复现，不含这些新增保护。

### 显式回放

来源记录完整后执行：

```text
python run_research.py --output runs/reproduction_replay --provider replay --replay-source runs/revision_reference
```

回放先验证来源事件链并绑定源文件哈希，再读取保存的模型响应、重做工具计算；不作新推理。响应耗尽会报错，不转成在线模型。默认回放不代表所有提示逐字一致；新入口提示已改变，`--strict-replay` 对历史记录通常会报提示不匹配。回放不能作为第二次独立模型研究。

## 应用于新观测

第二轮已冻结且独立校准完成后：

```text
python locate_revision.py --input examples/observations.json --run runs/revision_reference --calibration results/revision_benchmark/conformal.json --region mondrian --alpha 0.1 --max-radius 20 --output results/my_revision_location.json
```

输入只有 `anchors`、`times`、`ranges`、`sigma`，不接受真值或场景标签。输出为 t=0 位置、速度、诊断、误差区域与接受 / 拒绝结果。必须先检查 `numerical_quality.passed`：数值筛查失败时，即使半径小于阈值或未指定阈值，也会拒绝。筛查多执行一次诊断拟合，原论文计时不包含该开销。`--region global` 使用总体校准；20 m 是示例阈值，不是已证明最优阈值。

加固前保存的接口演示：[20 m 阈值接受](results/example_revision_location.json)、[1 m 阈值拒绝](results/example_revision_rejected.json)。新入口示例见 [本次数值检查输出](verification/hardening/example_prediction.json)。同一输入估计位置约为 (19.386, 21.668) m，分组半径约 7.199 m；输入没有真值，不报告其真实定位误差。这些接口演示不计入正式研究计算次数。

现场观测与合成校准不同分布时不自动保证覆盖，拒绝后的子集也不自动继承名义覆盖。详见 [第二轮应用说明](docs/application_revision.md)。

## 报告重建与旧入口

已保存 PDF 可直接阅读。需要的研究与评测完成后，安装报告依赖并配置 XeLaTeX / BibTeX：

```text
python -m pip install -r requirements-report.txt
python reporting/compile_report.py
```

脚本读取保存证据生成两轮实证章节与图表，编译 `latex/main.tex`，输出 `report/CrowdLocateLab_Report.pdf`；不运行新模型或定位实验。可用 `--xelatex`、`--bibtex` 指定工具，`--output` 指定 PDF。`--skip-refresh` 只编译已有章节，适合修改封面，不能将旧章节冒充新结果。模板提供 Windows 字体与 Fandol / TeX Gyre 回退，仍需检查最终页面。

第一轮保留入口：

```text
python audit.py --run runs/live_reference --require-frozen
python dashboard.py --run runs/live_reference --results results/benchmark --port 8765
```

第一轮重算应在完整链审计通过后使用 `benchmark_v2_1.py`，输出到新目录。原 `benchmark.py` 是初始化版本及共享校验 / 统计库，不是正式 V2 评测入口。预先修订的时间、快照和 diff 见 [evaluator_amendment.md](research/evaluator_amendment.md)。旧应用和复现详见 [application.md](docs/application.md)、[reproduction.md](docs/reproduction.md)。

## 主要目录与边界

```text
run_revision.py                 第二轮真实研究 / 回放
revision/                       新研究策略、评测与报告方法
benchmark_revision.py           第二轮正式评测入口
audit.py / doctor.py             历史证据审计 / 本地环境检查
locate_revision.py              新观测估计、区域与拒绝
dashboard_revision.py           双轮只读研究浏览器
agent_runtime/ tools/ localization/  保留的运行器、工具与数值模块
runs/live_reference/            第一轮模型证据
runs/revision_reference/        第二轮模型证据
results/benchmark/ sensitivity/ 第一轮评测及敏感性
results/revision_benchmark/     第二轮测试、边界与校准
research/revision_plan.json     第二轮预登记
reporting/compile_report.py     PDF 重建
latex/ report/ docs/ verification/  论文、说明与实际核验记录
```

智能体组合已实现算子、设计实验和修订假设，不在运行中任意编写求解器。合成数据、局部非线性优化与观测 CV 没有所有场景非劣保证。静态非劣界限与 bootstrap 区间仍属探索性诊断；多数据种子不是多次独立智能体重复。Global / Mondrian 覆盖、固定校准规则后的 Wilson 区间、真实场景表现和拒绝后覆盖须分别解释。

架构借鉴 [Creative-Intelligence](https://github.com/DeepMathLLM/Creative-Intelligence) 的研究、验证和状态分工；本项目原创实现，没有复制其源码，也不是 Moonshine fork。来源与文献见 [research_context.md](docs/research_context.md)、[references.bib](docs/references.bib)。[MIT LICENSE](LICENSE) 仅覆盖原创代码；原课件与凭据不打包。

学生身份须自行补充。GitHub 发布与课程提交的清单、步骤见 [SUBMISSION.md](docs/SUBMISSION.md)，演示见 [DEFENSE_DEMO.md](docs/DEFENSE_DEMO.md)。
