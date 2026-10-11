"""Research tool registry. Language models choose actions; tools compute evidence."""
from pathlib import Path
import time, re
import numpy as np
from localization import generate_suite,fit,Case
from .common import clean,save,load,digest,metrics
from .mathematics import verify

CORE=['static_clean','static_outliers','moving_clean','moving_outliers']
BASELINES={
 'static_wls':{'baseline':'static_wls'},
 'static_robust':{'baseline':'static_robust'},
 'dynamic_robust':{'baseline':'dynamic_robust'},
 'legacy_bic':{'baseline':'bic_select'},
}
TOOL_SPEC={
 'read_sources':{'arguments':{},'description':'Read source problem, scientific context, assumptions and exact implementation notes. No experiments.'},
 'register_method':{'arguments':{'method_id':'new stable lowercase id','hypothesis':'falsifiable research hypothesis',
     'mathematical_definition':'describe the objective/model/selection mathematically','spec':
     {'model':'static|velocity|cv_select','residual':'range|log_range','scale':'reported|constant',
      'loss':'linear|soft_l1|huber|cauchy','f_scale':1.5,'ridge':0.05,'trim_fraction':0.0,
      'consensus_trials':0,'gate_margin':0.05,'log_bias_correction':True,'log_sd':0.1,'starts':3}},
     'description':'Create a compositional solver method (not pick a fixed candidate list). IDs immutable. Mechanisms may be combined; all numeric computation is in registered solver modules.'},
 'run_experiment':{'arguments':{'method_ids':['static_wls','new_method'],'split':'discovery|validation'},
     'description':'Evaluate at most 4 methods on common instances. Discovery 4x16; validation 4x24, at most 4 tool calls. Returns scenario errors, mean, tails, costs and evidence paths. Truth is available only to evaluator, never to solver.'},
 'diagnose':{'arguments':{'method_id':'id','split':'discovery','top_k':4},
     'description':'Inspect failure cases, static/dynamic selection frequencies, observed residual/geometry diagnostics and comparative baselines. Must first run the experiment.'},
 'verify_math':{'arguments':{'proposition':'all|self_weight_bias|mirror|conformal'},
     'description':'Execute independent witnesses and inspect exact proposition assumptions. Does not turn numerical evidence into a proof.'},
 'ablate':{'arguments':{'method_id':'id','changes':{'trim_fraction':0},'new_id':'id_no_trim','split':'discovery'},
     'description':'Register one controlled component change and execute paired discovery experiment; explicitly tests the role of a component.'},
 'freeze':{'arguments':{'method_id':'id','scientific_conclusion':'supported results and unresolved failures'},
     'description':'Freeze one registered and validation-tested solver before test/calibration access. Requires 3 experiment calls, 2 new methods, a diagnosis and a mathematical check. Rejects future development edits.'},
 'finish':{'arguments':{'summary':'reason for stopping'},'description':'Stop only after freeze; no fabricated success.'}
}

def evaluate(cases,spec):
    rows=[]
    for c in cases:
        started=time.perf_counter();r=fit(c,spec)
        p=np.array(r['position'],float)
        if p.shape!=(2,) or not np.all(np.isfinite(p)):raise ValueError('Nonfinite/invalid estimate')
        diagnostics=dict(r.get('diagnostics',{}))
        diagnostics['selected_model']=r.get('selected_model',diagnostics.get('selected_model','unknown'))
        rows.append({'case_id':c.case_id,'scenario':c.scenario,'position':p.tolist(),
          'error_m':float(np.linalg.norm(p-c.truth)),
          'velocity':r.get('velocity',[0,0]),'diagnostics':diagnostics,
          'work_units':int(r.get('work_units',r.get('nfev',0))),
          'elapsed_seconds':time.perf_counter()-started})
    by={s:metrics([r for r in rows if r['scenario']==s]) for s in sorted({r['scenario'] for r in rows})}
    return {'overall':metrics(rows),'by_scenario':by,'macro_mean_m':float(np.mean([r['mean_m'] for r in by.values()])),
            'rows':rows}

class ResearchToolkit:
    def __init__(self,root,output,resume=False):
        self.root=Path(root);self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.path=self.output/'tool_state.json'
        if resume and self.path.exists():self.state=load(self.path)
        else:
            self.state={'registry':{k:{'spec':v,'hypothesis':'Fixed predeclared baseline','mathematical_definition':'See localization/legacy_model.py'} for k,v in BASELINES.items()},
                        'evaluations':{},'experiment_calls':0,'validation_calls':0,'diagnoses':0,'math_checks':0,'frozen':False}
            self.persist()
        self.cases={}
    def persist(self):save(self.path,self.state)
    def catalog(self):return clean({'methods':self.state['registry'],'counts':{k:self.state[k] for k in ['experiment_calls','validation_calls','diagnoses','math_checks','frozen']}})
    def cases_for(self,split):
        if split not in ('discovery','validation'):raise ValueError('Agent cannot access test/calibration splits')
        if split not in self.cases:
            self.cases[split]=generate_suite(46191 if split=='discovery' else 57383,16 if split=='discovery' else 24,CORE)
            save(self.output/f'{split}_inputs.json',[c.to_dict() for c in self.cases[split]])
        return self.cases[split]
    def dispatch(self,action,args):
        if self.state['frozen'] and action!='finish':raise ValueError('Research frozen; development tools disabled')
        handler=getattr(self,'tool_'+action,None)
        if handler is None:raise ValueError('Unknown registered action')
        result=handler(**args);self.persist();return clean(result)
    def tool_read_sources(self):
        files=['docs/research_context.md','localization/method_math.md']
        return {'sources':{p:(self.root/p).read_text(encoding='utf-8')[:20000] for p in files if (self.root/p).exists()},
                'scope':'Original source HarmonyOS problem 1, local reference context. Synthetic observations only. No claim of industrial acceptance.'}
    def tool_register_method(self,method_id,hypothesis,mathematical_definition,spec):
        if not re.fullmatch('[a-z][a-z0-9_]{2,45}',method_id):raise ValueError('Invalid method id')
        if method_id in self.state['registry']:raise ValueError('Method IDs immutable; register a new version')
        if len(self.state['registry'])>=20:raise ValueError('At most 16 new methods per study')
        if 'baseline' in spec:raise ValueError('New methods must declare an actual solver composition')
        # A tiny unrelated input probe catches API errors, not method performance.
        probe=generate_suite(901971,1,['static_clean'])[0];r=fit(probe,spec)
        self.state['registry'][method_id]={'spec':spec,'hypothesis':hypothesis,'mathematical_definition':mathematical_definition}
        save(self.output/'methods'/f'{method_id}.json',self.state['registry'][method_id])
        return {'registered':method_id,'spec':spec,'probe':'API validation only; not included in research metrics'}
    def tool_run_experiment(self,method_ids,split='discovery'):
        if not 1<=len(method_ids)<=4:raise ValueError('Evaluate 1..4 methods per experiment')
        if split not in ('discovery','validation'):raise ValueError('Agent cannot access test/calibration splits')
        if len(set(method_ids))!=len(method_ids):raise ValueError('Method ids must be unique within an experiment')
        if any(name not in self.state['registry'] for name in method_ids):raise ValueError('Unknown method id')
        if split=='validation':
            if self.state['validation_calls']>=4:raise ValueError('Validation call budget exhausted')
            self.state['validation_calls']+=1
        cases=self.cases_for(split);self.state['experiment_calls']+=1
        call_number=self.state['experiment_calls']
        # Reserve the attempt before computation so a later failure/restart
        # cannot recycle its evidence paths or recover spent validation budget.
        self.persist()
        summaries={}
        for name in method_ids:
            result=evaluate(cases,self.state['registry'][name]['spec'])
            relative=f'experiments/call_{call_number:03d}_{split}_{name}.json'
            if (self.output/relative).exists():raise FileExistsError('Refuse to overwrite immutable experiment evidence')
            save(self.output/relative,result)
            entry={k:v for k,v in result.items() if k!='rows'}
            entry.update(evidence_path=relative,evidence_sha256=digest(result))
            self.state['evaluations'][f'{split}:{name}']=entry;summaries[name]=entry
        return {'split':split,'metrics':summaries,'note':'Lower position error is better; improvement must be checked per scenario and against compute cost.'}
    def tool_diagnose(self,method_id,split='discovery',top_k=4):
        key=f'{split}:{method_id}'
        if key not in self.state['evaluations']:raise ValueError('Run requested method/split first')
        result=load(self.output/self.state['evaluations'][key]['evidence_path'])
        ordered=sorted(result['rows'],key=lambda r:r['error_m'],reverse=True)
        frequencies={}
        for row in result['rows']:
            k=row['scenario'];model=row['diagnostics'].get('selected_model','unknown')
            frequencies.setdefault(k,{});frequencies[k][model]=frequencies[k].get(model,0)+1
        self.state['diagnoses']+=1
        return {'worst_cases':ordered[:min(int(top_k),6)],'selection_frequencies':frequencies,
                'warning':'Worst-case examples are diagnostic evidence, not theorems or guarantees about other cases.'}
    def tool_verify_math(self,proposition='all'):
        result=verify(proposition);self.state['math_checks']+=1
        save(self.output/'mathematical_checks'/f'{proposition}.json',result);return result
    def tool_ablate(self,method_id,changes,new_id,split='discovery'):
        if split!='discovery':raise ValueError('Ablations during research use discovery split')
        parent=self.state['registry'][method_id];spec={**parent['spec'],**changes}
        self.tool_register_method(new_id,f'Ablation of {method_id}: {changes}',parent['mathematical_definition'],spec)
        result=self.tool_run_experiment([method_id,new_id],split)
        result['controlled_changes']=changes;return result
    def tool_freeze(self,method_id,scientific_conclusion):
        if self.state['experiment_calls']<3 or len(self.state['registry'])<6 or self.state['diagnoses']<1 or self.state['math_checks']<1:
            raise ValueError('Need >=3 experiments, >=2 new methods, a diagnosis and a mathematical check')
        if f'validation:{method_id}' not in self.state['evaluations']:raise ValueError('Candidate must have validation evidence before freezing')
        self.state['frozen']=True;self.state['selected_method']=method_id
        frozen={'method_id':method_id,**self.state['registry'][method_id],
                'validation':self.state['evaluations'][f'validation:{method_id}'],
                'scientific_conclusion':scientific_conclusion,
                'frozen_before_test':True,'registry_sha256':digest(self.state['registry'])}
        save(self.output/'frozen_method.json',frozen);return frozen
    def tool_finish(self,summary):
        if not self.state['frozen']:raise ValueError('Freeze a scientifically evaluated method first')
        return {'complete':True,'summary':summary}
