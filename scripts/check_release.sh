#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
work=$(mktemp -d /tmp/enterpriseengine-gate.XXXXXX)
trap 'rm -rf "$work"' EXIT

printf 'Candidate source: %s\n' "$(git rev-parse HEAD)"
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)
mkdir -p "$work/source" "$work/client/examples"
git archive HEAD | tar -x -C "$work/source"
cd "$work"

python=${PYTHON:-python3}
"$python" -I -m venv candidate-env upgrade-env
candidate="$work/candidate-env/bin/python"
upgrade="$work/upgrade-env/bin/python"

"$candidate" -I -m pip wheel --no-deps --wheel-dir candidate source/
candidate_wheels=(candidate/enterpriseengine-*.whl)
test "${#candidate_wheels[@]}" -eq 1
candidate_wheel="$work/${candidate_wheels[0]}"
candidate_version=${candidate_wheel##*/enterpriseengine-}
candidate_version=${candidate_version%%-*}
"$candidate" -I -m pip hash "$candidate_wheel"

# Build the previous release from the commit published as v0.2.0.
"$candidate" -I -m pip wheel --no-deps --wheel-dir baseline \
    'enterpriseengine @ git+https://github.com/debsagar/enterpriseengine.git@285eb0423cee85845ec667db2702e0b192a5ec56'
"$candidate" -I -m pip install --no-index "$candidate_wheel"
"$candidate" -I -m pip check
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

cp source/scripts/check_release_checkpoint.py source/tests/test_engine_resume_shifts.py client/
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
run_installed "$upgrade" "$candidate_version" check_release_checkpoint resume

tests=(test_engine_resume_shifts)
for test_module in test_engine_obligations test_obligation_cases; do
    if [[ -f "../source/tests/$test_module.py" ]]; then
        cp "../source/tests/$test_module.py" .
        tests+=("$test_module")
    else
        printf 'Optional suite absent: %s\n' "$test_module"
    fi
done

if [[ -f ../source/examples/obligation_cases.py ]]; then
    cp ../source/examples/obligation_cases.py examples/
    for case in warehouse sla; do
        for cutoff in 19 27; do
            printf 'Obligation example (%s, cutoff %s)\n' "$case" "$cutoff"
            run_installed "$candidate" "$candidate_version" examples.obligation_cases \
                "$case" --run "$case-run" --until "$cutoff"
        done
    done
else
    printf 'Optional example absent: examples/obligation_cases.py\n'
fi
if [[ -f ../source/tests/test_managed_services_obligations.py ]]; then
    # The suite's Goals fixture uses TestCase.enterContext (Python 3.11+).
    if "$candidate" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        mkdir -p consumers native norvale/loader
        cp ../source/consumers/{__init__,extraction,managed_services,shared}.py consumers/
        cp ../source/native/managed_services.py native/
        cp ../source/norvale/__init__.py norvale/
        cp ../source/norvale/loader/{__init__,erp_client}.py norvale/loader/
        cp ../source/tests/{test_engine_goals,test_managed_services_obligations}.py .
        tests+=(test_managed_services_obligations)
    else
        printf 'Managed-services obligation suite requires Python 3.11 or newer\n'
    fi
fi
run_installed "$candidate" "$candidate_version" unittest -v "${tests[@]}"
