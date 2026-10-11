"""Revision application tests: temporary evidence fixtures, no model or fresh data."""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from locate_revision import predict, region_from_files


OBS = {'anchors': [[0., 0.], [6., 0.], [0., 8.]], 'times': [-3., -2., -1.],
       'ranges': [3.61, 5., 5.39], 'sigma': [.8, .8, .8]}


class RevisionApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='revision-application-fixture-')
        self.root = Path(self.temp.name)
        self.run = self.root/'run'
        (self.run/'tools').mkdir(parents=True)
        self.input = self.root/'observations.json'
        self.write(self.input, OBS)
        self.frozen = {'method_id': 'fixture_revision', 'spec': {'model': 'static', 'starts': 1},
                       'scientific_conclusion': 'Software fixture only; not a research result.'}
        self.write(self.run/'tools/frozen_method.json', self.frozen)
        self.calibration = self.root/'calibration/conformal.json'
        self.calibration.parent.mkdir()
        self.protocol = {'frozen_file_sha256': self.sha(self.run/'tools/frozen_method.json'),
                         'methods': {'revision_selected': self.frozen['spec']}, 'freeze': self.frozen}
        quantile = {'n': 19, 'rank': 18, 'q': 2., 'unbounded': False}
        self.records = {'global': {'0.1': {'alpha': .1, 'calibration': {'global': copy.deepcopy(quantile)}}},
                        'mondrian': {'0.1': {'alpha': .1, 'calibration': {
                            'static': copy.deepcopy(quantile), 'velocity': copy.deepcopy(quantile)}}}}
        self.result = {'position': [1., 2.], 'velocity': [0., 0.], 'selected_model': 'static',
                       'diagnostics': {'selected_model': 'static', 'geometry_scale_m': .5}}
        self.checked = {'frozen': self.frozen, 'audit': {'fixture_only': True}}
        self.bundle()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf8')

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def bundle(self):
        self.summary = {'status': 'complete', 'protocol': self.protocol, 'frozen': self.frozen,
                        'conformal': self.records}
        self.write(self.calibration, self.records)
        self.write(self.calibration.parent/'protocol.json', self.protocol)
        self.write(self.calibration.parent/'summary.json', self.summary)
        self.write(self.calibration.parent/'status.json', {'status': 'complete',
                   'summary_sha256': self.sha(self.calibration.parent/'summary.json')})

    def region(self, **kwargs):
        return region_from_files(copy.deepcopy(self.result), self.run, self.calibration, self.frozen, **kwargs)

    def predict(self, **kwargs):
        # These fixtures exercise calibration/region semantics, not the
        # independent numerical guard (covered with real fits elsewhere).
        with patch('locate_revision.validate_revision_freeze', return_value=self.checked), \
             patch('locate_revision.fit', return_value=copy.deepcopy(self.result)), \
             patch('application_safety.numerical_quality', return_value={'passed': True, 'fixture_only': True}):
            return predict(self.input, self.run, self.calibration, root=self.root, **kwargs)

    def test_completed_bundle_radius_and_provenance(self):
        region, radius = self.region()
        self.assertEqual(radius, 1.)
        self.assertEqual(region['q'], 2.)
        self.assertEqual(region['calibration_group'], 'static')
        self.assertTrue(region['calibration_completion_verified'])
        self.assertEqual(region['summary_sha256'], self.sha(self.calibration.parent/'summary.json'))
        self.assertEqual(region['calibration_sha256'], self.sha(self.calibration))
        self.assertFalse(region['coverage_guarantee_asserted_for_this_input'])
        self.assertFalse(region['exchangeability_verified_for_this_input'])

    def test_running_or_failed_calibration_is_rejected(self):
        for status in ('running', 'failed'):
            with self.subTest(status=status):
                self.write(self.calibration.parent/'status.json', {'status': status})
                with self.assertRaisesRegex(ValueError, 'must be complete'):
                    self.region()

    def test_missing_completion_marker_is_rejected(self):
        (self.calibration.parent/'status.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.region()

    def test_summary_raw_byte_tampering_is_rejected(self):
        path = self.calibration.parent/'summary.json'
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError, 'summary bytes'):
            self.region()

    def test_incomplete_summary_even_with_matching_hash_is_rejected(self):
        self.summary['status'] = 'running'
        self.write(self.calibration.parent/'summary.json', self.summary)
        self.write(self.calibration.parent/'status.json', {'status': 'complete',
                   'summary_sha256': self.sha(self.calibration.parent/'summary.json')})
        with self.assertRaisesRegex(ValueError, 'summary must be complete'):
            self.region()

    def test_calibration_contents_are_bound_to_completed_summary(self):
        self.records['mondrian']['0.1']['calibration']['static']['q'] = 999.
        self.write(self.calibration, self.records)
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            self.region()

    def test_adjacent_protocol_is_bound_to_completed_summary(self):
        self.protocol['unexpected_other_run'] = True
        self.write(self.calibration.parent/'protocol.json', self.protocol)
        with self.assertRaisesRegex(ValueError, 'protocol differs'):
            self.region()

    def test_frozen_record_and_method_mismatches_are_rejected(self):
        self.protocol['frozen_file_sha256'] = '0'*64
        self.bundle()
        with self.assertRaisesRegex(ValueError, 'different frozen research record'):
            self.region()
        self.protocol['frozen_file_sha256'] = self.sha(self.run/'tools/frozen_method.json')
        self.protocol['methods']['revision_selected'] = {'model': 'velocity'}
        self.bundle()
        with self.assertRaisesRegex(ValueError, 'specifications differ'):
            self.region()

    def test_summary_frozen_object_must_match_verified_run(self):
        self.summary['frozen'] = {**self.frozen, 'method_id': 'other_fixture'}
        self.write(self.calibration.parent/'summary.json', self.summary)
        self.write(self.calibration.parent/'status.json', {'status': 'complete',
                   'summary_sha256': self.sha(self.calibration.parent/'summary.json')})
        with self.assertRaisesRegex(ValueError, 'different frozen object'):
            self.region()

    def test_alpha_is_exact_and_never_interpolated(self):
        for alpha in (.05, .10000000000000002, True, 0., 1., math.inf, math.nan, '0.1'):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                self.region(alpha=alpha)
        self.records['mondrian']['0.1']['alpha'] = '0.1'
        self.bundle()
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            self.region()

    def test_empty_or_insufficient_group_preserves_infinity(self):
        for n, rank in ((0, 1), (1, 2)):
            with self.subTest(n=n):
                self.records['mondrian']['0.1']['calibration']['static'] = {
                    'n': n, 'rank': rank, 'q': 'infinity', 'unbounded': True}
                self.bundle()
                output = self.predict(max_radius=100.)
                self.assertEqual(output['region']['radius_m'], 'infinity')
                self.assertTrue(output['region']['unbounded'])
                self.assertEqual(output['acceptance']['status'], 'rejected')
                self.assertFalse(output['acceptance']['accepted'])
                self.assertEqual(output['position'], [1., 2.])
                json.dumps(output, allow_nan=False)

    def test_invalid_quantile_rank_flags_and_types_are_rejected(self):
        valid = {'n': 19, 'rank': 18, 'q': 2., 'unbounded': False}
        cases = [{'n': True}, {'n': -1}, {'rank': 17}, {'unbounded': True}, {'unbounded': 0},
                 {'q': 'infinity'}, {'q': None}, {'q': -1.}, {'q': True}, {'q': '2'}]
        for change in cases:
            with self.subTest(change=change):
                self.records['mondrian']['0.1']['calibration']['static'] = {**valid, **change}
                self.bundle()
                with self.assertRaises(ValueError):
                    self.region()
        self.records['mondrian']['0.1']['calibration']['static'] = {'n': 0, 'rank': 1, 'q': None, 'unbounded': True}
        self.bundle()
        with self.assertRaisesRegex(ValueError, 'q=infinity'):
            self.region()

    def test_unknown_or_missing_observation_group_is_not_inferred(self):
        self.result['selected_model'] = 'unknown'
        self.result['diagnostics']['selected_model'] = 'unknown'
        for rule in ('mondrian', 'global'):
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, 'Unknown observation-derived'):
                self.predict(rule=rule)
        self.result['selected_model'] = 'static'
        self.result['diagnostics']['selected_model'] = 'static'
        del self.records['mondrian']['0.1']['calibration']['static']
        self.bundle()
        with self.assertRaisesRegex(ValueError, 'group is missing'):
            self.predict()

    def test_top_level_and_diagnostic_group_must_agree(self):
        self.result['diagnostics']['selected_model'] = 'velocity'
        with self.assertRaisesRegex(ValueError, 'group disagree'):
            self.predict()
        self.result['selected_model'] = 'dynamic'
        output = self.predict()
        self.assertEqual(output['region']['observed_model_group'], 'velocity')

    def test_global_rule_and_radius_threshold_semantics(self):
        for threshold, expected in ((None, None), (.9, False), (1., True), (2., True)):
            with self.subTest(threshold=threshold):
                output = self.predict(rule='global', max_radius=threshold)
                self.assertEqual(output['region']['calibration_group'], 'global')
                self.assertEqual(output['acceptance']['accepted'], expected)
                self.assertFalse(output['acceptance']['conditional_coverage_guaranteed'])
        for threshold in (True, 0., -1., math.inf, math.nan, 'infinity'):
            with self.subTest(threshold=threshold), self.assertRaisesRegex(ValueError, 'max_radius'):
                self.predict(max_radius=threshold)

    def test_input_truth_is_rejected_and_solver_only_receives_observations(self):
        for latent in ('truth', 'scenario', 'meta', 'velocity'):
            self.write(self.input, {**OBS, latent: [0., 0.]})
            with patch('locate_revision.validate_revision_freeze', return_value=self.checked), patch('locate_revision.fit') as fit:
                with self.assertRaisesRegex(ValueError, 'exactly anchors'):
                    predict(self.input, self.run, self.calibration, root=self.root)
                fit.assert_not_called()
        self.write(self.input, OBS)
        def only_observations(obs, spec):
            self.assertEqual(spec, self.frozen['spec'])
            self.assertEqual(set(type(obs).__slots__), {'anchors', 'times', 'ranges', 'sigma'})
            self.assertFalse(hasattr(obs, 'truth'))
            return copy.deepcopy(self.result)
        with patch('locate_revision.validate_revision_freeze', return_value=self.checked), patch('locate_revision.fit', side_effect=only_observations):
            output = predict(self.input, self.run, self.calibration, root=self.root)
        self.assertEqual(output['observation_count'], 3)
        self.assertNotIn('error_m', output)

    def test_unfrozen_or_invalid_run_never_reaches_solver(self):
        with patch('locate_revision.validate_revision_freeze', side_effect=ValueError('Fixture run is unfrozen')), patch('locate_revision.fit') as fit:
            with self.assertRaisesRegex(ValueError, 'unfrozen'):
                predict(self.input, self.run, self.calibration, root=self.root)
            fit.assert_not_called()

    def test_invalid_solver_diagnostics_or_position_fail_cleanly(self):
        for key, value in (('diagnostics', None), ('diagnostics', []), ('position', [1.]),
                           ('velocity', [math.inf, 0.])):
            with self.subTest(key=key, value=value):
                saved = copy.deepcopy(self.result)
                self.result[key] = value
                with self.assertRaisesRegex(ValueError, 'invalid'):
                    self.predict()
                self.result = saved


if __name__ == '__main__':
    unittest.main()
