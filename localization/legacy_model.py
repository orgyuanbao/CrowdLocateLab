"""Observation-only range estimators with diagnostics and guarded selection.

No function in this module reads a case's truth, velocity, scenario, or metadata.
The target is x(0). Static and constant-velocity fits optimize standardized range
residuals, optional age weights, and an optional quadratic velocity penalty.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

import numpy as np
from scipy.optimize import least_squares


def _rho(z: np.ndarray, loss: str) -> np.ndarray:
    if loss == "linear":
        return z
    if loss == "huber":
        return np.where(z <= 1.0, z, 2.0 * np.sqrt(np.maximum(z, 0.0)) - 1.0)
    return 2.0 * (np.sqrt(1.0 + z) - 1.0)


def _mixed_loss(loss: str, n: int):
    """Robustify only measurement rows; keep velocity regularization quadratic."""
    def func(z):
        out = np.empty((3, z.size), dtype=float)
        out[0] = z
        out[1] = 1.0
        out[2] = 0.0
        if loss == "huber":
            zz = np.maximum(z[:n], 1e-30)
            large = zz > 1.0
            out[0, :n] = np.where(large, 2 * np.sqrt(zz) - 1, zz)
            out[1, :n] = np.where(large, 1 / np.sqrt(zz), 1.0)
            out[2, :n] = np.where(large, -0.5 * zz**(-1.5), 0.0)
        elif loss == "soft_l1":
            zz = 1 + z[:n]
            out[0, :n] = 2 * (np.sqrt(zz) - 1)
            out[1, :n] = zz**(-0.5)
            out[2, :n] = -0.5 * zz**(-1.5)
        return out
    return func


def _observations(case):
    anchors = np.asarray(case.anchors, dtype=float)
    times = np.asarray(case.times, dtype=float)
    ranges = np.asarray(case.ranges, dtype=float)
    sigma = np.asarray(case.sigma, dtype=float)
    n = len(times)
    if times.shape != (n,) or anchors.shape != (n, 2) or ranges.shape != (n,) or sigma.shape != (n,):
        raise ValueError("Expected anchors (n,2), times/ranges/sigma (n,).")
    if n < 3 or not all(np.all(np.isfinite(v)) for v in (anchors, times, ranges, sigma)):
        raise ValueError("At least three finite observations are required.")
    if np.any(ranges < 0) or np.any(sigma <= 0):
        raise ValueError("Ranges must be nonnegative and sigma strictly positive.")
    return anchors, times, ranges, sigma


def _initial_points(anchors, times, ranges, sigma, starts):
    # Solving pairwise differences of squared range equations gives a linear
    # initializer. Centering prevents avoidable cancellation in large coordinates.
    inv = 1 / np.maximum(sigma, 1e-8) ** 2
    center = np.average(anchors, axis=0, weights=inv)
    a = anchors - center
    sq = np.sum(a*a, axis=1) - ranges*ranges
    weights = np.sqrt(inv / max(inv.mean(), 1e-12))
    design = 2 * (a - np.average(a, axis=0, weights=inv))
    rhs = sq - np.average(sq, weights=inv)
    delta = np.linalg.lstsq(design * weights[:, None], rhs * weights, rcond=1e-8)[0]
    spread = max(float(np.median(ranges)), float(np.std(anchors, axis=0).max()), 1.0)
    if not np.all(np.isfinite(delta)) or np.linalg.norm(delta) > 5*spread:
        delta = np.zeros(2)
    linear = center + delta
    # Mirror and perpendicular candidates reduce branch ambiguity from narrow
    # anchor geometry, using only the observation geometry.
    _, _, vv = np.linalg.svd(anchors - anchors.mean(axis=0), full_matrices=False)
    perpendicular = vv[-1]
    points = [linear, center, center - delta,
              center + 0.7*spread*perpendicular,
              center - 0.7*spread*perpendicular]
    return points[:starts], center, spread


def _fit(anchors, times, ranges, sigma, model, config, points, center, spread):
    n = len(times)
    dynamic = model == "dynamic"
    dimension = 4 if dynamic else 2
    f_scale = config["f_scale"]
    loss = config["loss"]
    half_life = config["half_life"]
    weights = np.ones(n) if half_life <= 0 else np.exp2(-np.maximum(-times, 0) / half_life)
    if float(weights.sum()) < 1e-12:
        raise ValueError('Time decay removed all effective information; increase half_life.')
    rootw = np.sqrt(weights)
    scale = rootw / sigma
    ridge_root = np.sqrt(config["ridge"]) if dynamic else 0.0
    add_ridge = dynamic and ridge_root > 0

    def measurements(p):
        relative = p[:2] - anchors
        if dynamic:
            relative = relative + times[:, None]*p[2:]
        lengths = np.linalg.norm(relative, axis=1)
        return (lengths - ranges)*scale

    def residual(p):
        r = measurements(p)
        if add_ridge:
            r = np.r_[r, ridge_root*p[2:]]
        return r

    def jacobian(p):
        relative = p[:2] - anchors
        if dynamic:
            relative = relative + times[:, None]*p[2:]
        lengths = np.maximum(np.linalg.norm(relative, axis=1), 1e-10)
        unit = relative / lengths[:, None] * scale[:, None]
        jac = np.hstack((unit, times[:, None]*unit)) if dynamic else unit
        if add_ridge:
            jac = np.vstack((jac, np.column_stack((np.zeros((2, 2)), ridge_root*np.eye(2)))))
        return jac

    time_scale = max(float(np.ptp(times)), 1.0)
    # Finite, intentionally broad numerical bounds are observation-derived.
    lower = center - 10*spread
    upper = center + 10*spread
    if dynamic:
        lower = np.r_[lower, [-10*spread/time_scale]*2]
        upper = np.r_[upper, [10*spread/time_scale]*2]
    candidates = []
    total_nfev = 0
    for point in points:
        initial = np.r_[point, 0.0, 0.0] if dynamic else point.copy()
        initial = np.clip(initial, lower+1e-7, upper-1e-7)
        try:
            fitted = least_squares(
                residual, initial, jac=jacobian,
                bounds=(lower, upper), loss=_mixed_loss(loss, n), f_scale=f_scale,
                max_nfev=120, ftol=2e-6, xtol=2e-6, gtol=2e-6,
                x_scale=np.r_[spread, spread, spread/time_scale, spread/time_scale]
                if dynamic else np.array([spread, spread]),
            )
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue
        total_nfev += int(fitted.nfev)
        r = measurements(fitted.x)
        measurement_cost = float(np.sum(f_scale**2 * _rho((r/f_scale)**2, loss)))
        if np.all(np.isfinite(fitted.x)) and np.isfinite(measurement_cost):
            candidates.append((float(2*fitted.cost), measurement_cost, fitted, r))
    if not candidates:
        raise RuntimeError(f"Every {model} numerical start failed.")
    _, measurement_cost, fitted, r = min(candidates, key=lambda item: item[0])
    # Report the unregularized, standardized data Jacobian, so ridge cannot
    # disguise non-identifiability. Velocity columns retain their natural units.
    raw_jac = jacobian(fitted.x)[:n]
    singular = np.linalg.svd(raw_jac, compute_uv=False)
    condition = float(singular[0]/singular[-1]) if singular[-1] > 1e-10 else None
    n_eff = float(weights.sum()**2 / np.sum(weights**2))
    # This is a heuristic robust BIC, not a likelihood-ratio calibration:
    # normalize loss by sum of weights, floor residual variance at 1e-8,
    # apply k*log(n_eff), and add the specified extra dynamic-model penalty.
    score = n_eff*np.log(max(measurement_cost / weights.sum(), 1e-8))
    score += dimension*np.log(max(n_eff, 2.0))
    if dynamic:
        score += config["gate_penalty"]
    diagnostics = {
        "fit_cost": measurement_cost,
        "objective_with_ridge": float(2*fitted.cost),
        "standardized_residual_rmse": float(np.sqrt(np.mean(r*r))),
        "jacobian_singular_values": singular.tolist(),
        "jacobian_condition": condition,
        "jacobian_rank": int(np.linalg.matrix_rank(raw_jac, tol=1e-8)),
        "ill_conditioned": condition is None or condition > 1e6,
        "effective_observations": n_eff,
        "robust_bic_score": float(score),
        "solver_success": bool(fitted.success),
        "solver_status": int(fitted.status),
        "solver_message": str(fitted.message),
        "starts_attempted": len(points),
        "starts_valid": len(candidates),
        "at_numerical_bound": bool(np.any(fitted.active_mask != 0)),
        "estimated_velocity": fitted.x[2:].tolist() if dynamic else [0.0, 0.0],
        "selection_score_formula": "n_eff*log(max(sum(rho_f(weighted_standardized_residual))/sum(weights),1e-8)) + k*log(max(n_eff,2)) + (gate_penalty if dynamic else 0)",
    }
    return {"position": fitted.x[:2].tolist(), "diagnostics": diagnostics,
            "nfev": total_nfev, "selected_model": model}


def estimate(case, config: dict[str, Any]) -> dict[str, Any]:
    """Estimate current location from observations only.

    ``half_life=0`` disables age weighting. ``ridge`` is the coefficient of
    ||velocity||^2 in the total loss. ``starts`` is 1, 3, or 5. ``select`` compares
    static (k=2) and dynamic (k=4) fits with a documented robust-BIC heuristic.
    Insufficient temporal information makes selection fall back to static.
    """
    started = perf_counter()
    cfg = {"model": "static", "loss": "linear", "f_scale": 1.0,
           "half_life": 0.0, "starts": 1, "ridge": 0.0, "gate_penalty": 0.0}
    cfg.update(config)
    if cfg["model"] not in ("centroid", "static", "dynamic", "select"):
        raise ValueError("model must be centroid, static, dynamic, or select")
    if cfg["loss"] not in ("linear", "huber", "soft_l1"):
        raise ValueError("loss must be linear, huber, or soft_l1")
    if cfg["starts"] not in (1, 3, 5):
        raise ValueError("starts must be 1, 3, or 5")
    for key in ("f_scale", "half_life", "ridge", "gate_penalty"):
        cfg[key] = float(cfg[key])
        if not np.isfinite(cfg[key]):
            raise ValueError(f"{key} must be finite")
    if cfg["f_scale"] <= 0 or cfg["half_life"] < 0 or cfg["ridge"] < 0:
        raise ValueError("f_scale must be positive; half_life and ridge nonnegative")
    anchors, times, ranges, sigma = _observations(case)
    if cfg["model"] == "centroid":
        # Deliberately simple reference method: nearer, better calibrated
        # observations have greater influence on the anchor centroid.
        inv = 1 / np.maximum(ranges*sigma, 1e-8)
        position = np.average(anchors, axis=0, weights=inv)
        result = {"position": position.tolist(), "diagnostics": {
            "method": "anchor centroid weighted by 1/(observed_range*sigma)",
            "estimated_velocity": [0.0, 0.0],
        }, "nfev": 0, "selected_model": "centroid"}
    else:
        points, center, spread = _initial_points(anchors, times, ranges, sigma, cfg["starts"])
        enough_time = len(times) >= 6 and float(np.ptp(times)) >= 1.0
        if cfg["model"] == "select" and enough_time:
            static = _fit(anchors, times, ranges, sigma, "static", cfg, points, center, spread)
            dynamic = _fit(anchors, times, ranges, sigma, "dynamic", cfg, points, center, spread)
            d_diag = dynamic["diagnostics"]
            allow_dynamic = (d_diag["jacobian_rank"] == 4
                             and not d_diag["ill_conditioned"]
                             and not d_diag["at_numerical_bound"]
                             and d_diag["effective_observations"] >= 6.0)
            result = dynamic if allow_dynamic and d_diag["robust_bic_score"] < static["diagnostics"]["robust_bic_score"] else static
            result["nfev"] = static["nfev"] + dynamic["nfev"]
            result["diagnostics"]["model_scores"] = {
                "static": static["diagnostics"]["robust_bic_score"],
                "dynamic": dynamic["diagnostics"]["robust_bic_score"],
            }
            result["diagnostics"]["dynamic_admissible"] = allow_dynamic
            if not allow_dynamic:
                result["diagnostics"]["dynamic_rejection_reason"] = "Insufficient effective observations, rank, conditioning, or an active numerical bound."
        else:
            chosen = cfg["model"] if cfg["model"] != "select" else "static"
            if chosen == "dynamic" and not enough_time:
                chosen = "static"
            result = _fit(anchors, times, ranges, sigma, chosen, cfg, points, center, spread)
            if chosen != cfg["model"] and cfg["model"] != "select":
                result["diagnostics"]["fallback_reason"] = "Insufficient temporal information for velocity."
    result["elapsed_seconds"] = float(perf_counter()-started)
    return result
