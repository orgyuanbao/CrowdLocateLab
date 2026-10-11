"""Composable observation-only solvers; no ground-truth fields are read here."""

from dataclasses import dataclass
import hashlib
from time import perf_counter

import numpy as np
from scipy.optimize import least_squares

from .legacy_model import estimate as _legacy_estimate


DEFAULT_SPEC = dict(model="velocity", residual="range", scale="reported",
                    loss="soft_l1", f_scale=1.5, starts=3, ridge=.05,
                    trim_fraction=0., consensus_trials=0, gate_margin=.05,
                    seed=0, cv_folds=3, log_bias_correction=True, log_sd=.1)
BASELINES = {
    "centroid": dict(model="centroid"),
    "static_wls": dict(model="static", loss="linear", starts=3),
    "static_robust": dict(model="static", loss="soft_l1", f_scale=1.5, starts=3),
    "dynamic_robust": dict(model="dynamic", loss="soft_l1", f_scale=1.5, ridge=.05, starts=3),
    "bic_select": dict(model="select", loss="soft_l1", f_scale=1.5, ridge=.05,
                       starts=3, gate_penalty=2.),
}


@dataclass(frozen=True)
class Observations:
    anchors: np.ndarray
    times: np.ndarray
    ranges: np.ndarray
    sigma: np.ndarray

    def subset(self, indices):
        return Observations(*(getattr(self, name)[indices] for name in
                              ("anchors", "times", "ranges", "sigma")))


def _read_observations(case):
    a, t, d, s = (np.asarray(getattr(case, key), dtype=float) for key in
                  ("anchors", "times", "ranges", "sigma"))
    if t.ndim != 1:
        raise ValueError("times must have shape (n,)")
    n = len(t)
    if n < 3 or a.shape != (n, 2) or d.shape != (n,) or s.shape != (n,):
        raise ValueError("Need n>=3, anchors (n,2), times/ranges/sigma (n,)")
    if not all(np.isfinite(v).all() for v in (a, t, d, s)) or np.any(d <= 0) or np.any(s <= 0):
        raise ValueError("Observations must be finite; ranges and sigma strictly positive")
    return Observations(a, t, d, s)


def validate_spec(spec):
    extra = set(spec)-set(DEFAULT_SPEC)
    if extra:
        raise ValueError(f"Unknown method keys: {sorted(extra)}")
    cfg = dict(DEFAULT_SPEC, **spec)
    for key, choices in {"model": ("static", "velocity", "cv_select"),
                         "residual": ("range", "log_range"),
                         "scale": ("reported", "constant"),
                         "loss": ("linear", "soft_l1", "huber", "cauchy")}.items():
        if cfg[key] not in choices:
            raise ValueError(f"Invalid {key}: {cfg[key]}")
    for key in ("f_scale", "ridge", "trim_fraction", "gate_margin", "log_sd"):
        cfg[key] = float(cfg[key])
        if not np.isfinite(cfg[key]):
            raise ValueError(f"{key} must be finite")
    if cfg["f_scale"] <= 0 or cfg["ridge"] < 0 or not 0 <= cfg["trim_fraction"] <= .35:
        raise ValueError("Require f_scale>0, ridge>=0, trim_fraction in [0,.35]")
    if not 0 <= cfg["gate_margin"] < 1 or not 0 < cfg["log_sd"] <= .6:
        raise ValueError("Require gate_margin in [0,1), log_sd in (0,.6]")
    for key in ("starts", "consensus_trials", "seed", "cv_folds"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], (int, np.integer)):
            raise ValueError(f"{key} must be an integer")
        cfg[key] = int(cfg[key])
    if cfg["starts"] not in (1, 3, 5) or not 0 <= cfg["consensus_trials"] <= 12:
        raise ValueError("starts must be 1/3/5 and consensus_trials in [0,12]")
    if not 2 <= cfg["cv_folds"] <= 5 or cfg["seed"] < 0:
        raise ValueError("cv_folds must be 2..5; seed must be nonnegative")
    if not isinstance(cfg["log_bias_correction"], bool):
        raise ValueError("log_bias_correction must be boolean")
    return cfg


def rho(z, loss):
    z = np.maximum(np.asarray(z), 0.)
    if loss == "linear": return z
    if loss == "soft_l1": return 2*(np.sqrt(1+z)-1)
    if loss == "huber": return np.where(z <= 1, z, 2*np.sqrt(z)-1)
    if loss == "cauchy": return np.log1p(z)
    raise ValueError(loss)


def _mixed_loss(name, n):
    def loss(z):
        out = np.empty((3, len(z)))
        out[0], out[1], out[2] = z, 1., 0.
        q = np.maximum(z[:n], 1e-30)
        if name == "soft_l1":
            out[:, :n] = np.array([2*(np.sqrt(1+q)-1), (1+q)**-.5, -.5*(1+q)**-1.5])
        elif name == "huber":
            large = q > 1
            out[:, :n] = np.array([np.where(large, 2*np.sqrt(q)-1, q),
                np.where(large, q**-.5, 1), np.where(large, -.5*q**-1.5, 0)])
        elif name == "cauchy":
            out[:, :n] = np.array([np.log1p(q), 1/(1+q), -1/(1+q)**2])
        return out
    return loss


def _scales(obs, cfg, constant=None):
    if cfg["residual"] == "range":
        scales = obs.sigma.copy()
    else:
        # Invert the lognormal coefficient-of-variation relation for an
        # observation-based delta approximation including coordinate uncertainty.
        scales = np.sqrt(np.log1p((obs.sigma/np.maximum(obs.ranges, .1))**2))
    scales = np.maximum(scales, 1e-5)
    if cfg["scale"] == "constant":
        scalar = float(np.median(scales)) if constant is None else float(constant)
        scales = np.full(len(obs.times), scalar)
    return scales


def residual_jacobian(obs, parameters, model, cfg, scales):
    """Residual and analytic Jacobian, exposed for independent numerical QA."""
    moving = model == "velocity"
    relative = parameters[:2]-obs.anchors
    if moving:
        relative = relative+obs.times[:, None]*parameters[2:]
    distance = np.linalg.norm(relative, axis=1)
    unit = relative/np.maximum(distance, 1e-10)[:, None]
    jac = np.hstack((unit, obs.times[:, None]*unit)) if moving else unit
    if cfg["residual"] == "log_range":
        clipped = np.maximum(distance, .1)
        shift = .5*cfg["log_sd"]**2 if cfg["log_bias_correction"] else 0.
        residual = (np.log(clipped)-np.log(np.maximum(obs.ranges, .1))-shift)/scales
        jac = jac*(distance > .1)[:, None]/(clipped*scales)[:, None]
    else:
        residual = (distance-obs.ranges)/scales
        jac = jac/scales[:, None]
    return residual, jac


def _initials(obs, scales, starts):
    weights = 1/np.maximum(scales, 1e-5)**2
    center = np.average(obs.anchors, axis=0, weights=weights)
    local = obs.anchors-center
    lhs = 2*(local-np.average(local, axis=0, weights=weights))
    rhs = np.sum(local**2, axis=1)-obs.ranges**2
    rhs = rhs-np.average(rhs, weights=weights)
    root = np.sqrt(weights/np.mean(weights))
    delta = np.linalg.lstsq(lhs*root[:, None], rhs*root, rcond=1e-8)[0]
    spread = max(float(np.median(obs.ranges)), float(np.std(obs.anchors, axis=0).max()), 1.)
    if not np.isfinite(delta).all() or np.linalg.norm(delta) > 5*spread:
        delta = np.zeros(2)
    _, _, right = np.linalg.svd(local, full_matrices=False)
    cross = right[-1]
    points = [center+delta, center, center-delta, center+.7*spread*cross, center-.7*spread*cross]
    return points[:starts], center, spread


def _geometry(obs, model, center, spread):
    lower, upper = center-10*spread, center+10*spread
    size = np.array([spread, spread])
    if model == "velocity":
        speed_scale = spread/max(float(np.ptp(obs.times)), 1.)
        lower, upper = np.r_[lower, [-10*speed_scale]*2], np.r_[upper, [10*speed_scale]*2]
        size = np.r_[size, [speed_scale]*2]
    return lower, upper, size


def _one_solve(obs, cfg, model, scales, initial, indices, bounds, tracker, max_nfev=100):
    sub = obs.subset(indices)
    scale = scales[indices]
    ridge = np.sqrt(cfg["ridge"]) if model == "velocity" else 0.
    n = len(indices)

    def function(p):
        tracker["nfev"] += 1
        result, _ = residual_jacobian(sub, p, model, cfg, scale)
        return np.r_[result, ridge*p[2:]] if ridge else result

    def derivative(p):
        tracker["njev"] += 1
        _, result = residual_jacobian(sub, p, model, cfg, scale)
        if ridge:
            result = np.vstack((result, np.column_stack((np.zeros((2, 2)), ridge*np.eye(2)))))
        return result

    lower, upper, size = bounds
    tracker["solves"] += 1
    return least_squares(function, np.clip(initial, lower+1e-7, upper-1e-7), jac=derivative,
            bounds=(lower, upper), x_scale=size, loss=_mixed_loss(cfg["loss"], n),
            f_scale=cfg["f_scale"], max_nfev=max_nfev, ftol=3e-6, xtol=3e-6, gtol=3e-6)


def _retained(residuals, cfg, dimension):
    n = len(residuals)
    h = max(dimension+1, int(np.ceil((1-cfg["trim_fraction"])*n)))
    h = min(h, n)
    return np.argsort(np.abs(residuals), kind="stable")[:h]


def _score(parameters, obs, model, cfg, scales):
    residual, jacobian = residual_jacobian(obs, parameters, model, cfg, scales)
    ids = _retained(residual, cfg, len(parameters))
    f = cfg["f_scale"]
    measurement = float(np.sum(f*f*rho((residual[ids]/f)**2, cfg["loss"])))
    penalty = float(cfg["ridge"]*np.dot(parameters[2:], parameters[2:])) if model == "velocity" else 0.
    return measurement+penalty, measurement, residual, jacobian, ids


def _rng(obs, seed):
    packed = b"".join(np.ascontiguousarray(getattr(obs, field), dtype="<f8").tobytes()
                       for field in ("anchors", "times", "ranges", "sigma"))
    observed_seed = int.from_bytes(hashlib.blake2b(packed, digest_size=8).digest(), "little")
    return np.random.default_rng(np.random.SeedSequence([seed, observed_seed]))


def _geometry_proxy(jacobian, residual):
    """An observable radius scale, not a confidence interval or CRLB claim."""
    rank = np.linalg.matrix_rank(jacobian, tol=1e-8)
    if rank < jacobian.shape[1]:
        return 100., "rank-deficient data Jacobian; conservative proxy cap; uncalibrated"
    covariance_shape = np.linalg.pinv(jacobian.T@jacobian, rcond=1e-10)
    residual_scale = max(1., float(np.median(np.abs(residual)))/.6745)
    raw = float(np.sqrt(max(float(np.trace(covariance_shape[:2, :2])), 0.))*residual_scale)
    return float(np.clip(raw, .2, 100.)), "data-Jacobian pseudoinverse and absolute residual proxy; uncalibrated"


def _fit_model(obs, cfg, model, tracker):
    n = len(obs.times)
    if model == "velocity" and (n < 6 or np.ptp(obs.times) < 1.):
        model = "static"
        fallback = "Insufficient observations or temporal span for velocity."
    else:
        fallback = None
    scales = _scales(obs, cfg)
    points, center, spread = _initials(obs, scales, cfg["starts"])
    bounds = _geometry(obs, model, center, spread)
    parameters = [np.r_[p, 0., 0.] if model == "velocity" else p for p in points]
    dimension = len(parameters[0])
    subset_candidates = []
    rng = _rng(obs, cfg["seed"])
    subset_size = min(n, max(dimension+2, int(np.ceil(.50*n))))
    for _ in range(cfg["consensus_trials"]):
        ids = np.sort(rng.choice(n, subset_size, replace=False))
        subset = obs.subset(ids)
        subset_scales = _scales(subset, cfg)
        subset_points, _, _ = _initials(subset, subset_scales, 1)
        p = np.r_[subset_points[0], 0., 0.] if model == "velocity" else subset_points[0]
        # Subset fitting uses its own constant calibration scale and no labels.
        fitted = _one_solve(subset, cfg, model, subset_scales, p,
                            np.arange(subset_size), bounds, tracker, max_nfev=60)
        score = _score(fitted.x, obs, model, cfg, scales)[0]
        subset_candidates.append((score, fitted.x))
    if subset_candidates:
        parameters.append(min(subset_candidates, key=lambda item: item[0])[1])
    candidates = []
    for initial in parameters:
        p = initial
        ids = np.arange(n)
        rounds = 3 if cfg["trim_fraction"] else 1
        completed = 0
        for _ in range(rounds):
            fitted = _one_solve(obs, cfg, model, scales, p, ids, bounds, tracker)
            p = fitted.x
            completed += 1
            residual, _ = residual_jacobian(obs, p, model, cfg, scales)
            next_ids = _retained(residual, cfg, dimension)
            if np.array_equal(np.sort(ids), np.sort(next_ids)):
                break
            ids = next_ids
        summary = _score(p, obs, model, cfg, scales)
        candidates.append((summary[0], fitted, summary, completed))
    _, fitted, summary, rounds = min(candidates, key=lambda item: item[0])
    objective, cost, residual, jacobian, ids = summary
    singular = np.linalg.svd(jacobian[ids], compute_uv=False)
    condition = float(singular[0]/singular[-1]) if singular[-1] > 1e-10 else None
    rank = int(np.linalg.matrix_rank(jacobian[ids], tol=1e-8))
    geometry_scale, geometry_note = _geometry_proxy(jacobian[ids], residual)
    diagnostic = {
        "selected_model": model, "fit_cost": cost, "regularized_objective": objective,
        "all_observation_robust_cost": float(np.sum(cfg["f_scale"]**2*rho((residual/cfg["f_scale"])**2, cfg["loss"]))),
        "standardized_residuals": residual.tolist(), "retained_indices": ids.tolist(),
        "retained_count": int(len(ids)), "trim_rounds": rounds,
        "starts_attempted": len(parameters), "consensus_trials": cfg["consensus_trials"],
        "jacobian_singular_values": singular.tolist(), "jacobian_condition": condition,
        "jacobian_rank": rank, "ill_conditioned": condition is None or condition > 1e6,
        "solver_success": bool(fitted.success), "solver_status": int(fitted.status),
        "at_numerical_bound": bool(np.any(fitted.active_mask)),
        "constant_scale": float(scales[0]) if cfg["scale"] == "constant" else None,
        "fallback_reason": fallback,
        "geometry_scale_m": geometry_scale, "geometry_scale_note": geometry_note,
    }
    return {"position": fitted.x[:2].tolist(),
            "velocity": fitted.x[2:].tolist() if model == "velocity" else [0., 0.],
            "selected_model": model, "diagnostics": diagnostic}


def _predictive_select(obs, cfg, tracker):
    n, k = len(obs.times), cfg["cv_folds"]
    order = np.argsort(obs.times, kind="stable")
    folds = [order[j::k] for j in range(k)]
    # A fixed inner budget limits model-selection cost. Both models use the
    # same policy; the final selected model uses the full requested budget.
    inner = dict(cfg, starts=1, consensus_trials=min(3, cfg["consensus_trials"]))
    scores = {"static": [], "velocity": []}
    fold_records = []
    allowed = True
    for validation in folds:
        training = np.setdiff1d(np.arange(n), validation)
        if len(training) < 6 or np.ptp(obs.times[training]) < 1.:
            allowed = False
            break
        train, valid = obs.subset(training), obs.subset(validation)
        row = {"train_indices": training.tolist(), "validation_indices": validation.tolist()}
        for name in ("static", "velocity"):
            fitted = _fit_model(train, inner, name, tracker)
            parameters = np.r_[fitted["position"], fitted["velocity"]] if name == "velocity" else np.array(fitted["position"])
            constant = fitted["diagnostics"]["constant_scale"]
            valid_scales = _scales(valid, cfg, constant=constant)
            residual, _ = residual_jacobian(valid, parameters, name, cfg, valid_scales)
            # Fixed soft-L1 at scale one evaluates predictions regardless of
            # the optimization loss; all held-out observations are retained.
            values = rho(residual**2, "soft_l1")
            scores[name].extend(values.tolist())
            row[name] = float(np.mean(values))
            if name == "velocity" and (fitted["selected_model"] != "velocity" or
                    fitted["diagnostics"]["jacobian_rank"] < 4 or fitted["diagnostics"]["at_numerical_bound"]):
                allowed = False
        fold_records.append(row)
    average = {name: float(np.mean(values)) if values else None for name, values in scores.items()}
    choose_velocity = (allowed and len(fold_records) == k and
                       average["velocity"] < (1-cfg["gate_margin"])*average["static"])
    selected = "velocity" if choose_velocity else "static"
    result = _fit_model(obs, cfg, selected, tracker)
    result["diagnostics"]["cv"] = {
        "scores": average, "dynamic_admissible": bool(allowed),
        "gate_margin": cfg["gate_margin"], "folds": fold_records,
        "selection_rule": "velocity_score < (1-gate_margin)*static_score",
        "score": "Mean held-out soft-L1 of standardized residuals; scale=1; no trimming",
        "inner_starts": 1, "inner_consensus_trials": inner["consensus_trials"],
        "fold_assignment": "time-sorted interleaving; every observation held out once",
    }
    return result


def fit_baseline(case, name):
    if name not in BASELINES:
        raise ValueError(f"Unknown baseline {name}")
    start = perf_counter()
    old = _legacy_estimate(case, BASELINES[name])
    obs = _read_observations(case)
    chosen = "velocity" if old["selected_model"] == "dynamic" else "static"
    parameters = np.r_[old["position"], old["diagnostics"]["estimated_velocity"]] if chosen == "velocity" else np.array(old["position"])
    cfg = dict(DEFAULT_SPEC, residual="range", scale="reported")
    residual, jacobian = residual_jacobian(obs, parameters, chosen, cfg, obs.sigma)
    geometry_scale, geometry_note = _geometry_proxy(jacobian, residual)
    return {"position": old["position"], "velocity": old["diagnostics"]["estimated_velocity"],
            "diagnostics": dict(old["diagnostics"], baseline=name, geometry_scale_m=geometry_scale,
                                geometry_scale_note=geometry_note),
            "selected_model": old["selected_model"], "work_units": old["nfev"],
            "elapsed_seconds": float(perf_counter()-start)}


def fit(case, spec=None):
    """Fit using observation fields only. Work units count all solver residual calls."""
    spec = {} if spec is None else spec
    if "baseline" in spec:
        if set(spec) != {"baseline"}:
            raise ValueError("A baseline specification cannot mix new-method options")
        return fit_baseline(case, spec["baseline"])
    start = perf_counter()
    cfg = validate_spec(spec)
    obs = _read_observations(case)
    tracker = dict(nfev=0, njev=0, solves=0)
    result = (_predictive_select(obs, cfg, tracker) if cfg["model"] == "cv_select" else
              _fit_model(obs, cfg, cfg["model"], tracker))
    result["work_units"] = int(tracker["nfev"])
    result["elapsed_seconds"] = float(perf_counter()-start)
    result["diagnostics"]["computation"] = tracker
    result["diagnostics"]["spec"] = cfg
    return result
