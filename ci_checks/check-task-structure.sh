#!/bin/bash
# Every campaign task has the files an AS-Bench campaign needs.
source "$(dirname "$0")/lib.sh"
REQUIRED="task.toml instruction.md README.md LICENSE.md
environment/Dockerfile environment/.dockerignore environment/docker-compose.yaml
environment/lab/Dockerfile environment/lab/campaign/campaign.json
solution/solve.sh tests/Dockerfile tests/test.sh tests/test_outputs.py"
FAILED=0
for task in $(task_dirs "$@"); do
    for file in $REQUIRED; do
        [ -f "$task/$file" ] || { echo "FAIL $task: missing $file"; FAILED=1; }
    done
    [ -x "$task/solution/solve.sh" ] || { echo "FAIL $task: solution/solve.sh not executable"; FAILED=1; }
    [ -x "$task/tests/test.sh" ] || { echo "FAIL $task: tests/test.sh not executable"; FAILED=1; }
done
[ $FAILED -eq 0 ] && echo "PASS check-task-structure"
exit $FAILED
