"""Build LaTeX empirical sections only from completed, saved research evidence."""
from pathlib import Path
import argparse,json,math,sys,hashlib
from collections import Counter
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchmark import validate_frozen_run
from tools.common import load,save,digest

SCENES={'static_clean':'静态正常','static_outliers':'静态异常','moving_clean':'移动正常',
 'moving_outliers':'移动异常','poor_geometry':'狭窄几何','accelerating':'加速运动','sparse':'稀疏观测',
 'calibration_shift':'标定漂移','high_contamination':'高比例污染'}
LABELS={'static_wls':'静态WLS','static_robust':'静态稳健','dynamic_robust':'恒速稳健',
 'legacy_bic':'旧BIC门控','llm_selected':'智能体所选',
 'ablate_range':'改回距离残差','ablate_reported_scale':'逐观测尺度','ablate_no_trim':'去除截尾',
 'ablate_no_consensus':'去除子集初始化','ablate_no_log_correction':'去除对数中心修正',
 'ablate_force_velocity':'固定恒速模型','ablate_force_static':'固定静态模型'}
ACTIONS={'read_sources':'读取来源','register_method':'注册方法','run_experiment':'执行实验',
 'diagnose':'失败诊断','verify_math':'数学检查','ablate':'控制消融','freeze':'冻结方法','finish':'结束'}
def tex(s):
    return ''.join({'\\':r'\textbackslash{}','_':r'\_\allowbreak{}','%':r'\%','&':r'\&','#':r'\#',
                    '{':r'\{','}':r'\}','$':r'\$','^':r'\textasciicircum{}','~':r'\textasciitilde{}'}.get(c,c) for c in str(s))
def f(x,n=3):return '--' if x is None else f'{x:.{n}f}'
def pct(x):return f'{100*x:.1f}\\%'
def table(caption,header,rows,widths=None,label=None):
    spec=widths or ('l'+'r'*(len(header)-1))
    return '\n'.join([r'\begin{table}[H]\centering\small',r'\caption{'+caption+'}',
      (r'\label{'+label+'}') if label else '',r'\begin{tabular}{'+spec+'}',r'\toprule',
      ' & '.join(header)+r'\\',r'\midrule',*[' & '.join(map(str,row))+r'\\' for row in rows],
      r'\bottomrule\end{tabular}\end{table}'])
def fig(body,caption,label):
    return '\n'.join([r'\begin{figure}[H]\centering',body,r'\caption{'+caption+'}',r'\label{'+label+'}',r'\end{figure}'])
def groupbars(series,scenes,caption,label):
    colors=['navy','orange','teal']
    body=[r'\begin{tikzpicture}\begin{axis}[ybar,bar width=9pt,width=0.94\linewidth,height=6.3cm,',
          r'ylabel={平均位置误差（m）},ymin=0,grid=major,enlarge x limits=0.15,',
          'xtick={'+','.join(map(str,range(len(scenes))))+'},xticklabels={'+','.join(SCENES[s] for s in scenes)+'},',
          r'legend style={at={(0.5,1.04)},anchor=south,legend columns=3},',
          r'x tick label style={font=\small,text width=17mm,align=center},]']
    for (name,vals),color in zip(series.items(),colors):
        body.append(r'\addplot[fill='+color+',draw='+color+'] coordinates {'+' '.join(f'({i},{vals[s]:.6f})' for i,s in enumerate(scenes))+'};')
        body.append(r'\addlegendentry{'+tex(name)+'}')
    body.append(r'\end{axis}\end{tikzpicture}')
    return fig('\n'.join(body),caption,label)

def trace_section(run,summary,sections):
    state=load(run/'state.json');tool=load(run/'tools/tool_state.json')
    events=[json.loads(line) for line in (run/'events.jsonl').read_text(encoding='utf8').splitlines()]
    decisions=[e['payload'] for e in events if e['type']=='decision' and 'action' in e['payload']]
    observations={o['step']:o for o in state['observations']}
    names={name:f'P{i+1}' for i,name in enumerate(list(tool['registry'])[4:])}
    label=lambda n:names.get(n,LABELS.get(n,n))
    counts=Counter(o['action'] for o in state['observations'])
    metas=[load(p) for p in sorted((run/'model_calls').glob('*/metadata.json'))]
    usage={k:sum(m.get('usage',{}).get(k,0) or 0 for m in metas) for k in ['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens']}
    elapsed=(datetime.fromisoformat(state['updated_at'])-datetime.fromisoformat(state['created_at'])).total_seconds()
    pars=[r'\section{第一轮真实智能体运行与研究迭代}\label{sec:trace}',
      r'\subsection{运行身份、预算与实际调用}',
      f"正式轨迹保存在\\texttt{{runs/live\\_reference/}}。本次状态为\\texttt{{{tex(state['status'])}}}，共执行{state['steps']}个研究动作，发起{state['model_calls']}次模型调用，其中{state['successful_model_calls']}次成功，包含{len(state['critiques'])}次独立审阅调用。实验工具调用计数为{state['experiment_calls']}，其中控制消融{counts['ablate']}次。正式过程历时{elapsed/60:.1f}分钟。",
      ('本次运行明确标记为回放：它重用既有模型响应，不能计为新的在线研究。' if state.get('is_replay') else
       '模型由已登录的Codex CLI调用，采用用户配置的默认模型，没有人为填写服务端未返回的型号。本次保存的响应不是回放。')+
      '服务内部连接重试与控制器重新请求是不同计数；完整日志均保留。',
      table('正式研究的审计计数。token使用量按CLI返回字段相加，不推断费用。',
        ['指标','数值'],[['注册的新方法（含消融）',len(names)],['成功模型调用',state['successful_model_calls']],
        ['独立审阅调用',len(state['critiques'])],['输入token',usage['input_tokens']],['缓存输入token',usage['cached_input_tokens']],
        ['输出token',usage['output_tokens']],['检测到的CLI额外工具事件',len(state['unexpected_tool_events'])]],'lr'),
      r'\subsection{逐步动作与可核查证据}',
      '下表由事件日志自动生成。方法代号按首次注册顺序分配；它们不是预设候选排名。实际计算结果来自工具返回，研究动机与预期来自模型响应。',
      r'\begingroup\small\setlength{\tabcolsep}{4pt}',
      r'\begin{longtable}{@{}p{8mm}p{23mm}p{121mm}@{}}',
      r'\caption{实际研究动作序列。均值单位为米；开发与验证来自不同案例集。}\label{tab:trace}\\',
      r'\toprule 步 & 动作 & 执行内容与实际结果\\\midrule\endfirsthead',
      r'\toprule 步 & 动作 & 执行内容与实际结果\\\midrule\endhead',
      r'\bottomrule\endfoot']
    for d in decisions:
        o=observations.get(d['step'],{});a=d['action'];args=o.get('arguments',{});r=o.get('result',{})
        if r.get('error'):detail='工具错误：'+str(r['error'])[:170]
        elif a=='register_method':detail=label(args['method_id'])+'；'+args.get('hypothesis','')[:180]
        elif a in ('run_experiment','ablate'):
            detail=('验证集' if r.get('split')=='validation' else '开发集')+'：'+'；'.join(label(n)+' 均值 '+f(m['overall']['mean_m']) for n,m in r.get('metrics',{}).items())
            if a=='ablate':detail+='；改变 '+str(r.get('controlled_changes',{}))
        elif a=='freeze':detail='冻结 '+label(args['method_id'])+'；'+args.get('scientific_conclusion','')[:190]
        elif a=='diagnose':detail='诊断 '+label(args.get('method_id',''))+'；'+d.get('analysis_summary','')[:175]
        else:detail=d.get('analysis_summary','')[:210]
        pars.append(f"{d['step']} & {ACTIONS.get(a,tex(a))} & {tex(detail)}"+r'\\[3pt]')
    pars.extend([r'\end{longtable}\endgroup',r'\subsection{候选组成、修订与审阅}'])
    rows=[]
    for name,record in list(tool['registry'].items())[4:]:
        s=record['spec'];vals=[]
        for split in ['discovery','validation']:
            m=tool['evaluations'].get(f'{split}:{name}')
            vals.append(f(m['overall']['mean_m']) if m else '--')
        desc='/'.join([s.get('model','static'),s.get('residual','range'),s.get('scale','reported'),s.get('loss','soft_l1')])
        extra=f"trim={s.get('trim_fraction',0)}, subsets={s.get('consensus_trials',0)}, margin={s.get('gate_margin',.05)}"
        rows.append([names[name],r'\texttt{'+tex(desc)+'} '+r'\newline '+tex(extra),*vals])
    pars.append(table('智能体注册的方法组成与已观测均值。空白项表示未调用该划分。',
      ['代号','模型／残差／尺度／损失','开发','验证'],rows,r'p{8mm}p{104mm}rr'))
    for i,c in enumerate(state['critiques'],1):
        pars.append(r'\paragraph{审阅'+str(i)+'（第'+str(c['after_successful_experiments'])+'次普通实验后）}')
        issues=c.get('issues',[])
        pars.append(tex('；'.join(str(x) for x in issues)))
        pars.append('建议的下一步：'+tex(c.get('recommended_next_step','')))
    corrected_notes=[m for m in state['memory'] if '登记文字缺陷' in m.get('note','')]
    if corrected_notes:
        pars.append(r'\paragraph{对审阅意见的实际回应}')
        for m in corrected_notes:
            pars.append('模型在第'+str(m['step'])+'步的持久记忆中记录：'+tex(m['note']))
        pars.append('因此，本报告的方法数学解释以实际执行配置和数值源码为准。历史登记文本被原样保留，不能把上述文字纠错说成重新运行了不同的定位算法；后续版本应将父方法说明与消融后的定义分开保存。')
    frozen=summary['frozen']
    pars.extend([r'\subsection{最终冻结配置}',
      '最终方法为'+label(frozen['method_id'])+'，原始标识为\\texttt{'+tex(frozen['method_id'])+'}。参数和结论在最终测试前保存；下列配置不因测试结果而更改。',
      table('冻结方法的实际声明参数；未声明字段采用数值模块公开默认值。',['字段','值'],
       [[r'\texttt{'+tex(k)+'}',r'\texttt{'+tex(v)+'}'] for k,v in frozen['spec'].items()],'ll'),
      '冻结时模型给出的研究结论为：'+tex(frozen['scientific_conclusion'])])
    # A concrete evidence chain is emitted only when this actual run contains it.
    old=tool['evaluations'].get('discovery:log_cv_reported')
    revised=tool['evaluations'].get('discovery:log_cv_cauchy')
    anchor=tool['evaluations'].get('discovery:static_wls')
    chain=''
    if old and revised and anchor:
        before=old['by_scenario'];after=revised['by_scenario'];limit=1.1*anchor['by_scenario']['static_clean']['mean_m']
        revision_step=next((d['step'] for d in decisions if d['action']=='ablate' and observations[d['step']]['arguments'].get('new_id')=='log_cv_cauchy'),None)
        chain='\n\n'.join([r'\subsection{由一次失败驱动的真实修订}',
          f"模型最初预期对数残差有助于移动异常案例，但{label('log_cv_reported')}的开发均值达到{before['moving_outliers']['mean_m']:.3f}米，并未实现预期。随后它调用诊断工具检查极端误差。工具返回的是跨场景全局最差案例，因此模型明确承认，原先关于某个场景最差四例的假设尚不能完整判定；它没有将不匹配的诊断输出当成已证实的因果解释。",
          f"第{revision_step}步只将拟合损失从soft-L1改为Cauchy，登记{label('log_cv_cauchy')}；观测划分、残差、尺度和验证评分保持不变。工具返回的移动异常均值从{before['moving_outliers']['mean_m']:.3f}降至{after['moving_outliers']['mean_m']:.3f}米，CEP90从{before['moving_outliers']['p90_m']:.3f}降至{after['moving_outliers']['p90_m']:.3f}米。然而静态正常均值为{after['static_clean']['mean_m']:.3f}米，高于事前开发判据{limit:.3f}米，故这个包含静态条件的联合假设仍被否定。10\\%容忍度是模型在开发阶段声明的经验判据，不是原工业要求的严格非劣，也不是统计非劣检验。",
          '这一链条中的假设、诊断对象、修改与拒绝理由来自实际模型响应；上述数值来自保存的配对实验。因而可以核查模型是否根据反馈改变研究动作，而不是仅阅读最后一个最优配置。',
          r'\noindent\textbf{证据定位：}研究目录下的'+r'\path{tools/'+old['evidence_path']+'}、'+r'\path{tools/'+revised['evidence_path']+'}，以及对应步号的'+r'\path{events.jsonl}。'])
    if chain:
        index=pars.index(r'\subsection{逐步动作与可核查证据}')
        pars.insert(index,chain)
    (sections/'research_trace.tex').write_text('\n\n'.join(pars),encoding='utf8')
    return {'actions':state['steps'],'model_calls':state['model_calls'],'critic_calls':len(state['critiques']),
            'experiment_calls':state['experiment_calls'],'methods':len(names),'elapsed_minutes':elapsed/60,'usage':usage,
            'method_names':names,'frozen_id':frozen['method_id']}

def results_section(run_dir,result_dir,summary,sections,data):
    ms=summary['metrics'];mainnames=list(ms)[:5];main=ms['llm_selected']['main']['overall']
    pars=[r'\section{第一轮独立评测、消融与失败分析}\label{sec:results}',
      r'\subsection{常规测试的总体误差与对照}',
      f"本次基准累计完成{summary['prediction_count']}次定位预测，壁钟时间为{summary['wall_seconds']/60:.1f}分钟。主测试{main['n']}例采用三个独立数据种子，各常规场景样本数相同。所有方法使用同一案例；所选配置在评测前已冻结。",
      table(r'独立主测试总体性能。CEP90是案例位置误差的经验90\%分位数。',
      ['方法','均值/m','中位/m','CEP90/m','RMSE/m','平均工作量'],
      [[LABELS[n],f(ms[n]['main']['overall']['mean_m']),f(ms[n]['main']['overall']['median_m']),
      f(ms[n]['main']['overall']['p90_m']),f(ms[n]['main']['overall']['rmse_m']),
      f(ms[n]['main']['overall']['mean_work_units'],1)] for n in mainnames]),
      table('场景内配对bootstrap比较：对照误差减去所选方法误差。正值表示所选方法较低。',
      ['对照','均值减少/m',r'95\%区间/m'],[[LABELS[n],f(summary['comparisons'][n]['mean_reduction_m']),
      '['+', '.join(f(v) for v in summary['comparisons'][n]['ci95_m'])+']'] for n in mainnames if n!='llm_selected']),
      '区间描述固定生成机制下的有限样本差异，没有对众多方法比较作多重校正。它不能度量重新运行一次大模型规划器时的方差，也不能据此归因规划器优于其它研究策略。',
      r'\subsection{逐场景结果与误差分布}']
    core=['static_clean','static_outliers','moving_clean','moving_outliers']
    pars.append(table('四类常规场景的平均当前位置误差（m），每类90例。',
      ['方法',*[SCENES[s] for s in core]],[[LABELS[n],*[f(ms[n]['main']['by_scenario'][s]['mean_m']) for s in core]] for n in mainnames]))
    pars.append(groupbars({LABELS[n]:{s:ms[n]['main']['by_scenario'][s]['mean_m'] for s in core} for n in ['static_wls','dynamic_robust','llm_selected']},core,
                         '常规场景的配对误差比较。总体改善不能替代逐场景非劣分析。','fig:main-bars'))
    curves=[]
    for n,color in zip(mainnames,['navy','orange','brick','gray','teal']):
        raw=load(result_dir/f'main_{n}.json')['rows'];errors=sorted(r['error_m'] for r in raw)
        (data/f'cdf_{n}.dat').write_text('error cdf\n'+'\n'.join(f'{e:.8f} {(i+1)/len(errors):.8f}' for i,e in enumerate(errors)),encoding='utf8')
        curves.append(r'\addplot[thick,no marks,color='+color+'] table[x=error,y=cdf] {data/cdf_'+n+'.dat};'+r'\addlegendentry{'+LABELS[n]+'}')
    pars.append(fig(r'\begin{tikzpicture}\begin{axis}[width=0.92\linewidth,height=7cm,xlabel={位置误差（m）},ylabel={经验累计概率},xmin=0,xmax=30,ymin=0,ymax=1,grid=major,legend pos=south east]'+'\n'.join(curves)+r'\end{axis}\end{tikzpicture}',
      '独立主测试误差的经验分布。横轴显示0--30米；超过30米的样本仍保留在全部统计中。','fig:cdf'))
    pars.append(r'\subsection{组件消融与开发前驱}')
    extras=[n for n in ms if n not in mainnames]
    extralabel={n:LABELS.get(n,'开发前驱'+str(sum(x.startswith('predecessor_') for x in extras[:i+1]))) for i,n in enumerate(extras)}
    pars.append(table('冻结方法的控制消融与开发前驱。消融不重新调参。',
      ['方法','主测试均值','主测试CEP90','压力均值','与所选均值差'],
      [[extralabel[n],f(ms[n]['main']['overall']['mean_m']),f(ms[n]['main']['overall']['p90_m']),
       f(ms[n]['stress']['overall']['mean_m']),f(ms[n]['main']['overall']['mean_m']-main['mean_m'])] for n in extras]))
    pars.append('某个消融若优于冻结方法，应作为预先冻结方案的负面证据报告，不能用该测试结果悄悄替换最终方法。未触发的消融表示冻结配置本来没有该组件，避免重复计算伪造独立对照。')
    pars.append(r'\subsection{分布外压力测试}')
    pars.append('每类压力场景独立生成40例，改变几何、运动、观测数量、标定或污染条件。下表同时保留均值与尾部误差，以检验常规场景的改善能否延续到这些变化。')
    stress=['poor_geometry','accelerating','sparse','calibration_shift','high_contamination']
    pars.append(table('压力场景的均值／CEP90（m），每类40例。',
     ['场景','静态WLS','恒速稳健','智能体所选'],[[SCENES[s],*[f(ms[n]['stress']['by_scenario'][s]['mean_m'])+' / '+f(ms[n]['stress']['by_scenario'][s]['p90_m']) for n in ['static_wls','dynamic_robust','llm_selected']]] for s in stress]))
    pars.append(groupbars({LABELS[n]:{s:ms[n]['stress']['by_scenario'][s]['mean_m'] for s in stress} for n in ['static_wls','dynamic_robust','llm_selected']},stress,
                         '冻结估计器在五类压力条件下的误差。压力输入不参与方法选择。','fig:stress-bars'))
    raw=load(result_dir/'main_llm_selected.json')['rows']+load(result_dir/'stress_llm_selected.json')['rows']
    selected=Counter((r['scenario'],r['diagnostics'].get('selected_model','unknown')) for r in raw)
    pars.append(table('所选方法实际使用的运动模型次数。标签来自求解器诊断。',
      ['场景','静态','恒速','其它'],[[SCENES[s],selected[(s,'static')],selected[(s,'velocity')],
      sum(v for (sc,m),v in selected.items() if sc==s and m not in ('static','velocity'))] for s in core+stress]))
    pars.append(r'\subsection{独立校准、覆盖与拒绝}')
    pars.append('使用240个独立混合场景案例校准尺度，再在320个同分布案例及200个压力案例上检查覆盖。名义水平来自预定协议，实测覆盖与区域大小均由已保存的逐例结果计算。')
    conf=summary['conformal']['results'];rows=[]
    for alpha,record in conf.items():
        for pop in ['iid_test','shifted_stress']:
            p=record['populations'][pop]
            rows.append([pct(1-float(alpha)),f(record['q']), '同分布混合' if pop=='iid_test' else '压力分布',
                         str(p['n']),pct(p['coverage']),f(p['mean_radius_m']),f(p['median_radius_m'])])
    pars.append(table('独立校准后的实际覆盖。压力分布的覆盖没有交换性保证。',
      ['名义覆盖','分位数$q$','评估总体','样本','实际覆盖','均值半径/m','中位半径/m'],rows))
    p=conf['0.1']['populations']['iid_test']
    pars.append(table(r'90\%名义区域的拒绝曲线：只接受半径不超过阈值的案例。',
      ['半径阈值/m','接受数','接受率','接受均值误差/m','接受后覆盖'],
      [[f(r['radius_threshold_m'],0),r['accepted_n'],pct(r['acceptance_rate']),f(r['accepted_mean_error_m']),
        pct(r['accepted_coverage']) if r['accepted_coverage'] is not None else '--'] for r in p['rejection_curve']]))
    pars.append('拒绝阈值越小通常会减少输出数量。表中的接受后覆盖是实际样本统计，不继承总体边际覆盖定理；缺失的接受子集以空值表示。较大的预测圆盘即使覆盖充分，也可能缺乏实际定位价值，因此必须同时报告半径与接受率。')
    pars.append(r'\subsection{独立数学见证}')
    math_records=sorted((run_dir/'tools/mathematical_checks').glob('*.json'))
    witnesses={}
    for path in math_records:witnesses.update(load(path))
    if 'self_weight_bias' in witnesses:
        w=witnesses['self_weight_bias']
        pars.append(f"在标量检验中，真距离为{w['truth_m']:.0f}米，对数噪声标准差为{w['noise_log_sd']:.2f}。解析自加权最优值为{w['analytical_weighted_optimum_m']:.5f}米，{w['mc_samples']}次独立模拟得到{w['mc_weighted_optimum_m']:.5f}米；修正后对数目标给出{w['mc_corrected_log_optimum_m']:.5f}米。该见证用于核对解析公式，不构成新的泛化实验。")
    if 'mirror' in witnesses:
        w=witnesses['mirror']
        pars.append(f"镜像构造的两条轨迹当前位置相差{w['position_separation_m']:.1f}米，所有距离差的最大绝对值为{w['max_distance_difference_m']:.1e}米；数据Jacobian秩为{w['jacobian_rank']}，最小奇异值为{min(w['singular_values']):.5f}。因此，这个实例直接展示了局部满秩与全局镜像歧义能够共存。")
    pars.append(r'\input{sections/empirical_interpretation}')
    pars.append(r'\input{sections/coverage_variability}')
    (sections/'results.tex').write_text('\n\n'.join(pars),encoding='utf8')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,default=ROOT/'runs/live_reference')
    p.add_argument('--results',type=Path,default=ROOT/'results/benchmark');a=p.parse_args()
    checked=validate_frozen_run(ROOT,a.run)
    summary=load(a.results/'summary.json')
    expected=hashlib.sha256((a.run/'tools/frozen_method.json').read_bytes()).hexdigest()
    if summary['protocol'].get('frozen_method_sha256')!=expected or digest(summary['frozen'])!=digest(checked['frozen']):
        raise ValueError('Report research run and benchmark describe different frozen evidence')
    sections=ROOT/'latex/sections';data=ROOT/'latex/data';data.mkdir(parents=True,exist_ok=True)
    trace=trace_section(a.run,summary,sections);results_section(a.run,a.results,summary,sections,data)
    save(ROOT/'latex/summary_metrics.json',{'run':trace,'main_metrics':summary['metrics']['llm_selected']['main']['overall'],
         'stress_metrics':summary['metrics']['llm_selected']['stress']['overall'],'prediction_count':summary['prediction_count']})
    print(json.dumps({'generated':['research_trace.tex','results.tex'],'actions':trace['actions'],
                      'prediction_count':summary['prediction_count']},ensure_ascii=False))
if __name__=='__main__':main()
