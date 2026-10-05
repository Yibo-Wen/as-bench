#!/bin/bash
# The path, [metadata], campaign.json, and the source card agree, and the card
# enables the campaign's backend. A source (sources/<id>/source.toml,
# [metadata] source) is the group whose published data backs the campaign.
source "$(dirname "$0")/lib.sh"
FAILED=0
for task in $(task_dirs "$@"); do
    python3 - "$task" <<'PY' || FAILED=1
import json, sys, tomllib
from pathlib import Path
task = Path(sys.argv[1])
_, domain, group, slug = task.parts[-4:]
m = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
errors = []
if m.get("domain") != domain:
    errors.append(f"[metadata].domain '{m.get('domain')}' != path domain '{domain}'")
if m.get("source") != group:
    errors.append(f"[metadata].source '{m.get('source')}' != path group '{group}'")
card_path = Path("sources") / group / "source.toml"
if not card_path.exists():
    errors.append(f"{card_path} does not exist")
else:
    card = tomllib.loads(card_path.read_text())
    if card.get("id") != group:
        errors.append(f"{card_path}: id must be '{group}'")
    if card.get("domain") != domain:
        errors.append(f"{card_path}: domain '{card.get('domain')}' != '{domain}'")
    if not card.get("backends", {}).get(m.get("backend")):
        errors.append(f"source '{group}' does not support backend '{m.get('backend')}'")
    if not any(d.get("doi") and d.get("license") for d in card.get("datasets", [])):
        errors.append(f"{card_path}: needs a [[datasets]] entry with doi and license")
campaign = json.loads((task / "environment/lab/campaign/campaign.json").read_text())
if campaign.get("source") != group or campaign.get("campaign_id") != f"{group}/{slug}":
    errors.append(f"campaign.json source/campaign_id must be '{group}' / '{group}/{slug}'")
if campaign.get("backend", {}).get("kind") != m.get("backend"):
    errors.append("campaign.json backend.kind differs from [metadata].backend")
for error in errors:
    print(f"FAIL {task}: {error}")
sys.exit(1 if errors else 0)
PY
done
[ $FAILED -eq 0 ] && echo "PASS check-source-registry"
exit $FAILED
