# 评估入口 v2.1 的预先代码审查修订

## 时间和研究状态

这次修订发生在**方法尚未冻结、任何正式 holdout 尚未生成或读取时**，不是根据
测试成绩作出的事后修改，也不是“冻结后改进算法”。

- 首次只读状态核查：2026-09-24 17:37:03 UTC，正式研究为 `running`，
  `frozen=false`，已完成 7 个动作、2 次实验调用，`results/` 目录不存在。
- 修订快照记录：2026-09-24 17:40:54.714109 UTC，仍为 `running`、
  `frozen=false`，已完成 9 个动作、3 次实验调用，`results/` 目录仍不存在。
- 这些是本地日志和文件状态记录，不是第三方时间戳或数字签名。

## 问题与唯一算法分支修改

研究工具允许科学地选择一个旧基线，例如冻结规格
`{"baseline":"static_robust"}`。原评估入口把
`chosen.get('model') != 'static'` 作为强制静态消融条件。
由于该简称没有 `model` 字段，条件为真，产生
`{"baseline":"static_robust","model":"static"}`。
数值 API 正确地拒绝这种将基线简称与新组合参数混合的非法规格。

`benchmark_v2_1.py` 在构造组件消融时只新增以下保护条件：

```python
if 'baseline' not in chosen and condition:
    ...
```

因此，当选定规格为基线简称时，不生成新组合方法的组件消融；仍保持原冻结
规格、四个预先声明基线、`llm_selected` 和既定的前两个研究前驱比较。
当选定规格是正常组合方法时，消融集合和参数与原入口完全相同。
没有把基线展开成另一套求解器，也没有更改模型作出的科学选择。

## 版本保留和代码复用

- `benchmark.py` 原字节保持不变，继续作为研究初始化版本和共享库。
- 新正式入口是 `benchmark_v2_1.py`；它直接导入原版本的完整性校验、案例生成、
  指标评价、成对 bootstrap、iid 混合抽样和 conformal 函数，不复制这些助手。
- 新文件只保留必要的主程序组织和方法表构造，并增加评估器版本追溯字段。
- 所有数据种子、CLI 选项及默认样本量、前驱选择顺序、误差指标、bootstrap
  和校准定义保持不变。工具、求解器、真实模型轨迹及初始化哈希完全不变。
- `research/benchmark_v2_initialization_snapshot.py` 是原入口的逐字节快照，
  不应作为另一个评估命令使用。
- `research/initial_research_protocol_snapshot.json` 保存原始研究初始化 manifest。
- `research/evaluator_v2_1.patch` 是原入口到并列新入口的精确统一 diff；只用于
  审计代码变化，**不要将其覆盖应用到原 `benchmark.py`**。

## SHA-256

| 对象 | SHA-256 |
|---|---|
| 原 `benchmark.py` 与其字节快照 | `30d9d356bbbc96bc9f35477e7a231a3b7ebcb9b2f08ad8ff970f6b86559249c5` |
| 原研究初始化 manifest | `6a146e5f816237f72a1f83d500c1f64cfc48833c51233d9b504e3e52fa36625d` |
| 新 `benchmark_v2_1.py` | `f4ed7a18c722536b0d1daa6197a8dd23d38a929ff6f97827741b5ada259810c5` |
| 精确 diff | `59efd1d33081d6eb516ce72064695f5a635e8d80bb0c6cea6a79cc16f699b353` |

机器可读记录位于 `research/evaluator_amendment_manifest.json`。
新评估器会在生成测试数据前校验原研究冻结记录及原源码，同时核查本修订的
评估器和 diff 哈希。新结果的 `protocol.json` 另外保存完整原研究 manifest、
它的文件哈希、原 `benchmark.py` 哈希、新评估器哈希和修订记录，避免把新版
评估器冒充成初始化时的原文件。

## 预先验证与正式评估承诺

执行的验证命令是：

```powershell
python -m unittest discover -s tests -p test_evaluator_amendment.py -v
```

四项测试通过。测试只构造方法字典和读取原始代码的 registry 构造段，验证：

1. 基线简称保持不变，不出现非法混合规格或组件消融；
2. 正常组合方法分支与原代码产生完全相同的方法及消融集合；
3. 统计、校准、生成和校验助手确实直接复用同一函数；
4. CLI 选项与数据种子不变。

测试未生成或评分任何正式 holdout。正式研究冻结后，**第一次正式 holdout
统一使用 v2.1**：

```powershell
python benchmark_v2_1.py --run runs/live_reference --output results/benchmark
```

不会同时运行两个版本再挑选有利结果。若模型最终选择基线，该结果应按真实
研究结论报告；不能为了制造“新方法更好”的叙事而改写冻结规格。
