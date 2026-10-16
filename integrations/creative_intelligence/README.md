# 实际接入 Creative-Intelligence 的数学研究 Agent

本目录使用 [DeepMathLLM/Creative-Intelligence 的 Stage 2 控制器](https://github.com/DeepMathLLM/Creative-Intelligence/tree/b7d17249f94949351faefd7ae28a020f0623db93/mathematical-concept-agent)，版本固定为 `b7d17249f94949351faefd7ae28a020f0623db93`。`vendor/agent.py` 保留上游原始字节，实际请求仍经过它的 `command_run`、`build_codex_command`、`execute_turn` 和恢复机制。`runner.py` 为原控制器提供独立工作目录、适用的数学研究 Skills 和过程证据记录。

这与项目既有的 `run_research.py` 是两条独立入口。旧的两轮研究记录不能算作这个上游 Agent 的运行结果。

已用本入口完成两个真实研究单元，结果见 [定位歧义研究](../../studies/creative_intelligence/mirror_ambiguity/README.md)：包括原始调用、同会话恢复、数学反例、程序修订和无模型复算命令。

## 文件与来源

| 文件 | 来源与作用 |
|---|---|
| `vendor/agent.py`、`vendor/tests/`、`vendor/schemas/` | 上游原文件；不修改控制器实现 |
| `vendor/AGENTS.md`、`vendor/config.toml`、`vendor/README.md` | 上游原说明及默认配置，作为来源保留 |
| `vendor/LICENSE`、`UPSTREAM.json` | 上游 MIT 许可、固定版本和文件 SHA-256 |
| `runner.py` | 本项目编写的目录绑定、一次执行入口和日志包装 |
| `runtime_config.toml` | 本项目的调用级配置；运行时不采用上游固定模型和联网搜索默认值 |
| `runtime_boundaries.md` | 本项目添加的研究范围和文件边界 |
| `skills/*/SKILL.md` | 本项目补写的构造与验证 Skill，明确标注来源 |

固定上游版本没有提供两个 Skill 的实现。这里补写了它期望的 `construct-mathematical-concept` 和 `verify-mathematical-concept`，引导 Agent 区分定义、证明、数值证据、反例和未解决义务。它们不是从上游恢复的文件，也不能使模型的回答自动成为数学证明。

## 初始化与逐轮运行

需要已经安装并登录的 Codex CLI，以及 Python 3.11 或更高版本。数值研究还需要研究题面列明的 NumPy/SciPy 等环境。控制器本身仅使用 Python 标准库。

在项目根目录执行，研究工作目录必须位于项目目录之外：

```powershell
python -B integrations/creative_intelligence/runner.py init `
  --workspace ../creative-research-runtime `
  --run-id localization-study `
  --problem /path/to/problem.md `
  --materials /path/to/materials

python -B integrations/creative_intelligence/runner.py validate `
  --workspace ../creative-research-runtime --run-id localization-study

python -B integrations/creative_intelligence/runner.py advance `
  --workspace ../creative-research-runtime --run-id localization-study
```

`init` 复制原始题面和参考材料，不调用模型。`validate` 运行上游检查和 CLI 版本检查，不发起研究。每次 `advance` 只调用原控制器的一个 `--once` 研究单元；继续执行同一条命令时，原控制器使用保存的 session ID 恢复会话。继续指令可通过 `--prompt-file /path/to/next-step.txt` 提供，原题面保持不变。

```powershell
python -B integrations/creative_intelligence/runner.py status `
  --workspace ../creative-research-runtime --run-id localization-study
```

此接入入口不提供无限循环或后台 `start`。一个研究单元内部仍可能调用多次工具；`--once` 不是令牌、费用或运行时长的上限。需要中止当前请求时使用 Ctrl+C，由上游控制器处理停止。

## 真实调用与失败证据

研究文件保存在工作目录 `runs/<run-id>/`；控制器记录放在其同级的 `<workspace-name>.evidence/`，避免放入模型的工作写区。

每次尝试产生 `turn-001` 等独立目录，保存：

- `command.json` 和 `prompt.txt`：传给原控制器的命令参数和实际研究指令；恢复会话 ID 可核查。
- `events.raw.jsonl`：原 `execute_turn` 交给 `parse_codex_event` 的逐行 CLI 输出，原解析器仍参与处理。
- `state_before.json`、`state_after.json`、`input_digest.json`：运行前后状态与原始输入摘要。
- `final_message.txt`（有返回时）、控制器与 CLI 错误输出：包含成功和失败信息。
- `result.json`：进程结果、是否接受了结构化研究状态、不可变输入检查结果。
- `SHA256SUMS`：本次证据文件的完整性摘要；不是第三方签名或数学正确性认证。

CLI 进程退出成功、产生普通回复，不会被包装器计为成功研究轮次。只有原控制器接受规定的研究状态、轮数推进且输入摘要未改变，`accepted_structured_research_turn` 才为 `true`。`blocked` 仍可能是有效结构化状态，须结合状态与研究产物解释，不能宣传为问题已经解决。

原始输出可能含本机路径与研究上下文。公开材料应明确区分本地原始记录、公开副本以及任何脱敏；不得把重新构造的记录标成原始运行日志。

## 边界与验证

运行配置使用 `workspace-write`、`approval_policy=never`、禁用网页搜索、禁用工作沙箱网络和子 Agent；不指定模型，沿用用户配置的默认模型，不读取或复制认证凭据，不改用户配置文件。上游默认配置保留在 `vendor/`，但不用于这条运行入口。

包装器检查固定版本文件、初始化输入、原始题面和 Skills 的摘要，拒绝重复初始化、项目内部工作目录及并发推进。不变输入发生变化会被报告；研究输出和状态文件的科学可信度仍需独立复算。摘要检查用于发现更改，不能抵御拥有同等系统权限的恶意进程。

可以独立运行原控制器测试与接入测试，均不需要调用真实模型：

```powershell
python -B -c "import os, sys, unittest; os.environ['CODEX_EXECUTABLE'] = sys.executable; suite = unittest.defaultTestLoader.discover('integrations/creative_intelligence/vendor/tests'); result = unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
python -B -m unittest discover -s integrations/creative_intelligence/tests -v
```

第一个命令仅为上游的命令构造测试提供一个存在的可执行路径，不运行该路径来调用模型，因此未安装 Codex CLI 也能检查。测试中的模拟进程仅用于验证运行、恢复与失败记录逻辑，不是数学研究证据。研究成功与否必须查看真实执行记录、保存的推导和独立数值检查。
