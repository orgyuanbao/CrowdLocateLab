# 课程提交与答辩说明

项目：**CrowdLocateLab 双轮研究版——面向异步众包定位的科研智能体开发与实证研究**。第二轮的状态、实际次数和指标以保存记录为准；尚未冻结或评测未完成时，不将计划数写成完成数。

提交前请填写真实信息，不要使用示例身份：

| 字段 | 提交者填写 |
| --- | --- |
| 姓名 | 待补充 |
| 学号 | 待补充 |
| 学校 / 院系 | 待补充 |
| 课程名称 / 任课教师 | 待补充 |
| 个人 GitHub 用户名 | [orgyuanbao](https://github.com/orgyuanbao) |
| 仓库链接 | [orgyuanbao/CrowdLocateLab](https://github.com/orgyuanbao/CrowdLocateLab) |
| 提交日期 | 按实际日期填写 |

PDF 封面在 `latex/main.tex` 中。两轮实证章节已经生成后，填写身份并使用 `python reporting/compile_report.py --skip-refresh` 编译；需要从完成证据更新实证时使用不带该选项的正常编译命令。无需为填写身份重新调用模型。

## 作业要求与证据对照

| 作业要求 | 本项目的对应内容 | 建议检查的文件 |
| --- | --- | --- |
| 开发一个智能体 | 实际大模型提出动作；控制器执行工具、保存记忆并安排批评；第二轮要求诊断后的修订与验证对照 | `run_revision.py`、`revision/agent.py`、`agent_runtime/`、`tools/` |
| 应用于数学未解决问题，或提出新问题 / 方法 / 概念 | 从原工业开放题提取异步污染定位子问题，研究残差建模、运动选择和不确定性 | `docs/research_context.md`、`localization/method_math.md`、报告理论章节 |
| 体现运行与迭代 | 两轮真实响应、假设、诊断、修订、消融与冻结；第一轮失败明确作为第二轮已暴露先验 | 两个正式 runs 目录中的 `events.jsonl`、`model_calls/`、`tools/` |
| 总结运行与迭代结果为 PDF | 报告包含方法、推导、轨迹、对照评测、失败及限制 | `report/CrowdLocateLab_Report.pdf` |
| 连同代码上传个人 GitHub | 保留可运行源码、依赖、测试、正式证据、合成输入和逐例输出 | 根目录 README 与本说明的上传清单 |
| 可以基于开源智能体二次开发 | 本项目选择原创实现，借鉴 Creative-Intelligence 的模块分工并明确致谢 | README“来源、许可与提交” |

“未解决问题”的准确表述应为：**原工业开放问题的合成数学子问题研究**。不能表述成“已解决华为题目”“证明所有场景更优”或“首次发明稳健定位 / 共形预测”。题目未给定真实数据和完整验收协议，项目结果不构成工业验收。原题的“运动场景精度提升 20%”缺少明确定义，报告中的平均误差或 CEP90 降幅不能直接替代该指标。

智能体贡献在于可审计的研究动作和证据更新。预实现的数学算子、作者给出的命题证明、模型提出的具体组合、数值实验观察是不同贡献，应分别说明。软件测试中的假模型与 `replay` 都不是新增真实模型研究。

## 建议的 5–8 分钟答辩演示

使用正式保存的记录演示，可避免现场网络或模型等待影响展示。明确区分已完成证据和当前运行状态；不要把回放说成现场重新推理。双轮浏览器的一页演示路径见 [DEFENSE_DEMO.md](DEFENSE_DEMO.md)，重点是第一轮失败如何成为第二轮修订的动机。

| 时间 | 内容 | 屏幕内容 |
| --- | --- | --- |
| 0:00–1:00 | 原工业题、所取子问题、可获得数据与研究边界 | 报告问题定义和数据模型 |
| 1:00–2:00 | 为什么这是模型驱动智能体；模型与数值工具如何分工 | 系统图、真实 provider 标记、一次模型响应及对应工具结果 |
| 2:00–4:00 | 顺着实际时间线讲一个“假设—实验—诊断—修订—验证”的过程 | 仪表盘中的实际研究动作、批评和方法注册；同时展示保留下来的失败或被拒绝假设 |
| 4:00–5:30 | 一个数学命题、条件及数值见证；解释证明与实验的不同 | 报告中的噪声偏移 / 镜像反例 / 局部误差界任选一个 |
| 5:30–7:00 | 冻结后测试、基线、消融、分场景结果和覆盖率限制 | 评测表、成对区间、压力或拒绝曲线；说明任何未改善处 |
| 7:00–8:00 | 重现入口和未解决部分 | README 命令、正式记录目录、后续真实数据需求 |

演示前，在项目根目录执行：

```text
python dashboard_revision.py --port 8766
```

打开 <http://127.0.0.1:8766>，切换 V2 / Revision。需要实时模型调用时，用 `run_revision.py --provider codex` 和新的目录；说明输出与耗时可能不同，不替换正式记录。第二轮操作见 [reproduction_revision.md](reproduction_revision.md)，浏览器说明见 [DASHBOARD.md](DASHBOARD.md)。

可准备回答的问题：

- 模型究竟决定了什么？请用一条实际模型动作及其工具返回举证，不能只展示聊天回答。
- 为什么比较距离与对数残差，最终为何保留距离残差？对数修正项来自什么噪声假设，该假设失效时怎么办？
- 为什么局部 Jacobian 满秩仍不排除镜像歧义？
- 静态 / 运动选择如何避免读取目标真值？开发验证和最终测试如何隔离？
- 覆盖率何时有理论依据？为什么拒绝部分定位后不能直接沿用同一个覆盖承诺？
- 哪些场景失败，计算代价多大？一次有界探索能支持什么强度的结论？

回答必须以交付的实际配置、日志与数字为依据，不用预先写好的理想结果代替。

## 上传内容

建议让 GitHub 仓库打开后直接看到 `README.md`，不要只上传一个压缩包。

| 保留内容 | 原因 |
| --- | --- |
| `README.md`、`LICENSE`、`.gitignore`、`.gitattributes`、`requirements*.txt` | 项目入口、原创代码许可、环境及审计文件字节保留 |
| `run_revision.py`、`benchmark_revision.py`、`locate_revision.py`、`dashboard_revision.py`、`doctor.py`、`audit.py` | 新版研究、正式评测、应用、双轮展示、环境与完整链检查 |
| `run_agent.py`、`benchmark.py`、`benchmark_v2_1.py`、`sensitivity.py`、`dashboard.py`、`locate.py` | 保留第一轮入口和冻结依赖，不删除旧校验库 |
| `Start-Dashboard.ps1`、`Start-Dashboard.sh` | 不安装依赖、不修改策略的界面启动器 |
| `examples/` | 不含目标真值的观测接口示例 |
| `agent_runtime/`、`tools/`、`localization/`、`revision/`、`tests/` | 原数值 / 运行器及独立第二轮扩展、测试 |
| `docs/`、`reporting/`、`latex/`、`report/` | 来源引用、复现说明、报告源码、实证生成器与最终 PDF |
| `runs/live_reference/`、`runs/revision_reference/` | 两轮真实模型调用、状态、证据链及冻结记录；未完成部分须标明状态 |
| `results/benchmark/`、`results/revision_benchmark/` | 两轮协议、合成输入、逐例预测与实际完成汇总；不把部分输出当完整结果 |
| `results/sensitivity/` | 已完成的预登记网格配对预测、分格误差与模型选择诊断 |
| `verification/` | 各次实际测试原始输出与两轮完整链审计，次数以记录为准 |
| `research/` | 数学见证、两轮计划、评估器预先修订的快照与 diff |
| `.github/`、`SHA256SUMS.txt`（若随最终包提供） | CI 配置及最终交付清单；清单应与最终文件对应 |

不上传虚拟环境、缓存、临时连通探针、开发中断记录或未采用的演练目录。报告正式引用的失败实验应保留在正式记录中，不能因为结果不理想而删除。

原始用户课件和难题 PDF/PPT 不在本项目授权范围内；上传来源名称、页码、论文链接和必要摘要即可，不添加原文件。项目不需要 `.env`、API key、登录 cookie、`auth.json`、个人 Codex 配置目录或任何账号凭据。提交前审阅模型请求和 CLI 诊断中是否包含不适合公开的个人路径或信息；如确需制作脱敏副本，应说明变更并保留原始证据的私有副本，不将重写后的哈希链冒充原始记录。

## 自行上传个人 GitHub

本说明保留自行发布、迁移仓库及课程提交的操作步骤。实际仓库地址以发布后的 GitHub 页面为准。

### 网页方式

1. 登录自己的 GitHub 账号，创建空仓库，例如 `CrowdLocateLab`，可见性按课程要求选择。
2. 在空仓库页面选择上传文件，上传项目根目录中的文件与文件夹，保持目录层级；大批量内容可分批上传。
3. 每批完成后提交更改。核对首页 README、最终 PDF、源码、正式模型记录和测试结果都能打开。
4. 在本说明中补充仓库地址，提交课程平台要求的链接与 PDF。网页文件数量或大小限制以 [GitHub 官方上传说明](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository) 为准；文件较多时用下面的 Git 方式。

### Git 命令行方式

先在自己的 GitHub 账号下创建**空仓库**，不要勾选自动生成 README、License 或 `.gitignore`。在项目根目录逐条执行下面的命令，将 `YOUR_USERNAME` 替换为自己的真实用户名。认证使用自己的 GitHub 登录流程，不把 token 写进 URL 或源码。

```text
git init
git branch -M main
git add README.md START_HERE.md LICENSE .gitignore .gitattributes requirements.txt requirements-report.txt requirements-lock.txt
git add run_agent.py benchmark.py benchmark_v2_1.py audit.py sensitivity.py dashboard.py locate.py
git add run_revision.py benchmark_revision.py locate_revision.py dashboard_revision.py doctor.py Start-Dashboard.ps1 Start-Dashboard.sh
git add agent_runtime tools localization revision tests docs reporting latex report research examples verification
git add runs/live_reference runs/revision_reference results/benchmark results/sensitivity
git status --short
```

第一轮敏感性证据应包含 `results/sensitivity/`。第二轮结果完成后再加入 `git add results/revision_benchmark`；如仍在运行或有失败尝试，应明确状态，不把缺失汇总称作完成。已有 `.github/` 和最终有效 `SHA256SUMS.txt` 可按实际交付加入暂存。`.gitattributes` 的 `* -text` 用于保留源码与证据原始字节，避免 Git 换行转换使已冻结的 SHA-256 校验失败；不要遗漏此点文件，也不要在冻结后统一格式化证据文件。

先检查暂存列表：仅保留正式交付，尤其确认 `agent_runtime/` 等源码目录中没有连通探针或缓存。`.gitignore` 不会自动排除所有个人临时文件；如暂存了某个不应公开的文件，在提交前用 `git restore --staged -- 路径` 撤出该文件，或在首次提交前使用 `git rm --cached -- 路径`。对目录可加 `-r`，这些命令应只修改暂存状态，不删除工作文件。确认后再执行：

```text
git commit -m "Add CrowdLocateLab research agent and reproducible study"
git remote add origin https://github.com/YOUR_USERNAME/CrowdLocateLab.git
git push -u origin main
```

如果已有 Git 仓库或已有 `origin`，先运行 `git status` 和 `git remote -v` 检查现状，按自己的仓库配置操作，不重复初始化或覆盖现有远端。上传后从 GitHub 重新下载一份，至少打开报告和关键证据，并按 README 安装依赖、运行测试；若做数值重现，应输出到新目录。

## 提交前核对

- 补全真实身份与仓库链接，最终 PDF 与 LaTeX 源码对应。
- 两轮分别执行 `python audit.py --run 对应目录 --require-frozen`，确认完整链审计通过；运行中通过不等于冻结完成。
- 保留真实调用、回放、软件夹具和数值评测的区别，第二轮旧先验不能重称新测试。
- 报告数字来自正式结果文件；包含对基线无改善、压力失效或条件不足的实际结果。
- 解释合成数据边界、原题指标差异、局部数学条件及覆盖率适用范围。
- 源码、依赖、测试、正式轨迹和最终报告上传完整；课件和凭据不上传。

第一轮正式入口为 `benchmark_v2_1.py`，第二轮为 `benchmark_revision.py`；原 `benchmark.py` 是冻结依赖和共享校验库，必须保留。还应提交 `research/evaluator_amendment.md`、原始快照、manifest、diff 和 `research/revision_plan.json`，说明旧评估器预先修订和新一轮研究的边界，不能择优混用成绩。
