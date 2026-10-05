#!/bin/bash
# task.toml carries the required [task] and [metadata] fields.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
config = tomllib.loads((task / "task.toml").read_text())
errors = []
t, m = config.get("task", {}), config.get("metadata", {})
if t.get("name") != f"as-bench/{task.name}":
    errors.append(f'[task].name must be "as-bench/{task.name}"')
for key in ("description", "authors", "keywords"):
    if not t.get(key):
        errors.append(f"[task].{key} is required")
for key in ("author_name", "author_email", "author_organization", "domain", "field",
            "tags", "expert_time_estimate_hours", "relevant_experience", "conflicts_of_interest"):
    if m.get(key) in (None, "", []):
        errors.append(f"[metadata].{key} is required")
if not m.get("source"):
    errors.append("[metadata].source is required: an id from sources/<id>/source.toml")
if "node" in m:
    errors.append("[metadata].node is not used; name the data provider with [metadata].source")
if m.get("lab_api_version") != 1:
    errors.append("[metadata].lab_api_version must be 1")
if m.get("backend") not in ("replay", "twin", "live"):
    errors.append("[metadata].backend must be replay, twin, or live")
if "artifacts" in config.get("verifier", {}):
    errors.append("artifacts must be top-level, not under [verifier]")
for error in errors:
    print(f"FAIL {task}: {error}")
sys.exit(1 if errors else 0)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-task-fields"
exit $FAILED
