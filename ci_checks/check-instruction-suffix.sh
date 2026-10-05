#!/bin/bash
# instruction.md ends with the time-limit sentence naming [agent].timeout_sec.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
seconds = int(tomllib.loads((task / "task.toml").read_text())["agent"]["timeout_sec"])
expected = (f"You have {seconds} seconds to complete this task. "
            "Do not cheat by using online solutions or hints specific to this task.")
last = (task / "instruction.md").read_text().strip().split("\n\n")[-1].strip()
if last != expected:
    print(f"FAIL {task}: instruction.md must end with: {expected}")
    sys.exit(1)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-instruction-suffix"
exit $FAILED
