"""Run the hardened research runtime on an explicitly selected output directory.

This entry reuses the disclosed v3 development protocol. It is an engineering
revision, not a fresh hidden scientific study; published results are prior data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from agent_runtime import CodexCLIProvider, ProviderError
from hardened_runtime import (HARDENED_TOOL_SPEC, HardenedResearchController,
    HardenedRevisionToolkit, ValidatedProvider, VerifiedReplayProvider,
    controller_identity, exclusive_run_directory, preflight_resume)
from revision.agent import DISCOVERY_SEED, VALIDATION_SEED, SCENARIOS
from run_revision import GOAL as HISTORICAL_GOAL, make_config
from tools.common import digest, save


GOAL = (
    "本次是运行安全增强后的工程复现，沿用已经公开的第二轮开发协议。\n"
    "既有实验结果、测试结果和种子都已公开，不得声称本次是新的盲测或新的独立科学发现。\n"
    "开发工具仍仅提供 discovery/validation；本次不得请求或读取 holdout/test/calibration。\n"
    "以下为历史研究目标，其中关于 fresh/新数据 的措辞仅描述原始第二轮，不适用于本次工程运行。\n"
    "无法满足冻结条件时可以调用 finish，诚实记录停止理由；不授权测试评测。\n\n"
    + HISTORICAL_GOAL
)


def make_hardened_config(root, reasoning_effort):
    root = Path(root).resolve()
    config = make_config(root, reasoning_effort)
    config["study"] = "CrowdLocateLab-v3-engineering-hardening"
    config["goal_sha256"] = hashlib.sha256(GOAL.encode("utf-8")).hexdigest()
    config["runtime_safeguards"] = {
        "version": 2, "finite_json_arguments": True, "verified_replay_source": True,
        "checkpoint_tail_preflight": True, "honest_unfrozen_finish": True,
        "checkpoint_file_hashes": True, "exclusive_run_directory": True,
        "scientific_scope": "Disclosed development protocol reused; not a new blind scientific study.",
    }
    for relative in ("hardened_runtime.py", "run_research.py"):
        config["source_hashes"][relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; preserved reference runs are prohibited")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--provider", choices=("codex", "replay"), default="codex")
    parser.add_argument("--replay-source", type=Path)
    parser.add_argument("--strict-replay", action="store_true", help="Require identical prompt hashes; historical prompts usually differ")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--reasoning-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"), default="medium")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-experiments", type=int, default=8)
    parser.add_argument("--max-model-calls", type=int, default=28)
    args = parser.parse_args(argv)
    root, output = Path(__file__).resolve().parent, args.output.resolve()
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("--timeout must be finite and positive")
        if min(args.max_steps, args.max_experiments, args.max_model_calls) < 1:
            raise ValueError("Runtime budgets must be positive integers")
        if any(output.is_relative_to((root / "runs" / name).resolve())
               for name in ("live_reference", "revision_reference")):
            raise ValueError("Preserved reference runs cannot be modified by this entry")
        if output.exists() and not args.resume:
            raise ValueError("Use a new output directory or --resume to preserve evidence")
        if args.provider == "replay" and not args.replay_source:
            raise ValueError("--replay-source is required for replay")
        if args.provider != "replay" and (args.replay_source or args.strict_replay):
            raise ValueError("Replay options require --provider replay")
        config = make_hardened_config(root, args.reasoning_effort)
        raw_provider = (VerifiedReplayProvider(args.replay_source, args.strict_replay)
                        if args.provider == "replay" else
                        CodexCLIProvider(timeout=args.timeout, cwd=root, reasoning_effort=args.reasoning_effort))
        provider = ValidatedProvider(raw_provider)
        seed_hash = digest({"discovery": DISCOVERY_SEED, "validation": VALIDATION_SEED,
                            "scenarios": SCENARIOS, "per_scenario": [20, 30]})
        limits = dict(max_steps=args.max_steps, max_experiments=args.max_experiments,
                      max_model_calls=args.max_model_calls, max_retries=2, critic_every=3)
        identity = controller_identity(provider, GOAL, HARDENED_TOOL_SPEC, config, seed_hash, **limits)
        with exclusive_run_directory(output, resume=args.resume):
            if args.resume:
                # Check only after obtaining exclusivity, before either constructor.
                preflight_resume(output, identity, require_tool_state=True)
            toolkit = HardenedRevisionToolkit(root, output / "tools", resume=args.resume)
            if not args.resume:
                save(output / "protocol.json", config)
            controller = HardenedResearchController(provider, toolkit, output, GOAL, HARDENED_TOOL_SPEC,
                config, seed_hash, **limits, resume=args.resume)
            state = controller.run()
            print(json.dumps({"status": state["status"], "output": str(output), "steps": state["steps"],
                              "engineering_revision": True, "new_blind_study": False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, ProviderError) as error:
        parser.exit(2, "run_research: " + str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
