"""Build the revision trace and research figures only from completed evidence."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import math
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from revision.evaluation import validate_revision_freeze
from revision.report import render
from tools.common import digest
from reporting.build_report import fig,tex


def f(value, n=3):
    """Report explicit unbounded regions without treating them as missing data."""
    if value is None: return '--'
    if value == 'infinity': return r'$\infty$'
    if value == '-infinity': return r'$-\infty$'
    value=float(value)
    if math.isnan(value): raise ValueError('NaN is not reportable study evidence')
    if math.isinf(value): return r'$\infty$' if value>0 else r'$-\infty$'
    return f'{value:.{n}f}'


def percent(value):
    return '--' if value is None else f(100*float(value),2)


def table(caption,header,rows,widths=None,label=None):
    """Bound tables by the current text width, with wrapping cells."""
    column=r'>{\raggedright\arraybackslash}X'
    spec=widths or ('@{}'+column*len(header)+'@{}')
    return '\n'.join([r'\begin{table}[H]\centering\small',r'\caption{'+caption+'}',
        (r'\label{'+label+'}') if label else '',
        r'\begin{tabularx}{\linewidth}{'+spec+'}',r'\toprule',
        ' & '.join(header)+r'\\',r'\midrule',
        *[' & '.join(map(str,row))+r'\\' for row in rows],
        r'\bottomrule\end{tabularx}\end{table}'])

LABELS={'static_wls':'静态WLS','static_robust':'静态稳健','dynamic_robust':'恒速稳健',
        'legacy_bic':'BIC门控','v2_frozen':'第一轮冻结','revision_selected':'第二轮冻结',
        'ablate_k3':'改为三折门控','ablate_k4':'改为四折门控',
        'ablate_force_velocity':'固定恒速','ablate_force_static':'固定静态'}
ACTIONS={'read_sources':'读取先验','register_method':'登记候选','run_experiment':'执行实验',
         'diagnose':'失败诊断','ablate':'单组件消融','verify_math':'数学核验','freeze':'冻结方法','finish':'结束'}
SCENES={'static_clean':'静态正常','static_outliers':'静态异常','moving_clean':'移动正常',
        'moving_outliers':'移动异常','poor_geometry':'狭窄几何','accelerating':'加速运动',
        'sparse':'稀疏观测','calibration_shift':'标定漂移','high_contamination':'高污染'}

def load(path):return json.loads(Path(path).read_text(encoding='utf8'))


def public_excerpt(text,limit=210):
    text=str(text)
    return text if len(text)<=limit else text[:limit]+'……（节选）'


def public_paragraphs(text,limit=320):
    """Break public source wording into readable paragraphs without omitting it."""
    output=[];current=''
    for sentence in re.split(r'(?<=[。；！？])',str(text)):
        if current and len(current)+len(sentence)>limit:
            output.append(tex(current));current=''
        current+=sentence
    if current:output.append(tex(current))
    return '\n\n'.join(output)


def trace(run, summary):
    run=Path(run)
    state=load(run/'state.json');tool=load(run/'tools/tool_state.json')
    if state.get('status')!='frozen' or not state.get('frozen') or not tool.get('frozen'):
        raise ValueError('A running or incomplete trace cannot be reported as frozen')
    if digest(state.get('freeze_result'))!=digest(summary.get('frozen')):
        raise ValueError('Trace freeze result differs from evaluation freeze')
    decisions={event['payload']['step']:event['payload'] for event in
      (json.loads(line) for line in (run/'events.jsonl').read_text(encoding='utf8').splitlines())
      if event['type']=='decision' and 'action' in event['payload']}
    ids=tool.get('new_method_ids',[]);names={name:f'R{i+1}' for i,name in enumerate(ids)}
    label=lambda name:names.get(name,LABELS.get(name,name))
    elapsed=(datetime.fromisoformat(state['updated_at'])-datetime.fromisoformat(state['created_at'])).total_seconds()/60
    lines=[r'\section{第二轮真实智能体轨迹}',r'\label{sec:revision-trace}',
      f"本次第二轮保存了{state['steps']}个研究动作，发起{state['model_calls']}次模型请求，"
      f"其中{state['successful_model_calls']}次获得成功响应，"
      f"其中审阅调用{len(state['critiques'])}次；实验与消融调用{tool['experiment_calls']}次，"
      f"登记{len(ids)}个新候选或消融版本，历时{elapsed:.1f}分钟。正式状态为冻结。"+
      ('本记录明确标为回放，不能计作新的在线模型研究。' if state.get('is_replay') else '本记录由真实在线模型调用产生，未标为回放。')+
      '网络等待包含在历时中，不能将其解释为定位求解耗时。各行决策来自模型返回，观测来自实际工具执行。',
      r'\begingroup\small\setlength{\tabcolsep}{4pt}',
      r'\begin{longtable}{@{}p{8mm}p{21mm}p{\dimexpr\linewidth-29mm-16pt\relax}@{}}',
      r'\caption{第二轮的完整动作顺序。方法以R编号简写，原始标识保留在方法表及日志中。}\\',
      r'\toprule 步 & 动作 & 公开决策依据与工具观测\\\midrule\endfirsthead',
      r'\toprule 步 & 动作 & 公开决策依据与工具观测\\\midrule\endhead']
    for observation in state['observations']:
        step=observation['step'];action=observation['action'];result=observation['result'];args=observation['arguments']
        rationale=decisions.get(step,{}).get('analysis_summary','')
        if len(rationale)>155:rationale=rationale[:152]+'……'
        info=''
        if result.get('ok',True) is False or result.get('error') or result.get('status') in ('error','failed'):
            info='工具失败：'+str(result.get('error',result))[:180]
        elif action in ('run_experiment','ablate'):
            info=('验证' if result.get('split',args.get('split'))=='validation' else '发现')+'集五场景均值/m：'+ '；'.join(
                label(name)+' '+f(record['macro_mean_m']) for name,record in result.get('metrics',{}).items())
            if action=='ablate':info+='。实际改动：'+json.dumps(result.get('controlled_changes',{}),ensure_ascii=False)
        elif action=='register_method':info='登记'+label(args['method_id'])+'；'+str(args.get('hypothesis',''))[:130]
        elif action=='diagnose':
            worst=result.get('worst_cases',[])
            info='诊断'+label(args['method_id'])+'；'+('最差返回案例误差'+f(worst[0]['error_m'])+' m' if worst else '无最差案例')
        elif action=='verify_math':info='核验：'+args.get('proposition','all')+'。数值见证不替代数学证明。'
        elif action=='freeze':info='冻结'+label(args['method_id'])+'；后续开发接口关闭。'
        elif action=='read_sources':info='读取原问题、实现数学、预登记计划及已暴露第一轮结果。'
        lines.append(f"{step} & {ACTIONS.get(action,action)} & "+tex(rationale+' '+info)+r'\\[3pt]')
    lines += [r'\bottomrule\end{longtable}\endgroup',r'\subsection{登记组合及冻结选择}']
    rows=[]
    for name in ids:
        record=tool['registry'][name];s=record['spec']
        desc=f"{s.get('model','velocity')}; K={s.get('cv_folds',3)}; margin={s.get('gate_margin',.05)}; "
        desc+=f"{s.get('loss','soft_l1')}; {s.get('residual','range')}; starts={s.get('starts',3)}"
        val=tool['evaluations'].get('validation:'+name)
        rows.append([names[name],tex(name),tex(desc),f(val['macro_mean_m']) if val else '--'])
    lines.append(table('新方法的主要组成。非CV模型不使用K和门槛；完整声明见原始注册表。',
      ['编号','原始标识','主要设置','验证均值/m'],rows,
      widths=r'@{}p{8mm}p{36mm}>{\raggedright\arraybackslash}Xp{19mm}@{}'))
    lines.append('最终冻结方法为'+label(state['freeze_result']['method_id'])+'，其完整配置如下。')
    lines.append(table('第二轮冻结配置，测试后不再调整。',['字段','值'],
                       [[tex(k),tex(v)] for k,v in state['freeze_result']['spec'].items()]))
    lines.append(r'\subsection{审阅反馈与研究结论原始记录}')
    for index,critic in enumerate(state['critiques'],1):
        lines.append(r'\noindent\textbf{审阅'+str(index)+'。} '+
            f"在{critic.get('after_successful_experiments','--')}次成功实验后记录。"
            '以下按真实审阅字段整理公开摘要，较长字段标明节选；这不是私有思维链。')
        lines.append(r'\noindent\textbf{范围检查：} '+('通过。' if critic['scope_valid'] else '未通过。'))
        issues=critic.get('issues',[])
        if issues:
            lines.append(r'\noindent\textbf{指出的问题：}\begin{enumerate}[leftmargin=2em,nosep]'+
                '\n'+ '\n'.join(r'\item '+tex(public_excerpt(issue)) for issue in issues)+r'\end{enumerate}')
        else:lines.append('本次审阅没有记录具体问题条目。')
        lines.append(r'\noindent\textbf{下一步建议：} '+tex(public_excerpt(critic['recommended_next_step'],360)))
        caveats=critic.get('evidence_caveats',[])
        if caveats:
            lines.append(r'\noindent\textbf{证据边界：}\begin{itemize}[leftmargin=2em,nosep]'+
                '\n'+'\n'.join(r'\item '+tex(public_excerpt(item)) for item in caveats)+r'\end{itemize}')
        lines.append(r'完整公开审阅字段见\nolinkurl{runs/revision_reference/state.json}中的critiques，'
                     r'及对应\nolinkurl{model_calls/}响应记录。独立提示不等于不同模型。')
    lines.append(r'\noindent\textbf{冻结时的公开结论（保留原文）：}')
    lines.append(public_paragraphs(state['freeze_result']['scientific_conclusion']))
    lines.append(r'完整提示、响应、失败和用量见\nolinkurl{runs/revision_reference/model_calls/}；'
                 r'完整动作、工具输入输出及证据链见同目录\texttt{events.jsonl}。')
    return '\n\n'.join(lines)+'\n'


def extra_results(summary):
    m=summary['metrics'];out=[r'\subsection{全场景与跨种子的结果}']
    edge_plan=summary['protocol']['plan']['edge'];edge_ns=edge_plan['observation_n']
    for split in ('main','stress'):
        scenes=list(m['revision_selected'][split]['by_scenario'])
        names=['static_wls','dynamic_robust','legacy_bic','v2_frozen','revision_selected']
        out.append(table(('主测试' if split=='main' else '压力测试')+'的逐场景均值，单位m。',
          ['场景',*[LABELS[n] for n in names]],
          [[SCENES[s],*[f(m[n][split]['by_scenario'][s]['mean_m']) for n in names]] for s in scenes]))
    out.append(table('三个新主测试数据批次的总体均值。它们不是三次智能体研究。',
      ['根种子','第一轮冻结/m','第二轮冻结/m','恒速稳健/m'],
      [[seed,*[f(summary['per_seed'][n][seed]['mean_m']) for n in ['v2_frozen','revision_selected','dynamic_robust']]]
       for seed in summary['per_seed']['revision_selected']]))
    out.append(r'\subsection{观测数边界与计算成本}')
    for scene in ('static_clean','moving_clean'):
        curves=[]
        for name,color,marker in [('v2_frozen','navy','square*'),('revision_selected','teal','*'),('dynamic_robust','orange','triangle*')]:
            pts=' '.join(f"({n},{m[name]['edge']['by_edge_group'][f'n{n}:{scene}']['mean_m']:.6f})" for n in edge_ns)
            curves.append(r'\addplot[thick,color='+color+',mark='+marker+'] coordinates {'+pts+'};'+r'\addlegendentry{'+LABELS[name]+'}')
        out.append(fig(r'\begin{tikzpicture}\begin{axis}[width=.92\linewidth,height=6.6cm,xlabel={观测数量},ylabel={平均位置误差（m）},xtick={'+
          ','.join(map(str,edge_ns))+r'},ymin=0,grid=major,legend style={at={(.5,1.04)},anchor=south,legend columns=3}]'+
          '\n'.join(curves)+r'\end{axis}\end{tikzpicture}',SCENES[scene]+f'的完整观测数边界；每格{edge_plan["per_cell"]}例，连线只表示离散格点。','fig:revision-edge-'+scene))
    rows=[]
    for n in edge_ns:
        row=[n]
        for scene in ('static_clean','moving_clean'):
            key=f'n{n}:{scene}'
            row.extend(f(m[name]['edge']['by_edge_group'][key]['mean_m']) for name in ['v2_frozen','revision_selected'])
        rows.append(row)
    out.append(table('边界误差表（m），同时保留静态和运动表现。',['观测数','旧静态场景','新静态场景','旧运动场景','新运动场景'],rows))
    out.append(table('新主测试的成本与尾部。不同实现的工作量口径不同，耗时为本机单次测量。',
      ['方法','P90/m','P95/m','最大/m','工作量','耗时/ms'],
      [[LABELS[name],*[f(m[name]['main']['overall'][key]) for key in ('p90_m','p95_m','max_m')],
        f(m[name]['main']['overall']['mean_work_units'],1),f(1000*m[name]['main']['overall']['mean_seconds'],2)]
       for name in ['static_wls','dynamic_robust','v2_frozen','revision_selected']]))
    out.append(r'\subsection{覆盖区间、分组差异和分布偏移}')
    qrows=[]
    for rule in ('global','mondrian'):
        for alpha in ('0.1','0.05'):
            for group,record in summary['conformal'][rule][alpha]['calibration'].items():
                qrows.append(['全局' if rule=='global' else '分组',round(100*(1-float(alpha))),
                    {'global':'全部','static':'输出静态','velocity':'输出恒速'}[group],
                    record['n'],record['rank'],f(record['q']),'是' if record['unbounded'] else '否'])
    out.append(table('有限样本秩与实际校准阈值。空组或秩超过样本数时明确保留无界区域。',
        ['规则','名义/\\%','校准组','样本','秩','阈值q','无界'],qrows))
    for alpha in ('0.1','0.05'):
        rows=[]
        for rule in ('global','mondrian'):
            iid=summary['conformal'][rule][alpha]['populations']['iid_test']
            for group,values in [('总体',iid),*[(SCENES[s],v) for s,v in iid['by_scenario'].items()],
                                  *[('输出静态' if g=='static' else '输出恒速',v) for g,v in iid['by_group'].items()]]:
                ci=values.get('coverage_wilson95')
                rows.append(['全局' if rule=='global' else '分组',group,values['n'],percent(values['coverage']),
                   ('['+', '.join(f(v*100,2) for v in ci)+']') if ci else '--',f(values['mean_radius_m'])])
        out.append(table(f'{round((1-float(alpha))*100)}\\%名义水平的IID总体、真实场景与观测选模分组覆盖。区间为Wilson95\\%，覆盖和区间单位为\\%。',
          ['规则','评价组','样本','覆盖','区间','半径/m'],rows))
    rows=[]
    for rule in ('global','mondrian'):
        for alpha in ('0.1','0.05'):
            for split,label in [('shifted_stress','压力'),('edge','边界')]:
                pop=summary['conformal'][rule][alpha]['populations'][split]
                rows.append(['全局' if rule=='global' else '分组',round(100*(1-float(alpha))),label,
                    f(100*pop['coverage'],2),f(pop['mean_radius_m']),pop['unbounded_n']])
    out.append(table('偏移分布上的描述性覆盖与半径，不赋予名义覆盖保证。',
               ['规则',r'名义/\%','总体',r'覆盖/\%','半径/m','无界数'],rows))
    out.append('Wilson区间描述固定本次校准规则后，IID测试抽样对覆盖概率估计的不确定性；'
      '它与对校准评分取平均的共形保证不同，也不是第二次独立校准实验。逐真实场景和多个水平的区间未校正多重比较。')
    return '\n\n'.join(out)+'\n'


def generate(root=ROOT,run=None,results=None,sections=None):
    root=Path(root);run=Path(run) if run is not None else root/'runs/revision_reference'
    results=Path(results) if results is not None else root/'results/revision_benchmark'
    for name in ('summary.json','status.json','protocol.json'):
        if not (results/name).is_file():
            raise ValueError('Formal completed revision results are required before report generation: '+name)
    summary=load(results/'summary.json');status=load(results/'status.json')
    if summary.get('status')!='complete' or status.get('status')!='complete':
        raise ValueError('Evaluation must finish before generating the report')
    if hashlib.sha256((results/'summary.json').read_bytes()).hexdigest()!=status.get('summary_sha256'):
        raise ValueError('Completed summary digest mismatch')
    if summary.get('protocol')!=load(results/'protocol.json'):
        raise ValueError('Saved protocol differs from summary')
    checked=validate_revision_freeze(root,run)
    if digest(checked['frozen'])!=digest(summary['frozen']):
        raise ValueError('Completed matching revision evidence is required')
    path=Path(sections) if sections is not None else root/'latex/sections'
    trace_text=trace(run,summary)
    text=render(summary)
    for name,label in LABELS.items():text=text.replace(name.replace('_',r'\_'),label)
    text=text.replace(r'\_',r'\_\allowbreak{}')
    results_text=text+'\n'+extra_results(summary)
    # Finish both render operations before replacing either previous artifact.
    path.mkdir(parents=True,exist_ok=True)
    for name,content in [('revision_trace.tex',trace_text),('revision_results.tex',results_text)]:
        destination=path/name;temporary=destination.with_suffix('.tex.tmp')
        temporary.write_text(content,encoding='utf8');temporary.replace(destination)
    return {'generated':['revision_trace.tex','revision_results.tex'],
       'actual_prediction_count':summary['prediction_count']}


def main():
    print(json.dumps(generate(),ensure_ascii=False))


if __name__=='__main__':main()
