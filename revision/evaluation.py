"""Predeclared second-study statistics; no dataset is generated on import.

Only this revision namespace changes. The v2 solvers and evidence stay intact.
"""
from pathlib import Path
import hashlib
import json
import math

import numpy as np

from benchmark import validate_frozen_run, scale
from localization import generate_case, generate_suite
from localization.model import validate_spec
from tools.common import digest, load, metrics
from tools.toolkit import BASELINES, evaluate


def json_safe(value):
    """Keep unbounded regions explicit; silently replacing infinity is invalid."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if math.isnan(value): raise ValueError('NaN is not valid study evidence')
        if math.isinf(value): return 'infinity' if value > 0 else '-infinity'
    return value


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_revision_freeze(root, run):
    """Verify original freeze consistency AND the newly registered evaluator."""
    root, run = Path(root).resolve(), Path(run).resolve()
    checked = validate_frozen_run(root, run)
    protocol = load(run / 'protocol.json')
    plan = load(root / 'research/revision_plan.json')
    required = set(plan['integrity']['required_new_source_hashes'])
    required.update(str(p.relative_to(root)).replace('\\', '/')
                    for p in (root / 'revision').rglob('*.py'))
    hashes = protocol.get('source_hashes', {})
    if not required.issubset(hashes):
        raise ValueError('Revision initialization omitted required hashes: ' +
                         ', '.join(sorted(required - set(hashes))))
    # validate_frozen_run already checks every listed source byte against disk.
    expected = plan['methods']['v2_frozen_spec']
    original = load(root / 'runs/live_reference/tools/frozen_method.json')
    if digest(original['spec']) != digest(expected):
        raise ValueError('Predeclared v2 comparator differs from preserved v2 freeze')
    checked['revision_provenance'] = {
        'plan_sha256': source_hash(root / 'research/revision_plan.json'),
        'run_protocol_sha256': source_hash(run / 'protocol.json'),
        'original_v2_frozen_sha256': source_hash(root / 'runs/live_reference/tools/frozen_method.json'),
        'new_sources_checked': sorted(required),
        'fresh_data_generated_after_this_check': True,
    }
    checked['plan'] = plan
    return checked


def effective_spec(spec):
    if 'baseline' in spec:
        if len(spec) != 1: raise ValueError('Baseline shorthand cannot have component keys')
        return dict(spec)
    normal = validate_spec(spec)
    if normal['model'] != 'cv_select': normal.pop('cv_folds')
    return normal


def build_methods(chosen, plan):
    methods = {**BASELINES, 'v2_frozen': plan['methods']['v2_frozen_spec'],
               'revision_selected': dict(chosen)}
    changes = {}
    if 'baseline' not in chosen:
        for name, change in plan['methods']['ablations'].items():
            methods[name] = {**chosen, **change}
            changes[name] = change
    aliases, seen = {}, {}
    for name, spec in methods.items():
        key = digest(effective_spec(spec))
        if key in seen: aliases[name] = seen[key]
        else: seen[key] = name
    return methods, aliases, changes


def _identified(case, split, root_seed, index, observation_n=None):
    original_id = case.case_id
    case.case_id = f'{split}-{root_seed}-{index:05d}-{original_id}'
    case.meta = {**case.meta, 'generator_case_id': original_id,
                 'study_split': split, 'study_seed': int(root_seed),
                 'study_index': int(index), 'observation_n': len(case.times)}
    if observation_n is not None:
        case.meta['edge_group'] = f'n{observation_n}:{case.scenario}'
    return case


def iid_mixture(seed, count, scenarios, split):
    rng = np.random.default_rng(seed)
    cases = []
    for i in range(count):
        scene = str(rng.choice(scenarios))
        sample_seed = int(rng.integers(1, 2**32 - 1))
        c = generate_case(sample_seed, scene)
        c.meta['generator_seed'] = sample_seed
        cases.append(_identified(c, split, seed, i))
    return cases


def generate_datasets(plan):
    """Entry point calls this only after validate_revision_freeze. Tests use fixtures."""
    datasets = {}
    cfg = plan['main']; cases = []
    for seed in cfg['seeds']:
        suite = generate_suite(seed, cfg['per_scenario_per_seed'], cfg['scenarios'])
        cases.extend(_identified(c, 'main', seed, i) for i, c in enumerate(suite))
    datasets['main'] = cases
    cfg = plan['stress']
    datasets['stress'] = [_identified(c, 'stress', cfg['seed'], i) for i, c in
        enumerate(generate_suite(cfg['seed'], cfg['per_scenario'], cfg['scenarios']))]
    cfg = plan['edge']; rng = np.random.default_rng(cfg['seed']); cases = []
    for n in cfg['observation_n']:
        for scene in cfg['scenarios']:
            for _ in range(cfg['per_cell']):
                sample_seed = int(rng.integers(1, 2**32 - 1))
                c = generate_case(sample_seed, scene, n=n)
                c.meta['generator_seed'] = sample_seed
                cases.append(_identified(c, 'edge', cfg['seed'], len(cases), n))
    datasets['edge'] = cases
    for split in ('calibration', 'coverage'):
        cfg = plan[split]
        datasets[split] = iid_mixture(cfg['seed'], cfg['size'], cfg['scenarios'], split)
    for split, cases in datasets.items():
        expected = plan[split].get('expected_n', plan[split].get('size'))
        if len(cases) != expected: raise ValueError(f'{split} case count differs from plan')
    ids = [c.case_id for cases in datasets.values() for c in cases]
    if len(ids) != len(set(ids)): raise ValueError('Study case identities must be unique')
    return datasets


def evaluate_cases(cases, spec):
    result = evaluate(cases, spec)
    for case, row in zip(cases, result['rows']):
        row.update(study_seed=case.meta['study_seed'], observation_n=len(case.times))
        if 'edge_group' in case.meta: row['edge_group'] = case.meta['edge_group']
    result['by_observation_n'] = {str(n): metrics([r for r in result['rows'] if r['observation_n'] == n])
                                 for n in sorted({r['observation_n'] for r in result['rows']})}
    result['by_edge_group'] = {group: metrics([r for r in result['rows'] if r.get('edge_group') == group])
                              for group in sorted({r['edge_group'] for r in result['rows'] if 'edge_group' in r})}
    return result


def _paired_cells(selected, reference, strata):
    a = {r['case_id']: r for r in selected}; b = {r['case_id']: r for r in reference}
    if len(a) != len(selected) or len(b) != len(reference): raise ValueError('Duplicate paired case ID')
    if not a or set(a) != set(b): raise ValueError('Paired cases must be the same nonempty set')
    groups = {}
    for cid in sorted(a):
        ra, rb = a[cid], b[cid]
        key = tuple(ra[k] for k in strata)
        if tuple(rb[k] for k in strata) != key or ra['scenario'] != rb['scenario']:
            raise ValueError('Paired case stratification mismatch')
        values = [float(ra['error_m']), float(rb['error_m'])]
        if not np.isfinite(values).all() or min(values) < 0: raise ValueError('Invalid error')
        groups.setdefault(key, []).append(values)
    return [np.asarray(groups[k]) for k in sorted(groups)]


def paired_bootstrap(selected, reference, strata, seed=1912303, repeats=4000, margin=1.1):
    """Paired equal-cell means; resampling never changes the predeclared mixture."""
    if repeats < 2: raise ValueError('Need at least two bootstrap replicates')
    cells = _paired_cells(selected, reference, strata)
    rng = np.random.default_rng(seed)
    draws = np.zeros((repeats, 2))
    for cell in cells:
        indices = rng.integers(0, len(cell), size=(repeats, len(cell)))
        draws += cell[indices].mean(axis=1) / len(cells)
    point = np.mean([cell.mean(axis=0) for cell in cells], axis=0)
    delta = draws[:, 1] - draws[:, 0]
    ratio = np.divide(draws[:, 0], draws[:, 1], out=np.full(repeats, np.inf), where=draws[:, 1] > 0)
    # Quantiles involving infinity are explicitly unbounded (avoid inf-inf NaNs).
    def interval(values):
        if not np.isfinite(values).all(): return [float(np.min(values)), math.inf]
        return np.quantile(values, [.025, .975]).tolist()
    ni = draws[:, 0] - margin * draws[:, 1]
    ratio_point = float(point[0] / point[1]) if point[1] > 0 else math.inf
    return {'n': len(selected), 'cells': len(cells), 'strata': list(strata),
            'selected_macro_mean_m': float(point[0]), 'reference_macro_mean_m': float(point[1]),
            'mean_reduction_m': float(point[1] - point[0]), 'ci95_m': interval(delta),
            'relative_mean_reduction_percent': float(100 * (point[1] - point[0]) / point[1]) if point[1] > 0 else None,
            'mean_error_ratio': ratio_point, 'ratio_ci95': interval(ratio),
            'margin': margin, 'noninferiority_excess_m': float(point[0] - margin * point[1]),
            'noninferiority_excess_ci95_m': interval(ni),
            'point_meets_margin': bool(ratio_point <= margin),
            'ci_upper_below_margin': bool(interval(ni)[1] < 0),
            'repetitions': repeats, 'seed': seed,
            'claim': 'Exploratory paired percentile intervals; no multiplicity correction, no uncertainty for research/search selection.'}


def summarize_comparisons(raw, methods, plan):
    stats = plan['statistics']; comparisons = {}
    for split in ('main', 'stress', 'edge'):
        comparisons[split] = {}
        for name in methods:
            if name != 'revision_selected':
                comparisons[split][name] = paired_bootstrap(raw[('revision_selected', split)]['rows'], raw[(name, split)]['rows'],
                    stats['strata'][split], stats['bootstrap_seed'], stats['bootstrap_repetitions'])
    per_seed = {name: {str(seed): metrics([r for r in raw[(name, 'main')]['rows'] if r['study_seed'] == seed])
                        for seed in plan['main']['seeds']} for name in methods}
    ni = {}
    for split in ('main', 'edge'):
        selected = [r for r in raw[('revision_selected', split)]['rows'] if r['scenario'] == 'static_clean']
        reference = [r for r in raw[('static_wls', split)]['rows'] if r['scenario'] == 'static_clean']
        ni[split] = paired_bootstrap(selected, reference, stats['strata'][split], stats['bootstrap_seed'], stats['bootstrap_repetitions'], 1.1)
    edge_pairs = {}
    for n in plan['edge']['observation_n']:
        for scene in plan['edge']['scenarios']:
            key = f'n{n}:{scene}'
            edge_pairs[key] = {name: paired_bootstrap(
                [r for r in raw[('revision_selected', 'edge')]['rows'] if r.get('edge_group') == key],
                [r for r in raw[(name, 'edge')]['rows'] if r.get('edge_group') == key],
                ['observation_n', 'scenario'], stats['bootstrap_seed'], stats['bootstrap_repetitions'])
                for name in methods if name != 'revision_selected'}
    return comparisons, per_seed, ni, edge_pairs


def observed_group(row):
    """No access to error, truth, scenario, velocity truth or other labels."""
    model = row['diagnostics']['selected_model']
    if model in ('velocity', 'dynamic'): return 'velocity'
    if model == 'static': return 'static'
    raise ValueError(f'Unknown observation-derived model group: {model}')


def conformal_rank(scores, alpha):
    scores = np.asarray(scores, dtype=float)
    if not 0 < alpha < 1: raise ValueError('alpha must be between zero and one')
    if scores.ndim != 1 or not np.isfinite(scores).all() or (scores < 0).any():
        raise ValueError('Scores must be finite nonnegative values')
    n = len(scores); k = math.ceil((n + 1) * (1 - alpha))
    q = float(np.partition(scores, k - 1)[k - 1]) if 1 <= k <= n else math.inf
    return {'n': n, 'rank': k, 'q': q, 'unbounded': math.isinf(q)}


def wilson_interval(covered, n):
    """Binomial sampling interval for a fixed coverage rule and IID test cases."""
    if n == 0: return None
    if not 0 <= covered <= n: raise ValueError('Invalid coverage count')
    z = 1.959963984540054
    p = covered / n; denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0., center - width), min(1., center + width)]


def _population(rows, quantiles, mondrian, thresholds, iid=False):
    if not rows: raise ValueError('Coverage population must be nonempty')
    groups = [observed_group(row) for row in rows]
    radii = np.array([quantiles[g if mondrian else 'global']['q'] * scale(row) for g, row in zip(groups, rows)])
    errors = np.array([row['error_m'] for row in rows], float)
    if not np.isfinite(errors).all() or (errors < 0).any(): raise ValueError('Invalid coverage errors')
    covered = errors <= radii
    def subset(mask):
        n = int(mask.sum())
        hits = int(covered[mask].sum())
        return {'n': n, 'covered_n': hits,
                'coverage': float(covered[mask].mean()) if n else None,
                'coverage_wilson95': wilson_interval(hits, n) if iid else None,
                'mean_radius_m': float(radii[mask].mean()) if n else None,
                'unbounded_n': int(np.isinf(radii[mask]).sum())}
    allstats = subset(np.ones(len(rows), dtype=bool))
    allstats['coverage_interval_claim'] = ('Wilson interval estimates the coverage probability of this fixed realized calibration rule from IID test draws, including an IID subgroup conditional on membership. It is not a conformal theorem or an interval over recalibration.' if iid else
        'No pooled binomial interval: stress/edge use fixed mixtures and distribution shift; report empirical coverage without an IID conformal guarantee.')
    allstats['median_radius_m'] = float(np.median(radii))
    allstats['by_scenario'] = {s: subset(np.array([r['scenario'] == s for r in rows])) for s in sorted({r['scenario'] for r in rows})}
    allstats['by_group'] = {g: subset(np.array([x == g for x in groups])) for g in ('static', 'velocity')}
    policy = []
    for threshold in thresholds:
        mask = radii <= threshold; n = int(mask.sum())
        policy.append({'radius_threshold_m': threshold, 'accepted_n': n,
            'acceptance_rate': n / len(rows), 'accepted_coverage': float(covered[mask].mean()) if n else None,
            'accepted_mean_error_m': float(errors[mask].mean()) if n else None})
    allstats['rejection_curve'] = policy
    allstats['rows'] = [{'case_id': row['case_id'], 'group': group, 'radius_m': float(radius), 'covered': bool(hit), 'unbounded': bool(math.isinf(radius))}
                        for row, group, radius, hit in zip(rows, groups, radii, covered)]
    return allstats


def compare_conformal(calibration, populations, plan):
    config = plan['regions']
    scores = np.array([row['error_m'] / scale(row) for row in calibration])
    groups = np.array([observed_group(row) for row in calibration])
    result = {'global': {}, 'mondrian': {}, 'claim': config['guarantee_scope'],
              'group_definition': 'Frozen observation-only selected_model, dynamic mapped to velocity; never true scenario.'}
    for alpha in config['alphas']:
        for name, mondrian in [('global', False), ('mondrian', True)]:
            quantiles = ({g: conformal_rank(scores[groups == g], alpha) for g in ('static', 'velocity')}
                         if mondrian else {'global': conformal_rank(scores, alpha)})
            result[name][str(alpha)] = {'alpha': alpha, 'calibration': quantiles,
                'populations': {key: _population(rows, quantiles, mondrian, config['radius_rejection_thresholds_m'], iid=(key == 'iid_test'))
                                for key, rows in populations.items()}}
    return result
