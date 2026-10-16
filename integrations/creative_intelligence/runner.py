#!/usr/bin/env python3
"""Run the unchanged upstream controller for one recorded research unit.

The wrapper supplies a separate workspace and project-authored Skills. It does
not substitute the existing CrowdLocateLab controller for the upstream agent.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
UPSTREAM_COMMIT = "b7d17249f94949351faefd7ae28a020f0623db93"
SAFE_CONFIG = {
    "model_reasoning_effort": "high", "sandbox_mode": "workspace-write",
    "approval_policy": "never", "web_search": "disabled",
    "sandbox_workspace_write": {"network_access": False},
    "agents": {"enabled": False},
}


class IntegrationError(RuntimeError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def reject_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise IntegrationError("Linked paths are not supported: {}".format(candidate))


def external_workspace(path: Path) -> Path:
    path = path.expanduser().absolute()
    reject_links(path)
    path = path.resolve()
    if path == CHECKOUT or CHECKOUT in path.parents or path in CHECKOUT.parents:
        raise IntegrationError("Research workspace must be separate from the project checkout")
    if path == path.parent:
        raise IntegrationError("A filesystem root cannot be a research workspace")
    return path


def evidence_root(workspace: Path) -> Path:
    result = workspace.with_name(workspace.name + ".evidence")
    reject_links(result)
    return result


def inventory(root: Path) -> dict[str, str]:
    result = {}
    size = 0
    for path in sorted(root.rglob("*")):
        reject_links(path)
        if path.is_dir():
            continue
        if not path.is_file():
            raise IntegrationError("Unsupported input file: {}".format(path))
        size += path.stat().st_size
        if path.stat().st_size > 32 * 1024 * 1024 or size > 128 * 1024 * 1024 or len(result) >= 2000:
            raise IntegrationError("Input file inventory exceeds integration limits")
        result[path.relative_to(root).as_posix()] = digest(path)
    return result


def verify_vendor() -> dict:
    provenance = read_json(HERE / "UPSTREAM.json")
    if provenance.get("commit") != UPSTREAM_COMMIT:
        raise IntegrationError("Unexpected upstream commit")
    if inventory(HERE / "vendor") != provenance["files_sha256"]:
        raise IntegrationError("Vendored upstream files differ from the recorded source")
    return provenance


def bind_controller(workspace: Path):
    """Load the original module and rebind its documented file locations only."""
    verify_vendor()
    spec = importlib.util.spec_from_file_location("crowdlocate_creative_upstream", HERE / "vendor" / "agent.py")
    if spec is None or spec.loader is None:
        raise IntegrationError("Cannot import upstream controller")
    module = importlib.util.module_from_spec(spec)
    # Avoid generating files in the preserved vendor tree.
    old_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode
    module.ROOT = workspace
    module.CONFIG_PATH = workspace / "config.toml"
    module.AGENTS_PATH = workspace / "AGENTS.md"
    module.SCHEMA_PATH = workspace / "schemas" / "turn-result.schema.json"
    if module.load_runtime_config() != SAFE_CONFIG:
        raise IntegrationError("Runtime configuration must match the invocation-only project settings")
    return module


def initialize(workspace: Path, run_id: str, problem: Path, materials: Path | None = None) -> dict:
    workspace = external_workspace(workspace)
    evidence = evidence_root(workspace)
    verify_vendor()
    if evidence.exists() or (workspace.exists() and any(workspace.iterdir())):
        raise IntegrationError("Initialization requires a new workspace and evidence directory")
    problem = problem.expanduser().absolute()
    reject_links(problem)
    if not problem.is_file() or not problem.read_text(encoding="utf-8").strip():
        raise IntegrationError("A nonempty UTF-8 problem file is required")
    if materials is not None:
        materials = materials.expanduser().absolute()
        reject_links(materials)
        if not materials.is_dir():
            raise IntegrationError("Materials must be a directory")
        inventory(materials)
    # Validate before creating directories without loading or executing the model.
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise IntegrationError("Invalid run id")
    workspace.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=False, exist_ok=False)
    shutil.copyfile(HERE / "runtime_config.toml", workspace / "config.toml")
    upstream_agents = (HERE / "vendor" / "AGENTS.md").read_bytes()
    boundaries = (HERE / "runtime_boundaries.md").read_bytes()
    (workspace / "AGENTS.md").write_bytes(upstream_agents + b"\n" + boundaries)
    shutil.copytree(HERE / "skills", workspace / ".agents" / "skills")
    shutil.copytree(HERE / "vendor" / "schemas", workspace / "schemas")
    if materials is None:
        (workspace / "materials").mkdir()
    else:
        shutil.copytree(materials, workspace / "materials")
    module = bind_controller(workspace)
    module.initialize_run(run_id, problem=str(problem))
    immutable = inventory(workspace)
    immutable.pop("runs/{}/state.json".format(run_id))
    metadata = {
        "format": "crowdlocate-creative-integration-v1",
        "upstream_repository": "https://github.com/DeepMathLLM/Creative-Intelligence",
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_agent_sha256": digest(HERE / "vendor" / "agent.py"),
        "runner_sha256": digest(Path(__file__)),
        "workspace": str(workspace), "run_id": run_id,
        "created_at": module.utc_now(),
        "skills_provenance": "CrowdLocateLab-authored replacements for missing upstream Skills",
        "immutable_inputs_sha256": immutable,
        "model_override": None, "config": SAFE_CONFIG,
    }
    write_json(evidence / "initialization.json", metadata)
    return metadata


def check_inputs(workspace: Path, run_id: str) -> dict:
    metadata = read_json(evidence_root(workspace) / "initialization.json")
    if metadata.get("workspace") != str(workspace) or metadata.get("run_id") != run_id:
        raise IntegrationError("Workspace or run id differs from its initialization record")
    if metadata.get("runner_sha256") != digest(Path(__file__)):
        raise IntegrationError("Integration runner changed after initialization; preserve the recorded version")
    for name, expected in metadata["immutable_inputs_sha256"].items():
        path = workspace / name
        reject_links(path)
        if not path.is_file() or digest(path) != expected:
            raise IntegrationError("Immutable research input changed: {}".format(name))
    return metadata


class Tee(io.TextIOBase):
    def __init__(self, live, saved):
        self.live, self.saved = live, saved

    def write(self, text):
        self.live.write(text)
        self.saved.write(text)
        self.saved.flush()
        return len(text)

    def flush(self):
        self.live.flush()
        self.saved.flush()


def advance(workspace: Path, run_id: str, prompt: str | None = None, *, controller=None) -> dict:
    workspace = external_workspace(workspace)
    metadata = check_inputs(workspace, run_id)
    module = controller if controller is not None else bind_controller(workspace)
    evidence = evidence_root(workspace)
    lock_path = evidence / "advance.lock"
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise IntegrationError("Another advance is active, or an interrupted invocation needs inspection") from exc
    lock.write(str(os.getpid()))
    lock.close()
    record = None
    original_parser = module.parse_codex_event
    original_builder = module.build_codex_command
    original_execute = module.execute_turn
    try:
        indices = [int(p.name[5:]) for p in evidence.glob("turn-*") if p.is_dir() and p.name[5:].isdigit()]
        record = evidence / "turn-{:03d}".format(max(indices, default=0) + 1)
        record.mkdir()
        before = module.read_json(module.state_path_for(run_id), {})
        write_json(record / "state_before.json", before)
        write_json(record / "input_digest.json", {"immutable_inputs_sha256": metadata["immutable_inputs_sha256"], "upstream_agent_sha256": metadata["upstream_agent_sha256"], "runner_sha256": metadata["runner_sha256"]})
        started = module.utc_now()
        observed = {"command_count": 0, "execute_turn_calls": 0, "process_exit_code": None, "stop_reason": None}
        diag = module.runtime_dir_for(run_id) / "codex-stderr.log"
        diagnostic_start = diag.stat().st_size if diag.exists() else 0
        with (record / "events.raw.jsonl").open("w", encoding="utf-8", newline="") as events, (record / "controller.stdout.log").open("w", encoding="utf-8") as stdout, (record / "controller.stderr.log").open("w", encoding="utf-8") as stderr:
            def parse_and_record(line):
                events.write(line)
                events.flush()
                return original_parser(line)

            def build_and_record(session_id, rendered_prompt, config=None):
                observed["command_count"] += 1
                if observed["command_count"] != 1:
                    raise IntegrationError("Only one upstream request is allowed per advance")
                command = original_builder(session_id, rendered_prompt, config)
                write_json(record / "command.json", {"argv": command, "cwd": str(workspace), "resumed_session_id": session_id})
                (record / "prompt.txt").write_text(rendered_prompt, encoding="utf-8")
                return command

            def execute_and_record(*args, **kwargs):
                observed["execute_turn_calls"] += 1
                if observed["execute_turn_calls"] != 1:
                    raise IntegrationError("Only one upstream process call is allowed per advance")
                result = original_execute(*args, **kwargs)
                observed["process_exit_code"] = result[0]
                observed["stop_reason"] = result[1]
                if result[3] is not None:
                    (record / "final_message.txt").write_text(result[3], encoding="utf-8")
                return result

            module.parse_codex_event = parse_and_record
            module.build_codex_command = build_and_record
            module.execute_turn = execute_and_record
            error = None
            code = 1
            try:
                with contextlib.redirect_stdout(Tee(sys.stdout, stdout)), contextlib.redirect_stderr(Tee(sys.stderr, stderr)):
                    code = module.command_run(argparse.Namespace(run_id=run_id, problem=None, prompt=prompt, once=True, keep_stop=False))
            except BaseException as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
            after = module.read_json(module.state_path_for(run_id), {})
            write_json(record / "state_after.json", after)
            if diag.exists():
                with diag.open("rb") as handle:
                    handle.seek(diagnostic_start)
                    (record / "codex.stderr.log").write_bytes(handle.read())
            integrity_error = None
            try:
                check_inputs(workspace, run_id)
            except (OSError, ValueError, RuntimeError) as exc:
                integrity_error = str(exc)
            structured = code == 0 and after.get("turn", 0) == before.get("turn", 0) + 1 and after.get("status") in {"paused", "complete", "blocked"}
            result = {
                "started_at": started, "finished_at": module.utc_now(), "controller_exit_code": code,
                **observed, "exception": error, "input_integrity_error": integrity_error,
                "accepted_structured_research_turn": bool(structured and error is None and integrity_error is None),
                "scientific_verification": "Not established by controller status; inspect saved research and independent checks",
                "state_status": after.get("status"), "session_id": after.get("session_id"),
                "evidence_directory": str(record),
            }
            write_json(record / "result.json", result)
        checksums = inventory(record)
        (record / "SHA256SUMS").write_text("".join("{}  {}\n".format(value, name) for name, value in sorted(checksums.items())), encoding="utf-8")
        return result
    finally:
        module.parse_codex_event = original_parser
        module.build_codex_command = original_builder
        module.execute_turn = original_execute
        lock_path.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "advance", "status", "validate"):
        item = commands.add_parser(name)
        item.add_argument("--workspace", type=Path, required=True)
        item.add_argument("--run-id", required=True)
        if name == "init":
            item.add_argument("--problem", type=Path, required=True)
            item.add_argument("--materials", type=Path)
        if name == "advance":
            item.add_argument("--prompt-file", type=Path)
    args = parser.parse_args(argv)
    try:
        workspace = external_workspace(args.workspace)
        if args.action == "init":
            result = initialize(workspace, args.run_id, args.problem, args.materials)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        check_inputs(workspace, args.run_id)
        if args.action == "advance":
            prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else None
            result = advance(workspace, args.run_id, prompt)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["accepted_structured_research_turn"] else 1
        module = bind_controller(workspace)
        if args.action == "status":
            return module.command_status(argparse.Namespace(run_id=args.run_id))
        return module.command_validate()
    except (OSError, ValueError, RuntimeError) as exc:
        print("Integration error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
