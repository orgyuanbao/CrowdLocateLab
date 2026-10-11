"""Real Codex CLI calls and explicitly labelled replay of saved responses.

The CLI may expose its own tools despite the prompt's no-tools instruction.
Raw events are retained and tool-like events are flagged; this is not claimed
to be a completely isolated model-only API. No authentication files are read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .schema import canonical_json, parse_json, validate_response


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ProviderResult:
    response: dict
    provider: str
    request_id: str
    elapsed_seconds: float
    prompt_sha256: str
    schema_sha256: str
    artifacts: dict = field(default_factory=dict)
    unexpected_tools: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    replay: bool = False
    audit: dict = field(default_factory=dict)
    model_identifier: str | None = None
    model_identifier_source: str = "not_reported_by_cli"


class ProviderError(RuntimeError):
    """A failed model call; the controller may retry, never synthesize a result."""


def detect_unexpected_tools(events: list[dict]) -> list[dict]:
    """Detect documented CLI tool items and conservative unknown tool events."""
    flagged = []
    seen = set()
    known = {"command_execution", "mcp_tool_call", "web_search", "file_change",
             "tool_call", "function_call", "computer_call", "image_generation"}
    for event in events:
        item = event.get("item", {}) if isinstance(event.get("item", {}), dict) else {}
        kind = str(item.get("type", event.get("type", "")))
        if kind in known or any(marker in kind.lower() for marker in ("tool_call", "function_call", "command_execution", "web_search")):
            identity = str(item.get("id", canonical_json(event)))
            if identity in seen:
                continue
            seen.add(identity)
            # Keep a small index into the raw log rather than duplicate any tool output.
            flagged.append({"event_type": event.get("type"), "item_type": kind,
                            "item_id": item.get("id"), "name": item.get("tool", item.get("name"))})
    return flagged


def extract_model_identifier(events, stderr_text):
    """Use only explicitly reported model fields; never infer from response prose."""
    def inspect(value, prefix="event"):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("model", "model_id", "model_slug") and isinstance(child, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", child):
                    return child, f"cli_json_field:{prefix}.{key}"
                if isinstance(child, (dict, list)):
                    found = inspect(child, f"{prefix}.{key}")
                    if found:
                        return found
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found = inspect(child, f"{prefix}[{index}]")
                if found:
                    return found
        return None
    for event in events:
        found = inspect(event)
        if found:
            return found
    match = re.search(r"(?mi)^\s*(?:model|model_id|model_slug)\s*:\s*([A-Za-z0-9._:/-]{1,160})\s*$", stderr_text)
    if match:
        return match.group(1), "cli_stderr_model_header"
    return None, "not_reported_by_cli"


class CodexCLIProvider:
    """Run the signed-in user's configured default model, with no model override."""
    name = "codex_cli"
    is_replay = False

    def __init__(self, executable=None, timeout=180, cwd=None, reasoning_effort=None):
        self.executable = executable or os.environ.get("CODEX_EXECUTABLE") or shutil.which("codex")
        if not self.executable:
            raise ProviderError("Codex executable was not found. Set CODEX_EXECUTABLE or install/sign in to Codex CLI; no scripted fallback is available.")
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout must be positive.")
        self.cwd = Path(cwd).resolve() if cwd else None
        if reasoning_effort not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise ValueError("Unsupported reasoning_effort setting.")
        self.reasoning_effort = reasoning_effort
        self.settings = {"reasoning_effort": reasoning_effort, "model_override": None}

    def complete(self, prompt: str, schema: dict, request_id: str,
                 artifact_dir: Path, timeout_s=None) -> ProviderResult:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", request_id):
            raise ValueError("Unsafe request id.")
        directory = Path(artifact_dir).resolve() / request_id
        directory.mkdir(parents=True, exist_ok=False)
        prompt_path = directory / "prompt.txt"
        schema_path = directory / "schema.json"
        response_path = directory / "response.json"
        stdout_path = directory / "cli_events.jsonl"
        stderr_path = directory / "stderr.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [str(self.executable), "exec", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--json", "--output-schema", str(schema_path),
                   "-o", str(response_path), "-"]
        if self.reasoning_effort is not None:
            command[2:2] = ["-c", f'model_reasoning_effort="{self.reasoning_effort}"']
        started = time.monotonic()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        metadata = {"provider": self.name, "request_id": request_id,
                    "prompt_sha256": sha256_text(prompt),
                    "schema_sha256": sha256_text(canonical_json(schema)),
                    "model_selection": "user_configured_default; no --model override",
                    "reasoning_effort_override": self.reasoning_effort,
                    "sandbox_requested": "read-only", "complete_tool_isolation_claimed": False,
                    "timeout_seconds": float(timeout_s or self.timeout)}
        try:
            # stdout/stderr are files to avoid pipe deadlocks and retain partial logs on timeout.
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(command, cwd=str(self.cwd or directory),
                                           stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                           creationflags=flags)
                try:
                    process.communicate(input=prompt.encode("utf-8"), timeout=timeout_s or self.timeout)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    metadata["status"] = "timeout"
                    raise ProviderError(f"Codex model request timed out; partial logs: {directory}") from exc
            metadata["returncode"] = process.returncode
            if process.returncode:
                metadata["status"] = "failed"
                raise ProviderError(f"Codex exited with code {process.returncode}; see {stderr_path}. No fallback response was generated.")
            if not response_path.is_file():
                raise ProviderError(f"Codex produced no response file; inspect {directory}.")
            response = validate_response(parse_json(response_path.read_text(encoding="utf-8-sig")), schema)
            metadata["status"] = "completed"
        except (OSError, ValueError) as exc:
            metadata["status"] = "failed"
            raise ProviderError(f"Codex request failed: {type(exc).__name__}: {exc}") from exc
        finally:
            metadata["elapsed_seconds"] = time.monotonic() - started
            events = []
            if stdout_path.exists():
                for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = parse_json(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except ValueError:
                        continue
            metadata["unexpected_tools"] = detect_unexpected_tools(events)
            metadata["raw_event_count"] = len(events)
            metadata["usage"] = next((event.get("usage", {}) for event in reversed(events)
                                      if event.get("type") == "turn.completed"), {})
            stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
            identifier, identifier_source = extract_model_identifier(events, stderr_text)
            metadata["model_identifier"] = identifier
            metadata["model_identifier_source"] = identifier_source
            metadata["cli_error_event_count"] = sum(event.get("type") == "error" for event in events)
            (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return ProviderResult(response=response, provider=self.name, request_id=request_id,
                              elapsed_seconds=metadata["elapsed_seconds"], prompt_sha256=metadata["prompt_sha256"],
                              schema_sha256=metadata["schema_sha256"], unexpected_tools=metadata["unexpected_tools"],
                              model_identifier=metadata["model_identifier"], model_identifier_source=metadata["model_identifier_source"],
                              usage=metadata["usage"], artifacts={"directory": str(directory),
                              "response": str(response_path), "raw_events": str(stdout_path),
                              "metadata": str(directory / "metadata.json")})


class ReplayProvider:
    """Replay recorded model responses for audit, never generate new decisions.

    Input is a controller run directory or its events.jsonl. Source responses
    remain explicitly labelled as replay; exhaustion is a hard error.
    """
    name = "replay"
    is_replay = True

    def __init__(self, source_run, strict_prompt=False):
        path = Path(source_run)
        self.path = path / "events.jsonl" if path.is_dir() else path
        self.strict_prompt = bool(strict_prompt)
        self.records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = parse_json(line)
            if event.get("type") == "model_response":
                payload = event.get("payload", event)
                if isinstance(payload.get("response"), dict):
                    self.records.append(payload)
        if not self.records:
            raise ProviderError("Replay source contains no recorded model_response events.")
        self.index = 0

    def complete(self, prompt: str, schema: dict, request_id: str,
                 artifact_dir: Path, timeout_s=None) -> ProviderResult:
        if self.index >= len(self.records):
            raise ProviderError("Saved response replay exhausted; no live or scripted fallback is permitted.")
        record = self.records[self.index]
        response = validate_response(record["response"], schema)
        prompt_hash = sha256_text(prompt)
        source_prompt_hash = record.get("prompt_sha256")
        matched = prompt_hash == source_prompt_hash
        if self.strict_prompt and not matched:
            raise ProviderError("Replay prompt hash differs from the saved request.")
        schema_hash = sha256_text(canonical_json(schema))
        if record.get("schema_sha256") and record["schema_sha256"] != schema_hash:
            raise ProviderError("Replay response schema differs from the saved request.")
        directory = Path(artifact_dir).resolve() / request_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
        (directory / "response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        audit = {"source": str(self.path.resolve()), "source_index": self.index,
                 "source_request_id": record.get("request_id"), "source_provider": record.get("provider"),
                 "prompt_hash_matches": matched, "new_model_inference": False}
        (directory / "replay_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        self.index += 1
        return ProviderResult(response=response, provider=self.name, request_id=request_id,
                              elapsed_seconds=0., prompt_sha256=prompt_hash, schema_sha256=schema_hash,
                              artifacts={"directory": str(directory)}, replay=True, audit=audit,
                              model_identifier=record.get("model_identifier"),
                              model_identifier_source=record.get("model_identifier_source", "not_reported_by_cli"),
                              unexpected_tools=record.get("unexpected_tools", []))
