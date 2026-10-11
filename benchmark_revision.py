"""One preregistered fresh evaluation after the second research agent freezes."""
from pathlib import Path
import argparse
import csv
import platform
import sys
import time

import numpy as np
import scipy

from revision.evaluation import (validate_revision_freeze, build_methods,
    generate_datasets, evaluate_cases, summarize_comparisons, compare_conformal,
    save_json, source_hash)


def run_benchmark(root, run, output):
    root, run, output = Path(root).resolve(), Path(run).resolve(), Path(output).resolve()
    if output.exists(): raise FileExistsError('Refuse to overwrite any previous evaluation directory')
    # This must precede output generation AND every generator call.
    checked = validate_revision_freeze(root, run)
    plan = checked['plan']; chosen = checked['frozen']['spec']
    methods, aliases, ablations = build_methods(chosen, plan)
    started = time.perf_counter()
    output.mkdir(parents=True)
    protocol = {'version': 'v3-second-study-independent-evaluation', 'plan': plan,
        'methods': methods, 'aliases': aliases, 'ablations': ablations,
        'alias_policy': 'Exact effective specs reuse the same saved predictions. Aliases are not independent methods; timing is reused from their canonical method.',
        'freeze': checked['frozen'], 'pre_holdout_integrity_check': checked['audit'],
        'revision_provenance': checked['revision_provenance'],
        'frozen_file_sha256': source_hash(run / 'tools/frozen_method.json'),
        'environment': {'python': sys.version.split()[0], 'numpy': np.__version__,
            'scipy': scipy.__version__, 'platform': platform.platform()},
        'entrypoint': 'benchmark_revision.py',
        'policy': 'One designated evaluator and all preregistered populations. Never tune on these results.'}
    save_json(output / 'protocol.json', protocol)
    save_json(output / 'status.json', {'status': 'running', 'fresh_generation_authorized_by_freeze': True})
    try:
        datasets = generate_datasets(plan)
        for split, cases in datasets.items():
            save_json(output / f'{split}_inputs.json', [case.to_dict() for case in cases])
        raw, summary, csvrows = {}, {}, []
        actual_predictions = 0
        for name, spec in methods.items():
            print(f'Evaluate {name}' + (f' (exact alias of {aliases[name]})' if name in aliases else ''), flush=True)
            summary[name] = {}
            for split in ('main', 'stress', 'edge'):
                if name in aliases:
                    result = {**raw[(aliases[name], split)], 'alias_of': aliases[name], 'timing_reused': True}
                else:
                    result = evaluate_cases(datasets[split], spec)
                    actual_predictions += len(datasets[split])
                raw[(name, split)] = result
                save_json(output / f'{split}_{name}.json', result)
                summary[name][split] = {k: v for k, v in result.items() if k != 'rows'}
                for row in result['rows']:
                    csvrows.append({'method': name, 'split': split, 'case_id': row['case_id'],
                        'scenario': row['scenario'], 'study_seed': row['study_seed'],
                        'observation_n': row['observation_n'], 'error_m': row['error_m'],
                        'x': row['position'][0], 'y': row['position'][1],
                        'selected_model': row['diagnostics'].get('selected_model'),
                        'work_units': row['work_units'], 'elapsed_seconds': row['elapsed_seconds'],
                        'alias_of': aliases.get(name, '')})
        print('Compute paired estimates and independent calibration/coverage', flush=True)
        comparisons, per_seed, noninferiority, edge_comparisons = summarize_comparisons(raw, methods, plan)
        cal = evaluate_cases(datasets['calibration'], chosen)
        cover = evaluate_cases(datasets['coverage'], chosen)
        actual_predictions += len(datasets['calibration']) + len(datasets['coverage'])
        save_json(output / 'calibration_predictions.json', cal)
        save_json(output / 'coverage_predictions.json', cover)
        conformal = compare_conformal(cal['rows'], {
            'iid_test': cover['rows'], 'shifted_stress': raw[('revision_selected', 'stress')]['rows'],
            'edge': raw[('revision_selected', 'edge')]['rows']}, plan)
        save_json(output / 'conformal.json', conformal)
        results = {'protocol': protocol, 'frozen': checked['frozen'], 'metrics': summary,
            'aliases': aliases, 'comparisons': comparisons, 'per_seed': per_seed,
            'static_noninferiority': noninferiority, 'edge_comparisons': edge_comparisons,
            'conformal': conformal, 'wall_seconds': time.perf_counter() - started,
            'prediction_count': actual_predictions,
            'logical_prediction_count': len(csvrows) + len(datasets['calibration']) + len(datasets['coverage']),
            'case_counts': {split: len(cases) for split, cases in datasets.items()},
            'unique_method_count': len(methods) - len(aliases), 'declared_method_count': len(methods),
            'status': 'complete'}
        save_json(output / 'summary.json', results)
        with (output / 'predictions.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(csvrows[0]))
            writer.writeheader(); writer.writerows(csvrows)
        save_json(output / 'status.json', {'status': 'complete',
            'summary_sha256': source_hash(output / 'summary.json'),
            'prediction_count': actual_predictions})
        print(f'Complete: {actual_predictions} actual predictions; {results["wall_seconds"]:.1f}s', flush=True)
        return results
    except Exception as exc:
        save_json(output / 'status.json', {'status': 'failed', 'error_type': type(exc).__name__,
            'error': str(exc), 'policy': 'Preserve this attempt; no overwrite or outcome-driven tuning.'})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=Path('runs/revision_reference'))
    parser.add_argument('--output', type=Path, default=Path('results/revision_benchmark'))
    parser.add_argument('--validate-only', action='store_true', help='Check frozen provenance; generate no cases')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.validate_only:
        checked = validate_revision_freeze(root, args.run)
        print('Revision frozen provenance verified; no fresh data generated.')
        print(checked['revision_provenance']['plan_sha256'])
    else:
        run_benchmark(root, args.run, args.output)


if __name__ == '__main__':
    main()
