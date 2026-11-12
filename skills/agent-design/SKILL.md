---
name: agent-design
description: >-
  Scopes and architects LLM agents: testable success criteria, the simplest shape (single
  call, workflow, single agent, multi-agent), framework and models, human checkpoints,
  agent-engineering/design.md. Use when
  building an agent from scratch, deciding agent vs workflow vs multi-agent, choosing a
  framework or model, or redesigning one that is too slow, costly or unreliable. Not for
  tool schemas or MCP (agent-tools), prompt wording (agent-prompting), memory
  (agent-context), retrieval (agent-rag), tuning a running agent (agent-production), or
  permissions (agent-guardrails).
license: MIT
metadata:
  version: 0.4.0
---

# Agent design

Every moving part in the design must be justified by a requirement it serves.

## Core rules

1. **Testable success criteria before architecture:** metric + threshold + how measured, for
   quality, safety, latency and cost per task.
2. **Climb the ladder one rung at a time:** single call → workflow → single agent →
   multi-agent. Write down why each simpler rung fails before moving up.
3. **Known steps → workflow.** Errors compound in agents: 95% per step is ~60% over 10 steps.
4. **Mostly deterministic code**, LLM steps only where judgement is needed. Hard-code
   confirmations, refusals and small input spaces.
5. **Multi-agent only for parallel independent reads (one writer), or a domain split after
   fixing tools didn't help.** Never for coupled work. Agents cost ~4x chat tokens,
   multi-agent ~15x.
6. **Most capable model first, then step down per step against evals.** Sweep reasoning
   effort before switching models.
7. **Raw SDK first.** Adopt a framework only for what it buys (durable execution, tracing,
   approvals), and only if you can still see and own prompts, context and control flow.
8. **Bound every loop:** observable goal, max turns, runnable check, cost cap; escalate hard
   blockers at once.
9. **Humans on irreversible or high-impact actions only.** Approving every step turns
   approvals into rubber stamps.
10. **Ask, don't assume,** users, data access, budgets and risk tolerance.

## Workflow

1. Read `agent-engineering/design.md` if it exists; find the current LLM calls, tools and
   data sources in the code. For a failing design, find the lowest stack layer that explains
   it (`references/the-agent-stack.md`) and fix it there.
2. Run the scoping interview (`references/scoping-interview.md`); get criteria confirmed.
3. Decompose the task as a competent human would. Label each step **code**, **LLM**,
   **tool** or **human**.
4. Draft 2–3 options with the decision rules, always including the simplest plausible one,
   and say why it falls short.
5. Any multi-agent option → run the checks in `references/multi-agent.md`. No single-agent
   baseline eval → build a minimal one first (`agent-evals`).
6. Choose framework and models (`references/model-and-framework-choice.md`).
7. Rate every tool low / medium / high risk with the tiers in `agent-guardrails` →
   `references/approvals-and-permissions.md` and place human checkpoints.
8. Send tools that write, spend or message outside to `agent-guardrails`. Flag the lethal
   trifecta: private data + untrusted content + a way out.
9. Write the design from `assets/design.md`: into `agent-engineering/design.md` if the
   project keeps one (offer once for a multi-session project), otherwise in your reply.
10. Define the first milestone: the smallest end-to-end slice plus the eval that proves it.
11. Next: `agent-tools`, `agent-rag` if it needs knowledge, `agent-prompting`,
    `agent-observability` with the first working version, then `agent-evals`.

## Decision rules

| If the task… | Choose |
|---|---|
| Is one input → one output, knowledge fits in prompt or retrieval | Single call |
| Has fixed, separable subtasks | Prompt chaining with code gates |
| Has distinct input categories or difficulty levels | Routing |
| Has independent subtasks, or needs several attempts | Parallelisation |
| Has subtasks you can't predict until you see the input | Orchestrator-workers |
| Has clear criteria and feedback measurably improves output | Evaluator-optimizer |
| Has a path that depends on intermediate results | Single agent loop |
| Takes real side effects users must approve first | Plan-then-execute |
| Needs breadth beyond one context, independent parts, high value | Lead + parallel readers, one writer |
| Has parts that share context or depend on each other (most coding) | Single agent + compaction |
| Has one agent failing on many overlapping tools | Fix tools (`agent-tools`), then split by domain |

## Anti-patterns

- Picking a multi-agent framework first and fitting the problem to it.
- An "agent" for a fixed pipeline.
- An LLM call on a graph edge where a deterministic check would do.
- Criteria like "accurate", "helpful", "fast" with no threshold or measurement.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- `agent-engineering/design.md`, including rejected alternatives and a decision log.
- The first milestone and its eval, as open items for `agent-evals`.

## References

- `references/the-agent-stack.md` — five layers, diagnosis order, loop essentials, when a
  graph is justified. Read when diagnosing a failure or before adding a layer.
- `references/scoping-interview.md` — question bank, vague → testable criteria. Read at step
  2 or whenever criteria are vague.
- `references/workflow-patterns.md` — choosing table and per-pattern pitfalls. Read
  when choosing or implementing a pattern.
- `references/multi-agent.md` — split checks, cost, topologies, delegation brief. Read before
  recommending more than one agent.
- `references/model-and-framework-choice.md` — framework checklist, model selection
  procedure, multi-model patterns. Read at step 6.
- `references/conversation-and-handoff.md` — clarify vs assume, refusals, progress, human
  handoff. Read for any user-facing agent.
