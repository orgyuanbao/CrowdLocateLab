"""Offline regression fixtures only; no model, holdout or performance evidence."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_runtime import ProviderResult, PLANNER_SCHEMA, EventLog
from agent_runtime.providers import sha256_text
from agent_runtime.schema import canonical_json
from hardened_runtime import (HARDENED_TOOL_SPEC, HardenedResearchController,
    HardenedResearchToolkit, ValidatedProvider, VerifiedReplayProvider,
    CHECKPOINT_NAME, LOCK_NAME, event_snapshot, exclusive_run_directory,
    preflight_resume, strict_json)
from run_research import make_hardened_config, main
from audit import audit_run


ROOT = Path(__file__).resolve().parents[1]


def decision(action="finish", arguments=None):
    return {"analysis_summary": "Offline software fixture, not model evidence.",
            "hypothesis": "Fixture hypothesis", "action": action,
            "arguments_json": json.dumps(arguments if arguments is not None else {"summary": "Insufficient evidence to freeze"}),
            "expected_result": "Fixture expectation", "memory_note": "Fixture memory"}


class FixtureProvider:
    name = "hardened_runtime_test_fixture_not_llm"
    is_replay = False

    def __init__(self, responses):
        self.responses, self.calls = iter(responses), 0

    def complete(self, prompt, schema, request_id, artifact_dir):
        self.calls += 1
        return ProviderResult(next(self.responses), self.name, request_id, 0.,
                              sha256_text(prompt), sha256_text(canonical_json(schema)))


class FixtureToolkit:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, arguments):
        self.calls.append(action)
        return {"ok": True}


def tree_hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(directory).rglob("*") if p.is_file()}


class HardenedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crowdloc-hardened-fixture-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, provider, output=None, toolkit=None, **kwargs):
        return HardenedResearchController(provider, toolkit or FixtureToolkit(), output or self.root / "run",
            "Fixture goal", HARDENED_TOOL_SPEC, {"fixture": True}, "fixture-seed",
            max_steps=4, max_experiments=3, max_model_calls=8, **kwargs)

    def source(self, name="source", note="Original source decision"):
        response = decision()
        response["memory_note"] = note
        output = self.root / name
        self.controller(FixtureProvider([response]), output).run()
        return output

    def test_actual_toolkit_finishes_without_freeze_in_one_call(self):
        provider = FixtureProvider([decision()])
        toolkit = HardenedResearchToolkit(ROOT, self.root / "run/tools")
        state = self.controller(provider, toolkit=toolkit).run()
        self.assertEqual(state["status"], "finished_without_freeze")
        self.assertFalse(state["frozen"])
        self.assertFalse(toolkit.state["frozen"])
        self.assertFalse(state["observations"][0]["result"]["test_authorized"])
        self.assertEqual(provider.calls, 1)
        self.assertFalse((self.root / "run/tools/frozen_method.json").exists())

    def test_finish_requires_a_nonempty_string_reason(self):
        toolkit = HardenedResearchToolkit(ROOT, self.root / "tools")
        for value in (None, "", "  ", [], 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                toolkit.dispatch("finish", {"summary": value})

    def test_numeric_overflow_is_retried_without_dispatched_tool(self):
        invalid = decision("register_method")
        invalid["arguments_json"] = '{"method_id":"fixture", "spec":{"ridge":1e309}}'
        provider, toolkit = FixtureProvider([invalid, decision()]), FixtureToolkit()
        state = self.controller(provider, toolkit=toolkit).run()
        self.assertEqual(provider.calls, 2)
        self.assertEqual(toolkit.calls, ["finish"])
        self.assertEqual(state["successful_model_calls"], 1)
        self.assertEqual(state["status"], "finished_without_freeze")
        snapshot = event_snapshot(self.root / "run/events.jsonl")
        self.assertEqual(state["last_event_sequence"], snapshot["last_sequence"])
        self.assertEqual(state["last_event_sha256"], snapshot["last_sha256"])
        failures = [e for e in snapshot["events"] if e.get("payload", {}).get("decision") == "model_request_failed"]
        self.assertEqual(len(failures), 1)
        self.assertIn("overflow", failures[0]["payload"]["error"])

    def test_strict_json_rejects_nested_overflow_and_duplicates(self):
        for value in ('{"a":[{"b":1e309}]}', '{"a":-1e309}', '{"a":NaN}',
                      '{"a":Infinity}', '{"a":1,"a":2}', '{"a":{"b":1,"b":2}}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json(value)
        self.assertEqual(strict_json('{"x":1.25e2,"a":[1,2]}'), {"x":125.,"a":[1,2]})

    def test_duplicate_tool_arguments_are_retried(self):
        invalid = decision()
        invalid["arguments_json"] = '{"summary":"first","summary":"second"}'
        provider = FixtureProvider([invalid, decision()])
        state = self.controller(provider).run()
        self.assertEqual(provider.calls, 2)
        self.assertEqual(state["steps"], 1)

    def test_truncated_event_tail_rejected_without_mutating_run(self):
        output = self.source()
        path = output / "events.jsonl"
        path.write_text("\n".join(path.read_text(encoding="utf8").splitlines()[:-1]) + "\n", encoding="utf8")
        # The surviving chain alone is valid, which was insufficient previously.
        EventLog(path)
        before = tree_hashes(output)
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.controller(FixtureProvider([]), output, resume=True)
        self.assertEqual(before, tree_hashes(output))

    def test_checkpoint_hash_mismatch_is_rejected_without_mutation(self):
        output = self.source()
        path = output / "state.json"
        state = json.loads(path.read_text(encoding="utf8"))
        state["last_event_sha256"] = "f" * 64
        path.write_text(json.dumps(state), encoding="utf8")
        before = tree_hashes(output)
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.controller(FixtureProvider([]), output, resume=True)
        self.assertEqual(before, tree_hashes(output))

    def test_resume_requires_existing_checkpoint_without_creating_output(self):
        output = self.root / "absent"
        with self.assertRaises(OSError):
            self.controller(FixtureProvider([]), output, resume=True)
        self.assertFalse(output.exists())

    def test_uncertain_tool_checkpoint_rejected_without_mutation(self):
        controller = self.controller(FixtureProvider([]))
        controller.state["pending_tool"] = {"action":"run_experiment"}
        controller._save()
        output = self.root / "run"
        before = tree_hashes(output)
        with self.assertRaisesRegex(ValueError, "uncertain"):
            preflight_resume(output)
        self.assertEqual(before, tree_hashes(output))

    def test_state_counter_and_memory_edits_are_rejected_without_mutation(self):
        output = self.source()
        path = output / "state.json"
        original_bytes = path.read_bytes()
        original = json.loads(original_bytes)
        for field, changed in (("model_calls", 0), ("successful_model_calls", 0),
                               ("steps", 0), ("observations", []), ("memory", [])):
            with self.subTest(field=field):
                self.assertNotEqual(original[field], changed)
                state = {**original, field: changed}
                path.write_text(json.dumps(state), encoding="utf8")
                before = tree_hashes(output)
                with self.assertRaisesRegex(ValueError, "checkpoint file hash"):
                    self.controller(FixtureProvider([]), output, resume=True)
                self.assertEqual(before, tree_hashes(output))
                path.write_bytes(original_bytes)

    def test_tool_state_and_protocol_edits_are_rejected(self):
        output = self.root / "run"
        toolkit = HardenedResearchToolkit(ROOT, output / "tools")
        (output / "protocol.json").write_text('{"fixture": true}', encoding="utf8")
        self.controller(FixtureProvider([decision()]), output, toolkit).run()
        for name in ("tools/tool_state.json", "protocol.json"):
            path = output / name
            original = path.read_bytes()
            path.write_text('{"altered":true}', encoding="utf8")
            before = tree_hashes(output)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "checkpoint file hash"):
                preflight_resume(output)
            self.assertEqual(before, tree_hashes(output))
            path.write_bytes(original)

    def test_missing_manifest_cannot_masquerade_as_hardened_resume(self):
        output = self.source()
        (output / CHECKPOINT_NAME).unlink()
        before = tree_hashes(output)
        with self.assertRaisesRegex(ValueError, "manifest missing"):
            self.controller(FixtureProvider([]), output, resume=True)
        self.assertEqual(before, tree_hashes(output))

    def test_interrupted_model_checkpoint_requires_inspection_without_mutation(self):
        controller = self.controller(FixtureProvider([]))
        controller.log.append("model_request", {"request_id":"planner-0001", "purpose":"planner"})
        controller.state["model_calls"] = 1
        controller._save()
        output = self.root / "run"
        before = tree_hashes(output)
        with self.assertRaisesRegex(ValueError, "interrupted model request"):
            self.controller(FixtureProvider([]), output, resume=True)
        self.assertEqual(before, tree_hashes(output))

    def test_corrupt_replay_source_is_rejected_before_artifact_creation(self):
        source = self.source()
        path = source / "events.jsonl"
        lines = path.read_text(encoding="utf8").splitlines()
        for index, line in enumerate(lines):
            event = json.loads(line)
            if event["type"] == "model_response":
                event["payload"]["response"]["memory_note"] = "Tampered source"
                lines[index] = json.dumps(event)
                break
        path.write_text("\n".join(lines) + "\n", encoding="utf8")
        before = tree_hashes(source)
        with self.assertRaisesRegex(ValueError, "content hash"):
            VerifiedReplayProvider(source)
        self.assertEqual(before, tree_hashes(source))

    def test_replay_uses_one_verified_immutable_source_snapshot(self):
        source = self.source()
        replay = VerifiedReplayProvider(source)
        original_hash = hashlib.sha256((source / "events.jsonl").read_bytes()).hexdigest()
        (source / "events.jsonl").write_text("altered after initialization", encoding="utf8")
        result = ValidatedProvider(replay).complete("Fixture prompt", PLANNER_SCHEMA, "planner-0001", self.root / "replay")
        self.assertEqual(result.response["memory_note"], "Original source decision")
        self.assertTrue(result.replay)
        self.assertFalse(result.audit["new_model_inference"])
        self.assertEqual(result.audit["source_sha256"], original_hash)
        saved = json.loads((self.root / "replay/planner-0001/replay_audit.json").read_text(encoding="utf8"))
        self.assertEqual(saved, result.audit)

    def test_resume_rejects_a_different_replay_source_or_strictness(self):
        first, second = self.source("source_one"), self.source("source_two", "Another source decision")
        output = self.root / "replay"
        self.controller(VerifiedReplayProvider(first), output)
        before = tree_hashes(output)
        for provider in (VerifiedReplayProvider(second), VerifiedReplayProvider(first, strict_prompt=True)):
            with self.subTest(settings=provider.settings), self.assertRaisesRegex(ValueError, "replay source"):
                self.controller(provider, output, resume=True)
            self.assertEqual(before, tree_hashes(output))

    def test_resume_of_completed_hardened_run_does_not_call_provider(self):
        output = self.source()
        provider = FixtureProvider([])
        state = self.controller(provider, output, resume=True).run()
        self.assertEqual(state["status"], "finished_without_freeze")
        self.assertEqual(provider.calls, 0)
        preflight_resume(output)

    def test_new_config_hashes_every_additive_runtime_entry_source(self):
        config = make_hardened_config(ROOT, "medium")
        for name in ("hardened_runtime.py", "run_research.py"):
            self.assertEqual(config["source_hashes"][name], hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        self.assertIn("not a new blind", config["runtime_safeguards"]["scientific_scope"])

    def test_cli_checks_invalid_resume_before_constructing_toolkit(self):
        source = self.source()
        output = self.root / "invalid_resume"
        output.mkdir()
        (output / "state.json").write_text('{"invalid":true}', encoding="utf8")
        before = tree_hashes(output)
        with patch("run_research.HardenedRevisionToolkit") as toolkit, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["--resume", "--output", str(output), "--provider", "replay", "--replay-source", str(source)])
            self.assertEqual(result.exception.code, 2)
            toolkit.assert_not_called()
        self.assertEqual(before, tree_hashes(output))

    def test_cli_replay_finish_keeps_auditable_artifacts_without_experiments(self):
        source, output = self.source(), self.root / "cli_replay_fixture"
        initial_saves = []
        original_save = HardenedResearchController._save

        def inspect_save(controller):
            original_save(controller)
            if not initial_saves:
                manifest = json.loads((output / CHECKPOINT_NAME).read_text(encoding="utf8"))
                self.assertEqual(manifest["last_event_sequence"], 1)
                self.assertTrue(all(manifest["files"].values()))
                self.assertTrue((output / "protocol.json").is_file())
                self.assertTrue((output / "tools/tool_state.json").is_file())
                self.assertTrue((output / LOCK_NAME).is_file())
                initial_saves.append(manifest)

        with patch.object(HardenedResearchController, "_save", inspect_save), redirect_stdout(io.StringIO()):
            code = main(["--output", str(output), "--provider", "replay", "--replay-source", str(source)])
        self.assertEqual(code, 0)
        self.assertEqual(len(initial_saves), 1)
        self.assertFalse((output / LOCK_NAME).exists())
        state = preflight_resume(output, require_tool_state=True)
        self.assertEqual(state["status"], "finished_without_freeze")
        self.assertTrue(state["is_replay"])
        self.assertEqual(state["experiment_calls"], 0)
        audit = audit_run(ROOT, output)
        self.assertEqual(audit["status"], "passed_unfrozen", audit.get("findings"))
        self.assertEqual(audit["counts"]["replay_responses"], 1)

    def test_cli_identical_resume_succeeds_and_refreshes_manifest_under_lock(self):
        source, output = self.source(), self.root / "cli_resume_fixture"
        arguments = ["--output", str(output), "--provider", "replay", "--replay-source", str(source)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            before = preflight_resume(output, require_tool_state=True)
            self.assertEqual(main([*arguments, "--resume"]), 0)
        after = preflight_resume(output, require_tool_state=True)
        self.assertEqual(after["last_event_sequence"], before["last_event_sequence"] + 1)
        self.assertEqual(after["model_calls"], before["model_calls"])
        self.assertEqual(after["status"], "finished_without_freeze")
        self.assertFalse((output / LOCK_NAME).exists())
        self.assertEqual(audit_run(ROOT, output)["status"], "passed_unfrozen")

    def test_bad_model_responses_exhaust_budget_with_consistent_manifest(self):
        response = decision("register_method")
        response["arguments_json"] = '{"nested":[1e309]}'
        provider = FixtureProvider([response, response])
        toolkit = FixtureToolkit()
        output = self.root / "budget_fixture"
        controller = HardenedResearchController(provider, toolkit, output, "Fixture goal", HARDENED_TOOL_SPEC,
            {"fixture": True}, "fixture-seed", max_steps=4, max_experiments=3, max_model_calls=2, max_retries=2)
        state = controller.run()
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual(state["model_calls"], 2)
        self.assertEqual(state["successful_model_calls"], 0)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(toolkit.calls, [])
        self.assertEqual(preflight_resume(output), state)

    def test_cli_blocks_preserved_runs_and_invalid_limits_before_provider(self):
        cases = [["--output", str(ROOT / "runs/revision_reference/new_run")],
                 ["--output", str(self.root / "new"), "--timeout", "nan"],
                 ["--output", str(self.root / "new"), "--max-steps", "0"]]
        for args in cases:
            with self.subTest(args=args), patch("run_research.CodexCLIProvider") as provider, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(args)
                self.assertEqual(result.exception.code, 2)
                provider.assert_not_called()
        self.assertFalse((self.root / "new").exists())

    def test_atomic_new_directory_claim_prevents_second_initialization(self):
        output = self.root / "atomic_claim"
        with exclusive_run_directory(output):
            before = tree_hashes(output)
            with self.assertRaisesRegex(ValueError, "already claimed"):
                with exclusive_run_directory(output):
                    self.fail("Second initialization obtained the same directory")
            self.assertEqual(before, tree_hashes(output))
            self.assertTrue((output / LOCK_NAME).is_file())
        self.assertFalse((output / LOCK_NAME).exists())

    def test_stale_or_foreign_lock_is_never_auto_removed(self):
        output = self.root / "stale_lock"
        output.mkdir()
        # A fake old/dead PID is diagnostic data, never sufficient for unlocking.
        content = b'{"pid":0,"created_utc":"1970-01-01T00:00:00Z"}\n'
        (output / LOCK_NAME).write_bytes(content)
        with self.assertRaisesRegex(ValueError, "never automatically"):
            with exclusive_run_directory(output, resume=True):
                self.fail("Stale lock was ignored")
        self.assertEqual((output / LOCK_NAME).read_bytes(), content)

    def test_normal_exception_releases_only_our_lock(self):
        output = self.root / "failed_run"
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with exclusive_run_directory(output):
                (output / "saved_evidence.txt").write_text("keep", encoding="utf8")
                raise RuntimeError("fixture failure")
        self.assertFalse((output / LOCK_NAME).exists())
        self.assertEqual((output / "saved_evidence.txt").read_text(encoding="utf8"), "keep")

    def test_replaced_lock_is_not_removed(self):
        output = self.root / "replaced_lock"
        with self.assertRaisesRegex(ValueError, "automatic removal refused"):
            with exclusive_run_directory(output):
                (output / LOCK_NAME).write_text("another owner", encoding="utf8")
        self.assertEqual((output / LOCK_NAME).read_text(encoding="utf8"), "another owner")

    def test_cli_resume_rejects_lock_before_preflight_or_toolkit(self):
        source, output = self.source(), self.root / "busy_run"
        output.mkdir()
        (output / LOCK_NAME).write_text("existing writer", encoding="utf8")
        before = tree_hashes(output)
        with patch("run_research.preflight_resume") as preflight, \
                patch("run_research.HardenedRevisionToolkit") as toolkit, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["--resume", "--output", str(output), "--provider", "replay", "--replay-source", str(source)])
            self.assertEqual(result.exception.code, 2)
            preflight.assert_not_called()
            toolkit.assert_not_called()
        self.assertEqual(before, tree_hashes(output))

    def test_two_processes_cannot_write_the_same_new_run(self):
        self._process_contention(resume=False)

    def test_two_processes_cannot_resume_the_same_run(self):
        self._process_contention(resume=True)

    def _process_contention(self, resume):
        output = self.root / "contended"
        if resume:
            output.mkdir()
            (output / "untouched_evidence.txt").write_text("original", encoding="utf8")
        script = r'''
from pathlib import Path
import sys,time,json
from hardened_runtime import exclusive_run_directory
base,output,identity,resume=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4]=='1'
(base/(identity+'.ready')).write_text('ready')
deadline=time.monotonic()+15
while not (base/'start').exists():
    if time.monotonic()>deadline: raise RuntimeError('Fixture start timeout')
    time.sleep(.01)
try:
    with exclusive_run_directory(output,resume=resume):
        (output/'writer.txt').write_text(identity)
        (base/(identity+'.result')).write_text('acquired')
        while not (base/'release').exists():
            if time.monotonic()>deadline: raise RuntimeError('Fixture release timeout')
            time.sleep(.01)
except ValueError:
    (base/(identity+'.result')).write_text('rejected')
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-X", "utf8", "-c", script,
                         str(self.root), str(output), name, "1" if resume else "0"], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                     for name in ("first", "second")]
        try:
            deadline = time.monotonic() + 15
            while not all((self.root / (name + ".ready")).exists() for name in ("first", "second")):
                if time.monotonic() > deadline:
                    self.fail("Child workers did not become ready")
                time.sleep(.01)
            (self.root / "start").touch()
            while not all((self.root / (name + ".result")).exists() for name in ("first", "second")):
                if time.monotonic() > deadline:
                    self.fail("Child contention did not finish")
                time.sleep(.01)
            outcomes = {name: (self.root / (name + ".result")).read_text() for name in ("first", "second")}
            self.assertEqual(sorted(outcomes.values()), ["acquired", "rejected"])
            winner = next(name for name, outcome in outcomes.items() if outcome == "acquired")
            self.assertEqual((output / "writer.txt").read_text(), winner)
        finally:
            (self.root / "release").touch()
            for process in processes:
                try:
                    stdout, stderr = process.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise
                self.assertEqual(process.returncode, 0, stderr.decode("utf8", errors="replace"))
        self.assertFalse((output / LOCK_NAME).exists())
        if resume:
            self.assertEqual((output / "untouched_evidence.txt").read_text(), "original")


if __name__ == "__main__":
    unittest.main()
