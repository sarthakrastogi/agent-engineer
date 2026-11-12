---
name: agent-context
description: >-
  Engineer what an agent's context window holds over long or multi-turn runs: ordering and
  token budgets, just-in-time loading, tool-result clearing and compaction, scratchpads,
  progress files, long-term memory, subagent isolation, prompt-cache hit rate, and measuring
  context from traces. Use when the agent forgets instructions or decisions, degrades or
  loops as runs grow, hits context limits, has token bloat or low cache hits, needs memory
  across sessions, or runs for hours. Not for prompt wording (agent-prompting), document
  search (agent-rag), one tool's output size (agent-tools), or overall cost and latency
  (agent-production).
license: MIT
metadata:
  version: 0.4.0
---

# Agent context

Give the model the smallest set of high-signal tokens for the next step, keep the prefix
cacheable, and hold long-run state outside the window. Every model degrades as input
grows, well before its limit; a bigger window does not fix it.

## Core rules

1. **Measure before changing:** per-turn tokens by component, cache hit rate, and the turn
   where quality drops, from traces.
2. **Cut low-signal tokens**, not tokens blindly. Report quality at a token budget.
3. **Load just in time.** Keep references (paths, IDs, queries) and fetch content with
   tools. Pre-load only what every run needs.
4. **Stable prefix, append-only history.** No timestamps or IDs at the top, deterministic
   serialisation, no edits to past turns, mask tools instead of adding or removing them.
5. **Compression is restorable:** drop content, keep the pointer. Tell the compactor what
   must survive.
6. **Keep errors in context; compact resolved ones.** Purge unsupported "facts" from
   summaries and goals before they poison later turns.
7. **Long-run state lives outside the window:** progress file, JSON for structured state,
   git for checkpoints.
8. **Isolate exploration in subagents** that return 1–2K-token summaries plus file
   references, not transcripts.
9. **Context changes are behaviour changes:** long-trajectory evals before and after,
   logged in `experiment-log.md`.

## Workflow

1. **Profile** real long runs: per-turn input split by system / tools / history / tool
   results / retrieved, plus cached and output tokens; note where quality drops
   (`references/context-budget-and-ordering.md`). No tracing → `agent-observability`.
2. **Diagnose:** poisoning, distraction, confusion, clash, lost instructions or cost.
   Count each across traces.
3. **Budget and order:** a cap per component, layout stable → volatile.
4. **Pick one lever** from the table below and change one thing at a time.
5. **Cache check** (`references/prompt-caching.md`): hit rate before and after.
6. **Runs that outlive a window:** harness in `references/long-running-agents.md`.
7. **Record** in `design.md` → "Context strategy": what's in the system prompt, what's
   fetched just in time, compaction trigger, memory stores.
8. **Evaluate** with long cases: task success, tokens per task, cache hit rate.
9. **Next:** `agent-evals` for trajectory graders, `agent-production` for cost and latency,
   `agent-guardrails` if memory stores untrusted content.

## Decision rules

| If | Then |
|---|---|
| Bulky, stale tool outputs fill history | Tool-result clearing first: keep the call, drop the payload, leave a pointer. |
| Long conversational back-and-forth | Compaction (server-side if offered) with a must-preserve list. |
| Iterative work with milestones | A progress/notes file the agent reads and rewrites. |
| Broad, parallel investigation | Subagents with clean windows returning condensed summaries. |
| Work spans windows or sessions | Fresh window + progress file + git + JSON task list. |
| Static knowledge < ~200K tokens and below your measured quality ceiling | In the prompt, cached. Larger or changing → `agent-rag` or agentic search. |
| Tool definitions dominate input | Tool search / deferred loading (`agent-tools` → `references/tool-definition-checklist.md`); mask per state. |
| Agent repeats past actions or drifts | Shorten history; vary serialisation of observations. |
| Instructions forgotten late in a run | Recite the plan (`todo.md`) at the end of context; restate format rules every 3–5 turns; enforce in code. |
| Low cache hit rate | Find the prefix mutation: timestamp, reordered JSON, changed tools or system prompt. |
| Memory across sessions or users | Scoped store with explicit read/write tools; stored text is untrusted input. |
| Two corrections failed in a session | Clear and restart with a better brief. |

## Anti-patterns

- Loading every document, tool and past message up front "just in case".
- A timestamp or request ID in the first line of the system prompt.
- Editing the tool list mid-run: breaks the cache and orphans earlier tool calls.
- Summaries that drop paths, IDs, decisions or open questions.
- Wiping failed actions from history, so the agent repeats them.
- Subagents returning full transcripts to the lead.
- Editing earlier turns or thinking blocks to "clean up".
- Markdown for structured state the model rewrites; use JSON.
- Memory with no scope, expiry or provenance.
- Switching to a longer-context model instead of fixing what goes into the window.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- `design.md` → "Context strategy".
- Per-turn token and cache metrics and a compaction marker in `observability.md`.
- `experiment-log.md` entries per context change, with eval and token deltas.

## References

- `references/context-budget-and-ordering.md` — layout order, always-on vs on-demand,
  two-stage selection, budgets, failure modes, trace metrics. Read first when profiling.
- `references/memory-and-compaction.md` — clearing, compaction prompt, errors, scratchpads,
  long-term memory. Read when history grows or memory is needed.
- `references/prompt-caching.md` — cache-friendly layout, tool masking, provider rules,
  hit-rate monitoring, cache breakers. Read when per-turn cost or latency is high.
- `references/long-running-agents.md` — multi-window harness, state files, bounds,
  evaluating long runs. Read when runs exceed one window or ~50 tool calls.
