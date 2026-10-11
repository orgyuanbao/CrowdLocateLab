"""Generate only a new LaTeX subsection from completed, saved revision results."""
from pathlib import Path
import argparse
import json
import math


def tex(text):
    return str(text).replace('\\', '\\textbackslash{}').replace('_', '\\_').replace('%', '\\%').replace('&', '\\&').replace('#', '\\#')


def number(value, digits=3):
    if value in ('infinity', '-infinity'): return r'$\infty$' if value == 'infinity' else r'$-\infty$'
    if value is None: return '--'
    if not math.isfinite(float(value)): return r'$\infty$'
    return f'{float(value):.{digits}f}'


def render(results):
    if results.get('status') != 'complete': raise ValueError('A completed revision evaluation is required')
    lines = [r'\section{第二轮冻结研究与全新独立评价}',
        r'\label{sec:revision-results}',
        '第一轮失败结果是第二轮的已知先验；以下是新智能体冻结方法后，按预登记种子首次生成的独立数据。'
        '它们不构成对第一轮研究过程的盲重复，也未用于再次调整方法。',
        '本轮冻结方法为\\texttt{' + tex(results['frozen']['method_id']) + '}。'
        '主测试、压力、观测数边界、校准和独立覆盖样本量依次为' +
        '、'.join(str(results['case_counts'][k]) for k in ('main', 'stress', 'edge', 'calibration', 'coverage')) + '。',
        r'\begin{table}[H]\centering\small',
        r'\caption{第二轮所有预定比较的平均定位误差与主测试成本。误差单位为米。}',
        r'\begin{tabular}{@{}lrrrr@{}}\toprule',
        r'方法 & 主测试 & 压力 & 边界 & 主测试工作量\\\midrule']
    for name, splits in results['metrics'].items():
        display = tex(name) + ('$^{*}$' if name in results.get('aliases', {}) else '')
        lines.append(display + ' & ' + ' & '.join(number(splits[s]['overall']['mean_m']) for s in ('main', 'stress', 'edge')) +
                     ' & ' + number(splits['main']['overall']['mean_work_units'], 1) + r'\\')
    lines += [r'\bottomrule\end{tabular}\end{table}',
        '旧基线工作量为求解器nfev，新组合方法为含CV等步骤的残差调用数，口径并不完全相同；'
        '其比值不能直接解释为加速比，耗时只代表本次机器实测。']
    if results.get('aliases'):
        lines.append('星号表示有效参数完全重合，复用同一预测而不视为独立方法：' +
            '；'.join(tex(a) + '$=$' + tex(b) for a, b in results['aliases'].items()) + '。')
    lines += [r'\begin{table}[H]\centering\small',
        r'\caption{主测试配对平均误差减少量及95\%区间；正值有利于第二轮冻结方法。}',
        r'\begin{tabular}{@{}lrrr@{}}\toprule',
        r'参照方法 & 减少量/m & 95\%区间/m & 相对减少/\%\\\midrule']
    for name in ('static_wls', 'static_robust', 'dynamic_robust', 'legacy_bic', 'v2_frozen'):
        c = results['comparisons']['main'][name]
        lines.append(tex(name) + ' & ' + number(c['mean_reduction_m']) + ' & [' +
                     ', '.join(number(v) for v in c['ci95_m']) + '] & ' + number(c['relative_mean_reduction_percent'], 2) + r'\\')
    lines += [r'\bottomrule\end{tabular}\end{table}',
        '区间在同一根种子与场景内重抽配对案例，并等权汇总固定单元；未校正多重比较，'
        '也不包含智能体搜索或方法选择的不确定性，因此属于探索性证据。']
    ni = results['static_noninferiority']['main']
    lines.append('静止干净场景相对WLS的平均误差比为' + number(ni['mean_error_ratio']) +
        '，95\\%区间为[' + ', '.join(number(v) for v in ni['ratio_ci95']) +
        ']。预定容忍界为1.10；点估计' + ('满足' if ni['point_meets_margin'] else '未满足') +
        '该界，配对差额的95\\%区间上界' + ('低于' if ni['ci_upper_below_margin'] else '未低于') +
        '零。两者应分开陈述，不作确认性非劣结论。')
    lines += [r'\begin{table}[H]\centering\small',
        r'\caption{全局与观测选模分组的Mondrian区域：独立同分布覆盖检验。}',
        r'\begin{tabular}{@{}lrrrr@{}}\toprule',
        r'校准规则 & 名义/\% & 实测覆盖/\% & 平均半径/m & 无界数\\\midrule']
    for method in ('global', 'mondrian'):
        for alpha, record in results['conformal'][method].items():
            pop = record['populations']['iid_test']
            lines.append(tex(method) + ' & ' + number(100 * (1 - float(alpha)), 0) + ' & ' +
                number(100 * pop['coverage'], 2) + ' & ' + number(pop['mean_radius_m']) + ' & ' + str(pop['unbounded_n']) + r'\\')
    lines += [r'\bottomrule\end{tabular}\end{table}',
        'Mondrian分组仅使用冻结定位器根据观测选择的静态或恒速标签；真实场景仅用于事后评价。'
        '其保证依赖校准与测试可交换，并对随机校准评分取平均；不保证给定本次校准集、真实场景、'
        '按半径拒绝后的子集或分布偏移案例的覆盖。校准组为空或秩超过组样本数时区域无界，明确保留该代价。',
        '全部场景、观测数边界、逐根种子结果、尾部与成本、压力与拒绝覆盖均保存在'
        r'\nolinkurl{results/revision_benchmark/summary.json}及配套逐例文件。'
        '三个测试根种子代表数据批次，不能解释为三个独立智能体研究过程。']
    return '\n\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=Path('results/revision_benchmark/summary.json'))
    parser.add_argument('--output', type=Path, default=Path('latex/sections/revision_results.tex'))
    args = parser.parse_args()
    results = json.loads(args.results.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(results), encoding='utf-8')
    print(args.output)


if __name__ == '__main__': main()
