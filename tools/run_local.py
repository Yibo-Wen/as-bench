#!/usr/bin/env python3
"""Run a campaign end to end without Docker: lab on localhost, agent, verifier.

  uv run python tools/run_local.py tasks/biology/jewett-lab/biosensor-active-learning --agent oracle
  uv run python tools/run_local.py tasks/biology/jewett-lab/biosensor-active-learning --agent nop --expect 0

This mirrors a Harbor trial: the lab sidecar starts from the task's campaign
files, the agent works in a scratch copy of /app with `asb` on PATH, only the
declared lab artifacts reach the verifier, and the verifier writes reward,
metrics, and CTRF. It is a development aid, not a substitute for
`harbor run` (no container isolation; the model sandbox runs unprivileged).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_healthy(url: str, lab: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lab.poll() is not None:
            raise SystemExit(f"lab exited early:\n{lab.stderr.read().decode()}")
        try:
            urllib.request.urlopen(f"{url}/health", timeout=1).read()
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("lab did not become healthy")


def stage_app(task: Path, app: Path) -> None:
    environment = task / "environment"
    shutil.copytree(environment / "data", app / "data")
    for name in ("MODEL_FORMAT.md", "model_template.js"):
        if (environment / name).exists():
            shutil.copy(environment / name, app / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task", type=Path)
    parser.add_argument("--agent", choices=["oracle", "nop"], default="oracle")
    parser.add_argument("--expect", type=int, choices=[0, 1],
                        help="exit non-zero unless the reward equals this value")
    parser.add_argument("--workdir", type=Path, help="keep trial files here (default: temp dir)")
    args = parser.parse_args()

    task = args.task.resolve()
    config = tomllib.loads((task / "task.toml").read_text())
    work = (args.workdir or Path(tempfile.mkdtemp(prefix="asb-trial-"))).resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"{work} is not empty")
    app, state, verifier_state = work / "app", work / "lab-state", work / "verifier-state"
    logs, bin_dir, tmp = work / "logs" / "verifier", work / "bin", work / "tmp"
    for directory in (app, state, verifier_state, logs, bin_dir, tmp):
        directory.mkdir(parents=True, exist_ok=True)
    stage_app(task, app)

    asb = bin_dir / "asb"
    asb.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{task}/environment/asb/asb.py" "$@"\n')
    asb.chmod(0o755)
    path = os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]])
    if shutil.which("deno", path=path) is None:
        raise SystemExit("deno not found; run through `uv run` so the dev env's deno is on PATH")

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    lab_dir = task / "environment" / "lab"
    lab = subprocess.Popen(
        [sys.executable, "-m", "asb_lab.server", "--campaign", str(lab_dir / "campaign" / "campaign.json"),
         "--state-dir", str(state), "--host", "127.0.0.1", "--port", str(port)],
        cwd=lab_dir, stderr=subprocess.PIPE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        wait_healthy(url, lab)
        print(f"[run_local] lab up at {url}; agent={args.agent}; workdir={work}", flush=True)
        agent_env = {**os.environ, "PATH": path, "ASB_LAB_URL": url, "ASB_APP_DIR": str(app),
                     "PYTHONDONTWRITEBYTECODE": "1",
                     "TMPDIR": str(tmp)}
        if args.agent == "oracle":
            started = time.monotonic()
            subprocess.run(["bash", str(task / "solution" / "solve.sh")], cwd=app, env=agent_env, check=True)
            print(f"[run_local] oracle finished in {time.monotonic() - started:.0f}s")
    finally:
        lab.terminate()
        lab.wait(timeout=10)

    # Only declared lab artifacts reach the verifier, as in Harbor's separate mode.
    for artifact in config.get("artifacts", []):
        source = artifact["source"] if isinstance(artifact, dict) else artifact
        if not source.startswith("/state/"):
            raise SystemExit(f"run_local only maps /state artifacts, got {source}")
        relative = Path(source).relative_to("/state")
        if (state / relative).is_file():
            (verifier_state / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(state / relative, verifier_state / relative)

    verifier_env = {**os.environ, "PATH": path, "TMPDIR": str(tmp), "ASB_VERIFY_LOCAL": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "ASB_VERIFY_STATE_DIR": str(verifier_state), "ASB_VERIFY_LOGS_DIR": str(logs)}
    subprocess.run([sys.executable, str(task / "tests" / "test_outputs.py")],
                   env=verifier_env, check=False, stdout=subprocess.DEVNULL)
    reward_path = logs / "reward.txt"
    reward = int(reward_path.read_text().strip()) if reward_path.exists() else 0
    metrics = json.loads((logs / "metrics.json").read_text()) if (logs / "metrics.json").exists() else {}
    print(json.dumps({"reward": reward, "metrics": metrics}, indent=2))
    if args.expect is not None and reward != args.expect:
        print(f"[run_local] FAIL: expected reward {args.expect}, got {reward}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
