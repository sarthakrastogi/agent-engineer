# Harness support

What each harness gets, and how. `install.sh` is a thin wrapper around `scripts/install.py`;
they take the same flags. Harness formats change fast; check the harness's docs before
relying on an edge case.

## Matrix

| | Claude Code | Codex CLI | Cursor | OpenCode | Gemini CLI |
|---|---|---|---|---|---|
| **Install** | plugin marketplace | plugin + `install.py` for agents | plugin, or `install.py` | `install.py` | `install.py` |
| **Skills** | `skills/` via plugin | `skills/` via plugin, or `.agents/skills` | plugin, or `.agents/skills` | `.agents/skills` | `.agents/skills` |
| **Subagents** | `agents/*.md` (canonical) | `adapters/codex/agents/*.toml` — plugins can't ship agents, so `install.py` links them | `adapters/cursor/agents/` | `adapters/opencode/agents/` | `adapters/gemini/agents/` |
| **Hooks** | `hooks/hooks.json` | `hooks/codex.json` (must be trusted in `/hooks`) | `hooks/cursor.json` | `adapters/opencode/agent-engineer.js` (experimental, OpenCode V2) | `adapters/gemini/hooks.json`, merged into `settings.json` |
| **session-start** | ✅ | ✅ | ✅ | ✅ system-prompt transform | ✅ |
| **skill hint** (prompt-submit) | ✅ UserPromptSubmit | ✅ UserPromptSubmit | — | — | — |
| **secret-guard** | ✅ | ✅ (`apply_patch`) | ✅ | ✅ | ✅ |
| **behaviour-change** | ✅ | ✅ | ✅ | ✅ appended to tool result | ✅ |
| **eval-gate** | ✅ Stop | ✅ Stop | ✅ `followup_message` | ⚠️ `session.idle` → prompt (unofficial) | ✅ AfterAgent |

Users get behaviour through skills and the session-start hook.

## How the pieces map

- **One hook script.** Every harness config calls `hooks/ae_hook.py <event> --harness <h>`.
  The script normalises each harness's stdin payload and prints that harness's output shape:
  - Claude, Codex: `hookSpecificOutput.additionalContext`, `permissionDecision: deny`,
    `decision: block`.
  - Gemini: same context shape; `decision: deny` for BeforeTool and AfterAgent.
  - Cursor: flat `additional_context`, `permission: deny`, `followup_message`.
  - OpenCode: generic `{context, deny, block}` interpreted by the JS plugin.
- **One subagent source.** `agents/*.md` (Claude format) → `scripts/build_adapters.py` →
  `adapters/<harness>/agents/`. Tool allowlists are translated: Codex `sandbox_mode`,
  Cursor `readonly`, OpenCode `permission`, Gemini snake_case `tools`.
- **Why no `gemini-extension.json` at the root.** Gemini extensions auto-load
  `hooks/hooks.json`, which is Claude's file (Claude auto-discovers the same path). Shipping
  both would run Claude-format hooks under Gemini. Gemini installs via `install.py` instead.

## Known gaps

- **Codex:** subagents need `install.py`; hooks need manual trust after each change.
  `codex plugin marketplace add` (reads `.claude-plugin/marketplace.json`, prefers
  `.codex-plugin/plugin.json`) is untested; `install.sh --harness codex` is the tested path.
- **Cursor:** imports `~/.claude` hooks and agents. If you also installed for Claude at user
  scope (not via plugin), hooks run twice in Cursor.
- **OpenCode:** no official Stop-hook contract; V2 plugin API not yet supported.
- **Windows:** hooks call `python3`; set up a `python3` alias or edit the command.
- **Skill size:** Codex truncates skills injected inline (`$skill`) at 8,000 bytes; every
  `SKILL.md` stays under 8,000 bytes whole-file, body ≤ 7,400 (enforced by `scripts/validate.py` on the `eval-results` branch).
