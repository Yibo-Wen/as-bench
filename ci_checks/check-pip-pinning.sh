#!/bin/bash
# pip installs in task Dockerfiles pin exact versions; base images pin digests.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    while IFS= read -r dockerfile; do
        python3 - "$dockerfile" <<'PY' || FAILED=1
import re, shlex, sys
path = sys.argv[1]
text = re.sub(r"\\\n", " ", open(path).read())
errors = []
for line in text.splitlines():
    stripped = line.strip()
    if stripped.upper().startswith("FROM ") and "@sha256:" not in stripped:
        errors.append(f"base image not pinned by digest: {stripped}")
    for command in re.split(r"&&|;", line):
        tokens = shlex.split(command, posix=True) if "pip" in command else []
        if "install" not in tokens or not any(t.endswith("pip") or t == "pip" for t in tokens):
            continue
        skip = False
        for token in tokens[tokens.index("install") + 1:]:
            if skip:
                skip = False
                continue
            if token in ("--index-url", "--extra-index-url", "-i", "--constraint", "-c",
                         "--requirement", "-r", "--find-links", "-f"):
                skip = True
                continue
            if token.startswith("-") or token.startswith("/") or token == ".":
                continue
            if "==" not in token:
                errors.append(f"unpinned pip package: {token}")
for error in errors:
    print(f"FAIL {path}: {error}")
sys.exit(1 if errors else 0)
PY
    done < <(find "$task" -name Dockerfile | sort)
done
[ $FAILED -eq 0 ] && echo "PASS check-pip-pinning"
exit $FAILED
