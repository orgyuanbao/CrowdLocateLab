#!/usr/bin/env sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dashboard_port=${1:-8766}
project_root=${2:-$script_dir}
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$script_dir/dashboard_revision.py" --root "$project_root" --port "$dashboard_port"
elif command -v python >/dev/null 2>&1; then
    exec python "$script_dir/dashboard_revision.py" --root "$project_root" --port "$dashboard_port"
else
    printf '%s\n' 'Python was not found. Use an existing Python interpreter to run dashboard_revision.py.' >&2
    exit 1
fi
