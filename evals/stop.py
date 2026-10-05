#!/usr/bin/env python3
"""Stop a running evaluation job and remove its trial containers.

  uv run python evals/stop.py                     # every job with live containers
  uv run python evals/stop.py 20260930-first-pass  # one job

Killing `harbor run` alone can leave each trial's docker-compose project (agent +
lab containers) running, and an agent inside keeps calling its model API. This
interrupts Harbor for the job, then tears down every compose project whose name
matches one of the job's trials. Finished trial results are kept.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "results" / "jobs"


def harbor_pids(job: str | None) -> list[int]:
    listing = subprocess.run(["pgrep", "-af", "harbor run"], capture_output=True, text=True).stdout
    pids = []
    for line in listing.splitlines():
        pid, _, command = line.partition(" ")
        if "pgrep" not in command and (job is None or f"{job}.json" in command):
            pids.append(int(pid))
    return pids


def trial_projects(job: str | None) -> list[str]:
    labels = subprocess.run(
        ["docker", "ps", "-a", "--format", '{{.Label "com.docker.compose.project"}}'],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    jobs = [JOBS / job] if job else [p for p in JOBS.glob("*") if p.is_dir()]
    trials = {t.name.lower() for j in jobs for t in j.glob("*") if t.is_dir()}
    return sorted({p for p in labels if p.split("__env")[0] in trials})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job", nargs="?", help="job name under results/jobs (default: all)")
    args = parser.parse_args()

    pids = harbor_pids(args.job)
    for pid in pids:
        subprocess.run(["kill", f"-{signal.SIGINT.value}", str(pid)], check=False)
    deadline = time.monotonic() + 30
    while pids and time.monotonic() < deadline and harbor_pids(args.job):
        time.sleep(1)
    for pid in harbor_pids(args.job):
        subprocess.run(["kill", str(pid)], check=False)
    print(f"stopped {len(pids)} harbor process(es)")

    projects = trial_projects(args.job)
    for project in projects:
        subprocess.run(["docker", "compose", "-p", project, "down", "-v", "--remove-orphans", "-t", "5"],
                       capture_output=True, check=False)
        print(f"removed {project}")
    print(f"removed {len(projects)} trial environment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
