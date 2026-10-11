# 第二轮应用：新观测定位、区域与拒绝

`locate_revision.py` 将冻结的第二轮方法用于新观测，执行本地求解，不调用模型、不调参。前提是第二轮已冻结且独立校准完成。与 V2 `locate.py` 不同，此入口要求有效校准文件，缺失时明确失败。

## 输入

JSON 只能含下列四个字段，不接受 truth、velocity、scenario、meta 等评测标签：

| 字段 | 形状 | 含义 / 单位 |
| --- | --- | --- |
| `anchors` | n×2 | 上报平面位置，m |
| `times` | n | 相对请求时刻 t=0 的历史时间，s，须 ≤0 |
| `ranges` | n | 正距离，m |
| `sigma` | n | 正报告尺度，m |

必须是有限 JSON 数字，不能用布尔值、字符串、NaN 或 Infinity；数组长度须一致，位置二维，重复键会被拒绝。接口至少接受 3 份观测，这只是格式条件，不意味着足以辨识运动；运动拟合和 CV 还有训练数量、时间跨度、秩和数值条件。

坐标须已换算到同一局部米制平面，时间须共用原点。入口不直接转换经纬度或原始 RSSI，也不估计现场路径损耗标定。[observations.json](../examples/observations.json) 只演示格式，不是工业数据或精度基准。

## 命令

在项目根目录，先通过完整链审计：

```text
python audit.py --run runs/revision_reference --require-frozen
```

成功后使用一个不存在的输出文件：

```text
python locate_revision.py --input examples/observations.json --run runs/revision_reference --calibration results/revision_benchmark/conformal.json --region mondrian --alpha 0.1 --max-radius 20 --output results/my_revision_location.json
```

20 m 是演示性阈值，不是已证明最优阈值。

| 参数 | 默认 / 行为 |
| --- | --- |
| `--input` | 必填，观测 JSON |
| `--run` | `runs/revision_reference` |
| `--calibration` | `results/revision_benchmark/conformal.json` |
| `--region` | mondrian，也可为 global |
| `--alpha` | 0.1，必须与保存水平精确匹配，不临时插值 |
| `--max-radius` | 省略时不做接受 / 拒绝；指定须有限且为正 |
| `--output` | 省略时 JSON 输出到终端；指定文件须不存在，且不能写进运行目录 |

同一输入可分别用 `--region global` 和 `--region mondrian`，写入不同新文件。Global 使用总体分位数；Mondrian 根据冻结估计器从观测选择的静态 / 运动模型分组，不能用真实运动状态或真实场景分组。两种规则使用同一个点估计器。

## 输出解释

- `position` / `velocity`：t=0 位置（m）和速度（m/s）。输入没有真值，不能计算该输入的真实误差。
- `selected_model` / `diagnostics`：实际模型、CV、Jacobian 和残差信息。优化成功或局部满秩不是位置正确的证明。
- `region`：规则、alpha、分组、校准样本量、阶数 k、分位数 q、半径和来源哈希；中心为本次点估计。
- `acceptance`：无阈值时 status=not_applied、accepted=null；有阈值时按 radius≤max_radius 接受或拒绝。拒绝后点估计仅保留作诊断，不能当成通过阈值的输出。
- `run_integrity`：当前冻结来源检查。入口要求评测已完成，并核对 status.json 中的摘要哈希、summary.json 与 protocol.json / conformal.json 的一致性，以及冻结哈希和方法规格。这些检查不替代独立的完整历史链审计，也不构成第三方数字签名。

某组校准量不足、k 超过组样本数时，JSON 保存 `radius_m="infinity"` 与 `unbounded=true`。这不是零半径；有限 max-radius 会拒绝无界区域。缺校准、校准不匹配、请求不存在的 alpha、格式非法或输出已存在时，程序报错退出，不伪造结果。

## 保证范围

合成校准的交换性不能自动迁移到新设备或现场。Global 边际覆盖、Mondrian 组内覆盖和固定校准规则后的经验检验各有条件，都不保证单次位置必在圆内，也不保证拒绝后子集的覆盖率。输出始终保留 `coverage_guarantee_asserted_for_this_input=false` 与 `conditional_coverage_guaranteed=false`。

应将其称为“冻结方法对新观测的应用与诊断”，而非新的模型发现。后续修改方法或标定应建立新研究版本，不能改写原冻结文件和测试成绩。
