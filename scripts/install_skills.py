#!/usr/bin/env python3
"""Install this checkout's Kluster skills without bootstrapping the application."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kllm.agent_skills import (  # noqa: E402
    AGENTS, CATEGORIES, SkillError, agent_destinations, apply_install,
    discover_skills, prepare_install,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", choices=(*AGENTS, "all"), help="repeat to select agents; default: all")
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--category", choices=CATEGORIES, default="all")
    parser.add_argument("--project-root", type=Path, default=ROOT, help="destination checkout; sources always come from this checkout")
    parser.add_argument("--list", action="store_true", help="list selected skills without writing")
    parser.add_argument("--dry-run", action="store_true", help="preflight and show destinations without writing")
    parser.add_argument("--force", action="store_true", help="back up and replace conflicting selected copies")
    args = parser.parse_args(argv)
    try:
        skills = discover_skills(ROOT / "skills", args.category)
        if args.list:
            for skill in skills:
                print(f"{skill.category}/{skill.name}: {skill.description}")
            return 0
        destinations = agent_destinations(args.agent or ["all"], args.scope, args.project_root)
        # Detect conflicts in every destination before changing any of them.
        plans = [(agents, prepare_install(skills, root, force=args.force))
                 for root, agents in destinations.items()]
        for agents, plan in plans:
            print(f"{', '.join(agents)}: {plan.root} ({len(skills)} skills, {len(plan.changes)} folder changes)")
            if not args.dry_run:
                for message in apply_install(plan):
                    print(message)
        if args.dry_run:
            print("Dry run: no files changed.")
        elif args.scope == "project":
            print("Restart/reload your agent. Hermes project discovery may require `hermes skills trust` locally.")
        return 0
    except (OSError, SkillError) as error:
        print(f"error: {error}. Completed destinations remain installed; rerunning is safe.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
