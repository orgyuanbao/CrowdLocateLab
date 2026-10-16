"""Independent read-only regression of upstream diagnostic_v2.py.

Only post_review/v2_checks receives probe files. Runtime research artifacts are
read and hashed; neither the agent's experiments nor its verifier are rerun.
"""
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RUN = HERE.parent / "runtime" / "runs" / "mirror-ambiguity-20261015"
SOURCE = RUN / "diagnostic_v2.py"
CHECKS = HERE / "v2_checks"


def hash_sources():
    names = ("diagnostic_v1.py", "diagnostic_v2.py", "oracle_math_v2.py",
             "verify_v2.py", "fixtures_v1.json", "results_v1.json",
             "results_final_v2.json")
    return {name: sha256((RUN/name).read_bytes()).hexdigest() for name in names}


def load_module():
    spec = importlib.util.spec_from_file_location("reviewed_diagnostic_v2", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli(input_path, output=None):
    args = [sys.executable, "-B", "-X", "utf8", str(SOURCE), str(input_path)]
    if output is not None:
        args += ["--output", str(output)]
    started = perf_counter()
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            timeout=15, cwd=CHECKS)
    return {"exit_code": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr, "seconds": perf_counter()-started}


def run():
    CHECKS.mkdir(exist_ok=True)
    before = hash_sources()
    module = load_module()
    fixtures = json.loads((RUN/"fixtures_v1.json").read_text(encoding="utf-8"))
    prior = json.loads((RUN/"results_v1.json").read_text(encoding="utf-8"))
    statuses = {}
    for name, data in fixtures.items():
        actual = module.diagnose(data)
        expected = deepcopy(prior["cases"][name])
        expected["version"] = 2
        assert actual == expected, name
        statuses[name] = actual["status"]

    rational_cases = [
        ("absolute_positive_boundary", "1000000", F(1000000)),
        ("absolute_negative_boundary", "-1000000", F(-1000000)),
        ("absolute_above", "1000000.000000001", None),
        ("absolute_below_negative", "-1000000.000000001", None),
        ("denominator_boundary", "1/1000000000000000000000000", F(1,10**24)),
        ("denominator_above", "1/1000000000000000000000001", None),
        ("numerator_boundary", "1000000000000000000000000/999999999999999999999999", F(10**24,10**24-1)),
        ("numerator_above", "1000000000000000000000001/999999999999999999999999", None),
        ("negative_exponent_boundary", "1e-24", F(1,10**24)),
        ("negative_exponent_above", "1e-25", None),
        ("positive_exponent_boundary", "0.000000000000000001e24", F(10**6)),
        ("positive_exponent_above", "0e25", None),
        ("huge_positive_exponent", "1e999999999999999999999999999999999999999999999999999999", None),
        ("huge_negative_exponent", "1e-999999999999999999999999999999999999999999999999999999", None),
        ("token_64", "0"*63+"1", F(1)),
        ("token_65", "0"*64+"1", None),
        ("long_decimal_outside_supported_domain", "0."+"9"*80, None),
        ("bool_rejected", True, None),
        ("zero_denominator", "1/0", None),
        ("nonfinite_float", float("inf"), None),
    ]
    numeric_records = []
    for name, token, expected in rational_cases:
        try:
            value = module.rational(token)
        except ValueError as exc:
            assert expected is None, (name, str(exc))
            numeric_records.append({"name": name, "status": "rejected"})
        else:
            assert expected is not None and value == expected, name
            numeric_records.append({"name": name, "status": "accepted", "value": str(value)})

    base = {"mode": "fixed_y", "anchors": [[0,0]], "times": [0],
            "epsilon": [1], "y": [2], "candidates": [[1,0,0,0], [3,0,0,0]]}
    dimension_records = []
    for n in (32,33):
        data = deepcopy(base)
        for key in ("anchors", "times", "epsilon", "y"):
            data[key] *= n
        try:
            actual = module.diagnose(data)
        except ValueError:
            assert n == 33
            status = "rejected"
        else:
            assert n == 32 and actual["status"] == "witness"
            status = "accepted"
        dimension_records.append({"observations": n, "status": status})
    for k in (16,17):
        data = deepcopy(base)
        data["candidates"] = [[F(j,100),0,0,0] for j in range(k)]
        try:
            module.diagnose(data)
        except ValueError:
            assert k == 17
            status = "rejected"
        else:
            assert k == 16
            status = "accepted"
        dimension_records.append({"candidates": k, "status": status})

    cases = {}
    duplicate = json.dumps(base)[:-1] + ', "y": [1]}'
    cases["duplicate_y"] = (duplicate.encode(), "invalid_input")
    for token in ("1e1000000000", "1e-1000000000", "1e999999999999999999999999999999999999"):
        data = deepcopy(base)
        data["epsilon"] = ["TOKEN"]
        cases["exponent_"+token.replace("-","minus")] = (
            json.dumps(data).replace('"TOKEN"',token).encode(), "invalid_input")
    body = json.dumps(base).encode()
    cases["bytes_32768"] = (body+b" "*(32768-len(body)), "witness")
    cases["bytes_32769"] = (body+b" "*(32769-len(body)), "invalid_input")
    cases["deep_nesting"] = (("["*9+"0"+"]"*9).encode(), "invalid_input")
    cases["invalid_utf8"] = (b"\xff\xfe", "invalid_input")
    cases["unknown_fields"] = ((json.dumps(base)[:-1]+',"unrecognized":0}').encode(), "invalid_input")
    cli_records = []
    for name,(content,status) in cases.items():
        path = CHECKS / (name+".json")
        path.write_bytes(content)
        actual = cli(path)
        parsed = json.loads(actual["stdout"])
        assert parsed["status"] == status, (name, actual)
        assert actual["exit_code"] == (0 if status == "witness" else 2)
        assert not actual["stderr"], (name,actual)
        cli_records.append({"name": name, "status": parsed["status"],
                            "exit_code": actual["exit_code"], "seconds": actual["seconds"]})

    input_path = CHECKS / "protected_input.json"
    input_path.write_text(json.dumps(base),encoding="utf-8")
    input_hash = sha256(input_path.read_bytes()).hexdigest()
    output_path = CHECKS / "protected_existing_output.json"
    output_path.write_text("independent review sentinel\n",encoding="utf-8")
    output_hash = sha256(output_path.read_bytes()).hexdigest()
    protections = []
    for name,target in (("same_input_output",input_path),("existing_output",output_path)):
        actual = cli(input_path,target)
        assert actual["exit_code"] == 2
        assert json.loads(actual["stdout"])["status"] == "invalid_output"
        assert sha256(input_path.read_bytes()).hexdigest() == input_hash
        assert sha256(output_path.read_bytes()).hexdigest() == output_hash
        protections.append({"name":name,"status":"rejected_without_changes"})
    new_output = CHECKS / "new_valid_output.json"
    # A subsequent run gets a distinct output, preserving the first review.
    suffix = 1
    while new_output.exists():
        new_output = CHECKS / f"new_valid_output_{suffix}.json"
        suffix += 1
    actual = cli(input_path,new_output)
    assert actual["exit_code"] == 0 and actual["stdout"] == ""
    assert json.loads(new_output.read_text(encoding="utf-8"))["status"] == "witness"
    protections.append({"name":"new_output","status":"created_successfully"})

    # Longest supported decimal boundary keeps exact comparisons inside the new
    # component limit, rather than treating rejected 80-digit data as a failure.
    boundaries = []
    for epsilon,status in (("0."+"9"*24,"no_witness_found"),
                           ("1","witness"),
                           ("1.00000000000000000000001","witness")):
        data=deepcopy(base)
        data["epsilon"]=[epsilon]
        actual=module.diagnose(data)
        assert actual["status"]==status
        boundaries.append({"epsilon":epsilon,"status":status})

    after=hash_sources()
    assert before==after
    return {
        "schema_version":1, "provenance":"independent_post_run_review_of_upstream_agent_turn_002",
        "read_only_runtime_artifacts":True,"reviewed_source_sha256":before,
        "runtime_sources_unchanged":True,"new_model_calls":0,
        "status":"passed_reviewed_regressions",
        "legacy_cases_unchanged_except_version":statuses,
        "numeric_input_boundaries":numeric_records,
        "count_boundaries":dimension_records,
        "cli_cases":cli_records,
        "output_preservation":protections,
        "supported_exact_decimal_boundaries":boundaries,
        "resolved_findings":["duplicate JSON keys rejected", "same/existing output refused without modification",
                             "huge scientific exponents rejected before Fraction construction", "explicit numeric/count/byte/nesting bounds enforced"],
        "major_remaining_errors_found":[],
        "limits":["This review does not certify arbitrary software correctness or formal proof completeness.",
                  "Input-domain contraction is deliberate; out-of-domain rejection is not a negative mathematical result.",
                  "Finite supplied candidates remain incomplete for continuous global ambiguity.",
                  "Original verify_v2.py still records absolute historical hash paths; no runtime verifier was rerun by this review.",
                  "New-file exclusive creation prevents overwrite but does not promise atomic complete-file publication on process interruption."]
    }


if __name__=="__main__":
    result=run()
    output=HERE/"review_v2.json"
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(output),"status":result["status"],
                      "legacy_cases":len(result["legacy_cases_unchanged_except_version"]),
                      "numeric_cases":len(result["numeric_input_boundaries"]),
                      "cli_cases":len(result["cli_cases"]),
                      "runtime_unchanged":result["runtime_sources_unchanged"]},ensure_ascii=False))
