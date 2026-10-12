"""Offline evidence/fault fixtures; never model or scientific performance data."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime import ProviderResult
from agent_runtime.providers import sha256_text
from agent_runtime.schema import canonical_json
from hardened_runtime import (CHECKPOINT_NAME, UNCERTAIN_TOOL_NAME, HARDENED_TOOL_SPEC,
    HardenedResearchController, HardenedResearchToolkit, HardenedRevisionToolkit,
    EvidenceIntegrityError, preflight_resume, verified_evidence_snapshot)
from localization import generate_suite
from tools.common import digest, load, save
from tools.toolkit import evaluate


def decision(action, arguments):
    return {"analysis_summary": "Offline fault fixture", "hypothesis": "Fixture",
            "action": action, "arguments_json": json.dumps(arguments),
            "expected_result": "Fixture", "memory_note": "Fixture"}


class OfflineProvider:
    name = "offline_evidence_fixture_not_llm"
    is_replay = False

    def __init__(self, responses):
        self.responses, self.calls = iter(responses), 0

    def complete(self, prompt, schema, request_id, artifact_dir):
        self.calls += 1
        return ProviderResult(next(self.responses), self.name, request_id, 0.,
                              sha256_text(prompt), sha256_text(canonical_json(schema)))


class HardenedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crowdloc-evidence-fixture-")
        self.root = Path(self.temp.name)
        self.output = self.root / "run"
        self.toolkit = HardenedResearchToolkit(self.root, self.output / "tools")
        self.toolkit.cases["discovery"] = generate_suite(2992743, 1, ["static_clean"])
        save(self.output / "protocol.json", {"fixture": True})

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, provider):
        return HardenedResearchController(provider, self.toolkit, self.output, "Offline fixture",
            HARDENED_TOOL_SPEC, {"fixture": True}, "fixture", max_steps=3,
            max_experiments=2, max_model_calls=3)

    def completed_fixture(self):
        provider = OfflineProvider([
            decision("run_experiment", {"method_ids": ["static_wls"]}),
            decision("finish", {"summary": "Software fixture only"})])
        self.controller(provider).run()
        self.entry = self.toolkit.state["evaluations"]["discovery:static_wls"]
        return self.output / "tools" / self.entry["evidence_path"]

    def corrupt(self, path):
        value = load(path)
        value["rows"][0]["error_m"] = 987654321.
        save(path, value)

    def test_resume_rejects_changed_experiment_bytes_without_writing(self):
        path = self.completed_fixture()
        self.corrupt(path)
        before = (self.output / "state.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "tool artifact inventory/hash"):
            preflight_resume(self.output, require_tool_state=True)
        self.assertEqual((self.output / "state.json").read_bytes(), before)

    def test_resume_rejects_missing_experiment_file(self):
        self.completed_fixture().unlink()
        with self.assertRaisesRegex(ValueError, "tool artifact inventory/hash"):
            preflight_resume(self.output, require_tool_state=True)

    def test_resume_rejects_extra_uncheckpointed_tool_file(self):
        self.completed_fixture()
        (self.output / "tools/extra.json").write_text("{}", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "tool artifact inventory/hash"):
            preflight_resume(self.output, require_tool_state=True)

    def test_checkpoint_binds_raw_bytes_even_when_normalized_digest_unchanged(self):
        path = self.completed_fixture()
        path.write_text(json.dumps(load(path), separators=(",", ":")), encoding="utf8")
        self.assertEqual(digest(load(path)), self.entry["evidence_sha256"])
        with self.assertRaisesRegex(ValueError, "tool artifact inventory/hash"):
            preflight_resume(self.output, require_tool_state=True)

    def test_reference_digest_is_verified_independently_of_raw_inventory(self):
        self.toolkit.dispatch("run_experiment", {"method_ids": ["static_wls"]})
        self.toolkit.state["evaluations"]["discovery:static_wls"]["evidence_sha256"] = "f" * 64
        self.toolkit.persist()
        self.controller(OfflineProvider([]))
        with self.assertRaisesRegex(EvidenceIntegrityError, "Recorded evidence digest"):
            preflight_resume(self.output, require_tool_state=True)

    def test_diagnosis_refuses_changed_evidence_immediately(self):
        path = self.completed_fixture()
        self.corrupt(path)
        before = self.toolkit.state["diagnoses"]
        with self.assertRaisesRegex(EvidenceIntegrityError, "Recorded evidence digest"):
            self.toolkit.dispatch("diagnose", {"method_id": "static_wls"})
        self.assertEqual(self.toolkit.state["diagnoses"], before)
        self.assertTrue((self.output / "tools" / UNCERTAIN_TOOL_NAME).is_file())

    def test_diagnosis_reads_verified_snapshot_only_once(self):
        path = self.completed_fixture().resolve()
        original = load(path)
        expected_error = original["rows"][0]["error_m"]
        changed = copy.deepcopy(original)
        changed["rows"][0]["error_m"] = 987654321.
        real_read = Path.read_bytes
        reads = []

        def read_then_replace(target):
            data = real_read(target)
            if target.resolve() == path:
                reads.append(target)
                save(path, changed)
            return data

        with patch.object(Path, "read_bytes", read_then_replace):
            result = self.toolkit.tool_diagnose("static_wls")
        self.assertEqual(len(reads), 1)
        self.assertEqual(result["worst_cases"][0]["error_m"], expected_error)
        self.assertEqual(load(path)["rows"][0]["error_m"], 987654321.)

    def test_evidence_paths_cannot_escape_this_run(self):
        outside = self.root / "outside.json"
        save(outside, {"fixture": True})
        entry = {"evidence_path": "../../outside.json", "evidence_sha256": digest({"fixture": True})}
        with self.assertRaisesRegex(EvidenceIntegrityError, "escapes this run"):
            verified_evidence_snapshot(self.output / "tools", entry)
        entry["evidence_path"] = str(outside)
        with self.assertRaisesRegex(EvidenceIntegrityError, "must be relative"):
            verified_evidence_snapshot(self.output / "tools", entry)

    def test_revision_diagnosis_retains_matching_reference_history(self):
        self.completed_fixture()
        # Invoke the additive implementation with isolated state shaped exactly
        # like the revision toolkit. No reference research files are changed.
        self.toolkit.state.update(revision_version=3, diagnosis_history=[])
        result = HardenedRevisionToolkit.tool_diagnose(self.toolkit, "static_wls")
        self.assertEqual(result["revision_diagnosis"]["evidence_sha256"], self.entry["evidence_sha256"])
        self.assertEqual(self.toolkit.state["diagnosis_history"], [result["revision_diagnosis"]])

    def test_hardened_revision_toolkit_completes_valid_research_prerequisites(self):
        prior = {"method_id": "prior_fixture", "spec": {"model": "cv_select", "cv_folds": 3, "starts": 3},
                 "mathematical_definition": "Software fixture only"}
        save(self.root / "runs/live_reference/tools/frozen_method.json", prior)
        with patch("revision.agent.validate_frozen_run", return_value={"frozen": prior}):
            toolkit = HardenedRevisionToolkit(self.root, self.root / "revision_run/tools")
        toolkit.cases["discovery"] = generate_suite(2992743, 1, ["static_clean"])
        toolkit.cases["validation"] = generate_suite(2992744, 1, ["static_clean"])
        toolkit.dispatch("run_experiment", {"method_ids": ["v2_frozen", "static_wls"]})
        toolkit.dispatch("diagnose", {"method_id": "v2_frozen"})
        toolkit.dispatch("register_method", {"method_id": "candidate_four", "hypothesis": "Fixture",
            "mathematical_definition": "Software fixture only",
            "spec": {"model": "cv_select", "cv_folds": 4, "starts": 1}})
        toolkit.dispatch("ablate", {"method_id": "candidate_four", "new_id": "candidate_gate",
                                    "changes": {"gate_margin": .15}})
        toolkit.dispatch("run_experiment", {"method_ids": ["candidate_gate", "v2_frozen", "static_wls", "dynamic_robust"],
                                            "split": "validation"})
        toolkit.dispatch("run_experiment", {"method_ids": ["static_robust", "legacy_bic"], "split": "validation"})
        toolkit.dispatch("verify_math", {"proposition": "mirror"})
        result = toolkit.dispatch("freeze", {"method_id": "candidate_gate", "scientific_conclusion": "Fixture only"})
        self.assertTrue(result["frozen_before_test"])
        self.assertEqual(toolkit.state["diagnosis_history"][0]["method_id"], "v2_frozen")
        self.assertFalse((toolkit.output / UNCERTAIN_TOOL_NAME).exists())

    def test_partial_experiment_failure_blocks_further_model_calls_and_resume(self):
        provider = OfflineProvider([
            decision("run_experiment", {"method_ids": ["static_wls", "static_robust"]}),
            decision("finish", {"summary": "Must not be called"})])
        calls = []

        def partial(cases, spec):
            calls.append(spec)
            if len(calls) == 2:
                raise RuntimeError("Offline second-method failure")
            return evaluate(cases, spec)

        with patch("tools.toolkit.evaluate", side_effect=partial):
            state = self.controller(provider).run()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(state["pending_tool"]["action"], "run_experiment")
        self.assertTrue((self.output / "tools" / UNCERTAIN_TOOL_NAME).is_file())
        self.assertEqual(len(list((self.output / "tools/experiments").glob("*.json"))), 1)
        with self.assertRaisesRegex(ValueError, "uncertain tool failure"):
            preflight_resume(self.output, require_tool_state=True)
        with self.assertRaisesRegex(ValueError, "Uncertain tool failure"):
            self.toolkit.dispatch("finish", {"summary": "Unsafe continuation"})

    def test_memory_only_failure_is_not_silently_discarded_on_restart(self):
        def mutate_then_fail(**kwargs):
            self.toolkit.state["diagnoses"] += 1
            raise ValueError("Offline in-memory mutation failure")
        provider = OfflineProvider([decision("diagnose", {"method_id": "fixture"})])
        with patch.object(self.toolkit, "tool_diagnose", side_effect=mutate_then_fail):
            state = self.controller(provider).run()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        marker = load(self.output / "tools" / UNCERTAIN_TOOL_NAME)
        self.assertTrue(marker["state_changed"])
        self.assertFalse(marker["artifacts_changed"])

    def test_file_only_failure_is_not_silently_accepted(self):
        def write_then_fail(**kwargs):
            save(self.output / "tools/orphan.json", {"fixture": True})
            raise OSError("Offline artifact-only failure")
        provider = OfflineProvider([decision("diagnose", {"method_id": "fixture"})])
        with patch.object(self.toolkit, "tool_diagnose", side_effect=write_then_fail):
            state = self.controller(provider).run()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        marker = load(self.output / "tools" / UNCERTAIN_TOOL_NAME)
        self.assertFalse(marker["state_changed"])
        self.assertTrue(marker["artifacts_changed"])

    def test_prewrite_validation_error_remains_recoverable(self):
        provider = OfflineProvider([
            decision("run_experiment", {"method_ids": ["unknown_method"]}),
            decision("finish", {"summary": "No valid experiment"})])
        state = self.controller(provider).run()
        self.assertEqual(state["status"], "finished_without_freeze")
        self.assertEqual(provider.calls, 2)
        self.assertIsNone(state["pending_tool"])
        self.assertFalse((self.output / "tools" / UNCERTAIN_TOOL_NAME).exists())
        preflight_resume(self.output, require_tool_state=True)

    def test_old_checkpoint_version_is_explicitly_refused(self):
        self.completed_fixture()
        path = self.output / CHECKPOINT_NAME
        manifest = load(path)
        manifest["version"] = 1
        save(path, manifest)
        with self.assertRaisesRegex(ValueError, "old checkpoint.*version 2"):
            preflight_resume(self.output)

    def test_marker_write_failure_still_preserves_controller_uncertainty(self):
        provider = OfflineProvider([decision("run_experiment", {"method_ids": ["static_wls"]})])
        with patch("tools.toolkit.evaluate", side_effect=RuntimeError("Offline numerical failure")), \
                patch("hardened_runtime.save", side_effect=OSError("Offline marker storage failure")):
            state = self.controller(provider).run()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        self.assertIsNotNone(state["pending_tool"])
        with self.assertRaisesRegex(ValueError, "uncertain side effects"):
            preflight_resume(self.output)

    def test_dispatch_snapshot_failure_blocks_next_model_request(self):
        provider = OfflineProvider([
            decision("finish", {"summary": "Must not execute with unknown state"}),
            decision("finish", {"summary": "Must not be requested"})])
        controller = self.controller(provider)
        with patch.object(self.toolkit, "_dispatch_snapshot", side_effect=OSError("Offline inventory read failure")), \
                patch.object(self.toolkit, "tool_finish") as finish:
            state = controller.run()
        finish.assert_not_called()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        self.assertEqual(provider.calls, 1)
        self.assertIsNone(load(self.output / "tools" / UNCERTAIN_TOOL_NAME)["artifacts_changed"])

    def test_missing_durable_tool_state_is_quarantined_before_handler(self):
        self.toolkit.path.unlink()
        with self.assertRaises(FileNotFoundError), patch.object(self.toolkit, "tool_finish") as finish:
            self.toolkit.dispatch("finish", {"summary": "Unsafe"})
        finish.assert_not_called()
        self.assertTrue((self.output / "tools" / UNCERTAIN_TOOL_NAME).is_file())

    def test_preexisting_marker_stops_controller_without_overwriting_marker(self):
        marker_path = self.output / "tools" / UNCERTAIN_TOOL_NAME
        marker_path.write_text('{"earlier_process":"uncertain"}', encoding="utf8")
        original = marker_path.read_bytes()
        provider = OfflineProvider([decision("finish", {"summary": "Unsafe"})])
        state = self.controller(provider).run()
        self.assertEqual(state["status"], "tool_failure_uncertain")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(marker_path.read_bytes(), original)

    def test_cli_uncertain_tool_failure_returns_nonzero(self):
        from run_research import main
        source = self.root / "source"
        source_toolkit = HardenedResearchToolkit(self.root, source / "tools")
        provider = OfflineProvider([decision("finish", {"summary": "Replay software fixture"})])
        HardenedResearchController(provider, source_toolkit, source, "Fixture", HARDENED_TOOL_SPEC,
            {"fixture": True}, "fixture").run()
        output = self.root / "cli_failure"

        def incomplete_finish(toolkit, summary):
            toolkit.state["diagnoses"] += 1
            raise OSError("Offline injected partial tool failure")

        with patch("hardened_runtime.HonestFinishMixin.tool_finish", incomplete_finish), \
                redirect_stdout(io.StringIO()) as captured:
            result = main(["--output", str(output), "--provider", "replay", "--replay-source", str(source)])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(captured.getvalue())["status"], "tool_failure_uncertain")
        self.assertEqual(load(output / "state.json")["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()
