#!/usr/bin/env python3
"""Run agent × model presets on AS-Bench campaigns through Harbor.

  uv run python evals/run.py --suite sanity --env docker
  uv run python evals/run.py --preset claude-haiku --preset gpt-mini -k 3 --env modal
  uv run python evals/run.py --suite small --task 'biology/jewett-lab/*' --agent-timeout-multiplier 0.25
  uv run python evals/run.py --suite frontier --dry-run          # write config, print command

Presets and suites live in evals/agents.toml. Tasks default to every campaign
listed in tasks/dataset.toml; --task selects by glob over "<domain>/<source>/<slug>"
or the slug alone. Each run is one Harbor job, written to results/jobs/<job>/
(gitignored) with the exact job config saved to results/configs/<job>.json.
After the job, results/trials.csv and results/summary.md are regenerated.
Anything after `--` is passed to `harbor run` unchanged.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESETS_FILE = ROOT / "evals" / "agents.toml"
RESULTS = ROOT / "results"

# Credentials each vendor-CLI agent needs; any one variable in a group is enough.
AGENT_CREDENTIALS = {
    "claude-code": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
    "codex": ["OPENAI_API_KEY", "CODEX_AUTH_JSON_PATH"],
    "grok-build": ["XAI_API_KEY"],
    "gemini-cli": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
}
# LiteLLM-based agents (mini-swe-agent, terminus-2, ...) and grok-build with a
# provider prefix: the key follows the model's "<provider>/" prefix.
PROVIDER_CREDENTIALS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "zai": ["ZAI_API_KEY"],
    "moonshot": ["MOONSHOT_API_KEY"],
}
NO_MODEL_AGENTS = {"oracle", "nop"}
ENV_TEMPLATE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}")


@dataclass(frozen=True)
class Preset:
    name: str
    agent: str
    model: str | None
    kwargs: dict
    env: dict


@dataclass(frozen=True)
class Task:
    path: Path
    key: str   # <domain>/<source>/<slug>
    name: str  # [task].name, e.g. as-bench/<slug>


def load_presets(path: Path = PRESETS_FILE) -> tuple[dict[str, Preset], dict[str, list[str]]]:
    raw = tomllib.loads(path.read_text())
    presets = {
        name: Preset(name, spec["agent"], spec.get("model"), dict(spec.get("kwargs", {})),
                     {k: str(v) for k, v in spec.get("env", {}).items()})
        for name, spec in raw.get("presets", {}).items()
    }
    suites = {name: list(members) for name, members in raw.get("suites", {}).items()}
    for suite, members in suites.items():
        unknown = [m for m in members if m not in presets]
        if unknown:
            raise SystemExit(f"suite {suite} references unknown presets: {unknown}")
    return presets, suites


def discover_tasks(root: Path = ROOT, include_unlisted: bool = False) -> list[Task]:
    listed = {
        entry["name"]
        for entry in tomllib.loads((root / "tasks" / "dataset.toml").read_text()).get("tasks", [])
    }
    tasks = []
    for toml in sorted((root / "tasks").glob("*/*/*/task.toml")):
        name = tomllib.loads(toml.read_text())["task"]["name"]
        if include_unlisted or name in listed:
            tasks.append(Task(toml.parent, toml.parent.relative_to(root / "tasks").as_posix(), name))
    return tasks


def select_tasks(tasks: list[Task], patterns: list[str]) -> list[Task]:
    if not patterns:
        return tasks
    chosen = [
        task for task in tasks
        if any(fnmatch.fnmatch(task.key, p) or fnmatch.fnmatch(task.path.name, p) for p in patterns)
    ]
    if not chosen:
        raise SystemExit(f"no task matches {patterns}; known: {[t.key for t in tasks]}")
    return chosen


def select_presets(presets: dict[str, Preset], suites: dict[str, list[str]],
                   names: list[str], suite_names: list[str]) -> list[Preset]:
    wanted: list[str] = []
    for suite in suite_names:
        if suite not in suites:
            raise SystemExit(f"unknown suite {suite}; known: {sorted(suites)}")
        wanted.extend(suites[suite])
    wanted.extend(names)
    if not wanted:
        raise SystemExit("choose at least one --preset or --suite")
    unknown = [name for name in wanted if name not in presets]
    if unknown:
        raise SystemExit(f"unknown presets {unknown}; known: {sorted(presets)}")
    return [presets[name] for name in dict.fromkeys(wanted)]


def read_env_file(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path and path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if value:
                    values[key.strip().removeprefix("export ").strip()] = value
    return values


def required_credentials(preset: Preset) -> list[list[str]]:
    """Groups of host variables the preset needs; each group needs any one member.

    Every ``${VAR}`` referenced in the preset's env is required. The agent's
    default credential is required unless the preset's env already supplies it
    (e.g. a GLM preset setting ANTHROPIC_API_KEY = "${ZAI_API_KEY}").
    """
    if preset.agent in NO_MODEL_AGENTS:
        return []
    groups = [
        [match.group(1)]
        for value in preset.env.values()
        for match in ENV_TEMPLATE.finditer(value)
        if not match.group(2)
    ]
    provider = (preset.model or "").split("/", 1)[0] if "/" in (preset.model or "") else ""
    if preset.agent in AGENT_CREDENTIALS and not (
        preset.agent == "grok-build" and provider in PROVIDER_CREDENTIALS
    ):
        defaults = AGENT_CREDENTIALS[preset.agent]
    else:
        defaults = PROVIDER_CREDENTIALS.get(provider, [])
    if defaults and not any(key in preset.env for key in defaults):
        groups.append(defaults)
    return groups


def preflight(presets: list[Preset], env_backend: str, environ: dict[str, str]) -> list[str]:
    """Return human-readable problems that would make the job fail immediately."""
    problems = []
    for preset in presets:
        for options in required_credentials(preset):
            if not any(environ.get(key) for key in options):
                problems.append(f"{preset.name} ({preset.agent}) needs one of: {', '.join(options)}")
        if preset.agent not in NO_MODEL_AGENTS and not preset.model:
            problems.append(f"{preset.name} has no model")
        literal = [k for k, v in preset.env.items()
                   if re.search(r"(_KEY|_TOKEN|_SECRET|PASSWORD)$", k) and v and not ENV_TEMPLATE.fullmatch(v)]
        if literal:
            problems.append(f"{preset.name}: put secrets in .env and reference them as ${{VAR}}: {literal}")
    if env_backend == "docker" and shutil.which("docker") is None:
        problems.append("--env docker needs Docker installed (or use --env modal / --env daytona)")
    if env_backend == "modal" and not (
        (environ.get("MODAL_TOKEN_ID") and environ.get("MODAL_TOKEN_SECRET"))
        or (Path.home() / ".modal.toml").exists()
    ):
        problems.append("--env modal needs MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or `modal token new`")
    if env_backend == "daytona" and not environ.get("DAYTONA_API_KEY"):
        problems.append("--env daytona needs DAYTONA_API_KEY")
    return problems


def build_job_config(job_name: str, presets: list[Preset], tasks: list[Task], *, attempts: int,
                     env_backend: str, concurrency: int, jobs_dir: Path,
                     agent_timeout_multiplier: float | None) -> dict:
    agents = []
    for preset in presets:
        agent = {"name": preset.agent}
        if preset.model:
            agent["model_name"] = preset.model
        if preset.kwargs:
            agent["kwargs"] = preset.kwargs
        if preset.env:
            agent["env"] = preset.env
        agents.append(agent)
    config = {
        "job_name": job_name,
        "jobs_dir": str(jobs_dir),
        "n_attempts": attempts,
        "n_concurrent_trials": concurrency,
        "environment": {"type": env_backend},
        "agents": agents,
        "tasks": [{"path": str(task.path)} for task in tasks],
    }
    if agent_timeout_multiplier is not None:
        config["agent_timeout_multiplier"] = agent_timeout_multiplier
    return config


def default_job_name(presets: list[Preset], suites: list[str]) -> str:
    label = "+".join(suites) if suites and len(presets) > 2 else "+".join(p.name for p in presets)
    label = re.sub(r"[^A-Za-z0-9+_.-]", "-", label)[:60]
    return f"{datetime.now():%Y%m%d-%H%M%S}__{label}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        index = argv.index("--")
        argv, passthrough = argv[:index], argv[index + 1:]
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", action="append", default=[], help="preset name (repeatable)")
    parser.add_argument("--suite", action="append", default=[], help="suite name (repeatable)")
    parser.add_argument("--task", action="append", default=[], help="task glob (repeatable)")
    parser.add_argument("--include-unlisted", action="store_true",
                        help="also consider tasks missing from tasks/dataset.toml")
    parser.add_argument("-k", "--attempts", type=int, default=1, help="attempts per task × preset")
    parser.add_argument("--env", default="docker", help="Harbor environment: docker, modal, daytona, ...")
    parser.add_argument("-n", "--concurrency", type=int, default=None,
                        help="concurrent trials (default: 2 for docker, all trials otherwise)")
    parser.add_argument("--agent-timeout-multiplier", type=float, default=None,
                        help="scale each task's agent time limit (e.g. 0.25 for quick smoke runs)")
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--results-dir", type=Path, default=RESULTS)
    parser.add_argument("--dry-run", action="store_true", help="write the config and print the command only")
    parser.add_argument("--list", action="store_true", help="list presets, suites, and tasks")
    args = parser.parse_args(argv)

    presets, suites = load_presets()
    tasks = select_tasks(discover_tasks(include_unlisted=args.include_unlisted), args.task)
    if args.list:
        print("presets:")
        for preset in presets.values():
            print(f"  {preset.name:24} {preset.agent:12} {preset.model or ''}")
        print("suites:")
        for name, members in suites.items():
            print(f"  {name:24} {', '.join(members)}")
        print("tasks:")
        for task in tasks:
            print(f"  {task.key}")
        return 0

    chosen = select_presets(presets, suites, args.preset, args.suite)
    environ = {**read_env_file(args.env_file), **os.environ}
    problems = preflight(chosen, args.env, environ)
    base_url = environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if base_url and base_url != "https://api.anthropic.com" and any(
        p.agent == "claude-code" and "ANTHROPIC_BASE_URL" not in p.env for p in chosen
    ):
        print(f"note: ANTHROPIC_BASE_URL={base_url} will route Claude presets through that endpoint")
    if problems and not args.dry_run:
        print("cannot start:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2

    results = args.results_dir.resolve()
    job_name = args.job_name or default_job_name(chosen, args.suite)
    total = len(tasks) * len(chosen) * args.attempts
    concurrency = args.concurrency or (min(total, 2) if args.env == "docker" else total)
    config = build_job_config(
        job_name, chosen, tasks, attempts=args.attempts, env_backend=args.env,
        concurrency=concurrency, jobs_dir=results / "jobs",
        agent_timeout_multiplier=args.agent_timeout_multiplier)
    config_path = results / "configs" / f"{job_name}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    harbor = shutil.which("harbor") or "harbor"
    command = [harbor, "run", "-c", str(config_path)]
    if args.env_file.exists():
        command += ["--env-file", str(args.env_file)]
    command += passthrough
    print(f"job {job_name}: {len(tasks)} task(s) × {len(chosen)} preset(s) × {args.attempts} "
          f"attempt(s) = {total} trial(s) on {args.env}")
    print(f"config: {config_path.relative_to(ROOT) if config_path.is_relative_to(ROOT) else config_path}")
    print("command: " + " ".join(command), flush=True)
    if args.dry_run:
        for problem in problems:
            print(f"warning: {problem}")
        return 0
    if shutil.which("harbor") is None:
        print("harbor not found: `uv sync --group eval` or `uv tool install 'harbor[modal,daytona]==0.21.0'`",
              file=sys.stderr)
        return 2
    status = subprocess.run(command, cwd=ROOT).returncode

    from summarize import main as summarize  # evals/ is on sys.path when run as a script
    summarize(["--results-dir", str(results)])
    return status


if __name__ == "__main__":
    sys.exit(main())
