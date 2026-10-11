"""Synthetic, deliberately imperfect, asynchronous crowd-ranging observations.

The uncertainty is a calibration estimate computed from observations, never from
the unobservable error.  Coordinates are local planar metres, time is seconds,
and the requested target is the position at time zero.  This is a teaching
benchmark, not a calibrated simulator of a particular Bluetooth radio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


SCENARIOS = (
    "static_clean", "static_outliers", "moving_clean", "moving_outliers",
    "poor_geometry", "accelerating",
    "sparse", "calibration_shift", "high_contamination",
)


@dataclass
class Case:
    case_id: str
    scenario: str
    anchors: np.ndarray
    times: np.ndarray
    ranges: np.ndarray
    sigma: np.ndarray
    truth: np.ndarray
    velocity: np.ndarray
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "anchors": np.asarray(self.anchors, dtype=float).tolist(),
            "times": np.asarray(self.times, dtype=float).tolist(),
            "ranges": np.asarray(self.ranges, dtype=float).tolist(),
            "sigma": np.asarray(self.sigma, dtype=float).tolist(),
            "truth": np.asarray(self.truth, dtype=float).tolist(),
            "velocity": np.asarray(self.velocity, dtype=float).tolist(),
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Case":
        return cls(
            case_id=str(value["case_id"]), scenario=str(value["scenario"]),
            anchors=np.asarray(value["anchors"], dtype=float),
            times=np.asarray(value["times"], dtype=float),
            ranges=np.asarray(value["ranges"], dtype=float),
            sigma=np.asarray(value["sigma"], dtype=float),
            truth=np.asarray(value["truth"], dtype=float),
            velocity=np.asarray(value["velocity"], dtype=float),
            meta=dict(value.get("meta", {})),
        )


def generate_case(seed: int, scenario: str, n: int = 16,
                  contamination: float | None = None, log_sd: float | None = None) -> Case:
    """Produce one independent synthetic episode with a recorded seed.

    BLE-like errors are multiplicative lognormal errors with log SD 0.10 and
    mean-one correction.  Each reported anchor additionally has independent
    0.70 m SD GNSS errors in both planar coordinates.  Outlier scenarios corrupt
    15--25% of ranges.  The poor-geometry case uses a 14-degree anchor sector.
    Accelerating trajectories violate the constant-velocity fitting model.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario!r}; choose from {SCENARIOS}")
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 6:
        raise ValueError("At least six observations are required.")
    if contamination is not None and (not np.isfinite(contamination) or not 0 <= contamination <= .60):
        raise ValueError("contamination must be between zero and 0.60")
    if log_sd is not None and (not np.isfinite(log_sd) or not 0 < log_sd <= .60):
        raise ValueError("log_sd must be positive and no greater than 0.60")
    rng = np.random.default_rng(int(seed))
    truth = rng.uniform(-25.0, 25.0, 2)
    times = np.sort(rng.uniform(-30.0, -1.0, n))
    times[-1] = rng.uniform(-0.8, 0.0)
    heading = rng.uniform(-np.pi, np.pi)
    moving = scenario in ("moving_clean", "moving_outliers", "poor_geometry", "accelerating",
                          "sparse", "calibration_shift", "high_contamination")
    speed = rng.uniform(0.35, 1.10) if moving else 0.0
    velocity = speed * np.array([np.cos(heading), np.sin(heading)])
    acceleration = np.zeros(2)
    if scenario == "accelerating":
        # A component perpendicular to instantaneous velocity bends the path.
        acc_heading = heading + rng.choice([-1, 1]) * rng.uniform(0.6, 2.3)
        acceleration = rng.uniform(0.025, 0.065) * np.array([
            np.cos(acc_heading), np.sin(acc_heading),
        ])
    target_history = truth + times[:, None] * velocity
    target_history += 0.5 * times[:, None] ** 2 * acceleration
    if scenario == "poor_geometry":
        sector_heading = rng.uniform(-np.pi, np.pi)
        angles = sector_heading + rng.uniform(-np.deg2rad(7), np.deg2rad(7), n)
        radii = rng.uniform(18.0, 43.0, n)
    else:
        angles = rng.uniform(-np.pi, np.pi, n)
        radii = rng.uniform(10.0, 42.0, n)
    true_anchors = truth + radii[:, None] * np.column_stack((np.cos(angles), np.sin(angles)))
    anchor_sd = 0.70
    log_sd = float(log_sd if log_sd is not None else (.16 if scenario == "calibration_shift" else .10))
    calibration_multiplier = 1.30 if scenario == "calibration_shift" else 1.0
    additive_sd = 0.25
    anchors = true_anchors + rng.normal(0.0, anchor_sd, (n, 2))
    true_ranges = np.linalg.norm(target_history - true_anchors, axis=1)
    ranges = true_ranges * np.exp(rng.normal(-0.5 * log_sd**2, log_sd, n))
    ranges = np.maximum(0.10, ranges + rng.normal(0.0, additive_sd, n))
    ranges *= calibration_multiplier
    outlier_mask = np.zeros(n, dtype=bool)
    outlier_rate = 0.0
    if contamination is not None or scenario in ("static_outliers", "moving_outliers", "high_contamination"):
        outlier_rate = float(contamination if contamination is not None else
                             rng.uniform(.35, .45) if scenario == "high_contamination" else
                             rng.uniform(0.15, 0.25))
        count = int(round(n * outlier_rate))
        ids = rng.choice(n, size=count, replace=False)
        outlier_mask[ids] = True
        high = rng.random(count) < 0.75
        multipliers = np.where(high, rng.uniform(1.7, 2.8, count), rng.uniform(0.30, 0.60, count))
        ranges[ids] *= multipliers
    # This plug-in scale is available to a real estimator from a radio-noise
    # calibration and observed range. It does not reveal the outlier mask.
    reported_log_sd = .10 if scenario == "calibration_shift" else log_sd
    range_cv = np.sqrt(np.expm1(reported_log_sd**2))
    sigma = np.sqrt((range_cv * ranges) ** 2 + anchor_sd**2 + additive_sd**2)
    return Case(
        case_id=f"{scenario}-{int(seed)}", scenario=scenario,
        anchors=anchors, times=times, ranges=ranges, sigma=sigma,
        truth=truth, velocity=velocity,
        meta={
            "seed": int(seed), "units": "metres, seconds",
            "target_time": 0.0, "observation_window_seconds": [-30, 0],
            "noise_model": "mean-one lognormal range multiplier plus Gaussian additive noise",
            "range_log_sd": log_sd, "range_additive_sd_m": additive_sd,
            "reported_range_log_sd": reported_log_sd,
            "calibration_multiplier_for_evaluation_only": calibration_multiplier,
            "n_observations": int(n),
            "gnss_coordinate_sd_m": anchor_sd,
            "sigma_formula": "sqrt(expm1(reported_log_sd^2)*observed_range^2 + gnss_sd^2 + additive_sd^2)",
            "nominal_outlier_rate": outlier_rate,
            "actual_outlier_fraction": float(outlier_mask.mean()),
            "outlier_indices_for_evaluation_only": np.flatnonzero(outlier_mask).tolist(),
            "acceleration_for_evaluation_only": acceleration.tolist(),
            "geometry_sector_width_degrees": 14 if scenario == "poor_geometry" else 360,
            "synthetic_only": True,
        },
    )


def generate_suite(seed: int, per_scenario: int = 12, scenarios=None) -> list[Case]:
    """Create reproducible episodes; seed streams do not depend on list order."""
    if per_scenario < 1:
        raise ValueError("per_scenario must be positive")
    selected = list(SCENARIOS if scenarios is None else scenarios)
    unknown = set(selected) - set(SCENARIOS)
    if unknown:
        raise ValueError(f"Unknown scenarios: {sorted(unknown)}")
    cases = []
    for scenario in selected:
        scenario_id = SCENARIOS.index(scenario)
        child_seeds = np.random.SeedSequence([int(seed), scenario_id]).spawn(per_scenario)
        for child in child_seeds:
            case_seed = int(child.generate_state(1, dtype=np.uint32)[0])
            cases.append(generate_case(case_seed, scenario, n=8 if scenario == "sparse" else 16))
    return cases
