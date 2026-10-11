"""Read-only numerical audit of saved second-study results; never fit or generate.

The calculations below deliberately do not import benchmark/evaluation helpers.
Only an optional audit JSON is written. Any mismatch produces nonzero exit.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import sys

import numpy as np


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonstandard JSON number: ' + x)))


def numeric(value):
    if value == 'infinity': return math.inf
    if value == '-infinity': return -math.inf
    return float(value)


class Check:
    def __init__(self): self.count = 0; self.errors = []
    def assert_(self, valid, label):
        self.count += 1
        if not valid: self.errors.append(label)
    def close(self, a, b, label, tolerance=1e-9):
        if a is None or b is None:
            self.assert_(a is None and b is None, label)
            return
        a, b = numeric(a), numeric(b)
        self.assert_((a == b) or math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance),
                     f'{label}: saved={a}, recomputed={b}')
    def interval(self, a, b, label):
        if a is None or b is None: self.assert_(a is None and b is None, label); return
        self.assert_(len(a) == len(b), label + ': length')
        for i, (x, y) in enumerate(zip(a, b)): self.close(x, y, f'{label}[{i}]')


def statistics(rows):
    errors = np.asarray([r['error_m'] for r in rows])
    return {'n': len(rows), 'mean_m': errors.mean(),
        'rmse_m': np.sqrt(np.mean(errors ** 2)), 'median_m': np.median(errors),
        'p90_m': np.quantile(errors, .9), 'p95_m': np.quantile(errors, .95),
        'max_m': errors.max(), 'within_5m': np.mean(errors <= 5),
        'within_30m': np.mean(errors <= 30),
        'mean_work_units': np.mean([r['work_units'] for r in rows]),
        'mean_seconds': np.mean([r['elapsed_seconds'] for r in rows])}


def paired(selected, reference, fields, seed, repetitions):
    left = {r['case_id']: r for r in selected}; right = {r['case_id']: r for r in reference}
    if set(left) != set(right) or len(left) != len(selected) or len(right) != len(reference):
        raise ValueError('Non-unique/unpaired case identity')
    groups = {}
    for cid in sorted(left):
        key = tuple(left[cid][field] for field in fields)
        groups.setdefault(key, []).append((left[cid]['error_m'], right[cid]['error_m']))
    cells = [np.asarray(groups[key]) for key in sorted(groups)]
    rng = np.random.default_rng(seed); sampled = []
    for cell in cells:
        indices = rng.integers(0, len(cell), (repetitions, len(cell)))
        sampled.append(cell[indices].mean(axis=1))
    draws = np.mean(sampled, axis=0); means = np.mean([v.mean(axis=0) for v in cells], axis=0)
    difference = draws[:, 1] - draws[:, 0]
    ratios = draws[:, 0] / draws[:, 1]
    return {'mean_reduction_m': means[1] - means[0],
        'ci95_m': np.quantile(difference, [.025, .975]),
        'mean_error_ratio': means[0] / means[1],
        'ratio_ci95': np.quantile(ratios, [.025, .975]),
        'noninferiority_excess_m': means[0] - 1.1 * means[1],
        'noninferiority_excess_ci95_m': np.quantile(draws[:, 0] - 1.1 * draws[:, 1], [.025, .975]),
        'relative_mean_reduction_percent': 100 * (means[1] - means[0]) / means[1]}


def group(row):
    label = row['diagnostics']['selected_model']
    if label == 'static': return 'static'
    if label in ('dynamic', 'velocity'): return 'velocity'
    raise ValueError('Unknown observed group')


def proxy(row):
    value = row['diagnostics'].get('geometry_scale_m', 1.)
    return max(.2, float(value)) if value is not None and math.isfinite(float(value)) else 100.


def wilson(hits, n):
    if not n: return None
    z2 = 1.959963984540054 ** 2; p = hits / n
    center = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = math.sqrt(z2 * (p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)
    return [max(0., center - half), min(1., center + half)]


def audit(directory):
    directory = Path(directory); c = Check()
    summary = read(directory / 'summary.json'); protocol = read(directory / 'protocol.json')
    status = read(directory / 'status.json')
    c.assert_(summary.get('status') == status.get('status') == 'complete', 'Completion markers')
    c.assert_(summary['protocol'] == protocol, 'Protocol copy in summary')
    c.assert_(hashlib.sha256((directory / 'summary.json').read_bytes()).hexdigest() == status['summary_sha256'], 'Summary completion digest')
    plan = protocol['plan']; methods = protocol['methods']; raw = {}; inputs = {}
    all_ids = []
    for split in ('main', 'stress', 'edge', 'calibration', 'coverage'):
        cases = read(directory / f'{split}_inputs.json'); inputs[split] = {v['case_id']: v for v in cases}
        c.assert_(len(inputs[split]) == len(cases), split + ': unique input IDs')
        c.close(len(cases), plan[split].get('expected_n', plan[split].get('size')), split + ': planned count')
        c.close(summary['case_counts'][split], len(cases), split + ': saved count')
        all_ids.extend(inputs[split])
    c.assert_(len(all_ids) == len(set(all_ids)), 'Unique cross-split IDs')
    matrix_rows = 0
    def check_prediction(split, name, data):
        rows = data['rows']; cases = inputs[split]
        c.assert_({r['case_id'] for r in rows} == set(cases) and len(rows) == len(cases), f'{split}/{name}: case match')
        for row in rows:
            case = cases[row['case_id']]
            p = np.asarray(row['position']); truth = np.asarray(case['truth'])
            c.assert_(p.shape == (2,) and np.isfinite(p).all(), f'{split}/{name}/{row["case_id"]}: valid estimate')
            c.close(row['error_m'], np.linalg.norm(p - truth), f'{split}/{name}/{row["case_id"]}: distance')
            c.assert_(row['scenario'] == case['scenario'], 'Scenario label matches input')
            c.close(row['observation_n'], len(case['times']), 'Observation count matches input')
            c.close(row['study_seed'], case['meta']['study_seed'], 'Root seed matches input')
        expected = statistics(rows)
        for k, v in expected.items(): c.close(data['overall'][k], v, f'{split}/{name}/overall/{k}')
        for scene, saved in data['by_scenario'].items():
            for k, v in statistics([r for r in rows if r['scenario'] == scene]).items():
                c.close(saved[k], v, f'{split}/{name}/{scene}/{k}')
        c.close(data['macro_mean_m'], np.mean([v['mean_m'] for v in data['by_scenario'].values()]), f'{split}/{name}/macro')
        return rows
    for name in methods:
        for split in ('main', 'stress', 'edge'):
            data = read(directory / f'{split}_{name}.json'); raw[(name, split)] = data
            rows = check_prediction(split, name, data); matrix_rows += len(rows)
            saved = summary['metrics'][name][split]
            c.assert_(saved == {k: v for k, v in data.items() if k != 'rows'}, f'{split}/{name}: summary copy')
    for alias, canonical in summary['aliases'].items():
        for split in ('main', 'stress', 'edge'):
            c.assert_(raw[(alias, split)]['rows'] == raw[(canonical, split)]['rows'], f'{split}/{alias}: exact alias rows')
    bs = plan['statistics']; comparison_count = 0
    def check_pair(saved, selected, reference, fields, label):
        nonlocal comparison_count
        computed = paired(selected, reference, fields, bs['bootstrap_seed'], bs['bootstrap_repetitions'])
        for key, value in computed.items():
            if key.endswith('ci95') or 'ci95_' in key: c.interval(saved[key], value, label + '/' + key)
            else: c.close(saved[key], value, label + '/' + key)
        comparison_count += 1
    for split, comparisons in summary['comparisons'].items():
        for reference, saved in comparisons.items():
            check_pair(saved, raw[('revision_selected', split)]['rows'], raw[(reference, split)]['rows'], bs['strata'][split], f'{split}/{reference}')
    for name, seeds in summary['per_seed'].items():
        for seed, saved in seeds.items():
            computed = statistics([r for r in raw[(name, 'main')]['rows'] if r['study_seed'] == int(seed)])
            for key, value in computed.items(): c.close(saved[key], value, f'per_seed/{name}/{seed}/{key}')
    for split, saved in summary['static_noninferiority'].items():
        select = lambda name: [r for r in raw[(name, split)]['rows'] if r['scenario'] == 'static_clean']
        check_pair(saved, select('revision_selected'), select('static_wls'), bs['strata'][split], 'noninferiority/' + split)
    for cell, comparisons in summary['edge_comparisons'].items():
        for reference, saved in comparisons.items():
            select = lambda name: [r for r in raw[(name, 'edge')]['rows'] if r['edge_group'] == cell]
            check_pair(saved, select('revision_selected'), select(reference), ['observation_n', 'scenario'], cell + '/' + reference)
    cal_data = read(directory / 'calibration_predictions.json'); cover_data = read(directory / 'coverage_predictions.json')
    cal = check_prediction('calibration', 'revision_selected', cal_data)
    cover = check_prediction('coverage', 'revision_selected', cover_data)
    regions = read(directory / 'conformal.json')
    c.assert_(regions == summary['conformal'], 'Conformal summary copy')
    scores = np.array([r['error_m'] / proxy(r) for r in cal]); groups = np.array([group(r) for r in cal])
    populations = {'iid_test': cover, 'shifted_stress': raw[('revision_selected', 'stress')]['rows'], 'edge': raw[('revision_selected', 'edge')]['rows']}
    region_count = 0
    for mode in ('global', 'mondrian'):
        for alpha, result in regions[mode].items():
            quantiles = {}
            for label, record in result['calibration'].items():
                subset = scores if label == 'global' else scores[groups == label]
                k = math.ceil((len(subset) + 1) * (1 - float(alpha)))
                q = np.sort(subset)[k - 1] if 1 <= k <= len(subset) else math.inf
                c.close(record['n'], len(subset), f'{mode}/{alpha}/{label}: calibration n')
                c.close(record['rank'], k, f'{mode}/{alpha}/{label}: rank')
                c.close(record['q'], q, f'{mode}/{alpha}/{label}: q')
                c.assert_(record['unbounded'] == math.isinf(q), 'Quantile infinity flag')
                quantiles[label] = q
            for population, rows in populations.items():
                record = result['populations'][population]
                radii = np.array([quantiles['global' if mode == 'global' else group(r)] * proxy(r) for r in rows])
                errors = np.array([r['error_m'] for r in rows]); hits = errors <= radii
                c.close(record['coverage'], hits.mean(), 'Coverage ' + mode + '/' + alpha + '/' + population)
                c.close(record['covered_n'], hits.sum(), 'Covered count')
                c.close(record['mean_radius_m'], radii.mean(), 'Mean radius')
                c.close(record['median_radius_m'], np.median(radii), 'Median radius')
                c.close(record['unbounded_n'], np.isinf(radii).sum(), 'Unbounded count')
                c.interval(record['coverage_wilson95'], wilson(int(hits.sum()), len(rows)) if population == 'iid_test' else None, 'Wilson population')
                for subgroup, labels in [('by_scenario', sorted({r['scenario'] for r in rows})), ('by_group', ['static', 'velocity'])]:
                    for label in labels:
                        mask = np.array([(r['scenario'] if subgroup == 'by_scenario' else group(r)) == label for r in rows])
                        saved = record[subgroup][label]; n = int(mask.sum()); count = int(hits[mask].sum())
                        c.close(saved['n'], n, 'Subgroup n'); c.close(saved['covered_n'], count, 'Subgroup covered')
                        c.close(saved['coverage'], float(count / n) if n else None, 'Subgroup coverage')
                        c.interval(saved['coverage_wilson95'], wilson(count, n) if population == 'iid_test' else None, 'Subgroup Wilson')
                for saved in record['rejection_curve']:
                    mask = radii <= saved['radius_threshold_m']; count = int(mask.sum())
                    c.close(saved['accepted_n'], count, 'Accepted count')
                    c.close(saved['accepted_coverage'], float(hits[mask].mean()) if count else None, 'Accepted coverage')
                c.assert_([r['case_id'] for r in record['rows']] == [r['case_id'] for r in rows], 'Region row order')
                for saved, radius, hit in zip(record['rows'], radii, hits):
                    c.close(saved['radius_m'], radius, 'Per-case radius'); c.assert_(saved['covered'] == bool(hit), 'Per-case coverage')
                region_count += 1
    with (directory / 'predictions.csv').open(encoding='utf-8', newline='') as stream:
        csvrows = list(csv.DictReader(stream))
    c.close(len(csvrows), matrix_rows, 'CSV matrix count')
    for row in csvrows:
        records = {r['case_id']: r for r in raw[(row['method'], row['split'])]['rows']}
        c.close(float(row['error_m']), records[row['case_id']]['error_m'], 'CSV error')
    unique = len(methods) - len(summary['aliases'])
    physical = unique * sum(len(inputs[s]) for s in ('main', 'stress', 'edge')) + len(cal) + len(cover)
    c.close(summary['prediction_count'], physical, 'Actual prediction count')
    c.close(summary['logical_prediction_count'], matrix_rows + len(cal) + len(cover), 'Logical prediction count')
    return {'ok': not c.errors, 'checks': c.count, 'errors': c.errors,
        'physical_prediction_errors_recomputed': physical, 'logical_matrix_rows': matrix_rows,
        'paired_bootstrap_comparisons_recomputed': comparison_count,
        'coverage_populations_recomputed': region_count, 'case_counts': summary['case_counts'],
        'scope': 'Read only saved inputs, positions and outcomes. No fitting, new data generation, retuning or modification of study evidence.',
        'limitations': 'Checks numerical consistency, not independence of research decisions, authenticity of all local records, model superiority or mathematical assumptions.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=Path('results/revision_benchmark'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try: result = audit(args.results)
    except Exception as exc: result = {'ok': False, 'errors': [type(exc).__name__ + ': ' + str(exc)]}
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    return 0 if result['ok'] else 1


if __name__ == '__main__': sys.exit(main())
