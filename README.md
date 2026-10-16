# CrowdLocateLab

CrowdLocateLab 探索 AI 智能体如何研究**异步众包定位中的误差与歧义**：从数值实验发现方法的局限，再通过数学推导、反例构造和程序验证研究这些局限。

[![自动检查](https://github.com/orgyuanbao/CrowdLocateLab/actions/workflows/tests.yml/badge.svg)](https://github.com/orgyuanbao/CrowdLocateLab/actions/workflows/tests.yml)

[研究报告](report/CrowdLocateLab_Quality_Review.pdf) · [Agent 研究记录](studies/creative_intelligence/mirror_ambiguity/README.md) · [快速开始](#快速开始) · [上游 Agent](https://github.com/DeepMathLLM/Creative-Intelligence)

本项目选题来自《HarmonyOS 难题发布》第七期的众包离线位置聚合问题。当不同设备在不同时刻报告距离，观测又含有噪声和异常值时，我们希望回答两个问题：**目标现在在哪里？哪些情况下，现有观测不足以支持一个可信的位置判断？**

## 初步成果

目前形成了三类可以复查的研究产物：

- **定位歧义的精确反例。** 即使锚点非共线、距离映射的局部 Jacobian 满列秩，两条轨迹仍可能产生完全相同的观测，而当前位置相距 **6 m**。由此可得，任何单点估计在这两个可行世界中的最大误差至少为 **3 m**。[查看推导与反例](studies/creative_intelligence/mirror_ambiguity/README.md#一个可以手算的反例)
- **可运行的歧义见证检查器。** Agent 编写了使用有理数精确计算的程序，检查给定候选轨迹能否同时解释观测，并在验证轮修复了输入解析、文件覆盖和输入规模限制问题。[查看第二版程序](studies/creative_intelligence/mirror_ambiguity/artifacts/diagnostic_v2.py)
- **两轮定位方法研究与评测。** 已保存 27 次真实模型调用、25 个研究动作和 22,052 次冻结后的定位计算。最终方法在主合成测试中的平均误差为 **3.920 m**，对照 WLS 为 **6.643 m**；结果同时保留静态场景退化和覆盖不足等局限。[查看实验分析](docs/results_revision_review.md)

完整交付包括 [52 页定位研究论文](report/CrowdLocateLab_Report.pdf) 与 [12 页 Agent 研究及复核补充报告](report/CrowdLocateLab_Quality_Review.pdf)，对应源文件保存在 [latex/](latex/)。

## 当前研究方向

1. **异步、含异常观测下的稳健定位。**  
   研究运动建模、异常值处理和不确定区域如何影响定位误差；通过对照实验与失败案例，确定方法的适用范围。

2. **从“得到一个位置”到“判断是否存在歧义”。**  
   研究同一观测是否允许明显不同的当前位置，以及如何给出可计算的歧义见证和误差下界，为拒绝不可靠估计提供依据。

当前研究使用合成数据与明确假设下的数学模型。已有成果是原开放问题的子问题进展，工业数据验证仍在后续工作中。

## 最新进展：Mathematical Concept Agent

本项目直接采用 [Creative-Intelligence Stage 2](https://github.com/DeepMathLLM/Creative-Intelligence/tree/b7d17249f94949351faefd7ae28a020f0623db93/mathematical-concept-agent) 的 **Mathematical Concept Agent**，通过其原始控制器调用 Codex，并恢复同一研究会话。我们为它提供定位题面、参考材料、构造与验证 Skills，以及研究过程的保存和复算入口。

这条研究流程围绕“**提出条件 → 推导与构造 → 编写验证程序 → 尝试反驳 → 修订结果**”展开。已完成的两个真实研究单元如下：

| 研究单元 | Agent 实际完成的工作 | 保存的产物 |
| --- | --- | --- |
| 第一轮：构造 | 区分给定观测与存在共同观测两个问题，推导反射条件与误差下界，构造案例并编写检查器 | [数学推导](studies/creative_intelligence/mirror_ambiguity/artifacts/research_v1.md)、[11 个案例](studies/creative_intelligence/mirror_ambiguity/artifacts/fixtures_v1.json)、第一版程序 |
| 第二轮：验证与修订 | 恢复会话，另写区间验证程序，检查变换与边界案例，修复程序缺陷 | [验证记录](studies/creative_intelligence/mirror_ambiguity/artifacts/verification_v2.md)、第二版程序、失败与通过的检查结果 |

“两个研究单元”指控制器接受的两次研究推进，每轮内部包含多次工具操作。第二轮属于同模型自审，后续另行复算也单独留档。实际指令、原始事件和状态见 [完整研究记录](studies/creative_intelligence/mirror_ambiguity/README.md)。

### 研究得到的方法

研究采用二维恒速模型，以当前时刻为 $t=0$；$x$ 为当前位置，$v$ 为速度，$a_i$、$t_i$、$y_i$ 分别为锚点、相对观测时刻和距离观测。在锚点与时刻精确已知、测距误差具有确定性上界 $\epsilon_i\geq 0$ 的条件下，对给定候选轨迹检查：

$$
d_i(x,v)=\lVert x+t_i v-a_i\rVert_2,\qquad
|y_i-d_i(x,v)|\leq \epsilon_i.
$$

如果两个候选都满足同一观测，且当前位置相距 $D$，那么任何单点估计在这两个候选上的最大误差至少为 $D/2$。检查器输出可核验的候选及位置分离的精确平方值，帮助说明定位结果为何可能不可信。

该程序目前检查**有限候选**：未找到见证时，仍不能证明全局唯一。这里的误差上界 $\epsilon_i$ 是确定性硬界，与数值实验中的噪声标准差 $\sigma_i$ 含义不同。自动发现候选、连续参数空间的唯一性判定，以及锚点和时钟误差，是下一步研究重点。

---

## 已有研究：定位方法的迭代与评测

在接入上述 Agent 之前，本项目使用独立的工具驱动研究流程完成了两轮数值研究：模型提出假设、选择实验和修订策略，数值工具执行计算，方法冻结后再进行评测。

| 主合成测试的平均定位误差 | WLS 基线 | 第一轮方法 | 第二轮方法 |
| --- | ---: | ---: | ---: |
| 误差（m） | 6.643 | 4.064 | **3.920** |

相对第一轮的配对改进区间跨零，尚不足以断言总体显著改善；第二轮在静态主测试中相对 WLS 退化 **29.86%**，标称 90% 的分组区域实际覆盖为 **88.17%**。这些结果促使研究进一步关注可辨识性、全局歧义和拒绝依据。

两轮原始轨迹分别保存在 [第一轮](runs/live_reference/) 与 [第二轮](runs/revision_reference/)，评测数据见 [results/](results/)。它们与新接入 Agent 的两个研究单元分别记录。[研究归档](archives/revision_reference/archive.json)可沿假设、工具结果和审阅意见检查迭代过程。

## 快速开始

下载或克隆完整仓库，在项目根目录运行以下命令。需要 **Python 3.11+**。

### 复算 Agent 的数学研究

仅使用 Python 标准库，不需要模型账号：

```text
python -B studies/creative_intelligence/mirror_ambiguity/reproduce_checks.py
```

程序在临时副本中运行保存的检查，复核反例、候选判据与程序边界。命令重算已有成果；新研究需使用下节的 Agent 入口。

### 浏览已有实验

```text
python dashboard_revision.py --port 8766
```

随后在浏览器打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)，查看实验结果与失败案例。研究浏览器同样只依赖标准库。

如需重跑数值实验或处理自己的观测，先安装 [requirements.txt](requirements.txt) 中的依赖，再按 [数值复现说明](docs/reproduction_revision.md) 或 [定位应用说明](docs/application_revision.md) 操作。

## 运行参考项目的 Agent

真实研究需要已安装、已登录且有可用额度的 **Codex CLI**。控制器使用 Python 标准库；研究程序所需的计算环境由题面说明。这里使用上游 Stage 2 的 Codex 入口。

准备 UTF-8 的 `problem.md` 和参考材料目录 `materials/`，可参考本项目的 [研究题面](studies/creative_intelligence/mirror_ambiguity/problem.md) 与 [材料](studies/creative_intelligence/mirror_ambiguity/materials/)。复用这些材料时，先制作自己的副本，并将其中的环境说明改为本机可用的 Python 路径。

在项目根目录执行；工作目录须位于仓库之外，并使用尚未初始化的新目录：

```text
python -B integrations/creative_intelligence/runner.py init --workspace ../creative-research-runtime --run-id localization-study --problem problem.md --materials materials
python -B integrations/creative_intelligence/runner.py validate --workspace ../creative-research-runtime --run-id localization-study
python -B integrations/creative_intelligence/runner.py advance --workspace ../creative-research-runtime --run-id localization-study
```

- `init` 保存题面、材料与初始状态；`validate` 检查配置和 CLI。
- 每次 `advance` 推进一个真实研究单元。查看产物后，再次执行该命令可恢复会话；也可用 `--prompt-file` 提供后续指令。
- 研究产物与调用证据分别保存，保留失败结果和每轮状态。一个研究单元内部的工具次数、时长和模型用量会随研究变化。

完整配置、恢复方式和运行边界见 [Agent 接入说明](integrations/creative_intelligence/README.md)。已有定位实验的独立研究入口为 [run_research.py](run_research.py)，复现方法见 [相关文档](docs/reproduction_revision.md)。

## 验证与研究记录

安装数值依赖后，可运行软件测试：

```text
python -m pip install -r requirements.txt
python -B -m unittest discover -s tests -v
python -B integrations/creative_intelligence/run_vendor_tests.py
python -B -m unittest discover -s integrations/creative_intelligence/tests -v
```

当前测试共 275 项，覆盖数值工具、运行控制器与接入层；这些测试不调用真实模型。云端在 Windows / Linux、Python 3.11 / 3.12 上执行检查，状态见 [GitHub Actions](https://github.com/orgyuanbao/CrowdLocateLab/actions/workflows/tests.yml)。

研究结论还需结合推导、实验与复算评估。仓库保留 [原始研究证据](studies/creative_intelligence/mirror_ambiguity/evidence/)、[数学复算说明](studies/creative_intelligence/mirror_ambiguity/README.md#无模型复算)、[历史实验复现协议](docs/reproduction_revision.md)和 [质量复核记录](docs/QUALITY_REVIEW.md)。

## 项目结构

```text
integrations/creative_intelligence/    上游 Agent、接入入口与研究 Skills
studies/creative_intelligence/         新研究题面、数学产物、迭代证据与复算入口
runs/                                 两轮已有定位研究的真实运行记录
results/                              定位预测、对照实验与覆盖结果
localization/  revision/               定位算法与第二轮研究方法
agent_runtime/  tools/                 已有工具驱动研究流程
archives/                             可追溯的研究卡片
tests/  verification/                  软件测试与核验记录
latex/  report/                        LaTeX 源码与 PDF 报告
docs/                                 研究背景、使用与复现说明
```

## 来源与许可

本项目是数学科研智能体课程作业，学生为 **徐楷沅（2352163）**。

Agent 直接接入自 [DeepMathLLM/Creative-Intelligence](https://github.com/DeepMathLLM/Creative-Intelligence)，固定版本为 `b7d17249f94949351faefd7ae28a020f0623db93`。上游原始文件与 MIT 许可保存在 [vendor/](integrations/creative_intelligence/vendor/)，版本和文件摘要见 [UPSTREAM.json](integrations/creative_intelligence/UPSTREAM.json)。该公开快照缺少的两份构造 / 验证 Skill 由本项目补写并注明来源；定位工具、研究题面和证据归档也由本项目提供。

数学背景与参考文献见 [研究背景](docs/research_context.md)和 [文献表](docs/references.bib)。本项目原创代码采用 [MIT License](LICENSE)，第三方材料保留各自权利。
