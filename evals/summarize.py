#!/usr/bin/env python3
"""Summarize Harbor job results into results/trials.csv and results/summary.md.

  uv run python evals/summarize.py                     # every job under results/jobs
  uv run python evals/summarize.py --job '2026*small*'  # only matching jobs

Reads each trial's result.json (agent, model, reward, tokens, cost, timing,
exception) and verifier/metrics.json (continuous task metrics and campaign
progress written by asb_verify). Metric columns are discovered per task, so
new campaigns need no changes here.

Trials that were stopped (unfinished or cancelled) are ignored. Trials whose
agent errored before making any billed model call and before completing any
stage (an out-of-credit key, a failed agent install) are infrastructure errors:
counted in the "infra" column and left out of pass rates and metrics.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COLUMNS = [
    "job", "trial", "task", "agent", "model", "effort", "reward", "exception", "infra_error",
    "verifier_error", "agent_minutes", "cost_usd", "input_tokens", "output_tokens", "cache_tokens",
]
CANCELLED = {"CancelledError", "KeyboardInterrupt"}
TIMEOUTS = {"AgentTimeoutError"}
NO_MODEL_AGENTS = {"oracle", "nop"}


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
        "task": result.get("task_name", ""),
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


def _fmt(value: float | None, digits: int = 3) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def aggregate(rows: list[dict]) -> dict[str, list[dict]]:
    """Group by task, then by (agent, model, effort)."""
    by_task: dict[str, dict[tuple, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_task[row["task"]][(row["agent"], row["model"], row["effort"])].append(row)
    summary: dict[str, list[dict]] = {}
    for task, groups in sorted(by_task.items()):
        metric_keys = sorted({k for r in rows if r["task"] == task for k in r if k not in BASE_COLUMNS})
        entries = []
        for (agent, model, effort), group in groups.items():
            trials = [t for t in group if not t["infra_error"]]
            rewards = [float(t["reward"] or 0) for t in trials]
            entry = {
                "agent": agent, "model": model, "effort": effort, "trials": len(trials),
                "infra": len(group) - len(trials),
                "passed": sum(r >= 1 for r in rewards),
                "pass_rate": _mean(rewards),
                "errors": sum(bool(t["exception"]) for t in trials),
                "cost_usd": _mean([t["cost_usd"] for t in trials if t["cost_usd"] is not None]),
                "agent_minutes": _mean([t["agent_minutes"] for t in trials if t["agent_minutes"] is not None]),
                "metrics": {},
            }
            for key in metric_keys:
                values = [t[key] for t in trials if t.get(key) is not None]
                entry["metrics"][key] = (_mean(values), max(values) if values else None)
            entries.append(entry)
        entries.sort(key=lambda e: (-(e["pass_rate"] or 0), e["agent"], e["model"]))
        summary[task] = entries
    return summary


def render_markdown(summary: dict[str, list[dict]], n_trials: int) -> str:
    lines = [
        "# AS-Bench results",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} from {n_trials} trial(s). "
        "Metrics show mean (max) across trials; failed trials count as reward 0. "
        "`infra` counts trials whose agent crashed before any model call (e.g. no API credit); "
        "they are excluded from pass rates and metrics.",
    ]
    for task, entries in summary.items():
        metric_keys = sorted({k for e in entries for k in e["metrics"]})
        header = ["agent", "model", "effort", "pass", "pass rate", "errors", "infra",
                  *metric_keys, "cost $", "agent min"]
        lines += ["", f"## {task}", "", "| " + " | ".join(header) + " |",
                  "|" + "---|" * len(header)]
        for e in entries:
            cells = [e["agent"], e["model"] or "–", e["effort"] or "–",
                     f"{e['passed']}/{e['trials']}", _fmt(e["pass_rate"], 2), str(e["errors"]),
                     str(e["infra"])]
            for key in metric_keys:
                mean, best = e["metrics"].get(key, (None, None))
                cells.append("–" if mean is None else f"{_fmt(mean)} ({_fmt(best)})")
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
