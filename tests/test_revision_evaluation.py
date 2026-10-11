"""Independent fixture tests; never generate formal revision holdout cases."""
import copy
import contextlib
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import benchmark_revision
from revision.evaluation import (build_methods, compare_conformal, conformal_rank,
    generate_datasets, iid_mixture, json_safe, observed_group, paired_bootstrap,
    save_json, validate_revision_freeze, wilson_interval)
from revision.report import render
from verification.check_revision_results import audit as audit_saved_results

ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / 'research/revision_plan.json').read_text(encoding='utf-8'))


def row(cid, error, model='static', scene='static_clean', root=11, n=16, scale=1.):
    return {'case_id': cid, 'error_m': float(error), 'scenario': scene,
            'study_seed': root, 'observation_n': n,
            'diagnostics': {'selected_model': model, 'geometry_scale_m': scale}}


class PairingTests(unittest.TestCase):
    def test_aligns_cases_and_equal_weights_declared_cells(self):
        a = [row('a', 1), row('b', 2, root=12), row('c', 3, root=12)]
        b = [row('c', 8, root=12), row('a', 2), row('b', 7, root=12)]
        result = paired_bootstrap(a, b, ['study_seed', 'scenario'], repeats=100)
        self.assertEqual(result['mean_reduction_m'], 3.)
        np.testing.assert_allclose(result['ci95_m'], [3, 3])
        self.assertEqual(result, paired_bootstrap(list(reversed(a)), b, ['study_seed', 'scenario'], repeats=100))

    def test_rejects_unpaired_duplicate_and_mismatched_labels(self):
        a = [row('a', 1), row('b', 2)]
        invalid = [[row('a', 2)], [row('a', 2), row('a', 3)],
                   [row('a', 2), row('b', 3, scene='moving_clean')]]
        for b in invalid:
            with self.assertRaises(ValueError): paired_bootstrap(a, b, ['scenario'], repeats=10)

    def test_noninferiority_margin_is_not_a_superiority_test(self):
        a = [row(str(i), 1.05) for i in range(10)]
        b = [row(str(i), 1.) for i in range(10)]
        result = paired_bootstrap(a, b, ['scenario'], repeats=20, margin=1.1)
        self.assertLess(result['mean_reduction_m'], 0)
        self.assertTrue(result['point_meets_margin'])
        self.assertTrue(result['ci_upper_below_margin'])
        self.assertAlmostEqual(result['mean_error_ratio'], 1.05)


class CoverageTests(unittest.TestCase):
    def test_rank_is_n_plus_one_without_interpolation(self):
        self.assertEqual(conformal_rank(range(1, 20), .1), {'n': 19, 'rank': 18, 'q': 18., 'unbounded': False})
        self.assertEqual(conformal_rank(range(1, 20), .05)['q'], 19.)
        self.assertTrue(math.isinf(conformal_rank([], .1)['q']))
        self.assertTrue(math.isinf(conformal_rank([1, 2], .05)['q']))
        with self.assertRaises(ValueError): conformal_rank([math.nan], .1)

    def test_group_function_can_only_read_observation_diagnostics(self):
        class Guard(dict):
            def __getitem__(self, key):
                if key != 'diagnostics': raise AssertionError('Label access: ' + key)
                return {'selected_model': 'velocity'}
        self.assertEqual(observed_group(Guard()), 'velocity')
        self.assertEqual(observed_group(row('a', 0, 'dynamic')), 'velocity')
        with self.assertRaises(ValueError): observed_group(row('a', 0, 'unknown'))

    def test_mondrian_uses_group_calibration_and_global_uses_all(self):
        cal = [row('s' + str(i), i) for i in range(1, 20)] + [row('v' + str(i), 100 + i, 'velocity') for i in range(1, 20)]
        test = [row('s', 19), row('v', 110, 'velocity')]
        out = compare_conformal(cal, {'iid_test': test}, PLAN)
        q = out['mondrian']['0.1']['calibration']
        self.assertEqual(q['static']['q'], 18.)
        self.assertEqual(q['velocity']['q'], 118.)
        self.assertEqual(out['mondrian']['0.1']['populations']['iid_test']['coverage'], .5)
        self.assertEqual(out['global']['0.1']['populations']['iid_test']['coverage'], 1.)

    def test_empty_group_is_unbounded_not_null_or_finite_success(self):
        cal = [row(str(i), i) for i in range(1, 20)]
        test = [row('unseen', 1e9, 'velocity')]
        out = compare_conformal(cal, {'iid_test': test, 'shifted_stress': test}, PLAN)
        population = out['mondrian']['0.1']['populations']['iid_test']
        self.assertEqual(population['unbounded_n'], 1)
        self.assertEqual(population['coverage'], 1.)
        self.assertTrue(math.isinf(population['mean_radius_m']))
        self.assertTrue(all(p['accepted_n'] == 0 for p in population['rejection_curve']))
        serial = json_safe(out)
        self.assertEqual(serial['mondrian']['0.1']['calibration']['velocity']['q'], 'infinity')
        self.assertIsNone(out['mondrian']['0.1']['populations']['shifted_stress']['coverage_wilson95'])
        self.assertIsNotNone(population['coverage_wilson95'])

    def test_rejection_is_not_groupconditional_guarantee(self):
        cal = [row(str(i), i, scale=1.) for i in range(1, 20)]
        test = [row('small', 20, scale=.5), row('large', 30, scale=3.)]
        pop = compare_conformal(cal, {'iid_test': test}, PLAN)['global']['0.1']['populations']['iid_test']
        accepted = next(item for item in pop['rejection_curve'] if item['radius_threshold_m'] == 10)
        self.assertEqual(pop['coverage'], .5)
        self.assertEqual(accepted['accepted_coverage'], 0.)

    def test_wilson_boundary_and_symmetric_interval(self):
        low, high = wilson_interval(50, 100)
        self.assertAlmostEqual(low + high, 1.)
        self.assertLess(low, .5); self.assertGreater(high, .5)
        self.assertAlmostEqual(wilson_interval(0, 100)[0], 0.)
        self.assertAlmostEqual(wilson_interval(100, 100)[1], 1.)
        self.assertIsNone(wilson_interval(0, 0))


class DesignIntegrityTests(unittest.TestCase):
    @staticmethod
    def fake_case(seed, scenario, n=16):
        return SimpleNamespace(case_id=f'{scenario}-{seed}', scenario=scenario,
                               meta={'seed': seed}, times=np.zeros(n))

    def test_all_predeclared_counts_and_unique_ids_without_physical_generation(self):
        # Only dummy namespaces are produced, not the hidden synthetic observations.
        def suite(seed, per, scenes):
            return [self.fake_case(seed * 100 + i, s, 8 if s == 'sparse' else 16)
                    for s in scenes for i in range(per)]
        with patch('revision.evaluation.generate_case', side_effect=self.fake_case), patch('revision.evaluation.generate_suite', side_effect=suite):
            datasets = generate_datasets(PLAN)
        self.assertEqual({k: len(v) for k, v in datasets.items()},
                         {'main': 720, 'stress': 400, 'edge': 420, 'calibration': 800, 'coverage': 1200})
        ids = [c.case_id for cases in datasets.values() for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for n in range(6, 13):
            for scene in ('static_clean', 'moving_clean'):
                self.assertEqual(sum(len(c.times) == n and c.scenario == scene for c in datasets['edge']), 30)

    def test_iid_mixture_is_reproducible_random_counts_not_stratified_quotas(self):
        with patch('revision.evaluation.generate_case', side_effect=self.fake_case):
            a = iid_mixture(773013, 37, PLAN['calibration']['scenarios'], 'fixture')
            b = iid_mixture(773013, 37, PLAN['calibration']['scenarios'], 'fixture')
        self.assertEqual([(c.case_id, c.scenario) for c in a], [(c.case_id, c.scenario) for c in b])
        counts = [sum(c.scenario == s for c in a) for s in PLAN['calibration']['scenarios']]
        self.assertGreater(max(counts) - min(counts), 1)

    def test_exact_effective_aliases_and_baseline_shorthand_are_explicit(self):
        methods, aliases, changes = build_methods(PLAN['methods']['v2_frozen_spec'], PLAN)
        self.assertEqual(aliases['revision_selected'], 'v2_frozen')
        self.assertEqual(aliases['ablate_k3'], 'v2_frozen')
        self.assertNotIn('ablate_k4', aliases)
        methods, aliases, changes = build_methods({'baseline': 'dynamic_robust'}, PLAN)
        self.assertEqual(aliases['revision_selected'], 'dynamic_robust')
        self.assertEqual(changes, {})
        self.assertFalse(any(k.startswith('ablate_') for k in methods))

    def test_unfrozen_entrypoint_never_calls_generation_or_creates_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result'
            with patch('benchmark_revision.validate_revision_freeze', side_effect=ValueError('not frozen')), patch('benchmark_revision.generate_datasets') as generate:
                with self.assertRaises(ValueError): benchmark_revision.run_benchmark(directory, directory, output)
            generate.assert_not_called()
            self.assertFalse(output.exists())

    def test_revision_hashes_are_required_in_addition_to_original_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run = root / 'run'
            save_json(root / 'research/revision_plan.json', PLAN)
            save_json(run / 'protocol.json', {'source_hashes': {}})
            with patch('revision.evaluation.validate_frozen_run', return_value={}):
                with self.assertRaisesRegex(ValueError, 'omitted required hashes'):
                    validate_revision_freeze(root, run)

    def test_nonfinite_serialization_never_silently_turns_into_null(self):
        self.assertEqual(json_safe({'q': math.inf}), {'q': 'infinity'})
        with self.assertRaises(ValueError): json_safe({'q': math.nan})

    def test_complete_pipeline_on_tiny_unrelated_fixture_then_real_result_render(self):
        # Deliberately replace every formal seed and count. This tests file
        # integration and true API behaviour, never the preregistered holdout.
        plan = copy.deepcopy(PLAN)
        plan['main'].update(seeds=[772013, 772019], per_scenario_per_seed=1, expected_n=8)
        plan['stress'].update(seed=772021, per_scenario=1, expected_n=5)
        plan['edge'].update(seed=772023, observation_n=[6, 8], per_cell=1, expected_n=4)
        plan['calibration'].update(seed=772027, size=20)
        plan['coverage'].update(seed=772029, size=24)
        plan['statistics']['bootstrap_repetitions'] = 20
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run = root / 'fixture_run'; output = root / 'result'
            frozen = {'method_id': 'fixture_only', 'spec': plan['methods']['v2_frozen_spec']}
            save_json(run / 'tools/frozen_method.json', frozen)
            checked = {'plan': plan, 'frozen': frozen, 'audit': {'fixture': True}, 'revision_provenance': {'fixture': True}}
            with patch('benchmark_revision.validate_revision_freeze', return_value=checked), contextlib.redirect_stdout(io.StringIO()):
                result = benchmark_revision.run_benchmark(root, run, output)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['case_counts'], {'main': 8, 'stress': 5, 'edge': 4, 'calibration': 20, 'coverage': 24})
            self.assertEqual(result['prediction_count'], result['unique_method_count'] * 17 + 44)
            saved = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['prediction_count'], result['prediction_count'])
            self.assertTrue((output / 'predictions.csv').is_file())
            self.assertIn(r'\begin{table}[H]', render(saved))
            self.assertIn('fixture\\_only', render(saved))
            audit = audit_saved_results(output)
            self.assertTrue(audit['ok'], audit['errors'][:5])
            self.assertEqual(audit['physical_prediction_errors_recomputed'], result['prediction_count'])
            with self.assertRaises(FileExistsError): benchmark_revision.run_benchmark(root, run, output)


if __name__ == '__main__': unittest.main()
