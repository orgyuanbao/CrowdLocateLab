"""Application-only numerical quality screening of an unchanged frozen estimate.

This module never replaces the point estimate or its recorded calibration. Its
extra centered fit is a diagnostic probe, not a new research result. Thresholds
are explicit engineering choices, not fitted on the preserved holdout outcomes.
"""
from time import perf_counter
import math

import numpy as np

from localization import fit
from localization.model import (DEFAULT_SPEC, Observations, _read_observations,
                                _scales, residual_jacobian, validate_spec)


POLICY = 'application-numerical-quality-v1'
MIN_SINGULAR_RATIO = math.sqrt(1e-10)
POSITION_ABSOLUTE_TOLERANCE_M = .05
POSITION_RELATIVE_TOLERANCE = 1e-3
VELOCITY_ABSOLUTE_TOLERANCE_MPS = 1e-3


def _model(value):
    if value in ('velocity', 'dynamic'):
        return 'velocity'
    if value == 'static':
        return 'static'
    raise ValueError('Unknown fitted model; only static and velocity are screened')


def _vector(value, name):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 2:
        raise ValueError(name + ' must have exactly two finite numbers')
    if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, (int, float, np.integer, np.floating)) for x in value):
        raise ValueError(name + ' must have exactly two finite numbers')
    vector = np.asarray(value, dtype=float)
    if vector.shape != (2,) or not np.isfinite(vector).all():
        raise ValueError(name + ' must have exactly two finite numbers')
    return vector


def _finite_norm(vector):
    value = math.hypot(*(float(x) for x in vector))
    if not math.isfinite(value):
        raise ValueError('Numerical quality calculation overflowed')
    return value


def numerical_quality(observations, result, spec, fit_function=fit):
    """Return strict-JSON-compatible quality diagnostics, failing closed.

    Missing convergence/bound metadata is not inferred from a finite point.
    Data-Jacobian diagnostics are recomputed from the observations and the
    existing frozen residual functions. All original outputs remain unchanged.
    """
    started = perf_counter()
    reasons = []
    diagnostics = {}
    computation = {'additional_fits': 0, 'additional_work_units': None}

    def reject(code, message):
        if not any(reason['code'] == code for reason in reasons):
            reasons.append({'code': code, 'message': message})

    def inspect_prediction(obs, prediction, cfg, baseline, prefix):
        if not isinstance(prediction, dict):
            raise ValueError(prefix + ' prediction must be an object')
        position = _vector(prediction.get('position'), 'position')
        velocity = _vector(prediction.get('velocity'), 'velocity')
        selected = _model(prediction.get('selected_model'))
        info = prediction.get('diagnostics')
        if not isinstance(info, dict):
            raise ValueError(prefix + ' diagnostics must be an object')
        if 'selected_model' in info and _model(info['selected_model']) != selected:
            reject(prefix + '_model_metadata_mismatch', 'Point estimate and diagnostic model labels disagree.')
        success = info.get('solver_success')
        bound = info.get('at_numerical_bound')
        if type(success) is not bool:
            reject(prefix + '_solver_status_unknown', 'Solver convergence status is unavailable or invalid.')
        elif not success:
            reject(prefix + '_solver_failed', 'The optimizer did not report successful termination.')
        if 'solver_status' in info and (type(info['solver_status']) is not int or info['solver_status'] <= 0):
            reject(prefix + '_invalid_solver_status', 'The numerical termination code does not indicate convergence.')
        if type(bound) is not bool:
            reject(prefix + '_bound_status_unknown', 'Numerical-bound status is unavailable or invalid.')
        elif bound:
            reject(prefix + '_active_bound', 'The fitted state is on an active numerical bound.')
        if info.get('ill_conditioned') is True:
            reject(prefix + '_reported_ill_conditioning', 'The solver explicitly reports ill-conditioned geometry.')
        for key in ('fit_cost', 'regularized_objective', 'all_observation_robust_cost', 'geometry_scale_m'):
            if key in info and (type(info[key]) not in (int, float) or not math.isfinite(info[key]) or info[key] < 0):
                reject(prefix + '_nonfinite_numerical_diagnostics', 'Recorded numerical diagnostics contain unavailable, nonfinite or invalid values.')

        parameters = np.r_[position, velocity] if selected == 'velocity' else position
        scales = obs.sigma if baseline else _scales(obs, cfg)
        residuals, jacobian = residual_jacobian(obs, parameters, selected, cfg, scales)
        if not np.isfinite(residuals).all():
            raise ValueError('Nonfinite independently recomputed residuals')
        ids = info.get('retained_indices')
        if ids is None:
            if cfg.get('trim_fraction', 0):
                raise ValueError('Trimmed estimator did not expose its retained observation set')
            ids = np.arange(len(obs.times))
        else:
            if (not isinstance(ids, list) or not ids or
                    any(type(i) is not int or not 0 <= i < len(obs.times) for i in ids) or
                    len(set(ids)) != len(ids)):
                raise ValueError('Invalid retained observation indices')
            ids = np.asarray(ids, dtype=int)
        data_jacobian = jacobian[ids]
        if not np.isfinite(data_jacobian).all():
            raise ValueError('Nonfinite data Jacobian')
        singular = np.linalg.svd(data_jacobian, compute_uv=False)
        if not len(singular) or not np.isfinite(singular).all():
            raise ValueError('Nonfinite data-Jacobian singular spectrum')
        dimension = len(parameters)
        rank = int(np.count_nonzero(singular > 1e-8))
        ratio = float(singular[-1] / singular[0]) if singular[0] > 0 and len(singular) >= dimension else 0.
        if rank < dimension:
            reject(prefix + '_rank_deficient', 'The retained data Jacobian is not full column rank.')
        if ratio <= MIN_SINGULAR_RATIO:
            reject(prefix + '_weak_geometry', 'A data-Jacobian direction is at or below the frozen covariance truncation threshold.')
        recorded_rank = info.get('jacobian_rank')
        if recorded_rank is not None and (type(recorded_rank) is not int or recorded_rank != rank):
            reject(prefix + '_rank_metadata_mismatch', 'Recorded and independently recomputed Jacobian ranks disagree.')
        recorded_singular = info.get('jacobian_singular_values')
        if recorded_singular is not None:
            if (not isinstance(recorded_singular, list) or len(recorded_singular) != len(singular) or
                    any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in recorded_singular)):
                reject(prefix + '_invalid_spectrum_metadata', 'Recorded singular-spectrum metadata is invalid.')
        return position, velocity, selected, {
            'selected_model': selected, 'solver_success': success if type(success) is bool else None,
            'at_numerical_bound': bound if type(bound) is bool else None,
            'jacobian_rank': rank, 'parameter_dimension': dimension,
            'jacobian_singular_values': singular.tolist(), 'smallest_largest_singular_ratio': ratio,
            'minimum_allowed_ratio_exclusive': MIN_SINGULAR_RATIO,
            'geometry_source': 'Independently recomputed frozen data-residual Jacobian on the retained observations.'}

    try:
        with np.errstate(over='raise', divide='raise', invalid='raise'):
            obs = _read_observations(observations)
            if not isinstance(spec, dict):
                raise ValueError('Method specification must be an object')
            baseline = 'baseline' in spec
            if baseline:
                if len(spec) != 1:
                    raise ValueError('Baseline specification cannot mix method components')
                cfg = dict(DEFAULT_SPEC, residual='range', scale='reported', trim_fraction=0., consensus_trials=0)
            else:
                cfg = validate_spec(spec)
            if cfg['consensus_trials'] != 0:
                reject('translation_screen_unsupported', 'Random-subset initialization hashes absolute observations; translation screening is not supported for nonzero consensus_trials.')
                diagnostics['translation_check'] = {'status': 'unsupported'}

            position, velocity, selected, original = inspect_prediction(obs, result, cfg, baseline, 'original')
            diagnostics['original'] = original
            origin = obs.anchors[0].copy()
            local_anchors = obs.anchors - origin
            local_position = position - origin
            if not np.isfinite(local_anchors).all() or not np.isfinite(local_position).all():
                raise ValueError('Coordinate translation overflowed')
            scene_spread = max(float(np.median(obs.ranges)), float(np.max(np.ptp(local_anchors, axis=0))), 1.)
            span = max(float(np.ptp(obs.times)), 1.)
            position_tolerance = max(POSITION_ABSOLUTE_TOLERANCE_M, POSITION_RELATIVE_TOLERANCE * scene_spread)
            velocity_tolerance = max(VELOCITY_ABSOLUTE_TOLERANCE_MPS, position_tolerance / span)
            if not all(math.isfinite(x) for x in (scene_spread, span, position_tolerance, velocity_tolerance)):
                raise ValueError('Translation tolerance calculation overflowed')
            if cfg['consensus_trials'] == 0:
                # A separate observation object and spec copy ensure this guard
                # does not modify the original input or the frozen result.
                centered_obs = Observations(local_anchors, obs.times.copy(), obs.ranges.copy(), obs.sigma.copy())
                computation['additional_fits'] += 1
                alternate = fit_function(centered_obs, dict(spec))
                work = alternate.get('work_units') if isinstance(alternate, dict) else None
                if type(work) in (int, float) and math.isfinite(work) and work >= 0:
                    computation['additional_work_units'] = work
                local_fit, alternate_velocity, alternate_model, centered = inspect_prediction(centered_obs, alternate, cfg, baseline, 'centered')
                position_change = _finite_norm(local_position - local_fit)
                velocity_change = _finite_norm(velocity - alternate_velocity)
                diagnostics['centered'] = centered
                diagnostics['translation_check'] = {
                    'status': 'checked', 'origin_m': origin.tolist(),
                    'position_change_m': position_change, 'position_tolerance_m': position_tolerance,
                    'velocity_change_mps': velocity_change, 'velocity_tolerance_mps': velocity_tolerance,
                    'model_agrees': selected == alternate_model,
                    'scene_spread_m': scene_spread, 'observation_span_seconds': span,
                    'position_comparison': 'Compare original_position minus origin against centered_fit_position to reduce cancellation.',
                    'point_estimate_replaced': False}
                if selected != alternate_model:
                    reject('translation_model_changed', 'Changing only the coordinate origin changed the selected motion model.')
                if position_change > position_tolerance:
                    reject('translation_position_changed', 'Changing only the coordinate origin materially changed the fitted position.')
                if velocity_change > velocity_tolerance:
                    reject('translation_velocity_changed', 'Changing only the coordinate origin materially changed the fitted velocity.')
    except Exception as exc:
        # A guard must not turn failed diagnostics into a successful application
        # decision. Exception text may contain untrusted input and is omitted.
        reject('quality_check_failed', 'Numerical quality screening failed; no quality acceptance is asserted.')
        diagnostics['failure_type'] = type(exc).__name__

    computation['elapsed_seconds'] = float(perf_counter() - started)
    return {'schema_version': 1, 'policy': POLICY, 'passed': not reasons,
            'status': 'passed' if not reasons else 'rejected', 'reasons': reasons,
            'diagnostics': diagnostics, 'guard_computation': computation,
            'threshold_policy': {
                'singular_ratio_must_exceed': MIN_SINGULAR_RATIO,
                'position_formula': 'max(0.05 m, 0.001 * max(median range, largest anchor-axis span, 1 m))',
                'velocity_formula': 'max(0.001 m/s, position tolerance / max(time span, 1 s))',
                'thresholds_fitted_on_holdout': False},
            'assumptions': [
                'Engineering screening of numerical stability, not a proof of correct position, global identifiability or deployment readiness.',
                'The frozen estimate and calibrated radius are preserved; the centered fit is diagnostic only.',
                'This additional application gate has no newly evaluated acceptance rate, error rate or conditional coverage guarantee.',
                'Guard work is additional to the frozen solver work and is reported separately; old performance timings do not include it.'],
            'coverage_guarantee_asserted': False}
