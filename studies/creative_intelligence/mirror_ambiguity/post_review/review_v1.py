"""Independent review of the archived upstream-agent first turn.

All CLI probes write only under this post_review directory. The first-turn
snapshot is read, never modified. This is distinct from independent_review.py,
which was a pre-run problem-planning check rather than review of agent output.
"""
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256
import importlib.util
from itertools import permutations
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE.parent / "snapshots" / "turn-001"
SOURCE = SNAPSHOT / "diagnostic_v1.py"


def source_hashes():
    return {p.name: sha256(p.read_bytes()).hexdigest()
            for p in SNAPSHOT.iterdir() if p.is_file()}


def determinant(matrix):
    n = len(matrix)
    return sum((-1) ** sum(p[i] > p[j] for i in range(n) for j in range(i+1, n))
               * math.prod(matrix[i][p[i]] for i in range(n))
               for p in permutations(range(n)))


def cli(input_path, output=None):
    command = [sys.executable, "-B", str(SOURCE), str(input_path)]
    if output is not None:
        command += ["--output", str(output)]
    result = subprocess.run(command, text=True, encoding="utf-8", capture_output=True,
                            cwd=HERE, timeout=30)
    return {"exit_code": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr}


def run():
    before = source_hashes()
    spec = importlib.util.spec_from_file_location("reviewed_diagnostic_v1", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixtures = json.loads((SNAPSHOT / "fixtures_v1.json").read_text(encoding="utf-8"))
    saved = json.loads((SNAPSHOT / "results_v1.json").read_text(encoding="utf-8"))
    reproduced = {name: module.diagnose(data) for name, data in fixtures.items()}
    assert all(reproduced[k] == saved["cases"][k] for k in fixtures)

    # Independent direct arithmetic on exact rational distances. No radical
    # squaring transformation from the implementation is reused in this oracle.
    rng = random.Random(2026101502)
    exact_counts = {"common_row": 0, "fixed_row": 0, "sqrt_lower": 0}
    for _ in range(3000):
        a = F(rng.randrange(0, 2000), rng.randrange(1, 100))
        b = F(rng.randrange(0, 2000), rng.randrange(1, 100))
        epsilon = F(rng.randrange(0, 1000), rng.randrange(1, 100))
        y = F(rng.randrange(0, 2000), rng.randrange(1, 100))
        assert module.common_row(a*a, b*b, epsilon) == (abs(a-b) <= 2*epsilon)
        assert module.fixed_row(a*a, y, epsilon) == (abs(a-y) <= epsilon)
        q = F(rng.randrange(0, 10**20), rng.randrange(1, 10**10))
        lower = module.sqrt_lower(q)
        step = F(1, 10**9)
        assert lower >= 0 and lower*lower <= q < (lower+step)**2
        for key in exact_counts:
            exact_counts[key] += 1

    # Exact nonzero minors checked using permutation expansion, independently
    # of the agent's Gaussian-elimination implementation.
    ranks = {}
    for name, record in saved["rank_observations"].items():
        case = fixtures[name]
        c = list(map(F, map(str, case["candidates"][0])))
        rows = []
        for anchor, time in zip(case["anchors"], case["times"]):
            a, t = list(map(F, map(str, anchor))), F(str(time))
            z = [c[j] + t*c[j+2] - a[j] for j in range(2)]
            rows.append(z + [t*z[0], t*z[1]])
        indices = record["nonzero_minor_rows"]
        exact_minor = determinant([rows[i] for i in indices])
        assert exact_minor == F(record["unnormalized_minor_exact"]) != 0
        array = np.asarray(rows, dtype=float)
        distances = np.linalg.norm(array[:, :2], axis=1)
        assert np.all(distances > 0)
        singular = np.linalg.svd(array/distances[:, None], compute_uv=False)
        assert np.allclose(singular, record["jacobian_singular_values_float"])
        ranks[name] = {"exact_minor": str(exact_minor),
                       "positive_distances": True, "jacobian_rank": 4,
                       "condition_float": float(singular[0]/singular[-1])}

    # A rational rotation plus translation checks coordinate-frame invariance
    # with non-axis-aligned and non-origin reflection geometry, without relying
    # on the agent's own reflection formula or its horizontal fixture coordinates.
    rotation = [[F(3,5), F(-4,5)], [F(4,5), F(3,5)]]
    shift = [F(7,3), F(-11,5)]
    def rotate(z):
        z = list(map(F, map(str, z)))
        return [sum(rotation[i][j]*z[j] for j in range(2)) for i in range(2)]
    def move(z):
        return [str(v+s) for v,s in zip(rotate(z), shift)]
    transformed = deepcopy(fixtures["noncollinear_crossing_full_rank"])
    transformed["anchors"] = list(map(move, transformed["anchors"]))
    transformed["candidates"] = [move(c[:2]) + list(map(str, rotate(c[2:])))
                                  for c in transformed["candidates"]]
    changed = module.diagnose(transformed)
    original = reproduced["noncollinear_crossing_full_rank"]
    assert changed["status"] == "witness"
    assert changed["squared_ranges"] == original["squared_ranges"]
    assert changed["witnesses"] == original["witnesses"]

    boundary = []
    for mode in ("fixed_y", "common_y"):
        for label, epsilon, expected in (
            ("below", "0." + "9"*80, "no_witness_found"),
            ("equal", "1.0", "witness"),
            ("above", "1." + "0"*79 + "1", "witness"),
        ):
            case = {"mode": mode, "anchors": [[0,0]], "times": [0],
                    "epsilon": ["EPSILON_TOKEN"],
                    "candidates": [[1,0,0,0], [3,0,0,0]]}
            if mode == "fixed_y":
                case["y"] = [2]
            # Actual unquoted JSON decimal token; verifies parser precision.
            text = json.dumps(case).replace('"EPSILON_TOKEN"', epsilon)
            path = HERE / f"boundary_{mode}_{label}.json"
            path.write_text(text, encoding="utf-8")
            result = cli(path)
            status = json.loads(result["stdout"])["status"]
            assert result["exit_code"] == 0 and status == expected
            boundary.append({"mode": mode, "side": label, "status": status,
                             "decimal_fraction_digits": 80 if label != "equal" else 1})

    # Engineering findings reproduced only on disposable post-review files.
    duplicate_path = HERE / "duplicate_y.json"
    duplicate_path.write_text('{"mode":"fixed_y","anchors":[[0,0]],"times":[0],'
                              '"epsilon":[1],"candidates":[[1,0,0,0],[3,0,0,0]],'
                              '"y":[1],"y":[2]}', encoding="utf-8")
    duplicate_result = cli(duplicate_path)
    duplicate_status = json.loads(duplicate_result["stdout"])["status"]
    assert duplicate_result["exit_code"] == 0 and duplicate_status == "witness"
    overwrite = HERE / "same_input_output_probe.json"
    original_text = json.dumps(fixtures["collinear_full_rank"])
    overwrite.write_text(original_text, encoding="utf-8")
    overwrite_before = sha256(overwrite.read_bytes()).hexdigest()
    overwrite_result = cli(overwrite, overwrite)
    overwrite_after = sha256(overwrite.read_bytes()).hexdigest()
    assert overwrite_result["exit_code"] == 0 and overwrite_before != overwrite_after
    assert json.loads(overwrite.read_text(encoding="utf-8"))["status"] == "witness"

    after = source_hashes()
    assert before == after
    return {
        "schema_version": 1,
        "provenance": "independent_post_run_review_of_upstream_agent_turn_001",
        "distinct_from_pre_run_check": "../independent_review.py is separate problem-planning evidence; this script reviews archived agent output.",
        "snapshot": "snapshots/turn-001", "reviewed_source_sha256": before,
        "snapshot_unchanged": True, "new_model_calls": 0,
        "core_math_status": "No substantive error found in A-D, candidate quantifiers, or the supplied exact counterexample within reviewed scope.",
        "reproduced_case_count": len(fixtures), "exact_oracle_checks": exact_counts,
        "independent_rank_checks": ranks,
        "rational_rotation_and_translation": "Exact squared ranges and witness lower bounds invariant.",
        "cli_decimal_boundary_checks": boundary,
        "findings": [
            {"id": "duplicate_json_keys", "severity": "engineering_input_ambiguity",
             "observed": "Repeated y keys accepted, last value wins and returns witness.",
             "exit_code": duplicate_result["exit_code"],
             "requested_revision": "Reject duplicate keys before interpreting a certificate input."},
            {"id": "existing_output_overwrite", "severity": "evidence_preservation",
             "observed": "--output equal to input overwrites the source input with a successful certificate.",
             "before_sha256": overwrite_before, "after_sha256": overwrite_after,
             "requested_revision": "Refuse existing output and input/output aliases, reserve new outputs without overwrite."},
            {"id": "unbounded_exact_arithmetic_work", "severity": "unresolved_resource_scope",
             "observed": "No documented input byte, count, rational-size, or pair-work limits; no huge-number denial-of-service probe executed.",
             "requested_revision": "Set explicit supported limits or report resource scope clearly; do not silently hang on hostile rationals."},
        ],
        "unfinished_scope": [
            "Finite supplied-candidate checker only; no candidate discovery or global decision procedure implemented.",
            "A positive witness can be arbitrarily small; meaningful operational rejection requires a declared error radius and comparison with D/2.",
            "Hard epsilon bounds and exact anchor/time data remain assumptions, not established for historical simulator or industrial data.",
            "No proof of global uniqueness from no_witness_found; current prose correctly avoids that claim.",
        ],
    }


if __name__ == "__main__":
    result = run()
    output = HERE / "review_v1.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "snapshot_unchanged": result["snapshot_unchanged"],
                      "math_status": result["core_math_status"],
                      "exact_checks": result["exact_oracle_checks"],
                      "findings": [x["id"] for x in result["findings"]]}, ensure_ascii=False))
