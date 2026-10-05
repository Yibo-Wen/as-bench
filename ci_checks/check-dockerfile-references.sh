#!/bin/bash
# Agent and lab images never copy solution/ or tests/, and the agent image's
# build context excludes the lab's private campaign files.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    for dockerfile in "$task/environment/Dockerfile" "$task/environment/lab/Dockerfile"; do
        if grep -nE '^\s*(COPY|ADD)\b.*\b(solution|tests)/' "$dockerfile"; then
            echo "FAIL $dockerfile: must not copy solution/ or tests/"; FAILED=1
        fi
    done
    grep -qxE 'lab/?' "$task/environment/.dockerignore" || {
        echo "FAIL $task/environment/.dockerignore: must exclude lab/"; FAILED=1; }
    if grep -nE '^\s*(COPY|ADD)\b.*\blab/' "$task/environment/Dockerfile"; then
        echo "FAIL $task/environment/Dockerfile: must not copy lab/"; FAILED=1
    fi
done
[ $FAILED -eq 0 ] && echo "PASS check-dockerfile-references"
exit $FAILED
