#!/usr/bin/env python3
"""Install agent-engineer into a harness without its plugin system.

  python3 scripts/install.py --harness codex                 # into the current project
  python3 scripts/install.py --harness gemini --scope user   # into your home config
  python3 scripts/install.py --harness opencode --uninstall
  add --dry-run to print what would change, --no-hooks / --no-agents to skip parts

Skills and subagents are symlinked from this checkout (so `git pull` updates them).
Hook entries are merged into the harness's hook config; existing entries are kept, and a
.bak copy is written before any config file is modified. Re-running is idempotent.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
MARK = "ae_hook.py"  # identifies our hook entries

# harness -> scope -> (skills dir, agents dir, agents source, hooks file)
LAYOUT = {
    "claude": {
        "project": (".claude/skills", ".claude/agents", "agents", ".claude/settings.json"),
        "user": ("~/.claude/skills", "~/.claude/agents", "agents", "~/.claude/settings.json"),
    },
    "codex": {
        "project": (".agents/skills", ".codex/agents", "adapters/codex/agents", ".codex/hooks.json"),
        "user": ("~/.agents/skills", "~/.codex/agents", "adapters/codex/agents", "~/.codex/hooks.json"),
    },
    "cursor": {
        "project": (".agents/skills", ".cursor/agents", "adapters/cursor/agents", ".cursor/hooks.json"),
        "user": ("~/.cursor/skills", "~/.cursor/agents", "adapters/cursor/agents", "~/.cursor/hooks.json"),
    },
    "opencode": {
        "project": (".agents/skills", ".opencode/agents", "adapters/opencode/agents", ".opencode/plugins"),
        "user": ("~/.config/opencode/skills", "~/.config/opencode/agents", "adapters/opencode/agents",
                 "~/.config/opencode/plugins"),
    },
    "gemini": {
        "project": (".agents/skills", ".gemini/agents", "adapters/gemini/agents", ".gemini/settings.json"),
        "user": ("~/.agents/skills", "~/.gemini/agents", "adapters/gemini/agents", "~/.gemini/settings.json"),
    },
}
NEXT_STEPS = {
    "claude": "Prefer the plugin: /plugin marketplace add <repo> then /plugin install "
              "agent-engineer@agent-engineer. Don't use both, or hooks run twice.",
    "codex": "Open Codex and approve the new hooks in /hooks (Codex skips untrusted hooks).",
    "cursor": "Cursor also imports ~/.claude hooks and agents — don't install for both "
              "claude --scope user and cursor, or hooks run twice.",
    "opencode": "The OpenCode plugin is experimental (OpenCode has no Stop hook contract).",
    "gemini": "Gemini asks for consent the first time a skill activates; hooks show a "
              "fingerprint warning until you accept them.",
}


class Installer:
    def __init__(self, harness: str, scope: str, project: Path, dry: bool):
        self.harness, self.scope, self.dry = harness, scope, dry
        self.base = project
        self.actions: list[str] = []

    def path(self, p: str) -> Path:
        return Path(os.path.expanduser(p)) if p.startswith("~") else self.base / p

    def say(self, msg: str) -> None:
        self.actions.append(msg)
        print(("[dry-run] " if self.dry else "") + msg)

    # -- links -------------------------------------------------------------------------
    def link(self, src: Path, dst: Path) -> None:
        if dst.is_symlink() and dst.resolve() == src.resolve():
            return
        if dst.exists() or dst.is_symlink():
            self.say(f"skip {dst} (exists and is not ours)")
            return
        self.say(f"link {dst} -> {src}")
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src, target_is_directory=src.is_dir())

    def unlink_ours(self, d: Path) -> None:
        if not d.is_dir():
            return
        for p in d.iterdir():
            if p.is_symlink() and str(Path(os.readlink(p))).startswith(str(ROOT)):
                self.say(f"remove {p}")
                if not self.dry:
                    p.unlink()

    # -- hook config -------------------------------------------------------------------
    def load_json(self, f: Path) -> dict:
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text() or "{}")
        except ValueError:
            sys.exit(f"error: {f} is not valid JSON — fix it or pass --no-hooks")

    def save_json(self, f: Path, data: dict) -> None:
        self.say(f"update {f}")
        if self.dry:
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists():
            shutil.copy2(f, f.with_name(f.name + ".bak"))
        f.write_text(json.dumps(data, indent=2) + "\n")

    def our_hooks(self) -> dict:
        if self.harness == "gemini":
            text = (ROOT / "adapters/gemini/hooks.json").read_text().replace("{AE_ROOT}", str(ROOT))
            return json.loads(text)
        src = {"claude": "hooks/hooks.json", "codex": "hooks/codex.json",
               "cursor": "hooks/cursor.json"}[self.harness]
        text = (ROOT / src).read_text()
        for var in ("${CLAUDE_PLUGIN_ROOT}", "${PLUGIN_ROOT}"):
            text = text.replace(var, str(ROOT))
        text = text.replace("python3 ./hooks/", f"python3 {ROOT}/hooks/")
        return json.loads(text)["hooks"]

    @staticmethod
    def strip_ours(events: dict) -> dict:
        out = {}
        for event, entries in events.items():
            kept = []
            for e in entries:
                if MARK in json.dumps(e):
                    continue
                kept.append(e)
            if kept:
                out[event] = kept
        return out

    def merge_hooks(self, f: Path, remove: bool) -> None:
        data = self.load_json(f)
        current = data.get("hooks", {})
        merged = self.strip_ours(current)
        if not remove:
            for event, entries in self.our_hooks().items():
                merged.setdefault(event, []).extend(entries)
        if merged == current:
            return
        data["hooks"] = merged
        if self.harness == "cursor":
            data.setdefault("version", 1)
        self.save_json(f, data)

    # -- opencode plugin ---------------------------------------------------------------
    def opencode_plugin(self, plugins_dir: Path, remove: bool) -> None:
        dst = plugins_dir / "agent-engineer.js"
        src = ROOT / "adapters/opencode/agent-engineer.js"
        if remove:
            if dst.exists() and "agent-engineer" in dst.read_text():
                self.say(f"remove {dst}")
                if not self.dry:
                    dst.unlink()
            return
        # OpenCode V2 loads the plugin as an ES module and needs a default export. A raw
        # Windows path ("C:\...") is not a valid ESM specifier, so the source is referenced
        # by file:// URL.
        shim = (f"// agent-engineer OpenCode plugin shim (installed by scripts/install.py)\n"
                f"export {{ default }} from {json.dumps(src.as_uri())};\n")
        if dst.exists() and dst.read_text() == shim:
            return
        self.say(f"write {dst}")
        if not self.dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(shim)

    # -- main --------------------------------------------------------------------------
    def run(self, remove: bool, hooks: bool, agents: bool) -> None:
        skills_dir, agents_dir, agents_src, hooks_target = LAYOUT[self.harness][self.scope]
        sd, ad, ht = self.path(skills_dir), self.path(agents_dir), self.path(hooks_target)
        if remove:
            self.unlink_ours(sd)
            self.unlink_ours(ad)
        else:
            for skill in sorted((ROOT / "skills").iterdir()):
                if (skill / "SKILL.md").is_file():
                    self.link(skill, sd / skill.name)
            if agents:
                for agent in sorted((ROOT / agents_src).iterdir()):
                    self.link(agent, ad / agent.name)
        if hooks:
            if self.harness == "opencode":
                self.opencode_plugin(ht, remove)
            else:
                self.merge_hooks(ht, remove)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--harness", required=True, choices=sorted(LAYOUT))
    ap.add_argument("--scope", default="project", choices=["project", "user"])
    ap.add_argument("--project-dir", default=os.getcwd(), help="project root (scope=project)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-hooks", action="store_true")
    ap.add_argument("--no-agents", action="store_true")
    args = ap.parse_args()

    project = Path(args.project_dir).resolve()
    if args.scope == "project" and project == ROOT:
        sys.exit("error: run this from your project (or pass --project-dir), not from the pack "
                 "checkout itself")
    inst = Installer(args.harness, args.scope, project, args.dry_run)
    inst.run(args.uninstall, hooks=not args.no_hooks, agents=not args.no_agents)
    if not inst.actions:
        print("nothing to do — already " + ("uninstalled" if args.uninstall else "installed"))
    elif not args.uninstall:
        print(f"\nnext: {NEXT_STEPS[args.harness]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
