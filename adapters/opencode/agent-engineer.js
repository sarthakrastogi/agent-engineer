// agent-engineer plugin for OpenCode V2.
//
// OpenCode has no shell-hook contract, so this plugin calls the same hooks/ae_hook.py the
// other harnesses use and maps its generic JSON ({context, deny, block}) onto OpenCode's
// in-process hooks:
//   session-start → ctx.session.hook("context")   (append profile to the system prompt)
//   pre-write     → ctx.tool.hook("execute.before") (throw to deny)
//   post-write    → ctx.tool.hook("execute.after")  (append reminder to the tool result)
//   post-bash     → ctx.tool.hook("execute.after")
//   stop          → ctx.event.subscribe() on "session.idle" (send the eval-gate prompt once)
// Every call fails open: if Python or the hook breaks, the session carries on.
//
// V2 note: the plugin must default-export { id, setup(ctx) }. A named export (the V1
// contract) fails to load with "Plugin must export a default definition with an id and an
// effect or setup function".

import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const HOOK = join(ROOT, "hooks", "ae_hook.py");
// Windows has no `python3` on PATH (it is a Microsoft Store stub that exits 9009), so the
// default interpreter is platform-dependent. AE_PYTHON overrides it.
const PYTHON = process.env.AE_PYTHON || (process.platform === "win32" ? "python" : "python3");
const WRITE_TOOLS = new Set(["write", "edit", "patch", "apply_patch", "multiedit"]);
// OpenCode V2 names the shell tool "shell" (V1 called it "bash"). Both are accepted so the
// post-bash hook fires either way.
const SHELL_TOOLS = new Set(["shell", "bash"]);

function runHook(event, payload) {
  try {
    const r = spawnSync(PYTHON, [HOOK, event, "--harness", "opencode"], {
      input: JSON.stringify(payload),
      encoding: "utf8",
      timeout: 5000,
    });
    return r.status === 0 && r.stdout ? JSON.parse(r.stdout) : {};
  } catch {
    return {};
  }
}

// Append text to a tool result without losing the parts the tool already produced.
function appendToResult(result, text) {
  const content = result?.content;
  if (typeof content === "string") return { ...result, content: `${content}\n\n${text}` };
  const parts = Array.isArray(content) ? [...content] : [];
  parts.push({ type: "text", text });
  return { ...result, content: parts };
}

export default {
  id: "agent-engineer",

  async setup(ctx) {
    const directory = ctx.location?.directory ?? process.cwd();
    const profiles = new Map(); // sessionID -> session-start context (or null)
    const controller = new AbortController();

    await ctx.session.hook("context", async (event) => {
      const sid = event.sessionID ?? "default";
      if (!profiles.has(sid)) {
        profiles.set(sid, runHook("session-start", { session_id: sid, cwd: directory }).context ?? null);
      }
      const profile = profiles.get(sid);
      if (profile && Array.isArray(event.system)) {
        event.system.push({ type: "text", text: profile });
      }
    });

    // The secret guard covers the write tools only. A shell command that merely mentions a
    // key pattern (rg "sk-ant-", a docs example) is legitimate work, and the hook script
    // does not scan commands for secrets either — blocking them here would be a false
    // positive machine.
    await ctx.tool.hook("execute.before", async (event) => {
      if (!WRITE_TOOLS.has(event.tool)) return;
      const r = runHook("pre-write", {
        session_id: event.sessionID,
        cwd: directory,
        tool_input: event.input ?? {},
      });
      if (r.deny) throw new Error(r.deny);
    });

    await ctx.tool.hook("execute.after", async (event) => {
      const isShell = SHELL_TOOLS.has(event.tool);
      if (!isShell && !WRITE_TOOLS.has(event.tool)) return;
      const name = isShell ? "post-bash" : "post-write";
      const r = runHook(name, {
        session_id: event.sessionID,
        cwd: directory,
        tool_input: event.input ?? {},
      });
      if (!r.context || event.status !== "completed") return;
      event.result = appendToResult(event.result, r.context);
    });

    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        if (event?.type !== "session.idle") continue;
        const sid = event.data?.sessionID;
        if (!sid) continue;
        const r = runHook("stop", { session_id: sid, cwd: directory });
        if (!r.block) continue;
        try {
          await ctx.session.prompt({ sessionID: sid, text: r.block });
        } catch {
          // fail open
        }
      }
    })();

    return () => controller.abort();
  },
};
