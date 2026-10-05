#!/bin/bash
# Tasks live at tasks/<domain>/<source>/<campaign>; campaign slug is kebab-case, at most 3 tokens.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    IFS=/ read -r root domain group slug extra <<< "$task"
    if [ "$root" != "tasks" ] || [ -z "$slug" ] || [ -n "$extra" ]; then
        echo "FAIL $task: expected tasks/<domain>/<source>/<campaign>"; FAILED=1; continue
    fi
    echo " $DOMAINS " | grep -q " $domain " || { echo "FAIL $task: domain '$domain' not in: $DOMAINS"; FAILED=1; }
    echo "$group" | grep -qE '^[a-z0-9]+(-[a-z0-9]+)*$' || { echo "FAIL $task: source '$group' is not kebab-case"; FAILED=1; }
    if ! echo "$slug" | grep -qE '^[a-z0-9]+(-[a-z0-9]+){0,2}$'; then
        echo "FAIL $task: slug '$slug' must be kebab-case with at most 3 tokens"; FAILED=1
    fi
done
[ $FAILED -eq 0 ] && echo "PASS check-task-path"
exit $FAILED
