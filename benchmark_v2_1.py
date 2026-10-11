"""Pre-freeze evaluator amendment: preserve baseline shorthand without invalid ablations.

The original benchmark.py remains unchanged as the initialization-version
snapshot and shared statistics/calibration/integrity library. This file is the
single designated entry point for the first formal holdout evaluation.
"""
from pathlib import Path
import argparse,csv,hashlib,platform,time,sys
import numpy as np
import scipy

from benchmark import (STRESS,CORE,BASELINES,evaluate,generate_suite,paired_ci,
                       iid_mixture,conformal,validate_frozen_run)
from tools.common import save,load,digest


def build_methods(chosen,registry):
    """Same predeclared comparisons; component ablation only for compositions."""
    methods={**BASELINES,'llm_selected':chosen}
    ablations=[]
    def ablate(name,change,condition=True):
        if 'baseline' not in chosen and condition:
            methods[name]={**chosen,**change};ablations.append({'method':name,'change':change})
    ablate('ablate_range',{'residual':'range'},chosen.get('residual','range')!='range')
    ablate('ablate_reported_scale',{'scale':'reported'},chosen.get('scale','reported')!='reported')
    ablate('ablate_no_trim',{'trim_fraction':0.},chosen.get('trim_fraction',0)>0)
    ablate('ablate_no_consensus',{'consensus_trials':0},chosen.get('consensus_trials',0)>0)
    ablate('ablate_no_log_correction',{'log_bias_correction':False},chosen.get('residual')=='log_range' and chosen.get('log_bias_correction',True))
    ablate('ablate_force_velocity',{'model':'velocity'},chosen.get('model')=='cv_select')
    ablate('ablate_force_static',{'model':'static'},chosen.get('model')!='static')
    # Unchanged predecessor policy: first two LLM-defined registry entries.
    for name,record in list(registry.items())[4:6]:
        if digest(record['spec'])!=digest(chosen):methods['predecessor_'+name]=record['spec']
    return methods,ablations


def amendment_provenance(root,run):
    root,run=Path(root),Path(run)
    original=hashlib.sha256((root/'benchmark.py').read_bytes()).hexdigest()
    amended=hashlib.sha256((root/'benchmark_v2_1.py').read_bytes()).hexdigest()
    manifest=load(root/'research'/'evaluator_amendment_manifest.json')
    if original!=manifest['original_benchmark_sha256'] or amended!=manifest['amended_evaluator_sha256']:
        raise ValueError('Evaluator differs from the documented pre-freeze amendment; preserve versions rather than rewriting study hashes.')
    if hashlib.sha256((root/'research'/'evaluator_v2_1.patch').read_bytes()).hexdigest()!=manifest['exact_diff_sha256']:
        raise ValueError('Evaluator amendment diff differs from its manifest')
    return {'evaluator_version':'v2.1-baseline-shorthand-guard',
            'original_benchmark_sha256':original,'evaluator_source_sha256':amended,
            'research_initialization_manifest':load(run/'protocol.json'),
            'research_initialization_manifest_sha256':hashlib.sha256((run/'protocol.json').read_bytes()).hexdigest(),
            'amendment':manifest,
            'evaluation_policy':'Use v2.1 for the first formal holdout; do not evaluate both versions and select favourable results.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=Path('runs/live_reference'))
    parser.add_argument('--output',type=Path,default=Path('results/benchmark'))
    parser.add_argument('--main-per-scenario',type=int,default=30)
    parser.add_argument('--stress-per-scenario',type=int,default=40)
    parser.add_argument('--calibration-size',type=int,default=240)
    parser.add_argument('--coverage-size',type=int,default=320)
    args=parser.parse_args()
    frozen_path=args.run/'tools'/'frozen_method.json'
    if not frozen_path.exists():parser.error('No frozen scientific method; run the real agent first')
    if args.output.exists():parser.error('Choose a new output directory')
    root=Path(__file__).resolve().parent
    checked=validate_frozen_run(root,args.run)
    provenance=amendment_provenance(root,args.run)
    args.output.mkdir(parents=True);started=time.perf_counter()
    frozen=checked['frozen'];registry=checked['registry'];chosen=frozen['spec']
    methods,ablations=build_methods(chosen,registry)
    codefiles=(sorted((root/'localization').glob('*.py'))+sorted((root/'tools').glob('*.py'))+
               sorted((root/'agent_runtime').glob('*.py'))+
               [root/'run_agent.py',root/'benchmark.py',Path(__file__).resolve()])
    protocol={'version':'v2.1-independent-benchmark','main_seeds':[814711,826013,837901],
      'stress_seed':849391,'calibration_seed':862193,'coverage_seed':875149,
      'main_per_scenario_per_seed':args.main_per_scenario,'stress_per_scenario':args.stress_per_scenario,
      'calibration_size':args.calibration_size,'coverage_size':args.coverage_size,'methods':methods,'ablations':ablations,
      'frozen_method_sha256':hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
      'pre_holdout_integrity_check':checked['audit'],'evaluator_provenance':provenance,
      'ablation_policy':'No compositional ablations for baseline shorthand; unchanged component ablations otherwise.',
      'source_sha256':{str(f.relative_to(root)).replace('\\','/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in codefiles},
      'environment':{'python':sys.version.split()[0],'numpy':np.__version__,'scipy':scipy.__version__,'platform':platform.platform()},
      'prohibition':'No test-driven revisions to the frozen method; report failures without retuning.'}
    save(args.output/'protocol.json',protocol)
    maincases=[]
    for seed in protocol['main_seeds']:maincases.extend(generate_suite(seed,args.main_per_scenario,CORE))
    stresscases=generate_suite(protocol['stress_seed'],args.stress_per_scenario,STRESS)
    save(args.output/'main_inputs.json',[c.to_dict() for c in maincases]);save(args.output/'stress_inputs.json',[c.to_dict() for c in stresscases])
    raw={};summary={};csvrows=[]
    for name,spec in methods.items():
        print(f'Evaluate {name}',flush=True);summary[name]={}
        for split,cases in [('main',maincases),('stress',stresscases)]:
            result=evaluate(cases,spec);raw[(name,split)]=result;save(args.output/f'{split}_{name}.json',result)
            summary[name][split]={k:v for k,v in result.items() if k!='rows'}
            for r in result['rows']:
                csvrows.append({'method':name,'split':split,'case_id':r['case_id'],'scenario':r['scenario'],'error_m':r['error_m'],
                    'x':r['position'][0],'y':r['position'][1],'work_units':r['work_units'],'elapsed_seconds':r['elapsed_seconds']})
    comparisons={name:paired_ci(raw[('llm_selected','main')]['rows'],raw[(name,'main')]['rows']) for name in methods if name!='llm_selected'}
    print('Independent iid mixture calibration and coverage evaluation',flush=True)
    calcases=iid_mixture(protocol['calibration_seed'],args.calibration_size);covercases=iid_mixture(protocol['coverage_seed'],args.coverage_size)
    save(args.output/'calibration_inputs.json',[c.to_dict() for c in calcases]);save(args.output/'coverage_inputs.json',[c.to_dict() for c in covercases])
    cal=evaluate(calcases,chosen);cover=evaluate(covercases,chosen)
    save(args.output/'calibration_predictions.json',cal);save(args.output/'coverage_predictions.json',cover)
    conf=conformal(cal['rows'],cover['rows'],raw[('llm_selected','stress')]['rows']);save(args.output/'conformal.json',conf)
    output={'protocol':protocol,'frozen':frozen,'metrics':summary,'comparisons':comparisons,'conformal':conf,
      'wall_seconds':time.perf_counter()-started,'prediction_count':len(csvrows)+len(calcases)+len(covercases)}
    save(args.output/'summary.json',output)
    with (args.output/'predictions.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csvrows[0]));writer.writeheader();writer.writerows(csvrows)
    print(f'Complete: {output["prediction_count"]} predictions; {output["wall_seconds"]:.1f}s',flush=True)


if __name__=='__main__':main()
