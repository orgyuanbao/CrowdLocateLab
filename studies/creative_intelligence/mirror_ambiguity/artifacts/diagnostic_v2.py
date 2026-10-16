"""Finite-candidate ambiguity witnesses, using exact rational arithmetic.

CLI: python diagnostic_v2.py input.json [--output NEW_output.json]
JSON numbers are interpreted as their written decimal values, not hidden reals.
No optimization, candidate generation, or uniqueness certificate is implemented.
"""
import argparse
from fractions import Fraction as F
from itertools import combinations
import json
import math
from pathlib import Path
import re


# Conservative research-tool bounds; rejection is not a mathematical conclusion.
MAX_INPUT_BYTES = 32768
MAX_OBSERVATIONS = 32
MAX_CANDIDATES = 16
MAX_TOKEN_CHARS = 64
MAX_EXPONENT = 24
MAX_COMPONENT = 10**24
MAX_ABS_VALUE = 10**6
MAX_NESTING = 8
NUMBER = re.compile(r"[+-]?(?:[0-9]+/[0-9]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\Z")


def numeric_text(value):
    if len(value) > MAX_TOKEN_CHARS or not NUMBER.fullmatch(value):
        raise ValueError("invalid or overlong numeric literal")
    exponent = re.search(r"[eE]([+-]?[0-9]+)\Z", value)
    if exponent and abs(int(exponent[1])) > MAX_EXPONENT:
        raise ValueError("numeric exponent exceeds bound")
    return value


def rational(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, F)):
        raise ValueError("expected finite rational number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    if isinstance(value, (int, F)):
        if abs(value.numerator) > MAX_COMPONENT or value.denominator > MAX_COMPONENT:
            raise ValueError("rational component exceeds bound")
    try:
        result = F(numeric_text(str(value)))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("expected bounded finite rational number") from exc
    if (abs(result) > MAX_ABS_VALUE or abs(result.numerator) > MAX_COMPONENT
            or result.denominator > MAX_COMPONENT):
        raise ValueError("rational magnitude or component exceeds bound")
    return result


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
    if set(data) - {"mode", "anchors", "times", "epsilon", "y", "candidates"}:
        raise ValueError("unknown input fields")
    mode = data.get("mode", "fixed_y")
    if mode not in ("fixed_y", "common_y"):
        raise ValueError("mode must be fixed_y or common_y")
    anchors_in = data.get("anchors")
    if not isinstance(anchors_in, list) or not anchors_in:
        raise ValueError("nonempty anchors required")
    n = len(anchors_in)
    if n > MAX_OBSERVATIONS:
        raise ValueError("too many observations")
    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= MAX_CANDIDATES:
        raise ValueError("candidate count must be between 1 and 16")
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
        "version": 2, "mode": mode,
        "status": "witness" if pairs else "no_witness_found",
        "arithmetic": "exact rational squared inequalities; no feasibility tolerance",
        "scope": "supplied candidates only; no uniqueness or safe-acceptance conclusion",
        "candidates": [[str(z) for z in c] for c in candidates],
        "squared_ranges": [[str(z) for z in row] for row in q],
        "fixed_y_feasible": feasible, "witnesses": pairs,
    }


def reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_bounded(path):
    if not path.is_file():
        raise ValueError("input must be a regular file")
    with path.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds byte limit")
    text = raw.decode("utf-8")
    # Bound nesting before JSON decoding, ignoring brackets inside JSON strings.
    depth, quoted, escaped = 0, False, False
    for ch in text:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_NESTING:
                raise ValueError("JSON nesting exceeds limit")
        elif ch in "]}":
            depth -= 1
    return json.loads(text, parse_int=numeric_text, parse_float=numeric_text,
                      parse_constant=reject_constant, object_pairs_hook=unique_object)


def emit(result):
    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output")
    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None
    if output_path is not None:
        try:
            if output_path.resolve() == input_path.resolve() or output_path.exists():
                raise ValueError("output must be a new path distinct from input")
        except (OSError, ValueError) as exc:
            print(emit({"status": "invalid_output", "error": str(exc)}), end="")
            return 2
    try:
        data = load_bounded(input_path)
        result = diagnose(data)
        code = 0
    except (ValueError, TypeError, OSError, RecursionError) as exc:
        result = {"status": "invalid_input", "error": str(exc)}
        code = 2
    rendered = emit(result)
    if output_path is not None:
        try:
            # Exclusive creation closes the existence-check race without clobbering.
            with output_path.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(rendered)
        except OSError as exc:
            print(emit({"status": "invalid_output", "error": str(exc)}), end="")
            return 2
    else:
        print(rendered, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
