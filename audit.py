"""Read-only audit of saved study evidence, including historical tool results.

Exit codes: 0 integrity checks pass; 1 integrity failure; 3 --require-frozen was
requested but the run is not frozen. An in-progress pass is not study completion.
No model calls, numerical experiments, or holdout generation are performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(text):
    def reject(value):
        raise ValueError(f"Nonfinite JSON value: {value}")
    return json.loads(text, parse_constant=reject)


def _load(path):
    return _json(Path(path).read_text(encoding="utf-8-sig"))


def _snapshot(run):
    # Atomic state writes can still lag an event append. Retry only a changing
    # read, then compare a live state's valid chain prefix rather than inventing
    # corruption from an expected in-flight checkpoint lag.
    changing = False
    for _ in range(3):
        before = (run/"state.json").read_bytes()
        events = (run/"events.jsonl").read_text(encoding="utf-8")
        after = (run/"state.json").read_bytes()
        if before == after:
            return _json(after.decode("utf-8-sig")), events, changing
        changing = True
    return _json(after.decode("utf-8-sig")), (run/"events.jsonl").read_text(encoding="utf-8"), True


def _evidence_path(run, reference):
    normalized = str(reference).replace("\\", "/")
    # Rebasing intentionally ignores the old machine's absolute prefix.
    # Never follow it to an original copy outside the run being audited.
    if "/tools/" in normalized:
        normalized = normalized.rsplit("/tools/", 1)[1]
    elif normalized.startswith("tools/"):
        normalized = normalized[len("tools/"):]
    path = (run/"tools"/normalized).resolve()
    if not path.is_relative_to((run/"tools").resolve()):
        raise ValueError("Evidence path escapes the audited tools directory")
    return path


def _artifact_file(directory, artifacts, name, default):
    recorded = artifacts.get(name)
    basename = str(recorded).replace("\\", "/").rsplit("/", 1)[-1] if recorded else default
    if basename in ("", ".", ".."):
        raise ValueError("Invalid model artifact basename")
    return directory/basename


def _references(value):
    if isinstance(value, dict):
        if "evidence_path" in value and "evidence_sha256" in value:
            yield value
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def audit_run(root, run, require_frozen=False):
    root, run = Path(root).resolve(), Path(run).resolve()
    findings = []
    counts = dict(events=0, source_files=0, evidence_references=0, evidence_files=0,
                  model_requests=0, model_responses=0, completed_model_artifacts=0,
                  failed_model_requests=0, pending_model_requests=0,
                  replay_responses=0, raw_cli_events=0, unexpected_cli_tools=0)
    summary = dict(audit_version=1, run_directory=str(run), read_only=True,
                   findings=findings, counts=counts, frozen=False, run_status="unavailable")

    def issue(level, code, message, **details):
        findings.append(dict(level=level, code=code, message=str(message), **details))

    def ensure(condition, code, message, **details):
        if not condition:
            issue("error", code, message, **details)
        return bool(condition)

    try:
        state, event_text, changing = _snapshot(run)
        protocol = _load(run/"protocol.json")
    except (OSError, ValueError, TypeError) as error:
        issue("error", "missing_or_invalid_run", error)
        return _finish(summary, require_frozen)
    live = state.get("status") == "running" and not state.get("frozen", False)
    summary.update(frozen=bool(state.get("frozen", False)), run_status=state.get("status", "unknown"),
                   is_replay=bool(state.get("is_replay", False)))
    if changing:
        issue("warning", "concurrent_snapshot", "Run changed during reading; this is a bounded live snapshot.")
    events, hashes = [], {0: "0"*64}
    previous = "0"*64
    lines = event_text.splitlines()
    for number, line in enumerate(lines, 1):
        try:
            event = _json(line)
            recorded = event["event_sha256"]
            body = {key: value for key, value in event.items() if key != "event_sha256"}
            ensure(event.get("sequence") == number, "event_sequence", "Unexpected event sequence", line=number)
            ensure(event.get("previous_sha256") == previous, "event_previous_hash", "Broken previous-event link", line=number)
            ensure(recorded == _digest(body), "event_content_hash", "Event content hash mismatch", line=number)
            previous = recorded
            hashes[number] = recorded
            events.append(event)
        except (ValueError, KeyError, TypeError) as error:
            if live and number == len(lines) and not event_text.endswith("\n"):
                issue("warning", "partial_live_event", "Last in-flight event is incomplete; not counted.")
            else:
                issue("error", "invalid_event", error, line=number)
            break
    counts["events"] = len(events)
    saved_sequence = state.get("last_event_sequence")
    ensure(saved_sequence in hashes, "state_sequence", "State points outside the validated event chain")
    if saved_sequence in hashes:
        ensure(state.get("last_event_sha256") == hashes[saved_sequence], "state_event_hash", "State checkpoint hash differs from its event-chain position")
        if saved_sequence != len(events):
            issue("warning" if live else "error", "state_checkpoint_lag",
                  "State checkpoint does not equal the latest event", checkpoint=saved_sequence, latest=len(events))
    identity = state.get("identity", {})
    ensure(state.get("identity_sha256") == _digest(identity), "state_identity_hash", "State identity hash mismatch")
    initializations = [e["payload"] for e in events if e.get("type") == "decision" and e.get("payload", {}).get("decision") == "initialize"]
    ensure(len(initializations) == 1, "initialization_count", "Expected exactly one initialization event")
    if initializations:
        ensure(initializations[0].get("identity_sha256") == state.get("identity_sha256") and
               initializations[0].get("identity") == identity,
               "initialization_identity", "State identity differs from the original initialization event")

    source_hashes = protocol.get("source_hashes", {})
    ensure(bool(source_hashes) and source_hashes == identity.get("config", {}).get("source_hashes"),
           "source_manifest_identity", "Source protocol differs from initialization or is empty")
    expected = [root/"run_agent.py", root/"benchmark.py"]
    for folder in ("localization", "tools", "agent_runtime"):
        expected.extend((root/folder).glob("*.py"))
    missing = sorted(str(p.relative_to(root)).replace("\\", "/") for p in expected
                     if str(p.relative_to(root)).replace("\\", "/") not in source_hashes)
    ensure(not missing, "incomplete_source_manifest", "Critical sources are absent from protocol", files=missing)
    for relative, expected_hash in source_hashes.items():
        try:
            path = (root/relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Source path escapes the audited project")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            ensure(actual == expected_hash, "source_hash", "Source changed from initialization", path=relative)
            counts["source_files"] += 1
        except (OSError, ValueError) as error:
            issue("error", "source_read", error, path=relative)

    unique_evidence = set()
    references = []
    for event in events:
        if event.get("type") == "tool_result":
            references.extend(_references(event.get("payload", {}).get("result", {})))
    try:
        toolkit_state = _load(run/"tools"/"tool_state.json")
        references.extend(_references(toolkit_state.get("evaluations", {})))
    except (OSError, ValueError) as error:
        issue("error", "tool_state_read", error)
    for reference in references:
        counts["evidence_references"] += 1
        try:
            path = _evidence_path(run, reference["evidence_path"])
            key = (str(path), reference["evidence_sha256"])
            if key in unique_evidence:
                continue
            unique_evidence.add(key)
            result = _load(path)
            ensure(_digest(result) == reference["evidence_sha256"], "historical_evidence_hash",
                   "Historical evidence differs from its recorded digest", path=str(path.relative_to(run)))
            for name, value in result.items():
                if name != "rows" and name in reference:
                    ensure(_digest(reference[name]) == _digest(value), "historical_evidence_summary",
                           "Historical summary differs from evidence file", path=str(path.relative_to(run)), field=name)
        except (OSError, ValueError, KeyError, TypeError) as error:
            issue("error", "historical_evidence_read", error, reference=reference.get("evidence_path"))
    counts["evidence_files"] = len({path for path, _ in unique_evidence})

    requests, responses, failures = {}, {}, set()
    for event in events:
        payload = event.get("payload", {})
        kind = event.get("type")
        if kind in ("model_request", "model_response"):
            destination = requests if kind == "model_request" else responses
            request_id = payload.get("request_id")
            if ensure(isinstance(request_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]+", request_id)),
                      "model_request_id", "Unsafe or missing request id"):
                ensure(request_id not in destination, "duplicate_model_event", "Repeated model event id", request_id=request_id)
                destination[request_id] = payload
        if kind == "decision" and payload.get("decision") == "model_request_failed":
            failures.add(payload.get("request_id"))
    counts.update(model_requests=len(requests), model_responses=len(responses), failed_model_requests=len(failures))
    for request_id in responses:
        ensure(request_id in requests, "response_without_request", "Response has no preceding request", request_id=request_id)
    for request_id, request in requests.items():
        response = responses.get(request_id)
        pending = response is None and request_id not in failures
        if pending:
            counts["pending_model_requests"] += 1
            issue("warning" if live else "error", "pending_model_request",
                  "Model request has no final recorded outcome yet", request_id=request_id)
        try:
            _audit_model_call(run, request_id, request, response, pending and live,
                              request_id in failures, ensure, issue, counts)
        except (OSError, ValueError, TypeError, KeyError) as error:
            issue("warning" if pending and live else "error", "model_artifact_read", error, request_id=request_id)

    if summary["frozen"]:
        try:
            from benchmark import validate_frozen_run
            checked = validate_frozen_run(root, run)
            summary["frozen_integrity_check"] = checked["audit"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            issue("error", "frozen_integrity", error)
    else:
        summary["frozen_integrity_check"] = {"performed": False, "reason": "Run is not frozen; no holdout authorization implied."}
    return _finish(summary, require_frozen)


def _audit_model_call(run, request_id, request, response, pending, failed, ensure, issue, counts):
    directory = run/"model_calls"/request_id
    ensure(_text_hash(request.get("prompt", "")) == request.get("prompt_sha256"),
           "request_prompt_hash", "Recorded request prompt hash mismatch", request_id=request_id)
    prompt_path = directory/"prompt.txt"
    if not prompt_path.exists() and pending:
        return
    # read_text uses universal newlines, matching the original Python string
    # hash even when write_text translated LF to CRLF on Windows.
    prompt = prompt_path.read_text(encoding="utf-8-sig")
    ensure(_text_hash(prompt) == request.get("prompt_sha256"), "prompt_file_hash",
           "Raw prompt differs from the request hash", request_id=request_id)
    artifacts = response.get("artifacts", {}) if response else {}
    replay = bool(response and response.get("replay"))
    schema_path = directory/"schema.json"
    if schema_path.exists():
        ensure(_digest(_load(schema_path)) == request.get("schema_sha256"), "schema_file_hash",
               "Raw schema differs from its request hash", request_id=request_id)
    elif not replay and not pending:
        issue("error", "missing_schema", "Raw schema file is absent", request_id=request_id)
    if response:
        for field in ("prompt_sha256", "schema_sha256"):
            ensure(response.get(field) == request.get(field), "request_response_hash",
                   "Request and response hashes differ", request_id=request_id, field=field)
        saved = _load(_artifact_file(directory, artifacts, "response", "response.json"))
        ensure(_digest(saved) == _digest(response.get("response")), "response_file_hash",
               "Raw response differs from the recorded model response", request_id=request_id)
    if replay:
        audit = _load(directory/"replay_audit.json")
        ensure(audit.get("new_model_inference") is False, "replay_label", "Replay must not claim new inference", request_id=request_id)
        ensure(_digest(audit) == _digest(response.get("audit", {})), "replay_audit", "Replay audit file differs from recorded audit", request_id=request_id)
        counts["replay_responses"] += 1
        counts["completed_model_artifacts"] += 1
        return
    metadata_path = _artifact_file(directory, artifacts, "metadata", "metadata.json")
    if not metadata_path.exists() and pending:
        return
    metadata = _load(metadata_path)
    for field in ("request_id", "prompt_sha256", "schema_sha256"):
        expected = request_id if field == "request_id" else request.get(field)
        ensure(metadata.get(field) == expected, "model_metadata", "Metadata differs from request", request_id=request_id, field=field)
    if response:
        ensure(metadata.get("status") == "completed", "model_completion", "Recorded response is not backed by completed metadata", request_id=request_id)
        for field in ("usage", "unexpected_tools", "model_identifier", "model_identifier_source"):
            ensure(metadata.get(field) == response.get(field), "response_metadata", "Response differs from provider metadata", request_id=request_id, field=field)
    elif failed:
        issue("warning", "recorded_model_failure", "Failed model attempt is preserved; no successful response is inferred",
              request_id=request_id, provider_status=metadata.get("status"))
    raw_path = _artifact_file(directory, artifacts, "raw_events", "cli_events.jsonl")
    raw_events, invalid = [], 0
    for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = _json(line)
            if isinstance(event, dict): raw_events.append(event)
        except ValueError:
            invalid += 1
    from agent_runtime.providers import detect_unexpected_tools, extract_model_identifier
    unexpected = detect_unexpected_tools(raw_events)
    identifier, identifier_source = extract_model_identifier(raw_events,
                          (directory/"stderr.txt").read_text(encoding="utf-8", errors="replace"))
    ensure(len(raw_events) == metadata.get("raw_event_count"), "raw_event_count", "Raw CLI event count differs from metadata", request_id=request_id)
    ensure(unexpected == metadata.get("unexpected_tools"), "raw_tool_metadata", "Detected CLI tools differ from metadata", request_id=request_id)
    ensure(identifier == metadata.get("model_identifier") and identifier_source == metadata.get("model_identifier_source"),
           "raw_model_identifier", "Raw model identifier differs from metadata", request_id=request_id)
    usage = next((event.get("usage", {}) for event in reversed(raw_events) if event.get("type") == "turn.completed"), {})
    ensure(usage == metadata.get("usage"), "raw_usage", "CLI usage differs from metadata", request_id=request_id)
    counts["raw_cli_events"] += len(raw_events)
    counts["unexpected_cli_tools"] += len(unexpected)
    if invalid:
        issue("warning", "nonjson_cli_lines", "Raw CLI log includes unparsed lines; retained but not covered by semantic event counts", request_id=request_id, lines=invalid)
    if unexpected:
        issue("warning", "unexpected_cli_tools", "CLI used additional tools; do not claim complete tool isolation", request_id=request_id, count=len(unexpected))
    if response:
        counts["completed_model_artifacts"] += 1


def _finish(summary, require_frozen):
    errors = sum(item["level"] == "error" for item in summary["findings"])
    warnings = sum(item["level"] == "warning" for item in summary["findings"])
    summary.update(integrity_ok=errors == 0, error_count=errors, warning_count=warnings,
                   frozen_required=bool(require_frozen))
    summary["status"] = ("failed" if errors else "passed_frozen" if summary["frozen"] else
                         "passed_in_progress" if summary["run_status"] == "running" else "passed_unfrozen")
    summary["exit_code"] = 1 if errors else 3 if require_frozen and not summary["frozen"] else 0
    summary["limitations"] = [
        "Local consistency is not a digital signature and cannot defeat coordinated rewriting of all files.",
        "Checks do not prove scientific claims, industrial performance, or absence of unlogged external influence.",
        "Raw CLI stdout/stderr are not separately content-hashed by the provider; available semantic metadata is cross-checked.",
        "An in-progress snapshot verifies existing evidence only and does not authorize holdout access.",
    ]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run", type=Path, default=Path("runs/live_reference"))
    parser.add_argument("--require-frozen", action="store_true")
    args = parser.parse_args()
    run = args.run if args.run.is_absolute() else args.root/args.run
    result = audit_run(args.root, run, args.require_frozen)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(result["exit_code"])


if __name__ == "__main__":
    main()
