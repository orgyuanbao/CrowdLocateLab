"""Application interface checks; fixtures are not holdout or scientific results."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from locate import calibrated_radius, load_json, locate_from_files, observations_from_dict


OBSERVATIONS = {"anchors": [[0., 0.], [6., 0.], [0., 8.]],
                "times": [-3., -2., -1.], "ranges": [3.61, 5., 5.39], "sigma": [.8, .8, .8]}


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.input = self.directory / "observations.json"
        self.input.write_text(json.dumps(OBSERVATIONS), encoding="utf-8")
        self.run = self.directory / "run"
        (self.run / "tools").mkdir(parents=True)
        self.frozen = {"method_id": "fixture_static", "spec": {"model": "static"}}
        self.frozen_path = self.run / "tools" / "frozen_method.json"
        self.frozen_path.write_text(json.dumps(self.frozen), encoding="utf-8")
        self.calibration = self.directory / "calibration" / "conformal.json"
        self.calibration.parent.mkdir()
        self.protocol = {"frozen_method_sha256": hashlib.sha256(self.frozen_path.read_bytes()).hexdigest(),
                         "methods": {"llm_selected": self.frozen["spec"]}}
        (self.calibration.parent / "protocol.json").write_text(json.dumps(self.protocol), encoding="utf-8")
        self.records = {"results": {"0.1": {"alpha": .1, "q": 2., "calibration_n": 99,
                                               "order_statistic_k": 90}}}
        self.calibration.write_text(json.dumps(self.records), encoding="utf-8")
        self.prediction = {"position": [1., 2.], "velocity": [0., 0.], "selected_model": "static",
                           "diagnostics": {"geometry_scale_m": .5}, "work_units": 1, "elapsed_seconds": .001}

    def tearDown(self):
        self.temporary.cleanup()

    def test_observation_object_has_no_latent_fields(self):
        observations = observations_from_dict(OBSERVATIONS)
        self.assertEqual(observations.anchors.shape, (3, 2))
        for field in ("truth", "velocity", "scenario", "meta", "case_id"):
            self.assertFalse(hasattr(observations, field))
        self.assertFalse(observations.anchors.flags.writeable)

    def test_rejects_latent_fields_even_if_solver_would_ignore_them(self):
        value = copy.deepcopy(OBSERVATIONS)
        value["truth"] = [1, 2]
        with self.assertRaises(ValueError):
            observations_from_dict(value)

    def test_rejects_invalid_numbers_dimensions_and_future_times(self):
        edits = [("ranges", [1, 2]), ("sigma", [0, .8, .8]), ("times", [-1, 0, 1]),
                 ("ranges", [float("nan"), 2, 3]), ("ranges", [True, 2, 3]),
                 ("ranges", ["1", 2, 3]), ("times", [[-1], [-2], [-3]]),
                 ("anchors", [[0, 0, 0], [1, 0, 0], [0, 1, 0]])]
        for key, changed in edits:
            with self.subTest(key=key, changed=changed):
                value = copy.deepcopy(OBSERVATIONS)
                value[key] = changed
                with self.assertRaises(ValueError):
                    observations_from_dict(value)

    def test_rejects_duplicate_and_nonfinite_json_values(self):
        for text in ('{"anchors":[],"anchors":[]}', '{"a":NaN}', '{"a":Infinity}'):
            self.input.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(self.input)

    def test_calibrated_radius_matches_q_times_benchmark_scale(self):
        result = calibrated_radius(self.calibration, .1, self.prediction, self.frozen_path, self.frozen)
        self.assertEqual(result["radius_m"], 1.)
        self.assertFalse(result["coverage_guarantee_asserted_for_this_input"])
        self.assertFalse(result["exchangeability_verified_for_this_input"])

    def test_calibration_from_other_frozen_method_is_rejected(self):
        self.protocol["frozen_method_sha256"] = "different"
        (self.calibration.parent / "protocol.json").write_text(json.dumps(self.protocol), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different frozen"):
            calibrated_radius(self.calibration, .1, self.prediction, self.frozen_path, self.frozen)

    def test_missing_calibration_does_not_invent_radius(self):
        result = calibrated_radius(self.directory / "absent.json", .1, self.prediction, self.frozen_path, self.frozen)
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["radius_m"])

    def test_alpha_requires_precomputed_matching_level(self):
        with self.assertRaisesRegex(ValueError, "precomputed"):
            calibrated_radius(self.calibration, .05, self.prediction, self.frozen_path, self.frozen)

    def test_api_uses_only_observations_and_verified_frozen_spec(self):
        def fixture_fit(observations, spec):
            self.assertFalse(hasattr(observations, "truth"))
            self.assertEqual(spec, self.frozen["spec"])
            return self.prediction

        checked = {"frozen": self.frozen, "audit": {"unit_test_fixture": True}}
        with patch("locate.validate_frozen_run", return_value=checked) as check, patch("locate.fit", side_effect=fixture_fit):
            result = locate_from_files(self.input, self.run, self.calibration, .1, self.directory)
        check.assert_called_once()
        self.assertEqual(result["observations"]["n"], 3)
        self.assertEqual(result["position"], [1., 2.])
        self.assertEqual(result["calibration"]["radius_m"], 1.)
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
