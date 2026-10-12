"""Repeat the engineering review without model calls or new study predictions.

Run from the project root. Optional --output must name a new file. This is
software/evidence verification, not an independent scientific replication.

Test counts ``run`` and ``passed`` count parent test cases. ``failures``,
``errors`` and ``skipped`` retain unittest's diagnostic events, so multiple
subtest events may belong to one parent case and must not be subtracted from
the number of cases run.
"""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy
import scipy
from audit import audit_run
from revision.evaluation import validate_revision_freeze
from verification.check_revision_results import audit as check_statistics


class SuccessfulCaseResult(unittest.TextTestResult):
    """Count successful parent cases independently of diagnostic events."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successful_cases = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successful_cases += 1


def summarize_tests(tested):
    """Return case counts and the original unittest diagnostic event counts."""
    return {
        'run': tested.testsRun, 'passed': tested.successful_cases,
        'failures': len(tested.failures), 'errors': len(tested.errors),
        'skipped': [{'test': str(test), 'reason': reason} for test, reason in tested.skipped],
        'successful': tested.wasSuccessful(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('Choose a new output file to preserve previous verification.')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    messages = io.StringIO()
    tested = unittest.TextTestRunner(
        stream=messages, verbosity=1, resultclass=SuccessfulCaseResult,
    ).run(suite)
    if not tested.wasSuccessful():
        sys.stderr.write(messages.getvalue())
    audits = {}
    for name in ('live_reference', 'revision_reference'):
        audits[name] = audit_run(ROOT, ROOT / 'runs' / name, require_frozen=True)
        audits[name]['run_directory'] = 'runs/' + name
    checked = validate_revision_freeze(ROOT, ROOT / 'runs/revision_reference')
    statistics = check_statistics(ROOT / 'results/revision_benchmark')
    result = {
        'review_date_utc': datetime.now(timezone.utc).isoformat(),
        'baseline_commit': '270b8040917386b8b3ac08e0bf0ab4e6f145642c',
        'scope': 'Engineering regression tests and consistency checks on preserved research evidence.',
        'new_model_calls': 0, 'new_research_predictions': 0,
        'environment': {'python': platform.python_version(), 'numpy': numpy.__version__, 'scipy': scipy.__version__},
        'software_tests': summarize_tests(tested),
        'historical_audits': audits,
        'freeze_validation': {'passed': True, 'method_id': checked['frozen']['method_id']},
        'statistical_consistency': statistics,
        'limitations': [
            'Finite tests are not a proof of zero defects, security or industrial performance.',
            'Numerical core and historical research remain unchanged; new application screening does not create coverage guarantees.',
            'No fresh live-model run or new blind study is claimed for the engineering changes.',
        ],
    }
    result['ok'] = tested.wasSuccessful() and all(x['exit_code'] == 0 for x in audits.values()) and statistics['ok']
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf8') as stream:
            stream.write(encoded)
    print(encoded)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
