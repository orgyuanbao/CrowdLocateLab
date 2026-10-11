"""Turn the saved, frozen-method sensitivity grid into a LaTeX section."""
from pathlib import Path
import argparse,json,sys,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_report import table,fig,tex,f
sys.path.insert(0,str(ROOT))
from benchmark import validate_frozen_run

def build(path,run):
    validate_frozen_run(ROOT,run)
    summary=json.loads(Path(path).read_text(encoding='utf8'))
    if summary.get('status')!='complete':raise ValueError('Sensitivity evaluation is incomplete')
    if summary['protocol']['frozen_method_sha256']!=hashlib.sha256((Path(run)/'tools/frozen_method.json').read_bytes()).hexdigest():
        raise ValueError('Sensitivity results and report run have different frozen evidence')
    cells=summary['cells'];nvals=[8,12,16,24];cvals=[0.,.1,.25,.4]
    sections=[r'\section{第一轮的观测数与污染敏感性研究}\label{sec:sensitivity}',
              r'\input{sections/sensitivity_protocol}',
              f"实际完成{summary['case_count']}个独立案例、{summary['prediction_count']}次定位预测，耗时{summary['wall_seconds']/60:.1f}分钟。以下结果没有返馈给第一轮研究智能体，也没有用于替换其已冻结方案；第二轮则明确把这些结果作为已暴露先验，另用新数据评价修订。"]
    all_wins={baseline:sum(c['paired_comparisons'][baseline]['mean_reduction_m']>0 for c in cells)
              for baseline in ['static_wls','dynamic_robust']}
    sections.append(f"在全部32个预设网格单元中，所选方法的样本均值低于静态WLS的单元为{all_wins['static_wls']}个，低于恒速稳健基线的单元为{all_wins['dynamic_robust']}个。每格仅12例，单元胜出数量是描述性结果，不是显著性检验或部署分布下的成功概率。")
    for scene,title in [('static_outliers','静止目标'),('moving_outliers','移动目标')]:
        lookup={(c['n_observations'],c['nominal_contamination']):c for c in cells if c['scenario']==scene}
        rows=[]
        for n in nvals:
            rows.append([str(n),*[f(lookup[n,c]['methods']['llm_selected']['mean_m']) for c in cvals]])
        sections.append(table(title+'：所选方法平均位置误差（m）。列为名义污染比例。',
                              ['观测数',r'0\%',r'10\%',r'25\%',r'40\%'],rows))
        plots=[r'\begin{tikzpicture}\begin{axis}[width=0.91\linewidth,height=6.4cm,xlabel={观测数},ylabel={平均位置误差（m）},xtick={8,12,16,24},ymin=0,grid=major,legend style={at={(0.5,1.04)},anchor=south,legend columns=4}]']
        for c,color,marker in zip(cvals,['navy','teal','orange','brick'],['*','square*','triangle*','diamond*']):
            pts=' '.join(f"({n},{lookup[n,c]['methods']['llm_selected']['mean_m']:.6f})" for n in nvals)
            plots.append(r'\addplot[thick,color='+color+',mark='+marker+'] coordinates {'+pts+'};'+r'\addlegendentry{'+str(round(c*100))+r'\%}')
        plots.append(r'\end{axis}\end{tikzpicture}')
        sections.append(fig('\n'.join(plots),title+'的样本量与污染敏感性。线段仅连接离散实验格点，不表示格点间的理论插值。','fig:sensitivity-'+scene))
        rows=[]
        for n in nvals:
            for c in cvals:
                cell=lookup[n,c];m=cell['methods']['llm_selected'];rate=m['selected_model_rates'].get('velocity',0)
                allowed=m['cv_dynamic_admissible_rate']
                rows.append([n,f(c*100,0)+r'\%',f(cell['actual_contamination']*100,1)+r'\%',
                  f(rate*100,1)+r'\%', '--' if allowed is None else f(allowed*100,1)+r'\%',
                  f(cell['paired_comparisons']['dynamic_robust']['mean_reduction_m'])])
        sections.append(table(title+'：模型选择及相对恒速基线的配对均值差。正差值表示所选方法误差较低。',
          ['观测数','名义污染','实际污染','选择恒速','CV可用','误差减少/m'],rows))
    sections.append('离散观测数使实际污染比例与名义比例有所差异，表中分别列出。CV可用率为空表示该方法未输出此诊断；不能将缺失信息当作零。若冻结方法使用三折观测门控，则8条观测时部分训练折少于6条，按预定实现会禁止选择恒速；这项设计限制应与由污染或优化失败引起的选择错误区分。')
    sections.append(r'\input{sections/sensitivity_interpretation}')
    (ROOT/'latex/sections/sensitivity_results.tex').write_text('\n\n'.join(sections),encoding='utf8')
    print(json.dumps({'section':'sensitivity_results.tex','grid_cells':len(cells),'cells_with_lower_sample_mean':all_wins},ensure_ascii=False))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--summary',type=Path,default=ROOT/'results/sensitivity/summary.json')
    p.add_argument('--run',type=Path,default=ROOT/'runs/live_reference');args=p.parse_args()
    build(args.summary,args.run)
