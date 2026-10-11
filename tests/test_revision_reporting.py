"""Report-layer contracts using saved history and deliberately small display fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reporting.revision_report import extra_results, f, generate, trace

ROOT=Path(__file__).resolve().parents[1]


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')


def display_fixture():
    metric={'mean_m':1.234,'p90_m':2.1,'p95_m':3.2,'max_m':9.1,
            'mean_work_units':123.4,'mean_seconds':.012345}
    methods=['static_wls','dynamic_robust','legacy_bic','v2_frozen','revision_selected']
    metrics={name:{split:{'overall':dict(metric),'by_scenario':{'static_clean':dict(metric)},
        'by_edge_group':{f'n{n}:{scene}':dict(metric) for n in [6,8] for scene in ['static_clean','moving_clean']}}
        for split in ['main','stress','edge']} for name in methods}
    full={'n':1,'covered_n':1,'coverage':1.,'coverage_wilson95':[.2,1.],
          'mean_radius_m':'infinity','unbounded_n':1}
    empty={'n':0,'covered_n':0,'coverage':None,'coverage_wilson95':None,
           'mean_radius_m':None,'unbounded_n':0}
    population={**full,'by_scenario':{'static_clean':dict(full)},
                'by_group':{'static':dict(empty),'velocity':dict(full)}}
    result={'metrics':metrics,'protocol':{'plan':{'edge':{'observation_n':[6,8],'per_cell':1}}},
            'per_seed':{name:{'fixture':dict(metric)} for name in methods},'conformal':{}}
    for rule in ['global','mondrian']:
        result['conformal'][rule]={}
        for alpha in ['0.1','0.05']:
            result['conformal'][rule][alpha]={'calibration':
                {'global':{'n':0,'rank':1,'q':'infinity','unbounded':True}} if rule=='global' else
                {'static':{'n':19,'rank':18,'q':2.,'unbounded':False},
                 'velocity':{'n':0,'rank':1,'q':'infinity','unbounded':True}},
                'populations':{split:copy.deepcopy(population) for split in ['iid_test','shifted_stress','edge']}}
    return result


class ReportingTests(unittest.TestCase):
    def test_infinite_missing_group_cost_units_and_bounded_tables_render(self):
        result=extra_results(display_fixture())
        self.assertIn(r'$\infty$',result)
        self.assertNotIn('infinity',result)
        self.assertIn('输出静态',result)
        self.assertIn('输出恒速',result)
        self.assertIn('12.35',result)  # .012345 seconds correctly displayed as milliseconds.
        self.assertIn('123.4',result)  # Solver work field remains separately labelled.
        self.assertIn(r'\begin{tabularx}{\linewidth}',result)
        self.assertIn('每格1例',result)
        self.assertIn('xtick={6,8}',result)
        self.assertEqual(f(None),'--')
        with self.assertRaises(ValueError): f(float('nan'))

    def test_real_completed_history_schema_renders_and_replay_is_not_rebranded(self):
        # The preserved v2 history supplies real controller field shapes. A
        # temporary registry adapter only identifies its candidate IDs for this
        # rendering test; no persisted scientific record is edited or relabelled.
        original=ROOT/'runs/live_reference'
        state=json.loads((original/'state.json').read_text(encoding='utf8'))
        tool=json.loads((original/'tools/tool_state.json').read_text(encoding='utf8'))
        tool['new_method_ids']=list(tool['registry'])[4:]
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)
            save(run/'state.json',state);save(run/'tools/tool_state.json',tool)
            (run/'events.jsonl').write_bytes((original/'events.jsonl').read_bytes())
            result=trace(run,{'frozen':state['freeze_result']})
            self.assertIn(str(state['model_calls'])+'次模型请求',result)
            self.assertIn(r'\_\allowbreak{}',result)
            self.assertIn(r'\dimexpr\linewidth',result)
            self.assertIn('这不是私有思维链',result)
            self.assertIn('下一步建议',result)
            self.assertNotIn('"recommended\\_next\\_step"',result)
            for observation in state['observations']:
                self.assertIn(str(observation['step'])+' & ',result)
            state['is_replay']=True;save(run/'state.json',state)
            self.assertIn('明确标为回放',trace(run,{'frozen':state['freeze_result']}))
            state['frozen']=False;state['status']='running';save(run/'state.json',state)
            with self.assertRaises(ValueError):trace(run,{'frozen':state['freeze_result']})

    def test_missing_or_incomplete_results_never_overwrite_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sections=root/'latex/sections';sections.mkdir(parents=True)
            old=sections/'revision_trace.tex';old.write_text('keep previous',encoding='utf8')
            with patch('reporting.revision_report.validate_revision_freeze') as validate:
                with self.assertRaises(ValueError):generate(root)
                validate.assert_not_called()
            results=root/'results/revision_benchmark'
            save(results/'summary.json',{'status':'complete'})
            save(results/'status.json',{'status':'running'})
            save(results/'protocol.json',{})
            with self.assertRaises(ValueError):generate(root)
            self.assertEqual(old.read_text(encoding='utf8'),'keep previous')

    def test_completed_summary_digest_is_required_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);results=root/'results/revision_benchmark'
            save(results/'summary.json',{'status':'complete'})
            save(results/'status.json',{'status':'complete','summary_sha256':'not-the-hash'})
            save(results/'protocol.json',{})
            with patch('reporting.revision_report.validate_revision_freeze') as validate:
                with self.assertRaisesRegex(ValueError,'digest mismatch'):generate(root)
                validate.assert_not_called()


if __name__=='__main__':unittest.main()
