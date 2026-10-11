# 大模型科研运行器

`ResearchController` 的每个研究动作来自真实大模型的结构化响应；没有模型失败后转成规则脚本的后备模式。数值求解器负责执行已经选择的实验，它不是规划器。`tests/test_runtime.py` 中的 fixture 仅供软件测试，绝不是实验结果。

```python
from agent_runtime import CodexCLIProvider, ResearchController
provider = CodexCLIProvider(timeout=180, reasoning_effort="medium")  # timeout 可由外层 CLI 调节
controller = ResearchController(
    provider=provider, toolkit=toolkit, output=run_directory,
    goal=research_goal, tool_spec=TOOL_SPEC,
    config=experiment_configuration, seed_hash=seed_hash,
    max_steps=24, max_experiments=9, max_model_calls=40,
    max_retries=2, critic_every=3, resume=False,
)
state = controller.run()
```

真实调用采用本机已登录的 `codex exec --ephemeral --skip-git-repo-check --sandbox read-only --json --output-schema ... -o ... -`，从标准输入发送完整提示，不传 `--model`，沿用用户配置。可选 `reasoning_effort="medium"` 通过 `-c model_reasoning_effort="medium"` 仅覆盖本次调用，不修改用户配置文件；省略则不覆盖。可通过 `CODEX_EXECUTABLE` 指定程序路径。程序不读取 `auth.json`，不保存或输出认证信息。Windows 子进程使用 `CREATE_NO_WINDOW`。

每次调用保存提示、严格 JSON Schema、最终响应、原始 CLI JSONL 事件、标准错误日志和元数据。提示禁止 CLI 自行调用 shell、网页或文件工具；这不等同于彻底移除了 CLI 自身工具。运行器检测并记录已知工具调用事件，报告不得声称完全工具隔离。事件检测也不是一个完备的安全边界。

`metadata.json` 保存 CLI 返回的 token usage；模型标识仅从明确的 JSON model 字段或标准错误的 model 标题提取。若 CLI 未返回，记录 `model_identifier=null` 和 `model_identifier_source=not_reported_by_cli`，不从模型自述、默认名称或文件路径推断实际模型。

模型每轮给出简短公开决策依据 `analysis_summary`、可检验假设、工具名、JSON 参数、预期结果及记忆。这里不要求输出私有思维链。原始工具观察完整写入日志；最近六条进入下一次模型上下文，普通观察限制约 15000 字符，`read_sources` 提高至约 36000 字符以容纳两份研究读物。上下文记录原长度、限额与是否截断；截断时保留明确标记与完整证据位置。注册方法目录和长期记忆也进入上下文。

每三个成功的 `run_experiment` 动作后，启动一个使用独立批评提示的模型调用，检查范围、数据泄漏、比较公平性与论证漏洞。这是同一配置模型承担不同角色，不应称为多个独立模型或独立实证复现。`ablate` 同样消耗实验调用预算，但不单独计入此“三次 run_experiment”触发器。

`state.json` 原子写入。`events.jsonl` 包含 `model_request/model_response/tool_call/tool_result/critique/decision`，每条包含前一事件哈希与当前哈希。恢复时要求相同目标、工具说明、配置、seed hash、provider 类型和预算。原始子进程日志不是这一哈希链本身，但请求和响应内容及其路径已记入事件。若发现调用中断且工具副作用不明，拒绝自动重放该工具，须先审查证据。

在运行目录创建 `STOP`，或调用 `controller.request_stop()`，将于下一个模型/工具边界停止。正在执行的单次模型调用由 timeout 限制。删除 `STOP` 后显式 `resume=True` 可继续尚未冻结的暂停运行；不能更改数据种子或配置。成功 `freeze` 后立即停止开发，恢复也不会再调用模型或开发工具；测试集评估由外层程序单独执行。冻结工具的前置条件由 toolkit 验证。模型失败至多重试两次，随后保存失败状态并抛错，不生成替代决策。工具失败保留为观察，由后续模型决定如何处理，不盲目重复可能已产生副作用的工具。

`ReplayProvider(saved_run, strict_prompt=False)` 只回放历史 `model_response`，每次标明 `replay=true` 与 `new_model_inference=false`；耗尽即报错。启用 `strict_prompt=True` 时还要求提示哈希相同。回放可以检查调度与证据一致性，不能被表述为新的大模型实验。

官方调用说明：https://learn.chatgpt.com/docs/non-interactive-mode

单次配置覆盖：https://learn.chatgpt.com/docs/developer-settings
