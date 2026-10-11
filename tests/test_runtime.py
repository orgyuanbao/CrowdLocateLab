"""Runtime tests use explicitly labelled fixtures; they are not research runs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime import (CodexCLIProvider, CRITIC_SCHEMA, EventLog, PLANNER_SCHEMA,
                           ProviderError, ProviderResult, ReplayProvider, ResearchController)
from agent_runtime.providers import detect_unexpected_tools, extract_model_identifier, sha256_text
from agent_runtime.schema import canonical_json, validate_response


def decision(action, arguments=None):
    return {"analysis_summary": "Fixture decision for a unit test, not model evidence.",
            "hypothesis": "Fixture hypothesis", "action": action,
            "arguments_json": json.dumps(arguments or {}), "expected_result": "Fixture observation",
            "memory_note": "Fixture memory"}


CRITIQUE = {"scope_valid": True, "issues": ["Fixture caveat"],
            "recommended_next_step": "Freeze if the tool prerequisites are met.",
            "evidence_caveats": ["This fixture is not actual model inference."]}


class FixtureProvider:
    name = "unit_test_fixture_not_llm"
    is_replay = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, prompt, schema, request_id, artifact_dir):
        self.calls.append((prompt, schema, request_id))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return ProviderResult(response, self.name, request_id, 0., sha256_text(prompt),
                              sha256_text(canonical_json(schema)))


class FixtureToolkit:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, arguments):
        self.calls.append((action, arguments))
        if action == "diagnose" and arguments.get("fail"):
            raise ValueError("Fixture tool error")
        return {"ok": True, "action": action, "case_id": "fixture-case-001"}

    def catalog(self):
        return {"fixture_method": {"spec": {"model": "static"}}}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def controller(self, provider, toolkit=None, output=None, **kwargs):
        options = dict(output=output or self.directory / "run", goal="Fixture mathematical research",
                       tool_spec={"freeze": {"description": "Fixture freeze"}}, config={"fixture": True},
                       seed_hash="fixed-fixture-seed-hash", max_steps=12, max_experiments=6,
                       max_model_calls=20, max_retries=2, critic_every=3)
        options.update(kwargs)
        return ResearchController(provider, toolkit or FixtureToolkit(), **options)

    def events(self, output=None):
        return [json.loads(line) for line in ((output or self.directory / "run") / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_model_actions_drive_tools_and_independent_critic(self):
        provider = FixtureProvider([decision("run_experiment") for _ in range(3)] + [CRITIQUE, decision("freeze")])
        toolkit = FixtureToolkit()
        controller = self.controller(provider, toolkit)
        state = controller.run()
        self.assertEqual(state["status"], "frozen")
        self.assertEqual(len(toolkit.calls), 4)
        self.assertEqual(len(state["critiques"]), 1)
        self.assertEqual(provider.calls[3][1], CRITIC_SCHEMA)
        self.assertIn("independent critic", provider.calls[3][0])
        self.assertEqual(state["successful_experiments"], 3)
        kinds = {event["type"] for event in self.events()}
        self.assertTrue({"model_request", "model_response", "tool_call", "tool_result", "critique", "decision"}.issubset(kinds))
        self.assertIn("fixture-case-001", provider.calls[1][0])

    def test_resume_rejects_seed_or_configuration_drift(self):
        self.controller(FixtureProvider([decision("freeze")])).run()
        with self.assertRaisesRegex(ValueError, "Resume rejected"):
            self.controller(FixtureProvider([]), resume=True, seed_hash="different")
        with self.assertRaisesRegex(ValueError, "Resume rejected"):
            self.controller(FixtureProvider([]), resume=True, config={"fixture": False})

    def test_frozen_resume_does_not_call_model_or_tools(self):
        self.controller(FixtureProvider([decision("freeze")])).run()
        provider, toolkit = FixtureProvider([]), FixtureToolkit()
        state = self.controller(provider, toolkit, resume=True).run()
        self.assertTrue(state["frozen"])
        self.assertEqual(provider.calls, [])
        self.assertEqual(toolkit.calls, [])

    def test_manual_stop_is_checked_before_model_call(self):
        provider = FixtureProvider([])
        controller = self.controller(provider)
        controller.request_stop("Unit test requests stop")
        self.assertEqual(controller.run()["status"], "stopped")
        self.assertEqual(provider.calls, [])

    def test_model_failure_has_two_retries_and_no_scripted_fallback(self):
        provider = FixtureProvider([ProviderError("fixture failure")] * 3)
        toolkit = FixtureToolkit()
        controller = self.controller(provider, toolkit)
        with self.assertRaises(ProviderError):
            controller.run()
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(toolkit.calls, [])
        self.assertEqual(controller.state["status"], "provider_failed")
        self.assertFalse(any(event["type"] == "model_response" for event in self.events()))

    def test_tool_failure_is_preserved_as_model_observation(self):
        provider = FixtureProvider([decision("diagnose", {"fail": True}), decision("freeze")])
        controller = self.controller(provider)
        controller.run()
        self.assertIn("Fixture tool error", provider.calls[1][0])
        self.assertFalse(controller.state["memory"][0]["tool_succeeded"])

    def test_experiment_budget_blocks_additional_dispatch(self):
        provider = FixtureProvider([decision("run_experiment"), decision("run_experiment"), decision("freeze")])
        toolkit = FixtureToolkit()
        state = self.controller(provider, toolkit, max_experiments=1).run()
        self.assertEqual(state["experiment_calls"], 1)
        self.assertEqual([action for action, _ in toolkit.calls], ["run_experiment", "freeze"])
        self.assertIn("budget exhausted", provider.calls[2][0])

    def test_event_tampering_is_detected_before_resume(self):
        self.controller(FixtureProvider([decision("freeze")])).run()
        path = self.directory / "run" / "events.jsonl"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("Fixture mathematical research", "Tampered mathematical research", 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash failed"):
            self.controller(FixtureProvider([]), resume=True)

    def test_replay_is_explicitly_labelled_and_exhausts(self):
        self.controller(FixtureProvider([decision("read_sources"), decision("freeze")])).run()
        replay = ReplayProvider(self.directory / "run", strict_prompt=True)
        output = self.directory / "audit"
        result = self.controller(replay, output=output).run()
        self.assertTrue(result["is_replay"])
        responses = [event["payload"] for event in self.events(output) if event["type"] == "model_response"]
        self.assertTrue(all(item["replay"] and not item["audit"]["new_model_inference"] for item in responses))
        with self.assertRaisesRegex(ProviderError, "exhausted"):
            replay.complete("extra", PLANNER_SCHEMA, "extra", output / "model_calls")

    def test_unexpected_cli_tool_events_are_flagged(self):
        events = [{"type": "item.started", "item": {"id": "1", "type": "command_execution"}},
                  {"type": "item.completed", "item": {"id": "1", "type": "command_execution"}},
                  {"type": "item.completed", "item": {"id": "2", "type": "agent_message"}}]
        self.assertEqual(len(detect_unexpected_tools(events)), 1)

    def test_response_arguments_must_be_finite_json_object(self):
        for invalid in ("[]", "null", '{"bad":NaN}'):
            response = decision("read_sources")
            response["arguments_json"] = invalid
            with self.assertRaises(ValueError):
                validate_response(response, PLANNER_SCHEMA)

    def test_model_identifier_is_reported_or_explicitly_unknown(self):
        self.assertEqual(extract_model_identifier([{"type": "turn.started", "model": "reported-fixture-model"}], "")[0],
                         "reported-fixture-model")
        self.assertEqual(extract_model_identifier([], "model: header-fixture-model\n")[0], "header-fixture-model")
        self.assertEqual(extract_model_identifier([{"text": "I claim to be some model"}], ""),
                         (None, "not_reported_by_cli"))

    def test_reasoning_override_is_per_call_without_model_override(self):
        captured = {}

        class ProcessFixture:
            returncode = 0

            def __init__(self, command, **kwargs):
                captured["command"] = command
                response_path = Path(command[command.index("-o") + 1])
                response_path.write_text(json.dumps(decision("read_sources")), encoding="utf-8")
                kwargs["stdout"].write(b'{"type":"turn.completed","usage":{"input_tokens":7}}\n')

            def communicate(self, input=None, timeout=None):
                captured["input"] = input
                captured["timeout"] = timeout

        provider = CodexCLIProvider(executable="fixture-codex", timeout=300, reasoning_effort="medium")
        with patch("agent_runtime.providers.subprocess.Popen", ProcessFixture):
            result = provider.complete("Fixture prompt", PLANNER_SCHEMA, "fixture-call", self.directory / "provider")
        self.assertIn('model_reasoning_effort="medium"', captured["command"])
        self.assertNotIn("--model", captured["command"])
        self.assertEqual(captured["input"], b"Fixture prompt")
        self.assertEqual(result.usage["input_tokens"], 7)
        self.assertIsNone(result.model_identifier)

    def test_observation_context_is_bounded_but_full_log_preserved(self):
        controller = self.controller(FixtureProvider([]))
        original = {"step": 1, "action": "diagnose", "result": {"text": "x" * 40000}}
        controller.state["observations"].append(original)
        contextual = controller._context()["recent_tool_observations"][0]
        self.assertTrue(contextual["context_truncated"])
        self.assertLess(len(canonical_json(contextual)), 15000)
        self.assertEqual(len(controller.state["observations"][0]["result"]["text"]), 40000)

    def test_source_context_preserves_both_research_readings(self):
        controller = self.controller(FixtureProvider([]))
        original = {"step": 1, "action": "read_sources", "result": {"sources": {
            "research_context.md": "r" * 14000, "method_math.md": "m" * 13000}}}
        controller.state["observations"].append(original)
        contextual = controller._context()["recent_tool_observations"][0]
        self.assertFalse(contextual["context_truncated"])
        self.assertEqual(contextual["context_character_limit"], 36000)
        self.assertEqual(contextual["result"]["sources"]["method_math.md"], "m" * 13000)
        self.assertGreater(contextual["context_characters"], 27000)


if __name__ == "__main__":
    unittest.main()
