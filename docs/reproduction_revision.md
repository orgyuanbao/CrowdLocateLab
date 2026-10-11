# 第二轮复现：从证据审计到评测

命令均在项目根目录执行。本文描述接口与预登记设计，不宣布某次运行已完成；实际状态、次数、模型标识和成绩以保存记录为准。

**工程加固说明（2026-10-12）**：本文保留原 `run_revision.py` 的历史复现命令。日常新增运行请按 [README](../README.md) 使用 `run_research.py`，它增加严格 JSON、状态/日志恢复预检和回放来源绑定，并明确已公开协议的研究边界。原运行目录不可直接作为新入口的恢复目录；历史源码和结果没有被改写。旧入口缺少这些新增保护。详见 [审查记录](QUALITY_REVIEW.md)。

## 两轮边界与环境

第一轮 runs/live_reference、results/benchmark、results/sensitivity 是已暴露先验。第二轮运行和评测分别在 runs/revision_reference、results/revision_benchmark。旧测试不能重新标作未见测试，重复计算同一协议也不增加独立样本。

原 agent_runtime、tools、localization 及第一轮初始化入口保持冻结字节；新策略在 revision、run_revision.py、benchmark_revision.py。初始化哈希包括预登记文件、完整 revision Python 源码和关键入口。修改这些文件后不能继续用旧记录，不能重写哈希绕过检查。保留 .gitattributes，避免换行转换破坏哈希。

使用 Python 3.11+，安装 requirements.txt；可选 requirements-lock.txt 接近参考数值环境，但它不固定远程模型及其决策。

```text
python doctor.py
python doctor.py --json
python -m unittest discover -s tests -v
```

doctor 只检查本地导入和 codex --version，不登录、不读凭据、不调用模型。ready 不表示认证、额度和网络一定可用。软件夹具不作为正式实验；实际测试输出可存 verification，不预填通过次数。

## 先完整链审计，再冻结校验，再生成评测

完整历史审计包含事件、原始模型请求 / 响应及历史工具证据：

```text
python audit.py --run runs/revision_reference --require-frozen
```

| 状态 | 含义与处理 |
| --- | --- |
| 退出码 0 / passed_frozen | 已冻结且当前完整性检查通过，可继续 |
| 退出码 3 | 尚未冻结；不能生成评测数据 |
| 退出码 1 / failed | 检查 findings，不能跳过继续 |
| 不要求冻结时的 passed_in_progress | 已保存部分一致，不表示研究完成 |

warning 需阅读并适当披露；例如额外 CLI 工具不能被说成完全隔离。审计不是第三方签名，也不证明科学结论。

通过后核对冻结规格、源码与计划，此命令不生成数据：

```text
python benchmark_revision.py --run runs/revision_reference --validate-only
```

再次通过后才执行指定评估器，使用新目录：

```text
python benchmark_revision.py --run runs/revision_reference --output results/reproduction_revision
```

评估器先做冻结来源检查，再保存协议并生成数据；内部检查不替代前面的完整历史链审计。CLI 没有 seed 或样本量参数供事后挑选有利评估，设计以锁定计划为准。输出目录存在会报错，不覆盖或自动续算；失败方法不会被静默跳过。

| 输出 | 作用 |
| --- | --- |
| protocol.json | 计划、方法、别名、来源与环境 |
| status.json | running / complete / failed，完成时含 summary 哈希 |
| `{main,stress,edge,calibration,coverage}_inputs.json` | 各用途合成输入 |
| `{main,stress,edge}_{method}.json` | 逐例预测、误差、模型、CV、成本 |
| calibration_predictions.json / coverage_predictions.json | 独立校准与覆盖预测 |
| conformal.json | Global / Mondrian、分组、半径与拒绝曲线 |
| predictions.csv | 主测试、压力、边界逐例表 |
| summary.json | 完成后的汇总、配对区间、分种子与非劣诊断 |

失败时 status.json 保留错误与已有文件，部分预测不表示完成。定位并披露修复影响，不依据已见成绩调整被冻结方法。

## 设计规模与统计口径

以下均为预登记计划值，实际执行状态另行核对：

| 用途 | 种子 | 设计 |
| --- | --- | --- |
| 发现 | 1646191 | 五场景各 20，共 100 |
| 开发验证 | 1657383 | 五场景各 30，共 150；最多四次调用 |
| 主测试 | 1814711 / 1826013 / 1837901 | 每种子四场景各 60，共 720 |
| 压力 | 1849391 | 五场景各 80，共 400 |
| 边界 | 1980221 | n=6…12、静态 / 运动正常，每格 30，共 420 |
| 校准 | 1862193 | 核心场景逐例均匀随机混合，共 800 |
| 覆盖检验 | 1875149 | 独立同分布逐例混合，共 1200 |

方法按 case_id 配对。4000 次分层 bootstrap 使用主测试的种子×场景、压力的场景、边界的观测数×场景，报告 95% 双侧百分位区间。mean_reduction_m>0 表示参考误差高于第二轮所选误差。未作多重比较校正，不能解释成智能体随机性区间；三个数据种子不是三次独立模型研究。

静态非劣诊断预声明相对 WLS 均值比界限 1.1，并列点估计与 bootstrap 上界；这是探索性诊断，不是确证性非劣试验。全部场景、n=8 比较和不利结果都应保留。

有效配置重复时 aliases 标记共享预测，计时复用 canonical 方法。prediction_count 是实际计算数，logical_prediction_count 含别名逻辑行。K3 / K4 对非 CV 方法启用 CV 时，不能称为仅改折数的单组件消融。新方法残差调用和旧基线 nfev 口径不同，应同时报告实测耗时，不把其比值称为加速倍数。

Global 使用总体分位数；Mondrian 以冻结估计器从观测选出的模型分组，不用真实场景。空组或样本不足保存 q="infinity" 和无界标记。IID 覆盖及其子组的 Wilson 区间描述本次固定校准规则下的测试抽样不确定性，不是重新校准过程的置信区间；压力 / 边界不报告对应 pooled IID 区间。真实场景条件、拒绝子集和分布偏移不自动继承共形保证。

## 真实研究、停止与回放

使用用户自己登录的 Codex CLI，沿用配置模型，不猜测未报告名称：

```text
python run_revision.py --output runs/my_revision --provider codex --timeout 300 --reasoning-effort medium
```

默认预算为动作 20、实验 / 消融 8、模型请求 28；请求含审阅与至多两次重试。每三个成功 run_experiment 后触发批评，ablate 计实验预算但不等于该触发计数。五个参考方法不计新候选；冻结要求新组合、诊断后修订、有效单组件消融、数学检查和本轮验证对照。新运行只产生新的记录，不应把同一固定协议重跑说成新预登记的首次盲测。

持续模型失败明确终止，无静默规则替代。冻结后把审计、校验和评测的 --run 同步改为 runs/my_revision，并使用新的结果目录。

Windows PowerShell 建立停止标记：

```powershell
New-Item -ItemType File -Path runs/my_revision/STOP
```

macOS / Linux 可用 touch runs/my_revision/STOP。它在下一模型 / 工具边界检查，不立即取消正在等待的请求。恢复前仅删除对应 STOP，保留原配置：

```powershell
Remove-Item -LiteralPath runs/my_revision/STOP
python run_revision.py --output runs/my_revision --resume --provider codex --timeout 300 --reasoning-effort medium
```

原来若改过预算，恢复也须重复原值；目标、provider、计划、种子与源码必须一致。有未确定副作用的中断工具调用时拒绝盲目重放，冻结后不能重新开发。

来源完整后可以显式回放：

```text
python run_revision.py --output runs/reproduction_replay --provider replay --replay-source runs/revision_reference
```

回放读取 model_response 并运行 CPU 工具，不产生新推理，记录 replay=true / new_model_inference=false。响应耗尽会报错；默认 CLI 不启用严格提示哈希模式，不能宣称全部提示逐字一致或独立科研发现。恢复回放仍需相同 provider、来源与配置。

## PDF 重建

安装 requirements-report.txt 和包含 ctex、TikZ/PGFPlots、algorithm2e 等包的 XeLaTeX / BibTeX。两轮及敏感性证据完整后运行：

```text
python reporting/compile_report.py
```

脚本运行保存证据生成器，然后 XeLaTeX、BibTeX、两次 XeLaTeX，复制到 report/CrowdLocateLab_Report.pdf。--xelatex / --bibtex 可指定工具，--output 可换路径；日志在 latex/build_logs。缺工具、证据不完整、编译失败或检查问题会明确退出，不称旧 PDF 已更新。

只改封面且实证章节已正确生成时可用 --skip-refresh。该选项不刷新数字，不能绕过未完成数据并声称新报告完整。编译后仍检查图表、引用和分页；不要为普通排版修改被冻结的 revision 报告代码。

## 常见问题

| 情况 | 处理 |
| --- | --- |
| 找不到 CLI | 离线数值仍可使用；真实研究需安装或设置 CODEX_EXECUTABLE |
| 认证 / 网络 / 额度失败 | 检查自己的正常 CLI 会话与记录，不导出凭据 |
| 审计返回 3 | 研究未冻结，检查预算、停止原因或继续研究 |
| 哈希不一致 | 检查原包、字节、变更和路径，不重写哈希 |
| 输出已存在 | 换新目录 / 文件，保留原始尝试 |
| 缺校准 / 无界半径 | 完成对应冻结评测或保留无界 / 拒绝，不当成零半径 |
| 界面无新数字 | 查 state.json、status.json、summary.json，区分运行 / 失败 / 完成 |
| V2 与新评估器混淆 | V2 用 benchmark_v2_1.py，新轮用 benchmark_revision.py，不择优混用 |
