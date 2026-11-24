#!/usr/bin/env bash
# Freeze the application code for a new isolated acceptance run.
set -euo pipefail
umask 077
runtime=${1:?new absolute directory}
source=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
runtime=$(realpath -m "$runtime")
[[ "$runtime" != "$source" && "$runtime" != "$source/"* ]]
mkdir -m 700 "$runtime"
mkdir "$runtime/release"
git -C "$source" archive HEAD | tar -xpf - -C "$runtime/release"
integrations="$runtime/release/packages/environments/src/environments/norvale/integrations"
cp -a "$integrations/revisions/odoo_addons/." "$integrations/mes/addons/"
(cd "$runtime/release" && UV_PROJECT_ENVIRONMENT="$runtime/release/.venv" uv sync --locked --all-packages)
