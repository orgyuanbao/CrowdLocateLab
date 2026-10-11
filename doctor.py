"""Check local prerequisites without login, credentials, model calls or experiments."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def check_environment(require_codex=False):
    checks = [{"name": "python", "status": "ok" if sys.version_info >= (3, 11) else "error",
               "version": sys.version.split()[0], "requirement": ">=3.11"}]
    for module_name, minimum, maximum in (("numpy", (1, 26), (3, 0)), ("scipy", (1, 11), (2, 0))):
        try:
            module = importlib.import_module(module_name)
            version = str(module.__version__)
            match = re.match(r"(\d+)\.(\d+)", version)
            compatible = bool(match) and minimum <= tuple(map(int, match.groups())) < maximum
            checks.append({"name": module_name, "version": version, "status": "ok" if compatible else "error",
                           "requirement": f">={minimum[0]}.{minimum[1]},<{maximum[0]}"})
        except Exception as error:
            checks.append({"name": module_name, "status": "error", "error_type": type(error).__name__,
                           "message": "Import failed; install requirements.txt in this Python environment."})
    executable = os.environ.get("CODEX_EXECUTABLE") or shutil.which("codex")
    cli = {"name": "codex_cli", "status": "warning", "version": None,
           "required_for_numeric_reproduction": False, "path": executable,
           "authentication": "not_checked", "configured_model": "not_checked"}
    if executable:
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            # Only report a version line; never display arbitrary diagnostic text.
            match = re.search(r"(?mi)^\s*(?:codex(?:-cli)?\s+)?(\d+\.\d+\.[\w.+-]+)\s*$", result.stdout)
            cli.update(status="ok" if result.returncode == 0 else "warning",
                       version=match.group(1) if match else "not_reported", exit_code=result.returncode)
        except (OSError, subprocess.TimeoutExpired) as error:
            cli["error_type"] = type(error).__name__
    else:
        cli["message"] = "CLI not found. Numeric reproduction works without it; real research needs Codex CLI."
    if require_codex and cli["status"] != "ok":
        cli["status"] = "error"
    checks.append(cli)
    errors = sum(c["status"] == "error" for c in checks)
    return {"status": "ready" if errors == 0 else "missing_prerequisites", "checks": checks,
            "error_count": errors, "model_calls": 0, "login_attempted": False,
            "credentials_read": False,
            "scope": "Local imports and codex --version only. Does not verify authentication, quota, remote model availability or scientific results."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable checks")
    parser.add_argument("--require-codex", action="store_true", help="Treat missing/unusable CLI as an error")
    args = parser.parse_args()
    result = check_environment(args.require_codex)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(f"[{check['status'].upper()}] {check['name']}: {check.get('version') or check.get('message', 'unavailable')}")
        print(result["scope"])
    return 0 if result["error_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
