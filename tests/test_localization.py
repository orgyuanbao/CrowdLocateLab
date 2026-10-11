"""Independent numerical and data-isolation checks; no formal holdout is opened."""
import dataclasses
import json
import unittest
from unittest.mock import patch

import numpy as np

from localization import Case, SCENARIOS, fit, fit_baseline, generate_case, generate_suite
import localization.model as module


class ObservationGuard:
    __slots__ = ("anchors", "times", "ranges", "sigma")
    def __init__(self, case):
        for name in self.__slots__:
            object.__setattr__(self, name, getattr(case, name))
    def __getattr__(self, name):
        raise AssertionError(f"Forbidden non-observation access: {name}")


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.case = generate_case(889023, "moving_outliers")

    def test_observation_only_for_composed_cv_and_legacy_paths(self):
        guarded = ObservationGuard(self.case)
        spec = dict(model="cv_select", residual="log_range", scale="constant",
                    loss="cauchy", trim_fraction=.2, consensus_trials=2)
        ordinary, isolated = fit(self.case, spec), fit(guarded, spec)
        self.assertEqual(ordinary["position"], isolated["position"])
        self.assertEqual(ordinary["velocity"], isolated["velocity"])
        self.assertEqual(ordinary["work_units"], isolated["work_units"])
        json.dumps(isolated, allow_nan=False)
        for name in module.BASELINES:
            json.dumps(fit_baseline(guarded, name), allow_nan=False)

    def test_all_residual_jacobians_match_finite_differences(self):
        obs = module._read_observations(self.case)
        for model in ("static", "velocity"):
            for residual in ("range", "log_range"):
                with self.subTest(model=model, residual=residual):
                    cfg = module.validate_spec(dict(model=model, residual=residual))
                    scales = module._scales(obs, cfg)
                    p = np.array([2.3, -4.1, .15, -.09])[:2 if model == "static" else 4]
                    _, analytic = module.residual_jacobian(obs, p, model, cfg, scales)
                    numerical = np.empty_like(analytic)
                    for k in range(len(p)):
                        delta = np.zeros_like(p); delta[k] = 1e-5
                        plus = module.residual_jacobian(obs, p+delta, model, cfg, scales)[0]
                        minus = module.residual_jacobian(obs, p-delta, model, cfg, scales)[0]
                        numerical[:, k] = (plus-minus)/(2e-5)
                    np.testing.assert_allclose(analytic, numerical, atol=2e-7, rtol=2e-6)

    def test_robust_loss_derivatives_and_unrobustified_ridge(self):
        z = np.array([.1, .6, 1.6, 5., 12., 25.])
        delta = 1e-5
        for name in ("linear", "soft_l1", "huber", "cauchy"):
            with self.subTest(loss=name):
                loss = module._mixed_loss(name, 4)
                actual = loss(z)
                numerical_first = (loss(z+delta)[0]-loss(z-delta)[0])/(2*delta)
                numerical_second = (loss(z+delta)[1]-loss(z-delta)[1])/(2*delta)
                np.testing.assert_allclose(actual[1], numerical_first, atol=1e-8, rtol=1e-7)
                np.testing.assert_allclose(actual[2], numerical_second, atol=1e-8, rtol=1e-7)
                np.testing.assert_array_equal(actual[:, 4:], [[12., 25.], [1., 1.], [0., 0.]])

    def test_mean_one_lognormal_correction_has_correct_sign(self):
        # Gaussian quadrature integrates the stated one-range population model;
        # this does not claim global unbiased nonlinear localization.
        nodes, weights = np.polynomial.hermite.hermgauss(32)
        log_sd = .2
        ranges = 10*np.exp(np.sqrt(2)*log_sd*nodes-.5*log_sd**2)
        obs = module.Observations(np.tile([10., 0.], (len(nodes), 1)),
                                  np.zeros(len(nodes)), ranges, np.ones(len(nodes)))
        for correction in (False, True):
            cfg = module.validate_spec(dict(residual="log_range", log_sd=log_sd,
                                             log_bias_correction=correction))
            residual, _ = module.residual_jacobian(obs, np.zeros(2), "static", cfg, np.ones(len(nodes)))
            expected = 0. if correction else .5*log_sd**2
            self.assertAlmostEqual(float(weights@residual/np.sqrt(np.pi)), expected, places=12)

    def test_noiseless_current_position_and_velocity_recovery(self):
        anchors = np.array([[-20., -8.], [3., 19.], [25., 5.], [-12., 17.],
                            [11., -21.], [-28., 13.], [21., 23.], [-5., -28.]])
        times = np.array([-30., -25., -20., -15., -10., -6., -3., 0.])
        x, velocity = np.array([2., -3.]), np.array([.2, -.15])
        ranges = np.linalg.norm(x+times[:, None]*velocity-anchors, axis=1)
        witness = Case("exact", "algebraic_witness", anchors, times, ranges, np.ones(8), x, velocity, {})
        result = fit(ObservationGuard(witness), dict(model="velocity", residual="range", loss="linear", ridge=0.))
        np.testing.assert_allclose(result["position"], x, atol=2e-5)
        np.testing.assert_allclose(result["velocity"], velocity, atol=2e-6)

    def test_cv_training_never_contains_its_validation_observations(self):
        real_fit = module._fit_model
        calls = []
        def monitored(obs, cfg, model, tracker):
            calls.append(obs.anchors.copy())
            return real_fit(obs, cfg, model, tracker)
        with patch.object(module, "_fit_model", side_effect=monitored):
            result = fit(self.case, dict(model="cv_select", scale="constant", consensus_trials=1))
        folds = result["diagnostics"]["cv"]["folds"]
        self.assertEqual(len(folds), 3)
        self.assertEqual(len(calls), 7)
        held_out = []
        for number, fold in enumerate(folds):
            self.assertTrue(set(fold["train_indices"]).isdisjoint(fold["validation_indices"]))
            held_out.extend(fold["validation_indices"])
            for which in (0, 1):
                np.testing.assert_array_equal(calls[2*number+which], self.case.anchors[fold["train_indices"]])
        self.assertEqual(sorted(held_out), list(range(len(self.case.times))))
        np.testing.assert_array_equal(calls[-1], self.case.anchors)

    def test_work_units_include_cv_consensus_and_trim_solver_calls(self):
        real_solver = module.least_squares
        observations = {"calls": 0, "evaluations": 0}
        def monitored(fun, *args, **kwargs):
            observations["calls"] += 1
            def counted(parameters):
                observations["evaluations"] += 1
                return fun(parameters)
            return real_solver(counted, *args, **kwargs)
        with patch.object(module, "least_squares", side_effect=monitored):
            result = fit(self.case, dict(model="cv_select", consensus_trials=2,
                                        trim_fraction=.2, scale="constant"))
        self.assertGreater(observations["calls"], 10)
        self.assertEqual(result["work_units"], observations["evaluations"])
        self.assertEqual(result["diagnostics"]["computation"]["solves"], observations["calls"])

    def test_constant_scale_is_train_observation_median(self):
        result = fit(self.case, dict(model="static", scale="constant"))
        self.assertAlmostEqual(result["diagnostics"]["constant_scale"], float(np.median(self.case.sigma)))
        cv = fit(self.case, dict(model="cv_select", scale="constant"))
        self.assertTrue(np.isfinite(cv["diagnostics"]["cv"]["scores"]["static"]))

    def test_geometry_proxy_is_finite_positive_for_every_method(self):
        for spec in ({"model": "static"}, {"model": "velocity", "trim_fraction": .3},
                     {"model": "cv_select", "residual": "log_range"}, {"baseline": "bic_select"}):
            with self.subTest(spec=spec):
                result = fit(self.case, spec)
                radius = result["diagnostics"]["geometry_scale_m"]
                self.assertTrue(np.isfinite(radius) and .2 <= radius <= 100.)
                self.assertIn("uncalibrated", result["diagnostics"]["geometry_scale_note"])

    def test_small_temporal_span_falls_back_and_invalid_input_rejects(self):
        simultaneous = dataclasses.replace(self.case, times=np.zeros(16))
        self.assertEqual(fit(simultaneous, {"model": "velocity"})["selected_model"], "static")
        bad_case = dataclasses.replace(self.case, times=self.case.times[:, None])
        with self.assertRaises(ValueError): fit(bad_case, {})
        for spec in ({"trim_fraction": .36}, {"consensus_trials": 13}, {"gate_margin": 1.},
                     {"unused_option": True}, {"log_bias_correction": "yes"}, {"sigma": "truth"},
                     {"loss": "unknown"}, {"baseline": "static_wls", "ridge": 1.}):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError): fit(self.case, spec)


class ExtendedDataTests(unittest.TestCase):
    def test_nine_scenarios_reproduce_with_stable_substreams(self):
        first, second = generate_suite(889022, 1), generate_suite(889022, 1)
        self.assertEqual(len(first), 9)
        self.assertEqual([c.to_dict() for c in first], [c.to_dict() for c in second])
        self.assertEqual(len(next(c for c in first if c.scenario == "sparse").times), 8)
        subset = generate_suite(889022, 1, ["calibration_shift", "moving_clean"])
        index = {c.case_id: c.to_dict() for c in first}
        for case in subset:
            self.assertEqual(case.to_dict(), index[case.case_id])
            self.assertEqual(Case.from_dict(case.to_dict()).to_dict(), case.to_dict())

    def test_stress_controls_change_documented_generation_not_estimator_truth(self):
        case = generate_case(889021, "moving_clean", n=20, contamination=.4, log_sd=.2)
        self.assertEqual(len(case.times), 20)
        self.assertEqual(case.meta["actual_outlier_fraction"], .4)
        self.assertEqual(case.meta["range_log_sd"], .2)
        self.assertEqual(case.to_dict(), generate_case(889021, "moving_clean", n=20,
                                                        contamination=.4, log_sd=.2).to_dict())
        json.dumps(fit(ObservationGuard(case), {"residual": "log_range"}), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
