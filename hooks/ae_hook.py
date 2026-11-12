#!/usr/bin/env python3
"""agent-engineer hook runner — one script for every harness.

Usage:  ae_hook.py <event> --harness <claude|codex|cursor|gemini|opencode>

Events:
  session-start   inject a short LLM-project profile
  prompt-submit   in an LLM project, name the agent-* skill(s) the request needs
  pre-write       deny file writes that contain likely API keys
  post-write      remember behaviour-changing edits (prompts, tools, agent config); remind once
  post-bash       remember when an eval command ran (not a script written this session)
  stop            if behaviour changed and no eval ran, ask the agent (once) for honest evidence

Contract: stdlib only, no network, never writes to the user's repo, fails open (any internal
error → exit 0 with no output), and finishes well under the harness timeout.

Env switches: AE_HOOKS=off (all), AE_SECRET_GUARD=off, AE_EVAL_GATE=off, AE_DIR (artifact dir).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path

ARTIFACT_DIR = os.environ.get("AE_DIR", "agent-engineering")
MAX_FILE_BYTES = 256_000
STATE_TTL_S = 7 * 24 * 3600
CONTEXT_LINES_ABOVE = 40  # how far above an edit to look for the prompt/tool it belongs to
CONTEXT_LINES_BELOW = 5

# --------------------------------------------------------------------------- detection

LLM_LIBS = {
    # python
    "anthropic", "openai", "langchain", "langgraph", "llama-index", "llama_index", "crewai",
    "autogen", "ag2", "pydantic-ai", "pydantic_ai", "dspy", "dspy-ai", "haystack-ai",
    "semantic-kernel", "smolagents", "strands-agents", "google-genai", "google-adk",
    "litellm", "instructor", "mistralai", "cohere", "claude-agent-sdk", "openai-agents",
    "bedrock-agentcore", "mcp", "fastmcp",
    # js / ts
    "@anthropic-ai/sdk", "@anthropic-ai/claude-agent-sdk", "@langchain/core",
    "@langchain/langgraph", "llamaindex", "@mastra/core", "@openai/agents", "@google/genai",
    "@modelcontextprotocol/sdk", "@ai-sdk/openai", "@ai-sdk/anthropic",
}
TRACING_LIBS = {
    "opentelemetry-sdk", "opentelemetry-api", "@opentelemetry/sdk-node", "@opentelemetry/api",
    "openinference", "langfuse", "langsmith", "arize-phoenix", "braintrust",
    "mlflow", "weave", "opik", "traceloop-sdk", "openllmetry", "logfire", "agentops",
    "helicone", "ddtrace", "@arizeai/openinference-core", "@traceloop/node-server-sdk",
}
EVAL_LIBS = {
    "promptfoo", "deepeval", "ragas", "inspect-ai", "inspect_ai", "braintrust",
    "openevals", "agentevals", "trulens", "giskard", "autoevals",
}
DEP_FILES = (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "requirements.in",
    "setup.py", "setup.cfg", "Pipfile", "uv.lock", "poetry.lock", "package.json",
)
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs")
MAX_SCANNED_SOURCES = 40
PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)
JS_IMPORT = re.compile(r"(?:\bfrom|\brequire\(|\bimport\()\s*[\"']([^\"'.][^\"']*)[\"']")
# Salesforce Agentforce: no manifest dependency, so detect agent metadata in a DX project.
AGENTFORCE_DIRS = {"aiAuthoringBundles", "genAiPlannerBundles", "genAiPromptTemplates", "bots"}
MAX_SFDX_DIRS = 400
EVAL_PATHS = ("evals", "eval", "tests/evals", "tests/eval", "promptfooconfig.yaml",
              "promptfooconfig.yml")

SECRET_PATTERNS = [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"),
    ("AWS access key ID", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("Hugging Face token", r"\bhf_[A-Za-z0-9]{30,}\b"),
    ("Groq API key", r"\bgsk_[A-Za-z0-9]{40,}\b"),
    ("Stripe live key", r"\b[rs]k_live_[0-9A-Za-z]{24,}\b"),
    ("Private key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
]
PLACEHOLDER = re.compile(r"(x{6,}|your[_\-]?|example|placeholder|dummy|redacted|\.\.\.|<[^>]+>)",
                         re.I)

BEHAVIOUR_PATH = re.compile(r"(^|/)(prompts?|instructions|system[_\-]?prompts?)(/|\.|_|-)|"
                            r"\.(prompt|prompty|j2|jinja2?)$|"
                            r"\.agent$|\.genAiPromptTemplate-meta\.xml$|"
                            r"\.genAiPlannerBundle(-meta\.xml)?$|\.aiEvaluationDefinition-meta\.xml$",
                            re.I)
# Markers of prompt text. Also used on the lines around an edit, so keep it to prompts.
PROMPT_CONTENT = (
    r"system_prompt|systemPrompt|\b(?:[A-Z]+_)*SYSTEM(?:_[A-Z]+)*\s*=[^=]|\bsystem\s*[=:][^=]|"
    r"[\"']system[\"']\s*:|[\"']role[\"']\s*:\s*[\"']system[\"']|"
    r"role\s*=\s*[\"']system[\"']|\binstructions\s*=|"
    r"@tool\b|@function_tool\b|\binput_schema\b|\bfunction_declarations\b"  # tool descriptions
)
PROMPT_MARKER = re.compile(PROMPT_CONTENT)
BEHAVIOUR_CONTENT = re.compile(
    PROMPT_CONTENT + r"|\btools\s*=\s*\[|\bFunctionTool\b|"
    r"\bAgent\(|\bcreate_react_agent\b|\bStateGraph\(|\bmodel\s*=\s*[\"'][^\"']*"
    r"(claude|gpt|gemini|llama|mistral|sonnet|opus|haiku)",
)
SKIP_DIRS = ("node_modules/", ".git/", ".venv/", "venv/", "dist/", "build/", "__pycache__/",
             ".claude/", ".agents/", ".codex/", ".cursor/", ".gemini/", ".opencode/")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.M)
# A shell token that names an eval: `evals/`, `run_eval.py`, `npm run eval`, `promptfoo`.
# Delimited, so `retrieval.py` and `medieval` don't count.
EVAL_TOKEN = re.compile(r"(^|[/._\-:])(evals?|evaluate|evaluation)([/._\-:]|$)|"
                        r"^(promptfoo|deepeval|ragas|inspect|braintrust|openevals)$", re.I)
# Commands that only look at eval files, so they never count as running evals.
READ_ONLY_COMMANDS = {"cat", "less", "more", "head", "tail", "ls", "tree", "grep", "rg", "ag",
                      "find", "fd", "git", "echo", "printf", "wc", "sed", "awk", "diff", "bat",
                      "vi", "vim", "nano", "code", "open", "file", "stat", "mkdir", "touch",
                      "cp", "mv", "rm", "ln", "chmod", "eval", "source", "."}
COMMAND_WRAPPERS = {"sudo", "time", "env", "nice", "nohup", "exec", "command", "timeout"}
SHELL_SEPARATORS = re.compile(r"&&|\|\||[;|&\n]")

# Request wording → the skill that carries the checks for it. Order = priority.
SKILL_HINTS = [
    ("agentforce", r"agentforce|agent ?script|\.agent\b|einstein|prompt builder|"
                   r"prompt templates?|data cloud|data 360|omnistudio|integration procedure|"
                   r"salesforce|\bsfdx\b|\bsf agent\b"),
    ("agent-accuracy", r"hallucinat|makes? (things |stuff )?up|invent|made[- ]up|wrong answers?|"
                       r"inaccura|flaky|accuracy (dropped|is)"),
    # Guardrails: the agent gains the power to act, or untrusted input reaches it.
    ("agent-guardrails", r"(issue|process|make|send|execute|trigger|grant|approve)s? "
                         r"([\w-]+ ){0,2}(refunds?|payments?|transfers?|e?mails?|money)|"
                         r"\bdelete\b|write access|permissions?|prompt injection|jailbreak|"
                         r"untrusted|malicious|private data|\bpii\b"),
    ("agent-observability", r"\btrac(e|es|ing)\b|observab|monitor|"
                            r"(see|tell|know) what .{0,30} doing"),
    ("agent-tools", r"\btools?\b(?![/.\w])|function[- ]call|\bmcp\b"),
    ("agent-prompting", r"\bprompts?\b|instructions|\btone\b|friendl|system message"),
    ("agent-evals", r"\bevals?\b|is it better|(is|was) (the new|this|it) .*better|whether .*better|"
                    r"\bjudge\b|test set|benchmark"),
    ("agent-production", r"\bcosts?\b|cheaper|expensive|latency|\bslow\b|timeouts?|retr(y|ies)|"
                         r"rollout|deploy|model (switch|upgrade|migration)|switch .*model|"
                         r"loses? (all )?progress"),
    ("agent-context", r"\bmemory\b|forget|context window|compaction"),
    ("agent-rag", r"\brag\b|retriev|embedding|vector|chunk|knowledge base|our docs|"
                  r"documents|polic(y|ies)"),
    ("agent-design", r"(build|create|write|set up|start) (me )?(a|an|a new|new) ([\w-]+ ){0,3}"
                     r"(agent|assistant|chatbot|bot)\b|multi-agent|architecture|from scratch"),
]
MAX_HINTED_SKILLS = 2


def read_text(p: Path) -> str:
    try:
        if p.is_file() and p.stat().st_size <= MAX_FILE_BYTES:
            return p.read_text(errors="ignore")
    except OSError:
        pass
    return ""


def dep_names(root: Path) -> set[str]:
    """Lower-cased dependency names found in common manifest files (top level + one down)."""
    found: set[str] = set()
    dirs = [root]
    try:
        dirs += [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")
                 and d.name not in ("node_modules", "venv", "dist", "build")][:40]
    except OSError:
        pass
    for d in dirs:
        for name in DEP_FILES:
            text = read_text(d / name)
            if not text:
                continue
            if name == "package.json":
                try:
                    pkg = json.loads(text)
                    for key in ("dependencies", "devDependencies", "peerDependencies"):
                        found.update(k.lower() for k in (pkg.get(key) or {}))
                except ValueError:
                    pass
            else:
                found.update(m.lower() for m in re.findall(r"[A-Za-z0-9_.\-@/]+", text))
    return found


def import_names(root: Path) -> set[str]:
    """Packages imported by top-level source files, for projects with no dependency manifest."""
    found: set[str] = set()
    try:
        files = [p for p in root.iterdir() if p.suffix in SOURCE_SUFFIXES][:MAX_SCANNED_SOURCES]
    except OSError:
        return found
    for f in files:
        text = read_text(f)
        for mod in PY_IMPORT.findall(text):
            found.add(mod.split(".")[0].lower().replace("_", "-"))
            found.add(mod.lower().replace(".", "-").replace("_", "-"))  # google.genai → google-genai
        for pkg in JS_IMPORT.findall(text):
            parts = pkg.lower().split("/")
            found.add("/".join(parts[:2]) if pkg.startswith("@") else parts[0])
    return found


def matches(deps: set[str], libs: set[str]) -> list[str]:
    hits = set()
    for dep in deps:
        base = re.split(r"[\[<>=~!;]", dep, maxsplit=1)[0]
        hits.update(lib for lib in libs if base == lib or base.startswith(lib + "-"))
    return sorted(hits)


def is_agentforce_project(root: Path) -> bool:
    """A Salesforce DX project holding agent, planner, prompt-template or bot metadata."""
    if not (root / "sfdx-project.json").is_file():
        return False
    seen = 0
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")
                       and d not in ("node_modules", "venv", "dist", "build")]
        if AGENTFORCE_DIRS & set(dirnames):
            return True
        seen += 1
        if seen > MAX_SFDX_DIRS:
            break
    return False


def profile(root: Path) -> dict:
    deps = dep_names(root)
    if not matches(deps, LLM_LIBS):
        deps |= import_names(root)
    art = root / ARTIFACT_DIR
    return {
        "llm": matches(deps, LLM_LIBS) or is_agentforce_project(root),
        "tracing": matches(deps, TRACING_LIBS),
        "eval_libs": matches(deps, EVAL_LIBS),
        "eval_paths": [p for p in EVAL_PATHS if (root / p).exists()],
        "artifacts": sorted(p.name for p in art.iterdir()) if art.is_dir() else None,
    }


def find_secret(text: str) -> str | None:
    for label, pattern in SECRET_PATTERNS:
        for m in re.finditer(pattern, text):
            window = text[max(0, m.start() - 20): m.end() + 5]
            if not PLACEHOLDER.search(m.group(0)) and "EXAMPLE" not in window:
                return label
    return None


def is_behaviour_change(path: str, text: str, marker: re.Pattern = BEHAVIOUR_CONTENT) -> bool:
    norm = path.replace("\\", "/")
    if any(s in norm for s in SKIP_DIRS) or f"{ARTIFACT_DIR}/" in norm:
        return False
    if BEHAVIOUR_PATH.search(norm):
        return True
    if norm.endswith((".md", ".txt", ".rst", ".lock", ".json")) and "prompt" not in norm.lower():
        return False
    return bool(marker.search(text))


def edit_context(path: Path, written: str) -> str:
    """The lines just above and around an edit, read back from disk after the write.

    A wording-only prompt edit ("concise" → "friendly") doesn't contain the `SYSTEM_PROMPT =`
    or `system=` that makes it a prompt edit; the surrounding lines do.
    """
    text = read_text(path)
    if not text:
        return ""
    at = text.find(written.strip()) if written.strip() else -1
    if at < 0:  # multi-edit or patch: anchor on the first added line found in the file
        for line in written.splitlines():
            s = line.lstrip("+").strip()
            if len(s) >= 8 and s in text:
                at = text.find(s)
                break
    if at < 0:
        return ""
    lines = text.splitlines()
    row = text.count("\n", 0, at)
    return "\n".join(lines[max(0, row - CONTEXT_LINES_ABOVE): row + CONTEXT_LINES_BELOW])


def is_eval_command(cmd: str) -> bool:
    in_eval_dir = False
    for segment in SHELL_SEPARATORS.split(cmd):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        while tokens and ("=" in tokens[0] or tokens[0] in COMMAND_WRAPPERS):
            tokens = tokens[1:]
        if not tokens:
            continue
        program = os.path.basename(tokens[0])
        named = any(EVAL_TOKEN.search(t) for t in tokens)
        if program == "sf" and tokens[1:3] == ["agent", "test"] and \
                len(tokens) > 3 and tokens[3] in ("run", "resume"):
            return True
        if program == "cd":
            in_eval_dir = named
        elif program not in READ_ONLY_COMMANDS and (named or in_eval_dir):
            return True
    return False


# --------------------------------------------------------------------------- state

def state_path(session: str) -> Path:
    d = Path(tempfile.gettempdir()) / "agent-engineer"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", session)[:120] or "default"
    return d / f"{safe}.json"


def load_state(session: str) -> dict:
    p = state_path(session)
    try:
        if time.time() - p.stat().st_mtime < STATE_TTL_S:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return {"changes": [], "written": [], "evals": 0, "reminded": False, "gated": False,
            "hinted": []}


def save_state(session: str, state: dict) -> None:
    try:
        state_path(session).write_text(json.dumps(state))
    except OSError:
        pass


# --------------------------------------------------------------------------- payloads

def strings_in(obj, keys=("content", "new_string", "newString", "new_str", "text", "patch",
                          "input", "contents", "file_text", "new_source")) -> str:
    """Collect text being written, across harness tool-input shapes."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in keys:
                out.append(v)
            elif isinstance(v, (dict, list)):
                out.append(strings_in(v, keys))
    elif isinstance(obj, list):
        out.extend(strings_in(v, keys) for v in obj)
    return "\n".join(s for s in out if s)


def first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


class Event:
    def __init__(self, payload: dict):
        self.raw = payload
        self.session = str(first(payload, "session_id", "sessionId", "conversation_id",
                                 "thread_id", default=f"ppid-{os.getppid()}"))
        self.cwd = Path(first(payload, "cwd", "workspace_root", default=os.getcwd()))
        roots = payload.get("workspace_roots")
        if isinstance(roots, list) and roots:
            self.cwd = Path(roots[0])
        self.tool_input = first(payload, "tool_input", "toolInput", "args", "input",
                                default={}) or {}
        if isinstance(self.tool_input, str):
            try:
                self.tool_input = json.loads(self.tool_input)
            except ValueError:
                self.tool_input = {"input": self.tool_input}
        ti = self.tool_input if isinstance(self.tool_input, dict) else {}
        self.file_path = str(first(ti, "file_path", "filePath", "path", "absolute_path",
                                   "notebook_path",
                                   default=first(payload, "file_path", default="")))
        self.command = str(first(ti, "command", "cmd", default=first(payload, "command",
                                                                    default="")))
        if isinstance(ti.get("command"), list):
            self.command = " ".join(map(str, ti["command"]))
        self.write_text = strings_in(self.tool_input) or strings_in(payload.get("edits", []))
        if "*** Begin Patch" in self.command:  # Codex apply_patch passes the patch as a command
            self.write_text = f"{self.write_text}\n{self.command}"
        self.file_paths = [self.file_path] if self.file_path else []
        self.file_paths += [m.strip() for m in PATCH_FILE.findall(self.write_text)
                            if m.strip() not in self.file_paths]
        self.prompt = str(first(payload, "prompt", "user_prompt", default=""))
        self.stop_active = bool(payload.get("stop_hook_active") or payload.get("loop_count"))


# --------------------------------------------------------------------------- outputs

def emit(harness: str, event: str, *, context: str = "", deny: str = "",
         block: str = "") -> None:
    """Print the harness-specific output. Exactly one of context/deny/block is set."""
    out: dict | None = None
    if harness in ("claude", "codex"):
        name = {"session-start": "SessionStart", "prompt-submit": "UserPromptSubmit",
                "pre-write": "PreToolUse", "post-write": "PostToolUse",
                "post-bash": "PostToolUse"}.get(event)
        if deny:
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "permissionDecisionReason": deny}}
        elif block:
            out = {"decision": "block", "reason": block}
        elif context and name:
            out = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}
    elif harness == "gemini":
        name = {"session-start": "SessionStart", "post-write": "AfterTool",
                "post-bash": "AfterTool"}.get(event)
        if deny:
            out = {"decision": "deny", "reason": deny}
        elif block:  # AfterAgent: "deny" makes Gemini retry with the reason as a new prompt
            out = {"decision": "deny", "reason": block}
        elif context and name:
            out = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}
    elif harness == "cursor":
        if deny:
            out = {"permission": "deny", "user_message": deny, "agent_message": deny}
        elif block:
            out = {"followup_message": block}
        elif context:
            out = {"additional_context": context}
    else:  # opencode plugin and anything else: plain JSON the caller interprets
        out = {"context": context or None, "deny": deny or None, "block": block or None}
    if out:
        sys.stdout.write(json.dumps(out))


# --------------------------------------------------------------------------- handlers

def on_session_start(ev: Event) -> dict:
    p = profile(ev.cwd)
    if not p["llm"] and p["artifacts"] is None:
        return {}
    lines = ["[agent-engineer] Project profile, for tasks that change how the LLM agent "
             "behaves (prompts, tools, retrieval, evals, tracing). Ignore it for unrelated work."]
    if p["llm"]:
        lines.append(f"- LLM libraries: {', '.join(p['llm'][:8])}")
    lines.append(f"- Tracing: {', '.join(p['tracing']) if p['tracing'] else 'none detected'}")
    evals = p["eval_libs"] + p["eval_paths"]
    lines.append(f"- Evals: {', '.join(evals) if evals else 'none detected'}")
    if p["artifacts"] is not None:
        lines.append(f"- {ARTIFACT_DIR}/: {', '.join(p['artifacts']) or 'empty'} "
                     "— read these before changing the agent.")
    gaps = []
    if not p["tracing"]:
        gaps.append("no tracing (see agent-observability)")
    if not evals:
        gaps.append("no evals (see agent-evals)")
    if gaps:
        lines.append(f"- Gaps: {'; '.join(gaps)}. A behaviour change here can't be shown to "
                     "be an improvement; say so rather than claim it.")
    return {"context": "\n".join(lines)}


def on_pre_write(ev: Event) -> dict:
    if os.environ.get("AE_SECRET_GUARD") == "off" or not ev.write_text:
        return {}
    target = ev.file_paths[0] if ev.file_paths else ""
    base = os.path.basename(target)
    if base.startswith(".env") and not re.search(r"example|sample|template", base, re.I):
        return {}
    label = find_secret(ev.write_text)
    if not label:
        return {}
    return {"deny": f"[agent-engineer secret-guard] This write to {target or 'a file'} contains "
                    f"what looks like a secret ({label}). Read it from an environment variable "
                    "or secret manager instead, and put real values only in a git-ignored "
                    ".env. If this is a real key, it is already in this session's transcript: "
                    "tell the user to revoke and reissue it. Set AE_SECRET_GUARD=off to bypass "
                    "if this is a false positive."}


def skills_for(prompt: str) -> list[str]:
    text = prompt.lower()
    return [skill for skill, pattern in SKILL_HINTS if re.search(pattern, text)]


def on_prompt_submit(ev: Event) -> dict:
    """Skills load from their descriptions on questions, but an imperative task ("add a
    tool", "build an agent") goes straight to code. Name the skill next to the request."""
    if not ev.prompt or os.environ.get("AE_SKILL_HINTS") == "off":
        return {}
    st = load_state(ev.session)
    if "llm_project" not in st:
        p = profile(ev.cwd)
        st["llm_project"] = bool(p["llm"] or p["artifacts"] is not None)
    skills = [s for s in skills_for(ev.prompt) if s not in st["hinted"]][:MAX_HINTED_SKILLS]
    if not st["llm_project"] or not skills:
        save_state(ev.session, st)
        return {}
    st["hinted"] += skills
    save_state(ev.session, st)
    names = " and ".join(f"`{s}`" for s in skills)
    return {"context": f"[agent-engineer] This request changes the LLM agent. Load the {names} "
                       f"skill{'s' if len(skills) > 1 else ''} before writing code: "
                       f"{'they carry' if len(skills) > 1 else 'it carries'} the checks to "
                       "apply. Skip this if the request turns out not to touch the agent."}


def on_post_write(ev: Event) -> dict:
    st = load_state(ev.session)
    st["written"] += [p for p in ev.file_paths if p not in st["written"]]
    changed = [p for p in ev.file_paths
               if is_behaviour_change(p, ev.write_text)
               or is_behaviour_change(p, edit_context(ev.cwd / p, ev.write_text), PROMPT_MARKER)]
    msg = {}
    if changed:
        st["changes"] += [p for p in changed if p not in st["changes"]]
        if not st["reminded"]:
            st["reminded"] = True
            msg = {"context": f"[agent-engineer] {changed[0]} changes agent behaviour (prompt, "
                              "tools or agent config). Only a real eval run shows whether that "
                              "helped; report the result, or say it's unevaluated."}
    save_state(ev.session, st)
    return msg


def runs_own_script(cmd: str, written: list[str], cwd: Path) -> bool:
    """True if the command runs a file written this session (a script the agent just made
    is not the project's eval suite: it may be a mock or a 'prediction')."""
    for path in written:
        p = Path(path)
        names = {path, p.name}
        try:
            names.add(str(p.resolve().relative_to(cwd.resolve())))
        except ValueError:
            pass
        if any(n and n in cmd for n in names):
            return True
    return False


def on_post_bash(ev: Event) -> dict:
    if ev.command and is_eval_command(ev.command):
        st = load_state(ev.session)
        if not runs_own_script(ev.command, st["written"], ev.cwd):
            st["evals"] += 1
            save_state(ev.session, st)
    return {}


def on_stop(ev: Event) -> dict:
    if os.environ.get("AE_EVAL_GATE") == "off" or ev.stop_active:
        return {}
    st = load_state(ev.session)
    if not st["changes"] or st["evals"] or st["gated"]:
        return {}
    st["gated"] = True
    save_state(ev.session, st)
    files = ", ".join(st["changes"][:5])
    return {"block": f"[agent-engineer eval-gate] Behaviour-changing files were edited this "
                     f"session ({files}) and the project's evals never ran against the real "
                     "model. Before you finish, make your final message honest about evidence: "
                     "mocked, simulated, keyword-based or predicted results and offline unit "
                     "tests show the code works, not that the agent got better, so don't call "
                     "the change better, fixed or equivalent on that basis. Say it's unevaluated, "
                     "why, and the one command that would measure it. Put this in your reply, "
                     "not in new files. (Fires once per session; AE_EVAL_GATE=off disables.)"}


HANDLERS = {"session-start": on_session_start, "prompt-submit": on_prompt_submit,
            "pre-write": on_pre_write,
            "post-write": on_post_write, "post-bash": on_post_bash, "stop": on_stop}


def main(argv: list[str]) -> int:
    if os.environ.get("AE_HOOKS") == "off" or not argv or argv[0] not in HANDLERS:
        return 0
    event = argv[0]
    harness = argv[argv.index("--harness") + 1] if "--harness" in argv else "claude"
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    try:
        result = HANDLERS[event](Event(payload if isinstance(payload, dict) else {}))
        if result:
            emit(harness, event, **result)
    except Exception:  # fail open: a broken hook must never break the user's session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
