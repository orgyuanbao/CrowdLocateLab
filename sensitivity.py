"""Evaluate a preregistered count-by-contamination grid after research freezing.

This standalone evaluation never registers, chooses, edits, or recalibrates a
method. Merely importing the module or requesting --help generates no cases.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
import sys
import time

import numpy as np
import scipy

from benchmark import validate_frozen_run
from localization import SCENARIOS, generate_case
from tools.common import load, metrics, save
from tools.toolkit import BASELINES, evaluate


ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "research" / "sensitivity_plan.json"
METHOD_NAMES = ("llm_selected", "dynamic_robust", "static_wls")
CSV_FIELDS = (
    "method", "case_id", "cell_id", "scenario", "n_observations",
    "nominal_contamination", "actual_contamination", "replicate", "seed",
    "error_m", "x", "y", "work_units", "elapsed_seconds",
    "selected_model", "selected_model_raw", "cv_dynamic_admissible",
    "cv_completed_folds",
)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_plan(path=PLAN_PATH):
    """Reject an accidental change to the registered experiment's design."""
    plan = load(path)
    required = {
        "version": "CrowdLocateLab-sensitivity-v1",
        "seed": 980221,
        "observation_counts": [8, 12, 16, 24],
        "contamination_rates": [0.0, 0.1, 0.25, 0.4],
        "scenarios": ["static_outliers", "moving_outliers"],
        "cases_per_cell": 12,
        "cell_count": 32,
        "case_count": 384,
        "method_names": list(METHOD_NAMES),
        "prediction_count": 1152,
    }
    for key, expected in required.items():
        if plan.get(key) != expected:
            raise ValueError(f"Sensitivity plan differs from registered design: {key}")
    return plan


def make_cells(plan):
    """Generate independent cell streams; call only after freeze validation."""
    cells, seen_seeds = [], set()
    for scene in plan["scenarios"]:
        for n in plan["observation_counts"]:
            for contamination in plan["contamination_rates"]:
                contamination_id = int(round(10000 * contamination))
                cell_id = f"{scene}_n{n}_c{contamination_id:04d}"
                parent = np.random.SeedSequence([
                    plan["seed"], SCENARIOS.index(scene), n, contamination_id,
                ])
                cases, metadata = [], []
                for replicate, child in enumerate(parent.spawn(plan["cases_per_cell"]), 1):
                    seed = int(child.generate_state(1, dtype=np.uint64)[0])
                    if seed in seen_seeds:
                        raise ValueError("Unexpected duplicate sensitivity substream seed")
                    seen_seeds.add(seed)
                    case = generate_case(seed, scene, n=n, contamination=contamination)
                    case.case_id = f"sensitivity-{cell_id}-r{replicate:02d}-{seed}"
                    record = {
                        "cell_id": cell_id, "scenario": scene,
                        "n_observations": n, "nominal_contamination": contamination,
                        "actual_contamination": case.meta["actual_outlier_fraction"],
                        "replicate": replicate, "seed": seed,
                    }
                    case.meta["sensitivity_design_for_evaluation_only"] = record
                    cases.append(case)
                    metadata.append(record)
                cells.append({"cell_id": cell_id, "scenario": scene,
                              "n_observations": n,
                              "nominal_contamination": contamination,
                              "cases": cases, "metadata": metadata})
    if len(cells) != plan["cell_count"] or len(seen_seeds) != plan["case_count"]:
        raise ValueError("Generated sensitivity design has an unexpected size")
    return cells


def normalized_model(raw):
    # The legacy dynamic baseline reports "dynamic"; preserve its raw label too.
    return "velocity" if raw == "dynamic" else raw


def summarize_rows(rows):
    result = metrics(rows)
    labels = [normalized_model(r["diagnostics"].get("selected_model", "unknown"))
              for r in rows]
    counts = Counter(labels)
    result["selected_model_counts"] = dict(sorted(counts.items()))
    result["selected_model_rates"] = {label: counts[label] / len(rows)
                                      for label in sorted(set(labels) | {"static", "velocity", "unknown"})}
    cv = [r["diagnostics"].get("cv") for r in rows]
    cv = [entry for entry in cv if isinstance(entry, dict)]
    flags = [entry["dynamic_admissible"] for entry in cv
             if isinstance(entry.get("dynamic_admissible"), bool)]
    result["cv_diagnostic_n"] = len(cv)
    result["cv_admissibility_diagnostic_n"] = len(flags)
    result["cv_dynamic_admissible_rate"] = float(np.mean(flags)) if flags else None
    result["mean_completed_cv_folds"] = float(np.mean([len(entry.get("folds", []))
                                                      for entry in cv])) if cv else None
    return result


def paired_reductions(chosen, baseline):
    """Descriptive paired differences; intentionally no cellwise p-values."""
    chosen_by_id = {row["case_id"]: row["error_m"] for row in chosen}
    baseline_by_id = {row["case_id"]: row["error_m"] for row in baseline}
    if (len(chosen_by_id) != len(chosen) or len(baseline_by_id) != len(baseline)
            or chosen_by_id.keys() != baseline_by_id.keys()):
        raise ValueError("Sensitivity comparisons require unique, matched case ids")
    differences = np.asarray([baseline_by_id[key] - chosen_by_id[key]
                              for key in chosen_by_id], dtype=float)
    return {
        "n_pairs": len(differences),
        "mean_reduction_m": float(differences.mean()),
        "median_reduction_m": float(np.median(differences)),
        "chosen_lower_error_fraction": float(np.mean(differences > 0)),
        "sign_convention": "baseline error minus frozen chosen error; positive favours chosen",
        "interpretation": "Descriptive paired comparison; no cellwise significance claim",
    }


def flatten_row(method, row, metadata):
    diagnostics = row["diagnostics"]
    raw_label = diagnostics.get("selected_model", "unknown")
    cv = diagnostics.get("cv")
    cv = cv if isinstance(cv, dict) else None
    return {
        **metadata, "method": method, "case_id": row["case_id"],
        "error_m": row["error_m"], "x": row["position"][0], "y": row["position"][1],
        "work_units": row["work_units"], "elapsed_seconds": row["elapsed_seconds"],
        "selected_model": normalized_model(raw_label), "selected_model_raw": raw_label,
        "cv_dynamic_admissible": cv.get("dynamic_admissible") if cv else None,
        "cv_completed_folds": len(cv.get("folds", [])) if cv else None,
        "velocity": row["velocity"], "diagnostics": diagnostics,
    }


def write_predictions(output, rows, complete):
    save(output / "predictions.json", {"complete": complete, "rows": rows})
    with (output / "predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "runs" / "live_reference")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "sensitivity")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory; sensitivity evidence is never overwritten")
    plan = read_plan()
    # No generated case or metric is available before this provenance check.
    checked = validate_frozen_run(ROOT, args.run)
    frozen = checked["frozen"]
    methods = {
        "llm_selected": dict(frozen["spec"]),
        "dynamic_robust": dict(BASELINES["dynamic_robust"]),
        "static_wls": dict(BASELINES["static_wls"]),
    }
    protocol = {
        "version": plan["version"],
        "evaluation_started_utc": datetime.now(timezone.utc).isoformat(),
        "registered_plan": plan,
        "plan_sha256": file_hash(PLAN_PATH),
        "sensitivity_script_sha256": file_hash(__file__),
        "frozen_method_sha256": file_hash(args.run / "tools" / "frozen_method.json"),
        "frozen_method_id": frozen["method_id"],
        "methods": methods,
        "pre_holdout_integrity_check": checked["audit"],
        "critical_source_sha256": load(args.run / "protocol.json")["source_hashes"],
        "environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                        "scipy": scipy.__version__, "platform": platform.platform()},
        "prohibition": plan["prohibition"],
    }
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "protocol.json", protocol)
    started = time.perf_counter()
    predictions, cell_summaries = [], []
    try:
        cells = make_cells(plan)
        save(args.output / "inputs.json", [case.to_dict() for cell in cells for case in cell["cases"]])
        for index, cell in enumerate(cells, 1):
            print(f"Sensitivity cell {index}/{len(cells)}: {cell['cell_id']}", flush=True)
            results, summaries = {}, {}
            for method, spec in methods.items():
                rows = evaluate(cell["cases"], spec)["rows"]
                results[method] = rows
                summaries[method] = summarize_rows(rows)
                predictions.extend(flatten_row(method, row, metadata)
                                   for row, metadata in zip(rows, cell["metadata"], strict=True))
            cell_summaries.append({
                "cell_id": cell["cell_id"], "scenario": cell["scenario"],
                "n_observations": cell["n_observations"],
                "nominal_contamination": cell["nominal_contamination"],
                "actual_contamination": cell["metadata"][0]["actual_contamination"],
                "cases": len(cell["cases"]), "methods": summaries,
                "paired_comparisons": {
                    method: paired_reductions(results["llm_selected"], results[method])
                    for method in METHOD_NAMES if method != "llm_selected"
                },
            })
            write_predictions(args.output, predictions, complete=False)
        if len(predictions) != plan["prediction_count"]:
            raise ValueError("Sensitivity prediction count differs from registered design")
        write_predictions(args.output, predictions, complete=True)
        save(args.output / "summary.json", {
            "status": "complete", "protocol": protocol,
            "case_count": plan["case_count"], "prediction_count": len(predictions),
            "cells": cell_summaries, "wall_seconds": time.perf_counter() - started,
            "interpretation": plan["inference_scope"],
            "work_unit_caveat": plan["work_unit_caveat"],
        })
    except Exception as error:
        write_predictions(args.output, predictions, complete=False)
        save(args.output / "summary.json", {
            "status": "failed", "protocol": protocol, "cells": cell_summaries,
            "prediction_count": len(predictions), "wall_seconds": time.perf_counter() - started,
            "error": f"{type(error).__name__}: {error}",
            "interpretation": "Partial evidence only; failed cells are not imputed or silently dropped",
        })
        raise
    print(f"Complete: {len(cell_summaries)} cells, {len(predictions)} predictions", flush=True)


if __name__ == "__main__":
    main()
