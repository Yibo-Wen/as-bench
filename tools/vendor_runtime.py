#!/usr/bin/env python3
"""Copy the shared AS-Bench runtime into every campaign task.

Harbor publishes each task directory on its own, so a task cannot import code
from outside itself. ``runtime/`` is the single source of truth; this tool
writes byte-stable copies (with a do-not-edit header) into each task that sets
``[metadata] lab_api_version``. ``[metadata] asb_components`` picks which parts a
task needs (default: all of lab, client, verify, js-sandbox).

  python tools/vendor_runtime.py            # write copies
  python tools/vendor_runtime.py --check    # fail if any copy is stale (CI)
  python tools/vendor_runtime.py TASK_DIR…  # limit to specific tasks
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"

# (component, source under runtime/, destination under the task directory)
MAPPING = [
    ("lab", "asb_lab", "environment/lab/asb_lab"),
    ("client", "asb_client/asb.py", "environment/asb/asb.py"),
    ("js-sandbox", "sandbox/js_runner.ts", "environment/asb/js_runner.ts"),
    ("verify", "asb_verify", "tests/asb_verify"),
    ("js-sandbox", "sandbox/js_runner.ts", "tests/js_runner.ts"),
]
COMPONENTS = {component for component, _, _ in MAPPING}


def components(task: Path) -> set[str]:
    metadata = tomllib.loads((task / "task.toml").read_text()).get("metadata", {})
    chosen = set(metadata.get("asb_components", COMPONENTS))
    unknown = chosen - COMPONENTS
    if unknown:
        raise SystemExit(f"{task}: unknown asb_components {sorted(unknown)}")
    return chosen
SUFFIXES = {".py": "#", ".ts": "//"}


def with_header(source: Path) -> str:
    text = source.read_text()
    marker = SUFFIXES[source.suffix]
    header = (f"{marker} Vendored from runtime/{source.relative_to(RUNTIME)} by "
              f"tools/vendor_runtime.py. Do not edit; change the source and re-run.\n")
    if text.startswith("#!"):
        shebang, _, rest = text.partition("\n")
        return f"{shebang}\n{header}{rest}"
    return header + text


def expected_files(task: Path) -> dict[Path, str]:
    files: dict[Path, str] = {}
    wanted = components(task)
    for component, source_rel, dest_rel in MAPPING:
        if component not in wanted:
            continue
        source = RUNTIME / source_rel
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file() and path.suffix in SUFFIXES and "__pycache__" not in path.parts:
                    files[task / dest_rel / path.relative_to(source)] = with_header(path)
        else:
            files[task / dest_rel] = with_header(source)
    return files


def vendored_roots(task: Path) -> list[Path]:
    return [task / dest for _, source, dest in MAPPING if (RUNTIME / source).is_dir()]


def campaign_tasks() -> list[Path]:
    tasks = []
    for toml in sorted((ROOT / "tasks").rglob("task.toml")):
        metadata = tomllib.loads(toml.read_text()).get("metadata", {})
        if "lab_api_version" in metadata:
            tasks.append(toml.parent)
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tasks", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tasks = [task.resolve() for task in args.tasks] or campaign_tasks()
    stale: list[str] = []
    for task in tasks:
        expected = expected_files(task)
        present = {
            path for root in vendored_roots(task) if root.exists()
            for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts
        }
        for path in sorted(present - set(expected)):
            stale.append(f"unexpected {path.relative_to(ROOT)}")
            if not args.check:
                path.unlink()
        for path, text in expected.items():
            if path.exists() and path.read_text() == text:
                continue
            stale.append(f"{'missing' if not path.exists() else 'outdated'} {path.relative_to(ROOT)}")
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    if args.check:
        for line in stale:
            print(f"FAIL {line}")
        if stale:
            print("Run: python tools/vendor_runtime.py")
            return 1
        print(f"runtime copies up to date in {len(tasks)} task(s)")
        return 0
    print(f"updated {len(stale)} file(s) across {len(tasks)} task(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
