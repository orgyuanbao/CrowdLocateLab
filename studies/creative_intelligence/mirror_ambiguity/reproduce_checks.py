"""Recompute the archived study in temporary copies; no model calls or dependencies.

The original v2 verifier has a historical absolute-path hash list. Only its
temporary copy is relocated. Archived scripts and their raw reports stay intact.
An optional --output directory must be new and receives recomputation reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_files():
    manifest = json.loads((HERE / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    files = manifest['files']
    require(len(files) == 70, 'The expected 70-file original archive is incomplete.')
    for name, entry in files.items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts
                and '\\' not in name and ':' not in name
                and relative.as_posix() == name, 'Invalid source manifest path.')
        path = HERE / name
        require(not any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                        for p in (path, *path.parents)), 'Linked archive paths are unsupported.')
        require(path.is_file() and digest(path) == entry['sha256'],
                'Original archive hash mismatch: ' + name)
    return {name: entry['sha256'] for name, entry in files.items()}


def relocate_hash_list(folder):
    path = folder / 'v1_hashes_before_v2.json'
    entries = json.loads(path.read_text(encoding='utf-8-sig'))
    require(isinstance(entries, list) and len(entries) == 11, 'Unexpected v1 hash list.')
    names = set()
    for entry in entries:
        name = PureWindowsPath(entry['Path']).name
        require(re.fullmatch(r'[A-Za-z0-9_]+\.(?:py|md|json)', name)
                and name not in names, 'Unsafe or duplicate v1 hash filename.')
        names.add(name)
        target = folder / name
        require(target.is_file() and digest(target).upper() == entry['Hash'],
                'A v1 temporary copy differs from its original hash.')
        entry['Path'] = str(target)
    # This is a private temporary copy, never the published hash manifest.
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return len(entries)


def copy_verified(prefix, target, originals):
    target.mkdir(parents=True, exist_ok=False)
    for name, expected in originals.items():
        if name.startswith(prefix):
            data = (HERE / name).read_bytes()
            require(hashlib.sha256(data).hexdigest() == expected,
                    'Original archive changed before copying: ' + name)
            destination = target / name[len(prefix):]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def execute(script, arguments, cwd, env):
    command = [sys.executable, '-X', 'utf8', '-B', str(script), *arguments]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=120)
    return {'command': command, 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def reproduce(output):
    original = archive_files()
    evidence = {'schema_version': 1, 'status': 'failed', 'new_model_calls': 0,
                'scope': 'Recomputation of saved finite checks, not new research or formal proof.',
                'original_files_checked': len(original),
                'archive_manifest_sha256': digest(HERE / 'SOURCE_MANIFEST.json'),
                'original_files_unchanged': False, 'executions': []}
    reports = {}
    try:
        with tempfile.TemporaryDirectory(prefix='crowdlocate-mirror-') as temporary:
            root = Path(temporary)
            run = root / 'runtime' / 'runs' / 'mirror-ambiguity-20261015'
            copy_verified('artifacts/', run, original)
            private_verifier = root / 'verifier'
            copy_verified('artifacts/', private_verifier, original)
            evidence['relocated_historical_hash_paths'] = relocate_hash_list(private_verifier)
            review = root / 'post_review'
            review.mkdir()
            shutil.copyfile(HERE / 'post_review/review_v2.py', review / 'review_v2.py')
            env = os.environ.copy()
            env.pop('PYTHONOPTIMIZE', None)
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', TEMP=str(root), TMP=str(root))
            execution = execute(private_verifier / 'verify_v2.py',
                                ['--report', 'portable_reproduction.json'], root, env)
            evidence['executions'].append(execution)
            report_path = private_verifier / 'portable_reproduction.json'
            if report_path.is_file():
                reports['agent_verification.json'] = json.loads(report_path.read_text(encoding='utf-8'))
            require(execution['returncode'] == 0, 'The archived v2 verifier failed; inspect its execution output.')
            report = reports['agent_verification.json']
            require(report.get('status') == 'passed' and report.get('failures') == []
                    and len(report.get('checks', {})) == 6, 'The v2 verification report is incomplete.')
            execution = execute(review / 'review_v2.py', [], root, env)
            evidence['executions'].append(execution)
            report_path = review / 'review_v2.json'
            if report_path.is_file():
                reports['independent_review_v2.json'] = json.loads(report_path.read_text(encoding='utf-8'))
            require(execution['returncode'] == 0, 'The independent v2 regression failed.')
            external = reports['independent_review_v2.json']
            require(external.get('status') == 'passed_reviewed_regressions'
                    and external.get('runtime_sources_unchanged') is True
                    and external.get('major_remaining_errors_found') == [],
                    'The independent v2 regression report did not pass.')
            predicates = report['checks']['squared_predicates_vs_interval_oracle']
            evidence.update(status='passed', check_groups=len(report['checks']),
                            fixed_cases_per_version=predicates['fixed_cases_per_version'],
                            common_cases_per_version=predicates['common_cases_per_version'],
                            cli_regressions=report['checks']['CLI_reproductions_and_regressions']['cli_invocations'],
                            independent_legacy_cases=len(external['legacy_cases_unchanged_except_version']),
                            independent_numeric_cases=len(external['numeric_input_boundaries']),
                            independent_cli_cases=len(external['cli_cases']),
                            historical_v1_external_review='Archived only; not rerun by this standard-library entry.')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        evidence.update(status='failed', error=str(error))
    finally:
        try:
            require(archive_files() == original, 'Original archive changed during recomputation.')
            evidence['original_files_unchanged'] = True
        except (OSError, ValueError, KeyError, TypeError) as error:
            evidence.update(status='failed', integrity_error=str(error))
        if output is not None:
            for name, report in {**reports, 'reproduction.json': evidence}.items():
                with (output / name).open('x', encoding='utf-8') as destination:
                    json.dump(report, destination, ensure_ascii=False, indent=2)
                    destination.write('\n')
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New directory for recomputation JSON reports; never overwrites.')
    args = parser.parse_args()
    try:
        if args.output is not None:
            args.output.mkdir(parents=False, exist_ok=False)
        result = reproduce(args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'status': 'failed', 'new_model_calls': 0, 'error': str(error)}
    compact = {key: value for key, value in result.items() if key != 'executions'}
    if args.output is not None:
        compact['output'] = str(args.output)
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
