"""Finite-candidate ambiguity witnesses, using exact rational arithmetic.

CLI: python diagnostic_v1.py input.json [--output output.json]
JSON numbers are interpreted as their written decimal values, not hidden reals.
No optimization, candidate generation, or uniqueness certificate is implemented.
"""
import argparse
from fractions import Fraction as F
from itertools import combinations
import json
import math
from pathlib import Path


def rational(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, F)):
        raise ValueError("expected finite rational number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    try:
        return F(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("expected finite rational number") from exc


def vector(value, length):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("incorrect vector shape")
    return tuple(rational(v) for v in value)


def squared_distance(anchor, time, candidate):
    x, v = candidate[:2], candidate[2:]
    return sum((x[j] + time * v[j] - anchor[j]) ** 2 for j in range(2))


def fixed_row(q, y, epsilon):
    """Equivalent to |sqrt(q)-y| <= epsilon for y, epsilon >= 0."""
    lower = max(F(0), y - epsilon)
    upper = y + epsilon
    return lower * lower <= q <= upper * upper


def common_row(q, r, epsilon):
    """Equivalent to existence of a nonnegative shared y, without rounding."""
    # |sqrt(q)-sqrt(r)| <= 2 epsilon. Only square a nonnegative side.
    s = q + r - 4 * epsilon * epsilon
    return s <= 0 or s * s <= 4 * q * r


def sqrt_lower(q, denominator=10**9):
    """A certified rational lower bound, not a rounded square root."""
    return F(math.isqrt((q.numerator * denominator**2) // q.denominator), denominator)


def diagnose(data):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    mode = data.get("mode", "fixed_y")
    if mode not in ("fixed_y", "common_y"):
        raise ValueError("mode must be fixed_y or common_y")
    anchors_in = data.get("anchors")
    if not isinstance(anchors_in, list) or not anchors_in:
        raise ValueError("nonempty anchors required")
    n = len(anchors_in)
    anchors = [vector(a, 2) for a in anchors_in]
    times = vector(data.get("times"), n)
    eps = vector(data.get("epsilon"), n)
    if any(e < 0 for e in eps):
        raise ValueError("epsilon must be nonnegative")
    if mode == "fixed_y":
        y = vector(data.get("y"), n)
        if any(z < 0 for z in y):
            raise ValueError("y must be nonnegative")
    else:
        if "y" in data:
            raise ValueError("common_y must omit y: quantifiers differ")
        y = None
    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("at least one candidate required")
    candidates = [vector(c, 4) for c in raw_candidates]
    q = [[squared_distance(a, t, c) for a, t in zip(anchors, times)]
         for c in candidates]
    feasible = None if y is None else [
        all(fixed_row(qi, yi, ei) for qi, yi, ei in zip(row, y, eps))
        for row in q
    ]
    pairs = []
    for j, k in combinations(range(len(candidates)), 2):
        d2 = sum((candidates[j][h] - candidates[k][h]) ** 2 for h in range(2))
        if d2 == 0:
            continue  # Different velocity alone is not current-position ambiguity.
        if mode == "fixed_y":
            accepted = feasible[j] and feasible[k]
        else:
            accepted = all(common_row(a, b, e) for a, b, e in zip(q[j], q[k], eps))
        if accepted:
            pair = {
                "indices": [j, k], "position_separation_squared": str(d2),
                "worst_case_error_lower_bound_squared": str(d2 / 4),
                "worst_case_error_lower_bound_rational": str(sqrt_lower(d2 / 4)),
            }
            if mode == "common_y":
                # Each coordinate y_i=(sqrt(q_i)+sqrt(r_i))/2 is a shared datum.
                pair["common_y_exact_recipe"] = "y_i=(sqrt(q_j_i)+sqrt(q_k_i))/2"
            pairs.append(pair)
    return {
        "version": 1, "mode": mode,
        "status": "witness" if pairs else "no_witness_found",
        "arithmetic": "exact rational squared inequalities; no feasibility tolerance",
        "scope": "supplied candidates only; no uniqueness or safe-acceptance conclusion",
        "candidates": [[str(z) for z in c] for c in candidates],
        "squared_ranges": [[str(z) for z in row] for row in q],
        "fixed_y_feasible": feasible, "witnesses": pairs,
    }


def reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"),
                          parse_float=str, parse_constant=reject_constant)
        result = diagnose(data)
        code = 0
    except (ValueError, TypeError, OSError) as exc:
        result = {"status": "invalid_input", "error": str(exc)}
        code = 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
