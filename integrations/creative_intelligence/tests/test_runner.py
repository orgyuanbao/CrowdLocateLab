"""Integration boundary tests; simulated messages are NOT research evidence.

Only the external Codex process is replaced. The vendored upstream controller,
its event reader, session checks, result parser, and state transitions run as-is.
All generated messages and evidence remain in disposable external workspaces.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


INTEGRATION = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("creative_runner_under_test", INTEGRATION / "runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def structured(status="continue"):
    return json.dumps({
        "status": status,
        "summary": "Simulated controller test only; no mathematical result.",
        "skills_used": ["construct-mathematical-concept"],
        "next_step": "Continue the controller boundary test." if status == "continue" else "",
    })


def event_stream(session="test-session-A", final=None):
    events = []
    if session is not None:
        events.append({"type": "thread.started", "thread_id": session})
    if final is not None:
        events.append({"type": "item.completed", "item": {"type": "agent_message", "text": final}})
    return "".join(json.dumps(event) + "\n" for event in events)


class SimulatedProcess:
    """A finite JSONL stream, with no shell, model, network, or subprocess."""
    def __init__(self, text, returncode=0):
        self.stdout = io.StringIO(text)
        self.pid = 987654321
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="creative-runner-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "research"
        self.problem = self.root / "problem-source.md"
        self.problem.write_text("# Simulated test problem\nNo numerical research is performed.\n", encoding="utf-8")
        self.materials = self.root / "materials-source"
        self.materials.mkdir()
        (self.materials / "input.txt").write_text("Immutable test input.\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.metadata = runner.initialize(self.workspace, "test-run", self.problem, self.materials)
        self.controller = runner.bind_controller(self.workspace)
        self.evidence = runner.evidence_root(self.workspace)

    def state(self):
        return runner.read_json(self.controller.state_path_for("test-run"))

    def advance(self, *, final=None, session="test-session-A", returncode=0, raw=None, effect=None, prompt=None):
        data = raw if raw is not None else event_stream(session, structured() if final is None else final)
        process = SimulatedProcess(data, returncode)

        def start(*args, **kwargs):
            if effect:
                effect(args, kwargs)
            return process

        with mock.patch.object(self.controller, "codex_executable", return_value="codex-test-never-executed"), \
             mock.patch.object(self.controller.subprocess, "Popen", side_effect=start) as popen, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = runner.advance(self.workspace, "test-run", prompt, controller=self.controller)
        return result, popen

    def test_initialize_binds_original_controller_and_immutable_inputs(self):
        self.assertEqual(self.controller.ROOT, self.workspace)
        self.assertEqual(Path(self.controller.__file__).resolve(), INTEGRATION / "vendor" / "agent.py")
        self.assertEqual(self.state()["turn"], 0)
        names = self.metadata["immutable_inputs_sha256"]
        self.assertIn("runs/test-run/problem.md", names)
        self.assertIn("materials/input.txt", names)
        self.assertNotIn("runs/test-run/state.json", names)
        runner.check_inputs(self.workspace, "test-run")

    def test_one_continue_result_pauses_after_exactly_one_original_process_call(self):
        result, popen = self.advance()
        self.assertTrue(result["accepted_structured_research_turn"])
        self.assertEqual(result["command_count"], 1)
        self.assertEqual(result["execute_turn_calls"], 1)
        self.assertEqual(result["process_exit_code"], 0)
        self.assertEqual(self.state()["status"], "paused")
        self.assertEqual(self.state()["turn"], 1)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.workspace))
        self.assertFalse((self.evidence / "advance.lock").exists())

    def test_resume_uses_original_session_and_preserves_problem(self):
        original_problem = (self.workspace / "runs/test-run/problem.md").read_bytes()
        self.advance()
        result, popen = self.advance(final=structured("complete"), prompt="Test continuation, not a replacement problem.")
        command = popen.call_args.args[0]
        self.assertTrue(result["accepted_structured_research_turn"])
        self.assertIn("resume", command)
        self.assertIn("test-session-A", command)
        self.assertEqual(self.state()["turn"], 2)
        self.assertEqual(self.state()["status"], "complete")
        self.assertEqual((self.workspace / "runs/test-run/problem.md").read_bytes(), original_problem)
        recorded = runner.read_json(self.evidence / "turn-002/command.json")
        self.assertEqual(recorded["resumed_session_id"], "test-session-A")
        self.assertIn("Test continuation", (self.evidence / "turn-002/prompt.txt").read_text(encoding="utf-8"))

    def test_unexpected_resumed_session_is_rejected_and_recorded(self):
        self.advance()
        result, popen = self.advance(session="test-session-WRONG")
        self.assertFalse(result["accepted_structured_research_turn"])
        self.assertEqual(result["controller_exit_code"], 1)
        self.assertEqual(self.state()["status"], "error")
        self.assertEqual(self.state()["session_id"], "test-session-A")
        self.assertIn("unexpected session", self.state()["summary"])
        self.assertEqual(popen.call_count, 1)
        self.assertIn("test-session-WRONG", (self.evidence / "turn-002/events.raw.jsonl").read_text(encoding="utf-8"))

    def test_ordinary_non_json_reply_is_not_an_accepted_research_turn(self):
        result, _ = self.advance(final="I am ready to discuss your problem.")
        self.assertEqual(result["controller_exit_code"], 0)
        self.assertFalse(result["accepted_structured_research_turn"])
        self.assertEqual(self.state()["turn"], 0)
        self.assertEqual(self.state()["status"], "initialized")
        self.assertEqual((self.evidence / "turn-001/final_message.txt").read_text(encoding="utf-8"), "I am ready to discuss your problem.")

    def test_nonzero_process_exit_preserves_diagnostics_without_retry(self):
        def diagnostics(args, kwargs):
            kwargs["stderr"].write(b"Simulated authentication failure; not a real model call.\n")
            kwargs["stderr"].flush()

        result, popen = self.advance(returncode=17, effect=diagnostics)
        self.assertFalse(result["accepted_structured_research_turn"])
        self.assertEqual(result["controller_exit_code"], 17)
        self.assertEqual(result["process_exit_code"], 17)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(self.state()["status"], "error")
        self.assertIn("Simulated authentication failure", (self.evidence / "turn-001/codex.stderr.log").read_text(encoding="utf-8"))
        self.assertTrue((self.evidence / "turn-001/SHA256SUMS").is_file())

    def test_input_tampering_before_advance_is_rejected_without_call(self):
        (self.workspace / "materials/input.txt").write_text("tampered", encoding="utf-8")
        with mock.patch.object(self.controller.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.IntegrationError, "Immutable research input changed"):
                runner.advance(self.workspace, "test-run", controller=self.controller)
        popen.assert_not_called()
        self.assertFalse(list(self.evidence.glob("turn-*")))

    def test_input_tampering_during_model_execution_is_detected_afterward(self):
        def tamper(args, kwargs):
            (self.workspace / "runs/test-run/problem.md").write_text("changed during invocation", encoding="utf-8")

        result, _ = self.advance(effect=tamper)
        self.assertFalse(result["accepted_structured_research_turn"])
        self.assertIn("Immutable research input changed", result["input_integrity_error"])
        self.assertEqual(result["controller_exit_code"], 0)
        self.assertEqual(self.state()["status"], "paused")
        self.assertFalse((self.evidence / "advance.lock").exists())
        self.assertTrue((self.evidence / "turn-001/SHA256SUMS").is_file())

    def test_concurrent_or_interrupted_lock_refuses_to_overwrite_evidence(self):
        lock = self.evidence / "advance.lock"
        lock.write_text("existing invocation", encoding="utf-8")
        with mock.patch.object(self.controller.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.IntegrationError, "Another advance is active"):
                runner.advance(self.workspace, "test-run", controller=self.controller)
        popen.assert_not_called()
        self.assertEqual(lock.read_text(encoding="utf-8"), "existing invocation")
        self.assertFalse(list(self.evidence.glob("turn-*")))

    def test_init_inside_checkout_is_rejected_before_writing(self):
        forbidden = runner.CHECKOUT / "should-never-be-created-by-integration-test"
        self.assertFalse(forbidden.exists())
        with self.assertRaisesRegex(runner.IntegrationError, "separate from the project checkout"):
            runner.initialize(forbidden, "test-run", self.problem)
        self.assertFalse(forbidden.exists())

    def test_reinitializing_existing_workspace_preserves_original_files(self):
        before = runner.inventory(self.workspace)
        with self.assertRaisesRegex(runner.IntegrationError, "requires a new workspace"):
            runner.initialize(self.workspace, "another-run", self.problem)
        self.assertEqual(runner.inventory(self.workspace), before)

    def test_vendor_hashes_match_pinned_original_bytes(self):
        provenance = runner.verify_vendor()
        self.assertEqual(provenance["commit"], runner.UPSTREAM_COMMIT)
        self.assertEqual(runner.inventory(INTEGRATION / "vendor"), provenance["files_sha256"])
        self.assertEqual(provenance["files_sha256"]["agent.py"], "a9c505d8bc1c66369ff71f1fd5c1f13453b6338336fcb123a2164c7606b6f342")
        self.assertEqual(self.metadata["upstream_agent_sha256"], provenance["files_sha256"]["agent.py"])

    def test_runtime_config_preserves_default_model_and_disables_network(self):
        config = self.controller.load_runtime_config()
        self.assertEqual(config, runner.SAFE_CONFIG)
        self.assertNotIn("model", config)
        self.assertIsNone(self.metadata["model_override"])
        self.assertEqual(config["sandbox_workspace_write"]["network_access"], False)
        self.assertEqual(config["web_search"], "disabled")
        self.assertEqual(config["agents"]["enabled"], False)
        with mock.patch.object(self.controller, "codex_executable", return_value="codex-test-never-executed"):
            command = self.controller.build_codex_command(None, "test only")
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertFalse(any(argument.startswith("model=") for argument in command))

    def test_raw_events_and_complete_evidence_have_verifiable_hashes(self):
        raw = event_stream("test-session-A", structured())
        result, _ = self.advance(raw=raw)
        record = Path(result["evidence_directory"])
        self.assertEqual((record / "events.raw.jsonl").read_bytes(), raw.encode("utf-8"))
        expected_names = set()
        for line in (record / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            expected, name = line.split("  ", 1)
            expected_names.add(name)
            self.assertEqual(hashlib.sha256((record / name).read_bytes()).hexdigest(), expected, name)
        actual_names = set(runner.inventory(record)) - {"SHA256SUMS"}
        self.assertEqual(expected_names, actual_names)
        self.assertIn("result.json", expected_names)
        self.assertIn("state_before.json", expected_names)
        self.assertIn("state_after.json", expected_names)

    def test_invalid_jsonl_and_missing_final_are_not_research_success(self):
        for raw in ("this is not JSONL\n", event_stream("test-session-A", None)):
            with self.subTest(raw=raw):
                result, _ = self.advance(raw=raw)
                self.assertFalse(result["accepted_structured_research_turn"])
                self.assertEqual(result["controller_exit_code"], 1)

    def test_malformed_structured_result_is_rejected(self):
        result, _ = self.advance(final=json.dumps({"status": "complete", "summary": "missing required fields"}))
        self.assertFalse(result["accepted_structured_research_turn"])
        self.assertEqual(result["controller_exit_code"], 1)
        self.assertEqual(self.state()["status"], "error")

    def test_blocked_is_structured_progress_without_claiming_scientific_verification(self):
        result, _ = self.advance(final=structured("blocked"))
        self.assertTrue(result["accepted_structured_research_turn"])
        self.assertEqual(result["state_status"], "blocked")
        self.assertIn("Not established", result["scientific_verification"])

    def test_controller_hooks_are_restored_after_rejection(self):
        names = ("parse_codex_event", "build_codex_command", "execute_turn")
        before = {name: getattr(self.controller, name) for name in names}
        self.advance(raw="invalid JSONL\n")
        for name, original in before.items():
            self.assertIs(getattr(self.controller, name), original)


if __name__ == "__main__":
    unittest.main()
