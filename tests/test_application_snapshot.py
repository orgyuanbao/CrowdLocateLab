"""Application boundary regressions; synthetic fixtures are not research results."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import locate
import locate_revision
from localization import fit


class ApplicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / 'input.json'
        self.value = {'anchors': [[0., 0.], [6., 0.], [0., 8.]],
                      'times': [-3., -2., -1.], 'ranges': [3.61, 5., 5.39],
                      'sigma': [.8, .8, .8]}
        self.input.write_text(json.dumps(self.value), encoding='utf8')
        self.original_hash = hashlib.sha256(self.input.read_bytes()).hexdigest()
        self.frozen = {'method_id': 'software_fixture', 'spec': {'model': 'static'}}
        self.checked = {'frozen': self.frozen, 'audit': {'fixture_only': True}}
        self.result = {'position': [1., 2.], 'velocity': [0., 0.], 'selected_model': 'static',
                       'diagnostics': {'selected_model': 'static'}}

    def tearDown(self):
        self.temp.cleanup()

    def overwrite_during_fit(self, observations, spec):
        np.testing.assert_array_equal(observations.anchors, self.value['anchors'])
        replacement = {**self.value, 'anchors': [[99., 99.], [88., 88.], [77., 77.]]}
        self.input.write_text(json.dumps(replacement), encoding='utf8')
        return copy.deepcopy(self.result)

    def test_legacy_input_hash_identifies_the_bytes_actually_fitted(self):
        with patch('locate.validate_frozen_run', return_value=self.checked), \
             patch('locate.fit', side_effect=self.overwrite_during_fit), \
             patch('application_safety.numerical_quality', return_value={'passed': True}):
            output = locate.locate_from_files(self.input, self.root / 'run', root=self.root)
        self.assertEqual(output['observations']['input_sha256'], self.original_hash)
        self.assertNotEqual(output['observations']['input_sha256'], hashlib.sha256(self.input.read_bytes()).hexdigest())

    def test_revision_input_hash_identifies_the_bytes_actually_fitted(self):
        with patch('locate_revision.validate_revision_freeze', return_value=self.checked), \
             patch('locate_revision.fit', side_effect=self.overwrite_during_fit), \
             patch('locate_revision.region_from_files', return_value=({'fixture_only': True}, 1.)), \
             patch('application_safety.numerical_quality', return_value={'passed': True}):
            output = locate_revision.predict(self.input, self.root / 'run', self.root / 'cal.json', root=self.root)
        self.assertEqual(output['input_sha256'], self.original_hash)
        self.assertNotEqual(output['input_sha256'], hashlib.sha256(self.input.read_bytes()).hexdigest())

    def test_calibration_hash_identifies_the_snapshot_used_for_radius(self):
        frozen_path = self.root / 'frozen.json'
        frozen_path.write_text(json.dumps(self.frozen), encoding='utf8')
        protocol = {'frozen_method_sha256': hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
                    'methods': {'llm_selected': self.frozen['spec']}}
        (self.root / 'protocol.json').write_text(json.dumps(protocol), encoding='utf8')
        calibration = self.root / 'conformal.json'
        calibration.write_text(json.dumps({'results': {'0.1': {'alpha': .1, 'q': 2.,
                               'calibration_n': 99, 'order_statistic_k': 90}}}), encoding='utf8')
        original_hash = hashlib.sha256(calibration.read_bytes()).hexdigest()
        def changed_after_read(result):
            calibration.write_text('{}', encoding='utf8')
            return .5
        with patch('locate.calibration_scale', side_effect=changed_after_read):
            region = locate.calibrated_radius(calibration, .1, self.result, frozen_path, self.frozen)
        self.assertEqual(region['radius_m'], 1.)
        self.assertEqual(region['calibration_sha256'], original_hash)

    def test_near_collinear_actual_solver_is_rejected_before_radius_acceptance(self):
        anchors = np.array([[10., 0.], [20., .0001], [30., -.0001]])
        self.value.update(anchors=anchors.tolist(), ranges=np.linalg.norm(anchors, axis=1).tolist(), sigma=[1., 1., 1.])
        self.input.write_text(json.dumps(self.value), encoding='utf8')
        original = fit(locate.observations_from_dict(self.value), self.frozen['spec'])
        for threshold in (None, 20.):
            with self.subTest(max_radius=threshold), \
                 patch('locate_revision.validate_revision_freeze', return_value=self.checked), \
                 patch('locate_revision.region_from_files', return_value=({'fixture_only': True}, 2.4)):
                output = locate_revision.predict(self.input, self.root / 'run', self.root / 'cal.json', max_radius=threshold, root=self.root)
            self.assertEqual(output['status'], 'rejected_numerical_quality')
            self.assertFalse(output['acceptance']['accepted'])
            self.assertFalse(output['acceptance']['numerical_quality_passed'])
            self.assertEqual(output['position'], original['position'])
            self.assertTrue(any('weak_geometry' in x['code'] for x in output['numerical_quality']['reasons']))

    def test_missing_solver_status_never_becomes_an_accepted_application_result(self):
        with patch('locate.validate_frozen_run', return_value=self.checked), patch('locate.fit', return_value=self.result):
            output = locate.locate_from_files(self.input, self.root / 'run', root=self.root)
        self.assertEqual(output['status'], 'rejected_numerical_quality')
        self.assertFalse(output['numerical_quality']['passed'])
        json.dumps(output, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
