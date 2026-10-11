"""Adversarial application-quality probes; no model calls or heldout tuning."""
import copy
import json
from pathlib import Path
import unittest

import numpy as np

from application_safety import numerical_quality
from localization import fit
from localization.model import Observations


SPEC = {'model': 'cv_select', 'cv_folds': 4, 'starts': 1, 'consensus_trials': 0,
        'residual': 'range', 'scale': 'reported', 'loss': 'soft_l1', 'f_scale': 1.5,
        'ridge': .05, 'gate_margin': .05, 'trim_fraction': 0.}


def noiseless_fixture(offset=None):
    anchors = np.array([[-20., -8.], [3., 19.], [25., 5.], [-12., 17.],
                        [11., -21.], [-28., 13.], [21., 23.], [-5., -28.]])
    times = np.array([-30., -25., -20., -15., -10., -6., -3., 0.])
    truth, velocity = np.array([2., -3.]), np.array([.6, -.45])
    ranges = np.linalg.norm(truth + times[:, None] * velocity - anchors, axis=1)
    return Observations(anchors + (np.zeros(2) if offset is None else offset), times, ranges, np.ones(8))


class ApplicationQualityTests(unittest.TestCase):
    def setUp(self):
        self.obs = noiseless_fixture()
        self.result = fit(self.obs, SPEC)

    def quality(self, result=None, fit_function=fit, **kwargs):
        output = numerical_quality(self.obs, self.result if result is None else result, SPEC,
                                   fit_function=fit_function, **kwargs)
        json.dumps(output, allow_nan=False)
        return output

    @staticmethod
    def codes(quality):
        return {reason['code'] for reason in quality['reasons']}

    def test_normal_local_fixture_passes_without_mutation(self):
        before = copy.deepcopy(self.result)
        output = self.quality()
        self.assertTrue(output['passed'], output)
        self.assertEqual(self.result, before)
        self.assertEqual(output['guard_computation']['additional_fits'], 1)
        self.assertGreater(output['guard_computation']['additional_work_units'], 0)
        self.assertFalse(output['diagnostics']['translation_check']['point_estimate_replaced'])
        self.assertFalse(output['coverage_guarantee_asserted'])

    def test_large_coordinate_origin_fails_actual_translation_screen(self):
        obs = noiseless_fixture(np.array([500000., 4000000.]))
        original = fit(obs, SPEC)
        before = copy.deepcopy(original)
        output = numerical_quality(obs, original, SPEC)
        self.assertFalse(output['passed'])
        self.assertIn('translation_position_changed', self.codes(output))
        self.assertGreater(output['diagnostics']['translation_check']['position_change_m'], 1.)
        self.assertEqual(original, before)
        json.dumps(output, allow_nan=False)

    def test_near_collinear_small_proxy_cannot_pass_quality(self):
        anchors = np.array([[10., 0.], [20., .0001], [30., -.0001]])
        obs = Observations(anchors, np.array([-3., -2., -1.]), np.linalg.norm(anchors, axis=1), np.ones(3))
        result = fit(obs, SPEC)
        self.assertLess(result['diagnostics']['geometry_scale_m'], 1.)
        output = numerical_quality(obs, result, SPEC)
        self.assertFalse(output['passed'])
        self.assertIn('original_weak_geometry', self.codes(output))

    def test_exact_rank_deficiency_is_rejected(self):
        anchors = np.array([[10., 0.], [20., 0.], [30., 0.]])
        obs = Observations(anchors, np.array([-3., -2., -1.]), np.linalg.norm(anchors, axis=1), np.ones(3))
        output = numerical_quality(obs, fit(obs, SPEC), SPEC)
        self.assertFalse(output['passed'])
        self.assertIn('original_rank_deficient', self.codes(output))

    def test_failed_bound_and_missing_optimizer_metadata_fail_closed(self):
        cases = [('solver_success', False, 'original_solver_failed'),
                 ('at_numerical_bound', True, 'original_active_bound'),
                 ('solver_success', None, 'original_solver_status_unknown'),
                 ('at_numerical_bound', None, 'original_bound_status_unknown'),
                 ('ill_conditioned', True, 'original_reported_ill_conditioning')]
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result)
                if value is None:
                    result['diagnostics'].pop(key)
                else:
                    result['diagnostics'][key] = value
                output = self.quality(result)
                self.assertFalse(output['passed'])
                self.assertIn(expected, self.codes(output))

    def test_missing_geometry_metadata_is_independently_recomputed(self):
        result = copy.deepcopy(self.result)
        for key in ('jacobian_rank', 'jacobian_singular_values', 'jacobian_condition', 'ill_conditioned'):
            result['diagnostics'].pop(key)
        output = self.quality(result)
        self.assertTrue(output['passed'], output)
        self.assertEqual(output['diagnostics']['original']['jacobian_rank'], 4)

    def test_alternate_solver_failure_or_exception_cannot_pass(self):
        def failed(obs, spec):
            result = fit(obs, spec)
            result['diagnostics']['solver_success'] = False
            return result
        output = self.quality(fit_function=failed)
        self.assertIn('centered_solver_failed', self.codes(output))
        def raises(obs, spec):
            raise RuntimeError('probe')
        output = self.quality(fit_function=raises)
        self.assertFalse(output['passed'])
        self.assertIn('quality_check_failed', self.codes(output))
        self.assertEqual(output['guard_computation']['additional_fits'], 1)

    def test_model_change_is_rejected_independently_of_position_change(self):
        def static_result(obs, spec):
            result = fit(obs, spec)
            result['selected_model'] = 'static'
            result['diagnostics']['selected_model'] = 'static'
            result['diagnostics'].pop('jacobian_rank')
            result['diagnostics'].pop('jacobian_singular_values')
            return result
        output = self.quality(fit_function=static_result)
        self.assertFalse(output['passed'])
        self.assertIn('translation_model_changed', self.codes(output))

    def test_randomized_absolute_observation_hash_is_not_claimed_translation_invariant(self):
        output = numerical_quality(self.obs, self.result, {**SPEC, 'consensus_trials': 1})
        self.assertFalse(output['passed'])
        self.assertIn('translation_screen_unsupported', self.codes(output))
        self.assertEqual(output['guard_computation']['additional_fits'], 0)

    def test_nonfinite_predictions_and_finite_overflow_fail_closed(self):
        for vector in ([float('nan'), 0.], [float('inf'), 0.], [1e309, 0.], [True, 0.]):
            result = copy.deepcopy(self.result)
            result['position'] = vector
            self.assertFalse(self.quality(result)['passed'])
        obs = Observations(np.array([[1e308, 0.], [-1e308, 1.], [0., 1.]]),
                           np.array([-3., -2., -1.]), np.ones(3), np.ones(3))
        output = numerical_quality(obs, self.result, SPEC)
        self.assertFalse(output['passed'])
        json.dumps(output, allow_nan=False)

    def test_nonfinite_or_cleaned_unavailable_cost_cannot_pass(self):
        for value in (float('nan'), float('inf'), None, -1.):
            with self.subTest(value=value):
                result = copy.deepcopy(self.result)
                result['diagnostics']['fit_cost'] = value
                output = self.quality(result)
                self.assertFalse(output['passed'])
                self.assertIn('original_nonfinite_numerical_diagnostics', self.codes(output))

    def test_contradictory_solver_termination_code_cannot_pass(self):
        result = copy.deepcopy(self.result)
        result['diagnostics']['solver_status'] = 0
        output = self.quality(result)
        self.assertFalse(output['passed'])
        self.assertIn('original_invalid_solver_status', self.codes(output))

    def test_packaged_example_gets_an_explicit_actual_decision(self):
        from locate import observations_from_dict
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / 'examples/observations.json').read_text(encoding='utf-8'))
        obs = observations_from_dict(data)
        output = numerical_quality(obs, fit(obs, SPEC), SPEC)
        self.assertTrue(output['passed'], output)


if __name__ == '__main__':
    unittest.main()
