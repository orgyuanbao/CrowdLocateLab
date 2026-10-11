"""Revision policy tests use isolated fixtures, never real model or holdout data."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark import validate_frozen_run
from localization import generate_suite
from revision.agent import (DISCOVERY_SEED, VALIDATION_SEED, SCENARIOS, REFERENCE_IDS,
                            RevisionToolkit, source_hashes)
from tools.common import digest, load, save


class RevisionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crowdloc-revision-test-")
        self.root = Path(self.temp.name)
        self.output = self.root / "run/tools"
        prior = {"method_id": "prior_fixture", "spec": {"model": "cv_select", "cv_folds": 3, "starts": 3},
                 "mathematical_definition": "Historical K=3 objective; fixture, not scientific evidence."}
        save(self.root / "runs/live_reference/tools/frozen_method.json", prior)
        with patch("revision.agent.validate_frozen_run", return_value={"frozen": prior}):
            self.toolkit = RevisionToolkit(self.root, self.output)
        # These tiny, unrelated inputs are software fixtures, not a study split.
        self.toolkit.cases["discovery"] = generate_suite(2992743, 1, ["static_clean"])
        self.toolkit.cases["validation"] = generate_suite(2992744, 1, ["static_clean"])

    def tearDown(self):
        self.temp.cleanup()

    def register(self, name="candidate_four", spec=None):
        return self.toolkit.dispatch("register_method", {
            "method_id": name, "hypothesis": "Fixture hypothesis following diagnostic evidence.",
            "mathematical_definition": "Fixture K=4 CV; not a performance claim.",
            "spec": spec or {"model": "cv_select", "cv_folds": 4, "starts": 1}})

    def prepare_freeze(self):
        t = self.toolkit
        t.dispatch("run_experiment", {"method_ids": ["v2_frozen", "static_wls"]})
        t.dispatch("diagnose", {"method_id": "v2_frozen"})
        self.register()
        t.dispatch("ablate", {"method_id": "candidate_four", "changes": {"gate_margin": .15},
                              "new_id": "candidate_gate"})
        t.dispatch("run_experiment", {"method_ids": ["candidate_gate", "v2_frozen", "static_wls", "dynamic_robust"],
                                      "split": "validation"})
        t.dispatch("run_experiment", {"method_ids": ["static_robust", "legacy_bic"], "split": "validation"})
        t.dispatch("verify_math", {"proposition": "mirror"})

    def test_five_references_are_not_new_candidates(self):
        self.assertEqual(tuple(self.toolkit.state["registry"]), REFERENCE_IDS)
        self.assertEqual(self.toolkit.state["new_method_ids"], [])
        with self.assertRaisesRegex(ValueError, "five prior references"):
            self.toolkit.dispatch("freeze", {"method_id": "v2_frozen", "scientific_conclusion": "Fixture"})
        self.register()
        self.toolkit.state.update(experiment_calls=3, diagnoses=1, math_checks=1,
                                  revision_method_ids=["candidate_four"], single_component_ablations=["candidate_four"])
        with self.assertRaisesRegex(ValueError, "2 new compositions"):
            self.toolkit.dispatch("freeze", {"method_id": "candidate_four", "scientific_conclusion": "Fixture"})

    def test_new_seed_and_scenario_protocol_without_generating_study_cases(self):
        self.toolkit.cases.clear()
        fixtures = generate_suite(2992745, 1, ["static_clean"])
        with patch("revision.agent.generate_suite", return_value=fixtures) as generate:
            self.toolkit.cases_for("discovery")
            self.toolkit.cases_for("validation")
        self.assertEqual(generate.call_args_list[0].args, (1646191, 20, SCENARIOS))
        self.assertEqual(generate.call_args_list[1].args, (1657383, 30, SCENARIOS))
        self.assertEqual(len(SCENARIOS), 5)
        self.assertIn("sparse", SCENARIOS)
        self.assertTrue({DISCOVERY_SEED, VALIDATION_SEED}.isdisjoint({46191, 57383, 814711, 826013, 837901, 849391, 862193, 875149}))
        for split in ("test", "holdout", "calibration", "stress", "coverage"):
            with self.assertRaisesRegex(ValueError, "cannot access"):
                self.toolkit.cases_for(split)

    def test_folds_validation_and_effective_duplicate_rejection(self):
        for folds in (True, 1, 6, 3.0, "4"):
            with self.subTest(folds=folds), self.assertRaises(ValueError):
                self.register("bad_folds", {"cv_folds": folds})
        with self.assertRaisesRegex(ValueError, "distinct effective"):
            self.register("same_prior", copy.deepcopy(self.toolkit.prior_spec))
        self.register()
        with self.assertRaisesRegex(ValueError, "distinct effective"):
            self.register("duplicate_four")

    def test_diagnosis_precedes_revision_and_registration_file_agrees(self):
        self.register("before_diagnosis")
        self.assertEqual(self.toolkit.state["revision_method_ids"], [])
        self.toolkit.dispatch("run_experiment", {"method_ids": ["before_diagnosis"]})
        self.toolkit.dispatch("diagnose", {"method_id": "before_diagnosis"})
        self.register("after_diagnosis", {"model": "cv_select", "cv_folds": 4, "starts": 1, "gate_margin": .2})
        record = self.toolkit.state["registry"]["after_diagnosis"]
        self.assertEqual(record["registered_after_diagnosis_count"], 1)
        self.assertEqual(record["diagnosis_evidence"][0]["method_id"], "before_diagnosis")
        self.assertEqual(record, load(self.output / "methods/after_diagnosis.json"))

    def test_single_change_ablation_updates_mathematical_definition(self):
        self.register()
        for changes in ({}, {"cv_folds": 4}, {"cv_folds": 5, "gate_margin": .2}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.toolkit.dispatch("ablate", {"method_id": "candidate_four", "new_id": "bad_ablation", "changes": changes})
        result = self.toolkit.dispatch("ablate", {"method_id": "candidate_four", "new_id": "candidate_five", "changes": {"cv_folds": 5}})
        record = self.toolkit.state["registry"]["candidate_five"]
        definition = record["mathematical_definition"]
        self.assertIn("cv_folds: 4 -> 5", definition)
        self.assertIn('"cv_folds": 5', definition)
        self.assertIn("overrides", definition)
        self.assertEqual(result["controlled_changes"], {"cv_folds": 5})
        self.assertEqual(record["spec"]["cv_folds"], 5)
        self.assertEqual(record, load(self.output / "methods/candidate_five.json"))

    def test_inactive_controls_do_not_create_new_methods_or_ablation(self):
        self.register("static_fixture", {"model": "static", "starts": 1, "cv_folds": 3})
        with self.assertRaisesRegex(ValueError, "distinct effective"):
            self.register("static_fake_new", {"model": "static", "starts": 1, "cv_folds": 4,
                                                "log_sd": .2, "ridge": .1, "seed": 77})
        with self.assertRaisesRegex(ValueError, "inactive field"):
            self.toolkit.dispatch("ablate", {"method_id": "static_fixture", "new_id": "static_fake_ablation",
                                             "changes": {"cv_folds": 4}})

    def test_freeze_requires_all_baselines_and_shared_validation(self):
        self.prepare_freeze()
        entry = self.toolkit.state["evaluations"].pop("validation:legacy_bic")
        with self.assertRaisesRegex(ValueError, "legacy_bic"):
            self.toolkit.dispatch("freeze", {"method_id": "candidate_gate", "scientific_conclusion": "Fixture"})
        self.toolkit.state["evaluations"]["validation:legacy_bic"] = entry
        groups = self.toolkit.state["validation_groups"]
        self.toolkit.state["validation_groups"] = [["candidate_gate"], [*REFERENCE_IDS]]
        with self.assertRaisesRegex(ValueError, "share a validation call"):
            self.toolkit.dispatch("freeze", {"method_id": "candidate_gate", "scientific_conclusion": "Fixture"})
        self.toolkit.state["validation_groups"] = groups
        frozen = self.toolkit.dispatch("freeze", {"method_id": "candidate_gate", "scientific_conclusion": "Fixture only; no scientific claim"})
        self.assertEqual(frozen["controlled_changes"], {"gate_margin": .15})
        self.assertTrue(frozen["frozen_before_test"])
        for action in ("register_method", "run_experiment", "diagnose", "read_sources", "ablate", "freeze"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "frozen"):
                self.toolkit.dispatch(action, {})

    def test_original_frozen_validator_accepts_extended_registry(self):
        self.prepare_freeze()
        frozen = self.toolkit.dispatch("freeze", {"method_id": "candidate_gate", "scientific_conclusion": "Fixture"})
        # Build isolated source manifest fixtures; never edit any project source.
        for name in ("run_agent.py", "benchmark.py", "run_revision.py", "benchmark_revision.py", "revision/agent.py", "revision/evaluation.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture source\n", encoding="utf8")
        save(self.root / "research/revision_plan.json", {"test_fixture": True})
        hashes = source_hashes(self.root)
        protocol = {"source_hashes": hashes}
        save(self.root / "run/protocol.json", protocol)
        save(self.root / "run/state.json", {"frozen": True, "status": "frozen", "freeze_result": frozen,
            "identity": {"config": protocol}, "is_replay": False})
        checked = validate_frozen_run(self.root, self.root / "run")
        self.assertEqual(checked["frozen"]["method_id"], "candidate_gate")
        self.assertIn("research/revision_plan.json", hashes)
        self.assertIn("revision/evaluation.py", hashes)
        self.assertIn("benchmark_revision.py", hashes)
        (self.root / "revision/agent.py").write_text("# changed fixture\n", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            validate_frozen_run(self.root, self.root / "run")


if __name__ == "__main__":
    unittest.main()
