"""Study contracts on independent fixtures, without a live LLM or formal benchmark."""
import copy
import hashlib
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from benchmark import conformal, iid_mixture, paired_ci, validate_frozen_run
from localization import generate_suite
from tools.common import digest, load, save
from tools.mathematics import verify
from tools.toolkit import CORE, ResearchToolkit


def prediction(case_id, error, radius_scale=1., scenario="static_clean"):
    return dict(case_id=case_id, scenario=scenario, error_m=float(error),
                diagnostics={"geometry_scale_m": float(radius_scale)})


class StatisticalProtocolTests(unittest.TestCase):
    def test_full_local_rank_does_not_remove_global_mirror_ambiguity(self):
        witness = verify("mirror")["mirror"]
        self.assertEqual(witness["jacobian_rank"], 4)
        self.assertLess(witness["max_distance_difference_m"], 1e-12)
        self.assertAlmostEqual(witness["position_separation_m"], 6.)
        self.assertGreater(min(witness["singular_values"]), .9)

    def test_conformal_uses_finite_sample_order_statistic_without_interpolation(self):
        calibration = [prediction(str(i), i, 2.) for i in range(1, 20)]
        test = [prediction("new", 17., 2.)]
        result = conformal(calibration, test, test)
        ninety = result["results"]["0.1"]
        ninety_five = result["results"]["0.05"]
        self.assertEqual(ninety["order_statistic_k"], 18)
        self.assertEqual(ninety["q"], 9.)
        self.assertEqual(ninety_five["order_statistic_k"], 19)
        self.assertEqual(ninety_five["q"], 9.5)

    def test_insufficient_calibration_yields_infinite_radius(self):
        calibration = [prediction(str(i), i+1) for i in range(5)]
        test = [prediction("new", 1000.)]
        result = conformal(calibration, test, test)
        for item in result["results"].values():
            self.assertTrue(math.isinf(item["q"]))
            self.assertEqual(item["populations"]["iid_test"]["coverage"], 1.)
            for policy in item["populations"]["iid_test"]["rejection_curve"]:
                self.assertEqual(policy["accepted_n"], 0)
                self.assertIsNone(policy["accepted_coverage"])

    def test_scale_rescaling_cancels_between_calibration_and_prediction(self):
        calibration = [prediction(str(i), i, 2.) for i in range(1, 20)]
        test = [prediction("a", 3., 1.), prediction("b", 40., 6.)]
        original = conformal(calibration, test, test)
        changed_calibration, changed_test = copy.deepcopy(calibration), copy.deepcopy(test)
        for row in changed_calibration+changed_test:
            row["diagnostics"]["geometry_scale_m"] *= 3
        changed = conformal(changed_calibration, changed_test, changed_test)
        for alpha in ("0.1", "0.05"):
            old, new = original["results"][alpha], changed["results"][alpha]
            self.assertAlmostEqual(old["q"], 3*new["q"])
            for name in ("iid_test", "shifted_stress"):
                self.assertEqual(old["populations"][name]["coverage"], new["populations"][name]["coverage"])
                self.assertAlmostEqual(old["populations"][name]["mean_radius_m"], new["populations"][name]["mean_radius_m"])

    def test_rejection_coverage_is_separate_and_not_claimed_conditional(self):
        calibration = [prediction(str(i), i) for i in range(1, 20)]
        # The smaller proxy radius has the larger normalized error. Selecting
        # small radii therefore need not preserve marginal coverage.
        test = [prediction("small", 20., .5), prediction("large", 30., 3.)]
        result = conformal(calibration, test, test)
        population = result["results"]["0.1"]["populations"]["iid_test"]
        selected = next(p for p in population["rejection_curve"] if p["radius_threshold_m"] == 10)
        self.assertEqual(population["coverage"], .5)
        self.assertEqual(selected["acceptance_rate"], .5)
        self.assertEqual(selected["accepted_coverage"], 0.)
        self.assertIn("No conditional/rejection/shift guarantee", result["claim"])

    def test_iid_sampler_uses_independent_scenario_draws_not_balanced_quotas(self):
        # Fixture generation only: intercept the expensive physical generator.
        calls = []
        def record(seed, scenario):
            calls.append((seed, scenario))
            return seed, scenario
        with patch("benchmark.generate_case", side_effect=record):
            first = iid_mixture(992740, 37)
            second = iid_mixture(992740, 37)
            other = iid_mixture(992741, 37)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 37)
        self.assertEqual({scenario for _, scenario in first}, set(CORE))
        counts = [sum(scene == scenario for _, scene in first) for scenario in CORE]
        self.assertGreater(max(counts)-min(counts), 1)
        self.assertTrue(all(1 <= seed < 2**32-1 for seed, _ in calls))

    def test_paired_ci_aligns_ids_and_gives_equal_scenario_weight(self):
        chosen = [prediction("s", 1., scenario="static_clean"),
                  prediction("m1", 2., scenario="moving_clean"),
                  prediction("m2", 3., scenario="moving_clean")]
        reference = [prediction("m2", 8., scenario="moving_clean"),
                     prediction("s", 2., scenario="static_clean"),
                     prediction("m1", 7., scenario="moving_clean")]
        result = paired_ci(chosen, reference, seed=992742, repeats=100)
        self.assertEqual(result["mean_reduction_m"], 3.)
        np.testing.assert_array_equal(result["ci95_m"], [3., 3.])


class ToolkitLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crowdloc-study-tests-")
        self.root = Path(__file__).resolve().parents[1]
        self.toolkit = ResearchToolkit(self.root, Path(self.temporary.name)/"tools")
        # Dedicated tiny fixtures, unrelated to every declared study split.
        self.toolkit.cases["discovery"] = generate_suite(992743, 1, CORE)
        self.toolkit.cases["validation"] = generate_suite(992744, 1, CORE)

    def tearDown(self):
        self.temporary.cleanup()

    def register(self, name, spec):
        return self.toolkit.dispatch("register_method", dict(method_id=name,
            hypothesis="Unit-test fixture, not scientific evidence.",
            mathematical_definition="Unit-test compositional fixture.", spec=spec))

    def test_registered_method_id_is_immutable_and_invalid_spec_is_not_registered(self):
        self.register("fixture_range", {"model": "static", "scale": "constant"})
        before = copy.deepcopy(self.toolkit.state["registry"])
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.register("fixture_range", {"model": "velocity"})
        with self.assertRaises(ValueError):
            self.register("fixture_invalid", {"model": "static", "not_a_real_parameter": 2})
        self.assertEqual(before, self.toolkit.state["registry"])

    def test_tools_cannot_request_test_or_calibration_cases(self):
        for split in ("test", "calibration", "stress", "coverage"):
            with self.subTest(split=split):
                with self.assertRaisesRegex(ValueError, "cannot access"):
                    self.toolkit.cases_for(split)

    def test_validation_budget_blocks_fifth_call(self):
        for _ in range(4):
            self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"], "split": "validation"})
        self.assertEqual(self.toolkit.state["validation_calls"], 4)
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"], "split": "validation"})
        self.assertEqual(self.toolkit.state["validation_calls"], 4)

    def test_repeated_experiments_keep_both_immutable_evidence_files(self):
        first = self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"], "split": "discovery"})
        old = first["metrics"]["static_wls"]
        old_path = self.toolkit.output/old["evidence_path"]
        original_bytes = old_path.read_bytes()
        second = self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"], "split": "discovery"})
        new = second["metrics"]["static_wls"]
        self.assertNotEqual(old["evidence_path"], new["evidence_path"])
        self.assertEqual(old_path.read_bytes(), original_bytes)
        self.assertEqual(digest(load(old_path)), old["evidence_sha256"])
        self.assertEqual(digest(load(self.toolkit.output/new["evidence_path"])), new["evidence_sha256"])

    def test_baseline_selection_type_survives_evaluation_and_diagnosis(self):
        self.toolkit.dispatch("run_experiment", {"method_ids": ["dynamic_robust"], "split": "discovery"})
        diagnosed = self.toolkit.dispatch("diagnose", {"method_id": "dynamic_robust"})
        for frequencies in diagnosed["selection_frequencies"].values():
            self.assertNotIn("unknown", frequencies)
            self.assertEqual(set(frequencies), {"dynamic"})

    def test_failed_numerical_attempt_consumes_durable_budget(self):
        with patch("tools.toolkit.evaluate", side_effect=RuntimeError("Fixture numerical failure")):
            with self.assertRaises(RuntimeError):
                self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"], "split": "validation"})
        saved = load(self.toolkit.path)
        self.assertEqual(saved["validation_calls"], 1)
        self.assertEqual(saved["experiment_calls"], 1)

    def test_freeze_requires_evidence_and_permanently_blocks_development_dispatch(self):
        with self.assertRaises(ValueError):
            self.toolkit.dispatch("freeze", {"method_id": "static_wls", "scientific_conclusion": "fixture"})
        self.register("fixture_first", {"model": "static", "scale": "constant"})
        self.register("fixture_second", {"model": "velocity", "residual": "log_range"})
        self.toolkit.dispatch("run_experiment", {"method_ids": ["fixture_first"], "split": "discovery"})
        self.toolkit.dispatch("run_experiment", {"method_ids": ["fixture_second"], "split": "discovery"})
        self.toolkit.dispatch("diagnose", {"method_id": "fixture_first"})
        self.toolkit.dispatch("verify_math", {"proposition": "mirror"})
        self.toolkit.dispatch("run_experiment", {"method_ids": ["fixture_first"], "split": "validation"})
        frozen = self.toolkit.dispatch("freeze", {"method_id": "fixture_first", "scientific_conclusion": "Fixture only."})
        self.assertTrue(frozen["frozen_before_test"])
        self.assertEqual(frozen["registry_sha256"], digest(self.toolkit.state["registry"]))
        self.assertEqual(load(self.toolkit.output/"frozen_method.json"), frozen)
        for action, arguments in (("register_method", {}), ("run_experiment", {}), ("read_sources", {})):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, "frozen"):
                    self.toolkit.dispatch(action, arguments)
        self.assertTrue(self.toolkit.dispatch("finish", {"summary": "fixture"})["complete"])


class FrozenIntegrityTests(unittest.TestCase):
    """Locally constructed provenance fixtures are not model-run evidence."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crowdloc-integrity-tests-")
        self.root = Path(self.temporary.name)/"fixture_repo"
        self.run = self.root/"runs"/"fixture"
        names = ["run_agent.py", "benchmark.py", "localization/model.py", "tools/toolkit.py", "agent_runtime/controller.py"]
        hashes = {}
        for name in names:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Unit-test fixture; not executable research code.\n", encoding="utf-8")
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.registry = {"fixture_method": {"spec": {"model": "static"}, "hypothesis": "Fixture",
                                           "mathematical_definition": "Fixture"}}
        result = {"overall": {"n": 1, "mean_m": 1.}, "macro_mean_m": 1.,
                  "rows": [prediction("fixture_case", 1.)]}
        evidence = "experiments/call_001_validation_fixture_method.json"
        save(self.run/"tools"/evidence, result)
        entry = {k: v for k, v in result.items() if k != "rows"}
        entry.update(evidence_path=evidence, evidence_sha256=digest(result))
        self.frozen = dict(method_id="fixture_method", **self.registry["fixture_method"],
                           validation=entry, scientific_conclusion="Unit-test fixture only.",
                           registry_sha256=digest(self.registry), frozen_before_test=True)
        tool_state = dict(frozen=True, selected_method="fixture_method", registry=self.registry,
                          evaluations={"validation:fixture_method": entry})
        state = dict(frozen=True, status="frozen", freeze_result=self.frozen, is_replay=False,
                     identity={"config": {"source_hashes": hashes}})
        save(self.run/"tools"/"frozen_method.json", self.frozen)
        save(self.run/"tools"/"tool_state.json", tool_state)
        save(self.run/"state.json", state)
        save(self.run/"protocol.json", {"source_hashes": hashes})

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_frozen_fixture_passes_without_generating_data(self):
        with patch("benchmark.generate_suite") as generate, patch("benchmark.iid_mixture") as mixture:
            result = validate_frozen_run(self.root, self.run)
        generate.assert_not_called()
        mixture.assert_not_called()
        self.assertEqual(result["audit"]["verified_source_files"], 5)
        self.assertEqual(result["audit"]["verified_evidence_files"], 1)

    def test_changed_source_is_rejected(self):
        (self.root/"localization"/"model.py").write_text("# Changed after freeze\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            validate_frozen_run(self.root, self.run)

    def test_changed_evidence_is_rejected(self):
        path = self.run/"tools"/self.frozen["validation"]["evidence_path"]
        evidence = load(path); evidence["rows"][0]["error_m"] = 0.
        save(path, evidence)
        with self.assertRaisesRegex(ValueError, "evidence digest mismatch"):
            validate_frozen_run(self.root, self.run)

    def test_unfrozen_or_modified_freeze_is_rejected(self):
        state_path = self.run/"state.json"
        state = load(state_path); state["frozen"] = False; save(state_path, state)
        with self.assertRaisesRegex(ValueError, "must be frozen"):
            validate_frozen_run(self.root, self.run)
        state["frozen"] = True; save(state_path, state)
        frozen = load(self.run/"tools"/"frozen_method.json")
        frozen["spec"]["model"] = "velocity"
        save(self.run/"tools"/"frozen_method.json", frozen)
        with self.assertRaisesRegex(ValueError, "freeze record differs"):
            validate_frozen_run(self.root, self.run)

    def test_partial_source_manifest_and_changed_registry_are_rejected(self):
        protocol_path, state_path = self.run/"protocol.json", self.run/"state.json"
        protocol, state = load(protocol_path), load(state_path)
        original_protocol, original_state = copy.deepcopy(protocol), copy.deepcopy(state)
        del protocol["source_hashes"]["agent_runtime/controller.py"]
        state["identity"]["config"]["source_hashes"] = protocol["source_hashes"]
        save(protocol_path, protocol); save(state_path, state)
        with self.assertRaisesRegex(ValueError, "missing critical source hashes"):
            validate_frozen_run(self.root, self.run)
        save(protocol_path, original_protocol); save(state_path, original_state)
        path = self.run/"tools"/"tool_state.json"
        tools = load(path); tools["registry"]["fixture_method"]["spec"]["ridge"] = 20.
        save(path, tools)
        with self.assertRaisesRegex(ValueError, "registry hash"):
            validate_frozen_run(self.root, self.run)


if __name__ == "__main__":
    unittest.main()
