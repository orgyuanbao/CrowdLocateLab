"""Additive runtime safeguards; preserved research implementation stays unchanged.

This engineering revision does not retroactively change the two recorded studies
or establish fresh scientific evidence. Use run_research.py for new executions.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from agent_runtime import ResearchController, ReplayProvider, ProviderError
from agent_runtime.providers import sha256_text
from agent_runtime.schema import PLANNER_SCHEMA, CRITIC_SCHEMA, canonical_json, validate_response
from revision.agent import RevisionToolkit, TOOL_SPEC
from tools.toolkit import ResearchToolkit
from tools.common import canonical, digest, save


LOCK_NAME = ".research.lock"
CHECKPOINT_NAME = "checkpoint_manifest.json"
CHECKPOINT_FILES = ("state.json", "tools/tool_state.json", "protocol.json")
CHECKPOINT_VERSION = 2
UNCERTAIN_TOOL_NAME = "uncertain_tool_failure.json"


class EvidenceIntegrityError(ValueError):
    """Recorded research evidence cannot safely be used for another decision."""


def _contained_file(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise EvidenceIntegrityError("Evidence path must be relative to this run")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise EvidenceIntegrityError("Evidence path escapes this run")
    return path


def _tool_artifacts(output):
    """Raw byte inventory, including partial/extra files, below this run's tools."""
    output = Path(output).resolve()
    directory = _contained_file(output, "tools")
    artifacts = {}
    if directory.exists():
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(output).as_posix()
            resolved = _contained_file(output, relative)
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise EvidenceIntegrityError("Linked tool artifact is forbidden: " + relative)
            if resolved.is_file() and relative != "tools/tool_state.json":
                artifacts[relative] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return artifacts


def verified_evidence_snapshot(output, entry):
    """Read once; validate the original normalized JSON digest; return those bytes' data."""
    if not isinstance(entry, dict) or not isinstance(entry.get("evidence_sha256"), str):
        raise EvidenceIntegrityError("Missing recorded evidence digest")
    path = _contained_file(output, entry.get("evidence_path"))
    try:
        value = strict_json(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as error:
        raise EvidenceIntegrityError("Cannot read complete evidence: " + str(path)) from error
    # Existing research entries use tools.common.digest (normalized JSON), not
    # the raw-byte hashes independently captured by the checkpoint inventory.
    if digest(value) != entry["evidence_sha256"]:
        raise EvidenceIntegrityError("Recorded evidence digest mismatch: " + str(path))
    return value


def _verify_tool_references(output, state):
    if isinstance(state, dict):
        if "evidence_path" in state:
            verified_evidence_snapshot(output, state)
        for value in state.values():
            _verify_tool_references(output, value)
    elif isinstance(state, list):
        for value in state:
            _verify_tool_references(output, value)


@contextmanager
def exclusive_run_directory(output, *, resume=False):
    """Own one output directory across initialization, resume and all run writes.

    mkdir and exclusive file creation are atomic competing-process operations.
    An abandoned lock is never removed by age or PID checks: PID reuse cannot
    establish that a writer has stopped. Inspect the run before manual recovery.
    """
    output = Path(output).resolve()
    if resume:
        if not output.is_dir():
            raise ValueError("Cannot resume without an existing output directory")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            output.mkdir()
        except FileExistsError as error:
            raise ValueError("Output directory was already claimed; choose a new directory or explicit resume") from error
    lock_path = output / LOCK_NAME
    token = uuid.uuid4().hex
    metadata = {"owner_token": token, "pid": os.getpid(),
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "recovery": "Never auto-unlock by PID or age. Confirm the writer has stopped and inspect evidence before manual removal."}
    lock_bytes = (json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    try:
        stream = lock_path.open("xb")
    except FileExistsError as error:
        raise ValueError(
            "Run is locked: " + str(lock_path) + ". Another process may be writing. "
            "If the lock survived a crash, confirm the writer has stopped and inspect the saved evidence "
            "before manually removing it; locks are never automatically discarded."
        ) from error
    # If writing the lock itself fails, retain it for inspection rather than
    # allowing a second writer to proceed after an uncertain initialization.
    with stream:
        stream.write(lock_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        yield output
    finally:
        # Do not remove another writer's/replaced lock after external changes.
        if not lock_path.is_file() or lock_path.read_bytes() != lock_bytes:
            raise ValueError("Run lock changed unexpectedly; automatic removal refused. Inspect evidence manually.")
        lock_path.unlink()


def _checkpoint_files(output):
    return {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            if (output / name).is_file() else None for name in CHECKPOINT_FILES}


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("JSON numeric overflow/non-finite value is forbidden")
    return value


def _reject_constant(text):
    raise ValueError("Non-finite JSON constant is forbidden: " + text)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def strict_json(text):
    """Reject duplicate keys and overflow, including nested exponent numbers."""
    return json.loads(text, parse_float=_finite_float,
                      parse_constant=_reject_constant, object_pairs_hook=_unique_keys)


def validate_decision(response, schema):
    response = validate_response(response, schema)
    if "arguments_json" in response:
        if not isinstance(strict_json(response["arguments_json"]), dict):
            raise ValueError("arguments_json must encode an object")
    # Includes invalid Unicode and nonfinite future schema/metadata values.
    canonical_json(response).encode("utf-8")
    return response


def event_snapshot(path):
    """Verify and parse precisely the bytes used for a replay or resume check."""
    data = Path(path).read_bytes()
    if not data or not data.endswith(b"\n"):
        raise ValueError("A complete newline-terminated event log is required")
    events, previous = [], "0" * 64
    for sequence, line in enumerate(data.decode("utf-8").splitlines(), 1):
        event = strict_json(line)
        if not isinstance(event, dict):
            raise ValueError("Event must be a JSON object")
        body = {k: v for k, v in event.items() if k != "event_sha256"}
        if (type(event.get("sequence")) is not int or event["sequence"] != sequence
                or event.get("previous_sha256") != previous):
            raise ValueError("Event log ordering/hash chain failed validation")
        actual = sha256_text(canonical_json(body))
        if event.get("event_sha256") != actual:
            raise ValueError("Event log content hash failed validation")
        events.append(event)
        previous = actual
    return {"events": events, "last_sequence": len(events), "last_sha256": previous,
            "source_sha256": hashlib.sha256(data).hexdigest()}


class VerifiedReplayProvider(ReplayProvider):
    """Replay a verified, immutable in-memory snapshot; never infer new actions."""

    def __init__(self, source_run, strict_prompt=False):
        path = Path(source_run).resolve()
        self.path = path / "events.jsonl" if path.is_dir() else path
        self.strict_prompt = bool(strict_prompt)
        snapshot = event_snapshot(self.path)
        self.source_sha256 = snapshot["source_sha256"]
        self.records = []
        for event in snapshot["events"]:
            if event.get("type") != "model_response":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("response"), dict):
                raise ProviderError("Replay source contains an invalid model response")
            schema = CRITIC_SCHEMA if payload.get("purpose") == "critic" else PLANNER_SCHEMA
            validate_decision(payload["response"], schema)
            self.records.append(payload)
        if not self.records:
            raise ProviderError("Replay source contains no recorded model responses")
        self.index = 0
        self.settings = {"source_sha256": self.source_sha256,
                         "strict_prompt": self.strict_prompt,
                         "source_event_chain_verified": True}

    def complete(self, *args, **kwargs):
        result = super().complete(*args, **kwargs)
        result.audit.update(source_sha256=self.source_sha256, source_event_chain_verified=True,
            source_integrity_scope="Verified local hash chain; not a digital signature or authenticity guarantee.")
        directory = Path(result.artifacts["directory"])
        (directory / "replay_audit.json").write_text(
            json.dumps(result.audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return result


class ValidatedProvider:
    """Validate before the original controller records a successful response.

    Applies equally to live providers, replay and offline software fixtures.
    The preserved controller handles ProviderError with its existing call budget.
    """

    def __init__(self, provider):
        self.provider = provider
        self.name = getattr(provider, "name", type(provider).__name__)
        self.is_replay = bool(getattr(provider, "is_replay", False))
        self.settings = {**getattr(provider, "settings", {}), "response_contract_version": 2,
                         "finite_arguments_and_unique_keys": True}

    @property
    def index(self):
        return self.provider.index

    @index.setter
    def index(self, value):
        self.provider.index = value

    def complete(self, *args, **kwargs):
        result = self.provider.complete(*args, **kwargs)
        schema = args[1] if len(args) > 1 else kwargs["schema"]
        try:
            validate_decision(result.response, schema)
            canonical_json(asdict(result)).encode("utf-8")
        except (ValueError, TypeError, OverflowError) as error:
            raise ProviderError("Strict response validation failed: " + str(error)) from error
        return result


def controller_identity(provider, goal, tool_spec, config, seed_hash, *, max_steps=24,
                        max_experiments=9, max_model_calls=40, max_retries=2, critic_every=3):
    """Match the preserved controller's identity contract before it can mutate."""
    return {"runtime_version": 1, "goal": goal, "tool_spec": tool_spec, "config": config,
            "seed_hash": seed_hash, "provider": getattr(provider, "name", type(provider).__name__),
            "provider_settings": getattr(provider, "settings", {}), "max_steps": max_steps,
            "max_experiments": max_experiments, "max_model_calls": max_model_calls,
            "max_retries": max_retries, "critic_every": critic_every}


def preflight_resume(output, expected_identity=None, *, require_tool_state=False):
    """Fail closed before constructing either a toolkit or controller.

    An interrupted/inconsistent checkpoint requires manual evidence inspection;
    this function does not reconstruct lost events or rerun uncertain tools.
    """
    output = Path(output)
    if (output / "tools" / UNCERTAIN_TOOL_NAME).exists():
        raise ValueError("Resume rejected: uncertain tool failure requires manual evidence inspection")
    state = strict_json((output / "state.json").read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Saved state must be an object")
    manifest_path = output / CHECKPOINT_NAME
    if not manifest_path.is_file():
        raise ValueError("Resume rejected: hardened checkpoint manifest missing; historical runs cannot be resumed by this entry")
    manifest = strict_json(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("version") != CHECKPOINT_VERSION
            or not isinstance(manifest.get("files"), dict)
            or set(manifest["files"]) != set(CHECKPOINT_FILES)
            or not isinstance(manifest.get("tool_artifacts"), dict)):
        raise ValueError("Resume rejected: invalid/old checkpoint manifest; version 2 is required")
    actual_files = _checkpoint_files(output)
    for name in CHECKPOINT_FILES:
        if manifest["files"][name] != actual_files[name]:
            raise ValueError("Resume rejected: checkpoint file hash mismatch: " + name)
    if manifest["tool_artifacts"] != _tool_artifacts(output):
        raise ValueError("Resume rejected: tool artifact inventory/hash mismatch")
    tool_state_path = output / "tools/tool_state.json"
    if tool_state_path.is_file():
        _verify_tool_references(output / "tools", strict_json(tool_state_path.read_text(encoding="utf-8")))
    if (manifest.get("last_event_sequence") != state.get("last_event_sequence")
            or manifest.get("last_event_sha256") != state.get("last_event_sha256")):
        raise ValueError("Resume rejected: checkpoint manifest and saved event anchor disagree")
    snapshot = event_snapshot(output / "events.jsonl")
    if (type(state.get("last_event_sequence")) is not int
            or state["last_event_sequence"] != snapshot["last_sequence"]
            or state.get("last_event_sha256") != snapshot["last_sha256"]):
        raise ValueError("Resume rejected: state checkpoint does not match the event-log tail")
    identity = state.get("identity")
    if not isinstance(identity, dict) or sha256_text(canonical_json(identity)) != state.get("identity_sha256"):
        raise ValueError("Resume rejected: saved identity hash mismatch")
    initializations = [e.get("payload") for e in snapshot["events"] if e.get("type") == "decision"
                       and isinstance(e.get("payload"), dict) and e["payload"].get("decision") == "initialize"]
    if (len(initializations) != 1 or initializations[0].get("identity") != identity
            or initializations[0].get("identity_sha256") != state["identity_sha256"]):
        raise ValueError("Resume rejected: initialization identity mismatch")
    if expected_identity is not None and sha256_text(canonical_json(expected_identity)) != state["identity_sha256"]:
        raise ValueError("Resume rejected: configuration, provider, replay source or budgets differ")
    if state.get("pending_tool"):
        raise ValueError("Resume rejected: interrupted tool has uncertain side effects; inspect evidence")
    requested, completed = set(), set()
    for event in snapshot["events"]:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("Resume rejected: malformed event payload")
        if event.get("type") == "model_request":
            requested.add(payload.get("request_id"))
        elif (event.get("type") == "model_response" or event.get("type") == "decision"
              and payload.get("decision") == "model_request_failed"):
            completed.add(payload.get("request_id"))
    if requested - completed:
        raise ValueError("Resume rejected: interrupted model request has no recorded outcome; inspect saved artifacts")
    if require_tool_state:
        protocol = strict_json((output / "protocol.json").read_text(encoding="utf-8"))
        tool_state = strict_json((output / "tools/tool_state.json").read_text(encoding="utf-8"))
        if not isinstance(tool_state, dict) or protocol != identity.get("config"):
            raise ValueError("Resume rejected: toolkit state missing or protocol differs from initialization")
        if bool(tool_state.get("frozen")) != bool(state.get("frozen")):
            raise ValueError("Resume rejected: controller and toolkit disagree about freeze")
    return state


class HardenedResearchController(ResearchController):
    def _save(self):
        uncertain = getattr(self.toolkit, "_uncertain_tool_failure", None)
        if uncertain:
            # The frozen controller normally clears pending_tool after catching
            # a tool exception. Preserve uncertainty at every save, before the
            # loop can make another model request or label this resumable.
            self.state["pending_tool"] = {"action": uncertain["action"],
                                          "uncertain_failure": uncertain}
            self.state["status"] = "tool_failure_uncertain"
        super()._save()
        manifest = {"version": CHECKPOINT_VERSION, "files": _checkpoint_files(self.output),
                    "tool_artifacts": _tool_artifacts(self.output),
                    "last_event_sequence": self.state["last_event_sequence"],
                    "last_event_sha256": self.state["last_event_sha256"],
                    "scope": "Local accidental-change detection; not a signature or protection against coordinated rewriting of all evidence."}
        target = self.output / CHECKPOINT_NAME
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                             encoding="utf-8", newline="\n")
        os.replace(temporary, target)

    def __init__(self, provider, toolkit, output, goal, tool_spec, config, seed_hash,
                 max_steps=24, max_experiments=9, max_model_calls=40, max_retries=2,
                 critic_every=3, resume=False):
        provider = provider if isinstance(provider, ValidatedProvider) else ValidatedProvider(provider)
        limits = dict(max_steps=max_steps, max_experiments=max_experiments,
                      max_model_calls=max_model_calls, max_retries=max_retries, critic_every=critic_every)
        for name, value in limits.items():
            minimum, maximum = (0, 2) if name == "max_retries" else (1, None)
            if type(value) is not int or value < minimum or maximum is not None and value > maximum:
                raise ValueError("Invalid integer runtime limit: " + name)
        if resume:
            identity = controller_identity(provider, goal, tool_spec, config, seed_hash, **limits)
            preflight_resume(output, identity)
        elif (Path(output) / "state.json").exists() or (Path(output) / "events.jsonl").exists():
            raise ValueError("Existing run found; use a fresh output or explicit resume")
        super().__init__(provider, toolkit, output, goal, tool_spec, config, seed_hash,
                         **limits, resume=resume)


class HonestFinishMixin:
    """Add evidence checks and failure quarantine without editing frozen tools."""

    def _quarantine_failure(self, action, error, state_changed, files_changed, durable_changed):
        marker = {"action": action, "error": f"{type(error).__name__}: {error}",
                  "state_changed": state_changed, "artifacts_changed": files_changed,
                  "durable_state_changed": durable_changed,
                  "recovery": "Manual evidence inspection required; no automatic resume or retry."}
        self._uncertain_tool_failure = marker
        try:
            save(self.output / UNCERTAIN_TOOL_NAME, marker)
        except OSError:
            # Even if storage fails, the controller keeps its durable
            # pre-dispatch pending checkpoint and/or this in-memory flag.
            pass

    def _dispatch_snapshot(self):
        return (canonical(self.state), _tool_artifacts(self.output.parent),
                self.path.read_bytes())

    def dispatch(self, action, args):
        if getattr(self, "_uncertain_tool_failure", None) or (self.output / UNCERTAIN_TOOL_NAME).exists():
            if not getattr(self, "_uncertain_tool_failure", None):
                # A marker left by an earlier process (or detected during this
                # run) must also stop this controller; preserve its disk bytes.
                self._uncertain_tool_failure = {"action": action,
                    "error": "Existing uncertain tool failure marker requires inspection"}
            raise EvidenceIntegrityError("Uncertain tool failure: inspect evidence before further actions")
        try:
            before_state, before_files, before_durable = self._dispatch_snapshot()
        except Exception as error:
            # Cannot establish the baseline, so absence of side effects cannot
            # be established either. None means unknown, never "unchanged".
            self._quarantine_failure(action, error, None, None, None)
            raise
        try:
            _verify_tool_references(self.output, self.state)
            return super().dispatch(action, args)
        except Exception as error:
            try:
                state_changed = canonical(self.state) != before_state
                files_changed = _tool_artifacts(self.output.parent) != before_files
                # Include durable toolkit counters as well as the artifact set.
                durable_changed = (self.path.read_bytes() if self.path.exists() else None) != before_durable
            except Exception:
                state_changed = files_changed = durable_changed = True
            if state_changed or files_changed or durable_changed or isinstance(error, EvidenceIntegrityError):
                self._quarantine_failure(action, error, state_changed, files_changed, durable_changed)
            raise

    def tool_diagnose(self, method_id, split="discovery", top_k=4):
        key = f"{split}:{method_id}"
        if key not in self.state["evaluations"]:
            raise ValueError("Run requested method/split first")
        count = min(int(top_k), 6)
        reference = self.state["evaluations"][key]
        result = verified_evidence_snapshot(self.output, reference)
        ordered = sorted(result["rows"], key=lambda row: row["error_m"], reverse=True)
        frequencies = {}
        for row in result["rows"]:
            scenario = row["scenario"]
            model = row["diagnostics"].get("selected_model", "unknown")
            frequencies.setdefault(scenario, {})
            frequencies[scenario][model] = frequencies[scenario].get(model, 0) + 1
        self.state["diagnoses"] += 1
        observation = {"worst_cases": ordered[:count], "selection_frequencies": frequencies,
                       "warning": "Worst-case examples are diagnostic evidence, not theorems or guarantees about other cases."}
        if self.state.get("revision_version") == 3:
            diagnosis = {"method_id": method_id, "split": split,
                         "diagnosis_number": self.state["diagnoses"],
                         "evidence_path": reference["evidence_path"],
                         "evidence_sha256": reference["evidence_sha256"]}
            self.state["diagnosis_history"].append(diagnosis)
            observation["revision_diagnosis"] = diagnosis
        return observation

    def tool_finish(self, summary):
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("A nonempty scientific stopping reason is required")
        return {"complete": True, "summary": summary, "frozen": bool(self.state.get("frozen")),
                "test_authorized": False, "note": "Stopping does not freeze a method or authorize holdout evaluation."}


class HardenedResearchToolkit(HonestFinishMixin, ResearchToolkit):
    pass


class HardenedRevisionToolkit(HonestFinishMixin, RevisionToolkit):
    pass


HARDENED_TOOL_SPEC = copy.deepcopy(TOOL_SPEC)
HARDENED_TOOL_SPEC["finish"]["description"] = (
    "Stop honestly with a nonempty reason when no valid freeze is possible. "
    "The run remains unfrozen and cannot authorize holdout evaluation."
)
