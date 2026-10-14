"""Fabricated archive boundary fixtures; no model calls or research results."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import research_archive as archive


class ResearchArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crowdloc-archive-fixture-")
        self.root = Path(self.temp.name) / "project"
        self.run = self.root / "runs" / "fixture_not_research"
        self.run.mkdir(parents=True)
        self.source = self.root / "solver.py"
        self.source.write_text("# Offline fixture, not a scientific solver\n", encoding="utf-8")
        self.protocol = {"source_hashes": {"solver.py": hashlib.sha256(self.source.read_bytes()).hexdigest()}}
        self.write("protocol.json", self.protocol)
        self.events = []
        self.action(1, "register_method", {"method_id": "fixture"}, {"registered": "fixture"})
        self.action(2, "verify_math", {"proposition": "mirror"}, {"status": "numerical witness", "proof": "fixture assertion"})
        self.append("critique", {"issues": ["fixture caveat"], "independent_prompt": True, "different_model_claimed": False})
        self.action(3, "freeze", {"method_id": "fixture"}, {"scientific_conclusion": "Fixture conclusion, not research evidence"})
        self.flush()
        self.checked = {"exit_code": 0, "status": "passed_frozen", "integrity_ok": True,
                        "counts": {"events": len(self.events)}, "findings": [], "warning_count": 0}
        self.patch = patch.object(archive, "audit_run", return_value=self.checked)
        self.auditor = self.patch.start()
        self.output = self.root / "archives" / "review"

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def write(self, name, value):
        path = self.run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def append(self, kind, payload):
        event = {"sequence": len(self.events) + 1, "type": kind, "payload": copy.deepcopy(payload),
                 "previous_sha256": self.events[-1]["event_sha256"] if self.events else "0" * 64}
        event["event_sha256"] = archive._hash(archive._canonical(event).encode("utf-8"))
        self.events.append(event)

    def action(self, step, action, arguments, observed):
        self.append("decision", {"step": step, "action": action, "arguments_json": json.dumps(arguments),
                                 "hypothesis": "Fixture hypothesis", "expected_result": "Fixture expectation",
                                 "analysis_summary": "Fixture choice"})
        self.append("tool_result", {"step": step, "action": action, "arguments": arguments,
                                    "executed": True, "result": observed})

    def flush(self):
        # Recompute chains only to construct valid alternate offline fixtures.
        original = copy.deepcopy(self.events)
        self.events = []
        for event in original:
            self.append(event["type"], event["payload"])
        (self.run / "events.jsonl").write_text("".join(archive._canonical(e) + "\n" for e in self.events), encoding="utf-8")
        self.state = {"frozen": True, "status": "frozen", "pending_tool": None,
                      "last_event_sequence": len(self.events), "last_event_sha256": self.events[-1]["event_sha256"]}
        self.write("state.json", self.state)

    def refuses(self):
        with self.assertRaises(archive.ArchiveError):
            archive.export_archive(self.root, self.run, self.output)
        self.assertFalse(self.output.exists())

    def test_verified_cards_bind_hypotheses_to_exact_observed_events(self):
        before = {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        data = archive.build_archive(self.root, self.run)
        first = data["cards"][0]
        self.assertEqual(first["decision_ref"]["sequence"], 1)
        self.assertEqual(first["observation_ref"]["event_sha256"], self.events[1]["event_sha256"])
        self.assertEqual(first["hypothesis"]["assessment"], "unassessed")
        self.assertEqual(data["new_model_calls"], 0)
        self.assertEqual(data["new_experiments"], 0)
        self.assertEqual(before, {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob("*") if p.is_file()})
        self.auditor.assert_called_once_with(self.root, self.run, require_frozen=True)

    def test_math_check_is_not_automatically_claimed_as_proof(self):
        data = archive.build_archive(self.root, self.run)
        witness = next(card for card in data["cards"] if card["kind"] == "mathematical_witness")
        self.assertEqual(witness["hypothesis"]["assessment"], "unassessed")
        self.assertEqual(witness["scientific_status"], "recorded_observation_not_automatic_claim_verification")
        self.assertTrue(any("形式化证明" in text for text in data["limitations"]))

    def test_critic_independent_prompt_does_not_claim_independent_model(self):
        data = archive.build_archive(self.root, self.run)
        critic = next(card for card in data["cards"] if card["kind"] == "critique")
        self.assertFalse(critic["different_model_claimed"])
        self.assertIn("not_independent_model", critic["role"])
        self.assertEqual(critic["event_ref"]["sequence"], 5)

    def test_html_escapes_untrusted_text_and_keeps_model_prompts_out(self):
        attack = '<script>alert("fixture")</script><img src=x onerror=alert(1)>'
        self.events[0]["payload"]["hypothesis"] = attack
        self.events[1]["payload"]["result"]["registered"] = attack
        self.append("model_request", {"prompt": "PRIVATE_FIXTURE_FULL_PROMPT_DO_NOT_EXPORT"})
        self.flush()
        result = archive.export_archive(self.root, self.run, self.output)
        text = (self.output / "index.html").read_text(encoding="utf-8")
        data = (self.output / "archive.json").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script", text.lower())
        self.assertNotIn("<img", text.lower())
        self.assertNotIn("PRIVATE_FIXTURE_FULL_PROMPT_DO_NOT_EXPORT", text + data)
        self.assertIn("Content-Security-Policy", text)
        self.assertEqual(result["cards"], 4)
        for line in (self.output / "SHA256SUMS.txt").read_text().splitlines():
            expected, name = line.split("  ")
            self.assertEqual(expected, hashlib.sha256((self.output / name).read_bytes()).hexdigest())

    def test_failed_historical_artifact_audit_produces_no_output(self):
        self.checked.update(exit_code=1, status="failed", integrity_ok=False)
        self.refuses()

    def test_changed_source_file_is_rejected_before_audit(self):
        self.source.write_text("# Changed frozen source\n", encoding="utf-8")
        self.refuses()
        self.auditor.assert_not_called()

    def test_mutation_during_audit_is_rejected(self):
        def mutate(*args, **kwargs):
            (self.run / "new_artifact.json").write_text("{}", encoding="utf-8")
            return self.checked
        self.auditor.side_effect = mutate
        self.refuses()

    def test_source_mutation_during_audit_is_rejected(self):
        def mutate(*args, **kwargs):
            self.source.write_text("# Changed during audit\n", encoding="utf-8")
            return self.checked
        self.auditor.side_effect = mutate
        self.refuses()

    def test_mutation_after_build_before_export_commit_is_rejected(self):
        original = archive._render
        def mutate(*args, **kwargs):
            (self.run / "late_change.json").write_text("{}", encoding="utf-8")
            return original(*args, **kwargs)
        with patch.object(archive, "_render", side_effect=mutate):
            self.refuses()

    def test_duplicate_tool_result_is_rejected(self):
        duplicate = self.events[1]
        self.append(duplicate["type"], duplicate["payload"])
        self.flush()
        self.refuses()

    def test_missing_tool_result_is_rejected(self):
        del self.events[1]
        self.flush()
        self.refuses()

    def test_changed_tool_arguments_are_rejected(self):
        self.events[1]["payload"]["arguments"] = {"method_id": "different"}
        self.flush()
        self.refuses()

    def test_budget_refused_step_is_preserved_without_claiming_execution(self):
        self.events[1]["payload"].update(executed=False, result={"ok": False, "error": "Fixture budget exhausted"})
        self.events[1]["payload"].pop("arguments")
        self.flush()
        card = archive.build_archive(self.root, self.run)["cards"][0]
        self.assertEqual(card["kind"], "attempt_not_executed")
        self.assertFalse(card["tool_executed"])
        self.assertEqual(card["arguments_binding"], "not_recorded_in_tool_result")
        self.assertEqual(card["observation"]["error"], "Fixture budget exhausted")

    def test_executed_failed_tool_is_not_claimed_as_successful_observation(self):
        self.events[1]["payload"]["result"] = {"ok": False, "error": "Fixture tool failure"}
        self.flush()
        card = archive.build_archive(self.root, self.run)["cards"][0]
        self.assertEqual(card["kind"], "tool_failure")
        self.assertTrue(card["tool_executed"])
        self.assertEqual(card["hypothesis"]["assessment"], "unassessed")

    def test_cross_drive_export_keeps_line_hash_references_without_private_paths(self):
        with patch.object(archive.os.path, "relpath", side_effect=ValueError("path is on mount D:")):
            archive.export_archive(self.root, self.run, self.output)
        text = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertIn("跨盘路径", text)
        self.assertIn(self.events[0]["event_sha256"], text)
        self.assertNotIn(str(self.run), text)

    def test_corrupt_event_chain_is_rejected(self):
        path = self.run / "events.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace("Fixture hypothesis", "Changed hypothesis", 1), encoding="utf-8")
        self.refuses()

    def test_duplicate_json_keys_are_rejected(self):
        (self.run / "state.json").write_text('{"frozen":true,"frozen":false}', encoding="utf-8")
        self.refuses()

    def test_numeric_overflow_in_protocol_is_rejected(self):
        (self.run / "protocol.json").write_text('{"source_hashes":{},"extra":1e999}', encoding="utf-8")
        self.refuses()

    def test_nonfrozen_pending_and_active_runs_are_rejected(self):
        for altered in ({"frozen": False}, {"pending_tool": {"action": "fixture"}}, {"status": "tool_failure_uncertain"}):
            with self.subTest(altered=altered):
                self.write("state.json", {**self.state, **altered})
                self.refuses()
        self.write("state.json", self.state)
        for marker in (".research.lock", "uncertain_tool_failure.json", "tools/uncertain_tool_failure.json"):
            with self.subTest(marker=marker):
                self.write(marker, {})
                self.refuses()
                (self.run / marker).unlink()

    def test_output_cannot_overwrite_existing_directory(self):
        self.output.mkdir(parents=True)
        keep = self.output / "user.txt"
        keep.write_text("Preserve me", encoding="utf-8")
        with self.assertRaises(archive.ArchiveError):
            archive.export_archive(self.root, self.run, self.output)
        self.assertEqual(keep.read_text(encoding="utf-8"), "Preserve me")

    def test_protected_output_locations_are_rejected(self):
        for protected in (self.run / "archive", self.root / "results" / "archive", self.root / "latex" / "archive", self.root):
            with self.subTest(path=str(protected)), self.assertRaises(archive.ArchiveError):
                archive.export_archive(self.root, self.run, protected)

    def test_source_path_escape_is_rejected(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("# Outside file", encoding="utf-8")
        self.write("protocol.json", {"source_hashes": {"../outside.py": hashlib.sha256(outside.read_bytes()).hexdigest()}})
        self.refuses()

    def test_symlinked_artifact_is_rejected(self):
        outside = self.root.parent / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            os.symlink(outside, self.run / "linked.json")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not available to this account")
        self.refuses()

    def test_input_size_limit_is_enforced_before_output(self):
        with patch.object(archive, "MAX_FILE_BYTES", 32):
            self.refuses()

    def test_audit_warnings_are_retained_without_scientific_success_claim(self):
        self.checked.update(warning_count=1, findings=[{"level": "warning", "code": "fixture_warning"}])
        data = archive.build_archive(self.root, self.run)
        self.assertEqual(data["verification"]["warning_count"], 1)
        self.assertEqual(data["verification"]["findings"][0]["code"], "fixture_warning")
        self.assertTrue(all(card.get("hypothesis", {}).get("assessment", "unassessed") == "unassessed" for card in data["cards"]))


if __name__ == "__main__":
    unittest.main()
