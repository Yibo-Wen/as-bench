#!/bin/bash
# Authored text files carry the canary. README.md carries it on line 1.
# Vendored and generated files (a "do not edit" header in the first 5 lines)
# and data formats without comment syntax (csv, json, pdb, a2m, fasta) are exempt.
source "$(dirname "$0")/lib.sh"
EXTENSIONS="sh|py|js|ts|md|toml|yml|yaml|txt|cfg|ini"
FAILED=0
check_file() {
    local f="$1" base ext
    base=$(basename "$f")
    if [ "$base" != "Dockerfile" ]; then
        ext="${base##*.}"
        echo "$ext" | grep -qE "^($EXTENSIONS)$" || return 0
    fi
    [ "$base" = "docker-compose.yaml" ] && return 0
    head -5 "$f" | grep -qi 'do not edit' && return 0
    grep -q "$CANARY_GUID" "$f" || { echo "FAIL $f: missing canary"; FAILED=1; }
}
for template in task-template.toml task-readme-template.md; do
    [ -f "$template" ] && check_file "$template"
done
for task in $(task_dirs "$@"); do
    for f in "$task/instruction.md" "$task/task.toml"; do check_file "$f"; done
    head -1 "$task/README.md" | grep -q "$CANARY_GUID" || { echo "FAIL $task/README.md: canary must be on line 1"; FAILED=1; }
    while IFS= read -r f; do check_file "$f"; done < <(
        find "$task/environment" "$task/solution" "$task/tests" "$task/authoring" -type f \
            -not -path '*/data/*' -not -path '*/campaign/*' -not -path '*/__pycache__/*' 2>/dev/null | sort)
done
[ $FAILED -eq 0 ] && echo "PASS check-canary"
exit $FAILED
