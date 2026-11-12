---
name: agent-production
description: >-
  Takes LLM agents to production and keeps them there: timeouts, bounded retries,
  idempotent side effects, durable execution, overall cost and latency (routing, batching,
  streaming, budgets), versioned release units, pinned models, shadow/canary rollouts,
  eval-gated model migration, incidents. Use when an agent is slow or expensive, errors,
  times out, loses work on crash, double-sends on retry, is being released, or needs a
  model migration. Not for flaky outputs or post-upgrade regressions (agent-accuracy),
  per-turn token bloat or cache layout (agent-context), or tracing and alerts
  (agent-observability).
license: MIT
metadata:
  version: 0.4.0
---

# Agent production

## Core rules

1. **Bound everything:** max turns, per-call and per-run timeouts, retry attempts, ~3
   consecutive errors → human, token and spend cap per run.
2. **Idempotent side effects before retries.** A retried `send_email` without an
   idempotency key sends twice.
3. **Own state and control flow.** Checkpoint each step of long runs; resume from the last
   success, never from scratch.
4. **Measure before optimising:** quality, cost per successful task, p50/p95 latency and
   cache hit rate on the eval set; attack the dominant term.
5. **Cache-friendly prefix:** stable tools and system prompt, no timestamps or request IDs,
   append-only history.
6. **Sweep effort and model size before building routing.** Multi-model must beat the
   single model's whole effort/cost curve.
7. **Pin model snapshot IDs.** Version model + params + prompts + tool schemas as one
   release unit, recorded on every trace.
8. **No release without the regression eval gate**, then shadow → canary → full, with
   rollback triggers written down first.
9. **Model migration is an eval-gated project**, not a config change.

## Workflow

1. **Get budgets from the user:** p95 latency, TTFT, cost per task, volume, run duration,
   cost of a failure. Record in `agent-engineering/design.md`. Don't invent numbers.
2. **Baseline** the eval set: quality, cost per successful task, p50/p95, tokens in/out,
   cache hit rate, tool calls per run. Missing metrics → set up with `agent-observability`.
3. **Reliability pass** (reliability.md).
4. **Cost/latency pass:** one lever at a time, each logged in `experiment-log.md` with
   quality, cost and latency deltas (cost-and-latency.md).
5. **Version, gate, roll out** (versioning-and-rollouts.md, release-pipeline.md).
6. **Model change:** model-migration.md end to end.
7. **Incident prep:** kill switches, runbook, on-call owner (incident-response.md).

## Decision rules

| Situation | Do |
|---|---|
| Runs exceed a request timeout, wait on humans, or have costly steps | Durable execution with per-step checkpoints (workflow engine or DB-backed state machine) |
| Tool writes, sends, pays or creates | Idempotency key from run + step ID; dedupe in executor or downstream |
| 429 / 5xx / overloaded | Backoff with full jitter, honour `retry-after`; one retry layer only |
| 400 / validation / context too long | Don't retry; fix, compact, or return an actionable error |
| Long shared prefix, repeated calls | Prompt caching; verify cache-read tokens > 0 |
| Offline or non-urgent work | Batch API (~−50%), stacked with caching |
| Latency dominated by output | Cut output tokens; stream; smaller model |
| Independent tool calls or subtasks | Parallel tool calls / subagents |
| Mixed easy and hard traffic | Route or cascade small → large, evals per route |
| New model or provider | model-migration.md; shadow then canary |
| Long runs at deploy time | Pin in-flight runs to their starting version |

## Anti-patterns

Beyond breaking the rules above:

- Retries stacked across SDK, framework and app.
- Semantic caching of answers.
- Shadow traffic that calls real write tools.
- Prompts edited live outside version control.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

Add to `agent-engineering/design.md`:

```markdown
## Production
- Budgets: p95 ≤ 8 s, TTFT ≤ 1 s, ≤ $0.04 per successful task, 2k tasks/day
- Bounds: 25 turns, 120 s per tool, 15 min per run, 3 consecutive errors → human, $0.50 cap
- Release unit: model <pinned-snapshot-id>, effort=medium, prompt v14, tools sha 3f2c…
- Rollout: shadow 48 h → canary 5% 24 h → 50% → 100%
- Rollback if: regression pass < 98%, online judge −5 pts vs control, p95 > 10 s,
  cost/task > $0.06, high-risk tool error spike
- Kill switches: per-tool disable, approve-all mode, global pause
- Fallbacks: per dependency, fail open / degrade / fail closed
```

Log each optimisation, rollout and migration with its measured delta. Before launch, walk
`assets/launch-checklist.md`.

## References

- [reliability.md](references/reliability.md) — failure classes, timeouts, retries,
  idempotency, durable runs, loop guards, fallbacks, quotas, escalation. Read for flaky,
  crashing or long runs, or before launch.
- [cost-and-latency.md](references/cost-and-latency.md) — metrics, levers, routing order,
  latency tactics, simplification, token budgets. Read when slow or expensive.
- [decision-classifiers.md](references/decision-classifiers.md) — small typed classifiers
  for routing, screening, gating, verification; thresholds; rollout. Read when an LLM call
  only decides.
- [versioning-and-rollouts.md](references/versioning-and-rollouts.md) — release unit,
  pinning, pre-release gate, shadow/canary, rollback. Read before any release.
- [release-pipeline.md](references/release-pipeline.md) — CI/CD stages, golden dataset,
  baseline, trace checks, code tests, nightly drift run. Read when wiring CI.
- [model-migration.md](references/model-migration.md) — what changes between models,
  migration steps, ship rule. Read when changing or retiring a model.
- [incident-response.md](references/incident-response.md) — incident types, pre-launch
  controls, runbook, follow-up. Read before launch and during an incident.
