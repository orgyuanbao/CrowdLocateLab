"""Estimate x(0) from observation-only JSON using a verified frozen method.

This application does not run an LLM, generate holdout data, or read input
ground truth. Optional calibrated radii inherit the supplied calibration
distribution's assumptions; field coverage is never asserted automatically.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

from benchmark import scale as calibration_scale, validate_frozen_run
from localization import fit
from tools.common import clean, digest


FIELDS = ("anchors", "times", "ranges", "sigma")
COVERAGE_SCOPE = (
    "该半径沿用提供的校准样本分布。标称边际覆盖需要校准样本与新样本的交换性及同一冻结估计器；"
    "未核验本次现场输入满足这些假设，不声称对任意现场输入、每个场景、拒识子集或分布偏移具有标称覆盖率。"
)


def _reject_constant(value):
    raise ValueError(f"JSON contains forbidden non-finite constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_snapshot(path):
    """Parse and identify one immutable byte snapshot, including a UTF-8 BOM."""
    data = Path(path).read_bytes()
    value = json.loads(data.decode("utf-8-sig"), parse_constant=_reject_constant,
                       object_pairs_hook=_unique_object)
    return value, hashlib.sha256(data).hexdigest()


def load_json(path):
    return load_json_snapshot(path)[0]


@dataclass(frozen=True, slots=True)
class ObservationFrame:
    """Exactly the four observables accepted by the frozen numerical solver."""
    anchors: np.ndarray
    times: np.ndarray
    ranges: np.ndarray
    sigma: np.ndarray


def observations_from_dict(value) -> ObservationFrame:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("Input must contain exactly anchors, times, ranges, sigma; truth, scenario, meta and other fields are not accepted.")
    if not all(isinstance(value[key], list) for key in FIELDS):
        raise ValueError("All four observation fields must be JSON arrays.")
    count = len(value["times"])
    if count < 3:
        raise ValueError("At least three observations are required.")
    if len(value["anchors"]) != count or any(not isinstance(row, list) or len(row) != 2 for row in value["anchors"]):
        raise ValueError("anchors must have shape (n, 2), with n matching times.")
    if any(len(value[key]) != count for key in ("ranges", "sigma")):
        raise ValueError("times, ranges and sigma must have identical length n.")
    scalars = [number for row in value["anchors"] for number in row]
    scalars.extend(number for key in ("times", "ranges", "sigma") for number in value[key])
    if any(type(number) not in (int, float) for number in scalars):
        raise ValueError("Observation values must be JSON numbers, not booleans, numeric strings or nested arrays.")
    try:
        arrays = {key: np.asarray(value[key], dtype=np.float64) for key in FIELDS}
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Observation values must fit finite float64 numbers.") from exc
    if not all(np.isfinite(array).all() for array in arrays.values()):
        raise ValueError("All observations must be finite.")
    if any(arrays[key].shape != (count,) for key in ("times", "ranges", "sigma")):
        raise ValueError("times, ranges and sigma must each have shape (n,).")
    if np.any(arrays["ranges"] <= 0) or np.any(arrays["sigma"] <= 0):
        raise ValueError("ranges and sigma must be strictly positive.")
    if np.any(arrays["times"] > 0):
        raise ValueError("Historical times must be <= 0 seconds relative to the requested current position x(0).")
    for array in arrays.values():
        array.setflags(write=False)
    return ObservationFrame(**arrays)


def _positive_integer(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def calibrated_radius(calibration_path, alpha, result, frozen_path, frozen):
    base = {"alpha": alpha, "nominal_marginal_coverage": 1. - alpha,
            "coverage_guarantee_asserted_for_this_input": False,
            "exchangeability_verified_for_this_input": False, "scope": COVERAGE_SCOPE}
    if calibration_path is None:
        return {**base, "status": "not_provided", "radius_m": None}
    calibration_path = Path(calibration_path).resolve()
    if not calibration_path.exists():
        return {**base, "status": "not_found", "radius_m": None,
                "note": "未找到校准文件，只返回冻结方法的点估计与诊断。"}
    protocol_path = calibration_path.parent / "protocol.json"
    if not protocol_path.is_file():
        raise ValueError("Calibration requires its adjacent benchmark protocol.json to verify the frozen method.")
    protocol = load_json(protocol_path)
    expected_hash = hashlib.sha256(Path(frozen_path).read_bytes()).hexdigest()
    if not isinstance(protocol, dict) or protocol.get("frozen_method_sha256") != expected_hash:
        raise ValueError("Calibration protocol belongs to a different frozen method record.")
    if digest(protocol.get("methods", {}).get("llm_selected")) != digest(frozen["spec"]):
        raise ValueError("Calibration estimator specification does not match the selected frozen method.")
    calibration, calibration_hash = load_json_snapshot(calibration_path)
    if not isinstance(calibration, dict) or not isinstance(calibration.get("results"), dict):
        raise ValueError("Calibration file must contain a results object.")
    matching = []
    for record in calibration["results"].values():
        if not isinstance(record, dict):
            raise ValueError("Each calibration record must be an object.")
        stored_alpha = record.get("alpha")
        if type(stored_alpha) in (int, float) and math.isfinite(stored_alpha) and stored_alpha == alpha:
            matching.append(record)
    if len(matching) != 1:
        raise ValueError("Requested alpha must match exactly one precomputed calibration level; no interpolation is performed.")
    record = matching[0]
    count = _positive_integer(record.get("calibration_n"), "calibration_n")
    order = _positive_integer(record.get("order_statistic_k"), "order_statistic_k")
    if order != math.ceil((count + 1) * (1. - alpha)):
        raise ValueError("Calibration order statistic does not match n and alpha.")
    q = record.get("q")
    if order > count or q is None:
        return {**base, "status": "no_finite_quantile", "radius_m": None,
                "calibration_n": count, "order_statistic_k": order,
                "note": "当前样本量和校准级别未提供有限分位数，不能输出有限校准半径。"}
    if type(q) not in (int, float) or not math.isfinite(q) or q < 0:
        raise ValueError("Calibration q must be a finite nonnegative number.")
    geometry = float(calibration_scale(result))
    radius = float(q * geometry)
    if not math.isfinite(radius):
        raise ValueError("Calibrated radius overflowed; no finite radius can be reported.")
    return {**base, "status": "available_under_assumptions", "radius_m": radius,
            "center": result["position"], "q": float(q), "geometry_scale_m": geometry,
            "calibration_n": count, "order_statistic_k": order,
            "formula": "radius_m = q * benchmark.scale(prediction)",
            "frozen_method_sha256": expected_hash,
            "calibration_sha256": calibration_hash,
            "provenance": "Adjacent benchmark protocol matches the verified frozen record and solver specification."}


def locate_from_files(input_path, run, calibration=None, alpha=.1, root=None):
    if type(alpha) not in (int, float) or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be finite and strictly between 0 and 1.")
    root = Path(root).resolve() if root else Path(__file__).resolve().parent
    run, input_path = Path(run).resolve(), Path(input_path).resolve()
    input_value, input_hash = load_json_snapshot(input_path)
    observations = observations_from_dict(input_value)
    checked = validate_frozen_run(root, run)
    frozen = checked["frozen"]
    result = clean(fit(observations, frozen["spec"]))
    for key in ("position", "velocity"):
        vector = result.get(key)
        if not isinstance(vector, list) or len(vector) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise ValueError(f"Frozen solver returned an invalid {key}.")
    if not isinstance(result.get("diagnostics"), dict):
        raise ValueError("Frozen solver returned invalid diagnostics.")
    from application_safety import numerical_quality
    quality = numerical_quality(observations, result, frozen["spec"], fit_function=fit)
    radius = calibrated_radius(calibration, float(alpha), result, run / "tools" / "frozen_method.json", frozen)
    return {"schema_version": 2, "method_id": frozen["method_id"], "method_spec": frozen["spec"],
            "status": "estimated" if quality["passed"] else "rejected_numerical_quality",
            "numerical_quality": quality,
            "run_integrity": checked["audit"],
            "observations": {"n": len(observations.times), "reference_time_s": 0.,
                             "coordinate_units": "local planar metres", "time_units": "seconds",
                             "input_sha256": input_hash},
            "position": result["position"], "velocity": result["velocity"],
            "selected_model": result.get("selected_model"), "diagnostics": result["diagnostics"],
            "work_units": result.get("work_units"), "elapsed_seconds": result.get("elapsed_seconds"),
            "calibration": radius,
            "interpretation": "position 是输入坐标系中 t=0 的二维点估计，velocity 的单位是米/秒；本输入不含真实位置，不能据此计算定位误差。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSON containing exactly anchors/times/ranges/sigma")
    parser.add_argument("--run", type=Path, default=Path("runs/live_reference"))
    parser.add_argument("--calibration", type=Path, default=Path("results/benchmark/conformal.json"))
    parser.add_argument("--alpha", type=float, default=.1)
    parser.add_argument("--output", type=Path, help="New result file; omitted means JSON to stdout")
    args = parser.parse_args()
    try:
        if args.output:
            output = args.output.resolve()
            if output.exists():
                raise ValueError("Output already exists; choose a new filename to preserve evidence.")
            if output.is_relative_to(args.run.resolve()):
                raise ValueError("Application predictions must be written outside the preserved research run directory.")
        result = locate_from_files(args.input, args.run, args.calibration, args.alpha)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
            print(f"Location result saved: {args.output}")
        else:
            print(text, end="")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"locate: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
