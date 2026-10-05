#!/bin/bash
# No file the agent image receives duplicates the lab's private replay data or
# the verifier's hidden truth, and no query or evaluation ID has its value
# visible in public data.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import hashlib, json, sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
env = task / "environment"
# Files a task declares as intentionally public on both sides (e.g. a target pattern
# the verifier also needs, or a public simulator the lab also runs).
public_copies = {task / p for p in tomllib.loads((task / "task.toml").read_text())
                 .get("metadata", {}).get("public_copies", [])}
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
private = [p for p in (env / "lab/campaign").rglob("*") if p.is_file() and "catalog" not in p.parts
           and p.name != "campaign.json"] + [p for p in (task / "tests/data").rglob("*") if p.is_file()]
private = [p for p in private if p not in public_copies]
public = [p for p in env.rglob("*") if p.is_file() and "lab" not in p.relative_to(env).parts]
private_digests = {digest(p): p for p in private}
errors = [f"{p.relative_to(task)} duplicates {private_digests[digest(p)].relative_to(task)}"
          for p in public if digest(p) in private_digests]
table = env / "lab/campaign/replay"
secret_values = set()
for path in table.glob("*.json"):
    for design, row in json.loads(path.read_text())["values"].items():
        secret_values.update(f"{design},{format(v, '.15g')}" for v in row.values())
for p in public:
    if p.suffix == ".csv":
        text = p.read_text()
        leaked = [s for s in secret_values if s in text]
        if leaked:
            errors.append(f"{p.relative_to(task)} exposes replay values, e.g. {leaked[0]}")
for error in errors:
    print(f"FAIL {task}: {error}")
sys.exit(1 if errors else 0)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-answer-isolation"
exit $FAILED
