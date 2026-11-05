#!/usr/bin/env bash
# Builds agents/<runtime>.zip for AgentCore direct code deployment.
#
#   scripts/package_runtime.sh policy_matching_runtime
#   scripts/package_runtime.sh medical_necessity_runtime
#
# IMPORTANT: the zip is built from INSIDE the runtime folder, so server.py sits
# at the zip root. Zipping the folder itself (zip -r x.zip folder/) produces an
# opaque "entrypoint could not be found or accessed" failure at deploy time.
# This script does it the right way and checks the result.
set -euo pipefail

runtime="${1:?usage: scripts/package_runtime.sh <runtime_folder_name, e.g. medical_necessity_runtime>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/agents/$runtime"
out="$root/agents/$runtime.zip"
[ -f "$dir/server.py" ] || { echo "no server.py in $dir" >&2; exit 1; }

python3 "$root/scripts/check_agent_types_sync.py"

if [ "$runtime" = "medical_necessity_runtime" ]; then
  # The peer call needs boto3 inside the container; direct code deployment
  # does not run pip install, so vendor it into the folder.
  python3 "$root/scripts/gen_runtime_config_arns.py"
  if [ ! -d "$dir/boto3" ] || [ "${REFRESH_BOTO3:-0}" = "1" ]; then
    pip install --quiet --upgrade boto3 -t "$dir"
  fi
fi

rm -f "$out"
( cd "$dir" && zip -rqX "$out" . -x '__pycache__/*' '*/__pycache__/*' '*.pyc' )

if ! unzip -Z1 "$out" | grep -qx 'server.py'; then
  echo "ERROR: server.py is not at the root of $out" >&2
  exit 1
fi
echo "built $out ($(du -h "$out" | cut -f1)), server.py at zip root"
