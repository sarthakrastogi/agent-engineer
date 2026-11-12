---
name: agent-evals
description: >-
  Measure LLM agents and RAG systems: error analysis on traces, failure taxonomy, datasets
  and synthetic inputs, code graders and LLM judges, judge validation (TPR/TNR, bias
  correction), trajectory and tool-call evals, pass@k vs pass^k, CI gates, online evals.
  Use when the user says "add evals", "is this change better?", "write a judge", "can I
  trust this judge", "build a test set", "add an eval gate to CI", "score production
  traffic", or has traces and doesn't know what to measure. Not for fixing the cause of
  wrong output (agent-accuracy), retrieval metrics in depth (agent-rag) or emitting
  traces (agent-observability).
license: MIT
metadata:
  version: 0.4.0
---

# Agent evals

## Core rules

1. **Read traces before writing metrics.** Generic scores (helpfulness, ROUGE,
   BERTScore, cosine) create false confidence.
2. **Every eval maps to a success criterion in `design.md` or a mode in
   `failure-taxonomy.md`.** No orphan metrics.
3. **Fix first, then measure.** Build an evaluator only for failures that recur after a
   fix or are costly enough to guard.
4. **Cheapest grader that works:** code → LLM judge → human.
5. **Binary pass/fail, one judge per failure mode.** Severity = several binary judges.
   A judge may emit a score for diagnosis; gates use the verdict.
6. **No judge is trusted until validated** on a held-out split: TPR and TNR > 0.9 target,
   0.8 floor.
7. **Agents: grade the end state first;** trajectory and per-step for diagnosis.
8. **Report uncertainty:** CIs, k trials per case, paired comparisons. A delta inside the
   noise is no delta.
9. **Cost and latency are reported next to quality on every run.**
10. **Real data first.** Synthetic inputs fill gaps; never synthetic expected outputs.
11. **Only a real model run is a result.** A mock, a keyword simulation or a "predicted"
    score measures your guess, not the agent. Can't run it (no key, no SDK)? Say so and
    give the command; never report predicted numbers as an eval.

## Shortcuts

Use one when it fits instead of the full workflow (`references/quick-paths.md`):

| Situation | Section |
|---|---|
| Eval set exists; "is B better than A?" | *Comparing two versions* |
| One objective failure a code check can catch | *Minimal eval* |
| Inherited suite you haven't verified | *Auditing an existing suite* |

## Workflow

1. **Gather.** Read `design.md`, existing evals, where traces live. Ask for success
   criteria and acceptable error rate if missing; derive the rate from business cost.
2. **Error analysis** (`references/error-analysis.md`): ~100 traces sampled for coverage,
   open-code 30–50, axial-code into 5–10 modes, count. The user (domain expert) confirms
   the modes. Write `failure-taxonomy.md`.
3. **Decide per top mode:** fix directly, evaluator, or product decision. Fill
   `eval-plan.md`.
4. **Datasets** (`references/datasets-and-synthetic-data.md`): 20–50 cases per eval from
   real failures, tagged, with provenance, split and version.
5. **Graders and judges** (`references/graders-and-judges.md`).
6. **Validate judges** (`references/judge-validation.md`): ≥ ~100 balanced expert labels
   (150–200 for tight CIs), train/dev/test.
7. **Agent specifics** (`references/agent-and-trajectory-evals.md`).
8. **Make it runnable.** One command prints per-eval pass rate with CI, cost and latency,
   and saves per-case results with git SHA, model, prompt, dataset and judge versions.
9. **Gate and monitor** (`references/ci-and-online-evals.md`).
10. **Next:** `agent-accuracy` to fix the top modes; `agent-observability` to attach
    scores to traces.

With subagents: `trace-analyst` may draft step 2 (the user still reviews modes and ~30
notes); `eval-engineer` may do steps 4–8 (check its label counts and splits).

## Decision rules

| If | Then |
|---|---|
| Comparing two versions on taste | Pairwise judge, both orders, plus the absolute gate |
| No traffic yet | 20–50 hand-written + dimension-based synthetic inputs; replace with real traces |
| 100+ real traces | Stratified sample; skip synthetic |
| Failures rare | Oversample failures for judge labels |
| User-facing or must work every time | pass^k (k ≥ 3); "can it ever?" → pass@k |
| Multi-turn failure | Reduce to single turn or N-1 prefix |
| Eval passes ~100% | Move to regression; add harder capability cases |
| Task at 0% over many trials | Suspect the task or grader; read transcripts |
| Judge model | Strongest in budget; same family as the agent is fine if it meets TPR/TNR on test |
| Production traffic | Judge ~10% plus all negative-feedback traces; report bias-corrected rate with CI |
| Provider can change the model under you | Nightly suite against production; new failures with no repo change = drift |

**RAG:** evaluate retrieval separately from generation. Retrieval: recall@k on
query→chunk labels first. Generation: faithfulness and answer relevance via validated
judges (RAGAS scores are unvalidated judges). Depth: `agent-rag`.

## Anti-patterns

- One judge grading five dimensions; raw judge agreement quoted when failures are rare.
- Judge few-shots from dev/test; agent examples from the eval set.
- Exact tool-sequence matching that fails valid alternative paths.
- Trials sharing state; retry-until-green in CI.
- Buying eval tooling before one round of error analysis by hand.
- Scoring empty trace-level I/O when the content sits on a child span.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- `failure-taxonomy.md`; `eval-plan.md` (judge table with TPR/TNR, gates, budget).
- `datasets/*.jsonl` with provenance, split and version.
- Versioned judge prompts; per-run results with versions.
- Scores linked to traces (`gen_ai.evaluation.result`).

## References

- `references/quick-paths.md` — the three shortcuts. Read first when one fits.
- `references/error-analysis.md` — sampling, coding, transition matrix. Before any metric.
- `references/datasets-and-synthetic-data.md` — sizes, case schema, synthetic inputs,
  splits. When building a dataset.
- `references/graders-and-judges.md` — grader choice, judge prompt skeleton, judge model,
  biases. When writing a judge.
- `references/judge-validation.md` — labels, splits, TPR/TNR, bias correction, bootstrap.
  Before trusting a judge's numbers.
- `references/agent-and-trajectory-evals.md` — outcome vs trajectory, tool checks,
  pass^k, per-agent-type checks, calibration. For tool-using agents.
- `references/ci-and-online-evals.md` — noise table, CI gates, run cost, online scoring,
  scores on traces. When wiring CI or production scoring.
