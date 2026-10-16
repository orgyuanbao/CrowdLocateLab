"""One bounded adversarial review and CLI regression run. No dependencies added.

Run: python -B runs/mirror-ambiguity-20261015/verify_v2.py --report results_v2.json
Report path must be new; all scratch artifacts are retained inside this run.
"""
import argparse
from copy import deepcopy
from fractions import Fraction as F
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True
import diagnostic_v1 as v1
import diagnostic_v2 as v2
from oracle_math_v2 import (fixed_oracle, common_oracle, root_interval,
    distance_squared, candidate_oracle, determinant_expansion)

HERE = Path(__file__).resolve().parent
SEED = 20261016
RECORD = {"seed": SEED, "review": "same-model review with separate implementation, not independent-model confirmation",
          "commands": [], "checks": {}, "failures": []}


def check(name, function):
    try:
        RECORD["checks"][name] = function()
    except Exception:
        RECORD["failures"].append({"check": name, "traceback": traceback.format_exc()})


def test_predicates():
    rng = random.Random(SEED)
    # Upper/lower fixed shells; clipped lower bound; both-zero and one-zero roots;
    # signs S<=0 and S>0; equal irrational roots; explicit threshold sides.
    fixed = [(F(q), F(y), F(e)) for q, y, e in
             [(0, 0, 0), (0, 1, 1), (0, 2, 1), (1, 2, 1), (9, 2, 1), (2, 0, 2)]]
    common = [(F(q), F(r), F(e)) for q, r, e in
              [(0, 0, 0), (0, 4, 1), (1, 9, 1), (2, 2, 0), (2, 3, 0), (1, 1, 1)]]
    for shift in (F(-1, 10**20), F(0), F(1, 10**20)):
        fixed += [(F(9), F(2), 1+shift), (F(1), F(2), 1+shift),
                  (F(0), F(1), 1+shift)]
        common += [(F(1), F(9), 1+shift), (F(0), F(4), 1+shift)]
    # Irrational threshold adversaries on certified opposite sides of sqrt(2).
    lo, hi = root_interval(F(2), 160)
    fixed += [(F(2), F(0), lo), (F(2), F(0), hi)]
    # |sqrt(2)-1|/2 lies strictly inside these two bounds.
    common += [(F(2), F(1), (lo-1)/2), (F(2), F(1), (hi-1)/2)]
    for _ in range(500):
        fixed.append((F(rng.randrange(500), rng.randrange(1, 31)),
                      F(rng.randrange(60), 7), F(rng.randrange(40), 11)))
        common.append((F(rng.randrange(500), rng.randrange(1, 31)),
                       F(rng.randrange(500), rng.randrange(1, 31)), F(rng.randrange(40), 11)))
    for q, y, e in fixed:
        expected = fixed_oracle(q, y, e)
        assert v1.fixed_row(q, y, e) == expected
        assert v2.fixed_row(q, y, e) == expected
    for q, r, e in common:
        expected = common_oracle(q, r, e)
        assert v1.common_row(q, r, e) == expected
        assert v2.common_row(q, r, e) == expected
    # Emitted rational lower bounds must never exceed the exact root.
    for q in (F(0), F(9), F(125), F(1, 10**24), F(2, 3)):
        lower = v2.sqrt_lower(q)
        assert lower >= 0 and lower*lower <= q
        assert (lower+F(1, 10**9))**2 > q
    return {"fixed_cases_per_version": len(fixed), "common_cases_per_version": len(common),
            "rational_error_bound_cases": 5, "mismatches": 0}


def test_fixtures():
    fixtures = json.loads((HERE/"fixtures_v1.json").read_text(encoding="utf-8"))
    statuses = {}
    for name, data in fixtures.items():
        pairs, rows = candidate_oracle(data)
        for implementation in (v1, v2):
            got = implementation.diagnose(data)
            assert [z["indices"] for z in got["witnesses"]] == pairs
            assert [[F(z) for z in row] for row in got["squared_ranges"]] == rows
        statuses[name] = "witness" if pairs else "no_witness_found"
    # Missing candidate is not a uniqueness theorem; quantifier cases differ.
    assert statuses["omitted_mirror_no_witness"] == "no_witness_found"
    assert statuses["collinear_full_rank"] == "witness"
    assert statuses["fixed_y_quantifier"] == "no_witness_found"
    assert statuses["common_y_boundary"] == "witness"
    return statuses


def rotate(z):
    return (F(3, 5)*z[0]-F(4, 5)*z[1], F(4, 5)*z[0]+F(3, 5)*z[1])


def dot(x, y):
    return sum(a*b for a, b in zip(x, y))


def reflect(z, center, n, velocity=False):
    offset = dot(n, z if velocity else [z[j]-center[j] for j in (0, 1)])
    return tuple(z[j]-2*n[j]*offset for j in (0, 1))


def test_reflection_and_rank():
    fixtures = json.loads((HERE/"fixtures_v1.json").read_text(encoding="utf-8"))
    transformed, minors = {}, {}
    center, normal = (F(7), F(-11)), rotate((F(0), F(1)))
    # Rotate and translate every v1 example, not just the exact mirror example.
    for name, data in fixtures.items():
        new = deepcopy(data)
        new["anchors"] = [[str(z+center[j]) for j, z in enumerate(rotate(list(map(F, a))))]
                          for a in data["anchors"]]
        new["candidates"] = []
        for c in data["candidates"]:
            p, v = rotate(list(map(F, c[:2]))), rotate(list(map(F, c[2:])))
            new["candidates"].append([str(p[j]+center[j]) for j in (0, 1)] + list(map(str, v)))
        expected, rows = candidate_oracle(new)
        original_pairs, original_rows = candidate_oracle(data)
        assert expected == original_pairs and rows == original_rows
        for implementation in (v1, v2):
            assert [w["indices"] for w in implementation.diagnose(new)["witnesses"]] == expected
        transformed[name] = new
    data = transformed["noncollinear_crossing_full_rank"]
    for label, example in [("original", fixtures["noncollinear_crossing_full_rank"]),
                           ("rotated_translated", data)]:
        a0, a1, a2 = [list(map(F, example["anchors"][i])) for i in (0, 1, 2)]
        area = (a1[0]-a0[0])*(a2[1]-a0[1])-(a1[1]-a0[1])*(a2[0]-a0[0])
        assert area != 0
        minors[label] = {"anchor_oriented_double_area": str(area)}
        for cindex, cc in enumerate(example["candidates"]):
            c = list(map(F, cc))
            matrix = []
            for i in (0, 1, 3, 4):
                t, a = F(example["times"][i]), list(map(F, example["anchors"][i]))
                z = [c[j]+t*c[j+2]-a[j] for j in (0, 1)]
                assert dot(z, z) > 0
                matrix.append(z + [t*z[0], t*z[1]])
            determinant = determinant_expansion(matrix)
            assert determinant != 0
            if cindex == 0:
                assert determinant == -6
            minors[label]["candidate_"+str(cindex)+"_minor"] = str(determinant)
    # Reflection algebra on a translated oblique line, including zeros.
    rng = random.Random(SEED)
    geometries = [(F(0), F(0), F(0)), (F(0), F(1), F(1)), (F(0), F(1), F(-1)),
                  (F(0), F(0), F(2)), (F(1), F(2), F(0))]
    geometries += [tuple(F(rng.randint(-8, 8), 3) for _ in range(3)) for _ in range(80)]
    zeros = []
    tangent = rotate((F(1), F(0)))
    for index, (r, s, h) in enumerate(geometries):
        p = tuple(center[j]+r*tangent[j]+s*normal[j] for j in (0, 1))
        a = tuple(center[j]+h*normal[j] for j in (0, 1))
        pr = reflect(p, center, normal)
        q = sum((p[j]-a[j])**2 for j in (0, 1))
        qr = sum((pr[j]-a[j])**2 for j in (0, 1))
        assert q == r*r+(s-h)**2 and qr == r*r+(s+h)**2
        assert qr-q == 4*h*s
        assert common_oracle(q, qr, min(abs(h), abs(s)))
        assert (q == qr) == (h*s == 0)
        if q == 0 or qr == 0:
            zeros.append({"q": str(q), "q_reflected": str(qr), "hs": str(h*s)})
        for bits in (96,):
            l1, u1 = root_interval(q, bits)
            l2, u2 = root_interval(qr, bits)
            if q+qr != 0:
                dlo, dhi = max(F(0), l1-u2, l2-u1), max(u1-l2, u2-l1)
                formula_lo, formula_hi = 4*abs(h*s)/(u1+u2), 4*abs(h*s)/(l1+l2)
                assert max(dlo, formula_lo) <= min(dhi, formula_hi)
            else:
                assert h == s == r == 0
    # Affine trajectory reflection commutes with evaluating at each time.
    c = list(map(F, data["candidates"][0]))
    xr, vr = reflect(c[:2], center, normal), reflect(c[2:], center, normal, True)
    assert list(xr+vr) == list(map(F, data["candidates"][1]))
    for t in map(F, data["times"]):
        point = tuple(c[j]+t*c[j+2] for j in (0, 1))
        assert reflect(point, center, normal) == tuple(xr[j]+t*vr[j] for j in (0, 1))
    assert sum((xr[j]-c[j])**2 for j in (0, 1)) == 4*dot(normal, [c[j]-center[j] for j in (0, 1)])**2
    return {"transformed_fixture_count": len(transformed), "exact_rank": minors,
            "oblique_reflection_cases": len(geometries), "zero_cases": zeros}


def test_error_guarantee():
    # Independent rational-square tests of D/2: no numerical square roots needed.
    x1, x2 = (F(0), F(3)), (F(0), F(-3))
    d2 = sum((a-b)**2 for a, b in zip(x1, x2))
    grid_count = 0
    for zx in range(-3, 4):
        for zy in range(-4, 5):
            errors = [sum((x[j]-z)**2 for j, z in enumerate((F(zx), F(zy)))) for x in (x1, x2)]
            assert max(errors) >= d2/4
            grid_count += 1
    assert d2/4 == 9
    # Equality midpoint shows that D=2r does not refute a <=r guarantee for this pair.
    assert all(sum(a*a for a in x) == 9 for x in (x1, x2))
    threshold = {str(r): d2 > 4*r*r for r in (F(2999, 1000), F(3), F(3001, 1000))}
    assert list(threshold.values()) == [True, False, False]
    # Finite output law: expectation is computed per world before taking maximum.
    points = [(F(0), F(0)), x1, x2]
    weights = [F(1, 2), F(1, 4), F(1, 4)]
    expected = []
    for x in (x1, x2):
        total = F(0)
        for z, w in zip(points, weights):
            q = sum((x[j]-z[j])**2 for j in (0, 1))
            # All these distances are rational; interval oracle handles them exactly.
            lo, hi = root_interval(q, 32)
            assert lo == hi
            total += w*lo
        expected.append(total)
    assert max(expected) >= 3
    return {"grid_estimators": grid_count, "radius_rejection": threshold,
            "randomized_per_world_expected_errors": list(map(str, expected))}


def test_cli(scratch):
    base = {"mode": "fixed_y", "anchors": [[0, 0]], "times": [0], "y": [2],
            "epsilon": [1], "candidates": [[1, 0, 0, 0], [3, 0, 0, 0]]}
    def write(name, text):
        path = scratch/(name+"_v2.json")
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
        return path
    def cli(version, path, output=None):
        command = [sys.executable, "-X", "utf8", "-B", str(HERE/("diagnostic_"+version+".py")), str(path)]
        if output is not None:
            command += ["--output", str(output)]
        result = subprocess.run(command, cwd=HERE, capture_output=True, text=True,
                                encoding="utf-8", timeout=10)
        log = {"argv": command, "exit_code": result.returncode, "stdout": result.stdout,
               "stderr": result.stderr}
        RECORD["commands"].append(log)
        return result
    def expect_invalid(path, status="invalid_input"):
        got = cli("v2", path)
        assert got.returncode == 2 and json.loads(got.stdout)["status"] == status
    # Concrete v1 flaws, only in fresh scratch files; no original v1 data touched.
    duplicate = write("duplicate", json.dumps(base)[:-1]+', "y": [1]}')
    got = cli("v1", duplicate)
    assert got.returncode == 0 and json.loads(got.stdout)["status"] == "no_witness_found"
    expect_invalid(duplicate)
    alias = write("input_equals_output", json.dumps(base))
    before = alias.read_bytes()
    got = cli("v1", alias, alias)
    assert got.returncode == 0 and alias.read_bytes() != before
    valid = write("valid", json.dumps(base))
    before = valid.read_bytes()
    got = cli("v2", valid, valid)
    assert got.returncode == 2 and json.loads(got.stdout)["status"] == "invalid_output"
    assert valid.read_bytes() == before
    earlier = write("earlier_result_v1_reproduction", '"previous result"')
    got = cli("v1", valid, earlier)
    assert got.returncode == 0 and earlier.read_text(encoding="utf-8") != '"previous result"'
    old = write("earlier_result_v2_protected", '"previous result"')
    got = cli("v2", valid, old)
    assert got.returncode == 2 and json.loads(got.stdout)["status"] == "invalid_output"
    assert old.read_bytes() == b'"previous result"'
    # Modest inputs already exceed chosen v2 limits; never stress v1 to exhaustion.
    over_n = deepcopy(base)
    for key in ("anchors", "times", "y", "epsilon"):
        over_n[key] *= 33
    over_k = deepcopy(base)
    over_k["candidates"] = [[1, 0, 0, 0]]*17
    over_exp = deepcopy(base)
    over_exp["candidates"][0][0] = "1e25"
    oversized = json.dumps(base)+" "*v2.MAX_INPUT_BYTES
    repros = {"observations_33": json.dumps(over_n), "candidates_17": json.dumps(over_k),
              "exponent_25": json.dumps(over_exp), "file_bytes_over_limit": oversized}
    for name, text in repros.items():
        path = write(name, text)
        assert cli("v1", path).returncode == 0
        expect_invalid(path)
    # Numeric and structural regressions, including malformed UTF8 and extreme exponent.
    variants = {}
    for name, value in [("token_65", "0"*64+"1"), ("denominator_over", "1/1000000000000000000000001"),
                        ("numerator_over", "1000000000000000000000001/1000000000000000000000000"),
                        ("magnitude_over", "1000001"), ("huge_exponent", "1e999999999999999999"),
                        ("negative_epsilon", "-1"), ("nonfinite", "NaN"),
                        ("zero_denominator", "1/0")]:
        value_base = deepcopy(base)
        if name == "negative_epsilon":
            value_base["epsilon"][0] = value
        else:
            value_base["candidates"][0][0] = value
        variants[name] = json.dumps(value_base)
    variants["json_nan"] = json.dumps(base).replace('"y": [2]', '"y": [NaN]')
    variants["deep_nesting"] = "["*9+"0"+"]"*9
    variants["unknown_key"] = json.dumps(dict(base, unused={"a": 1}))
    variants["escaped_duplicate"] = json.dumps(base)[:-1]+', "\\u0079": [1]}'
    variants["mode_y_mix"] = json.dumps(dict(base, mode="common_y"))
    variants["bool_time"] = json.dumps(dict(base, times=[True]))
    for name, text in variants.items():
        expect_invalid(write(name, text))
    bad_utf = scratch/"bad_utf8_v2.json"
    bad_utf.write_bytes(b"\xff")
    expect_invalid(bad_utf)
    missing = scratch/"absent_v2.json"
    expect_invalid(missing)
    # Exact accepted numeric bounds, n/k upper bounds, no feasibility oracle involved.
    for token in ("1e-24", "1/1000000000000000000000000", "1000000", "0"*63+"1"):
        assert v2.rational(token) == F(token)
    full = deepcopy(base)
    for key in ("anchors", "times", "y", "epsilon"):
        full[key] *= 32
    full["candidates"] = [[1, 0, 0, 0]]*16
    assert v2.diagnose(full)["status"] == "no_witness_found"
    byte_boundary = write("exact_byte_boundary", json.dumps(base)+" "*(v2.MAX_INPUT_BYTES-len(json.dumps(base))))
    assert byte_boundary.stat().st_size == v2.MAX_INPUT_BYTES
    got = cli("v2", byte_boundary)
    assert got.returncode == 0 and json.loads(got.stdout)["status"] == "witness"
    new_output = scratch/"new_certificate_v2.json"
    got = cli("v2", valid, new_output)
    assert got.returncode == 0 and json.loads(new_output.read_text(encoding="utf-8"))["status"] == "witness"
    preserved = new_output.read_bytes()
    got = cli("v2", valid, new_output)
    assert got.returncode == 2 and new_output.read_bytes() == preserved
    bad_output = scratch/"missing_directory_v2"/"certificate_v2.json"
    got = cli("v2", valid, bad_output)
    assert got.returncode == 2 and json.loads(got.stdout)["status"] == "invalid_output"
    # Malformed input error can be saved to a fresh path, never over an existing file.
    error_output = scratch/"error_result_v2.json"
    got = cli("v2", duplicate, error_output)
    assert got.returncode == 2 and json.loads(error_output.read_text(encoding="utf-8"))["status"] == "invalid_input"
    return {"v1_flaws_reproduced": ["duplicate key", "input clobber", "previous result clobber",
              "uncapped observation/candidate/byte/numeric sizes"],
            "cli_invocations": len(RECORD["commands"]), "invalid_variants": len(variants)+2,
            "numeric_bound_acceptance_cases": 4, "max_n_k": [32, 16],
            "scratch_directory": str(scratch)}


def test_v1_preserved():
    manifest = json.loads((HERE/"v1_hashes_before_v2.json").read_text(encoding="utf-8-sig"))
    for entry in manifest:
        assert hashlib.sha256(Path(entry["Path"]).read_bytes()).hexdigest().upper() == entry["Hash"]
    return {"sha256_unchanged_files": len(manifest)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="results_v2.json")
    args = parser.parse_args()
    report = HERE/args.report
    # Fail before any checks if this would overwrite an earlier report.
    with report.open("x", encoding="utf-8") as destination:
        scratch = Path(tempfile.mkdtemp(prefix="checks_", suffix="_v2", dir=HERE))
        RECORD["invocation"] = list(sys.orig_argv)
        check("squared_predicates_vs_interval_oracle", test_predicates)
        check("v1_fixtures_vs_separate_oracle", test_fixtures)
        check("reflection_transforms_and_rank", test_reflection_and_rank)
        check("D_over_2", test_error_guarantee)
        check("CLI_reproductions_and_regressions", lambda: test_cli(scratch))
        check("v1_integrity", test_v1_preserved)
        RECORD["status"] = "passed" if not RECORD["failures"] else "failed"
        json.dump(RECORD, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps({k: v for k, v in RECORD.items() if k != "commands"}, ensure_ascii=False, indent=2))
    return 0 if not RECORD["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
