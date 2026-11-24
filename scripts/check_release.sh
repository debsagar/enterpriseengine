#!/usr/bin/env bash
set -euo pipefail

# Example tests also launch Python subprocesses with the staged client as cwd.
unset PYTHONPATH PYTHONHOME

cd "$(dirname "${BASH_SOURCE[0]}")/.."
work=$(mktemp -d "${TMPDIR:-/tmp}/enterpriseengine-gate.XXXXXX")
trap 'rm -rf "$work"' EXIT

printf 'Candidate source: %s\n' "$(git rev-parse HEAD)"
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)
mkdir -p "$work/source" "$work/client/docs/examples"
git archive HEAD | tar -x -C "$work/source"
cd "$work"

python=${PYTHON:-python3}
"$python" -I -m venv candidate-env upgrade-env
candidate="$work/candidate-env/bin/python"
upgrade="$work/upgrade-env/bin/python"

"$candidate" -I -m pip wheel --no-deps --wheel-dir candidate \
    source/packages/{engine,components,environments}
candidate_wheels=(candidate/enterpriseengine-*.whl)
test "${#candidate_wheels[@]}" -eq 1
candidate_wheel="$work/${candidate_wheels[0]}"
candidate_version=${candidate_wheel##*/enterpriseengine-}
candidate_version=${candidate_version%%-*}
"$candidate" -I -m pip hash "$candidate_wheel"

"$candidate" -I -m pip install --no-index candidate/*.whl
"$candidate" -I -m pip check


"$candidate" -I - <<'PY_CHECK_RESOURCES'
from importlib.resources import files

norvale = files("environments.norvale")
for path in (
    "scenarios/norvale-q1.json",
    "environment/images/Dockerfile.erp",
    "environment/images/Dockerfile.calendar",
    "integrations/mes/compose.yaml",
    "integrations/qms/compose.yml",
    "integrations/company_run/provision.sh",
    "integrations/mes/addons/norvale_mes/security/ir.model.access.csv",
    "integrations/revisions/odoo_addons/norvale_revision_receiver/__manifest__.py",
):
    assert norvale.joinpath(*path.split("/")).is_file(), path
PY_CHECK_RESOURCES

# Build the previous release from the commit published as v0.2.0.
"$candidate" -I -m pip wheel --no-deps --wheel-dir baseline \
    'enterpriseengine @ git+https://github.com/debsagar/enterpriseengine.git@285eb0423cee85845ec667db2702e0b192a5ec56'
"$upgrade" -I -m pip install --no-index baseline/enterpriseengine-0.2.0-*.whl

# Expose only staged client code, after checking both installed imports.
run_installed() {
    local interpreter=$1 expected_version=$2
    shift 2
    "$interpreter" -I -c '
from importlib.metadata import version
from pathlib import Path
import runpy
import sys
import sysconfig

import engine
import enterpriseengine

site_packages = Path(sysconfig.get_path("purelib")).resolve()
for package in (engine, enterpriseengine):
    location = Path(package.__file__).resolve()
    assert location.is_relative_to(site_packages), location
expected_version = sys.argv.pop(1)
assert version("enterpriseengine") == expected_version, version("enterpriseengine")
sys.path.append(str(Path.cwd()))
runpy.run_module(sys.argv.pop(1), run_name="__main__", alter_sys=True)
' "$expected_version" "$@"
}

cp source/scripts/check_release_checkpoint.py source/packages/engine/tests/test_engine_resume_shifts.py client/
# Run the Python example published in the README.
awk '
    /^## Install and use from Python$/ { section = 1; next }
    /^## / { section = 0 }
    section && /^```python$/ { code = 1; next }
    code && /^```$/ { exit }
    code { print }
' source/README.md > client/my_company.py

cd client
for attempt in first-run reopen; do
    output=$(run_installed "$candidate" "$candidate_version" my_company)
    printf 'Public Python example (%s): %s\n' "$attempt" "$output"
    test "$output" = "{'A': [550, 'out of range'], 'B': [560, 'in range']}"
done

run_installed "$upgrade" 0.2.0 check_release_checkpoint seed
"$upgrade" -I -m pip install --no-index --upgrade "$candidate_wheel"
run_installed "$upgrade" "$candidate_version" check_release_checkpoint reject

tests=(test_engine_resume_shifts test_engine_obligations test_obligation_cases)
cp ../source/packages/engine/tests/test_engine_obligations.py ../source/contracts/test_obligation_cases.py .
cp ../source/docs/examples/obligation_cases.py docs/examples/
for case in warehouse sla; do
    for cutoff in 19 27; do
        printf 'Obligation example (%s, cutoff %s)\n' "$case" "$cutoff"
        run_installed "$candidate" "$candidate_version" docs.examples.obligation_cases \
            "$case" --run "$case-run" --until "$cutoff"
    done
done

cp ../source/contracts/{test_engine_goals,test_managed_services_obligations}.py .
# The suite's Goals fixture uses TestCase.enterContext (Python 3.11+).
if "$candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    tests+=(test_managed_services_obligations)
else
    printf 'Managed-services obligation suite requires Python 3.11 or newer\n'
fi
run_installed "$candidate" "$candidate_version" unittest -v "${tests[@]}"
