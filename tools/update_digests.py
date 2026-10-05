#!/usr/bin/env python3
"""Refresh the task digests in tasks/dataset.toml with Harbor's content hash.

  uv run --group eval python tools/update_digests.py          # rewrite digests
  uv run --group eval python tools/update_digests.py --check  # fail if any is stale

`harbor sync` cannot do this for nested task trees (it only inspects immediate
children of tasks/), so this mirrors the snippet documented in
terminal-bench-science's tasks/dataset.toml.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

from harbor.publisher.packager import Packager

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tasks" / "dataset.toml"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    by_name = {
        tomllib.loads(toml.read_text())["task"]["name"]: toml.parent
        for toml in (ROOT / "tasks").glob("*/*/*/task.toml")
    }
    lines = MANIFEST.read_text().split("\n")
    output, stale, current = [], [], None
    for line in lines:
        name = re.match(r'^name = "([^"]+)"$', line)
        if name and name.group(1) in by_name:
            current = name.group(1)
            output.append(line)
            continue
        if current and re.match(r'^#?\s*digest = ', line):
            for cache in by_name[current].rglob("__pycache__"):
                raise SystemExit(f"remove {cache} before hashing")
            digest = f'digest = "sha256:{Packager.compute_content_hash(by_name[current])[0]}"'
            if line != digest:
                stale.append(current)
            output.append(digest)
            current = None
            continue
        output.append(line)
    if args.check:
        for name in stale:
            print(f"FAIL stale digest: {name}")
        return 1 if stale else 0
    MANIFEST.write_text("\n".join(output))
    print(f"updated {len(stale)} digest(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
