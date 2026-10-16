# v1 实际执行命令与状态

所有命令的工作目录为当前研究 workspace 根目录。没有联网、安装依赖或启动后台研究。
Python 路径由 `materials/environment.txt` 提供。以下为本单元已执行的命令，不是未执行计划。

## 1. 构造与初步实验

```powershell
& 'C:\Users\沅\Documents\Codex\2026-09-25\pptx\work\deep_audit\.venv\Scripts\python.exe' runs\mirror-ambiguity-20261015\experiment_v1.py
```

退出码 0。标准输出包含 11 个案例的实际状态、3 个精确非零子式、10 类非法输入拒绝、200 次恒等式探测。
原始数值保存为 `results_v1.json`，输入为 `fixtures_v1.json`。关键数值：432，269997/625，-6；
最大反射恒等式误差与中点恒等式误差均为 1.7763568394002505e-15，几何界正超出量 0.0。
随机种子 20261015。无优化器；无吞掉的失败断言。

## 2. 实际数据 CLI 证书

```powershell
& 'C:\Users\沅\Documents\Codex\2026-09-25\pptx\work\deep_audit\.venv\Scripts\python.exe' runs\mirror-ambiguity-20261015\diagnostic_v1.py runs\mirror-ambiguity-20261015\input_collinear_full_rank_v1.json --output runs\mirror-ambiguity-20261015\certificate_collinear_v1.json
```

退出码 0，无标准输出（使用 --output）。输出实际状态 `witness`，两候选可行，D²=36，下界平方=9。

## 3. 共同观测 CLI 边界证书

```powershell
& 'C:\Users\沅\Documents\Codex\2026-09-25\pptx\work\deep_audit\.venv\Scripts\python.exe' runs\mirror-ambiguity-20261015\diagnostic_v1.py runs\mirror-ambiguity-20261015\input_common_y_boundary_v1.json --output runs\mirror-ambiguity-20261015\certificate_common_boundary_v1.json
```

退出码 0，无标准输出。输出 `witness`，D²=4，下界平方=1，共同 y 的精确构造式见证书。

## 失败和修正

上述三次执行均成功，没有失败调用；预期的 `no_witness_found` 结果不是实验失败。
目前尚未执行后续独立实现的验证阶段。本文件后续新增的检查必须记录实际命令与退出状态。
