#!/bin/bash
# The reward is exactly 1 or 0: test.sh falls back to 0, and test_outputs.py
# writes the reward only through asb_verify.report.run_verifier.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    grep -q "printf '0\\\\n' > /logs/verifier/reward.txt" "$task/tests/test.sh" || {
        echo "FAIL $task/tests/test.sh: must fall back to reward 0 on exit"; FAILED=1; }
    grep -q "run_verifier(" "$task/tests/test_outputs.py" || {
        echo "FAIL $task/tests/test_outputs.py: must report through asb_verify.report.run_verifier"; FAILED=1; }
    if grep -n "reward" "$task/tests/test_outputs.py" | grep -v "^\s*#" | grep -q "write"; then
        echo "FAIL $task/tests/test_outputs.py: must not write the reward directly"; FAILED=1
    fi
done
[ $FAILED -eq 0 ] && echo "PASS check-binary-reward"
exit $FAILED
