#!/bin/bash
# Vendored runtime copies in each task match runtime/ exactly.
source "$(dirname "$0")/lib.sh"
TASKS=$(task_dirs "$@")
[ -z "$TASKS" ] && { echo "No tasks to check"; exit 0; }
# shellcheck disable=SC2086
python3 tools/vendor_runtime.py --check $TASKS && echo "PASS check-runtime-sync"
