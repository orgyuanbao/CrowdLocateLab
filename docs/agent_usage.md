# 运行、展示与审查科研智能体

本项目的研究动作来自大模型的真实结构化响应。模型选择下一步工具、提出可检验假设、根据实验与诊断修订方法；本地数值程序负责执行计算。研究日志、软件单元测试和历史回放是三种不同证据，不能混为同一次科研实验。

## 1. 准备与认证

在项目根目录安装基础依赖，并保证 `codex` 在命令搜索路径中：

```powershell
python -m pip install -r requirements.txt
codex --version
```

使用自己有权限的 Codex 账号完成 CLI 登录；未登录时按 CLI 的正常登录流程操作。项目不会读取 `auth.json`、导出认证文件或要求把登录信息写入源码。程序路径不在 PATH 时，可在当前终端设置：

```powershell
$env:CODEX_EXECUTABLE = 'C:\path\to\codex.exe'
```

运行器实际调用 `codex exec`，通过标准输入传入完整研究上下文，使用 `--output-schema` 要求结构化输出，并保存 `--json` 事件及 `-o` 最终响应。它不指定新的模型名称，沿用用户配置；`--reasoning-effort medium` 是本次调用的临时设置，不修改用户配置文件。[官方非交互说明](https://learn.chatgpt.com/docs/non-interactive-mode)、[临时配置说明](https://learn.chatgpt.com/docs/developer-settings)解释了相应接口。

实际模型名称只采用 CLI 明确返回的 model 字段或模型标题。如果没有返回，`model_identifier` 为 `null`，界面显示“CLI 未报告”；不能把项目名称、推理设置或模型自述当作实际模型标识。

当前环境的原始连通测试出现网络连接超时重试。程序保持已经验证成功的 CLI 调用路径，保留真实耗时和诊断，不修改内置 provider 的传输配置，也不把重连耗时描述为模型推理耗时。

## 2. 发起真实研究

在项目根目录执行，输出目录必须是新目录：

```powershell
python run_agent.py --output runs/my_study --provider codex --timeout 300 --reasoning-effort medium
```

默认研究动作预算为 26 步、实验或消融调用预算为 10 次、模型调用预算为 38 次；模型调用预算包含独立批评与重试。每三个成功的 `run_experiment` 动作后，以独立提示调用同一配置模型进行批评。这里的“独立”指提示与角色分离，不表示用了不同模型，也不表示独立实证复现。

模型响应包含六个字段：`analysis_summary` 是简短公开决策依据，`hypothesis` 是可检验假设，`action` 和 `arguments_json` 指定研究工具，`expected_result` 记录预期观察，`memory_note` 写入研究记忆。项目不要求输出私有思维链。

工具名限于 `read_sources`、`register_method`、`run_experiment`、`diagnose`、`verify_math`、`ablate`、`freeze`、`finish`。注册方法采用声明式算子组合；预实现的鲁棒损失、数值求解器和校准方法不能被表述为智能体新发明的理论。科研价值应依据实际提出的假设、控制实验、失败分析和数学论证来评估。

研究工具可访问 discovery 和 validation 数据，不能读取测试或校准数据。验证集限制四次工具调用。冻结要求已有实验、至少两个新方法、失败诊断、数学检查和所选方法的验证证据；研究目标另外要求至少三个有区别的方法及一次控制消融。成功冻结后运行器停止开发动作，之后独立运行 benchmark，不根据测试结果继续修改被冻结方法。

## 3. 停止与恢复

在相应运行目录创建 `STOP` 文件：

```powershell
New-Item -ItemType File -Path runs/my_study/STOP
```

运行器在下一次模型或工具边界停止；正在进行的一次模型请求受 `--timeout` 限制，STOP 不是立即杀死计算进程。恢复前仅删除这个明确的停止标记，再使用相同配置：

```powershell
Remove-Item -LiteralPath runs/my_study/STOP
python run_agent.py --output runs/my_study --resume --timeout 300 --reasoning-effort medium
```

恢复时校验目标、工具说明、种子 hash、代码配置、provider 类型、推理设置和预算。若初次运行修改过 `--max-steps`、`--max-experiments` 或 `--max-model-calls`，恢复时也要保留对应值。不能换种子后继续写入同一研究记录。存在未确定副作用的中断工具调用时，运行器拒绝盲目重放，须先检查其证据。已经冻结的状态不能再恢复为开发状态。

每次模型请求最多重试两次；仍然失败会保存 `provider_failed` 并抛错。没有静默改用规则脚本的后备模式。工具失败保留为观察，由下一次模型决策处理，避免无条件重复已可能产生副作用的工具调用。

## 4. 打开只读课堂仪表盘

```powershell
python dashboard.py --run runs/live_reference --results results/benchmark --port 8765
```

浏览器打开终端打印的 [本地仪表盘](http://127.0.0.1:8765)。页面每五秒读取状态与最近 100 条事件，显示：

- 真实大模型运行或 REPLAY 回放的醒目标记；
- 当前动作、实验和模型调用预算；
- 注册的方法、科研假设、数学定义、配置与开发指标；
- 模型动作、工具参数、真实返回、独立批评和失败记录；
- 冻结后的方法测试表、覆盖率与未解决结论。

服务只监听 `127.0.0.1`，仅提供 `/`、`/api/state`、`/api/events` 的读取接口。没有任意文件下载、目录浏览、shell 命令、模型调用或修改运行状态的 HTTP 接口。关闭页面或按 Ctrl+C 关闭仪表盘不会停止研究进程；停止研究需要使用该运行目录的 STOP 机制。

仪表盘直接读取已保存数据，不启动科研任务。文件尚未生成时显示等待，不填充演示指标；`results/benchmark/summary.json` 完成后自动展示独立测试结果。页面会保留所选方法劣于基线、改善区间包含零及分布偏移覆盖下降等结果，不把开发集进步包装成泛化保证。

## 5. 冻结后独立测试

```powershell
python benchmark_v2_1.py --run runs/my_study --output results/my_benchmark
python dashboard.py --run runs/my_study --results results/my_benchmark --port 8765
```

`benchmark_v2_1.py` 是唯一正式入口；原 `benchmark.py` 保留为研究初始化版本和校验/统计库，不直接运行。修订发生在冻结前、首次 holdout 前，详见 [修订记录](../research/evaluator_amendment.md)。

评估器需要已有 `tools/frozen_method.json`，输出目录必须是新目录。它执行独立种子测试、压力场景、组件消融与独立校准。校准覆盖率结论依赖交换性和冻结估计器；同分布边际保证不等于按场景、拒识子集或分布偏移后的覆盖保证。所有数据仍是合成子问题，不能据此声称通过原工业题目的验收。

## 6. 真实模型调用、成本和回放

每个请求目录 `model_calls/<request_id>/` 包含：

| 文件 | 证据用途 |
| --- | --- |
| `prompt.txt` | 本次实际发送的研究上下文 |
| `schema.json` | 要求返回的结构化契约 |
| `response.json` | 模型实际返回的最终决策 |
| `cli_events.jsonl` | CLI 原始过程事件 |
| `stderr.txt` | CLI 运行诊断，不作为研究结论 |
| `metadata.json` | 耗时、报告的 token usage、模型标识来源、额外工具检测 |

Token usage 采用 CLI 报告值；缺失不等于零。界面累计的是成功响应所报告的用量，可能不包含失败请求消耗，也不是账单金额。账号额度、计费方式与实际费用应以用户账号记录为准。模型执行、网络重连和上下文长度都会影响耗时；项目中连通性探针不计作数学研究实验。

提示要求 CLI 不自行调用 shell、网页或文件工具，研究工具应由外层调度器执行；但提示并不构成完全隔离。运行器保存并检测 `unexpected_tools`，若出现相关事件，应在报告中披露，不能称为“完全禁用内置工具”。

回放必须显式选择 `replay`，输入为保存的运行目录：

```powershell
python run_agent.py --output runs/replay_audit --provider replay --replay-source runs/live_reference
```

回放依次读取已保存的 `model_response`，不产生新模型推理；每个响应包含 `replay=true`、`new_model_inference=false` 和来源索引。源码使用同样工具时可以重新执行相应数值计算，因此回放仍可能耗费 CPU；它不是新的科研发现或新的大模型决策。响应耗尽即报错，不转成在线模型或脚本。严格提示哈希审查可直接使用 `ReplayProvider(source_run, strict_prompt=True)`。

## 7. 日志与状态字段

`events.jsonl` 的通用字段为 `sequence`、`time_utc`、`type`、`payload`、`previous_sha256`、`event_sha256`。事件按序构成哈希链，恢复时检查链的完整性。

| 事件类型 | 含义 |
| --- | --- |
| `model_request` | 请求目的、重试次数、完整提示与提示/schema hash |
| `model_response` | 结构化响应、provider、usage、耗时、证据路径、回放标记 |
| `tool_call` | 来自模型决策的工具名与参数 |
| `tool_result` | 是否实际执行、真实返回、失败信息 |
| `critique` | 范围检查、问题列表、下一步建议、证据局限 |
| `decision` | 初始化、恢复、模型动作、失败、额外工具检测、冻结、停止或预算终止 |

`state.json` 保存身份指纹、累计预算、记忆、工具观察、批评和冻结状态；`tools/tool_state.json` 保存方法注册表与开发评估目录。界面会对过长结果提供摘要，完整证据仍在日志中。哈希链帮助发现记录被改动，但本身不证明实验科学结论正确，也不等于第三方时间戳认证。
