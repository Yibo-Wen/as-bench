# Evaluating agents and models

AS-Bench evaluates agents the same way terminal-bench-science does. Every run is a **Harbor job**: a set of tasks × (agent, model) pairs × attempts. Harbor starts each trial's containers (agent + lab sidecar), runs the agent, runs the separate verifier, and writes everything to a job directory. This folder adds three thin layers on top:

| File | Role | terminal-bench-science equivalent |
|---|---|---|
| `agents.toml` | Named agent × model presets and suites | `.github/llm-config.yml` → `harbor_run.agents` |
| `run.py` | Builds a Harbor job config from presets + tasks, checks credentials, runs `harbor run -c` | `run-trials.yml` (the `harbor run -c /tmp/harbor-job.json` path) |
| `summarize.py` | Collects every trial into `results/trials.csv` and `results/summary.md` | the inline result tables in `run-trials.yml` |

Results go to `results/`, which is gitignored:

```
results/
├── configs/<job>.json            exact Harbor job config for each run
├── jobs/<job>/                   Harbor's native output
│   ├── config.json, result.json  job config and aggregate stats
│   └── <task>__<id>/             one trial
│       ├── result.json           agent, model, reward, tokens, cost, timing, exception
│       ├── agent/                agent logs and trajectory
│       ├── verifier/             reward.txt, metrics.json (NDCG, P@50, campaign progress), ctrf.json
│       └── artifacts/
├── trials.csv                    one row per trial, all jobs
└── summary.md                    per-task table: pass rate, metrics mean (max), cost, time
```

## Setup (once)

```bash
uv sync --group eval              # installs harbor[modal,daytona]==0.21.0 into .venv
cp .env.example .env              # add ANTHROPIC_API_KEY / OPENAI_API_KEY / ...
```

You also need somewhere to run the containers:

- **`--env docker`** (default): Docker on this machine. The task runs two containers (agent + lab), and each trial needs 4 CPUs and 8 GB RAM.
- **`--env modal`** or **`--env daytona`**: cloud sandboxes. Both support the task's docker-compose sidecar through Docker-in-Docker. terminal-bench-science recommends these for full runs because they run many trials in parallel. Set `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` (or run `modal token new`), or `DAYTONA_API_KEY`.

## Running

```bash
uv run python evals/run.py --list                               # presets, suites, tasks

# 1. Sanity: the environment builds, the oracle scores 1, and nop scores 0
uv run python evals/run.py --suite sanity

# 2. Small models first, with a shorter clock (0.25 × 8 h = 2 h per trial)
uv run python evals/run.py --suite small --agent-timeout-multiplier 0.25

# 3. Several attempts per model for pass rates
uv run python evals/run.py --preset claude-sonnet-5.5 --preset gpt-5.6-terra -k 3 --env modal

# 4. Frontier models at the full time limit
uv run python evals/run.py --suite frontier -k 3 --env modal

uv run python evals/summarize.py                                # rebuild tables from all jobs
uv run python evals/run.py --suite small --dry-run              # inspect config + command only
uv run python evals/run.py --preset gpt-5.6-luna -- --debug              # pass extra flags to harbor run
```

The summary groups trials by task and by agent × model × reasoning effort. The metric columns come from each task's `verifier/metrics.json`:
- task metrics, such as `discovery_hits` and `ndcg_at_20`;
- `campaign.stages_completed`, `campaign.designs_measured`, and `campaign.deliverables_submitted`, which show how far an agent got even when it fails;
- `verifier_error` in `trials.csv`, which records why a trial failed.

## Presets

Agent and reasoning-effort settings follow the [Terminal-Bench-Science 0.1 leaderboard](https://snorkel.ai/leaderboard/terminal-bench-science/). Scores on AS-Bench can therefore be read against each model's TB-Science rank (in brackets).

| Preset | Agent | Model id | Effort | Key |
|---|---|---|---|---|
| `gpt-6-astra` [1] | codex | gpt-6-astra | max | `OPENAI_API_KEY` |
| `claude-opus-5.5` [2] | claude-code | claude-opus-5-5 | max | `ANTHROPIC_API_KEY` |
| `claude-fable-5.1` [3] | claude-code | claude-fable-5-1 | max | `ANTHROPIC_API_KEY` |
| `gpt-5.6-sol` [5] | codex | gpt-5.6-sol | max | `OPENAI_API_KEY` |
| `deepseek-v4.1-flash` [7] | codex → api.deepseek.com | deepseek-flash | max | `DEEPSEEK_API_KEY` |
| `grok-4.7` [8] | grok-build | grok-4.7 | xhigh | `XAI_API_KEY` |
| `gemini-3.8-flash` [9] | mini-swe-agent | gemini/gemini-3.8-flash | high | `GEMINI_API_KEY` |
| `claude-opus-4.8` [10] | claude-code | claude-opus-4-8 | max | `ANTHROPIC_API_KEY` |
| `gpt-5.6-terra` [11] | codex | gpt-5.6-terra | max | `OPENAI_API_KEY` |
| `glm-5.3` [12] | claude-code → api.z.ai | glm-5.3 | max | `ZAI_API_KEY` |
| `kimi-k3` [13] | claude-code → api.moonshot.ai | kimi-k3 | max | `MOONSHOT_API_KEY` |
| `grok-4.6` [14] | grok-build | grok-4.6 | xhigh | `XAI_API_KEY` |
| `gemini-3.7-flash` [15] | mini-swe-agent | gemini/gemini-3.7-flash | high | `GEMINI_API_KEY` |
| `deepseek-v4-pro` [16] | codex → api.deepseek.com | deepseek-v4-pro | max | `DEEPSEEK_API_KEY` |
| `gpt-5.6-luna` [17] | codex | gpt-5.6-luna | max | `OPENAI_API_KEY` |
| `claude-sonnet-5.5` | claude-code | claude-sonnet-5-5 | high | `ANTHROPIC_API_KEY` |
| `claude-haiku-4.5` | claude-code | claude-haiku-4-5-20251001 | – | `ANTHROPIC_API_KEY` |
| `qwen3.8-27b`, `glm-5.3-flash`, `minimax-m3` | mini-swe-agent | openrouter/… | – | `OPENROUTER_API_KEY` |
| `mswea-claude-haiku-4.5`, `mswea-gpt-5.6-luna` | mini-swe-agent | anthropic/…, openai/… | – | vendor key |

| Suite | Presets | Use |
|---|---|---|
| `sanity` | oracle, nop | environment check: 1 and 0 |
| `small` | claude-haiku-4.5, gpt-5.6-luna, gemini-3.8-flash, deepseek-v4.1-flash | cheapest capable models; start here |
| `open-small` | qwen3.8-27b, glm-5.3-flash, minimax-m3 | small open-weight models, one OpenRouter key |
| `mid` | claude-sonnet-5.5, gpt-5.6-terra, grok-4.7, glm-5.3, kimi-k3, deepseek-v4-pro | |
| `frontier` | gpt-6-astra, claude-opus-5.5, claude-fable-5.1, gpt-5.6-sol | highest cost per trial |
| `tbs-leaderboard` | the 15 reproducible leaderboard entries | calibrate AS-Bench against TB-Science |
| `harness` | haiku, luna, gemini flash, all under mini-swe-agent | compare models with the agent scaffold held fixed |

Notes:
- **Vendor agent vs. neutral harness.** Each vendor's own CLI (`claude-code`, `codex`, `grok-build`) is how the leaderboard runs its top models. mini-swe-agent (LiteLLM) runs any provider under the same scaffold, so compare scores across the two only with that in mind.
- **Open-weight models behind vendor CLIs.** GLM and Kimi run in Claude Code against their vendors' Anthropic-compatible endpoints. DeepSeek runs in Codex against DeepSeek's Responses endpoint. The preset overrides the key and base URL, so your Anthropic or OpenAI key is never sent to those endpoints.
- **Model ids change.** If a vendor renames a model, edit `model` in `agents.toml`. `uv run harbor run --help` lists every Harbor agent, including gemini-cli, opencode, openhands, and kimi-cli.
- **Adding a preset.** Add a `[presets."<name>"]` table with `agent`, `model`, and optionally `kwargs` (passed as `--ak`) and `env`. Secrets must be written as `"${VAR}"`; `run.py` refuses literal keys.

## Stopping and budget

- **Stopping.** Stop a job with `uv run python evals/stop.py [<job>]`. Killing `harbor run` alone can leave each trial's agent and lab containers running, and the agent keeps calling its model. `stop.py` interrupts Harbor, then removes the job's trial containers. Trials that had not finished are left out of `summary.md`.
- **Budget.** Each trial can run up to the task's full agent time limit (8 h here). `--agent-timeout-multiplier 0.25` cuts that to 2 h; the instruction still states the task's full limit. TB-Science cost for its whole 70-task suite ranged from about $380 (GPT-5.6 Luna, DeepSeek V4.1 Flash) to $6K+ (Fable). Start with `--suite small`, `-k 1`, and a multiplier, and check `cost $` in `summary.md` before scaling up.

## Adding tasks

Nothing here is task-specific. `run.py` picks up every campaign listed in `tasks/dataset.toml` (or `--task 'chemistry/*'` for a subset). `summarize.py` reads whatever numeric metrics the task's verifier writes. A new campaign only needs `asb_verify.report.run_verifier(..., progress=...)` in its `tests/test_outputs.py`.

## Sharing results

Results stay local by default. To share a job the way terminal-bench-science does for its leaderboard, upload it to Harbor Hub (private unless you add `--public`):

```bash
uv run harbor upload results/jobs/<job>
```

For a qualitative review of trajectories, use `harbor analyze -m <model> results/jobs/<job>`, as terminal-bench-science does with its `rubrics/trial-analysis.toml`.
