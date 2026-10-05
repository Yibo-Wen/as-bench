#!/bin/bash
# Agent phase has public network; verifier has none; the lab sits only on an
# internal network so it can never reach (or be reached from) the internet.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import re, sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
config = tomllib.loads((task / "task.toml").read_text())
compose = (task / "environment/docker-compose.yaml").read_text()
errors = []
if config.get("environment", {}).get("network_mode") != "public":
    errors.append('[environment].network_mode must be "public"')
if config.get("verifier", {}).get("environment", {}).get("network_mode") != "no-network":
    errors.append('[verifier.environment].network_mode must be "no-network"')
lab = re.search(r"^  lab:\n((?:    .*\n|\s*\n)+)", compose, re.M)
if not lab:
    errors.append("docker-compose.yaml must define a lab service")
else:
    networks = re.search(r"^    networks:\s*\[([^\]]*)\]", lab.group(1), re.M)
    if not networks or [n.strip() for n in networks.group(1).split(",")] != ["task"]:
        errors.append("lab service must be on networks: [task] only")
if not re.search(r"^  task:\n    internal: true", compose, re.M):
    errors.append("the task network must be internal: true")
if re.search(r"^\s*-\s*[./~][^:\n]*:[^\n]*$", compose, re.M):
    errors.append("host bind mounts are not allowed; use named volumes")
for error in errors:
    print(f"FAIL {task}: {error}")
sys.exit(1 if errors else 0)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-network-policy"
exit $FAILED
