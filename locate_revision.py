"""Apply the verified revision estimator and either precomputed region rule.

The JSON input contains observations only. A radius threshold can reject an
output but does not create a conditional coverage guarantee.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math

from benchmark import scale
from localization import fit
from locate import load_json, observations_from_dict, _reject_constant, _unique_object
from revision.evaluation import observed_group, validate_revision_freeze
from tools.common import clean, digest

ROOT = Path(__file__).resolve().parent


def _json_snapshot(path):
    """Parse and hash the same bytes, retaining strict JSON input rules."""
    data = Path(path).read_bytes()
    value = json.loads(data.decode('utf-8-sig'), parse_constant=_reject_constant,
                       object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError('Calibration evidence must contain JSON objects')
    return value, hashlib.sha256(data).hexdigest()


def completed_calibration(calibration, run, frozen):
    """Bind raw completion marker to summary, calibration, protocol and freeze.

    These are consistency checks, not signatures: coordinated rewriting of every
    local file is outside the integrity guarantee.
    """
    calibration = Path(calibration)
    parent = calibration.parent
    status, status_hash = _json_snapshot(parent/'status.json')
    if status.get('status') != 'complete':
        raise ValueError('Revision calibration evaluation must be complete')
    summary, summary_hash = _json_snapshot(parent/'summary.json')
    if status.get('summary_sha256') != summary_hash:
        raise ValueError('Completion marker does not match the summary bytes')
    if summary.get('status') != 'complete':
        raise ValueError('Revision calibration summary must be complete')
    protocol, protocol_hash = _json_snapshot(parent/'protocol.json')
    records, calibration_hash = _json_snapshot(calibration)
    if digest(summary.get('protocol')) != digest(protocol):
        raise ValueError('Calibration protocol differs from the completed summary')
    if digest(summary.get('conformal')) != digest(records):
        raise ValueError('Calibration contents differ from the completed summary')
    if digest(summary.get('frozen')) != digest(frozen) or digest(protocol.get('freeze')) != digest(frozen):
        raise ValueError('Completed calibration references a different frozen object')
    frozen_hash = hashlib.sha256((Path(run)/'tools/frozen_method.json').read_bytes()).hexdigest()
    if protocol.get('frozen_file_sha256') != frozen_hash:
        raise ValueError('Calibration belongs to a different frozen research record')
    methods = protocol.get('methods')
    if not isinstance(methods, dict) or digest(methods.get('revision_selected')) != digest(frozen['spec']):
        raise ValueError('Calibration and point estimator specifications differ')
    return records, {'frozen_method_sha256': frozen_hash, 'calibration_sha256': calibration_hash,
                     'summary_sha256': summary_hash, 'protocol_sha256': protocol_hash,
                     'completion_status_sha256': status_hash,
                     'calibration_completion_verified': True,
                     'provenance_scope': 'Local byte/object consistency; not a digital signature or protection against rewriting all evidence.'}


def region_from_files(result, run, calibration, frozen, rule='mondrian', alpha=.1):
    if rule not in ('global', 'mondrian'):
        raise ValueError('Region rule must be global or mondrian')
    if type(alpha) not in (int, float) or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError('alpha must be finite and between zero and one')
    calibration = Path(calibration)
    if not calibration.is_file():
        raise ValueError('A completed revision calibration file is required; no finite radius is invented')
    records, provenance = completed_calibration(calibration, run, frozen)
    rule_records = records.get(rule)
    if not isinstance(rule_records, dict):
        raise ValueError('Requested region rule is absent from calibration')
    record = rule_records.get(str(float(alpha)))
    if (not isinstance(record, dict) or type(record.get('alpha')) not in (int, float)
            or record['alpha'] != alpha):
        raise ValueError('Requested alpha must exactly match a stored calibration level')
    observed = observed_group(result)
    group = observed if rule == 'mondrian' else 'global'
    quantiles = record.get('calibration')
    if not isinstance(quantiles, dict):
        raise ValueError('Calibration group records must be an object')
    quantile = quantiles.get(group)
    if not isinstance(quantile, dict):
        raise ValueError('The required observation group is missing from calibration')
    n, k = quantile.get('n'), quantile.get('rank')
    if type(n) is not int or n < 0 or type(k) is not int or k != math.ceil((n + 1)*(1-alpha)):
        raise ValueError('Invalid calibration group size or conformal order statistic')
    unbounded = k > n
    if type(quantile.get('unbounded')) is not bool or quantile['unbounded'] != unbounded:
        raise ValueError('Inconsistent unbounded-region indicator')
    q = quantile.get('q')
    if unbounded:
        if q != 'infinity': raise ValueError('An unbounded region must have q=infinity')
        radius = math.inf
    else:
        if type(q) not in (int, float) or not math.isfinite(q) or q < 0:
            raise ValueError('A finite quantile must be a nonnegative number')
        radius = q * float(scale(result))
        if not math.isfinite(radius): raise ValueError('Finite calibrated radius overflow')
    return {'rule': rule, 'alpha': alpha, 'nominal_coverage': 1-alpha,
            'status': 'unbounded' if unbounded else 'available_under_assumptions',
            'observed_model_group': observed, 'calibration_group': group,
            'calibration_n': n, 'order_statistic_k': k, 'q': q,
            'radius_m': 'infinity' if unbounded else radius, 'unbounded': unbounded,
            'center': result['position'], 'geometry_scale_m': float(scale(result)),
            **provenance,
            'coverage_guarantee_asserted_for_this_input': False,
            'exchangeability_verified_for_this_input': False,
            'scope': '全局或可观测分组覆盖需要同分布、冻结估计器/尺度/分组；不保证本条现场输入、真实场景、拒绝子集或分布偏移下的覆盖。'}, radius


def predict(input_path, run, calibration, rule='mondrian', alpha=.1, max_radius=None, root=ROOT):
    if max_radius is not None and (type(max_radius) not in (int, float) or
            not math.isfinite(max_radius) or max_radius <= 0):
        raise ValueError('max_radius must be a finite positive number')
    checked = validate_revision_freeze(root, run)
    obs = observations_from_dict(load_json(input_path))
    result = clean(fit(obs, checked['frozen']['spec']))
    for key in ('position', 'velocity'):
        vector = result.get(key)
        if not isinstance(vector, list) or len(vector) != 2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):
            raise ValueError('Estimator returned an invalid '+key)
    if not isinstance(result.get('diagnostics'), dict):
        raise ValueError('Estimator returned invalid diagnostics')
    selected = result.get('selected_model')
    selected_group = observed_group({'diagnostics': {'selected_model': selected}})
    if 'selected_model' in result['diagnostics'] and observed_group(result) != selected_group:
        raise ValueError('Estimator model and diagnostic observation group disagree')
    result['diagnostics']['selected_model'] = selected
    region, radius = region_from_files(result, run, calibration, checked['frozen'], rule, alpha)
    accepted = None if max_radius is None else bool(radius <= max_radius)
    return {'schema_version': 3, 'method_id': checked['frozen']['method_id'],
            'method_spec': checked['frozen']['spec'], 'input_sha256': hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
            'observation_count': len(obs.times), 'position': result['position'], 'velocity': result['velocity'],
            'selected_model': result['selected_model'], 'diagnostics': result['diagnostics'], 'region': region,
            'acceptance': {'max_radius_m': max_radius, 'accepted': accepted,
                           'status': 'not_applied' if accepted is None else 'accepted' if accepted else 'rejected',
                           'conditional_coverage_guaranteed': False},
            'run_integrity': checked['audit'],
            'interpretation': '位置为输入平面坐标中的t=0估计，单位米；速度单位米/秒。输入没有真值，不能计算定位误差。拒绝时点估计仍作为诊断保留。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--run', type=Path, default=Path('runs/revision_reference'))
    parser.add_argument('--calibration', type=Path, default=Path('results/revision_benchmark/conformal.json'))
    parser.add_argument('--region', choices=('global','mondrian'), default='mondrian')
    parser.add_argument('--alpha', type=float, default=.1)
    parser.add_argument('--max-radius', type=float)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.output and (args.output.exists() or args.output.resolve().is_relative_to(args.run.resolve())):
            raise ValueError('Choose a new output filename outside the preserved research run')
        result = predict(args.input, args.run, args.calibration, args.region, args.alpha, args.max_radius)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf8') as stream: stream.write(text)
            print(f'Revision estimate saved: {args.output}')
        else: print(text, end='')
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f'locate_revision: {error}\n')


if __name__ == '__main__': main()
