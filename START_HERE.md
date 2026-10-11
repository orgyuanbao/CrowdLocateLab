# 先看这里

这是 **CrowdLocateLab 双轮研究版**。两轮真实智能体研究、独立评测与结果复核均已完成，合计 27 次模型调用、22,052 次冻结后定位计算，129 项软件测试通过。第一轮失败被保留为已知先验，第二轮提出、诊断和验证修订，仍保留静态退化与部分覆盖不足。

## 1. 只查看，不调用模型

打开 [中文报告](report/CrowdLocateLab_Report.pdf)，或在项目根目录执行：

```text
python dashboard_revision.py --port 8766
```

浏览器打开 <http://127.0.0.1:8766>。右上角切换 V2 / Revision，查看概览、真实迭代、失败案例、校准与拒绝、n=6…12 边界。界面仅需 Python 标准库；没有记录时显示等待。

两轮模型证据分别在 `runs/live_reference/`、`runs/revision_reference/`。评测在 `results/benchmark/`、`results/sensitivity/`、`results/revision_benchmark/`。阅读不需要模型账号，演示步骤见 [DEFENSE_DEMO.md](docs/DEFENSE_DEMO.md)。

## 2. 检查环境和证据

数值运行使用 Python 3.11+，推荐独立虚拟环境；安装步骤见 [README](README.md)。

```text
python -m pip install -r requirements.txt
python doctor.py
python -m unittest discover -s tests -v
```

第二轮已冻结后，先完整链审计：

```text
python audit.py --run runs/revision_reference --require-frozen
```

必须返回退出码 0、`passed_frozen` 后才能进入评测。运行中已有证据一致不等于冻结完成。随后按 [复现说明](docs/reproduction_revision.md) 进行 `--validate-only` 和新目录评测，不覆盖正式结果。

## 3. 应用或发起研究

第二轮已冻结且独立校准完成后，使用不含真值的输入：

```text
python locate_revision.py --input examples/observations.json --region mondrian --alpha 0.1 --max-radius 20 --output results/my_location.json
```

这不调用模型。缺失校准会报错；现场观测与合成校准不同分布时不保证覆盖。详见 [应用说明](docs/application_revision.md)。

重新执行模型研究使用新目录：

```text
python run_revision.py --output runs/my_revision --provider codex --reasoning-effort medium
```

需要用户自己的 Codex CLI 登录和额度。replay 是旧决策回放，离线评测是重算数值，都不是新模型推理。完整区别见 README。

## 4. 重建与提交

`latex/main.tex` 是论文入口，`reporting/compile_report.py` 从完成的证据生成章节并编译 PDF。身份字段请自行补齐，不需重跑模型。

源码、两轮正式记录、结果、预登记、审计与 PDF 应一并提交；课件和凭据不上传。保留 `.gitattributes` 以免换行转换破坏冻结哈希。GitHub 发布与课程提交步骤见 [SUBMISSION.md](docs/SUBMISSION.md)。

本项目处理工业开放问题的合成子问题。阅读成果时同时查看负面结果、成本与覆盖限制，不把总体改善当作全部问题已解决。
