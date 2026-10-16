# CrowdLocateLab numerical specification

This document describes the implemented algorithm, including approximations and
budget reductions. It is not a claim of a new globally optimal localization
algorithm or an industrial solution to Bluetooth crowd localization.

## Observation boundary and trajectory

Every estimator reads only the arrays `anchors`, `times`, `ranges`, and `sigma`.
Coordinates and ranges are metres; times are seconds relative to the target
time zero. `truth`, `velocity`, `scenario`, and `meta` are evaluation-only fields.
No optimizer, initializer, consensus rule, or model-selection fold reads them.
Case labels can be used by an external evaluation tool to summarize development
errors, but those labels do not enter the estimator.

For observation i, let a_i be the reported anchor coordinate, t_i its time,
y_i>0 its observed range, and sigma_i>0 its reported range uncertainty.

    static:    z_i(theta) = x
    velocity:  z_i(theta) = x + t_i v
    d_i(theta) = ||z_i(theta) - a_i||_2

The estimated current position is x, not the position at the mean observation
time. Static fits have two parameters; velocity fits have four. A velocity fit
with fewer than six observations or less than one second of temporal spread
falls back to the static model.

## Residual alternatives and their actual calibration

For `residual="range"`, the standardized residual is

    r_i(theta) = (d_i(theta) - y_i) / s_i.

For `scale="reported"`, s_i = sigma_i. For `scale="constant"`, every s_i
equals the median of the supplied sigma values in that fitting dataset. This
removes per-observation dependence of weights on a particular observed range,
but the shared scale still comes from noisy observations.

For `residual="log_range"`, with clipping threshold delta=0.1 m,

    b = log_sd^2/2 if log_bias_correction else 0
    r_i(theta) = [log(max(d_i(theta), delta))
                  - log(max(y_i, delta)) - b] / ell_i.
    ell_i = sqrt(log(1 + (sigma_i / max(y_i, delta))^2)).

The `reported` log scale ell_i comes from the reported total range uncertainty,
so it approximately includes coordinate and additive range uncertainty. It is
the lognormal coefficient-of-variation inversion; it is **not** the exact
likelihood of the mixture of lognormal radio noise and Gaussian coordinate
noise. For `scale="constant"`, all ell_i are replaced by their training-data
median. At long ranges under the nominal generator, ell_i is approximately 0.1.
The configured `log_sd` (default 0.1) controls the centering correction only;
the supplied sigma values control the log residual scale. A calibration-shift
scenario can invalidate either calibration assumption.

For the simplified model Y=d exp(epsilon-s^2/2), epsilon~N(0,s^2),

    E[log Y] = log d - s^2/2.

Thus the correction sign above gives E[log d-log Y-s^2/2]=0 before scaling.
This exact scalar identity does not make a scaled, robust, clipped, or nonlinear
multi-anchor estimator unbiased. Additive noise and anchor-coordinate error
also break this simple scalar derivation.

The scalar weighted objective E[(q-Y)^2/Y^2] has minimizer

    q* = E[Y^-1] / E[Y^-2] = d exp(-2s^2).

This is a mechanism motivating a constant scale or a log residual. It applies
to a one-range population model with weight exactly proportional to 1/Y^2;
it is not a theorem that every nonlinear localization estimate has this bias.
The generator's additive coordinate term weakens the exact proportionality.

## Optimization loss and Jacobian

Let c=`f_scale`, lambda=`ridge`, and I the currently retained observations.
The optimization objective is

    L(theta; I) = sum_{i in I} c^2 rho((r_i(theta)/c)^2)
                 + lambda ||v||_2^2          [velocity only].

The loss rho(z) is one of:

| loss | rho(z) |
|---|---|
| linear | z |
| soft_l1 | 2(sqrt(1+z)-1) |
| huber | z for z<=1; 2sqrt(z)-1 otherwise |
| cauchy | log(1+z) |

Velocity regularization is always quadratic, even with a robust measurement
loss. A callable SciPy loss applies robustness to measurement rows and leaves
the last two regularization rows linear. This distinction is tested.

For d_i>0, let u_i=(z_i-a_i)/d_i. The range Jacobian row is

    static:   u_i^T / s_i
    velocity: [u_i^T, t_i u_i^T] / s_i.

For the log residual, divide the unscaled geometric row by d_i ell_i while
d_i>delta, and set its row to zero in the clipped region d_i<=delta. At the
clipping boundary the mathematical derivative is not unique; the implementation
chooses zero there. The mean correction has zero parameter derivative.

SciPy `least_squares` uses analytic Jacobians, at most 100 residual evaluations
per main solve, and 60 per consensus subset solve. Relative tolerances are
3e-6. These are local solves. Finite data-derived bounds are a numerical guard:
position lies within 10 times an observation spread around the weighted anchor
centroid; velocity bounds are 10 times spread divided by temporal span.

## Initializers and observation-subset consensus

The default three position starts are (1) a centered linear solution of
differenced squared-range equations, (2) the weighted anchor centroid, and
(3) the reflection of the linear start about that centroid. Five starts add
two offsets perpendicular to the dominant anchor direction. An unstable linear
start farther than five observation spreads is replaced by the centroid.
Velocity starts are zero. No start uses hidden position or velocity labels.

For C=`consensus_trials` in 0..12, C subsets are sampled without replacement,
each of size min(n, max(k+2, ceil(n/2))), where k is the model dimension. The
random generator uses the supplied method seed and a deterministic hash of
the four observation arrays. Each subset is fit once from its own linear
initializer, with its own scale estimate. Subset solutions are scored on all
available training observations using the same robust, optionally trimmed,
regularized objective. The best subset solution becomes **one additional full
data initialization**, not C additional full solves.

This is a bounded random-subset initialization method, related to consensus
ideas. It does not implement a classical RANSAC stopping guarantee and does not
claim a certified breakdown point or that any sampled subset is uncontaminated.

## Fixed-budget iterative trimming

Let alpha=`trim_fraction` in [0,0.35]. The retained count is

    h = min(n, max(k+1, ceil((1-alpha)n))).

For each start, first solve with all observations. If alpha>0, retain the h
smallest absolute standardized residuals, refit, and repeat with at most three
total solves, stopping early when the retained index set is unchanged. After
the final solve, recalculate the h-smallest-residual score. Among starts,
select the smallest **trimmed robust loss plus velocity penalty**, rather than
the smallest measurement term alone. This is a local, fixed-budget alternating
approximation to least-trimmed estimation; it need not reach a stationary point
or the global optimum. The retained set recorded in diagnostics is the final
ranked set; after the last allowed solve it may differ from the final solve's
input set.

## Observation-level predictive model selection

For `model="cv_select"`, sort observations by time and assign them to K=3
interleaved folds by default. Each observation is a validation observation
exactly once. In each fold fit static and velocity models using **training
observations only**. Initializers, random subsets, and constant scales use that
training fold. Reported per-observation uncertainties may be used to score the
validation observations, because they are observed features, not hidden labels.

To bound computation, inner fits use one ordinary start and at most three
consensus trials. Their residual, loss, regularization and trimming definitions
are otherwise unchanged. The final selected model is refit to all observations
with the full requested number of starts and consensus trials. Consequently
this is validation of a fixed-budget proxy for the final fitting procedure,
not exact cross-validation of the full computation budget.

Each validation residual is evaluated with fixed unit-scale soft-L1, regardless
of the training loss. No validation residual is trimmed. Aggregate by the mean
over validation observations, rather than equally weighting unequal fold sizes:

    CV_m = (1/n) sum_i 2(sqrt(1+r_{i,held-out,m}^2)-1).

Choose velocity only when all dynamic folds are admissible and

    CV_velocity < (1-gate_margin) CV_static.

The default relative margin is 0.05. A fold with fewer than six training
observations, less than one second of training temporal spread, a rank-deficient
velocity data Jacobian, or an active numerical bound blocks dynamic selection.
Inadmissible model selection falls back to a full static fit. With eight sparse
observations and K=3, some training folds have five observations, so velocity
selection is deliberately unavailable under this policy.

Interleaved folds test interpolation in the observed time window; they do not
establish forward forecasting performance or accurately extrapolate arbitrary
accelerating motion. Contamination affects validation measurements too, so this
criterion is not guaranteed to choose the true motion class or best x(0).

## Geometry proxy and computational accounting

Let J be the data-residual Jacobian of the selected model on its retained set,
excluding the ridge rows. Let r contain its standardized observation residuals.
The returned `geometry_scale_m` is

    clip(sqrt(trace(pinv(J^T J)[0:2,0:2]))
         * max(1, median(abs(r))/0.6745), 0.2, 100).

A rank-deficient J receives the conservative cap 100. This local quantity is
an **uncalibrated observable scale**. It does not account for global mirror
ambiguity, optimizer failures, model misspecification, selection, or systematic
radio bias, and is not a confidence interval or a proven Cramer-Rao bound.
Any split-conformal coverage statement must come from an independent exchangeable
calibration procedure, not from this Jacobian calculation. Out-of-distribution
coverage or coverage conditional on a data-dependent rejection rule is not
automatically guaranteed by ordinary marginal conformal coverage.

`work_units` counts actual residual-function evaluations inside every optimizer
call, including all starts, all subset trials, all trimming rounds, all CV
folds, and the final full-data refit. Diagnostics also count Jacobian evaluations
and solver calls. It is not CPU time, total floating-point operations, or total
residual scoring outside optimizers. `elapsed_seconds` measures wall time.

## Synthetic data and retained references

Nine scenarios are supplied: static_clean, static_outliers, moving_clean,
moving_outliers, poor_geometry, accelerating, sparse, calibration_shift,
high_contamination. The first six preserve the version-one observation
generation for the same seeds under default settings. New scenarios use
independent scenario seed streams.

Nominal generation uses t in [-30,0], lognormal mean-one radio multipliers with
log SD 0.1, independent 0.70 m Gaussian anchor-coordinate errors, and 0.25 m
Gaussian additive range noise. Standard outliers affect approximately 15--25%
of observations; 75% of corruptions multiply range by 1.7--2.8, and 25% multiply
it by 0.30--0.60. The actual finite-sample fraction is recorded. Narrow geometry
uses a 14-degree anchor sector. Accelerating paths add 0.5*a*t^2, with acceleration
magnitude 0.025--0.065 m/s^2 and a changed direction.

`generate_suite` gives the sparse scenario eight observations and others sixteen.
`generate_case(..., n=...)` always respects the explicit/default n, permitting
anchor-count stress studies. Calibration shift multiplies ranges by 1.30 and
uses actual log SD 0.16 but reports uncertainty using nominal log SD 0.10.
High contamination draws the nominal corruption fraction uniformly in 0.35--0.45.
Explicit `contamination` and `log_sd` arguments enable stress grids; metadata
records their actual values and all latent generation labels for evaluation.
These are stylized synthetic stress conditions, not measured radio statistics.

`legacy_model.py` retains version-one WLS, robust static, robust velocity, and
robust-BIC selection. `fit_baseline` wraps those references and adds the common
geometry proxy without changing their estimated positions. Its BIC remains a
heuristic fitted-residual criterion, not a calibrated likelihood-ratio test.

## Implemented verification scope

`tests/test_localization.py` checks finite-difference Jacobians for both models
and both residual types, derivatives of four losses and quadratic ridge rows,
the exact lognormal-centering identity by Gaussian quadrature, exact noiseless
velocity recovery, guarded observation-only access, explicit fold exclusion,
actual work-unit accounting, strict JSON output, input checks, nine-scenario
seed reproducibility, and stress controls. Its seeds are dedicated probes and
it does not open a formal held-out benchmark.
