#!/usr/bin/env python3
"""Summarize Harbor job results into results/trials.csv and results/summary.md.

  uv run python evals/summarize.py                     # every job under results/jobs
  uv run python evals/summarize.py --job '2026*small*'  # only matching jobs

Reads each trial's result.json (agent, model, reward, tokens, cost, timing,
exception) and verifier/metrics.json (continuous task metrics and campaign
progress written by asb_verify). trials.csv keeps every metric and the reward
Harbor recorded. summary.md reports only each task's gated metrics, read from
the GATES of its tests/test_outputs.py, so new campaigns need no changes here.

summary.md grades each trial against those same GATES rather than against its
recorded reward, so trials scored before a gate was retuned stay comparable with
later ones. A trial missing a gated metric, having delivered nothing, keeps its
recorded reward.

Trials recorded before the PCL Bench -> Autonomous Science Bench rename carry task names like
"pcl-bench/<slug>"; they are merged under "as-bench/<slug>".

Trials that were stopped (unfinished or cancelled) are ignored. Trials whose
agent errored before making any billed model call and before completing any
stage (an out-of-credit key, a failed agent install) are infrastructure errors:
counted in the "infra" column and left out of pass rates and metrics.
"""

from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COLUMNS = [
    "job", "trial", "task", "agent", "model", "effort", "reward", "exception", "infra_error",
    "verifier_error", "refused", "refusal_soft", "refusal_category", "refusal_models",
    "model_requested", "answered_by",
    "agent_minutes", "cost_usd", "input_tokens", "output_tokens", "cache_tokens",
]
#: Where task directories live: the dataset, then retired and withheld campaigns.
TASK_ROOTS = [ROOT / "tasks", ROOT / "archive", ROOT / "withheld"]
#: Task-name prefixes from before the rename, all reported as Autonomous Science Bench.
OLD_PREFIXES = ("pcl-bench/",)
CANCELLED = {"CancelledError", "KeyboardInterrupt"}
TIMEOUTS = {"AgentTimeoutError"}
NO_MODEL_AGENTS = {"oracle", "nop"}

#: A policy refusal, which is not a failure and not a capability result. The API answers
#: 200 with stop_reason "refusal" and a category rather than a 4xx, so a harness that
#: only looks at the exception type records it as a crash. Counting it as a failed trial
#: reads a policy decision as "the model could not do this", which is wrong in both
#: directions: it understates the model and it inflates the apparent difficulty of the
#: task.
#:
#: A refusal has two outcomes and they must not be merged. A **hard** refusal ends the
#: run: the requested model declines and either no fallback is offered or the fallback
#: target declines as well. A **soft** refusal is retried on another model server-side,
#: which then does the work -- observed on three runs here, where Opus 5.5 declined and
#: Opus 4.8 produced 128 to 168 turns of it. Scoring a soft refusal as a refusal throws
#: away a completed trial; scoring it as the requested model's result credits the wrong
#: model. So the pass rate keeps soft refusals and drops hard ones, and `refusal_models`
#: records who actually answered.
REFUSAL_CATEGORY = re.compile(r'"api_refusal_category"\s*:\s*"([a-z_]+)"')
REFUSAL_STOP = re.compile(r'"stop_details"\s*:\s*\{"type"\s*:\s*"refusal"\s*,'
                          r'\s*"category"\s*:\s*"([a-z_]+)"')
#: Which model produced each refusal. A refusal may be followed by a server-side
#: fallback to a different model, which can also refuse, so the requested model is not
#: enough to say what was measured.
REFUSAL_MODEL = re.compile(r'"original_model"\s*:\s*"([^"]+)"[^}]*?'
                           r'"api_refusal_category"\s*:\s*"[a-z_]+"')
#: A refusal the run did not come back from: no fallback offered, or the provider's own
#: hard-stop wording. `model_refusal_fallback` is deliberately absent -- that one is
#: retried and the run continues.
HARD_REFUSAL = re.compile(r'model_refusal_no_fallback|violate our Usage Policy|'
                          r'Start a new session to continue')
#: Which model actually produced each assistant turn, so a soft refusal's result is
#: credited to the model that answered rather than to the one that declined.
WORKER_MODEL = re.compile(r'"type"\s*:\s*"assistant".{0,400}?"model"\s*:\s*"'
                          r'(claude-[a-z0-9.-]+)"')


def refusal_of(trial_dir: Path) -> tuple[str, str, bool, str]:
    """What a trial's agent logs say about refusals.

    Returns (categories, refusing models, hard, models that did the work). ``hard`` is
    true when the run ended on the refusal rather than being retried on another model.
    Reads the agent's own transcript rather than the result's exception, because the
    exception only says the command exited non-zero.
    """
    categories: list[str] = []
    models: list[str] = []
    workers: dict[str, int] = {}
    hard = False
    agent_dir = trial_dir / "agent"
    if not agent_dir.is_dir():
        return "", "", False, ""
    for path in sorted(agent_dir.glob("*.txt")) + sorted(agent_dir.glob("*.json")):
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in REFUSAL_CATEGORY.finditer(text):
            categories.append(match.group(1))
        for match in REFUSAL_STOP.finditer(text):
            categories.append(match.group(1))
        models.extend(match.group(1) for match in REFUSAL_MODEL.finditer(text))
        if HARD_REFUSAL.search(text):
            hard = True
        for match in WORKER_MODEL.finditer(text):
            workers[match.group(1)] = workers.get(match.group(1), 0) + 1
    answered = "|".join(name for name, _ in
                        sorted(workers.items(), key=lambda kv: -kv[1]))
    if not categories and not hard:
        return "", "", False, answered
    return ("|".join(sorted(set(categories))), "|".join(dict.fromkeys(models)),
            hard, answered)


def is_infra_error(row: dict) -> bool:
    """The agent crashed before doing any work: no billed usage, no stage completed."""
    return bool(
        row["exception"]
        and row["exception"] not in TIMEOUTS
        and row["agent"] not in NO_MODEL_AGENTS
        and not row.get("input_tokens")
        and not row.get("cost_usd")
        and not row.get("campaign.stages_completed")
    )


def task_name(name: str) -> str:
    """One name per task: "pcl-bench/<slug>" from before the rename becomes "as-bench/<slug>"."""
    for prefix in OLD_PREFIXES:
        if name.startswith(prefix):
            return "as-bench/" + name[len(prefix):]
    return name


def task_gates(task: str, roots: list[Path] = TASK_ROOTS) -> dict[str, tuple[str, float]] | None:
    """The task's gates as {metric: (comparison, threshold)}, from GATES in tests/test_outputs.py.

    A bare number is a lower bound; {"min": x} and {"max": x} are explicit bounds.
    Returns None when the task directory or its GATES literal cannot be found.
    """
    slug = task.rsplit("/", 1)[-1]
    for root in roots:
        for path in sorted(root.glob(f"**/{slug}/tests/test_outputs.py")):
            for node in ast.parse(path.read_text()).body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and getattr(node.targets[0], "id", None) == "GATES"):
                    try:
                        raw = ast.literal_eval(node.value)
                    except ValueError:
                        return None
                    gates = {}
                    for metric, bound in raw.items():
                        if isinstance(bound, dict) and "max" in bound:
                            gates[metric] = ("≤", float(bound["max"]))
                        elif isinstance(bound, dict):
                            gates[metric] = ("≥", float(bound["min"]))
                        else:
                            gates[metric] = ("≥", float(bound))
                    return gates
    return None


def regrade(row: dict, gates: dict[str, tuple[str, float]] | None) -> float | None:
    """Pass or fail against the gates in force now, rather than the recorded reward.

    A trial keeps the reward Harbor stored when the task has no readable GATES, or when
    the trial is missing a gated metric because it delivered nothing. Returns None in
    both cases, which the caller reads as "fall back to the recorded reward".
    """
    if not gates:
        return None
    for metric, (comparison, threshold) in gates.items():
        value = row.get(metric)
        if value is None:
            return None
        if value < threshold - 1e-12 if comparison == "≥" else value > threshold + 1e-12:
            return 0.0
    return 1.0


def _seconds(timing: dict | None) -> float | None:
    if not timing or not timing.get("started_at") or not timing.get("finished_at"):
        return None
    start = datetime.fromisoformat(timing["started_at"])
    end = datetime.fromisoformat(timing["finished_at"])
    return (end - start).total_seconds()


def verifier_metrics(trial_dir: Path) -> tuple[dict[str, float], str | None]:
    """Numeric metrics from verifier/metrics.json, with campaign.* flattened."""
    path = trial_dir / "verifier" / "metrics.json"
    if not path.exists():
        return {}, None
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}, "unreadable metrics.json"
    metrics: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = float(value)
    for key, value in (raw.get("campaign") or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[f"campaign.{key}"] = float(value)
    return metrics, raw.get("error")


def trial_row(job: str, trial_dir: Path, result: dict) -> dict:
    agent_config = (result.get("config") or {}).get("agent") or {}
    agent_info = result.get("agent_info") or {}
    model_info = agent_info.get("model_info") or {}
    agent_result = result.get("agent_result") or {}
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    exception = result.get("exception_info") or {}
    agent_seconds = _seconds(result.get("agent_execution"))
    metrics, verifier_error = verifier_metrics(trial_dir)
    row = {
        "job": job,
        "trial": result.get("trial_name", trial_dir.name),
        "task": task_name(result.get("task_name", "")),
        "agent": agent_config.get("name") or agent_info.get("name", ""),
        "model": agent_config.get("model_name") or model_info.get("name") or "",
        "effort": (agent_config.get("kwargs") or {}).get("reasoning_effort", ""),
        "reward": rewards.get("reward"),
        "exception": exception.get("exception_type", ""),
        "verifier_error": verifier_error or "",
        "agent_minutes": round(agent_seconds / 60, 2) if agent_seconds is not None else None,
        "cost_usd": agent_result.get("cost_usd"),
        "input_tokens": agent_result.get("n_input_tokens"),
        "output_tokens": agent_result.get("n_output_tokens"),
        "cache_tokens": agent_result.get("n_cache_tokens"),
    }
    row.update(metrics)
    row["infra_error"] = is_infra_error(row)
    category, models, hard, answered = refusal_of(trial_dir)
    row["refused"] = hard
    row["refusal_soft"] = bool(category) and not hard
    row["refusal_category"] = category
    row["refusal_models"] = models
    row["model_requested"] = row["model"]
    row["answered_by"] = answered
    # A soft refusal is retried on another model server-side, and that model does the
    # work. The row then describes what the answering model achieved, not the one that
    # declined, so the result is attributed to it and the request is kept alongside.
    # Without this, a trial Opus 4.8 completed after Opus 5.5 declined would be reported
    # as Opus 5.5 passing a task it never took a turn on.
    if row["refusal_soft"] and answered and answered != row["model"]:
        row["model"] = answered.split("|")[0]
    return row


def collect(jobs_dir: Path, job_patterns: list[str]) -> list[dict]:
    rows = []
    for job_dir in sorted(p for p in jobs_dir.glob("*") if p.is_dir()):
        if job_patterns and not any(fnmatch.fnmatch(job_dir.name, p) for p in job_patterns):
            continue
        for result_path in sorted(job_dir.glob("*/result.json")):
            try:
                result = json.loads(result_path.read_text())
            except json.JSONDecodeError:
                continue
            # Stopped trials (still running, or cancelled with the job) are skipped.
            exception = (result.get("exception_info") or {}).get("exception_type")
            if "trial_name" in result and result.get("finished_at") and exception not in CANCELLED:
                rows.append(trial_row(job_dir.name, result_path.parent, result))
    return rows


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _sum(values: list[float]) -> float | None:
    return sum(values) if values else None


def _fmt(value: float | None, digits: int = 3) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def aggregate(rows: list[dict]) -> dict[str, dict]:
    """Group by task, then by (agent, model, effort)."""
    by_task: dict[str, dict[tuple, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_task[row["task"]][(row["agent"], row["model"], row["effort"])].append(row)
    summary: dict[str, dict] = {}
    for task, groups in sorted(by_task.items()):
        gates = task_gates(task)
        if gates is None:  # unknown task: every task metric, without campaign progress
            metric_keys = sorted({k for r in rows if r["task"] == task for k in r
                                  if k not in BASE_COLUMNS and not k.startswith("campaign.")})
        else:
            metric_keys = list(gates)
        entries = []
        for (agent, model, effort), group in groups.items():
            # Refusals leave the pass rate for the same reason infrastructure errors do:
            # neither is a measurement of what the agent can do.
            trials = [t for t in group if not t["infra_error"] and not t.get("refused")]
            graded = [regrade(t, gates) for t in trials]
            rewards = [float(t["reward"] or 0) if value is None else value
                       for t, value in zip(trials, graded)]
            entry = {
                "agent": agent, "model": model, "effort": effort, "trials": len(trials),
                "infra": sum(1 for t in group if t["infra_error"]),
                "refused": sum(1 for t in group if t.get("refused")
                               and not t["infra_error"]),
                "passed": sum(r >= 1 for r in rewards),
                "pass_rate": _mean(rewards),
                "errors": sum(bool(t["exception"]) for t in trials),
                "cost_usd": _sum([t["cost_usd"] for t in trials if t["cost_usd"] is not None]),
                "agent_minutes": _sum([t["agent_minutes"] for t in trials if t["agent_minutes"] is not None]),
                "metrics": {},
            }
            for key in metric_keys:
                values = [t[key] for t in trials if t.get(key) is not None]
                entry["metrics"][key] = (_mean(values), min(values) if values else None,
                                         max(values) if values else None)
            entries.append(entry)
        entries.sort(key=lambda e: (-(e["pass_rate"] or 0), e["agent"], e["model"]))
        summary[task] = {"gates": gates or {}, "metrics": metric_keys, "entries": entries}
    return summary


def _metric(value: float | None) -> str:
    """Counts read as counts (198, or 162.5 for a mean), and scores keep three decimals."""
    if value is None:
        return "–"
    if abs(value) >= 10:
        return f"{value:.0f}" if value == round(value) else f"{value:.1f}"
    return f"{value:.3f}"


def _model_label(model: str, effort: str) -> str:
    """The model without the OpenRouter routing prefix, with its reasoning effort."""
    model = model.removeprefix("openrouter/") or "–"
    return f"{model} ({effort})" if effort else model


def render_markdown(summary: dict[str, dict], n_trials: int) -> str:
    lines = [
        "# Autonomous Science Bench results",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} from {n_trials} trial(s). "
        "Columns are each task's gated metrics, as mean (best) over trials that reached scoring; "
        "a trial passes when every gate holds, and failed or timed-out trials count as 0. "
        "Trials whose agent crashed before any model call (e.g. no API credit) are infrastructure "
        "errors, shown in the pass column and excluded from pass rates and metrics. "
        "A trial the model refused on policy grounds is shown there too, as `refused`, and "
        "is also excluded: the API answers 200 with stop_reason \"refusal\" and a category, "
        "so it is neither a failure nor a measurement of capability, and scoring it as 0 "
        "would read a policy decision as an inability. `trials.csv` carries the category "
        "and every model that refused, including a server-side fallback target. "
        "Cost and agent minutes are totals over a configuration's scored trials. "
        "Trials are graded against the gates printed in each table, so a trial scored "
        "before a gate moved is reported at today's gates, not at the one it ran under. "
        "Every metric is in `trials.csv`.",
    ]
    for task, table in summary.items():
        gates, metric_keys = table["gates"], table["metrics"]
        names = [f"{k} {gates[k][0]} {gates[k][1]:g}" if k in gates else k for k in metric_keys]
        header = ["agent", "model", "pass", *names, "cost $", "agent min"]
        lines += ["", f"## {task}", "",
                  "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for e in table["entries"]:
            passed = f"{e['passed']}/{e['trials']}"
            notes = ([f"{e['infra']} infra"] if e["infra"] else []) + \
                    ([f"{e['refused']} refused"] if e.get("refused") else [])
            if notes:
                passed += f" ({', '.join(notes)})"
            cells = [e["agent"], _model_label(e["model"], e["effort"]), passed]
            for key in metric_keys:
                mean, low, high = e["metrics"].get(key, (None, None, None))
                best = low if gates.get(key, ("≥",))[0] == "≤" else high
                cells.append("–" if mean is None else f"{_metric(mean)} ({_metric(best)})")
            cells += [_fmt(e["cost_usd"], 2), _fmt(e["agent_minutes"], 1)]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_csv(rows: list[dict], path: Path) -> None:
    extra = sorted({k for row in rows for k in row} - set(BASE_COLUMNS))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BASE_COLUMNS + extra, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--job", action="append", default=[], help="job-name glob (repeatable)")
    args = parser.parse_args(argv)
    rows = collect(args.results_dir / "jobs", args.job)
    if not rows:
        print(f"no finished trials under {args.results_dir / 'jobs'}")
        return 0
    args.results_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.results_dir / "trials.csv")
    markdown = render_markdown(aggregate(rows), len(rows))
    (args.results_dir / "summary.md").write_text(markdown)
    print(markdown)
    print(f"wrote {args.results_dir / 'trials.csv'} and {args.results_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
