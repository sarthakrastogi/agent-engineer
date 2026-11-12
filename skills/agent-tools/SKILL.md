---
name: agent-tools
description: >-
  Designs and reviews the tools an LLM agent calls: tool/function definitions, JSON schemas,
  MCP servers, naming, consolidation, descriptions, error messages, response size, tool
  search, write-tool confirmation. Use when writing tool definitions, building or wrapping
  an MCP server, turning an API into tools, or when the agent picks the wrong tool, passes
  bad arguments, loops on errors or floods context with tool output. Not for architecture
  (agent-design), prompt wording (agent-prompting), memory (agent-context) or retrieval
  (agent-rag); write-tool permissions pair with agent-guardrails.
license: MIT
metadata:
  version: 0.4.0
---

# Agent tools

Design tools for a model that reads every word of the definition and nothing else.

## Core rules

1. **Tools for tasks, not endpoints.** One `schedule_event` beats `list_users` +
   `list_events` + `create_event`.
2. **No overlap.** If a human engineer can't say which tool applies, the model can't either.
   Agents handle 15+ distinct tools and fail with < 10 overlapping ones.
3. **The description matters most.** 3–4+ sentences: what, when, when not, parameters,
   returns, caveats.
4. **Make invalid calls unrepresentable.** Enums, required fields, unambiguous names
   (`user_id`), absolute paths. Never ask the model for values your code knows.
5. **Bounded, high-signal results.** Names over UUIDs, pagination and truncation with
   guidance, concise default with a detailed option.
6. **Errors are instructions:** what went wrong plus a correct call. No tracebacks, no empty
   success on failure.
7. **Separate reads from writes.** Writes are idempotent, narrow and gated by risk;
   authorisation is enforced in code, never by the model.
8. **10+ tools or > ~10K tokens of definitions → tool search**; ≤ ~20 loaded at once.
9. **Test tools with the agent** on realistic tasks; remove any tool whose removal doesn't
   hurt the eval.

## Workflow

1. Read the Tools section of `agent-engineering/design.md` and the tool code. List the tasks
   the agent must accomplish, not the APIs available.
2. Map tasks to a minimal set with the decision rules; note each merge or split.
3. Write each definition against `references/tool-definition-checklist.md`.
4. Shape responses and errors with `references/tool-responses-and-errors.md`.
5. Rate each tool's risk tier; add idempotency keys and confirmation per tier; check
   `agent-guardrails` if the agent also reads untrusted content.
6. Decide delivery (native tools, MCP server per `references/mcp-servers.md`, a CLI, or code
   execution) and loading (all up front vs tool search).
7. Test with the agent (checklist §7): 30+ tasks plus a held-out set. Read the transcripts
   where it chose wrong; have the agent rewrite the descriptions; re-run.
8. Record the tool list (name, purpose, read/write, risk, loading) in `design.md`. Log every
   definition change in `experiment-log.md`; descriptions are behaviour.
9. Next: `agent-prompting` for cross-tool policy, `agent-evals` for tool-selection and
   argument evals, `agent-guardrails` for write tools.

## Decision rules

| If… | Then |
|---|---|
| Two tools are always called in sequence | Merge them |
| Tools differ only by operation on one resource | One tool with an `action` enum, unless parameters diverge a lot |
| A tool's modes barely share parameters | Split it |
| The agent confuses two tools | "Use X instead when…" in both, rename; still confused → merge or remove one |
| A tool returns lists | Filters + pagination + total count; small default page |
| Output can exceed a few thousand tokens | Truncate with narrowing guidance; offer `concise` / `detailed` |
| The agent must join or aggregate many results | Do it in code and return the summary |
| Nested or format-sensitive input | 1–3 input examples in the definition |
| A tool has side effects | Apply its risk tier's controls (`agent-guardrails` → `references/approvals-and-permissions.md`) |
| Several agents or harnesses need the same tools | MCP server; one app → native tools |
| A good CLI exists and the agent has a sandboxed shell | Consider the CLI over a wrapper |

## Anti-patterns

- One tool per REST endpoint, producing dozens of overlapping tools.
- One-line descriptions, or descriptions in CAPS and MUSTs.
- `data: object` or `query: string` with no format, example or enum.
- Raw API JSON with internal IDs, nulls and unused metadata.
- Stack traces, `{"error": true}`, or `null` instead of a recoverable message.
- Unbounded list or file-read tools.
- Generic `run_sql` / `http_request` / shell tools where a narrow tool would do.
- Letting the model decide whether the user may perform an action.
- Editing the tool list mid-session instead of masking (`agent-context` →
  `references/prompt-caching.md`).
- Changing a description without re-running the eval.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- Tool definitions and handlers, or an MCP server, in the user's codebase.
- `design.md` → Architecture → Tools; `experiment-log.md` entries with eval deltas.

## References

- `references/tool-definition-checklist.md` — set design, counts, naming, schemas,
  descriptions, examples, read vs write, testing with the agent, review checklist. Read when
  writing or reviewing any definition.
- `references/tool-responses-and-errors.md` — response shaping, pagination, truncation, bulk
  work in code, error classes, retry and escalation. Read when results are large or the
  agent loops on errors.
- `references/mcp-servers.md` — when to use MCP, primitives, annotations, transports,
  server-side auth, testing. Read when building or connecting MCP servers.
