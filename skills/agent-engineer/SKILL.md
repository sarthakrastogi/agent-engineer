---
name: agent-engineer
description: >-
  Entry point for LLM agent work when the right agent-* skill isn't obvious. Maps symptoms
  to the specialised agent-* skills and defines the optional agent-engineering/ project
  record. Use when starting a blank-page agent, chatbot or RAG project ("build an agent
  that…", "where do I start?"), for a broad request spanning several areas ("we're
  launching next week", "review our agent"), or to survey an existing LLM codebase before
  changing it. For a specific task (a prompt, tools, evals, tracing, retrieval, cost) load
  that skill directly instead.
license: MIT
metadata:
  version: 0.4.0
---

# Agent engineer

Pick the specialist skill and get out of the way. Each `agent-*` skill carries its own
rules; this one only maps problems to skills and holds the shared conventions.

## Where is it failing? (the agent stack)

Each layer contains the one below: graph ⊃ loop ⊃ harness ⊃ context ⊃ prompt. Fix at the
lowest layer that explains the failure; add a layer only on evidence.

| Symptom | Layer | Go to |
|---|---|---|
| Right facts in view; wrong wording, format or tone | Prompt | `agent-prompting` |
| Model didn't see the right facts or tools | Context | `agent-context`, `agent-rag` |
| Unsafe action, no sandbox or verification, lost state | Harness | `agent-tools`, `agent-guardrails` |
| Runs forever, stops early, retries blindly | Loop | `agent-design`, `agent-production` |
| Roles collide or handoffs lose work | Graph | `agent-design` (most tasks never need one) |

Wrong answers or hallucinations with no obvious layer → `agent-accuracy` localises them.
New agent → `agent-design`, then tools → prompting → observability → evals.
About to launch → `agent-production` → `assets/launch-checklist.md`.
Salesforce Agentforce (`.agent` files, Agent Script, `sf agent`) → `agentforce`, which maps
these skills onto the platform.

## Shortcuts: the user already has something

| Situation | Do this |
|---|---|
| Two versions + an eval set | `agent-evals` → *Comparing two versions* |
| One objective failure (invented IDs, broken format) | `agent-evals` → *Minimal eval*, then `agent-accuracy` |
| No tracing, need to see what it does | `agent-observability` → *Debug-grade tracing* |
| Inherited eval suite | `agent-evals` → *Auditing an existing suite* |

An existing agent without evals or tracing gets the minimum the task needs. Name the gap;
don't block on it.

## Evidence

For a non-trivial behaviour change, propose the smallest eval that would show it worked.
If the user declines, or the change is cosmetic, make it and say plainly that it's
unevaluated. Never call an unmeasured change an improvement: mocked or predicted results
and offline unit tests show the code runs, not that the agent got better.

Report in your reply or the PR, not in new summary or report files the user didn't ask
for.

## Starting in an existing codebase

1. Find the LLM calls, prompts, tool definitions and agent loop.
2. Check for tracing, eval sets and logged production data.
3. Read `agent-engineering/` if present. It holds past decisions.
4. Summarise in ≤ 10 lines: architecture, models, tools, what's measured, biggest gap.
   Propose the next step.

## Project record (optional)

For an agent project that spans sessions, offer once to keep `agent-engineering/` (or
`$AE_DIR`) in the repo; create it only if the user agrees. If it exists, keep it current.
Otherwise put decisions and eval deltas in your reply or the PR description.

| File | Template | Holds |
|---|---|---|
| `design.md` | `agent-design` → `assets/design.md` | success criteria, architecture, risks, production plan, decision log |
| `eval-plan.md` | `agent-evals` → `assets/eval-plan.md` | evals, graders, judge validation, gates |
| `failure-taxonomy.md` | `agent-evals` → `assets/failure-taxonomy.md` | failure modes with counts |
| `experiment-log.md` | `agent-accuracy` → `assets/experiment-log.md` | per behaviour change: hypothesis, eval delta, decision |
| `observability.md` | `agent-observability` → `assets/observability.md` | span schema, metrics, PII policy |
| `datasets/*.jsonl` | | eval sets, retrieval and attack sets |

## Delegation

If subagents are available: `trace-analyst` for error analysis over many traces,
`eval-engineer` for datasets and judges, `rag-diagnostician` for retrieval failures,
`agent-reviewer` for a pre-release review. They can't ask the user anything, so put what
they need in the brief, including whether the user agreed to `agent-engineering/` files.
