"""Regenerate completed evidence sections and compile the LaTeX report safely.

This command performs no model calls or scientific tuning. It fails on missing
tools, incomplete evidence, or LaTeX errors instead of claiming a stale PDF.
"""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def resolve_tool(name, configured=None):
    value = configured or shutil.which(name)
    if not value:
        raise RuntimeError(f'{name} was not found. Install TeX Live/MiKTeX and add its bin directory to PATH, or pass --{name} PATH.')
    path = Path(value)
    if not path.is_file():
        raise RuntimeError(f'{name} executable does not exist: {path}')
    return str(path.resolve())


def invoke(command, cwd, log):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf8', errors='replace', creationflags=flags)
    log.write_text(result.stdout, encoding='utf8')
    if result.returncode:
        raise RuntimeError(f'Command failed with exit {result.returncode}. Inspect {log}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xelatex')
    parser.add_argument('--bibtex')
    parser.add_argument('--skip-refresh', action='store_true', help='Compile existing TeX sections only')
    parser.add_argument('--output', type=Path, default=ROOT/'report/CrowdLocateLab_Report.pdf')
    args = parser.parse_args()
    try:
        xelatex = resolve_tool('xelatex', args.xelatex)
        bibtex = resolve_tool('bibtex', args.bibtex)
        logs = ROOT/'latex/build_logs'
        logs.mkdir(parents=True, exist_ok=True)
        if not args.skip_refresh:
            for script in ('build_report.py', 'sensitivity_figures.py', 'revision_report.py'):
                invoke([sys.executable, '-B', '-X', 'utf8', str(ROOT/'reporting'/script)], ROOT, logs/(script+'.txt'))
        for index, command in enumerate((
            [xelatex, '-interaction=nonstopmode', '-halt-on-error', 'main.tex'],
            [bibtex, 'main'],
            [xelatex, '-interaction=nonstopmode', '-halt-on-error', 'main.tex'],
            [xelatex, '-interaction=nonstopmode', '-halt-on-error', 'main.tex'],
        ), 1):
            invoke(command, ROOT/'latex', logs/f'pass_{index}.txt')
        text = (ROOT/'latex/main.log').read_text(encoding='utf8', errors='replace')
        problems = [line for line in text.splitlines() if any(token in line for token in
                    ('Overfull', 'Missing character', 'undefined references', 'undefined citations'))]
        if problems:
            raise RuntimeError('Layout or reference warnings remain: ' + '\n'.join(problems))
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/'latex/main.pdf', output)
        print(json.dumps({'status': 'compiled', 'pdf': str(output),
                          'visual_review_still_required': True}, ensure_ascii=False))
    except (OSError, RuntimeError) as error:
        parser.exit(2, f'compile_report: {error}\n')


if __name__ == '__main__':
    main()
