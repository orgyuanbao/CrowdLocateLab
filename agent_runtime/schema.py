"""Strict response contracts for the research planner and independent critic."""
from __future__ import annotations

import json
import math


ACTIONS = ("read_sources", "register_method", "run_experiment", "diagnose",
           "verify_math", "ablate", "freeze", "finish")

PLANNER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "analysis_summary": {"type": "string", "description": "Brief decision rationale, not private chain-of-thought."},
        "hypothesis": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "arguments_json": {"type": "string", "description": "A JSON object encoded as a string containing the chosen tool arguments."},
        "expected_result": {"type": "string"},
        "memory_note": {"type": "string"},
    },
    "required": ["analysis_summary", "hypothesis", "action", "arguments_json", "expected_result", "memory_note"],
}

CRITIC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scope_valid": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "recommended_next_step": {"type": "string"},
        "evidence_caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scope_valid", "issues", "recommended_next_step", "evidence_caveats"],
}


def _reject_nonfinite(value):
    raise ValueError(f"Non-finite JSON constant is forbidden: {value}")


def parse_json(text: str):
    return json.loads(text, parse_constant=_reject_nonfinite)


def validate_response(value: dict, schema: dict) -> dict:
    """Validate the small, fixed response schema without optional dependencies."""
    if not isinstance(value, dict):
        raise ValueError("Model response must be an object.")
    properties = schema["properties"]
    if set(value) != set(schema["required"]):
        raise ValueError("Response keys do not match the required schema exactly.")
    for name, rule in properties.items():
        item = value[name]
        expected = rule["type"]
        if expected == "string" and not isinstance(item, str):
            raise ValueError(f"{name} must be a string.")
        if expected == "boolean" and not isinstance(item, bool):
            raise ValueError(f"{name} must be a boolean.")
        if expected == "array" and (not isinstance(item, list) or not all(isinstance(x, str) for x in item)):
            raise ValueError(f"{name} must be an array of strings.")
        if "enum" in rule and item not in rule["enum"]:
            raise ValueError(f"Unsupported {name}: {item}")
    if "arguments_json" in value:
        arguments = parse_json(value["arguments_json"])
        if not isinstance(arguments, dict):
            raise ValueError("arguments_json must encode an object, not an array or scalar.")
    return value


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
