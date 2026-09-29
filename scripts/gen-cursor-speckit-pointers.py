#!/usr/bin/env python3
"""Generate .cursor/skills pointer wrappers for .opencode/commands/speckit*.md."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = ROOT / ".opencode" / "commands"
SKILLS_ROOT = ROOT / ".cursor" / "skills"


def description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return "Spec Kit command"
    yaml = m.group(1)
    for pat in (
        r'description:\s*"([^"]*)"',
        r"description:\s*'([^']*)'",
        r"description:\s*(.+)\n",
    ):
        dm = re.search(pat, yaml)
        if dm:
            return dm.group(1).strip().strip('"')
    return "Spec Kit command"


def main() -> None:
    for cmd in sorted(COMMANDS_DIR.glob("speckit*.md")):
        stem = cmd.stem
        skill_name = "speckit-" + stem.removeprefix("speckit.").replace(".", "-")
        skill_dir = SKILLS_ROOT / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        rel = f"../../../.opencode/commands/{cmd.name}"
        desc = description(cmd).replace('"', '\\"')
        body = f"""---
name: "{skill_name}"
description: "{desc}"
compatibility: "Requires Spec Kit project structure with .specify/ directory"
metadata:
  author: "kluster-llm"
  speckit-command: "{stem}"
  canonical: ".opencode/commands/{cmd.name}"
---

# Spec Kit: /{skill_name}

This skill is a **pointer** to the canonical command definition. Do not substitute your own workflow.

1. **Read** the full command at [`.opencode/commands/{cmd.name}`]({rel}).
2. **Follow** every instruction in that file as if it were part of this skill.
3. Treat the user's skill invocation text as `$ARGUMENTS` in the canonical command.
4. When the canonical file references other `/speckit.*` commands, invoke the matching Cursor skill (dots become hyphens, e.g. `speckit.plan` → `/speckit-plan`).
"""
        (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")

    hub_dir = SKILLS_ROOT / "memory-md"
    hub_dir.mkdir(parents=True, exist_ok=True)
    (hub_dir / "SKILL.md").write_text(
        """---
name: memory-md
description: "Spec Kit Memory Hub — when and how to run memory-md commands and MCP tools."
metadata:
  canonical: ".claude/skills/memory-md/SKILL.md"
---

# Memory-MD (pointer)

Read and follow [`.claude/skills/memory-md/SKILL.md`](../../.claude/skills/memory-md/SKILL.md) for trigger maps, auto-execute rules, and MCP vs CLI fallback.
""",
        encoding="utf-8",
    )
    print(f"Wrote {len(list(SKILLS_ROOT.iterdir()))} skill directories under {SKILLS_ROOT}")


if __name__ == "__main__":
    main()
