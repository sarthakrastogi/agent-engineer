---
name: agent-accuracy
description: >-
  Debug and fix an LLM agent or RAG system that gives wrong, inconsistent, hallucinated or
  low-quality results: reproduce, localise the failing component, one hypothesis, the
  least-powerful fix, measure, check regressions, log. Use when the user says "it
  hallucinates", "wrong answers", "accuracy dropped", "flaky / right only sometimes",
  "worse after the model upgrade", "should I add RAG / a bigger model / fine-tune?", or
  wants a pass rate up. Not for building evals (agent-evals), errors/timeouts/migrations
  (agent-production), retrieval internals (agent-rag), prompt rewrites (agent-prompting)
  or tool redesign (agent-tools).
license: MIT
metadata:
  version: 0.4.0
---

# Agent accuracy

Take one failure mode at a time to a measured, logged fix that breaks nothing else, using
the cheapest change that works.

## Core rules

1. **Measure before and after.** A fix needs a baseline. No eval set → propose 20–50
   cases from real failures via `agent-evals` (*Minimal eval*); if the user declines, say
   the fix is unverified.
2. **Reproduce first:** k ≈ 3 / expected failure rate runs (≈ 5 at 50%, ≈ 30 at 10%;
   20–30 if unknown). Five clean runs only rule out failure rates above ~45%.
3. **Fix the first upstream failure.** Later errors cascade from it.
4. **One hypothesis, one change per experiment.**
5. **Least-powerful fix first:** code/config/data → prompt → examples → tools → retrieval
   → architecture → model → fine-tune. Climb only when the lower rung failed and
   localisation still points higher.
6. **Fix categories, not traces.** A single bad trace goes into the dataset, not the
   prompt.
7. **Paired comparison on dev with repeats; confirm once on held-out test;** full suite
   for regressions, cost, latency.
8. **Log every experiment,** failures included, in `experiment-log.md`.
9. **0/k = capability (something missing); some/k = reliability.** Different fixes.

## Workflow

1. **Intake.** Find the failures yourself before touching code: logged conversations,
   traces or eval output in the repo (`logs/`, `data/`, `*.jsonl`) or the tracing backend.
   Read the failing ones; they become the reproduction cases. Also read
   `failure-taxonomy.md` and `experiment-log.md` if present, and state what "fixed" means.
2. **Reproduce.** Each failure becomes an eval case (multi-turn → single turn or N-1).
   Run k times; record per-case pass rate.
3. **Rule out the eval.** Is the reference, grader or task wrong?
4. **Localise** (`references/localising-failures.md`): first wrong step, confirmed by a
   counterfactual probe. With subagents: many traces → `trace-analyst`; retrieval →
   `rag-diagnostician`.
5. **Quantify.** How many cases share the mode? Work down by rate × severity.
6. **Hypothesise** in the log before running: "M fails because C; changing X moves M from
   a% to b% without hurting N."
7. **Fix** at the lowest rung that addresses the cause (`references/intervention-ladder.md`).
8. **Measure** (`references/experiment-discipline.md`; procedure: `agent-evals` →
   *Comparing two versions*).
9. **Regression check:** full suite, cost, latency. Reproduced cases join the regression
   set.
10. **Log and decide:** keep, revert or iterate. Next mode, or stop.
11. **Next:** `agent-evals` for new gates; `agent-production` to roll out;
    `agent-observability` to watch the mode online.

## Decision rules

| Symptom | First move |
|---|---|
| Confident wrong fact | Search the trace's context for the right fact. Absent → retrieval/data; present → generation |
| Right context, wrong answer | Answer only from context, quote first, allow "I don't know"; trim distractors |
| Wrong tool or arguments | Tool names, descriptions, schemas, examples (`agent-tools`) |
| Format breaks | Structured output + validation in code |
| Answers half of a multi-part question | Decompose; completeness check per part |
| Confident answer after empty/`null` tool result | Silent tool failure: explicit tool errors (`agent-tools`) |
| Varies run to run | Remove ambiguity, examples, rules into code, verification step; measure pass^k |
| Worse after model/prompt change | Paired diff by mode vs last good version; read the log |
| Degrades in long runs | `agent-context` |
| Correct but slow/expensive | Not accuracy → `agent-production` |

- **Add RAG** only if the needed fact is absent from context on failing traces; for
  format, policy or consistency it can hurt.
- **Bigger model** only when cases fail 0/k with oracle context, clean instructions and
  good tools, and a stronger-model probe passes. Sweep effort first.
- **Fine-tune** only for consistent behaviour/format, with 50+ examples and a stable eval.

**Stop** when the target is met with the CI's lower bound above it; when the last 2–3
experiments are within noise and the next rung costs more than the remaining failures; or
when what's left needs a product decision. Tell the user which.

## Anti-patterns

- Tweak, eyeball two examples, ship.
- Several changes in one experiment; one prompt line per bad trace.
- Iterating on the test split; eval cases in few-shot examples.
- RAG, a bigger model, multi-agent or fine-tuning before localising.
- Fixing the downstream symptom instead of the first upstream failure.
- Trusting one run of a stochastic agent.
- Running a prompt optimiser on an unvalidated eval.
- Blaming the model when the grader or reference is wrong.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- One `experiment-log.md` entry per experiment: change, hypothesis, eval and dataset
  version, before → after with CI, regressions, cost/latency, decision.
- `failure-taxonomy.md`: updated counts and `Component` column.
- New regression cases in `datasets/` with provenance.

## References

- `references/localising-failures.md` — trace reading order, symptom → component →
  confirmation, counterfactual probes, capability vs reliability. Read before choosing a
  fix.
- `references/intervention-ladder.md` — each rung's when/how/pitfalls, when popular fixes
  are wrong, reliability fixes. Read when choosing a fix.
- `references/experiment-discipline.md` — hypothesis, run counts, keep/revert,
  overfitting, log entry, stopping. Comparison mechanics: `agent-evals` →
  `references/quick-paths.md`.
