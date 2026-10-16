"""Bounded synthetic construction probes, seed 20261015, no optimizer.

Run from workspace root: <python> runs/mirror-ambiguity-20261015/experiment_v1.py
Writes v1 fixtures, exact certificates, and numeric observations beside this file.
"""
from copy import deepcopy
from fractions import Fraction as F
from itertools import combinations
import json
from pathlib import Path
import random
import sys

import numpy as np
from diagnostic_v1 import diagnose, rational, common_row, fixed_row, reject_constant


HERE = Path(__file__).resolve().parent
SEED = 20261015


def determinant(matrix):
    a = [[F(z) for z in row] for row in matrix]
    result = F(1)
    for j in range(len(a)):
        pivot = next((i for i in range(j, len(a)) if a[i][j]), None)
        if pivot is None:
            return F(0)
        if pivot != j:
            a[j], a[pivot] = a[pivot], a[j]
            result = -result
        p = a[j][j]
        result *= p
        for i in range(j + 1, len(a)):
            ratio = a[i][j] / p
            for k in range(j + 1, len(a)):
                a[i][k] -= ratio * a[j][k]
    return result


def rank_observation(data):
    c = list(map(rational, data["candidates"][0]))
    rows = []
    for aa, tt in zip(data["anchors"], data["times"]):
        a, t = list(map(rational, aa)), rational(tt)
        z = [c[j] + t * c[j + 2] - a[j] for j in range(2)]
        rows.append(z + [t * z[0], t * z[1]])
    minor = next(((ids, determinant([rows[i] for i in ids]))
                  for ids in combinations(range(len(rows)), 4)
                  if determinant([rows[i] for i in ids]) != 0), None)
    b = np.array(rows, dtype=float)
    distances = np.linalg.norm(b[:, :2], axis=1)
    jacobian = b / distances[:, None]
    return {"nonzero_minor_rows": list(minor[0]) if minor else None,
            "unnormalized_minor_exact": str(minor[1]) if minor else "0",
            "jacobian_singular_values_float": np.linalg.svd(jacobian, compute_uv=False).tolist()}


def main():
    rng = random.Random(SEED)
    collinear = {"mode": "fixed_y", "anchors": [[-3, 0], [-6, 0], [3, 0], [0, 0]],
                 "times": [-3, -2, -1, 0], "y": [3, 5, 5, 3], "epsilon": [0]*4,
                 "candidates": [[0, 3, 1, 0], [0, -3, 1, 0]]}
    near = deepcopy(collinear)
    near["anchors"] = [[a[0], h] for a, h in zip(near["anchors"], ["1/100", "-1/100", "1/200", "-1/100"])]
    near["epsilon"] = ["1/100"]*4
    crossing = {"mode": "fixed_y", "anchors": [[-4, 0], [-3, 1], [-2, 0], ["-5/2", 0], [-4, 0]],
                "times": [-4, -3, -2, -1, 0], "y": [1, 1, 1, "5/2", 5], "epsilon": [0]*5,
                "candidates": [[0, 3, 1, 1], [0, -3, 1, -1]]}
    disambiguated_pair = deepcopy(collinear)
    disambiguated_pair["anchors"][-1] = [0, 1]
    disambiguated_pair["y"][-1] = 2
    incomplete = deepcopy(collinear)
    incomplete["candidates"] = incomplete["candidates"][:1]
    fixed_quantifier = {"mode": "fixed_y", "anchors": [[0, 0]], "times": [0], "y": [1],
                        "epsilon": [1], "candidates": [[1, 0, 0, 0], [3, 0, 0, 0]]}
    common_quantifier = deepcopy(fixed_quantifier)
    common_quantifier.pop("y")
    common_quantifier["mode"] = "common_y"
    common_below = deepcopy(common_quantifier)
    common_below["epsilon"] = ["999/1000"]
    zero = {"mode": "fixed_y", "anchors": [[0, 0]], "times": [0], "y": [0],
            "epsilon": [1], "candidates": [[0, 0, 0, 0], [1, 0, 0, 0]]}
    same_current = deepcopy(common_quantifier)
    same_current["candidates"] = [[1, 0, 0, 0], [1, 0, 2, 3]]
    equal_time = {"mode": "fixed_y", "anchors": [[0, 0], [2, 0], [0, 2]], "times": [-1]*3,
                  "y": [0, 2, 2], "epsilon": [0]*3,
                  "candidates": [[0, 0, 0, 0], [10, 20, 10, 20]]}
    cases = {"collinear_full_rank": collinear, "near_noncollinear": near,
             "noncollinear_crossing_full_rank": crossing,
             "pair_rejected_after_anchor_change": disambiguated_pair,
             "omitted_mirror_no_witness": incomplete, "fixed_y_quantifier": fixed_quantifier,
             "common_y_boundary": common_quantifier, "common_y_below_boundary": common_below,
             "zero_range": zero, "same_current_different_velocity": same_current,
             "equal_time_velocity_confounding": equal_time}
    expected = [True, True, True, False, False, False, True, False, True, False, True]
    records = {}
    for (name, data), witness in zip(cases.items(), expected):
        result = diagnose(data)
        assert (result["status"] == "witness") == witness, name
        records[name] = result
    ranks = {name: rank_observation(cases[name]) for name in
             ["collinear_full_rank", "near_noncollinear", "noncollinear_crossing_full_rank"]}
    assert all(z["unnormalized_minor_exact"] != "0" for z in ranks.values())

    invalid = {}
    mutations = {"negative_epsilon": ("epsilon", [-1]*4),
                 "negative_y": ("y", [-1]*4), "nan_time": ("times", [float("nan")]*4),
                 "infinite_anchor": ("anchors", [[float("inf"), 0]]*4),
                 "bad_shape": ("candidates", [[0, 1, 2]]),
                 "boolean_time": ("times", [True]*4), "missing_times": ("times", None),
                 "empty_candidates": ("candidates", [])}
    for name, (key, value) in mutations.items():
        data = deepcopy(collinear)
        data[key] = value
        try:
            diagnose(data)
        except ValueError as exc:
            invalid[name] = str(exc)
        else:
            raise AssertionError("invalid input accepted: " + name)
    try:
        diagnose(dict(common_quantifier, y=[2]))
    except ValueError as exc:
        invalid["mixed_quantifiers"] = str(exc)
    else:
        raise AssertionError("mixed quantifiers accepted")
    try:
        json.loads('{"x": NaN}', parse_constant=reject_constant)
    except ValueError as exc:
        invalid["json_nan"] = str(exc)
    else:
        raise AssertionError("JSON NaN accepted")

    # Finite identity probes use integer seed draws and deliberately modest sizes.
    # These floating evaluations support the identities; they are not certificates.
    max_identity_error, max_bound_excess, max_midpoint_error = 0., 0., 0.
    comparisons = 0
    for _ in range(200):
        tangent, s, h = [F(rng.randint(-50, 50), 10) for _ in range(3)]
        q, r = tangent*tangent + (s-h)**2, tangent*tangent + (s+h)**2
        d1, d2 = np.sqrt(float(q)), np.sqrt(float(r))
        delta = abs(d1-d2)
        identity = 0. if d1+d2 == 0 else float(4*abs(h*s))/(d1+d2)
        max_identity_error = max(max_identity_error, abs(delta-identity))
        max_bound_excess = max(max_bound_excess, delta-float(2*min(abs(h), abs(s))))
        e = F(rng.randint(0, 50), 10)
        # sqrt equality probes are supplemented by exact threshold cases above.
        assert common_row(q, r, e) == (delta <= float(2*e) + 1e-13)
        y = F(rng.randint(0, 100), 10)
        for qi, di in [(q, d1), (r, d2)]:
            assert fixed_row(qi, y, e) == (abs(di-float(y)) <= float(e)+1e-13)
            comparisons += 1
        mid = (d1+d2)/2
        max_midpoint_error = max(max_midpoint_error,
            abs(max(abs(d1-float(y)), abs(d2-float(y))) - (abs(mid-float(y))+delta/2)))

    output = {"version": 1, "stage": "construction_and_initial_probes_not_independent_verification",
              "seed": SEED, "python": sys.version, "numpy": np.__version__,
              "case_count": len(cases), "cases": records, "rank_observations": ranks,
              "invalid_input_rejections": invalid,
              "finite_identity_probes": {"count": 200, "fixed_row_comparisons": comparisons,
                 "max_reflection_identity_error_float": max_identity_error,
                 "max_geometric_bound_excess_float": max_bound_excess,
                 "max_midpoint_identity_error_float": max_midpoint_error}}
    (HERE / "fixtures_v1.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    for name in ["collinear_full_rank", "common_y_boundary", "fixed_y_quantifier"]:
        (HERE / ("input_" + name + "_v1.json")).write_text(json.dumps(cases[name], indent=2)+"\n", encoding="utf-8")
    (HERE / "results_v1.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"cases": {k: v["status"] for k, v in records.items()},
                      "rank_observations": ranks, "invalid_input_rejections": invalid,
                      "finite_identity_probes": output["finite_identity_probes"]},
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
