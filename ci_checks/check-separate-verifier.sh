#!/bin/bash
# The verifier runs in its own image (environment_mode = "separate"), bakes its
# scripts into /tests, and pre-creates the parent of every declared artifact.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import re, sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
config = tomllib.loads((task / "task.toml").read_text())
dockerfile = (task / "tests/Dockerfile").read_text()
errors = []
if config.get("verifier", {}).get("environment_mode") != "separate":
    errors.append('[verifier].environment_mode must be "separate"')
if not re.search(r"^\s*(COPY|ADD)\b.*\s/tests/?", dockerfile, re.M):
    errors.append("tests/Dockerfile must COPY verifier files into /tests")
created = set()
for match in re.finditer(r"mkdir\s+-p\s+([^\n&;]+)", dockerfile):
    created.update(match.group(1).split())
for artifact in config.get("artifacts", []):
    source = artifact["source"] if isinstance(artifact, dict) else artifact
    if isinstance(artifact, dict) and artifact.get("service") != "lab":
        errors.append(f"artifact {source} must come from service = \"lab\"")
    parent = str(Path(source).parent)
    if not any(path == parent or path.startswith(parent + "/") for path in created):
        errors.append(f"tests/Dockerfile must `mkdir -p {parent}` for artifact {source}")
for error in errors:
    print(f"FAIL {task}: {error}")
sys.exit(1 if errors else 0)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-separate-verifier"
exit $FAILED
