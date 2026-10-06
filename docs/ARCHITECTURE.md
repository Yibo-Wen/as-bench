# Autonomous Science Bench architecture

## Trial topology

```
┌──────────────── Harbor trial ────────────────────────────────────────────────┐
│                                                                              │
│  main (agent)                      lab (sidecar, internal network only)      │
│  ─────────────                     ──────────────────────────────────────    │
│  /app/data  public data            asb_lab.server  Lab API v1                │
│  asb  ── HTTP ─────────────────▶   campaign.json   stages, catalogs, budget  │
│  deno (local validation)           backend         replay | twin | live      │
│                                    /state/ledger.json                        │
│                                    /state/deliverables/<file>                │
│                                                                              │
└──────────────────────────────────────────────┬───────────────────────────────┘
                                               │ artifacts (service = "lab")
                                               ▼
                             verifier (separate image, no network)
                             tests/data  hidden truth
                             asb_verify  ledger check, metrics, JS sandbox
                             → reward.txt (0/1), metrics.json, ctrf.json
```

The agent container never holds answer data. Query truth exists only in the lab image, and evaluation truth only in the verifier image. The verifier receives the ledger and deliverables from the lab, not from the agent. Anything the agent writes to its own filesystem cannot affect grading.

## Invariants across backends

The lab server, not the backend, owns everything the agent can observe:

| Concern | Owner |
|---|---|
| Stage order, batch size, catalog or grid eligibility, uniqueness (grid and shared-pool catalog: never re-measured) | `Lab.submit_experiment` |
| Idempotent replay of the latest accepted batch | `Lab.submit_experiment` |
| Rejected requests never consume budget | `Lab.submit_experiment` (400/409/503 before any ledger write) |
| Budget accounting | `Lab.status` (`spent` counts accepted, non-failed jobs) |
| Deliverable gating (`deliverable.requires_stages`, default all stages), size and kind checks | `Lab.submit_deliverable` |
| Ledger schema and determinism (sequential job IDs, no clock) | `asb_lab.ledger` |

A backend only turns an accepted batch into measurements (`Backend.submit`, plus `Backend.poll` if it is asynchronous). The public campaign card never names the backend. Replacing replay with a twin or a live lab therefore leaves the instruction, the `asb` commands, and the ledger format unchanged.

## Design spaces and measurements

| Design space | The agent sends | Example |
|---|---|---|
| `catalog` (default) | design IDs from each stage's CSV catalog | DREAM (archived): 96 variant IDs per stage |
| `catalog` with `shared_pool` | design IDs from one pool listed by every stage; each design at most once per campaign | Sargent lab: 20 dilute-alloy designs per round from 736 |
| `upload` | design files (`asb run FILE...`), validated by the lab before a stage is used | Nanofab (archived): two `1024 × 1024` binary photomasks per print round |
| `grid` | positions `r{row}c{col}` inside `shape` (`asb run` takes a `row,col` CSV); any position in any stage, each at most once per campaign | ATHENA (archived): 20 probe positions per scan round on a `128 × 128` field of view |

Measurements are scalar `value`s returned inline, or `file`s (e.g. SEM images) that the client downloads from `/v1/experiments/{job}/files/{name}`. Deliverables are text (a JS predictor, a JSON scan report) or binary (a `.npy` mask). A campaign may accept the deliverable before every stage is used (`deliverable.requires_stages`), so agents decide when the evidence is enough.

## Jobs

`POST /v1/experiments` returns a job. Replay jobs are `completed` on return. Twin jobs run in a background thread and start `running`; live jobs start `queued` or `running`. For both, `GET /v1/experiments/{id}` polls the backend. `asb run` waits by default, so agents see the same workflow at every tier. A `failed` job does not consume its stage; the agent may resubmit. While one job is in flight, the lab rejects submissions for other stages with 409.

## Ledger (`/state/ledger.json`, format_version 1)

```json
{
  "format_version": 1,
  "campaign_id": "dream/protein-active-learning",
  "backend": "replay",
  "jobs": [
    {"job_id": "job-0001", "stage": 1, "designs": ["variant_…"], "status": "completed",
     "results": [{"variant_id": "variant_…", "stain_activity": 0.1234}]}
  ],
  "deliverables": [
    {"seq": 1, "kind": "js-predictor", "filename": "final_model.js", "bytes": 12345, "sha256": "…"}
  ]
}
```

`asb_verify.ledger_check.check_ledger` verifies all of the following:
- the campaign and backend identity;
- exactly one completed job per stage, in order;
- batch sizes, uniqueness, and eligibility;
- that each returned measurement equals the truth table;
- that no design was measured twice;
- that a deliverable was recorded.

## Why the runtime is vendored

`harbor publish` packages each task directory by itself, and Docker build contexts cannot reach outside the task. `runtime/` is the single source of truth. `tools/vendor_runtime.py` writes copies with a do-not-edit header into each campaign, and `ci_checks/check-runtime-sync.sh` fails if any copy drifts. Both cover `tasks/` only, so a campaign in `archive/` keeps the copy it was archived with and still runs as it did then.

## Backend tiers

| Tier | Truth for the verifier | What changes in the task |
|---|---|---|
| replay | Recorded measurements (`tests/data`) | — |
| twin | The same hidden process, re-implemented independently in the verifier | `backend.kind = "twin"`, `backend.module` = the campaign's process model in the lab image (`asb_lab/backends/twin.py` documents the contract); measurements run in a background thread |
| live | Lab-measured evaluation library, reviewed by the lab's scientists | `backend.kind = "live"`, lab credentials via env, verifier truth delivered after the lab run |

## Sources

The second path level, `tasks/<domain>/<source>/<campaign>`, names who provides the campaign. A **source** (`sources/<id>/source.toml`, `[metadata] source`) is the research group whose published data back it, for example the Sargent lab's CO₂-reduction screening data. Its card lists the dataset's DOI and licence and declares which backends exist; today every source ships replay or twin only. `campaign.json` sets `source` to the group id, so the public card names the provider.

## Scoring

Rewards are binary, as in terminal-bench-science. Each campaign gates two skills, and the reward is 1 only if every gate passes: discovery, judged on what the agent measured, and learning, judged on the delivered model. For example, biosensor-active-learning asks for at least 17 selective hits among the 30 variants ordered and NDCG@20 ≥ 0.50 on 332 held-out variants. `metrics.json` keeps the continuous values for analysis.
