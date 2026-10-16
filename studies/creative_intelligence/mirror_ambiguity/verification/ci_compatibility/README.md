# 2026-10-16：上游显示测试的平台假设

首次交付提交 `0823350ad34d732996998eda6372fdb15478bffc` 的 [CI 运行](https://github.com/orgyuanbao/CrowdLocateLab/actions/runs/38866371393)中，两组 Windows 成功，两个 Linux 任务各有三项上游显示格式测试失败。Linux 根工程的 233 项已经通过；因后续上游测试失败，剩余步骤被跳过，不能视为已执行。

原 `agent.py` 根据宿主与命令选择 `Shell` 或 `PowerShell`，三个原测试却把裸命令的预期固定为 Windows 的 `PowerShell`。这是测试夹具的平台前提遗漏，未发现数学算法或真实研究执行错误。[失败状态快照](failed_ci.json)和[Linux 错误节选](linux_failure_excerpt.log)保留实际失败，未删除或改写。

修正放在 [run_vendor_tests.py](../../../../../integrations/creative_intelligence/run_vendor_tests.py)：

- 仍运行全部 22 个原测试，不修改 vendor 文件或预期断言。
- 仅在三个 Windows 显示夹具方法内替换被测模块的 `os` 引用，用代理提供 `name="nt"`；没有修改全局 Python `os.name`，执行结束恢复引用。
- 增加两个独立测试，检查真实宿主的标签、POSIX 中显式 `pwsh` / `powershell` 命令及代理恢复。
- 保持模拟进程测试性质，不调用真实模型；研究运行入口不使用此测试适配。

[本地结果](vendor_compatibility.log)为 24 项全部通过。与未变的根工程 233 项、适配层 18 项合计 275 项；此前本地整体检查中的两个符号链接跳过仍如实保留，Linux 应在本次修正提交完整执行后确认。最新四组结果以 GitHub 对应提交为准。

补充报告记录了该问题与计数变化，原 PDF 视觉检查摘要继续作为第一次发布快照保存；修订后的来源和 PDF 校验见同目录的 `report_update.json`。本次只修订测试适配、流程和说明，不更改原 70 份研究材料、7 份上游原文件或冻结定位实验。
